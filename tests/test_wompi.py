"""
Tests de Fase 4: Pagos Wompi y comanda persistida.

Cubre:
- payment_service.generar_link_pago()
- order_service.crear_pedido(), obtener_pedido_por_referencia(), marcar_pedido_pagado()
- Webhook POST /webhooks/wompi (checksum, flujo APPROVED, idempotencia)
"""
import hashlib
import os
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("OPENAI_API_KEY", "test-key-fake")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")


# ═════════════════════════════════════════════════════════════════════════════
# 1. TESTS DEL SERVICIO DE PAGOS
# ═════════════════════════════════════════════════════════════════════════════

class TestPaymentService:
    def test_genera_url_con_public_key(self):
        with patch("app.services.payment_service.settings") as mock_settings:
            mock_settings.wompi_public_key = "pub_stagtest_abc123"
            from app.services.payment_service import generar_link_pago
            url = generar_link_pago("NEX-AB12", 25000)

        assert "pub_stagtest_abc123" in url
        assert "amount-in-cents=2500000" in url
        assert "reference=NEX-AB12" in url
        assert "currency=COP" in url
        assert url.startswith("https://checkout.wompi.co/p/")

    def test_fallback_si_public_key_vacia(self):
        with patch("app.services.payment_service.settings") as mock_settings:
            mock_settings.wompi_public_key = ""
            from app.services.payment_service import generar_link_pago
            url = generar_link_pago("NEX-0001", 10000)

        assert "pub_stagtest_demo" in url
        assert "amount-in-cents=1000000" in url

    def test_convierte_cop_a_centavos(self):
        with patch("app.services.payment_service.settings") as mock_settings:
            mock_settings.wompi_public_key = "pub_key"
            from app.services.payment_service import generar_link_pago
            url = generar_link_pago("NEX-ZZZZ", 35000)

        assert "amount-in-cents=3500000" in url


# ═════════════════════════════════════════════════════════════════════════════
# 2. TESTS DEL SERVICIO DE PEDIDOS
# ═════════════════════════════════════════════════════════════════════════════

COMANDA_TEST = {
    "referencia": "NEX-TEST",
    "items": [
        {"id": "combo-1", "nombre": "Combo 1", "cantidad": 2, "precio_unitario": 25000, "modificadores": None},
        {"id": "gaseosa", "nombre": "Gaseosa Personal", "cantidad": 1, "precio_unitario": 5000, "modificadores": None},
    ],
    "total": 55000,
    "tipo_pedido": "llevar",
    "direccion_entrega": None,
    "estado": "pendiente",
}


class TestOrderService:
    @patch("app.services.order_service.buscar_producto_por_slug", return_value=None)
    def test_crear_pedido_crea_registro(self, mock_buscar):
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = None  # no existe
        mock_db.execute.return_value.scalar.return_value = 1  # contador secuencial

        from app.services.order_service import crear_pedido
        pedido = crear_pedido(mock_db, COMANDA_TEST, "cliente-uuid-1")

        mock_db.add.assert_called()
        mock_db.flush.assert_called_once()
        mock_db.commit.assert_called_once()

    @patch("app.services.order_service.buscar_producto_por_slug", return_value=None)
    def test_crear_pedido_idempotente(self, mock_buscar):
        """Si ya existe el pedido, lo retorna sin crear duplicado."""
        existing = MagicMock()
        existing.referencia = "NEX-TEST"
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = existing

        from app.services.order_service import crear_pedido
        pedido = crear_pedido(mock_db, COMANDA_TEST, "cliente-uuid-1")

        mock_db.add.assert_not_called()
        assert pedido is existing

    def test_obtener_pedido_por_referencia(self):
        mock_pedido = MagicMock()
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = mock_pedido

        from app.services.order_service import obtener_pedido_por_referencia
        result = obtener_pedido_por_referencia(mock_db, "NEX-TEST")

        assert result is mock_pedido

    def test_obtener_pedido_no_encontrado(self):
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = None

        from app.services.order_service import obtener_pedido_por_referencia
        result = obtener_pedido_por_referencia(mock_db, "NEX-9999")

        assert result is None

    def test_marcar_pedido_pagado(self):
        mock_pedido = MagicMock()
        mock_db = MagicMock()

        from app.services.order_service import marcar_pedido_pagado
        marcar_pedido_pagado(mock_db, mock_pedido)

        assert mock_pedido.estado == "pagado"
        mock_db.commit.assert_called_once()


# ═════════════════════════════════════════════════════════════════════════════
# 3. TESTS DEL WEBHOOK WOMPI
# ═════════════════════════════════════════════════════════════════════════════

def _construir_evento_wompi(
    referencia: str = "NEX-ABCD",
    status: str = "APPROVED",
    monto: int = 5500000,
    events_secret: str = "test_secret",
) -> dict:
    """Construye un evento Wompi con checksum válido."""
    transaction_id = "test-tx-123"
    timestamp = 1530291411

    # Propiedades según spec Wompi
    props = ["transaction.id", "transaction.status", "transaction.amount_in_cents"]
    valores = [transaction_id, status, str(monto)]
    cadena = "".join(valores) + str(timestamp) + events_secret
    checksum = hashlib.sha256(cadena.encode()).hexdigest().upper()

    return {
        "event": "transaction.updated",
        "data": {
            "transaction": {
                "id": transaction_id,
                "reference": referencia,
                "status": status,
                "amount_in_cents": monto,
                "payment_method_type": "NEQUI",
                "currency": "COP",
            }
        },
        "environment": "test",
        "signature": {
            "properties": props,
            "checksum": checksum,
        },
        "timestamp": timestamp,
        "sent_at": "2018-07-20T16:45:05.000Z",
    }


class TestWebhookWompi:
    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient
        from app.main import app
        return TestClient(app)

    @pytest.fixture(autouse=True)
    def skip_wompi_checksum(self):
        """Omite validación de checksum Wompi en todos los tests."""
        with patch("app.routers.wompi._validar_checksum_wompi", return_value=True):
            yield

    def test_body_invalido_retorna_400(self, client):
        response = client.post(
            "/webhooks/wompi",
            content=b"not-json",
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 400

    def test_evento_ignorado_retorna_200(self, client):
        body = {"event": "nequi_token.updated", "data": {}, "signature": {}, "timestamp": 0}
        response = client.post("/webhooks/wompi", json=body)
        assert response.status_code == 200

    @patch("app.routers.wompi.obtener_pedido_por_referencia")
    def test_pedido_no_encontrado_retorna_200(self, mock_buscar, client):
        mock_buscar.return_value = None
        body = _construir_evento_wompi()
        response = client.post("/webhooks/wompi", json=body)
        assert response.status_code == 200

    @patch("app.routers.wompi.obtener_pedido_por_referencia")
    def test_pedido_ya_pagado_no_reprocesa(self, mock_buscar, client):
        mock_pedido = MagicMock()
        mock_pedido.estado = "pagado"
        mock_buscar.return_value = mock_pedido

        body = _construir_evento_wompi()
        response = client.post("/webhooks/wompi", json=body)

        assert response.status_code == 200

    @patch("app.routers.wompi.enviar_recibo")
    @patch("app.routers.wompi.enviar_mensaje")
    @patch("app.routers.wompi.finalizar_conversacion")
    @patch("app.routers.wompi.obtener_pedido_por_referencia")
    def test_approved_actualiza_pedido_y_envia_confirmacion(
        self, mock_buscar, mock_finalizar, mock_enviar, mock_recibo, client
    ):
        from app.models.item_pedido import ItemPedido as IP

        mock_pedido = MagicMock()
        mock_pedido.id = "pedido-uuid"
        mock_pedido.referencia = "NEX-ABCD"
        mock_pedido.estado = "pendiente"
        mock_pedido.cliente_id = "cliente-uuid"
        mock_pedido.total = 55000
        mock_pedido.tipo = "llevar"
        mock_pedido.direccion_entrega = None
        mock_buscar.return_value = mock_pedido

        mock_cliente = MagicMock()
        mock_cliente.id = "cliente-uuid"
        mock_cliente.telefono = "+573001234567"

        # Parchamos las queries de DB
        with patch("app.routers.wompi.marcar_pedido_pagado") as mock_pagar, \
             patch("app.routers.wompi.Pago") as mock_pago_cls:

            # Setup DB session mock via dependency override
            mock_db = MagicMock()
            mock_db.query.return_value.filter.return_value.first.return_value = mock_cliente
            mock_db.query.return_value.filter.return_value.all.return_value = []

            from app.main import app
            from app.database import get_db
            app.dependency_overrides[get_db] = lambda: mock_db

            body = _construir_evento_wompi(referencia="NEX-ABCD")
            response = client.post("/webhooks/wompi", json=body)

            app.dependency_overrides.clear()

        assert response.status_code == 200
        mock_pagar.assert_called_once()
        mock_enviar.assert_called_once()
        telefono_llamado = mock_enviar.call_args[0][0]
        assert telefono_llamado == "+573001234567"

    def test_checksum_invalido_retorna_401(self, client):
        # Sobreescribimos el autouse fixture para permitir validación real
        with patch("app.routers.wompi._validar_checksum_wompi", return_value=False):
            body = _construir_evento_wompi()
            response = client.post("/webhooks/wompi", json=body)
        assert response.status_code == 401


# ═════════════════════════════════════════════════════════════════════════════
# 4. TESTS DE VALIDACIÓN DE CHECKSUM
# ═════════════════════════════════════════════════════════════════════════════

class TestValidarChecksumWompi:
    def test_checksum_valido(self):
        with patch("app.routers.wompi.settings") as mock_settings:
            mock_settings.wompi_events_secret = "stagtest_secret"
            from app.routers.wompi import _validar_checksum_wompi

            evento = _construir_evento_wompi(events_secret="stagtest_secret")
            checksum = evento["signature"]["checksum"]
            result = _validar_checksum_wompi(evento, checksum)

        assert result is True

    def test_checksum_invalido(self):
        with patch("app.routers.wompi.settings") as mock_settings:
            mock_settings.wompi_events_secret = "stagtest_secret"
            from app.routers.wompi import _validar_checksum_wompi

            evento = _construir_evento_wompi(events_secret="stagtest_secret")
            result = _validar_checksum_wompi(evento, "CHECKSUM_INCORRECTO")

        assert result is False

    def test_secret_vacio_omite_validacion(self):
        with patch("app.routers.wompi.settings") as mock_settings:
            mock_settings.wompi_events_secret = ""
            from app.routers.wompi import _validar_checksum_wompi

            result = _validar_checksum_wompi({}, "cualquier-cosa")

        assert result is True
