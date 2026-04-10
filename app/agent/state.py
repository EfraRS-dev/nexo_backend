from typing import Annotated, Optional
from typing_extensions import TypedDict
from langgraph.graph.message import add_messages


class AgentState(TypedDict):
    # ── Mensajes de la conversación ─────────────────────────────────
    messages: Annotated[list, add_messages]

    # ── Ítems del pedido en curso ────────────────────────────────────
    # Cada ítem: {id, nombre, cantidad, precio_unitario, modificadores}
    items: list[dict]

    # ── Flags de flujo ───────────────────────────────────────────────
    pedido_listo: bool
    esperando_confirmacion: bool

    # ── Intención clasificada ────────────────────────────────────────
    # "pedir" | "faq" | "estado_pedido" | "escalamiento" | "conversacion"
    intencion: str

    # ── Tipo y destino del pedido ────────────────────────────────────
    tipo_pedido: str          # "llevar" | "domicilio"
    direccion_entrega: Optional[str]

    # ── Método de pago (solo para llevar) ────────────────────────────
    # "online" → link Wompi  |  "caja" → pago al recoger  |  "" → sin determinar
    metodo_pago: str

    # ── Comanda y pago (resultado final) ────────────────────────────
    comanda: Optional[dict]
    link_pago: Optional[str]

    # ── Identificadores de sesión ────────────────────────────────────
    telefono_cliente: str
    cliente_id: Optional[str]      # UUID del registro en DB
    conversacion_id: Optional[str] # UUID del registro en DB

    # ── Etapa global del flujo ───────────────────────────────────────
    # "conversando" | "confirmando" | "pagando" | "finalizado" | "escalado"
    etapa: str

    # ── Flags de control ─────────────────────────────────────────────
    requiere_escalamiento: bool
