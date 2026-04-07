"""
Servicio de menú: consultas a la tabla `menu` en PostgreSQL.
"""
from sqlalchemy.orm import Session
from app.models.menu import Menu
from app.utils.menu_utils import formatear_menu


def obtener_menu(db: Session, restaurante_id: str = "default") -> list[Menu]:
    return (
        db.query(Menu)
        .filter(Menu.restaurante_id == restaurante_id)
        .order_by(Menu.categoria, Menu.nombre)
        .all()
    )


def obtener_menu_formateado(db: Session, restaurante_id: str = "default") -> str:
    items = obtener_menu(db, restaurante_id)
    return formatear_menu(items)


def buscar_producto_por_slug(db: Session, slug: str) -> Menu | None:
    return db.query(Menu).filter(Menu.slug == slug).first()
