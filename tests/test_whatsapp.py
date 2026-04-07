"""
Tests de Fase 3: Integración WhatsApp (Twilio).

Cubre:
- Servicio de WhatsApp (enviar_mensaje, enviar_recibo)
- Servicio de conversaciones (crear, guardar, restaurar, finalizar, escalar)
- Servicio de clientes (obtener_o_crear)
- Webhook POST /webhooks/whatsapp (end-to-end con grafo mockeado)
"""
import json
import os
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

os.environ.setdefault("OPENAI_API_KEY", "test-key-fake")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")


# ═════════════════════════════════════════════════════════════════════════════
# 1. TESTS DEL SERVICIO DE WHATSAPP
# ═════════════════════════════════════════════════════════════════════════════

class TestWhatsappService:
    @patch("app.services.whatsapp_service._get_client")
    def test_enviar_mensaje_exitoso(self, mock_client):
        mock_msg = MagicMock()
        mock_msg.sid = "SM1234567890"
        mock_client.return_value.messages.create.return_value = mock_msg

        from app.services.whatsapp_service import enviar_mensaje
        sid = enviar_mensaje("+573001234567", "Hola, tu pedido está listo")

        assert sid == "SM1234567890"
        mock_client.return_value.messages.create.assert_called_once()
        call_kwargs = mock_client.return_value.messages.create.call_args
        assert "Hola, tu pedido está listo" in str(call_kwargs)

    @patch("app.services.whatsapp_service._get_client")
    def test_enviar_mensaje_falla_retorna_none(self, mock_client):
        mock_client.return_value.messages.create.side_effect = Exception("Twilio error")

        from app.services.whatsapp_service import enviar_mensaje
        sid = enviar_mensaje("+573001234567", "Test")

        assert sid is None

    @patch("app.services.whatsapp_service.enviar_mensaje")
    def test_enviar_recibo(self, mock_enviar):
        mock_enviar.return_value = "SM999"
        comanda = {
            "referencia": "NEX-AB12",
            "items": [
                {"nombre": "Combo 1", "cantidad": 1, "precio_unitario": 25000},
                {"nombre": "Gaseosa Personal", "cantidad": 2, "precio_unitario": 5000},
            ],
            "total": 35000,
            "tipo_pedido": "domicilio",
            "direccion_entrega": "Calle 123 #45-67",
        }

        from app.services.whatsapp_service import enviar_recibo
        sid = enviar_recibo("+573001234567", comanda)

        assert sid == "SM999"
        texto_enviado = mock_enviar.call_args[0][1]
        assert "NEX-AB12" in texto_enviado
        assert "$35,000 COP" in texto_enviado
        assert "Calle 123 #45-67" in texto_enviado
        assert "Domicilio" in texto_enviado

    @patch("app.services.whatsapp_service.enviar_mensaje")
    def test_enviar_recibo_llevar_sin_direccion(self, mock_enviar):
        mock_enviar.return_value = "SM888"
        comanda = {
            "referencia": "NEX-0001",
            "items": [{"nombre": "Hamburguesa", "cantidad": 1, "precio_unitario": 15000}],
            "total": 15000,
            "tipo_pedido": "llevar",
            "direccion_entrega": None,
        }

        from app.services.whatsapp_service import enviar_recibo
        enviar_recibo("+573001234567", comanda)

        texto = mock_enviar.call_args[0][1]
        assert "Llevar" in texto
        assert "Dirección" not in texto


# ═════════════════════════════════════════════════════════════════════════════
# 2. TESTS DEL SERVICIO DE CONVERSACIONES
# ═════════════════════════════════════════════════════════════════════════════

class TestConversationService:
    def test_restaurar_mensajes_vacio(self):
        from app.services.conversation_service import restaurar_mensajes

        conv = MagicMock()
        conv.mensajes = []
        result = restaurar_mensajes(conv)
        assert result == []

    def test_restaurar_mensajes_con_historial(self):
        from app.services.conversation_service import restaurar_mensajes

        conv = MagicMock()
        conv.mensajes = [
            {"role": "user", "content": "Hola"},
            {"role": "assistant", "content": "¡Bienvenido!"},
            {"role": "user", "content": "Quiero un combo"},
        ]
        result = restaurar_mensajes(conv)

        assert len(result) == 3
        assert isinstance(result[0], HumanMessage)
        assert isinstance(result[1], AIMessage)
        assert isinstance(result[2], HumanMessage)
        assert result[0].content == "Hola"
        assert result[1].content == "¡Bienvenido!"

    def test_restaurar_mensajes_ignora_roles_desconocidos(self):
        from app.services.conversation_service import restaurar_mensajes

        conv = MagicMock()
        conv.mensajes = [
            {"role": "user", "content": "Hola"},
            {"role": "system", "content": "System msg"},
            {"role": "assistant", "content": "Respuesta"},
        ]
        result = restaurar_mensajes(conv)
        assert len(result) == 2  # system ignorado

    def test_restaurar_mensajes_none(self):
        from app.services.conversation_service import restaurar_mensajes

        conv = MagicMock()
        conv.mensajes = None
        result = restaurar_mensajes(conv)
        assert result == []


# ═════════════════════════════════════════════════════════════════════════════
# 3. TESTS DEL SERVICIO DE CLIENTES
# ═════════════════════════════════════════════════════════════════════════════

class TestClientService:
    @patch("app.services.client_service.Cliente")
    def test_obtener_cliente_existente(self, MockCliente):
        from app.services.client_service import obtener_o_crear_cliente

        mock_db = MagicMock()
        mock_cliente = MagicMock()
        mock_cliente.id = "uuid-123"
        mock_cliente.telefono = "+573001234567"
        mock_db.query.return_value.filter.return_value.first.return_value = mock_cliente

        result = obtener_o_crear_cliente(mock_db, "+573001234567")

        assert result.id == "uuid-123"
        mock_db.add.assert_not_called()  # No se crea un nuevo registro

    @patch("app.services.client_service.Cliente")
    def test_crear_cliente_nuevo(self, MockCliente):
        from app.services.client_service import obtener_o_crear_cliente

        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = None  # No existe

        mock_new = MagicMock()
        mock_new.id = "uuid-new"
        mock_new.telefono = "+573009999999"
        MockCliente.return_value = mock_new
        mock_db.refresh = MagicMock()

        result = obtener_o_crear_cliente(mock_db, "+573009999999")

        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()


# ═════════════════════════════════════════════════════════════════════════════
# 4. TESTS DEL WEBHOOK (integración con FastAPI TestClient)
# ═════════════════════════════════════════════════════════════════════════════

class TestWebhookWhatsapp:
    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient
        from app.main import app
        return TestClient(app)

    @pytest.fixture(autouse=True)
    def skip_twilio_signature(self):
        """Omite validación de firma Twilio en todos los tests del webhook."""
        with patch("app.routers.whatsapp._validar_firma_twilio", return_value=True):
            yield

    @patch("app.routers.whatsapp.enviar_mensaje")
    @patch("app.routers.whatsapp.construir_grafo")
    @patch("app.routers.whatsapp.obtener_menu_formateado")
    @patch("app.routers.whatsapp.obtener_o_crear_conversacion")
    @patch("app.routers.whatsapp.obtener_o_crear_cliente")
    @patch("app.routers.whatsapp.guardar_mensajes")
    def test_mensaje_normal_ejecuta_grafo(
        self, mock_guardar, mock_cliente, mock_conv, mock_menu, mock_grafo, mock_enviar, client
    ):
        # Setup mocks
        mock_cliente_obj = MagicMock()
        mock_cliente_obj.id = "client-1"
        mock_cliente.return_value = mock_cliente_obj

        mock_conv_obj = MagicMock()
        mock_conv_obj.id = "conv-1"
        mock_conv_obj.mensajes = []
        mock_conv.return_value = mock_conv_obj

        mock_menu.return_value = "1. Hamburguesa — $15,000"

        # Mock del grafo: retorna estado con respuesta
        mock_invoke = MagicMock(return_value={
            "messages": [
                HumanMessage(content="Quiero un combo"),
                AIMessage(content="¡Perfecto! ¿Para llevar o domicilio?"),
            ],
            "etapa": "conversando",
        })
        mock_grafo.return_value.invoke = mock_invoke

        # Ejecutar webhook
        response = client.post(
            "/webhooks/whatsapp",
            data={"Body": "Quiero un combo", "From": "whatsapp:+573001234567"},
        )

        assert response.status_code == 200
        mock_enviar.assert_called_once()
        texto_enviado = mock_enviar.call_args[0][1]
        assert "llevar o domicilio" in texto_enviado.lower()

    @patch("app.routers.whatsapp.enviar_mensaje")
    def test_mensaje_vacio_responde_amable(self, mock_enviar, client):
        response = client.post(
            "/webhooks/whatsapp",
            data={"Body": "", "From": "whatsapp:+573001234567"},
        )

        assert response.status_code == 200
        mock_enviar.assert_called_once()
        texto = mock_enviar.call_args[0][1]
        assert "escribirlo de nuevo" in texto

    def test_sin_telefono_retorna_400(self, client):
        response = client.post(
            "/webhooks/whatsapp",
            data={"Body": "Hola", "From": ""},
        )

        assert response.status_code == 400

    @patch("app.routers.whatsapp.enviar_mensaje")
    @patch("app.routers.whatsapp.construir_grafo")
    @patch("app.routers.whatsapp.obtener_menu_formateado")
    @patch("app.routers.whatsapp.obtener_o_crear_conversacion")
    @patch("app.routers.whatsapp.obtener_o_crear_cliente")
    @patch("app.routers.whatsapp.guardar_mensajes")
    @patch("app.routers.whatsapp.finalizar_conversacion")
    def test_etapa_finalizado_cierra_conversacion(
        self, mock_finalizar, mock_guardar, mock_cliente, mock_conv,
        mock_menu, mock_grafo, mock_enviar, client
    ):
        mock_cliente.return_value = MagicMock(id="c1")
        mock_conv_obj = MagicMock(id="conv-1", mensajes=[])
        mock_conv.return_value = mock_conv_obj
        mock_menu.return_value = "menu"

        mock_grafo.return_value.invoke.return_value = {
            "messages": [AIMessage(content="Pedido finalizado")],
            "etapa": "finalizado",
        }

        client.post(
            "/webhooks/whatsapp",
            data={"Body": "Sí, confirmo", "From": "whatsapp:+573001234567"},
        )

        mock_finalizar.assert_called_once()

    @patch("app.routers.whatsapp.enviar_mensaje")
    @patch("app.routers.whatsapp.construir_grafo")
    @patch("app.routers.whatsapp.obtener_menu_formateado")
    @patch("app.routers.whatsapp.obtener_o_crear_conversacion")
    @patch("app.routers.whatsapp.obtener_o_crear_cliente")
    @patch("app.routers.whatsapp.guardar_mensajes")
    @patch("app.routers.whatsapp.escalar_conversacion")
    def test_etapa_escalado_marca_conversacion(
        self, mock_escalar, mock_guardar, mock_cliente, mock_conv,
        mock_menu, mock_grafo, mock_enviar, client
    ):
        mock_cliente.return_value = MagicMock(id="c1")
        mock_conv_obj = MagicMock(id="conv-1", mensajes=[])
        mock_conv.return_value = mock_conv_obj
        mock_menu.return_value = "menu"

        mock_grafo.return_value.invoke.return_value = {
            "messages": [AIMessage(content="Te conecto con un operador")],
            "etapa": "escalado",
        }

        client.post(
            "/webhooks/whatsapp",
            data={"Body": "Quiero hablar con una persona", "From": "whatsapp:+573001234567"},
        )

        mock_escalar.assert_called_once()

    @patch("app.routers.whatsapp.enviar_mensaje")
    @patch("app.routers.whatsapp.construir_grafo")
    @patch("app.routers.whatsapp.obtener_menu_formateado")
    @patch("app.routers.whatsapp.obtener_o_crear_conversacion")
    @patch("app.routers.whatsapp.obtener_o_crear_cliente")
    @patch("app.routers.whatsapp.guardar_mensajes")
    def test_error_grafo_responde_amablemente(
        self, mock_guardar, mock_cliente, mock_conv,
        mock_menu, mock_grafo, mock_enviar, client
    ):
        mock_cliente.return_value = MagicMock(id="c1")
        mock_conv.return_value = MagicMock(id="conv-1", mensajes=[])
        mock_menu.return_value = "menu"

        # El grafo lanza excepción
        mock_grafo.return_value.invoke.side_effect = RuntimeError("LLM timeout")

        response = client.post(
            "/webhooks/whatsapp",
            data={"Body": "Hola", "From": "whatsapp:+573001234567"},
        )

        assert response.status_code == 200
        mock_enviar.assert_called_once()
        texto = mock_enviar.call_args[0][1]
        assert "intentar de nuevo" in texto

    def test_health_endpoint(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"
