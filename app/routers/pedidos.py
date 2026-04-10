"""
Router de pedidos: endpoints para operadores.

PATCH /pedidos/{referencia}/estado  — actualiza el estado de un pedido
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.pedido import EstadoPedido
from app.services.order_service import obtener_pedido_por_referencia

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/pedidos", tags=["pedidos"])


class ActualizarEstadoRequest(BaseModel):
    estado: EstadoPedido


@router.patch("/{referencia}/estado")
def actualizar_estado_pedido(
    referencia: str,
    body: ActualizarEstadoRequest,
    db: Session = Depends(get_db),
):
    """
    Actualiza el estado de un pedido. Para uso de operadores.

    Estados válidos: pendiente, confirmado, pagado, preparando, en_camino, entregado
    """
    pedido = obtener_pedido_por_referencia(db, referencia)
    if pedido is None:
        raise HTTPException(status_code=404, detail=f"Pedido '{referencia}' no encontrado")

    pedido.estado = body.estado.value
    db.commit()
    logger.info("Pedido %s actualizado a estado '%s'", referencia, body.estado.value)
    return {"referencia": referencia, "estado": body.estado.value}
