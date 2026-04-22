# Deuda Técnica — Migración Multi-Restaurante

El sistema actualmente soporta un único restaurante configurado via `RESTAURANTE_NOMBRE` en `.env`. Las siguientes tareas convierten Nexo en una plataforma real multi-tenant.

## Tareas

### 1. Propagar `restaurante_id` desde el contexto del request

Resolver el `restaurante_id` en el webhook de WhatsApp a partir del número destino de Twilio (`To` en el payload), en lugar del default `"default"` hardcodeado en `menu_service.py` y `seed_menu.py`.

**Archivos afectados:** `app/routers/whatsapp.py`, `app/services/menu_service.py`

---

### 2. Eliminar defaults de `restaurante_id`

En `app/services/menu_service.py`, quitar `restaurante_id="default"` como valor por defecto y forzarlo a ser explícito en cada llamada. Esto genera errores en tiempo de compilación si algún callsite lo omite.

**Archivos afectados:** `app/services/menu_service.py`, `scripts/seed_menu.py`

---

### 3. Resolver `restaurante_nombre` en runtime desde DB

En lugar de usar `settings.restaurante_nombre` (variable global de `.env`), leer el nombre del restaurante desde la tabla `restaurantes` en base al `restaurante_id` de la sesión activa. Esto permite que cada restaurante tenga su propio nombre en los mensajes del agente.

**Archivos afectados:** `app/agent/prompts.py`, `app/routers/whatsapp.py`

---

### 4. Tabla `restaurantes`

Crear el modelo `Restaurante` con los siguientes campos:

| Campo | Tipo | Descripción |
| --- | --- | --- |
| `id` | `str` (PK) | Slug o UUID del restaurante |
| `nombre` | `str` | Nombre visible para el agente |
| `numero_whatsapp` | `str` | Número Twilio asociado (ej: `+14155238886`) |
| `activo` | `bool` | Si el restaurante está operativo |
| `config_json` | `JSONB` | Configuración extendida (zonas, horarios, etc.) |

Agregar migración Alembic correspondiente.

**Archivos nuevos:** `app/models/restaurante.py`, `alembic/versions/[hash]_add_restaurantes.py`

---

### 5. Aislamiento de datos

Garantizar que **todas** las queries de menú, pedidos y conversaciones estén siempre filtradas por `restaurante_id` para evitar fugas de datos entre tenants.

Queries a revisar:

- `app/services/menu_service.py` → `obtener_menu_formateado()`
- `app/services/order_service.py` → `crear_pedido()`, `obtener_pedido_por_referencia()`
- `app/services/conversation_service.py` → `obtener_o_crear_conversacion()`
- `app/routers/admin.py` → todos los endpoints de `/admin/pedidos` y `/admin/menu`

---

### 6. Seed parametrizable

Actualizar `scripts/seed_menu.py` para aceptar `--restaurante-id` como argumento CLI y poder cargar menús por restaurante sin modificar el código.

```bash
python scripts/seed_menu.py --restaurante-id mi-restaurante
```

**Archivos afectados:** `scripts/seed_menu.py`
