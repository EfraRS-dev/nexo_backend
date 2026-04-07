"""
Router de WhatsApp: webhook para recibir mensajes vía Twilio.

POST /webhooks/whatsapp
- Valida firma HMAC de Twilio
- Extrae teléfono + mensaje
- Busca/crea cliente
- Recupera conversación activa
- Ejecuta grafo LangGraph
- Persiste en DB
- Responde vía Twilio
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, Form, Request, Response
from sqlalchemy.orm import Session
from twilio.request_validator import RequestValidator

from app.config import settings
from app.database import get_db
from app.services.client_service import obtener_o_crear_cliente
from app.services.conversation_service import (
    escalar_conversacion,
    finalizar_conversacion,
    guardar_mensajes,
    guardar_estado_pedido,
    obtener_o_crear_conversacion,
    restaurar_mensajes,
    restaurar_estado_pedido,
)
from app.services.menu_service import obtener_menu_formateado
from app.services.whatsapp_service import enviar_mensaje
from app.agent.graph import construir_grafo
from app.agent.state import AgentState
from app.utils.input_utils import limitar_entrada_usuario

from langchain_core.messages import HumanMessage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

# Lock por teléfono para serializar mensajes rápidos del mismo cliente
_phone_locks: dict[str, asyncio.Lock] = {}


def _get_phone_lock(telefono: str) -> asyncio.Lock:
    if telefono not in _phone_locks:
        _phone_locks[telefono] = asyncio.Lock()
    return _phone_locks[telefono]


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
    db: Session = Depends(get_db),
):
    """
    Recibe mensajes de WhatsApp vía Twilio y responde con el agente.

    Twilio envía:
    - Body: texto del mensaje
    - From: whatsapp:+573001234567
    """
    # ── 1. Validar firma ─────────────────────────────────────────────
    if not await _validar_firma_twilio(request):
        logger.warning("Firma Twilio inválida — rechazando request")
        return Response(status_code=403, content="Firma inválida")

    # ── 2. Extraer datos ──────────────────────────────────────────────
    telefono = From.replace("whatsapp:", "").strip()
    mensaje = Body.strip()

    if not telefono:
        return Response(status_code=400, content="Número de teléfono requerido")

    if not mensaje:
        enviar_mensaje(telefono, "No pude leer tu mensaje. ¿Podrías escribirlo de nuevo? 😊")
        return Response(status_code=200, content="OK")

    # Limitar entrada para evitar abusos
    mensaje = limitar_entrada_usuario(mensaje, settings.max_input_chars)

    logger.info("Mensaje recibido de %s: %s", telefono, mensaje[:80])

    async with _get_phone_lock(telefono):
        await _procesar_mensaje(db, telefono, mensaje)
    return Response(status_code=200, content="OK")


async def _procesar_mensaje(db: Session, telefono: str, mensaje: str) -> None:
    """Lógica de negocio serializada por teléfono para evitar race conditions."""

    # ── 3. Buscar/crear cliente ───────────────────────────────────────
    cliente = obtener_o_crear_cliente(db, telefono)

    # ── 4. Recuperar conversación activa ──────────────────────────────
    conversacion = obtener_o_crear_conversacion(db, cliente.id)

    # ── 5. Restaurar historial + agregar mensaje nuevo ────────────────
    historial = restaurar_mensajes(conversacion)
    historial.append(HumanMessage(content=mensaje))

    # ── 6. Restaurar estado del pedido en curso ───────────────────────
    estado_previo = restaurar_estado_pedido(conversacion)

    # ── 7. Cargar menú y construir grafo ──────────────────────────────
    menu_texto = obtener_menu_formateado(db)
    grafo = construir_grafo(menu_texto)

    # ── 8. Armar estado inicial para el grafo ─────────────────────────
    estado: AgentState = {
        "messages": historial,
        "items": estado_previo.get("items", []),
        "pedido_listo": False,
        "esperando_confirmacion": estado_previo.get("esperando_confirmacion", False),
        "intencion": "",
        "tipo_pedido": estado_previo.get("tipo_pedido", ""),
        "direccion_entrega": estado_previo.get("direccion_entrega"),
        "comanda": estado_previo.get("comanda"),
        "link_pago": None,
        "telefono_cliente": telefono,
        "cliente_id": cliente.id,
        "conversacion_id": conversacion.id,
        "etapa": estado_previo.get("etapa", "conversando"),
        "requiere_escalamiento": False,
    }

    # ── 8. Ejecutar grafo ─────────────────────────────────────────────
    try:
        resultado = grafo.invoke(estado)
    except Exception as exc:
        logger.error("Error ejecutando grafo para %s: %s", telefono, exc)
        enviar_mensaje(
            telefono,
            "Lo siento, tuve un problema procesando tu mensaje. ¿Podrías intentar de nuevo? 🙏",
        )
        return

    # ── 9. Extraer respuesta del agente ───────────────────────────────
    mensajes_resultado = resultado.get("messages", [])
    respuesta = ""
    if mensajes_resultado:
        respuesta = mensajes_resultado[-1].content

    # ── 10. Persistir historial y estado del pedido en DB ─────────────
    guardar_mensajes(db, conversacion, mensajes_resultado)
    guardar_estado_pedido(db, conversacion, resultado)

    # ── 11. Acciones post-grafo ───────────────────────────────────────
    etapa = resultado.get("etapa", "conversando")

    if etapa == "finalizado":
        finalizar_conversacion(db, conversacion)

    if etapa == "escalado":
        escalar_conversacion(db, conversacion)

    # ── 12. Enviar respuesta al cliente ───────────────────────────────
    if respuesta:
        enviar_mensaje(telefono, respuesta)
