"""
Router de WhatsApp: webhook para recibir mensajes vía Twilio.

POST /webhooks/whatsapp
- Valida firma HMAC de Twilio
- Extrae teléfono + mensaje
- Encola el mensaje y retorna 200 a Twilio de inmediato
- Un worker por teléfono drena la cola en orden, con su propia sesión DB
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque

from fastapi import APIRouter, Form, Request, Response
from sqlalchemy.orm import Session
from twilio.request_validator import RequestValidator

from app.config import settings
from app.database import SessionLocal
from app.services.client_service import obtener_o_crear_cliente
from app.services.conversation_service import (
    escalar_conversacion,
    expirar_conversacion,
    finalizar_conversacion,
    guardar_mensajes,
    guardar_estado_pedido,
    hay_timeout_conversacion,
    obtener_o_crear_conversacion,
    restaurar_mensajes,
    restaurar_estado_pedido,
)
from app.services.menu_service import obtener_menu_formateado
from app.services.payment_service import generar_link_pago
from app.services.whatsapp_service import enviar_mensaje
from app.services.order_service import crear_pedido, obtener_ultimo_pedido
from app.services.restaurante_service import (
    RESTAURANTE_DEFAULT,
    obtener_restaurante,
    resolver_restaurante_por_numero,
)
from app.utils.delivery_utils import validar_zona_domicilio
from app.agent.graph import construir_grafo
from app.agent.state import AgentState
from app.observability import make_langfuse_handler, langfuse_context
from app.utils.input_utils import limitar_entrada_usuario

from langchain_core.messages import HumanMessage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

# ── Rate limiting ─────────────────────────────────────────────────────────────
# Ventana deslizante de 60 s con máximo de mensajes por número.

_rate_limits: dict[str, deque] = {}
_RATE_LIMIT_PER_MIN = 20  # mensajes máximos por teléfono por minuto
_RATE_LIMIT_WINDOW_S = 60.0


def _esta_bajo_limite(telefono: str) -> bool:
    """
    Verifica el rate limit por teléfono (ventana deslizante de 60 s).
    Retorna True si el número puede enviar otro mensaje; False si excedió el límite.
    """
    ahora = time.monotonic()
    if telefono not in _rate_limits:
        _rate_limits[telefono] = deque()
    timestamps = _rate_limits[telefono]
    # Purgar timestamps fuera de la ventana
    while timestamps and ahora - timestamps[0] > _RATE_LIMIT_WINDOW_S:
        timestamps.popleft()
    if len(timestamps) >= _RATE_LIMIT_PER_MIN:
        return False
    timestamps.append(ahora)
    return True


# ── Cola por teléfono ─────────────────────────────────────────────────────────
# Un asyncio.Queue + una Task de worker por número.
# El webhook encola el mensaje y retorna 200 a Twilio de inmediato.
# El worker drena la cola en orden con su propia sesión DB.

_phone_queues: dict[str, asyncio.Queue] = {}
_phone_workers: dict[str, asyncio.Task] = {}

# Tiempo máximo de inactividad antes de destruir el worker (segundos)
_WORKER_IDLE_TIMEOUT = 300


async def _ensure_worker(telefono: str) -> None:
    """Crea la cola y el worker del teléfono si no existen o si el worker terminó."""
    if telefono not in _phone_queues:
        _phone_queues[telefono] = asyncio.Queue()
    worker = _phone_workers.get(telefono)
    if worker is None or worker.done():
        _phone_workers[telefono] = asyncio.create_task(
            _phone_worker(telefono), name=f"worker-{telefono}"
        )


async def _phone_worker(telefono: str) -> None:
    """
    Worker por teléfono: procesa mensajes en orden, uno a la vez.
    Se auto-destruye tras _WORKER_IDLE_TIMEOUT segundos sin mensajes.
    """
    queue = _phone_queues[telefono]
    while True:
        try:
            item = await asyncio.wait_for(queue.get(), timeout=_WORKER_IDLE_TIMEOUT)
        except asyncio.TimeoutError:
            # Inactividad — limpiar y salir
            _phone_workers.pop(telefono, None)
            _phone_queues.pop(telefono, None)
            logger.debug("Worker de %s destruido por inactividad", telefono)
            break

        mensaje, numero_destino = item
        db: Session = SessionLocal()
        try:
            await _procesar_mensaje(db, telefono, mensaje, numero_destino)
        except Exception as exc:
            logger.error("Worker error para %s: %s", telefono, exc, exc_info=True)
            try:
                await asyncio.to_thread(
                    enviar_mensaje,
                    telefono,
                    "Lo siento, tuve un problema procesando tu mensaje. ¿Podrías intentar de nuevo? 🙏",
                )
            except Exception:
                pass
        finally:
            db.close()
            queue.task_done()


# ─────────────────────────────────────────────────────────────────────────────
# Validación de firma Twilio
# ─────────────────────────────────────────────────────────────────────────────

async def _validar_firma_twilio(request: Request) -> bool:
    """
    Valida la firma X-Twilio-Signature contra el auth_token.
    En desarrollo (auth_token vacío) se omite la validación.
    """
    if not settings.twilio_auth_token:
        logger.warning("twilio_auth_token vacío — omitiendo validación de firma (desarrollo)")
        return True

    validator = RequestValidator(settings.twilio_auth_token)
    signature = request.headers.get("X-Twilio-Signature", "")

    # Reconstruir URL pública usando BASE_URL para evitar mismatch detrás de proxies/tunnels
    base = settings.base_url.rstrip("/")
    url = f"{base}{request.url.path}"

    # Twilio envía application/x-www-form-urlencoded
    form = await request.form()
    params = dict(form)

    return validator.validate(url, params, signature)


# ─────────────────────────────────────────────────────────────────────────────
# Endpoint principal
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/whatsapp")
async def webhook_whatsapp(
    request: Request,
    Body: str = Form(""),
    From: str = Form(""),
    To: str = Form(""),
    NumMedia: str = Form("0"),
):
    """
    Recibe mensajes de WhatsApp vía Twilio.
    Retorna 200 a Twilio de inmediato; el procesamiento ocurre en background.
    """
    # ── 1. Validar firma ─────────────────────────────────────────────
    if not await _validar_firma_twilio(request):
        logger.warning("Firma Twilio inválida — rechazando request")
        return Response(status_code=403, content="Firma inválida")

    # ── 2. Extraer datos ──────────────────────────────────────────────
    telefono = From.replace("whatsapp:", "").strip()
    numero_destino = To.replace("whatsapp:", "").strip()
    mensaje = Body.strip()

    if not telefono:
        return Response(status_code=400, content="Número de teléfono requerido")

    # ── 2a. Rate limiting ─────────────────────────────────────────────
    if not _esta_bajo_limite(telefono):
        logger.warning("Rate limit excedido para %s", telefono)
        enviar_mensaje(
            telefono,
            "Estás enviando muchos mensajes seguidos. Espera un momento antes de continuar. 🙏",
        )
        return Response(status_code=200, content="OK")

    # ── 2b. Mensajes multimedia (audio, imagen, etc.) ─────────────────
    num_media = int(NumMedia) if NumMedia.isdigit() else 0
    if num_media > 0 and not mensaje:
        enviar_mensaje(
            telefono,
            "Solo proceso mensajes de texto. ¿Podrías escribirme tu pedido? 📝",
        )
        return Response(status_code=200, content="OK")

    if not mensaje:
        enviar_mensaje(telefono, "No pude leer tu mensaje. ¿Podrías escribirlo de nuevo? 😊")
        return Response(status_code=200, content="OK")

    mensaje = limitar_entrada_usuario(mensaje, settings.max_input_chars)
    logger.info("Mensaje recibido de %s: %s", telefono, mensaje[:80])

    # ── 3. Encolar y retornar 200 a Twilio de inmediato ──────────────
    await _ensure_worker(telefono)
    await _phone_queues[telefono].put((mensaje, numero_destino))
    return Response(status_code=200, content="OK")


async def _procesar_mensaje(
    db: Session, telefono: str, mensaje: str, numero_destino: str = ""
) -> None:
    """
    Lógica de negocio para un mensaje. Llamada por el worker del teléfono.
    La sesión DB es propiedad del worker (creada y cerrada externamente).
    """

    # ── 3a. Resolver el restaurante (tenant) por el número destino ────
    restaurante = resolver_restaurante_por_numero(db, numero_destino)
    if restaurante is None:
        logger.warning(
            "Número destino '%s' sin restaurante asociado — usando '%s'",
            numero_destino,
            RESTAURANTE_DEFAULT,
        )
        restaurante = obtener_restaurante(db, RESTAURANTE_DEFAULT)

    restaurante_id = restaurante.id if restaurante else RESTAURANTE_DEFAULT
    restaurante_nombre = restaurante.nombre if restaurante else settings.restaurante_nombre

    # ── 3. Buscar/crear cliente ───────────────────────────────────────
    cliente = obtener_o_crear_cliente(db, telefono)

    # ── 4. Recuperar conversación activa ──────────────────────────────
    conversacion = obtener_o_crear_conversacion(db, cliente.id, restaurante_id)

    # ── 4a. Timeout de inactividad (30 min) ───────────────────────────
    # Si la conversación lleva más de 30 min sin actividad, expira y se
    # inicia una nueva para no retomar pedidos incompletos de hace tiempo.
    if hay_timeout_conversacion(conversacion, timeout_min=30):
        logger.info(
            "Conversación %s expirada por inactividad para %s — iniciando nueva",
            conversacion.id,
            telefono,
        )
        expirar_conversacion(db, conversacion)
        conversacion = obtener_o_crear_conversacion(db, cliente.id, restaurante_id)

    # ── 5. Restaurar historial + agregar mensaje nuevo ────────────────
    historial = restaurar_mensajes(conversacion)
    historial.append(HumanMessage(content=mensaje))

    # ── 6. Restaurar estado del pedido en curso ───────────────────────
    estado_previo = restaurar_estado_pedido(conversacion)
    # If the previous turn finalized the order but the conversation-finalizing
    # DB write failed (caught exception), etapa is stuck at "finalizado".
    # Reset to a clean state so the next message starts a new order.
    if estado_previo.get("etapa") == "finalizado":
        logger.warning(
            "Etapa 'finalizado' en conversación activa para %s — reseteando estado",
            telefono,
        )
        estado_previo = {}

    # ── 6a. Poblar comanda desde DB para consultas de estado ──────────
    # Si no hay comanda en el estado (conversación nueva o post-finalización),
    # cargamos el último pedido del cliente desde DB para que nodo_estado_pedido
    # pueda responder "¿cómo va mi pedido?" correctamente.
    comanda_en_estado = estado_previo.get("comanda")
    if not comanda_en_estado:
        ultimo_pedido = obtener_ultimo_pedido(db, cliente.id, restaurante_id)
        if ultimo_pedido:
            comanda_en_estado = {
                "referencia": ultimo_pedido.referencia,
                "estado": ultimo_pedido.estado,
                "total": ultimo_pedido.total,
                "tipo_pedido": ultimo_pedido.tipo,
                "direccion_entrega": ultimo_pedido.direccion_entrega,
            }
    # ── 7. Cargar menú y construir grafo ──────────────────────────────
    menu_texto = obtener_menu_formateado(db, restaurante_id)
    grafo = construir_grafo(menu_texto, restaurante_nombre)

    # ── 8. Armar estado inicial para el grafo ─────────────────────────
    estado: AgentState = {
        "messages": historial,
        "items": estado_previo.get("items", []),
        "pedido_listo": False,
        "esperando_confirmacion": estado_previo.get("esperando_confirmacion", False),
        "intencion": "",
        "tipo_pedido": estado_previo.get("tipo_pedido", ""),
        "direccion_entrega": estado_previo.get("direccion_entrega"),
        "comanda": comanda_en_estado,
        "link_pago": None,
        "metodo_pago": estado_previo.get("metodo_pago", ""),
        "telefono_cliente": telefono,
        "cliente_id": cliente.id,
        "conversacion_id": conversacion.id,
        "restaurante_id": restaurante_id,
        "etapa": estado_previo.get("etapa", "conversando"),
        "requiere_escalamiento": False,
    }

    # ── 8. Ejecutar grafo ─────────────────────────────────────────────
    # Usar asyncio.to_thread para no bloquear el event loop durante la inferencia LLM
    lf_handler = make_langfuse_handler()
    lf_config = {"callbacks": [lf_handler]} if lf_handler else {}
    try:
        with langfuse_context(
            session_id=str(conversacion.id),
            user_id=telefono,
            trace_name="whatsapp-message",
        ):
            resultado = await asyncio.to_thread(grafo.invoke, estado, lf_config)
    except Exception as exc:
        logger.error("Error ejecutando grafo para %s: %s", telefono, exc)
        await asyncio.to_thread(
            enviar_mensaje,
            telefono,
            "Lo siento, tuve un problema procesando tu mensaje. ¿Podrías intentar de nuevo? 🙏",
        )
        return

    # ── 9. Extraer respuesta del agente ───────────────────────────────
    mensajes_resultado = resultado.get("messages", [])
    # data.get() returns None for null JSON values, so use `or ""` instead of a default
    respuesta = (mensajes_resultado[-1].content if mensajes_resultado else None) or ""

    # ── 9a. Validación de cobertura de domicilio (red de seguridad) ───
    # El SYSTEM_PROMPT ya instruye al agente sobre las zonas; esta verificación
    # actúa como salvaguarda adicional antes de finalizar el pedido.
    etapa = resultado.get("etapa", "conversando")
    tipo_pedido_resultado = resultado.get("tipo_pedido", "")
    direccion_resultado = resultado.get("direccion_entrega")

    if (
        etapa == "finalizado"
        and tipo_pedido_resultado == "domicilio"
        and direccion_resultado
        and not validar_zona_domicilio(direccion_resultado)
    ):
        logger.warning(
            "Domicilio fuera de cobertura para %s: %s",
            telefono,
            direccion_resultado,
        )
        etapa = "conversando"  # abortar finalización
        await asyncio.to_thread(
            enviar_mensaje,
            telefono,
            f"Lo siento, no cubrimos domicilios en esa dirección. 😔\n"
            "¿Te gustaría recoger tu pedido en tienda (llevar) o indicar "
            "una dirección dentro de nuestra zona de cobertura?",
        )
        return
    # Wrapped so a DB error never silences the response to the client.
    try:
        guardar_mensajes(db, conversacion, mensajes_resultado)
        guardar_estado_pedido(db, conversacion, resultado)
    except Exception as exc:
        logger.error("Error persistiendo conversación para %s: %s", telefono, exc)

    # ── 11. Persistir comanda en DB cuando el pedido está listo ───────
    # `etapa` ya fue extraído/modificado en el paso 9a (zona validation)
    comanda = resultado.get("comanda")
    pedido = None
    if etapa == "finalizado" and comanda:
        try:
            pedido = crear_pedido(db, comanda, cliente.id, restaurante_id)
        except Exception as exc:
            logger.error("Error persistiendo pedido: %s", exc)

    # ── 12. Acciones post-grafo ───────────────────────────────────────
    try:
        if etapa == "finalizado":
            finalizar_conversacion(db, conversacion)
        if etapa == "escalado":
            escalar_conversacion(db, conversacion)
    except Exception as exc:
        logger.error("Error actualizando estado conversación para %s: %s", telefono, exc)

    # ── 13. Enviar respuesta del agente al cliente ────────────────────
    if respuesta:
        await asyncio.to_thread(enviar_mensaje, telefono, respuesta)
    else:
        logger.warning("Respuesta vacía generada para %s — enviando fallback", telefono)
        await asyncio.to_thread(enviar_mensaje, telefono, "Un momento, ¿en qué te puedo ayudar? 😊")

    # ── 14. Enviar link de pago (online) o confirmación (caja) ────────
    if etapa == "finalizado" and pedido:
        metodo = (resultado.get("metodo_pago") or comanda.get("metodo_pago") or "online")
        if metodo != "caja":
            link = generar_link_pago(pedido.referencia, pedido.total)
            await asyncio.to_thread(
                enviar_mensaje,
                telefono,
                f"💳 *Paga tu pedido {pedido.referencia}:*\n{link}\n\n"
                "Una vez confirmado recibirás la notificación. 🏦",
            )
        else:
            await asyncio.to_thread(
                enviar_mensaje,
                telefono,
                f"📋 Tu número de pedido es *{pedido.referencia}*. ¡Te esperamos! 🍔",
            )
