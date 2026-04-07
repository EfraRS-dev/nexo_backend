"""
Servicio de clientes: buscar o crear cliente por teléfono.
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.models.cliente import Cliente

logger = logging.getLogger(__name__)


def obtener_o_crear_cliente(db: Session, telefono: str) -> Cliente:
    """
    Busca un cliente por su teléfono.
    Si no existe, lo crea y retorna el registro nuevo.
    """
    cliente = db.query(Cliente).filter(Cliente.telefono == telefono).first()
    if cliente is None:
        cliente = Cliente(telefono=telefono)
        db.add(cliente)
        db.commit()
        db.refresh(cliente)
        logger.info("Nuevo cliente creado: %s — tel: %s", cliente.id, telefono)
    return cliente
