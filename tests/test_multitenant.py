"""
Tests de la migración multi-tenant.

Cubre:
- Resolución de restaurante por número de WhatsApp (restaurante_service)
- Referencia de pedido por restaurante ({PREFIJO}-{NNNN})
- Contador de pedidos namespaced por restaurante
- Claves de caché namespaced por restaurante
"""
import os
from unittest.mock import MagicMock, patch

os.environ.setdefault("OPENAI_API_KEY", "test-key-fake")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")


# ═════════════════════════════════════════════════════════════════════════════
# 1. RESOLUCIÓN DE RESTAURANTE POR NÚMERO
# ═════════════════════════════════════════════════════════════════════════════

class TestResolverRestaurante:
    @patch("app.services.restaurante_service.cache_set")
    @patch("app.services.restaurante_service.cache_get", return_value=None)
    def test_resolver_por_numero_encontrado(self, mock_get, mock_set):
        from app.services.restaurante_service import resolver_restaurante_por_numero

        rest = MagicMock(id="kikes")
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = rest

        result = resolver_restaurante_por_numero(mock_db, "whatsapp:+14155238886")

        assert result is rest
        mock_set.assert_called_once()  # cachea el mapeo número → id

    @patch("app.services.restaurante_service.cache_set")
    @patch("app.services.restaurante_service.cache_get", return_value=None)
    def test_resolver_por_numero_no_encontrado(self, mock_get, mock_set):
        from app.services.restaurante_service import resolver_restaurante_por_numero

        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = None

        result = resolver_restaurante_por_numero(mock_db, "+99999999999")

        assert result is None
        mock_set.assert_not_called()

    def test_resolver_numero_vacio_retorna_none(self):
        from app.services.restaurante_service import resolver_restaurante_por_numero

        mock_db = MagicMock()
        assert resolver_restaurante_por_numero(mock_db, "") is None
        assert resolver_restaurante_por_numero(mock_db, "whatsapp:") is None


# ═════════════════════════════════════════════════════════════════════════════
# 2. REFERENCIA Y CONTADOR POR RESTAURANTE
# ═════════════════════════════════════════════════════════════════════════════

_COMANDA = {
    "referencia": "PENDIENTE",
    "items": [
        {"id": "x", "nombre": "X", "cantidad": 1, "precio_unitario": 1000, "modificadores": None},
    ],
    "total": 1000,
    "tipo_pedido": "llevar",
    "direccion_entrega": None,
    "metodo_pago": "online",
    "estado": "pendiente",
}


class TestReferenciaPorRestaurante:
    @patch("app.services.order_service.buscar_producto_por_slug", return_value=None)
    def test_referencia_usa_prefijo_y_secuencia(self, mock_buscar):
        from app.services.order_service import crear_pedido

        pedido_q = MagicMock()
        pedido_q.filter.return_value.first.return_value = None  # no idempotencia
        rest_q = MagicMock()
        rest_q.filter.return_value.first.return_value = MagicMock(prefijo="KIKE")
        menu_q = MagicMock()
        menu_q.filter.return_value.first.return_value = None

        mock_db = MagicMock()

        def _query(model):
            nombre = model.__name__
            if nombre == "Pedido":
                return pedido_q
            if nombre == "Restaurante":
                return rest_q
            return menu_q

        mock_db.query.side_effect = _query
        mock_db.execute.return_value.scalar.return_value = 7  # secuencia

        pedido = crear_pedido(mock_db, _COMANDA, "cli-1", "kikes")

        assert pedido.referencia == "KIKE-0007"
        assert pedido.restaurante_id == "kikes"

    def test_siguiente_numero_usa_contador_namespaced(self):
        from app.services.order_service import _siguiente_numero_pedido

        mock_db = MagicMock()
        mock_db.execute.return_value.scalar.return_value = 3

        numero = _siguiente_numero_pedido(mock_db, "kikes")

        assert numero == 3
        # Ambas sentencias deben usar el contador 'pedidos:kikes'
        params = [c.args[1] for c in mock_db.execute.call_args_list if len(c.args) > 1]
        assert {"nombre": "pedidos:kikes"} in params


# ═════════════════════════════════════════════════════════════════════════════
# 3. CLAVES DE CACHÉ POR RESTAURANTE
# ═════════════════════════════════════════════════════════════════════════════

class TestCacheNamespaced:
    def test_menu_cache_key_incluye_restaurante(self):
        from app.cache import menu_cache_key
        assert menu_cache_key("kikes") == "nexo:menu:kikes"
        assert menu_cache_key("default") == "nexo:menu:default"

    def test_faq_cache_key_incluye_restaurante(self):
        from app.cache import faq_cache_key
        key_a = faq_cache_key("¿horario?", "kikes")
        key_b = faq_cache_key("¿horario?", "otro")
        assert key_a.startswith("nexo:faq:kikes:")
        assert key_b.startswith("nexo:faq:otro:")
        assert key_a != key_b  # mismo texto, distinto tenant → distinta clave
