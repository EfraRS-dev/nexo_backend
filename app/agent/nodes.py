"""
Nodos del grafo LangGraph de Nexo.

Cada función recibe AgentState, realiza su trabajo y devuelve
un dict con los campos de estado que cambian (merge parcial).
"""
from __future__ import annotations

import json
import logging

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.agent.prompts import (
    CLASSIFICATION_PROMPT,
    FAQ_PROMPT,
    MSG_ESCALAMIENTO,
    MSG_ESTADO_SIN_PEDIDO,
    SYSTEM_PROMPT,
)
from app.agent.state import AgentState
from app.config import settings

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# LLM compartido — inicialización lazy para no requerir API key en import
# ─────────────────────────────────────────────────────────────────────────────

_llm: ChatOpenAI | None = None
_llm_json: ChatOpenAI | None = None
_llm_classifier: ChatOpenAI | None = None

# Modelos que soportan response_format json_object
_JSON_MODE_MODELS = ("gpt-4o", "gpt-4-turbo", "gpt-3.5-turbo-1106", "gpt-3.5-turbo-0125")


def _get_llm() -> ChatOpenAI:
    global _llm
    if _llm is None:
        _llm = ChatOpenAI(
            api_key=settings.openai_api_key,
            model=settings.ai_model,
            temperature=0.3,
            max_tokens=settings.max_output_tokens,
        )
    return _llm


def _get_llm_json() -> ChatOpenAI:
    """LLM para nodo_conversar: activa json_object mode en modelos que lo soportan."""
    global _llm_json
    if _llm_json is None:
        supports_json_mode = any(
            settings.ai_model.startswith(m) for m in _JSON_MODE_MODELS
        )
        kwargs: dict = dict(
            api_key=settings.openai_api_key,
            model=settings.ai_model,
            temperature=0.1,
            max_tokens=settings.max_output_tokens,
        )
        if supports_json_mode:
            kwargs["model_kwargs"] = {"response_format": {"type": "json_object"}}
        _llm_json = ChatOpenAI(**kwargs)
    return _llm_json


def _get_classifier_llm() -> ChatOpenAI:
    global _llm_classifier
    if _llm_classifier is None:
        _llm_classifier = ChatOpenAI(
            api_key=settings.openai_api_key,
            model=settings.ai_model,
            temperature=0.0,
            max_tokens=5,
        )
    return _llm_classifier


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _repair_json(raw: str, state: AgentState) -> dict:
    """Llama al LLM una vez más para convertir una respuesta en texto plano al JSON requerido."""
    logger.info("Intentando reparar respuesta no-JSON...")
    tipo_actual = state.get("tipo_pedido", "")
    full_repair = (
        "La siguiente respuesta fue generada por un asistente de pedidos pero NO es JSON válido. "
        "Convierte su contenido al formato JSON requerido sin cambiar el significado. "
        "Devuelve SOLAMENTE el objeto JSON, sin texto adicional.\n\n"
        f"Respuesta original:\n{raw}\n\n"
        f"Items actuales (no los pierdas): {json.dumps(state.get('items', []), ensure_ascii=False)}\n"
        f'Tipo pedido actual: "{tipo_actual}"\n\n'
        'El JSON debe tener exactamente estas claves: "respuesta", "items", "tipo_pedido", '
        '"direccion_entrega", "pedido_listo", "esperando_confirmacion".'
    )
    try:
        repair_response = _get_llm_json().invoke([
            SystemMessage(content="Eres un convertidor de texto a JSON. Responde SOLO con el objeto JSON."),
            HumanMessage(content=full_repair),
        ])
        text = repair_response.content.strip()
        if text.startswith("```"):
            parts = text.split("```")
            text = parts[1] if len(parts) > 1 else text
            if text.startswith("json"):
                text = text[4:]
        result = json.loads(text)
        logger.info("Reparación JSON exitosa.")
        return result
    except Exception as exc:
        logger.error("Reparación JSON fallida: %s", exc)
        return {
            "respuesta": raw,
            "items": state.get("items", []),
            "tipo_pedido": state.get("tipo_pedido", ""),
            "direccion_entrega": state.get("direccion_entrega"),
            "pedido_listo": False,
            "esperando_confirmacion": False,
        }


def _parse_llm_json(raw: str, state: AgentState) -> dict:
    """Parsea la respuesta JSON del LLM; si falla intenta reparación automática."""
    text = raw.strip()
    if text.startswith("```"):
        parts = text.split("```")
        text = parts[1] if len(parts) > 1 else text
        if text.startswith("json"):
            text = text[4:]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        logger.warning("LLM devolvió respuesta no-JSON, reparando... %s", raw[:80])
        return _repair_json(raw, state)


# ─────────────────────────────────────────────────────────────────────────────
# NODO 1 — Clasificar intención
# ─────────────────────────────────────────────────────────────────────────────

def nodo_clasificar(state: AgentState) -> dict:
    """
    Clasifica la intención del último mensaje del cliente.
    RF-08, RF-10, RF-11 (punto de entrada para FAQ, estado y escalamiento).
    """
    ultimo_mensaje = next(
        (m.content for m in reversed(state["messages"]) if isinstance(m, HumanMessage)),
        "",
    )

    # Si ya hay un pedido en curso (items > 0 o etapa confirmando), se mantiene "pedir"
    if state.get("items") or state.get("etapa") in ("confirmando", "pagando"):
        return {"intencion": "pedir"}

    response = _get_classifier_llm().invoke([
        SystemMessage(content=CLASSIFICATION_PROMPT),
        HumanMessage(content=ultimo_mensaje),
    ])

    intencion = response.content.strip().lower()
    if intencion not in ("pedir", "faq", "estado_pedido", "escalamiento"):
        intencion = "pedir"

    return {"intencion": intencion}


# ─────────────────────────────────────────────────────────────────────────────
# NODO 2 — Conversar (tomar el pedido)
# ─────────────────────────────────────────────────────────────────────────────

def nodo_conversar(state: AgentState, menu_texto: str = "") -> dict:
    """
    Mantiene la conversación de pedido con el cliente.
    Lee el menú del parámetro menu_texto (inyectado por el grafo o el webhook).
    RF-03, RF-04, RF-06, RF-09.
    """
    system = SYSTEM_PROMPT.format(menu=menu_texto or "(menú no disponible)")

    response = _get_llm_json().invoke(
        [SystemMessage(content=system)] + state["messages"]
    )

    data = _parse_llm_json(response.content, state)

    respuesta_texto = data.get("respuesta", "")
    items = data.get("items") or state.get("items", [])
    pedido_listo = bool(data.get("pedido_listo", False))
    esperando = bool(data.get("esperando_confirmacion", False))
    tipo_pedido = data.get("tipo_pedido") or state.get("tipo_pedido", "")
    direccion = data.get("direccion_entrega") or state.get("direccion_entrega")

    etapa = state.get("etapa", "conversando")
    if pedido_listo:
        etapa = "pagando"
        esperando = False  # reset explícito al confirmar
    elif esperando:
        etapa = "confirmando"

    return {
        "messages": state["messages"] + [AIMessage(content=respuesta_texto)],
        "items": items,
        "pedido_listo": pedido_listo,
        "esperando_confirmacion": esperando,
        "tipo_pedido": tipo_pedido,
        "direccion_entrega": direccion,
        "etapa": etapa,
    }


# ─────────────────────────────────────────────────────────────────────────────
# NODO 3 — Confirmar pedido
# ─────────────────────────────────────────────────────────────────────────────

def nodo_confirmar(state: AgentState) -> dict:
    """
    Presenta el resumen del pedido y solicita confirmación explícita. RF-07.
    Este nodo solo se alcanza cuando esperando_confirmacion=True.
    El LLM en nodo_conversar ya genera el mensaje de resumen; aquí solo
    aseguramos que la etapa sea 'confirmando'.
    """
    return {"etapa": "confirmando"}


# ─────────────────────────────────────────────────────────────────────────────
# NODO 4 — FAQ
# ─────────────────────────────────────────────────────────────────────────────

def nodo_faq(state: AgentState, menu_texto: str = "") -> dict:
    """Responde preguntas frecuentes del restaurante. RF-08."""
    ultimo_mensaje = next(
        (m.content for m in reversed(state["messages"]) if isinstance(m, HumanMessage)),
        "",
    )

    response = _get_llm().invoke([
        SystemMessage(content=FAQ_PROMPT.format(menu=menu_texto or "(menú no disponible)")),
        HumanMessage(content=ultimo_mensaje),
    ])

    return {
        "messages": state["messages"] + [AIMessage(content=response.content.strip())],
    }


# ─────────────────────────────────────────────────────────────────────────────
# NODO 5 — Estado del pedido
# ─────────────────────────────────────────────────────────────────────────────

def nodo_estado_pedido(state: AgentState) -> dict:
    """
    Informa al cliente el estado de su último pedido consultando la DB. RF-10.
    La consulta real a DB la realiza el webhook antes de invocar el grafo;
    si hay un pedido activo se pasa en state["comanda"]. Si no, devuelve
    el mensaje por defecto.
    """
    comanda = state.get("comanda")

    if comanda:
        estado = comanda.get("estado", "pendiente")
        referencia = comanda.get("referencia", comanda.get("id", "—"))
        ESTADOS = {
            "pendiente": "⏳ pendiente de pago",
            "confirmado": "✅ confirmado",
            "pagado": "💳 pago recibido — en preparación",
            "preparando": "👨‍🍳 en preparación",
            "en_camino": "🛵 en camino",
            "entregado": "🎉 entregado",
        }
        texto_estado = ESTADOS.get(estado, estado)
        respuesta = (
            f"Tu pedido #{referencia} está actualmente: *{texto_estado}*.\n"
            "¿Hay algo más en lo que pueda ayudarte?"
        )
    else:
        respuesta = MSG_ESTADO_SIN_PEDIDO

    return {
        "messages": state["messages"] + [AIMessage(content=respuesta)],
    }


# ─────────────────────────────────────────────────────────────────────────────
# NODO 6 — Escalamiento humano
# ─────────────────────────────────────────────────────────────────────────────

def nodo_escalamiento(state: AgentState) -> dict:
    """
    Marca la conversación como escalada y notifica al cliente. RF-11.
    El webhook se encargará de actualizar conversacion.estado en DB.
    """
    logger.warning(
        "ESCALAMIENTO solicitado — telefono=%s conversacion_id=%s",
        state.get("telefono_cliente"),
        state.get("conversacion_id"),
    )
    return {
        "messages": state["messages"] + [AIMessage(content=MSG_ESCALAMIENTO)],
        "etapa": "escalado",
        "requiere_escalamiento": True,
    }


# ─────────────────────────────────────────────────────────────────────────────
# NODO 7 — Generar comanda (persiste en DB vía el webhook)
# ─────────────────────────────────────────────────────────────────────────────

def nodo_generar_comanda(state: AgentState) -> dict:
    """
    Construye el dict de comanda en memoria para que el webhook lo persista.
    RF-13, RF-14.
    """
    import uuid as _uuid

    items = state.get("items", [])
    total = sum(i["cantidad"] * i["precio_unitario"] for i in items)
    referencia = "NEX-" + str(_uuid.uuid4())[:4].upper()

    lineas = "\n".join(
        f"• {i['cantidad']}x {i['nombre']} — ${i['cantidad'] * i['precio_unitario']:,} COP"
        for i in items
    )

    comanda = {
        "referencia": referencia,
        "items": items,
        "total": total,
        "tipo_pedido": state.get("tipo_pedido", "llevar"),
        "direccion_entrega": state.get("direccion_entrega"),
        "estado": "pendiente",
    }

    msg = (
        f"✅ *Pedido registrado* — Ref: {referencia}\n\n"
        f"{lineas}\n\n"
        f"*Total: ${total:,} COP*\n\n"
        "Generando tu enlace de pago… ⏳"
    )

    return {
        "messages": state["messages"] + [AIMessage(content=msg)],
        "comanda": comanda,
        "etapa": "pagando",
    }


# ─────────────────────────────────────────────────────────────────────────────
# NODO 8 — Generar link de pago
# ─────────────────────────────────────────────────────────────────────────────

def nodo_pago(state: AgentState) -> dict:
    """
    Genera el link de pago Wompi y lo envía al cliente. RF-17.
    En producción el webhook llama a payment_service.generar_link_pago();
    aquí construimos el mensaje con el link ya disponible en state["link_pago"].
    """
    comanda = state.get("comanda", {})
    link = state.get("link_pago")

    if link:
        msg = (
            f"💳 *Paga tu pedido aquí:*\n{link}\n\n"
            "Una vez confirmado el pago recibirás la notificación. ¡Gracias! 🍔"
        )
    else:
        # Fallback: link de demo (se sustituye en Fase 4 por Wompi real)
        referencia = comanda.get("referencia", "NEX-0000")
        total = comanda.get("total", 0)
        link_demo = (
            f"https://checkout.wompi.co/p/?public-key=DEMO"
            f"&amount-in-cents={total * 100}&reference={referencia}"
        )
        msg = (
            f"💳 *Paga tu pedido aquí:*\n{link_demo}\n\n"
            "Una vez confirmado el pago recibirás la notificación. ¡Gracias! 🍔"
        )

    return {
        "messages": state["messages"] + [AIMessage(content=msg)],
        "etapa": "finalizado",
    }
