"""
Módulo de caché Redis para Nexo.

Proporciona helpers síncronos de get/set/delete con TTL y degradación
graciosa: si Redis no está disponible el sistema continúa sin caché.

Claves utilizadas:
  nexo:menu:{restaurante_id}        — menú formateado (texto plano)
  nexo:faq:{restaurante_id}:{hash}  — respuesta FAQ por pregunta hasheada
"""
from __future__ import annotations

import hashlib
import logging
from typing import Optional

import redis as redis_lib

from app.config import settings

logger = logging.getLogger(__name__)

_client: Optional[redis_lib.Redis] = None
_unavailable: bool = False  # bandera para no reintentar conexión fallida


def _get_client() -> Optional[redis_lib.Redis]:
    """Retorna el cliente Redis (lazy init). None si no está disponible."""
    global _client, _unavailable
    if _unavailable:
        return None
    if _client is None:
        try:
            _client = redis_lib.Redis.from_url(
                settings.redis_url,
                decode_responses=True,
                socket_connect_timeout=1,
                socket_timeout=1,
            )
            _client.ping()
            logger.info("Redis conectado en %s", settings.redis_url)
        except Exception as exc:
            logger.warning(
                "Redis no disponible (%s) — continuando sin caché", exc
            )
            _client = None
            _unavailable = True
    return _client


# ─────────────────────────────────────────────────────────────────────────────
# Primitivas públicas
# ─────────────────────────────────────────────────────────────────────────────

def cache_get(key: str) -> Optional[str]:
    r = _get_client()
    if r is None:
        return None
    try:
        return r.get(key)
    except Exception as exc:
        logger.debug("cache_get error: %s", exc)
        return None


def cache_set(key: str, value: str, ttl: int = 300) -> None:
    r = _get_client()
    if r is None:
        return
    try:
        r.setex(key, ttl, value)
    except Exception as exc:
        logger.debug("cache_set error: %s", exc)


def cache_delete(key: str) -> None:
    r = _get_client()
    if r is None:
        return
    try:
        r.delete(key)
    except Exception as exc:
        logger.debug("cache_delete error: %s", exc)


def cache_delete_pattern(pattern: str) -> None:
    """Elimina todas las claves que coincidan con el patrón (usa KEYS — solo desarrollo)."""
    r = _get_client()
    if r is None:
        return
    try:
        keys = r.keys(pattern)
        if keys:
            r.delete(*keys)
    except Exception as exc:
        logger.debug("cache_delete_pattern error: %s", exc)


# ─────────────────────────────────────────────────────────────────────────────
# Claves con convención
# ─────────────────────────────────────────────────────────────────────────────

def menu_cache_key(restaurante_id: str = "default") -> str:
    return f"nexo:menu:{restaurante_id}"


def faq_cache_key(question: str, restaurante_id: str = "default") -> str:
    digest = hashlib.sha256(question.lower().strip().encode()).hexdigest()[:16]
    return f"nexo:faq:{restaurante_id}:{digest}"


def invalidar_menu(restaurante_id: str = "default") -> None:
    """Borra caché del menú y de todas las FAQ asociadas al restaurante."""
    cache_delete(menu_cache_key(restaurante_id))
    cache_delete_pattern(f"nexo:faq:{restaurante_id}:*")
    logger.debug("Caché de menú y FAQ invalidada para restaurante '%s'", restaurante_id)
