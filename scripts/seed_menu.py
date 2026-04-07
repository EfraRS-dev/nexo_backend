"""
Seed script: carga el menú del restaurante en la tabla `menu`.

Uso:
    python scripts/seed_menu.py
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from app.database import SessionLocal
from app.models.menu import Menu

MENU_SEED = {
    "hamburguesa_clasica": {
        "nombre": "Hamburguesa Clásica",
        "precio": 18000,
        "disponible": True,
        "categoria": "hamburguesas",
    },
    "hamburguesa_doble": {
        "nombre": "Hamburguesa Doble",
        "precio": 24000,
        "disponible": True,
        "categoria": "hamburguesas",
    },
    "perro_caliente": {
        "nombre": "Perro Caliente",
        "precio": 12000,
        "disponible": True,
        "categoria": "perros",
    },
      "salchipapa": {
        "nombre": "Salchipapa",
        "precio": 19000,
        "disponible": True,
        "categoria": "favoritos",
    },
    "papas_medianas": {
        "nombre": "Papas Medianas",
        "precio": 8000,
        "disponible": True,
        "categoria": "acompañantes",
    },
    "papas_grandes": {
        "nombre": "Papas Grandes",
        "precio": 10000,
        "disponible": True,
        "categoria": "acompañantes",
    },
    "gaseosa_personal": {
        "nombre": "Gaseosa Personal",
        "precio": 5000,
        "disponible": True,
        "categoria": "bebidas",
    },
    "gaseosa_grande": {
        "nombre": "Gaseosa Grande",
        "precio": 7000,
        "disponible": False,
        "categoria": "bebidas",
    },
    "jugo_natural": {
        "nombre": "Jugo Natural",
        "precio": 9000,
        "disponible": True,
        "categoria": "bebidas",
    },
    "combo_1": {
        "nombre": "Combo 1 (Hambur. + Papas + Gaseosa)",
        "precio": 28000,
        "disponible": True,
        "categoria": "combos",
    },
    "combo_2": {
        "nombre": "Combo 2 (Doble + Papas + Jugo)",
        "precio": 36000,
        "disponible": True,
        "categoria": "combos",
    },
}


def seed() -> None:
    db = SessionLocal()
    added = 0
    try:
        for slug, data in MENU_SEED.items():
            existing = db.query(Menu).filter_by(slug=slug).first()
            if existing:
                print(f"  [skip] {slug} — ya existe")
                continue
            item = Menu(
                slug=slug,
                nombre=data["nombre"],
                precio=data["precio"],
                disponible=data["disponible"],
                categoria=data.get("categoria"),
                restaurante_id="default",
            )
            db.add(item)
            print(f"  [add]  {slug} — {data['nombre']}")
            added += 1
        db.commit()
        print(f"\nSeed completado: {added} ítems añadidos.")
    except Exception as exc:
        db.rollback()
        print(f"Error durante el seed: {exc}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    seed()
