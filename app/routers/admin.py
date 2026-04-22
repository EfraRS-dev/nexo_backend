"""
Router de administración.

GET    /admin/pedidos           — lista paginada con filtros (estado, metodo_pago, fecha)
GET    /admin/menu              — todos los ítems del menú
POST   /admin/menu              — crea ítem
PATCH  /admin/menu/{id}         — actualiza ítem (parcial)
DELETE /admin/menu/{id}         — elimina ítem

Todos los endpoints requieren autenticación JWT (Bearer token).
"""
from __future__ import annotations

import logging
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.cliente import Cliente
from app.models.menu import Menu
from app.models.operador import Operador
from app.models.pedido import Pedido
from app.routers.auth import get_current_operador
from app.cache import invalidar_menu

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class ClienteOut(BaseModel):
    id: str
    telefono: str
    nombre: str | None

    model_config = {"from_attributes": True}


class PedidoOut(BaseModel):
    id: str
    referencia: str
    estado: str
    tipo: str
    metodo_pago: str
    total: int
    created_at: str
    cliente: ClienteOut

    model_config = {"from_attributes": True}


class PaginatedPedidos(BaseModel):
    items: list[PedidoOut]
    total: int
    page: int
    page_size: int


class MenuItemOut(BaseModel):
    id: str
    slug: str
    nombre: str
    precio: int
    categoria: str | None
    disponible: bool

    model_config = {"from_attributes": True}


class CreateMenuItemRequest(BaseModel):
    slug: str
    nombre: str
    precio: int
    categoria: str | None = None
    disponible: bool = True


class UpdateMenuItemRequest(BaseModel):
    slug: str | None = None
    nombre: str | None = None
    precio: int | None = None
    categoria: str | None = None
    disponible: bool | None = None


# ── Pedidos ───────────────────────────────────────────────────────────────────

@router.get("/pedidos", response_model=PaginatedPedidos)
def listar_pedidos(
    estado: str | None = Query(None),
    metodo_pago: str | None = Query(None),
    fecha: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    _: Operador = Depends(get_current_operador),
):
    """Lista pedidos con filtros opcionales. Requiere JWT."""
    query = db.query(Pedido)

    if estado:
        query = query.filter(Pedido.estado == estado)
    if metodo_pago:
        query = query.filter(Pedido.metodo_pago == metodo_pago)
    if fecha:
        try:
            day = date.fromisoformat(fecha)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="Formato de fecha inválido. Use YYYY-MM-DD.",
            )
        query = query.filter(func.date(Pedido.created_at) == day)

    total = query.count()
    pedidos = (
        query.order_by(Pedido.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    items: list[dict] = []
    for p in pedidos:
        cliente = db.query(Cliente).filter(Cliente.id == p.cliente_id).first()
        items.append(
            {
                "id": p.id,
                "referencia": p.referencia,
                "estado": p.estado,
                "tipo": p.tipo,
                "metodo_pago": p.metodo_pago,
                "total": p.total,
                "created_at": p.created_at.isoformat(),
                "cliente": {
                    "id": cliente.id if cliente else "",
                    "telefono": cliente.telefono if cliente else "",
                    "nombre": cliente.nombre if cliente else None,
                },
            }
        )

    return {"items": items, "total": total, "page": page, "page_size": page_size}


# ── Menú ──────────────────────────────────────────────────────────────────────

@router.get("/menu", response_model=list[MenuItemOut])
def listar_menu(
    db: Session = Depends(get_db),
    _: Operador = Depends(get_current_operador),
):
    """Retorna todos los ítems del menú activo."""
    return (
        db.query(Menu)
        .filter(Menu.restaurante_id == "default")
        .order_by(Menu.categoria, Menu.nombre)
        .all()
    )


@router.post("/menu", response_model=MenuItemOut, status_code=201)
def crear_item_menu(
    body: CreateMenuItemRequest,
    db: Session = Depends(get_db),
    _: Operador = Depends(get_current_operador),
):
    """Crea un nuevo ítem en el menú."""
    if (
        db.query(Menu)
        .filter(Menu.slug == body.slug, Menu.restaurante_id == "default")
        .first()
    ):
        raise HTTPException(
            status_code=409,
            detail=f"Ya existe un ítem con slug '{body.slug}'",
        )

    item = Menu(
        slug=body.slug,
        nombre=body.nombre,
        precio=body.precio,
        categoria=body.categoria,
        disponible=body.disponible,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    invalidar_menu()
    logger.info("Ítem menú creado: %s (%s)", item.nombre, item.id)
    return item


@router.patch("/menu/{item_id}", response_model=MenuItemOut)
def actualizar_item_menu(
    item_id: str,
    body: UpdateMenuItemRequest,
    db: Session = Depends(get_db),
    _: Operador = Depends(get_current_operador),
):
    """Actualiza parcialmente un ítem del menú."""
    item = db.query(Menu).filter(Menu.id == item_id).first()
    if item is None:
        raise HTTPException(
            status_code=404,
            detail=f"Ítem '{item_id}' no encontrado",
        )

    if body.slug is not None:
        conflict = (
            db.query(Menu)
            .filter(
                Menu.slug == body.slug,
                Menu.restaurante_id == "default",
                Menu.id != item_id,
            )
            .first()
        )
        if conflict:
            raise HTTPException(
                status_code=409,
                detail=f"El slug '{body.slug}' ya está en uso",
            )
        item.slug = body.slug

    if body.nombre is not None:
        item.nombre = body.nombre
    if body.precio is not None:
        item.precio = body.precio
    if body.categoria is not None:
        item.categoria = body.categoria
    if body.disponible is not None:
        item.disponible = body.disponible

    db.commit()
    db.refresh(item)
    invalidar_menu()
    logger.info("Ítem menú actualizado: %s (%s)", item.nombre, item_id)
    return item


@router.delete("/menu/{item_id}", status_code=204)
def eliminar_item_menu(
    item_id: str,
    db: Session = Depends(get_db),
    _: Operador = Depends(get_current_operador),
):
    """Elimina un ítem del menú."""
    item = db.query(Menu).filter(Menu.id == item_id).first()
    if item is None:
        raise HTTPException(
            status_code=404,
            detail=f"Ítem '{item_id}' no encontrado",
        )
    db.delete(item)
    db.commit()
    invalidar_menu()
    logger.info("Ítem menú eliminado: %s", item_id)
