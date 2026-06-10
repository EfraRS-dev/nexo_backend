"""
Servicio de conversaciones: gestión de sesiones activas en DB.

Regla: 1 conversación activa por cliente. Se finaliza al completar pedido.
Historial en JSONB (mensajes).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from langchain_core.messages import AIMessage, HumanMessage
from sqlalchemy.orm import Session

from app.models.conversacion import Conversacion

logger = logging.getLogger(__name__)


def obtener_o_crear_conversacion(
    db: Session, cliente_id: str, restaurante_id: str
) -> Conversacion:
    """
    Busca la conversación activa del cliente en este restaurante.
    Si no existe, crea una nueva. Una conversación pertenece a un único tenant.
    """
    conv = (
        db.query(Conversacion)
        .filter(
            Conversacion.cliente_id == cliente_id,
            Conversacion.restaurante_id == restaurante_id,
            Conversacion.estado == "activa",
        )
        .first()
    )
    if conv is None:
        conv = Conversacion(
            cliente_id=cliente_id,
            restaurante_id=restaurante_id,
            mensajes=[],
            estado="activa",
        )
        db.add(conv)
        db.commit()
        db.refresh(conv)
        logger.info(
            "Nueva conversación creada: %s para cliente %s (restaurante %s)",
            conv.id,
            cliente_id,
            restaurante_id,
        )
    return conv


def guardar_mensajes(db: Session, conversacion: Conversacion, messages: list) -> None:
    """
    Persiste el historial de mensajes (langchain Messages) en JSONB.
    Convierte HumanMessage/AIMessage a dicts serializables.
    """
    mensajes_serializados = []
    for m in messages:
        if isinstance(m, HumanMessage):
            mensajes_serializados.append({"role": "user", "content": m.content})
        elif isinstance(m, AIMessage):
            mensajes_serializados.append({"role": "assistant", "content": m.content})

    conversacion.mensajes = mensajes_serializados
    conversacion.updated_at = datetime.now(timezone.utc)
    db.commit()


def guardar_estado_pedido(db: Session, conversacion: Conversacion, resultado: dict) -> None:
    """
    Persiste el estado del pedido en curso dentro de la conversación para
    restaurarlo en la próxima invocación. Campos guardados: items, tipo_pedido,
    direccion_entrega, etapa, esperando_confirmacion, comanda y metodo_pago.
    """
    estado_pedido = {
        "items": resultado.get("items", []),
        "tipo_pedido": resultado.get("tipo_pedido", ""),
        "direccion_entrega": resultado.get("direccion_entrega"),
        "etapa": resultado.get("etapa", "conversando"),
        "esperando_confirmacion": resultado.get("esperando_confirmacion", False),
        "comanda": resultado.get("comanda"),
        "metodo_pago": resultado.get("metodo_pago", ""),
    }
    # Almacenamos en el mismo JSONB usando una clave reservada
    mensajes_actuales = conversacion.mensajes or []
    # Filtramos cualquier estado previo y lo reemplazamos
    mensajes_sin_estado = [m for m in mensajes_actuales if m.get("role") != "__estado__"]
    mensajes_sin_estado.append({"role": "__estado__", "content": estado_pedido})
    conversacion.mensajes = mensajes_sin_estado
    conversacion.updated_at = datetime.now(timezone.utc)
    db.commit()


def restaurar_estado_pedido(conversacion: Conversacion) -> dict:
    """
    Restaura el estado del pedido en curso desde la conversación persistida.
    Retorna un dict con los mismos campos que guarda `guardar_estado_pedido`:
    items, tipo_pedido, direccion_entrega, etapa, esperando_confirmacion,
    comanda y metodo_pago. Dict vacío si no hay estado previo.
    """
    for m in (conversacion.mensajes or []):
        if m.get("role") == "__estado__":
            return m.get("content", {})
    return {}


def restaurar_mensajes(conversacion: Conversacion) -> list:
    """
    Convierte los mensajes JSONB almacenados a objetos langchain
    para alimentar el grafo. Ignora entradas de estado interno.
    """
    messages = []
    for m in conversacion.mensajes or []:
        role = m.get("role", "")
        content = m.get("content", "")
        if role == "user":
            messages.append(HumanMessage(content=content))
        elif role == "assistant":
            messages.append(AIMessage(content=content))
    return messages


def finalizar_conversacion(db: Session, conversacion: Conversacion) -> None:
    """Marca la conversación como finalizada."""
    conversacion.estado = "finalizada"
    conversacion.updated_at = datetime.now(timezone.utc)
    db.commit()


def escalar_conversacion(db: Session, conversacion: Conversacion) -> None:
    """Marca la conversación como escalada."""
    conversacion.estado = "escalada"
    conversacion.updated_at = datetime.now(timezone.utc)
    db.commit()


def expirar_conversacion(db: Session, conversacion: Conversacion) -> None:
    """Marca la conversación como expirada (timeout de inactividad)."""
    conversacion.estado = "expirada"
    conversacion.updated_at = datetime.now(timezone.utc)
    db.commit()
    logger.info("Conversación %s expirada por inactividad", conversacion.id)


def hay_timeout_conversacion(conversacion: Conversacion, timeout_min: int = 30) -> bool:
    """
    Retorna True si la conversación lleva más de `timeout_min` minutos sin actividad.
    Una conversación sin `updated_at` (o con valor no-datetime) se considera activa.
    """
    updated = conversacion.updated_at
    if not updated or not isinstance(updated, datetime):
        return False
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - updated
    return delta.total_seconds() > timeout_min * 60
