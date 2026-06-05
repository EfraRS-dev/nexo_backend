"""
Observabilidad LLM con Langfuse 4.x.

En Langfuse 4.x el CallbackHandler ya no acepta secret_key/host/session_id/user_id
en su constructor — esos datos se toman de las variables de entorno
(LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, LANGFUSE_BASE_URL) y del context
manager `propagate_attributes`.

Uso en whatsapp.py:
    from app.observability import make_langfuse_handler, langfuse_context

    handler = make_langfuse_handler()
    lf_config = {"callbacks": [handler]} if handler else {}

    with langfuse_context(session_id=conv.id, user_id=telefono, trace_name="whatsapp-message"):
        resultado = await asyncio.to_thread(grafo.invoke, estado, lf_config)

El context manager propaga session_id/user_id a todos los spans dentro del bloque,
incluidos los que se ejecutan en el hilo secundario lanzado por asyncio.to_thread
(contextvars se propagan automáticamente).
"""
from __future__ import annotations

import logging
import os
from contextlib import nullcontext
from typing import Any, List, Optional

logger = logging.getLogger(__name__)


def make_langfuse_handler() -> Optional[object]:
    """
    Crea un CallbackHandler de Langfuse (Langfuse 4.x).

    Las credenciales y el host se leen automáticamente de las variables de entorno:
        LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, LANGFUSE_BASE_URL

    Returns:
        CallbackHandler listo para pasar en lf_config["callbacks"], o None si
        Langfuse no está configurado o no está instalado.
    """
    try:
        from app.config import settings  # importación local para evitar ciclos

        if not settings.langfuse_public_key or not settings.langfuse_secret_key:
            logger.debug("Langfuse no configurado — tracing deshabilitado")
            return None

        # Langfuse 4.x lee las credenciales de las variables de entorno.
        # pydantic-settings carga el .env en el objeto Settings pero NO exporta
        # a os.environ, así que las propagamos aquí antes de crear el handler.
        os.environ.setdefault("LANGFUSE_PUBLIC_KEY", settings.langfuse_public_key)
        os.environ.setdefault("LANGFUSE_SECRET_KEY", settings.langfuse_secret_key)
        os.environ.setdefault("LANGFUSE_HOST", settings.langfuse_host)

        from langfuse.langchain import CallbackHandler  # type: ignore

        handler = CallbackHandler()
        logger.debug("Langfuse CallbackHandler creado")
        return handler

    except ImportError:
        logger.warning("langfuse no instalado — tracing deshabilitado")
        return None
    except Exception as exc:
        logger.warning("Error creando Langfuse handler: %s", exc)
        return None


def langfuse_context(
    session_id: str = "",
    user_id: str = "",
    trace_name: str = "nexo-agent",
    tags: Optional[List[str]] = None,
) -> Any:
    """
    Context manager que propaga atributos de traza (session_id, user_id, etc.)
    a todos los spans creados dentro del bloque — incluidos los de hilos secundarios.

    Si Langfuse no está disponible devuelve un nullcontext() inerte.

    Uso:
        with langfuse_context(session_id="...", user_id="...", trace_name="..."):
            resultado = await asyncio.to_thread(grafo.invoke, estado, lf_config)
    """
    try:
        from langfuse import propagate_attributes  # type: ignore

        return propagate_attributes(
            session_id=session_id or None,
            user_id=user_id or None,
            trace_name=trace_name or None,
            tags=tags or ["nexo", "whatsapp"],
        )
    except Exception:
        return nullcontext()
