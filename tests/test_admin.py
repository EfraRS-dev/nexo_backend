"""
Tests de Fase 6: Autenticación JWT y endpoints de administración.

Cubre:
- hash_password / verify_password
- create_access_token / get_current_operador
- POST /auth/login
- POST /auth/refresh
- GET  /admin/pedidos
- GET  /admin/menu
- POST /admin/menu
- PATCH /admin/menu/{id}
- DELETE /admin/menu/{id}
"""
import os
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("OPENAI_API_KEY", "test-key-fake")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")


# ═════════════════════════════════════════════════════════════════════════════
# 1. HELPERS DE CONTRASEÑA
# ═════════════════════════════════════════════════════════════════════════════

class TestPasswordHelpers:
    def test_hash_es_distinto_al_original(self):
        from app.routers.auth import hash_password
        hashed = hash_password("mi_contraseña_segura")
        assert hashed != "mi_contraseña_segura"

    def test_verify_contraseña_correcta(self):
        from app.routers.auth import hash_password, verify_password
        hashed = hash_password("correcta")
        assert verify_password("correcta", hashed) is True

    def test_verify_contraseña_incorrecta(self):
        from app.routers.auth import hash_password, verify_password
        hashed = hash_password("correcta")
        assert verify_password("incorrecta", hashed) is False

    def test_verify_hash_malformado(self):
        from app.routers.auth import verify_password
        assert verify_password("cualquiera", "hash-malformado") is False

    def test_dos_hashes_son_distintos_para_misma_contraseña(self):
        from app.routers.auth import hash_password
        h1 = hash_password("igual")
        h2 = hash_password("igual")
        # Salt aleatorio → hashes distintos
        assert h1 != h2


# ═════════════════════════════════════════════════════════════════════════════
# 2. JWT
# ═════════════════════════════════════════════════════════════════════════════

class TestJWT:
    def test_token_contiene_sub(self):
        import jwt
        from app.config import settings
        from app.routers.auth import create_access_token

        token = create_access_token("admin@test.com")
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.jwt_algorithm])
        assert payload["sub"] == "admin@test.com"

    def test_token_invalido_lanza_401(self):
        from fastapi import HTTPException
        from fastapi.security import HTTPAuthorizationCredentials
        from app.routers.auth import get_current_operador

        fake_credentials = HTTPAuthorizationCredentials(
            scheme="Bearer", credentials="token-invalido"
        )
        mock_db = MagicMock()

        with pytest.raises(HTTPException) as exc_info:
            get_current_operador(credentials=fake_credentials, db=mock_db)

        assert exc_info.value.status_code == 401

    def test_token_valido_operador_no_encontrado_lanza_401(self):
        from fastapi import HTTPException
        from fastapi.security import HTTPAuthorizationCredentials
        from app.routers.auth import create_access_token, get_current_operador

        token = create_access_token("noexiste@test.com")
        fake_credentials = HTTPAuthorizationCredentials(
            scheme="Bearer", credentials=token
        )
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            get_current_operador(credentials=fake_credentials, db=mock_db)

        assert exc_info.value.status_code == 401


# ═════════════════════════════════════════════════════════════════════════════
# 3. POST /auth/login
# ═════════════════════════════════════════════════════════════════════════════

class TestAuthLogin:
    def _make_operador(self, email: str, password: str):
        from app.routers.auth import hash_password
        from app.models.operador import Operador

        op = MagicMock(spec=Operador)
        op.email = email
        op.hashed_password = hash_password(password)
        op.activo = True
        return op

    def test_login_exitoso_devuelve_token(self):
        from app.routers.auth import login, LoginRequest

        operador = self._make_operador("admin@nexo.app", "secreto")
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = operador

        response = login(LoginRequest(email="admin@nexo.app", password="secreto"), db=mock_db)

        assert response.access_token
        assert response.token_type == "bearer"

    def test_login_contraseña_incorrecta_lanza_401(self):
        from fastapi import HTTPException
        from app.routers.auth import login, LoginRequest

        operador = self._make_operador("admin@nexo.app", "correcta")
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = operador

        with pytest.raises(HTTPException) as exc_info:
            login(LoginRequest(email="admin@nexo.app", password="incorrecta"), db=mock_db)

        assert exc_info.value.status_code == 401

    def test_login_usuario_no_existe_lanza_401(self):
        from fastapi import HTTPException
        from app.routers.auth import login, LoginRequest

        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            login(LoginRequest(email="nadie@nexo.app", password="cualquiera"), db=mock_db)

        assert exc_info.value.status_code == 401


# ═════════════════════════════════════════════════════════════════════════════
# 3b. POST /auth/refresh
# ═════════════════════════════════════════════════════════════════════════════

class TestAuthRefresh:
    def test_refresh_devuelve_token_nuevo(self):
        import jwt
        from app.config import settings
        from app.routers.auth import refresh
        from app.models.operador import Operador

        operador = MagicMock(spec=Operador)
        operador.email = "admin@nexo.app"

        response = refresh(operador=operador)

        assert response.token_type == "bearer"
        payload = jwt.decode(
            response.access_token,
            settings.secret_key,
            algorithms=[settings.jwt_algorithm],
        )
        assert payload["sub"] == "admin@nexo.app"

    def test_refresh_requiere_token_valido(self):
        # /auth/refresh se protege con la dependencia get_current_operador,
        # que rechaza tokens inválidos con 401 (cubierto en TestJWT).
        from fastapi import HTTPException
        from fastapi.security import HTTPAuthorizationCredentials
        from app.routers.auth import get_current_operador

        fake_credentials = HTTPAuthorizationCredentials(
            scheme="Bearer", credentials="token-invalido"
        )

        with pytest.raises(HTTPException) as exc_info:
            get_current_operador(credentials=fake_credentials, db=MagicMock())

        assert exc_info.value.status_code == 401


# ═════════════════════════════════════════════════════════════════════════════
# 4. GET /admin/pedidos
# ═════════════════════════════════════════════════════════════════════════════

class TestAdminPedidos:
    def _mock_db_con_pedidos(self, pedidos_list=None):
        from datetime import datetime, timezone

        mock_pedido = MagicMock()
        mock_pedido.id = "ped-001"
        mock_pedido.referencia = "NEX-0001"
        mock_pedido.estado = "pendiente"
        mock_pedido.tipo = "domicilio"
        mock_pedido.direccion_entrega = "Calle 84 #45-12"
        mock_pedido.metodo_pago = "caja"
        mock_pedido.total = 25000
        mock_pedido.created_at = datetime(2026, 4, 1, 12, 0, 0, tzinfo=timezone.utc)
        mock_pedido.cliente_id = "cli-001"

        mock_cliente = MagicMock()
        mock_cliente.id = "cli-001"
        mock_cliente.telefono = "+573001234567"
        mock_cliente.nombre = "Juan"

        mock_item = MagicMock()
        mock_item.pedido_id = "ped-001"
        mock_item.producto_id = "prod-001"
        mock_item.cantidad = 2
        mock_item.precio_unitario = 12500
        mock_item.modificadores = {"Hamburguesa": {"sin": ["lechuga"]}}

        mock_producto = MagicMock()
        mock_producto.id = "prod-001"
        mock_producto.nombre = "Hamburguesa"

        mock_db = MagicMock()
        rows = pedidos_list if pedidos_list is not None else [mock_pedido]

        # query(Pedido) chain
        ped_query = MagicMock()
        ped_query.filter.return_value = ped_query
        ped_query.count.return_value = len(rows)
        ped_query.order_by.return_value = ped_query
        ped_query.offset.return_value = ped_query
        ped_query.limit.return_value = ped_query
        ped_query.__iter__ = lambda self: iter(rows)
        ped_query.all.return_value = rows

        # Cargas en lote: query(X).filter(...).all()
        cli_query = MagicMock()
        cli_query.filter.return_value = cli_query
        cli_query.all.return_value = [mock_cliente] if rows else []

        item_query = MagicMock()
        item_query.filter.return_value = item_query
        item_query.all.return_value = [mock_item] if rows else []

        menu_query = MagicMock()
        menu_query.filter.return_value = menu_query
        menu_query.all.return_value = [mock_producto] if rows else []

        mock_db.query.side_effect = lambda model: {
            "Pedido": ped_query,
            "Cliente": cli_query,
            "ItemPedido": item_query,
            "Menu": menu_query,
        }[model.__name__]
        mock_db._ped_query = ped_query
        return mock_db

    def test_lista_pedidos_retorna_paginado(self):
        from app.routers.admin import listar_pedidos
        from app.models.operador import Operador

        mock_db = self._mock_db_con_pedidos()
        mock_operador = MagicMock(spec=Operador)

        result = listar_pedidos(
            estado=None, metodo_pago=None, fecha=None,
            page=1, page_size=20,
            db=mock_db, operador=mock_operador,
        )

        assert result["total"] == 1
        assert result["page"] == 1
        assert len(result["items"]) == 1
        assert result["items"][0]["referencia"] == "NEX-0001"

    def test_fecha_invalida_lanza_400(self):
        from fastapi import HTTPException
        from app.routers.admin import listar_pedidos
        from app.models.operador import Operador

        mock_db = MagicMock()
        mock_operador = MagicMock(spec=Operador)

        with pytest.raises(HTTPException) as exc_info:
            listar_pedidos(
                estado=None, metodo_pago=None, fecha="no-es-fecha",
                page=1, page_size=20,
                db=mock_db, operador=mock_operador,
            )

        assert exc_info.value.status_code == 400

    def test_lista_pedidos_vacia(self):
        from app.routers.admin import listar_pedidos
        from app.models.operador import Operador

        mock_db = self._mock_db_con_pedidos(pedidos_list=[])
        mock_operador = MagicMock(spec=Operador)

        result = listar_pedidos(
            estado=None, metodo_pago=None, fecha=None,
            page=1, page_size=20,
            db=mock_db, operador=mock_operador,
        )

        assert result["total"] == 0
        assert result["items"] == []

    def test_pedido_incluye_items_y_direccion(self):
        from app.routers.admin import listar_pedidos
        from app.models.operador import Operador

        mock_db = self._mock_db_con_pedidos()
        mock_operador = MagicMock(spec=Operador)

        result = listar_pedidos(
            estado=None, metodo_pago=None, fecha=None,
            page=1, page_size=20,
            db=mock_db, operador=mock_operador,
        )

        pedido = result["items"][0]
        assert pedido["direccion_entrega"] == "Calle 84 #45-12"
        assert len(pedido["items"]) == 1
        item = pedido["items"][0]
        assert item["nombre"] == "Hamburguesa"
        assert item["cantidad"] == 2
        assert item["precio_unitario"] == 12500
        assert item["modificadores"] == {"Hamburguesa": {"sin": ["lechuga"]}}

    def test_fecha_con_tz_offset_filtra_por_rango_local(self):
        from datetime import datetime, timezone
        from app.routers.admin import listar_pedidos
        from app.models.operador import Operador

        mock_db = self._mock_db_con_pedidos()
        mock_operador = MagicMock(spec=Operador)

        listar_pedidos(
            estado=None, metodo_pago=None, fecha="2026-04-01", tz_offset=300,
            page=1, page_size=20,
            db=mock_db, operador=mock_operador,
        )

        # Segunda llamada a filter = rango de fecha (la primera es restaurante_id).
        # El día local 2026-04-01 en Colombia (UTC-5) cubre [05:00 UTC, 05:00 UTC+1d)
        fecha_filter = mock_db._ped_query.filter.call_args_list[1].args
        assert fecha_filter[0].right.value == datetime(2026, 4, 1, 5, 0, tzinfo=timezone.utc)
        assert fecha_filter[1].right.value == datetime(2026, 4, 2, 5, 0, tzinfo=timezone.utc)


# ═════════════════════════════════════════════════════════════════════════════
# 5. CRUD /admin/menu
# ═════════════════════════════════════════════════════════════════════════════

class TestAdminMenu:
    def _make_item(self, item_id="item-001", slug="hamburguesa", nombre="Hamburguesa"):
        from app.models.menu import Menu
        item = MagicMock(spec=Menu)
        item.id = item_id
        item.slug = slug
        item.nombre = nombre
        item.precio = 15000
        item.categoria = "hamburguesas"
        item.disponible = True
        item.restaurante_id = "default"
        return item

    def test_listar_menu(self):
        from app.routers.admin import listar_menu
        from app.models.operador import Operador

        item = self._make_item()
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [item]

        result = listar_menu(db=mock_db, operador=MagicMock(spec=Operador, restaurante_id="default"))

        assert len(result) == 1

    def test_crear_item_exitoso(self):
        from app.routers.admin import crear_item_menu, CreateMenuItemRequest
        from app.models.operador import Operador

        mock_db = MagicMock()
        # No existe conflicto de slug
        mock_db.query.return_value.filter.return_value.first.return_value = None
        mock_db.refresh.side_effect = lambda obj: None

        result = crear_item_menu(
            body=CreateMenuItemRequest(slug="nueva-burger", nombre="Nueva Burger", precio=18000),
            db=mock_db,
            operador=MagicMock(spec=Operador, restaurante_id="default"),
        )

        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()

    def test_crear_item_slug_duplicado_lanza_409(self):
        from fastapi import HTTPException
        from app.routers.admin import crear_item_menu, CreateMenuItemRequest
        from app.models.operador import Operador

        existing = self._make_item()
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = existing

        with pytest.raises(HTTPException) as exc_info:
            crear_item_menu(
                body=CreateMenuItemRequest(slug="hamburguesa", nombre="Copy", precio=10000),
                db=mock_db,
                operador=MagicMock(spec=Operador, restaurante_id="default"),
            )

        assert exc_info.value.status_code == 409

    def test_actualizar_item_exitoso(self):
        from app.routers.admin import actualizar_item_menu, UpdateMenuItemRequest
        from app.models.operador import Operador

        item = self._make_item()
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = item

        result = actualizar_item_menu(
            item_id="item-001",
            body=UpdateMenuItemRequest(disponible=False),
            db=mock_db,
            operador=MagicMock(spec=Operador, restaurante_id="default"),
        )

        assert item.disponible is False
        mock_db.commit.assert_called_once()

    def test_actualizar_categoria_null_la_limpia(self):
        from app.routers.admin import actualizar_item_menu, UpdateMenuItemRequest
        from app.models.operador import Operador

        item = self._make_item()
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = item

        actualizar_item_menu(
            item_id="item-001",
            body=UpdateMenuItemRequest(categoria=None),
            db=mock_db,
            operador=MagicMock(spec=Operador, restaurante_id="default"),
        )

        assert item.categoria is None
        mock_db.commit.assert_called_once()

    def test_actualizar_sin_categoria_no_la_toca(self):
        from app.routers.admin import actualizar_item_menu, UpdateMenuItemRequest
        from app.models.operador import Operador

        item = self._make_item()
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = item

        actualizar_item_menu(
            item_id="item-001",
            body=UpdateMenuItemRequest(precio=20000),
            db=mock_db,
            operador=MagicMock(spec=Operador, restaurante_id="default"),
        )

        assert item.categoria == "hamburguesas"
        assert item.precio == 20000

    def test_actualizar_item_no_encontrado_lanza_404(self):
        from fastapi import HTTPException
        from app.routers.admin import actualizar_item_menu, UpdateMenuItemRequest
        from app.models.operador import Operador

        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            actualizar_item_menu(
                item_id="no-existe",
                body=UpdateMenuItemRequest(nombre="X"),
                db=mock_db,
                operador=MagicMock(spec=Operador, restaurante_id="default"),
            )

        assert exc_info.value.status_code == 404

    def test_eliminar_item_exitoso(self):
        from app.routers.admin import eliminar_item_menu
        from app.models.operador import Operador

        item = self._make_item()
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = item

        eliminar_item_menu(item_id="item-001", db=mock_db, operador=MagicMock(spec=Operador, restaurante_id="default"))

        mock_db.delete.assert_called_once_with(item)
        mock_db.commit.assert_called_once()

    def test_eliminar_item_no_encontrado_lanza_404(self):
        from fastapi import HTTPException
        from app.routers.admin import eliminar_item_menu
        from app.models.operador import Operador

        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            eliminar_item_menu(item_id="no-existe", db=mock_db, operador=MagicMock(spec=Operador, restaurante_id="default"))

        assert exc_info.value.status_code == 404


# ═════════════════════════════════════════════════════════════════════════════
# 6. PERFIL DEL OPERADOR (/admin/me)
# ═════════════════════════════════════════════════════════════════════════════

class TestPerfilOperador:
    @patch("app.routers.admin.obtener_restaurante")
    def test_me_devuelve_nombre_del_restaurante(self, mock_obtener):
        from app.routers.admin import obtener_perfil
        from app.models.operador import Operador

        mock_obtener.return_value = MagicMock(nombre="Kike's Plaza")
        operador = MagicMock(spec=Operador, email="admin@nexo.app", restaurante_id="kikes")

        result = obtener_perfil(db=MagicMock(), operador=operador)

        assert result.restaurante_id == "kikes"
        assert result.restaurante_nombre == "Kike's Plaza"
        assert result.email == "admin@nexo.app"

    @patch("app.routers.admin.obtener_restaurante", return_value=None)
    def test_me_sin_restaurante_usa_id_como_fallback(self, mock_obtener):
        from app.routers.admin import obtener_perfil
        from app.models.operador import Operador

        operador = MagicMock(spec=Operador, email="x@y.z", restaurante_id="huerfano")

        result = obtener_perfil(db=MagicMock(), operador=operador)

        assert result.restaurante_nombre == "huerfano"


# ═════════════════════════════════════════════════════════════════════════════
# 7. INFORMACIÓN DEL RESTAURANTE (/admin/restaurante)
# ═════════════════════════════════════════════════════════════════════════════

class TestAdminRestaurante:
    def _make_rest(self, config=None):
        from types import SimpleNamespace
        return SimpleNamespace(
            id="default",
            nombre="Nexo",
            numero_whatsapp="+573000000000",
            prefijo="NEXO",
            activo=True,
            config_json=config,
        )

    @patch("app.routers.admin.obtener_restaurante")
    def test_get_aplana_config_json(self, mock_obtener):
        from app.routers.admin import obtener_restaurante_info
        from app.models.operador import Operador

        mock_obtener.return_value = self._make_rest({
            "horario": "L-D 10am-9pm",
            "zonas_cobertura": ["el prado", "boston"],
            "telefono_contacto": "+57 300 111 2233",
        })
        operador = MagicMock(spec=Operador, restaurante_id="default")

        result = obtener_restaurante_info(db=MagicMock(), operador=operador)

        assert result.nombre == "Nexo"
        assert result.numero_whatsapp == "+573000000000"
        assert result.horario == "L-D 10am-9pm"
        assert result.zonas_cobertura == ["el prado", "boston"]
        assert result.telefono_contacto == "+57 300 111 2233"
        assert result.descripcion is None  # campo no definido → None

    @patch("app.routers.admin.obtener_restaurante")
    def test_get_config_json_nulo_devuelve_campos_none(self, mock_obtener):
        from app.routers.admin import obtener_restaurante_info
        from app.models.operador import Operador

        mock_obtener.return_value = self._make_rest(None)

        result = obtener_restaurante_info(
            db=MagicMock(), operador=MagicMock(spec=Operador, restaurante_id="default")
        )

        assert result.horario is None
        assert result.zonas_cobertura is None

    @patch("app.routers.admin.obtener_restaurante", return_value=None)
    def test_get_404_si_no_existe(self, mock_obtener):
        from fastapi import HTTPException
        from app.routers.admin import obtener_restaurante_info
        from app.models.operador import Operador

        with pytest.raises(HTTPException) as exc:
            obtener_restaurante_info(
                db=MagicMock(), operador=MagicMock(spec=Operador, restaurante_id="default")
            )
        assert exc.value.status_code == 404

    @patch("app.routers.admin.invalidar_menu")
    @patch("app.routers.admin.obtener_restaurante")
    def test_patch_mergea_sin_pisar_otras_claves(self, mock_obtener, mock_inval):
        from app.routers.admin import actualizar_restaurante, UpdateRestauranteRequest
        from app.models.operador import Operador

        rest = self._make_rest({"zonas_cobertura": ["el prado"]})
        mock_obtener.return_value = rest
        mock_db = MagicMock()

        result = actualizar_restaurante(
            body=UpdateRestauranteRequest(horario="L-V 9-6"),
            db=mock_db,
            operador=MagicMock(spec=Operador, restaurante_id="default"),
        )

        assert rest.config_json["horario"] == "L-V 9-6"
        assert rest.config_json["zonas_cobertura"] == ["el prado"]  # intacto
        assert result.horario == "L-V 9-6"
        mock_db.commit.assert_called_once()
        # Aislamiento: lee/escribe solo el tenant del operador, y limpia su caché
        assert mock_obtener.call_args.args[1] == "default"
        mock_inval.assert_called_once_with("default")

    @patch("app.routers.admin.invalidar_menu")
    @patch("app.routers.admin.obtener_restaurante")
    def test_patch_campo_null_lo_limpia(self, mock_obtener, mock_inval):
        from app.routers.admin import actualizar_restaurante, UpdateRestauranteRequest
        from app.models.operador import Operador

        rest = self._make_rest({"horario": "viejo", "zonas_cobertura": ["el prado"]})
        mock_obtener.return_value = rest

        actualizar_restaurante(
            body=UpdateRestauranteRequest(horario=None),
            db=MagicMock(),
            operador=MagicMock(spec=Operador, restaurante_id="default"),
        )

        assert rest.config_json["horario"] is None  # explícito null → limpiado
        assert rest.config_json["zonas_cobertura"] == ["el prado"]

    @patch("app.routers.admin.invalidar_menu")
    @patch("app.routers.admin.obtener_restaurante")
    def test_patch_nombre_actualiza_columna_y_recorta(self, mock_obtener, mock_inval):
        from app.routers.admin import actualizar_restaurante, UpdateRestauranteRequest
        from app.models.operador import Operador

        rest = self._make_rest()
        mock_obtener.return_value = rest

        result = actualizar_restaurante(
            body=UpdateRestauranteRequest(nombre="  Kike's Plaza  "),
            db=MagicMock(),
            operador=MagicMock(spec=Operador, restaurante_id="default"),
        )

        assert rest.nombre == "Kike's Plaza"
        assert result.nombre == "Kike's Plaza"

    @patch("app.routers.admin.invalidar_menu")
    @patch("app.routers.admin.obtener_restaurante")
    def test_patch_nombre_vacio_lanza_400(self, mock_obtener, mock_inval):
        from fastapi import HTTPException
        from app.routers.admin import actualizar_restaurante, UpdateRestauranteRequest
        from app.models.operador import Operador

        mock_obtener.return_value = self._make_rest()

        with pytest.raises(HTTPException) as exc:
            actualizar_restaurante(
                body=UpdateRestauranteRequest(nombre="   "),
                db=MagicMock(),
                operador=MagicMock(spec=Operador, restaurante_id="default"),
            )
        assert exc.value.status_code == 400

    @patch("app.routers.admin.obtener_restaurante", return_value=None)
    def test_patch_404_si_no_existe(self, mock_obtener):
        from fastapi import HTTPException
        from app.routers.admin import actualizar_restaurante, UpdateRestauranteRequest
        from app.models.operador import Operador

        with pytest.raises(HTTPException) as exc:
            actualizar_restaurante(
                body=UpdateRestauranteRequest(horario="x"),
                db=MagicMock(),
                operador=MagicMock(spec=Operador, restaurante_id="default"),
            )
        assert exc.value.status_code == 404
