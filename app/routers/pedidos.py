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
from app.models.operador import Operador
from app.models.pedido import EstadoPedido
from app.routers.auth import get_current_operador
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
    operador: Operador = Depends(get_current_operador),
):
    """
    Actualiza el estado de un pedido. Requiere JWT. Aislado por restaurante.

    Estados válidos: pendiente, confirmado, pagado, preparando, en_camino, entregado
    """
    pedido = obtener_pedido_por_referencia(db, referencia)
    if pedido is None or pedido.restaurante_id != operador.restaurante_id:
        raise HTTPException(status_code=404, detail=f"Pedido '{referencia}' no encontrado")

    pedido.estado = body.estado.value
    db.commit()
    logger.info("Pedido %s actualizado a estado '%s'", referencia, body.estado.value)
    return {"referencia": referencia, "estado": body.estado.value}
