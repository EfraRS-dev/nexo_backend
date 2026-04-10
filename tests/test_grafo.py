"""
Tests del grafo LangGraph: verifica routing y comportamiento de cada flujo.

Todas las llamadas al LLM están mockeadas para que los tests
sean determinísticos y no consuman la API de OpenAI.
"""
import json
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.agent.graph import construir_grafo, _router_clasificar, _router_conversar
from app.agent.nodes import (
    nodo_clasificar,
    nodo_confirmar,
    nodo_escalamiento,
    nodo_estado_pedido,
    nodo_faq,
    nodo_generar_comanda,
    nodo_pago,
)
from app.agent.prompts import MSG_ESCALAMIENTO, MSG_ESTADO_SIN_PEDIDO

from tests.conftest import _make_state, MENU_TEXTO_TEST


# ─────────────────────────────────────────────────────────────────────────────
# Helpers para mockear LLM
# ─────────────────────────────────────────────────────────────────────────────

def _mock_ai_response(content: str) -> MagicMock:
    """Crea un mock de respuesta del LLM."""
    resp = MagicMock()
    resp.content = content
    return resp


# ─────────────────────────────────────────────────────────────────────────────
# 1. TESTS DE ROUTERS (sin LLM, puro estado)
# ─────────────────────────────────────────────────────────────────────────────

class TestRouterClasificar:
    def test_default_pedir(self):
        state = _make_state(intencion="pedir")
        assert _router_clasificar(state) == "nodo_conversar"

    def test_faq(self):
        state = _make_state(intencion="faq")
        assert _router_clasificar(state) == "nodo_faq"

    def test_estado_pedido(self):
        state = _make_state(intencion="estado_pedido")
        assert _router_clasificar(state) == "nodo_estado_pedido"

    def test_escalamiento(self):
        state = _make_state(intencion="escalamiento")
        assert _router_clasificar(state) == "nodo_escalamiento"

    def test_intencion_desconocida_route_a_conversar(self):
        state = _make_state(intencion="xyz_invalida")
        assert _router_clasificar(state) == "nodo_conversar"

    def test_sin_intencion_route_a_conversar(self):
        state = _make_state()
        assert _router_clasificar(state) == "nodo_conversar"


class TestRouterConversar:
    def test_pedido_listo_va_a_comanda(self):
        state = _make_state(pedido_listo=True)
        assert _router_conversar(state) == "nodo_generar_comanda"

    def test_esperando_confirmacion_va_a_confirmar(self):
        state = _make_state(esperando_confirmacion=True)
        assert _router_conversar(state) == "nodo_confirmar"

    def test_continua_conversacion(self):
        state = _make_state()
        assert _router_conversar(state) == "__end__"

    def test_pedido_listo_tiene_prioridad_sobre_confirmacion(self):
        state = _make_state(pedido_listo=True, esperando_confirmacion=True)
        assert _router_conversar(state) == "nodo_generar_comanda"


# ─────────────────────────────────────────────────────────────────────────────
# 2. TESTS DE NODOS INDIVIDUALES (LLM mockeado)
# ─────────────────────────────────────────────────────────────────────────────

class TestNodoClasificar:
    @patch("app.agent.nodes._get_classifier_llm")
    def test_clasifica_pedir(self, mock_llm):
        mock_llm.return_value.invoke.return_value = _mock_ai_response("pedir")
        state = _make_state(messages=[HumanMessage(content="Quiero una hamburguesa")])
        result = nodo_clasificar(state)
        assert result["intencion"] == "pedir"

    @patch("app.agent.nodes._get_classifier_llm")
    def test_clasifica_faq(self, mock_llm):
        mock_llm.return_value.invoke.return_value = _mock_ai_response("faq")
        state = _make_state(messages=[HumanMessage(content="¿Cuál es el horario?")])
        result = nodo_clasificar(state)
        assert result["intencion"] == "faq"

    @patch("app.agent.nodes._get_classifier_llm")
    def test_clasifica_estado_pedido(self, mock_llm):
        mock_llm.return_value.invoke.return_value = _mock_ai_response("estado_pedido")
        state = _make_state(messages=[HumanMessage(content="¿Cómo va mi pedido?")])
        result = nodo_clasificar(state)
        assert result["intencion"] == "estado_pedido"

    @patch("app.agent.nodes._get_classifier_llm")
    def test_clasifica_escalamiento(self, mock_llm):
        mock_llm.return_value.invoke.return_value = _mock_ai_response("escalamiento")
        state = _make_state(messages=[HumanMessage(content="Quiero hablar con una persona")])
        result = nodo_clasificar(state)
        assert result["intencion"] == "escalamiento"

    @patch("app.agent.nodes._get_classifier_llm")
    def test_intencion_invalida_default_pedir(self, mock_llm):
        mock_llm.return_value.invoke.return_value = _mock_ai_response("banana")
        state = _make_state(messages=[HumanMessage(content="blah")])
        result = nodo_clasificar(state)
        assert result["intencion"] == "pedir"

    def test_items_existentes_skip_clasificacion(self):
        """Si ya hay items, se mantiene 'pedir' sin llamar al LLM."""
        state = _make_state(
            items=[{"id": "combo-1", "nombre": "Combo 1", "cantidad": 1, "precio_unitario": 25000}],
            messages=[HumanMessage(content="¿Cuál es el horario?")],
        )
        result = nodo_clasificar(state)
        assert result["intencion"] == "pedir"

    def test_etapa_confirmando_skip_clasificacion(self):
        state = _make_state(
            etapa="confirmando",
            messages=[HumanMessage(content="Si, confirmo")],
        )
        result = nodo_clasificar(state)
        assert result["intencion"] == "pedir"


# ─────────────────────────────────────────────────────────────────────────────
# 3. FLUJO PEDIR — nodo_conversar
# ─────────────────────────────────────────────────────────────────────────────

class TestFlujoPedir:
    @patch("app.agent.nodes._get_llm_json")
    def test_respuesta_con_items(self, mock_llm):
        llm_json = json.dumps({
            "respuesta": "¡Perfecto! Un Combo 1. ¿Para llevar o domicilio?",
            "items": [{
                "id": "combo-1",
                "nombre": "Combo 1",
                "cantidad": 1,
                "precio_unitario": 25000,
                "modificadores": {},
            }],
            "tipo_pedido": "",
            "direccion_entrega": None,
            "pedido_listo": False,
            "esperando_confirmacion": False,
        })
        mock_llm.return_value.invoke.return_value = _mock_ai_response(llm_json)

        from app.agent.nodes import nodo_conversar
        state = _make_state(messages=[HumanMessage(content="Quiero un combo 1")])
        result = nodo_conversar(state, menu_texto=MENU_TEXTO_TEST)

        assert len(result["items"]) == 1
        assert result["items"][0]["nombre"] == "Combo 1"
        assert result["pedido_listo"] is False
        assert result["etapa"] == "conversando"
        # La respuesta del LLM se añade como AIMessage
        assert any("Combo 1" in m.content for m in result["messages"] if isinstance(m, AIMessage))

    @patch("app.agent.nodes._get_llm_json")
    def test_pedido_listo_cambia_etapa(self, mock_llm):
        llm_json = json.dumps({
            "respuesta": "Pedido confirmado. Generando enlace de pago...",
            "items": [{"id": "combo-1", "nombre": "Combo 1", "cantidad": 1, "precio_unitario": 25000}],
            "tipo_pedido": "llevar",
            "direccion_entrega": None,
            "pedido_listo": True,
            "esperando_confirmacion": False,
        })
        mock_llm.return_value.invoke.return_value = _mock_ai_response(llm_json)

        from app.agent.nodes import nodo_conversar
        state = _make_state(messages=[HumanMessage(content="Sí, confirmo")])
        result = nodo_conversar(state, menu_texto=MENU_TEXTO_TEST)

        assert result["pedido_listo"] is True
        assert result["etapa"] == "pagando"

    @patch("app.agent.nodes._get_llm_json")
    def test_esperando_confirmacion_cambia_etapa(self, mock_llm):
        llm_json = json.dumps({
            "respuesta": "Tu pedido: 1x Combo 1. Total: $25,000. ¿Confirmas?",
            "items": [{"id": "combo-1", "nombre": "Combo 1", "cantidad": 1, "precio_unitario": 25000}],
            "tipo_pedido": "llevar",
            "direccion_entrega": None,
            "pedido_listo": False,
            "esperando_confirmacion": True,
        })
        mock_llm.return_value.invoke.return_value = _mock_ai_response(llm_json)

        from app.agent.nodes import nodo_conversar
        state = _make_state(messages=[HumanMessage(content="Para llevar")])
        result = nodo_conversar(state, menu_texto=MENU_TEXTO_TEST)

        assert result["esperando_confirmacion"] is True
        assert result["etapa"] == "confirmando"

    @patch("app.agent.nodes._get_llm_json")
    def test_modificadores_por_producto(self, mock_llm):
        llm_json = json.dumps({
            "respuesta": "Combo 1 sin lechuga, anotado.",
            "items": [{
                "id": "combo-1",
                "nombre": "Combo 1",
                "cantidad": 1,
                "precio_unitario": 25000,
                "modificadores": {"Hamburguesa Clásica": {"sin": ["lechuga"]}},
            }],
            "tipo_pedido": "",
            "direccion_entrega": None,
            "pedido_listo": False,
            "esperando_confirmacion": False,
        })
        mock_llm.return_value.invoke.return_value = _mock_ai_response(llm_json)

        from app.agent.nodes import nodo_conversar
        state = _make_state(messages=[HumanMessage(content="Combo 1 sin lechuga")])
        result = nodo_conversar(state, menu_texto=MENU_TEXTO_TEST)

        mods = result["items"][0]["modificadores"]
        assert "Hamburguesa Clásica" in mods
        assert "lechuga" in mods["Hamburguesa Clásica"]["sin"]


# ─────────────────────────────────────────────────────────────────────────────
# 4. FLUJO FAQ — nodo_faq
# ─────────────────────────────────────────────────────────────────────────────

class TestFlujoFaq:
    @patch("app.agent.nodes._get_llm")
    def test_responde_horario(self, mock_llm):
        mock_llm.return_value.invoke.return_value = _mock_ai_response(
            "Nuestro horario es de lunes a domingo de 11:00 a.m. a 10:00 p.m."
        )
        state = _make_state(messages=[HumanMessage(content="¿Cuál es el horario?")])
        result = nodo_faq(state, menu_texto=MENU_TEXTO_TEST)

        assert len(result["messages"]) > 0
        last_msg = result["messages"][-1]
        assert isinstance(last_msg, AIMessage)
        assert "horario" in last_msg.content.lower()

    @patch("app.agent.nodes._get_llm")
    def test_faq_recibe_menu(self, mock_llm):
        """Verifica que el FAQ_PROMPT se formatee con el menú."""
        mock_llm.return_value.invoke.return_value = _mock_ai_response("Te recomiendo el Combo 1.")
        state = _make_state(messages=[HumanMessage(content="¿Qué me recomiendas?")])
        nodo_faq(state, menu_texto=MENU_TEXTO_TEST)

        # El SystemMessage pasado al LLM debe contener el menú
        call_args = mock_llm.return_value.invoke.call_args[0][0]
        system_content = call_args[0].content
        assert "Hamburguesa Clásica" in system_content

    @patch("app.agent.nodes._get_llm")
    def test_faq_sin_menu_muestra_fallback(self, mock_llm):
        mock_llm.return_value.invoke.return_value = _mock_ai_response("No tengo el menú disponible.")
        state = _make_state(messages=[HumanMessage(content="¿Qué tienen?")])
        nodo_faq(state, menu_texto="")

        call_args = mock_llm.return_value.invoke.call_args[0][0]
        system_content = call_args[0].content
        assert "(menú no disponible)" in system_content

    @patch("app.agent.nodes._get_llm")
    def test_faq_no_modifica_items_ni_etapa(self, mock_llm):
        """FAQ no debe cambiar items, etapa ni otros campos de pedido."""
        mock_llm.return_value.invoke.return_value = _mock_ai_response("Aceptamos Nequi y PSE.")
        state = _make_state(messages=[HumanMessage(content="¿Qué métodos de pago aceptan?")])
        result = nodo_faq(state, menu_texto=MENU_TEXTO_TEST)

        assert "items" not in result
        assert "etapa" not in result
        assert "pedido_listo" not in result


# ─────────────────────────────────────────────────────────────────────────────
# 5. FLUJO ESTADO DE PEDIDO — nodo_estado_pedido
# ─────────────────────────────────────────────────────────────────────────────

class TestFlujoEstadoPedido:
    def test_con_comanda_muestra_estado(self):
        state = _make_state(
            comanda={
                "referencia": "NEX-AB12",
                "estado": "pagado",
            },
            messages=[HumanMessage(content="¿Cómo va mi pedido?")],
        )
        result = nodo_estado_pedido(state)

        last_msg = result["messages"][-1]
        assert isinstance(last_msg, AIMessage)
        assert "NEX-AB12" in last_msg.content
        assert "pago recibido" in last_msg.content

    def test_sin_comanda_muestra_mensaje_default(self):
        state = _make_state(messages=[HumanMessage(content="¿Cómo va mi pedido?")])
        result = nodo_estado_pedido(state)

        last_msg = result["messages"][-1]
        assert last_msg.content == MSG_ESTADO_SIN_PEDIDO

    def test_todos_los_estados(self):
        """Verifica que todos los estados mapeados produzcan respuesta."""
        for estado_val in ("pendiente", "confirmado", "pagado", "preparando", "en_camino", "entregado"):
            state = _make_state(
                comanda={"referencia": "NEX-0001", "estado": estado_val},
                messages=[HumanMessage(content="Estado?")],
            )
            result = nodo_estado_pedido(state)
            assert isinstance(result["messages"][-1], AIMessage)

    def test_no_modifica_items_ni_etapa(self):
        state = _make_state(messages=[HumanMessage(content="Estado?")])
        result = nodo_estado_pedido(state)
        assert "items" not in result
        assert "etapa" not in result


# ─────────────────────────────────────────────────────────────────────────────
# 6. FLUJO ESCALAMIENTO — nodo_escalamiento
# ─────────────────────────────────────────────────────────────────────────────

class TestFlujoEscalamiento:
    def test_marca_escalamiento(self):
        state = _make_state(
            messages=[HumanMessage(content="Quiero hablar con una persona")],
        )
        result = nodo_escalamiento(state)

        assert result["requiere_escalamiento"] is True
        assert result["etapa"] == "escalado"
        last_msg = result["messages"][-1]
        assert isinstance(last_msg, AIMessage)
        assert last_msg.content == MSG_ESCALAMIENTO

    def test_mensaje_contiene_operador(self):
        state = _make_state(messages=[HumanMessage(content="Necesito ayuda real")])
        result = nodo_escalamiento(state)
        assert "operador" in result["messages"][-1].content.lower()


# ─────────────────────────────────────────────────────────────────────────────
# 7. NODOS DE CIERRE — nodo_confirmar, nodo_generar_comanda, nodo_pago
# ─────────────────────────────────────────────────────────────────────────────

class TestNodoConfirmar:
    def test_cambia_etapa_a_confirmando(self):
        state = _make_state(esperando_confirmacion=True)
        result = nodo_confirmar(state)
        assert result["etapa"] == "confirmando"


class TestNodoGenerarComanda:
    def test_genera_comanda_con_referencia(self):
        state = _make_state(
            items=[
                {"id": "combo-1", "nombre": "Combo 1", "cantidad": 1, "precio_unitario": 25000},
                {"id": "gaseosa", "nombre": "Gaseosa Personal", "cantidad": 1, "precio_unitario": 5000},
            ],
            tipo_pedido="llevar",
            messages=[HumanMessage(content="Confirmo")],
        )
        result = nodo_generar_comanda(state)

        assert result["comanda"] is not None
        assert result["comanda"]["referencia"] == "PENDIENTE"
        assert result["comanda"]["total"] == 30000
        assert result["comanda"]["tipo_pedido"] == "llevar"
        assert result["etapa"] == "pagando"

    def test_comanda_incluye_direccion_domicilio(self):
        state = _make_state(
            items=[{"id": "combo-1", "nombre": "Combo 1", "cantidad": 1, "precio_unitario": 25000}],
            tipo_pedido="domicilio",
            direccion_entrega="Calle 123 #45-67",
            messages=[HumanMessage(content="Confirmo")],
        )
        result = nodo_generar_comanda(state)
        assert result["comanda"]["direccion_entrega"] == "Calle 123 #45-67"
        assert result["comanda"]["tipo_pedido"] == "domicilio"


class TestNodoPago:
    def test_online_avisa_enlace_pendiente(self):
        """Para metodo_pago online (o vacío), avisa que el link llegará pronto."""
        state = _make_state(
            comanda={"referencia": "PENDIENTE", "total": 25000},
            metodo_pago="online",
            messages=[HumanMessage(content="ok")],
        )
        result = nodo_pago(state)
        last_msg = result["messages"][-1].content
        assert "enlace de pago" in last_msg.lower()
        assert result["etapa"] == "finalizado"
        # El link de pago NO se genera en el nodo — lo envía el webhook
        assert result.get("link_pago") is None

    def test_caja_confirma_sin_enlace(self):
        """Para metodo_pago caja, envía confirmación sin generar link Wompi."""
        state = _make_state(
            comanda={"referencia": "PENDIENTE", "total": 25000},
            metodo_pago="caja",
            messages=[HumanMessage(content="en caja")],
        )
        result = nodo_pago(state)
        last_msg = result["messages"][-1].content
        assert "pedido" in last_msg.lower()
        assert "wompi.co" not in last_msg
        assert result["etapa"] == "finalizado"


# ─────────────────────────────────────────────────────────────────────────────
# 8. TEST DE INTEGRACIÓN — Grafo completo (LLM mockeado)
# ─────────────────────────────────────────────────────────────────────────────

class TestGrafoIntegracion:
    def test_grafo_compila_con_todos_los_nodos(self):
        grafo = construir_grafo(MENU_TEXTO_TEST)
        nodos = list(grafo.get_graph().nodes)
        assert "nodo_clasificar" in nodos
        assert "nodo_conversar" in nodos
        assert "nodo_faq" in nodos
        assert "nodo_estado_pedido" in nodos
        assert "nodo_escalamiento" in nodos
        assert "nodo_confirmar" in nodos
        assert "nodo_generar_comanda" in nodos
        assert "nodo_pago" in nodos

    @patch("app.agent.nodes._get_classifier_llm")
    @patch("app.agent.nodes._get_llm")
    def test_flujo_faq_end_to_end(self, mock_llm, mock_classifier):
        """Grafo completo: mensaje FAQ → nodo_clasificar → nodo_faq → END."""
        mock_classifier.return_value.invoke.return_value = _mock_ai_response("faq")
        mock_llm.return_value.invoke.return_value = _mock_ai_response(
            "Nuestro horario es de 11am a 10pm todos los días."
        )

        grafo = construir_grafo(MENU_TEXTO_TEST)
        state = _make_state(messages=[HumanMessage(content="¿Cuál es el horario?")])
        result = grafo.invoke(state)

        assert result["intencion"] == "faq"
        assert any("horario" in m.content.lower() for m in result["messages"] if isinstance(m, AIMessage))

    @patch("app.agent.nodes._get_classifier_llm")
    def test_flujo_escalamiento_end_to_end(self, mock_classifier):
        """Grafo completo: mensaje de escalamiento → END con etapa 'escalado'."""
        mock_classifier.return_value.invoke.return_value = _mock_ai_response("escalamiento")

        grafo = construir_grafo(MENU_TEXTO_TEST)
        state = _make_state(messages=[HumanMessage(content="Quiero hablar con alguien")])
        result = grafo.invoke(state)

        assert result["intencion"] == "escalamiento"
        assert result["etapa"] == "escalado"
        assert result["requiere_escalamiento"] is True

    @patch("app.agent.nodes._get_classifier_llm")
    def test_flujo_estado_sin_pedido_end_to_end(self, mock_classifier):
        """Grafo completo: consulta estado sin comanda → MSG_ESTADO_SIN_PEDIDO."""
        mock_classifier.return_value.invoke.return_value = _mock_ai_response("estado_pedido")

        grafo = construir_grafo(MENU_TEXTO_TEST)
        state = _make_state(messages=[HumanMessage(content="¿Cómo va mi pedido?")])
        result = grafo.invoke(state)

        assert result["intencion"] == "estado_pedido"
        last_ai = [m for m in result["messages"] if isinstance(m, AIMessage)][-1]
        assert last_ai.content == MSG_ESTADO_SIN_PEDIDO

    @patch("app.agent.nodes._get_classifier_llm")
    @patch("app.agent.nodes._get_llm_json")
    def test_flujo_pedir_simple_end_to_end(self, mock_llm_json, mock_classifier):
        """Grafo completo: pedir un combo → respuesta con items → END (sin confirmar aún)."""
        mock_classifier.return_value.invoke.return_value = _mock_ai_response("pedir")
        mock_llm_json.return_value.invoke.return_value = _mock_ai_response(json.dumps({
            "respuesta": "¡Claro! Un Combo 1. ¿Para llevar o a domicilio?",
            "items": [{"id": "combo-1", "nombre": "Combo 1", "cantidad": 1, "precio_unitario": 25000}],
            "tipo_pedido": "",
            "direccion_entrega": None,
            "pedido_listo": False,
            "esperando_confirmacion": False,
        }))

        grafo = construir_grafo(MENU_TEXTO_TEST)
        state = _make_state(messages=[HumanMessage(content="Quiero un combo 1")])
        result = grafo.invoke(state)

        assert result["intencion"] == "pedir"
        assert len(result["items"]) == 1
        assert result["items"][0]["nombre"] == "Combo 1"
