"""
Observabilidad LLM con Langfuse.

Si LANGFUSE_PUBLIC_KEY y LANGFUSE_SECRET_KEY están configurados en .env,
make_langfuse_handler() retorna un CallbackHandler listo para LangChain/LangGraph.
Si no están configurados (o langfuse no está instalado), retorna None y el
sistema continúa sin tracing — degradación graciosa.

Uso en whatsapp.py:
    from app.observability import make_langfuse_handler
    handler = make_langfuse_handler(session_id=conv.id, user_id=telefono)
    resultado = grafo.invoke(estado, {"callbacks": [handler]} if handler else {})

El handler se pasa al grafo y cada nodo lo recibe vía RunnableConfig, luego
lo propaga a sus llamadas LLM con config={"callbacks": callbacks}.
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def make_langfuse_handler(
    session_id: str = "",
    user_id: str = "",
    trace_name: str = "nexo-agent",
) -> Optional[object]:
    """
    Crea un CallbackHandler de Langfuse con contexto de sesión y usuario.

    Args:
        session_id: ID de la conversación — agrupa todos los turnos de un chat.
        user_id:    Teléfono del cliente — identifica al usuario en Langfuse.
        trace_name: Nombre de la traza raíz visible en el dashboard.

    Returns:
        CallbackHandler listo para usar, o None si Langfuse no está disponible.
    """
    try:
        from app.config import settings  # importación local para evitar ciclos

        if not settings.langfuse_public_key or not settings.langfuse_secret_key:
            logger.debug("Langfuse no configurado — tracing deshabilitado")
            return None

        from langfuse.langchain import CallbackHandler  # type: ignore

        handler = CallbackHandler(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host,
            session_id=session_id,
            user_id=user_id,
            trace_name=trace_name,
            tags=["nexo", "whatsapp"],
        )
        logger.debug(
            "Langfuse handler creado — session=%s user=%s", session_id, user_id
        )
        return handler

    except ImportError:
        logger.warning("langfuse no instalado — tracing deshabilitado")
        return None
    except Exception as exc:
        logger.warning("Error creando Langfuse handler: %s", exc)
        return None
