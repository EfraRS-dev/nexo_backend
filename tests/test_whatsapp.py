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

    @pytest.fixture(autouse=True)
    def inline_queue(self):
        """
        Reemplaza la cola async con procesamiento inline para que los tests
        puedan hacer assert en los efectos secundarios de forma síncrona.
        """
        import app.routers.whatsapp as wa

        fake_rest = MagicMock(id="default", nombre="Test Restaurante")

        async def mock_ensure_worker(clave) -> None:
            telefono, numero_destino = clave

            class _InlineQueue:
                async def put(self, mensaje) -> None:
                    db = MagicMock()
                    await wa._procesar_mensaje(db, telefono, mensaje, numero_destino)

                def task_done(self) -> None:
                    pass

            wa._phone_queues[clave] = _InlineQueue()

        with patch.object(wa, "_ensure_worker", mock_ensure_worker), \
             patch.object(wa, "resolver_restaurante_por_numero", return_value=fake_rest):
            wa._phone_queues.clear()
            yield
            wa._phone_queues.clear()

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


# ═════════════════════════════════════════════════════════════════════════════
# 5. TESTS DE FASE 5 — Edge cases, media, timeout, rate limit, zona
# ═════════════════════════════════════════════════════════════════════════════

class TestFase5EdgeCases:
    """Tests para las funcionalidades nuevas de Fase 5."""

    # ── Delivery utils ────────────────────────────────────────────────

    def test_zona_valida_detectada(self):
        from app.utils.delivery_utils import validar_zona_domicilio
        assert validar_zona_domicilio("Calle 10 # 5-20, El Centro") is True

    def test_zona_valida_el_prado(self):
        from app.utils.delivery_utils import validar_zona_domicilio
        assert validar_zona_domicilio("Carrera 54 # 72-40, El Prado") is True

    def test_zona_fuera_de_cobertura(self):
        from app.utils.delivery_utils import validar_zona_domicilio
        assert validar_zona_domicilio("Autopista Norte km 12, Usaquén") is False

    def test_zona_direccion_vacia(self):
        from app.utils.delivery_utils import validar_zona_domicilio
        assert validar_zona_domicilio("") is False
        assert validar_zona_domicilio("   ") is False

    def test_zona_custom_lista(self):
        from app.utils.delivery_utils import validar_zona_domicilio
        assert validar_zona_domicilio("Carrera 1 en Zona Norte", ["zona norte"]) is True
        assert validar_zona_domicilio("Carrera 1 en Zona Sur", ["zona norte"]) is False

    # ── Timeout de conversación ───────────────────────────────────────

    def test_timeout_conversacion_activa(self):
        from app.services.conversation_service import hay_timeout_conversacion
        from datetime import timedelta
        conv = MagicMock()
        conv.updated_at = datetime.now(timezone.utc) - timedelta(minutes=10)
        assert hay_timeout_conversacion(conv, timeout_min=30) is False

    def test_timeout_conversacion_expirada(self):
        from app.services.conversation_service import hay_timeout_conversacion
        from datetime import timedelta
        conv = MagicMock()
        conv.updated_at = datetime.now(timezone.utc) - timedelta(minutes=45)
        assert hay_timeout_conversacion(conv, timeout_min=30) is True

    def test_timeout_conversacion_sin_fecha(self):
        from app.services.conversation_service import hay_timeout_conversacion
        conv = MagicMock()
        conv.updated_at = None
        assert hay_timeout_conversacion(conv) is False

    def test_timeout_conversacion_updated_at_no_datetime(self):
        """MagicMock como updated_at no debe lanzar excepción."""
        from app.services.conversation_service import hay_timeout_conversacion
        conv = MagicMock()
        # updated_at es un MagicMock (no datetime) — debe retornar False sin error
        assert hay_timeout_conversacion(conv) is False

    # ── Rate limiting ─────────────────────────────────────────────────

    def test_rate_limit_permite_mensajes_normales(self):
        import app.routers.whatsapp as wa
        wa._rate_limits.clear()
        telefono = "+573009990001"
        for _ in range(5):
            assert wa._esta_bajo_limite(telefono) is True

    def test_rate_limit_bloquea_tras_exceder(self):
        import app.routers.whatsapp as wa
        wa._rate_limits.clear()
        telefono = "+573009990002"
        for _ in range(wa._RATE_LIMIT_PER_MIN):
            wa._esta_bajo_limite(telefono)
        # El siguiente debe ser bloqueado
        assert wa._esta_bajo_limite(telefono) is False

    # ── Webhook: mensajes de audio/media ─────────────────────────────

    class _WebhookFixtures:
        @pytest.fixture
        def client(self):
            from fastapi.testclient import TestClient
            from app.main import app
            return TestClient(app)

        @pytest.fixture(autouse=True)
        def skip_twilio_signature(self):
            with patch("app.routers.whatsapp._validar_firma_twilio", return_value=True):
                yield

    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient
        from app.main import app
        return TestClient(app)

    @pytest.fixture(autouse=True)
    def skip_twilio_signature(self):
        with patch("app.routers.whatsapp._validar_firma_twilio", return_value=True):
            yield

    @pytest.fixture(autouse=True)
    def inline_queue(self):
        """Procesamiento inline + limpia rate limits entre tests de webhook."""
        import app.routers.whatsapp as wa

        fake_rest = MagicMock(id="default", nombre="Test Restaurante")

        async def mock_ensure_worker(clave) -> None:
            telefono, numero_destino = clave

            class _InlineQueue:
                async def put(self, mensaje) -> None:
                    db = MagicMock()
                    await wa._procesar_mensaje(db, telefono, mensaje, numero_destino)

                def task_done(self) -> None:
                    pass

            wa._phone_queues[clave] = _InlineQueue()

        wa._rate_limits.clear()
        with patch.object(wa, "_ensure_worker", mock_ensure_worker), \
             patch.object(wa, "resolver_restaurante_por_numero", return_value=fake_rest):
            wa._phone_queues.clear()
            yield
            wa._phone_queues.clear()
        wa._rate_limits.clear()

    @patch("app.routers.whatsapp.enviar_mensaje")
    def test_audio_sin_texto_responde_indicacion(self, mock_enviar, client):
        """Cuando llega un audio (NumMedia=1, Body vacío) informa que solo procesa texto."""
        response = client.post(
            "/webhooks/whatsapp",
            data={"Body": "", "From": "whatsapp:+573001234567", "NumMedia": "1"},
        )
        assert response.status_code == 200
        texto = mock_enviar.call_args[0][1]
        assert "texto" in texto.lower()

    @patch("app.routers.whatsapp.enviar_mensaje")
    @patch("app.routers.whatsapp.construir_grafo")
    @patch("app.routers.whatsapp.obtener_menu_formateado")
    @patch("app.routers.whatsapp.obtener_o_crear_conversacion")
    @patch("app.routers.whatsapp.obtener_o_crear_cliente")
    @patch("app.routers.whatsapp.guardar_mensajes")
    @patch("app.routers.whatsapp.obtener_ultimo_pedido")
    def test_audio_con_texto_procesa_texto(
        self, mock_ultimo, mock_guardar, mock_cliente, mock_conv,
        mock_menu, mock_grafo, mock_enviar, client
    ):
        """Si hay Body con texto y NumMedia>0, el texto se procesa (audio ignorado)."""
        mock_cliente.return_value = MagicMock(id="c1")
        mock_conv.return_value = MagicMock(id="conv-1", mensajes=[])
        mock_menu.return_value = "menu"
        mock_ultimo.return_value = None
        mock_grafo.return_value.invoke.return_value = {
            "messages": [AIMessage(content="Hola")],
            "etapa": "conversando",
        }

        response = client.post(
            "/webhooks/whatsapp",
            data={"Body": "Quiero una hamburguesa", "From": "whatsapp:+573001234567", "NumMedia": "1"},
        )
        assert response.status_code == 200
        mock_grafo.return_value.invoke.assert_called_once()

    @patch("app.routers.whatsapp.enviar_mensaje")
    def test_rate_limit_webhook_responde_amable(self, mock_enviar, client):
        """Cuando se supera el rate limit, el webhook responde 200 con mensaje cortés."""
        import app.routers.whatsapp as wa
        telefono = "+573001111222"
        # Llenar el bucket
        for _ in range(wa._RATE_LIMIT_PER_MIN):
            wa._esta_bajo_limite(telefono)

        response = client.post(
            "/webhooks/whatsapp",
            data={"Body": "Mensaje extra", "From": f"whatsapp:{telefono}"},
        )
        assert response.status_code == 200
        texto = mock_enviar.call_args[0][1]
        assert "espera" in texto.lower()

    # ── obtener_ultimo_pedido ─────────────────────────────────────────

    def test_obtener_ultimo_pedido_retorna_mas_reciente(self):
        from app.services.order_service import obtener_ultimo_pedido

        pedido_reciente = MagicMock()
        pedido_reciente.referencia = "NEX-000002"

        mock_db = MagicMock()
        (mock_db.query.return_value
            .filter.return_value
            .order_by.return_value
            .first.return_value) = pedido_reciente

        resultado = obtener_ultimo_pedido(mock_db, "cliente-uuid", "default")
        assert resultado.referencia == "NEX-000002"

    def test_obtener_ultimo_pedido_sin_pedidos(self):
        from app.services.order_service import obtener_ultimo_pedido

        mock_db = MagicMock()
        (mock_db.query.return_value
            .filter.return_value
            .order_by.return_value
            .first.return_value) = None

        assert obtener_ultimo_pedido(mock_db, "cliente-uuid", "default") is None

    # ── Conversation service: expirar_conversacion ────────────────────

    def test_expirar_conversacion_cambia_estado(self):
        from app.services.conversation_service import expirar_conversacion

        mock_db = MagicMock()
        conv = MagicMock()
        expirar_conversacion(mock_db, conv)

        assert conv.estado == "expirada"
        mock_db.commit.assert_called_once()

    # ── Webhook: zona fuera de cobertura ──────────────────────────────

    @patch("app.routers.whatsapp.enviar_mensaje")
    @patch("app.routers.whatsapp.construir_grafo")
    @patch("app.routers.whatsapp.obtener_menu_formateado")
    @patch("app.routers.whatsapp.obtener_o_crear_conversacion")
    @patch("app.routers.whatsapp.obtener_o_crear_cliente")
    @patch("app.routers.whatsapp.guardar_mensajes")
    @patch("app.routers.whatsapp.crear_pedido")
    @patch("app.routers.whatsapp.obtener_ultimo_pedido")
    def test_webhook_rechaza_domicilio_fuera_de_zona(
        self, mock_ultimo, mock_crear, mock_guardar, mock_cliente, mock_conv,
        mock_menu, mock_grafo, mock_enviar, client
    ):
        """Un pedido domicilio con dirección fuera de cobertura no persiste y envía rechazo."""
        mock_cliente.return_value = MagicMock(id="c1")
        mock_conv_obj = MagicMock(id="conv-1", mensajes=[])
        mock_conv.return_value = mock_conv_obj
        mock_menu.return_value = "menu"
        mock_ultimo.return_value = None
        mock_grafo.return_value.invoke.return_value = {
            "messages": [AIMessage(content="Pedido listo")],
            "etapa": "finalizado",
            "tipo_pedido": "domicilio",
            "direccion_entrega": "Autopista Norte km 20, Chía",  # fuera de cobertura
            "metodo_pago": "online",
        }

        client.post(
            "/webhooks/whatsapp",
            data={"Body": "confirmo", "From": "whatsapp:+573001234567"},
        )

        # El pedido NO debe haberse creado
        mock_crear.assert_not_called()
        # Debe haberse enviado un mensaje de rechazo
        textos = [call[0][1] for call in mock_enviar.call_args_list]
        assert any("cobertura" in t.lower() or "sector" in t.lower() for t in textos)

    # ── Webhook: comanda cargada desde DB para nueva conversación ─────

    @patch("app.routers.whatsapp.enviar_mensaje")
    @patch("app.routers.whatsapp.construir_grafo")
    @patch("app.routers.whatsapp.obtener_menu_formateado")
    @patch("app.routers.whatsapp.obtener_o_crear_conversacion")
    @patch("app.routers.whatsapp.obtener_o_crear_cliente")
    @patch("app.routers.whatsapp.guardar_mensajes")
    @patch("app.routers.whatsapp.obtener_ultimo_pedido")
    def test_webhook_carga_ultimo_pedido_para_estado(
        self, mock_ultimo, mock_guardar, mock_cliente, mock_conv,
        mock_menu, mock_grafo, mock_enviar, client
    ):
        """En una conversación nueva, el último pedido DB se inyecta en state['comanda']."""
        mock_cliente.return_value = MagicMock(id="c1")
        mock_conv_obj = MagicMock(id="conv-1", mensajes=[])
        mock_conv.return_value = mock_conv_obj
        mock_menu.return_value = "menu"

        pedido_db = MagicMock()
        pedido_db.referencia = "NEX-000005"
        pedido_db.estado = "preparando"
        pedido_db.total = 25000
        pedido_db.tipo = "llevar"
        pedido_db.direccion_entrega = None
        mock_ultimo.return_value = pedido_db

        # Capturar el estado que se pasa al grafo
        captured_state = {}
        def fake_invoke(state, *args, **kwargs):
            captured_state.update(state)
            return {
                "messages": [AIMessage(content="Tu pedido está en preparación")],
                "etapa": "conversando",
            }
        mock_grafo.return_value.invoke.side_effect = fake_invoke

        client.post(
            "/webhooks/whatsapp",
            data={"Body": "¿Cómo va mi pedido?", "From": "whatsapp:+573001234567"},
        )

        # El state pasado al grafo debe tener comanda con datos del DB
        assert captured_state.get("comanda") is not None
        assert captured_state["comanda"]["referencia"] == "NEX-000005"
        assert captured_state["comanda"]["estado"] == "preparando"
