"""
Servicio de pagos: generación de links de checkout Wompi.
"""
from __future__ import annotations

import logging

from app.config import settings

logger = logging.getLogger(__name__)


def generar_link_pago(referencia: str, total_cop: int) -> str:
    """
    Genera el link de pago del checkout de Wompi.

    Args:
        referencia: ID legible del pedido (ej: NEX-AB12).
        total_cop: Total en pesos COP (se convierte a centavos internamente).

    Returns:
        URL del checkout de Wompi lista para enviar al cliente.
    """
    public_key = settings.wompi_public_key or "pub_stagtest_demo"
    monto_centavos = total_cop * 100

    url = (
        f"https://checkout.wompi.co/p/"
        f"?public-key={public_key}"
        f"&amount-in-cents={monto_centavos}"
        f"&reference={referencia}"
        f"&currency=COP"
    )
    logger.info("Link de pago generado para %s — $%s COP", referencia, total_cop)
    return url
