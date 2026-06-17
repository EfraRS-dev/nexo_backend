"""
Grafo LangGraph de Nexo.

Topología:
    START
      └─► nodo_clasificar
            ├─ "pedir"         ─► nodo_conversar ─► (router_conversar)
            │                         ├─ pedido_listo            ─► nodo_generar_comanda ─► nodo_pago ─► END
            │                         ├─ esperando_confirmacion  ─► nodo_confirmar ─► END
            │                         └─ END  (seguir conversando — el webhook vuelve a invocar)
            ├─ "faq"           ─► nodo_faq ─► END
            ├─ "estado_pedido" ─► nodo_estado_pedido ─► END
            └─ "escalamiento"  ─► nodo_escalamiento ─► END

Nota: el grafo se invoca UNA vez por mensaje entrante (desde el webhook de Twilio).
      El historial completo se pasa en state["messages"] en cada invocación.
"""
from __future__ import annotations

from functools import partial
from typing import Literal

from langgraph.graph import END, StateGraph

from app.agent.nodes import (
    nodo_clasificar,
    nodo_confirmar,
    nodo_conversar,
    nodo_escalamiento,
    nodo_estado_pedido,
    nodo_faq,
    nodo_generar_comanda,
    nodo_pago,
)
from app.agent.state import AgentState


# ─────────────────────────────────────────────────────────────────────────────
# Routers
# ─────────────────────────────────────────────────────────────────────────────

def _router_clasificar(
    state: AgentState,
) -> Literal["nodo_conversar", "nodo_faq", "nodo_estado_pedido", "nodo_escalamiento"]:
    intencion = state.get("intencion", "pedir")
    mapping = {
        "pedir": "nodo_conversar",
        "faq": "nodo_faq",
        "estado_pedido": "nodo_estado_pedido",
        "escalamiento": "nodo_escalamiento",
    }
    return mapping.get(intencion, "nodo_conversar")


def _router_conversar(
    state: AgentState,
) -> Literal["nodo_confirmar", "nodo_generar_comanda", "__end__"]:
    if state.get("pedido_listo"):
        return "nodo_generar_comanda"
    if state.get("esperando_confirmacion"):
        return "nodo_confirmar"
    return END


# ─────────────────────────────────────────────────────────────────────────────
# Factory: construir_grafo
# ─────────────────────────────────────────────────────────────────────────────

def construir_grafo(
    menu_texto: str = "",
    restaurante_nombre: str = "",
    zonas_texto: str = "",
    horario: str = "",
    contacto: str = "",
    metodos_pago: str = "",
) -> StateGraph:
    """
    Construye y compila el grafo LangGraph.

    Args:
        menu_texto: Texto formateado del menú (se inyecta en nodo_conversar).
                    Se puede pasar vacío y actualizarse en cada invocación
                    desde el webhook.
        restaurante_nombre: Nombre del restaurante (tenant) para los prompts.
        zonas_texto: Zonas de cobertura del tenant ya formateadas. Vacío → los
                     nodos usan el default _ZONAS_TEXTO. Misma fuente que la
                     validación de domicilio (ver bug A-2).
        horario, contacto, metodos_pago: Datos de la FAQ del tenant
                     (config_json). Vacío → los nodos usan los defaults.
    """
    builder = StateGraph(AgentState)

    # Envolvemos nodo_conversar/nodo_faq para inyectar menú, nombre y zonas del tenant
    conversar_con_menu = partial(
        nodo_conversar,
        menu_texto=menu_texto,
        restaurante_nombre=restaurante_nombre,
        zonas_texto=zonas_texto,
    )
    faq_con_menu = partial(
        nodo_faq,
        menu_texto=menu_texto,
        restaurante_nombre=restaurante_nombre,
        zonas_texto=zonas_texto,
        horario=horario,
        contacto=contacto,
        metodos_pago=metodos_pago,
    )

    # ── Registrar nodos ───────────────────────────────────────────────
    builder.add_node("nodo_clasificar", nodo_clasificar)
    builder.add_node("nodo_conversar", conversar_con_menu)
    builder.add_node("nodo_confirmar", nodo_confirmar)
    builder.add_node("nodo_faq", faq_con_menu)
    builder.add_node("nodo_estado_pedido", nodo_estado_pedido)
    builder.add_node("nodo_escalamiento", nodo_escalamiento)
    builder.add_node("nodo_generar_comanda", nodo_generar_comanda)
    builder.add_node("nodo_pago", nodo_pago)

    # ── Entry point ───────────────────────────────────────────────────
    builder.set_entry_point("nodo_clasificar")

    # ── Edges desde clasificar ────────────────────────────────────────
    builder.add_conditional_edges(
        "nodo_clasificar",
        _router_clasificar,
        {
            "nodo_conversar": "nodo_conversar",
            "nodo_faq": "nodo_faq",
            "nodo_estado_pedido": "nodo_estado_pedido",
            "nodo_escalamiento": "nodo_escalamiento",
        },
    )

    # ── Edges desde conversar ─────────────────────────────────────────
    builder.add_conditional_edges(
        "nodo_conversar",
        _router_conversar,
        {
            "nodo_confirmar": "nodo_confirmar",
            "nodo_generar_comanda": "nodo_generar_comanda",
            END: END,
        },
    )

    # nodo_confirmar termina la invocación — el siguiente mensaje del cliente
    # (sí/no) llega como una nueva invocación del webhook
    builder.add_edge("nodo_confirmar", END)

    # ── Edges lineales ────────────────────────────────────────────────
    builder.add_edge("nodo_generar_comanda", "nodo_pago")
    builder.add_edge("nodo_pago", END)
    builder.add_edge("nodo_faq", END)
    builder.add_edge("nodo_estado_pedido", END)
    builder.add_edge("nodo_escalamiento", END)

    return builder.compile()
