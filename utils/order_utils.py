import uuid
from datetime import datetime


def calcular_total(items: list[dict]) -> int:
    return sum(i["cantidad"] * i["precio_unitario"] for i in items)


def generar_comanda(items: list[dict], telefono: str) -> dict:
    return {
        "id": str(uuid.uuid4())[:8].upper(),
        "timestamp": datetime.now().isoformat(),
        "telefono": telefono,
        "items": items,
        "total": calcular_total(items),
        "estado": "pendiente_pago",
    }


def generar_link_pago(comanda: dict) -> str:
    # En produccion: llamada real a Wompi
    return f"https://checkout.wompi.co/p/?public-key=DEMO&amount-in-cents={comanda['total'] * 100}&reference={comanda['id']}"
