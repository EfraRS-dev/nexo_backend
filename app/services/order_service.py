"""
Servicio de pedidos: persistencia de comandas en DB.

Crea registros en `pedidos` + `items_pedido` a partir del dict de comanda
generado por nodo_generar_comanda.
"""
from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.item_pedido import ItemPedido
from app.models.menu import Menu
from app.models.pedido import Pedido
from app.models.restaurante import Restaurante
from app.services.menu_service import buscar_producto_por_slug

logger = logging.getLogger(__name__)


def _siguiente_numero_pedido(db: Session, restaurante_id: str) -> int:
    """
    Incrementa atómicamente el contador de pedidos del restaurante y retorna
    el nuevo valor. Inicializa la fila si no existe (idempotente).
    Cada restaurante tiene su propia secuencia: contador 'pedidos:{restaurante_id}'.
    """
    nombre = f"pedidos:{restaurante_id}"
    db.execute(
        text(
            "INSERT INTO contadores (nombre, valor) VALUES (:nombre, 0)"
            " ON CONFLICT (nombre) DO NOTHING"
        ),
        {"nombre": nombre},
    )
    result = db.execute(
        text(
            "UPDATE contadores SET valor = valor + 1"
            " WHERE nombre = :nombre RETURNING valor"
        ),
        {"nombre": nombre},
    )
    return result.scalar()


def _prefijo_restaurante(db: Session, restaurante_id: str) -> str:
    """Devuelve el prefijo (4 letras) del restaurante; fallback 'NEXO'."""
    rest = db.query(Restaurante).filter(Restaurante.id == restaurante_id).first()
    if rest and rest.prefijo:
        return rest.prefijo
    return "NEXO"


def crear_pedido(db: Session, comanda: dict, cliente_id: str, restaurante_id: str) -> Pedido:
    """
    Crea registros en pedidos + items_pedido a partir del dict de comanda.
    Asigna una referencia secuencial legible por restaurante ({PREFIJO}-0001).
    Idempotente: si la referencia ya existe (y no es PENDIENTE), devuelve el pedido existente.

    Args:
        db: Sesión SQLAlchemy.
        comanda: Dict con items, total, tipo_pedido, direccion_entrega, metodo_pago.
        cliente_id: UUID del cliente.
        restaurante_id: Tenant al que pertenece el pedido.

    Returns:
        Instancia de Pedido creada o ya existente.
    """
    ref_actual = comanda.get("referencia", "PENDIENTE")

    # Idempotencia solo cuando la referencia ya fue asignada (no PENDIENTE)
    if ref_actual and ref_actual != "PENDIENTE":
        existente = (
            db.query(Pedido).filter(Pedido.referencia == ref_actual).first()
        )
        if existente:
            logger.info("Pedido %s ya existe — omitiendo creación", ref_actual)
            return existente

    # Asignar referencia secuencial por restaurante (formato KIKE-0001)
    numero = _siguiente_numero_pedido(db, restaurante_id)
    prefijo = _prefijo_restaurante(db, restaurante_id)
    referencia = f"{prefijo}-{numero:04d}"

    pedido = Pedido(
        cliente_id=cliente_id,
        restaurante_id=restaurante_id,
        referencia=referencia,
        estado="pendiente",
        tipo=comanda.get("tipo_pedido", "llevar"),
        metodo_pago=comanda.get("metodo_pago", "online"),
        direccion_entrega=comanda.get("direccion_entrega"),
        total=comanda.get("total", 0),
    )
    db.add(pedido)
    db.flush()  # obtener pedido.id antes de crear items

    for item in comanda.get("items", []):
        slug = item.get("id", "")
        menu_item = buscar_producto_por_slug(db, slug, restaurante_id) if slug else None

        if menu_item is None:
            # Fallback: slug might already be the menu UUID (LLM error)
            menu_item = (
                db.query(Menu)
                .filter(Menu.id == slug, Menu.restaurante_id == restaurante_id)
                .first()
                if slug
                else None
            )

        if menu_item is None:
            # FK constraint on producto_id references menu.id (UUID) — skip
            # unresolvable slugs to avoid IntegrityError killing the whole pedido.
            logger.warning(
                "Producto no encontrado para slug '%s' — ítem omitido de items_pedido", slug
            )
            continue

        item_db = ItemPedido(
            pedido_id=pedido.id,
            producto_id=menu_item.id,
            cantidad=item.get("cantidad", 1),
            modificadores=item.get("modificadores"),
            precio_unitario=item.get("precio_unitario", 0),
        )
        db.add(item_db)

    db.commit()
    db.refresh(pedido)
    logger.info("Pedido creado: %s — $%s COP", pedido.referencia, pedido.total)
    return pedido


def obtener_pedido_por_referencia(db: Session, referencia: str) -> Pedido | None:
    """Busca un pedido por su referencia legible (NEX-000001)."""
    return db.query(Pedido).filter(Pedido.referencia == referencia).first()


def marcar_pedido_pagado(db: Session, pedido: Pedido) -> None:
    """Actualiza el estado del pedido a 'pagado'."""
    pedido.estado = "pagado"
    db.commit()
    logger.info("Pedido %s marcado como pagado", pedido.referencia)


def obtener_ultimo_pedido(db: Session, cliente_id: str, restaurante_id: str) -> Pedido | None:
    """
    Retorna el pedido más reciente del cliente EN ESTE restaurante (cualquier estado).
    El cliente es global (un teléfono = una persona), por lo que filtramos por
    restaurante_id para no mezclar pedidos de distintos tenants.
    Usado por el webhook para poblar state["comanda"] cuando el cliente
    pregunta por el estado de su pedido en una conversación nueva.
    """
    return (
        db.query(Pedido)
        .filter(
            Pedido.cliente_id == cliente_id,
            Pedido.restaurante_id == restaurante_id,
        )
        .order_by(Pedido.created_at.desc())
        .first()
    )

