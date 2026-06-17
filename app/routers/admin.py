"""
Router de administración.

GET    /admin/me               — perfil del operador autenticado + su restaurante
GET    /admin/restaurante       — información del restaurante (tenant) del operador
PATCH  /admin/restaurante       — actualiza la información del restaurante (parcial)
GET    /admin/pedidos           — lista paginada con filtros (estado, metodo_pago, fecha)
GET    /admin/menu              — todos los ítems del menú
POST   /admin/menu              — crea ítem
PATCH  /admin/menu/{id}         — actualiza ítem (parcial)
DELETE /admin/menu/{id}         — elimina ítem

Todos los endpoints requieren autenticación JWT (Bearer token).
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.cliente import Cliente
from app.models.item_pedido import ItemPedido
from app.models.menu import Menu
from app.models.operador import Operador
from app.models.pedido import Pedido
from app.models.restaurante import Restaurante
from app.routers.auth import get_current_operador
from app.services.restaurante_service import obtener_restaurante
from app.cache import invalidar_menu

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class ClienteOut(BaseModel):
    id: str
    telefono: str
    nombre: str | None

    model_config = {"from_attributes": True}


class ItemPedidoOut(BaseModel):
    producto_id: str
    cantidad: int
    precio_unitario: int
    modificadores: dict | None
    nombre: str | None  # nombre del producto en el menú (None si fue eliminado)

    model_config = {"from_attributes": True}


class PedidoOut(BaseModel):
    id: str
    referencia: str
    estado: str
    tipo: str
    direccion_entrega: str | None
    metodo_pago: str
    total: int
    created_at: str
    cliente: ClienteOut
    items: list[ItemPedidoOut]

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


class OperadorMeOut(BaseModel):
    email: str
    restaurante_id: str
    restaurante_nombre: str


# Campos de la información del restaurante que viven en config_json (el resto
# —nombre— es columna). Se usan para aplanar la salida y validar el merge.
_CONFIG_FIELDS = (
    "descripcion",
    "direccion",
    "horario",
    "email",
    "telefono_contacto",
    "redes_sociales",
    "metodos_pago",
    "zonas_cobertura",
)


class RestauranteOut(BaseModel):
    id: str
    nombre: str
    numero_whatsapp: str
    prefijo: str
    activo: bool
    # Campos de config_json (aplanados); null si el tenant no los ha definido.
    descripcion: str | None = None
    direccion: str | None = None
    horario: str | None = None
    email: str | None = None
    telefono_contacto: str | None = None
    redes_sociales: dict | None = None
    metodos_pago: list[str] | None = None
    zonas_cobertura: list[str] | None = None


class UpdateRestauranteRequest(BaseModel):
    nombre: str | None = None
    descripcion: str | None = None
    direccion: str | None = None
    horario: str | None = None
    email: str | None = None
    telefono_contacto: str | None = None
    redes_sociales: dict | None = None
    metodos_pago: list[str] | None = None
    zonas_cobertura: list[str] | None = None


def _restaurante_out(rest: Restaurante) -> RestauranteOut:
    """Aplana un Restaurante (columnas + config_json) a RestauranteOut."""
    config = rest.config_json or {}
    return RestauranteOut(
        id=rest.id,
        nombre=rest.nombre,
        numero_whatsapp=rest.numero_whatsapp,
        prefijo=rest.prefijo,
        activo=rest.activo,
        **{k: config.get(k) for k in _CONFIG_FIELDS},
    )


# ── Perfil ──────────────────────────────────────────────────────────────────

@router.get("/me", response_model=OperadorMeOut)
def obtener_perfil(
    db: Session = Depends(get_db),
    operador: Operador = Depends(get_current_operador),
):
    """Retorna el operador autenticado y el restaurante (tenant) al que pertenece."""
    rest = obtener_restaurante(db, operador.restaurante_id)
    return OperadorMeOut(
        email=operador.email,
        restaurante_id=operador.restaurante_id,
        restaurante_nombre=rest.nombre if rest else operador.restaurante_id,
    )


# ── Restaurante (tenant) ────────────────────────────────────────────────────────

@router.get("/restaurante", response_model=RestauranteOut)
def obtener_restaurante_info(
    db: Session = Depends(get_db),
    operador: Operador = Depends(get_current_operador),
):
    """Retorna la información del restaurante (tenant) del operador."""
    rest = obtener_restaurante(db, operador.restaurante_id)
    if rest is None:
        raise HTTPException(status_code=404, detail="Restaurante no encontrado")
    return _restaurante_out(rest)


@router.patch("/restaurante", response_model=RestauranteOut)
def actualizar_restaurante(
    body: UpdateRestauranteRequest,
    db: Session = Depends(get_db),
    operador: Operador = Depends(get_current_operador),
):
    """Actualiza la información del restaurante del operador.

    `nombre` es columna; el resto de campos se mergean dentro de config_json.
    exclude_unset distingue "no enviado" de "enviado como null": un campo
    explícito a null/[] lo limpia; un campo ausente no se toca.
    """
    rest = obtener_restaurante(db, operador.restaurante_id)
    if rest is None:
        raise HTTPException(status_code=404, detail="Restaurante no encontrado")

    data = body.model_dump(exclude_unset=True)

    if "nombre" in data:
        nombre = data.pop("nombre")
        if not nombre or not nombre.strip():
            raise HTTPException(status_code=400, detail="El nombre no puede estar vacío")
        rest.nombre = nombre.strip()

    # Campos restantes → merge en config_json. Reasignar un dict nuevo (no mutar
    # in-place) para que SQLAlchemy detecte el cambio en la columna JSONB.
    if data:
        config = dict(rest.config_json or {})
        config.update(data)
        rest.config_json = config

    db.commit()
    db.refresh(rest)
    # Limpia la caché de FAQ del tenant (invalidar_menu borra menú + nexo:faq:{id}:*)
    # para que las respuestas reflejen el horario/contacto/zonas nuevos.
    invalidar_menu(operador.restaurante_id)
    logger.info("Restaurante actualizado: %s", rest.id)
    return _restaurante_out(rest)


# ── Pedidos ───────────────────────────────────────────────────────────────────

@router.get("/pedidos", response_model=PaginatedPedidos)
def listar_pedidos(
    estado: str | None = Query(None),
    metodo_pago: str | None = Query(None),
    fecha: str | None = Query(None),
    tz_offset: int = Query(0, ge=-840, le=840),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    operador: Operador = Depends(get_current_operador),
):
    """Lista pedidos con filtros opcionales. Requiere JWT. Aislado por restaurante.

    `fecha` se interpreta como día local del cliente; `tz_offset` son los minutos
    que hay que sumar a la hora local para obtener UTC (convención de JS
    `Date.getTimezoneOffset()`; Colombia = 300).
    """
    query = db.query(Pedido).filter(Pedido.restaurante_id == operador.restaurante_id)

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
        inicio = datetime(
            day.year, day.month, day.day, tzinfo=timezone.utc
        ) + timedelta(minutes=tz_offset)
        fin = inicio + timedelta(days=1)
        query = query.filter(Pedido.created_at >= inicio, Pedido.created_at < fin)

    total = query.count()
    pedidos = (
        query.order_by(Pedido.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    # Carga en lote de clientes, items y nombres de producto (evita N+1)
    cliente_ids = {p.cliente_id for p in pedidos}
    clientes: dict[str, Cliente] = {}
    if cliente_ids:
        clientes = {
            c.id: c
            for c in db.query(Cliente).filter(Cliente.id.in_(cliente_ids)).all()
        }

    pedido_ids = [p.id for p in pedidos]
    items_por_pedido: dict[str, list[ItemPedido]] = {}
    productos: dict[str, Menu] = {}
    if pedido_ids:
        items_pedido = (
            db.query(ItemPedido).filter(ItemPedido.pedido_id.in_(pedido_ids)).all()
        )
        producto_ids = {i.producto_id for i in items_pedido}
        if producto_ids:
            productos = {
                m.id: m
                for m in db.query(Menu).filter(Menu.id.in_(producto_ids)).all()
            }
        for i in items_pedido:
            items_por_pedido.setdefault(i.pedido_id, []).append(i)

    items: list[dict] = []
    for p in pedidos:
        cliente = clientes.get(p.cliente_id)
        items.append(
            {
                "id": p.id,
                "referencia": p.referencia,
                "estado": p.estado,
                "tipo": p.tipo,
                "direccion_entrega": p.direccion_entrega,
                "metodo_pago": p.metodo_pago,
                "total": p.total,
                "created_at": p.created_at.isoformat(),
                "cliente": {
                    "id": cliente.id if cliente else "",
                    "telefono": cliente.telefono if cliente else "",
                    "nombre": cliente.nombre if cliente else None,
                },
                "items": [
                    {
                        "producto_id": i.producto_id,
                        "cantidad": i.cantidad,
                        "precio_unitario": i.precio_unitario,
                        "modificadores": i.modificadores,
                        "nombre": (
                            productos[i.producto_id].nombre
                            if i.producto_id in productos
                            else None
                        ),
                    }
                    for i in items_por_pedido.get(p.id, [])
                ],
            }
        )

    return {"items": items, "total": total, "page": page, "page_size": page_size}


# ── Menú ──────────────────────────────────────────────────────────────────────

@router.get("/menu", response_model=list[MenuItemOut])
def listar_menu(
    db: Session = Depends(get_db),
    operador: Operador = Depends(get_current_operador),
):
    """Retorna todos los ítems del menú del restaurante del operador."""
    return (
        db.query(Menu)
        .filter(Menu.restaurante_id == operador.restaurante_id)
        .order_by(Menu.categoria, Menu.nombre)
        .all()
    )


@router.post("/menu", response_model=MenuItemOut, status_code=201)
def crear_item_menu(
    body: CreateMenuItemRequest,
    db: Session = Depends(get_db),
    operador: Operador = Depends(get_current_operador),
):
    """Crea un nuevo ítem en el menú del restaurante del operador."""
    if (
        db.query(Menu)
        .filter(Menu.slug == body.slug, Menu.restaurante_id == operador.restaurante_id)
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
        restaurante_id=operador.restaurante_id,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    invalidar_menu(operador.restaurante_id)
    logger.info("Ítem menú creado: %s (%s)", item.nombre, item.id)
    return item


@router.patch("/menu/{item_id}", response_model=MenuItemOut)
def actualizar_item_menu(
    item_id: str,
    body: UpdateMenuItemRequest,
    db: Session = Depends(get_db),
    operador: Operador = Depends(get_current_operador),
):
    """Actualiza parcialmente un ítem del menú del restaurante del operador."""
    item = (
        db.query(Menu)
        .filter(Menu.id == item_id, Menu.restaurante_id == operador.restaurante_id)
        .first()
    )
    if item is None:
        raise HTTPException(
            status_code=404,
            detail=f"Ítem '{item_id}' no encontrado",
        )

    # exclude_unset distingue "campo no enviado" de "enviado como null":
    # categoria es nullable y debe poder limpiarse; el resto ignora null.
    data = body.model_dump(exclude_unset=True)

    if data.get("slug") is not None:
        conflict = (
            db.query(Menu)
            .filter(
                Menu.slug == data["slug"],
                Menu.restaurante_id == operador.restaurante_id,
                Menu.id != item_id,
            )
            .first()
        )
        if conflict:
            raise HTTPException(
                status_code=409,
                detail=f"El slug '{data['slug']}' ya está en uso",
            )
        item.slug = data["slug"]

    if data.get("nombre") is not None:
        item.nombre = data["nombre"]
    if data.get("precio") is not None:
        item.precio = data["precio"]
    if "categoria" in data:
        item.categoria = data["categoria"]
    if data.get("disponible") is not None:
        item.disponible = data["disponible"]

    db.commit()
    db.refresh(item)
    invalidar_menu(operador.restaurante_id)
    logger.info("Ítem menú actualizado: %s (%s)", item.nombre, item_id)
    return item


@router.delete("/menu/{item_id}", status_code=204)
def eliminar_item_menu(
    item_id: str,
    db: Session = Depends(get_db),
    operador: Operador = Depends(get_current_operador),
):
    """Elimina un ítem del menú del restaurante del operador."""
    item = (
        db.query(Menu)
        .filter(Menu.id == item_id, Menu.restaurante_id == operador.restaurante_id)
        .first()
    )
    if item is None:
        raise HTTPException(
            status_code=404,
            detail=f"Ítem '{item_id}' no encontrado",
        )
    db.delete(item)
    db.commit()
    invalidar_menu(operador.restaurante_id)
    logger.info("Ítem menú eliminado: %s", item_id)
