"""
Servicio de WhatsApp: envío de mensajes vía Twilio.
"""
from __future__ import annotations

import logging

from twilio.rest import Client

from app.config import settings

logger = logging.getLogger(__name__)

_client: Client | None = None


def _get_client() -> Client:
    """Inicialización lazy del cliente Twilio."""
    global _client
    if _client is None:
        _client = Client(settings.twilio_account_sid, settings.twilio_auth_token)
    return _client


def enviar_mensaje(telefono: str, texto: str) -> str | None:
    """
    Envía un mensaje de WhatsApp al número indicado.

    Args:
        telefono: Número destino en formato E.164 (ej: +573001234567).
        texto: Cuerpo del mensaje.

    Returns:
        SID del mensaje de Twilio, o None si falla.
    """
    try:
        message = _get_client().messages.create(
            body=texto,
            from_=f"whatsapp:{settings.twilio_whatsapp_number}",
            to=f"whatsapp:{telefono}",
        )
        logger.info("Mensaje enviado a %s — SID: %s", telefono, message.sid)
        return message.sid
    except Exception as exc:
        logger.error("Error enviando mensaje a %s: %s", telefono, exc)
        return None


def enviar_recibo(telefono: str, comanda: dict) -> str | None:
    """
    Envía un recibo formateado con los detalles del pedido.

    Args:
        telefono: Número destino.
        comanda: Dict con referencia, items, total, tipo_pedido, direccion_entrega.
    """
    items = comanda.get("items", [])
    lineas = "\n".join(
        f"  • {i['cantidad']}x {i['nombre']} — ${i['cantidad'] * i['precio_unitario']:,} COP"
        for i in items
    )
    tipo = comanda.get("tipo_pedido", "llevar").capitalize()
    direccion = comanda.get("direccion_entrega")
    dir_linea = f"\n📍 Dirección: {direccion}" if direccion else ""

    texto = (
        f"🧾 *Recibo — NexoBurger*\n"
        f"Ref: {comanda.get('referencia', '—')}\n\n"
        f"{lineas}\n\n"
        f"*Total: ${comanda.get('total', 0):,} COP*\n"
        f"📦 Tipo: {tipo}{dir_linea}\n\n"
        f"¡Gracias por tu compra! 🍔"
    )
    return enviar_mensaje(telefono, texto)
