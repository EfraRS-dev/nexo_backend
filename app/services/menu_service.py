"""
Servicio de menú: consultas a la tabla `menu` en PostgreSQL.
"""
from sqlalchemy.orm import Session
from app.models.menu import Menu
from app.utils.menu_utils import formatear_menu
from app.cache import cache_get, cache_set, menu_cache_key
from app.config import settings


def obtener_menu(db: Session, restaurante_id: str) -> list[Menu]:
    return (
        db.query(Menu)
        .filter(Menu.restaurante_id == restaurante_id)
        .order_by(Menu.categoria, Menu.nombre)
        .all()
    )


def obtener_menu_formateado(db: Session, restaurante_id: str) -> str:
    key = menu_cache_key(restaurante_id)
    cached = cache_get(key)
    if cached is not None:
        return cached
    items = obtener_menu(db, restaurante_id)
    resultado = formatear_menu(items)
    cache_set(key, resultado, settings.cache_menu_ttl)
    return resultado


def buscar_producto_por_slug(db: Session, slug: str, restaurante_id: str) -> Menu | None:
    return (
        db.query(Menu)
        .filter(Menu.slug == slug, Menu.restaurante_id == restaurante_id)
        .first()
    )
