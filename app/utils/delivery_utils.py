"""
Utilidades de cobertura de domicilio.

validar_zona_domicilio — Verifica si una dirección cae dentro de las zonas cubiertas.
La validación es simple (coincidencia de subcadena insensible a mayúsculas) y sirve
como red de seguridad de último recurso; el agente LLM ya recibe las zonas en el prompt.
"""
from __future__ import annotations

from app.agent.prompts import ZONAS_COBERTURA


def validar_zona_domicilio(direccion: str, zonas: list[str] | None = None) -> bool:
    """
    Retorna True si la dirección pertenece a alguna zona cubierta.

    Args:
        direccion: Texto libre ingresado por el cliente.
        zonas: Lista de zonas permitidas (por defecto usa ZONAS_COBERTURA).

    Returns:
        True si se detecta al menos una zona cubierta en la dirección.
        False si la dirección no corresponde a ninguna zona (o si está vacía).
    """
    if not direccion or not direccion.strip():
        return False
    zonas_a_validar = zonas if zonas is not None else ZONAS_COBERTURA
    direccion_lower = direccion.lower()
    return any(z.lower() in direccion_lower for z in zonas_a_validar)
