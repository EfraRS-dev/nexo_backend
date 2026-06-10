"""
Router de Wompi: webhook para recibir notificaciones de pago.

POST /webhooks/wompi
- Valida checksum SHA256 del evento
- Procesa transaction.updated
- Si APPROVED: actualiza pedido → pagado, crea registro Pago,
  envía confirmación + recibo por WhatsApp
"""
from __future__ import annotations

import hashlib
import logging

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.cliente import Cliente
from app.models.conversacion import Conversacion
from app.models.pago import Pago
from app.services.conversation_service import finalizar_conversacion
from app.services.order_service import marcar_pedido_pagado, obtener_pedido_por_referencia
from app.services.whatsapp_service import enviar_mensaje, enviar_recibo

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


# ─────────────────────────────────────────────────────────────────────────────
# Validación de checksum Wompi
# ─────────────────────────────────────────────────────────────────────────────

def _validar_checksum_wompi(body: dict, checksum_recibido: str) -> bool:
    """
    Valida el checksum SHA256 de un evento Wompi.

    Algoritmo (docs.wompi.co/docs/colombia/eventos/):
      SHA256( prop_val_1 + prop_val_2 + ... + timestamp + events_secret )

    Las propiedades a usar están en signature.properties, y los valores
    se resuelven por notación de punto dentro de data.
    """
    if not settings.wompi_events_secret:
        logger.warning("wompi_events_secret vacío — omitiendo validación de checksum (desarrollo)")
        return True

    signature = body.get("signature", {})
    properties = signature.get("properties", [])
    timestamp = body.get("timestamp", "")
    data = body.get("data", {})

    # Resolver cada propiedad path (ej: "transaction.id") desde data
    valores = []
    for prop in properties:
        parts = prop.split(".")
        val: object = data
        for part in parts:
            val = val.get(part, "") if isinstance(val, dict) else ""
        valores.append(str(val))

    cadena = "".join(valores) + str(timestamp) + settings.wompi_events_secret
    checksum_calculado = hashlib.sha256(cadena.encode()).hexdigest().upper()

    return checksum_calculado == checksum_recibido.upper()


# ─────────────────────────────────────────────────────────────────────────────
# Endpoint principal
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/wompi")
async def webhook_wompi(
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Recibe eventos de Wompi y procesa pagos aprobados.

    Wompi envía:
    - event: "transaction.updated"
    - data.transaction.reference: referencia del pedido (NEX-XXXX)
    - data.transaction.status: APPROVED | DECLINED | VOIDED | ERROR
    - signature.checksum + signature.properties: para validación HMAC
    """
    # ── 1. Parsear body ───────────────────────────────────────────────
    try:
        body = await request.json()
    except Exception:
        logger.warning("Wompi webhook recibió body inválido")
        return Response(status_code=400, content="Body inválido")

    # ── 2. Validar checksum ───────────────────────────────────────────
    checksum_recibido = (
        request.headers.get("X-Event-Checksum", "")
        or body.get("signature", {}).get("checksum", "")
    )

    if not _validar_checksum_wompi(body, checksum_recibido):
        logger.warning("Checksum Wompi inválido — rechazando evento")
        return Response(status_code=401, content="Checksum inválido")

    # ── 3. Filtrar evento ─────────────────────────────────────────────
    evento = body.get("event")
    if evento != "transaction.updated":
        logger.info("Evento Wompi ignorado: %s", evento)
        return Response(status_code=200, content="OK")

    # ── 4. Extraer datos de la transacción ────────────────────────────
    transaccion = body.get("data", {}).get("transaction", {})
    referencia = transaccion.get("reference", "")
    status = transaccion.get("status", "")
    monto_centavos = transaccion.get("amount_in_cents", 0)
    referencia_wompi = transaccion.get("id", "")

    logger.info("transaction.updated — ref=%s  status=%s", referencia, status)

    if status != "APPROVED":
        logger.info("Transacción %s no aprobada (status=%s) — sin acción", referencia, status)
        return Response(status_code=200, content="OK")

    # ── 5. Buscar pedido en DB ────────────────────────────────────────
    pedido = obtener_pedido_por_referencia(db, referencia)
    if not pedido:
        logger.error("Pedido no encontrado para referencia=%s", referencia)
        return Response(status_code=200, content="OK")  # 200 para que Wompi no reintente

    if pedido.estado == "pagado":
        logger.info("Pedido %s ya estaba pagado — idempotencia OK", referencia)
        return Response(status_code=200, content="OK")

    # ── 6. Actualizar estado del pedido ───────────────────────────────
    marcar_pedido_pagado(db, pedido)

    # ── 7. Registrar pago ─────────────────────────────────────────────
    pago = Pago(
        pedido_id=pedido.id,
        metodo=transaccion.get("payment_method_type"),
        estado="completado",
        referencia_wompi=referencia_wompi,
        monto=monto_centavos // 100,
    )
    db.add(pago)
    db.commit()

    # ── 8. Obtener teléfono del cliente ───────────────────────────────
    cliente = db.query(Cliente).filter(Cliente.id == pedido.cliente_id).first()
    if not cliente:
        logger.error("Cliente no encontrado para pedido %s", referencia)
        return Response(status_code=200, content="OK")

    # ── 9. Enviar confirmación + recibo por WhatsApp ──────────────────
    enviar_mensaje(
        cliente.telefono,
        f"✅ *¡Pago recibido!* Tu pedido *{referencia}* está confirmado y en preparación. ¡Gracias! 🍔",
    )

    from app.models.item_pedido import ItemPedido
    from app.models.menu import Menu
    items_db = db.query(ItemPedido).filter(ItemPedido.pedido_id == pedido.id).all()
    # Resolver nombres legibles del menú (producto_id es el UUID en menu.id).
    # Si un ítem fue borrado del menú, caemos al id como último recurso.
    ids_productos = [i.producto_id for i in items_db]
    nombres_por_id = (
        {
            m.id: m.nombre
            for m in db.query(Menu).filter(Menu.id.in_(ids_productos)).all()
        }
        if ids_productos
        else {}
    )
    comanda_recibo = {
        "referencia": pedido.referencia,
        "items": [
            {
                "nombre": nombres_por_id.get(i.producto_id, i.producto_id),
                "cantidad": i.cantidad,
                "precio_unitario": i.precio_unitario,
            }
            for i in items_db
        ],
        "total": pedido.total,
        "tipo_pedido": pedido.tipo,
        "direccion_entrega": pedido.direccion_entrega,
    }
    enviar_recibo(cliente.telefono, comanda_recibo)

    # ── 10. Finalizar conversación activa del cliente en este tenant ──
    # Filtra por restaurante_id del pedido para no cerrar la conversación de
    # otro tenant si el cliente tiene varias activas (ver bug R-2).
    conversacion = (
        db.query(Conversacion)
        .filter(
            Conversacion.cliente_id == cliente.id,
            Conversacion.restaurante_id == pedido.restaurante_id,
            Conversacion.estado == "activa",
        )
        .first()
    )
    if conversacion:
        finalizar_conversacion(db, conversacion)

    logger.info("Pago procesado exitosamente para pedido %s", referencia)
    return Response(status_code=200, content="OK")
