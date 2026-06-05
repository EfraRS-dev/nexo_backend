"""
Servicio de restaurantes (tenants): resolución por número de WhatsApp.

Resuelve a qué restaurante pertenece un mensaje entrante a partir del número
destino de Twilio (`To`). Cachea el mapeo numero_whatsapp → restaurante_id
en Redis con degradación graciosa.
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.cache import cache_get, cache_set
from app.models.restaurante import Restaurante

logger = logging.getLogger(__name__)

RESTAURANTE_DEFAULT = "default"

# TTL de la caché del mapeo número → restaurante_id (segundos)
_CACHE_TTL = 3600


def _num_cache_key(numero: str) -> str:
    return f"nexo:restaurante:num:{numero}"


def obtener_restaurante(db: Session, restaurante_id: str) -> Restaurante | None:
    """Busca un restaurante por su id (slug)."""
    return db.query(Restaurante).filter(Restaurante.id == restaurante_id).first()


def resolver_restaurante_por_numero(db: Session, numero: str) -> Restaurante | None:
    """
    Resuelve el restaurante a partir del número de WhatsApp destino (Twilio `To`).
    Retorna el Restaurante activo o None si no hay match.

    Usa caché (numero → restaurante_id) para evitar un query por mensaje.
    """
    numero = (numero or "").replace("whatsapp:", "").strip()
    if not numero:
        return None

    cached_id = cache_get(_num_cache_key(numero))
    if cached_id is not None:
        rest = obtener_restaurante(db, cached_id)
        if rest is not None:
            return rest

    rest = (
        db.query(Restaurante)
        .filter(
            Restaurante.numero_whatsapp == numero,
            Restaurante.activo.is_(True),
        )
        .first()
    )
    if rest is not None:
        cache_set(_num_cache_key(numero), rest.id, _CACHE_TTL)
    return rest
