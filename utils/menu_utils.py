def formatear_menu(menu: dict) -> str:
    lineas = []
    for item_id, item in menu.items():
        estado = "✅" if item["disponible"] else "❌ NO DISPONIBLE"
        lineas.append(f"- {item['nombre']} (${item['precio']:,} COP) [{estado}] — id: {item_id}")
    return "\n".join(lineas)
