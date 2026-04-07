from __future__ import annotations


def formatear_menu(menu) -> str:
    """Formatea el menú para incluirlo en el system prompt.

    Acepta:
    - dict: formato legado CLI  (slug -> {nombre, precio, disponible})
    - list: objetos ORM Menu de la tabla menu
    """
    lineas = []
    if isinstance(menu, dict):
        for item_id, item in menu.items():
            estado = "✅" if item["disponible"] else "❌ NO DISPONIBLE"
            lineas.append(
                f"- {item['nombre']} (${item['precio']:,} COP) [{estado}] — id: {item_id}"
            )
    else:
        for item in menu:
            estado = "✅" if item.disponible else "❌ NO DISPONIBLE"
            lineas.append(
                f"- {item.nombre} (${item.precio:,} COP) [{estado}] — id: {item.slug}"
            )
    return "\n".join(lineas)
