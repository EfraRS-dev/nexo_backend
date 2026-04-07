"""
Fixtures compartidos para los tests del agente Nexo.

Se mockean todas las llamadas al LLM de OpenAI para que los tests
sean determinísticos, rápidos y no requieran API key.
"""
import json
import os
import pytest
from unittest.mock import MagicMock, patch

from langchain_core.messages import AIMessage, HumanMessage

# Asegurar que .env no interfiera en tests
os.environ.setdefault("OPENAI_API_KEY", "test-key-fake")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from app.agent.state import AgentState


MENU_TEXTO_TEST = """1. Hamburguesa Clásica — $15,000 COP ✅
2. Hamburguesa Doble — $22,000 COP ✅
3. Combo 1 (Hamburguesa Clásica + Papas + Gaseosa) — $25,000 COP ✅
4. Combo 2 (Hamburguesa Doble + Papas Grandes + Gaseosa) — $35,000 COP ✅
5. Papas Medianas — $8,000 COP ✅
6. Papas Grandes — $12,000 COP ✅
7. Gaseosa Personal — $5,000 COP ✅
8. Gaseosa Litro — $9,000 COP ✅
9. Malteada — $14,000 COP ✅
10. Agua Botella — $4,000 COP ✅"""


def _make_state(**overrides) -> AgentState:
    """Crea un AgentState limpio con valores por defecto."""
    base: AgentState = {
        "messages": [],
        "items": [],
        "pedido_listo": False,
        "esperando_confirmacion": False,
        "intencion": "",
        "tipo_pedido": "",
        "direccion_entrega": None,
        "comanda": None,
        "link_pago": None,
        "telefono_cliente": "+573001234567",
        "cliente_id": None,
        "conversacion_id": None,
        "etapa": "conversando",
        "requiere_escalamiento": False,
    }
    base.update(overrides)
    return base


@pytest.fixture
def menu_texto():
    return MENU_TEXTO_TEST


@pytest.fixture
def estado_base():
    """Estado inicial limpio."""
    return _make_state()


@pytest.fixture
def estado_con_items():
    """Estado con un pedido en curso (1 item)."""
    return _make_state(
        items=[{
            "id": "combo-1",
            "nombre": "Combo 1",
            "cantidad": 1,
            "precio_unitario": 25000,
            "modificadores": {},
        }],
        messages=[
            HumanMessage(content="Quiero un combo 1"),
        ],
    )


@pytest.fixture
def estado_con_comanda():
    """Estado con una comanda generada (para test de estado_pedido)."""
    return _make_state(
        comanda={
            "referencia": "NEX-AB12",
            "items": [{"nombre": "Combo 1", "cantidad": 1, "precio_unitario": 25000}],
            "total": 25000,
            "tipo_pedido": "llevar",
            "direccion_entrega": None,
            "estado": "pagado",
        },
        messages=[
            HumanMessage(content="¿Cómo va mi pedido?"),
        ],
    )
