"""
Utilidades de cobertura de domicilio.

validar_zona_domicilio — Verifica si una dirección cae dentro de las zonas cubiertas.
La validación es simple (coincidencia de subcadena insensible a mayúsculas) y sirve
como red de seguridad de último recurso; el agente LLM ya recibe las zonas en el prompt.
"""
from __future__ import annotations

from typing import Any

from app.agent.prompts import (
    CONTACTO_DEFAULT,
    HORARIO_DEFAULT,
    METODOS_PAGO_DEFAULT,
    ZONAS_COBERTURA,
)


def _config(restaurante: Any) -> dict:
    """Devuelve el ``config_json`` del tenant (o {} si no hay restaurante/config)."""
    if restaurante is None:
        return {}
    return getattr(restaurante, "config_json", None) or {}


def obtener_zonas_cobertura(restaurante: Any = None) -> list[str]:
    """
    Fuente única de verdad de las zonas de cobertura de un tenant.

    Lee ``restaurante.config_json["zonas_cobertura"]`` si está definido y no vacío;
    en caso contrario usa la lista por defecto ``ZONAS_COBERTURA``. El resultado
    alimenta por igual el prompt de pedido, el de FAQ y la validación de domicilio,
    de modo que no puedan contradecirse (ver bug A-2 en docs/codebase-map.md).

    Args:
        restaurante: Objeto Restaurante (o cualquiera con atributo ``config_json``).
                     Si es None, retorna la lista por defecto.
    """
    zonas = _config(restaurante).get("zonas_cobertura")
    if zonas:
        return [str(z) for z in zonas]
    return ZONAS_COBERTURA


def obtener_horario(restaurante: Any = None) -> str:
    """Horario del tenant para la FAQ; default si no está en ``config_json``."""
    horario = str(_config(restaurante).get("horario") or "").strip()
    return horario or HORARIO_DEFAULT


def obtener_contacto(restaurante: Any = None) -> str:
    """
    Línea de contacto del tenant para la FAQ a partir de ``telefono_contacto``
    y/o ``email``; default si no hay ninguno en ``config_json``.
    """
    config = _config(restaurante)
    telefono = str(config.get("telefono_contacto") or "").strip()
    email = str(config.get("email") or "").strip()
    partes = []
    if telefono:
        partes.append(f"teléfono {telefono}")
    if email:
        partes.append(f"correo {email}")
    return " · ".join(partes) if partes else CONTACTO_DEFAULT


def obtener_metodos_pago_texto(restaurante: Any = None) -> str:
    """
    Métodos de pago del tenant para la FAQ a partir de ``metodos_pago``
    (lista con "online"/"caja"); default si no está en ``config_json``.
    """
    metodos = _config(restaurante).get("metodos_pago")
    if not metodos:
        return METODOS_PAGO_DEFAULT
    etiquetas = {
        "online": "pago en línea (Wompi: tarjeta, Nequi, PSE)",
        "caja": "pago en caja al recoger",
    }
    return ", ".join(etiquetas.get(str(m), str(m)) for m in metodos)


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
