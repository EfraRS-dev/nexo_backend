# Codebase Map — Nexo Backend

> **Actualizado:** 2026-06-09
> **Fuente de verdad:** el código. Este documento se reconstruyó leyendo los archivos, no la doc previa.
> Agente conversacional de IA para restaurantes (FastAPI + LangGraph + PostgreSQL + Redis + Twilio + Wompi), multi-tenant.

---

## 1. Flujo de ejecución por entrada

### 1.1 Webhook WhatsApp — `POST /webhooks/whatsapp` (`app/routers/whatsapp.py`)

Punto de entrada principal. La función `webhook_whatsapp` hace el trabajo *síncrono mínimo* y delega el resto a un worker en background:

1. **Firma Twilio** — `_validar_firma_twilio` (líneas 172-192). Reconstruye la URL con `settings.base_url` (evita mismatch detrás de túneles). Si `TWILIO_AUTH_TOKEN` está vacío → devuelve `True` (modo desarrollo).
2. **Extracción** — `telefono` desde `From`, `numero_destino` desde `To`, `mensaje` desde `Body` (todos sin prefijo `whatsapp:`).
3. **Rate limit** — `_esta_bajo_limite(telefono)` (líneas 86-102). Ventana deslizante de 60 s, máx. `_RATE_LIMIT_PER_MIN = 20` por teléfono. Llama primero a `_purgar_rate_limits(ahora)` (líneas 67-83), que barre el dict a lo sumo una vez por ventana eliminando teléfonos con ventana expirada (ver R-4). Si excede: avisa (`enviar_mensaje` vía `to_thread`) y devuelve 200.
4. **Multimedia / vacío** — si `NumMedia>0` sin texto, o `Body` vacío → responde fallback (`enviar_mensaje` vía `to_thread`) y 200.
5. **Sanitización** — `limitar_entrada_usuario(mensaje, settings.max_input_chars)` (recorta a 360 chars).
6. **Encolar** — `_ensure_worker((telefono, numero_destino))` crea cola+worker si no existen; se hace `put(mensaje)` y se retorna 200 a Twilio de inmediato. La clave es el par `(telefono, numero_destino)` para aislar tenants.

**Worker por (teléfono, tenant)** (`_phone_worker`): drena la cola en orden, una sesión `SessionLocal()` por mensaje (creada y cerrada por el worker). La clave incluye el `numero_destino` (tenant) para que un mismo cliente que escribe a dos restaurantes no comparta worker/cola. Se auto-destruye tras `_WORKER_IDLE_TIMEOUT = 300 s` de inactividad. Errores no controlados → envía mensaje de disculpa al cliente.

**`_procesar_mensaje`** (líneas 266-465) — núcleo de negocio:

1. **Resolver tenant** — `resolver_restaurante_por_numero(db, numero_destino)`; si `None`, fallback a `obtener_restaurante(db, "default")`. De aquí salen `restaurante_id` y `restaurante_nombre`.
2. **Cliente** — `obtener_o_crear_cliente(db, telefono)` (global, no por tenant).
3. **Conversación** — `obtener_o_crear_conversacion(db, cliente.id, restaurante_id)` (1 activa por cliente+tenant).
4. **Timeout** — `hay_timeout_conversacion(conv, 30)`; si expiró → `expirar_conversacion` + nueva conversación.
5. **Rehidratar** — `restaurar_mensajes(conv)` + append del `HumanMessage` nuevo; `restaurar_estado_pedido(conv)` (lee la entrada JSONB `role="__estado__"`). Si `etapa=="finalizado"` quedó pegada → resetea estado a `{}`.
6. **Poblar comanda** — si el estado no trae `comanda`, carga `obtener_ultimo_pedido(db, cliente.id, restaurante_id)` para que `nodo_estado_pedido` pueda responder.
7. **Zonas + menú + grafo** — `obtener_zonas_cobertura(restaurante)` resuelve las zonas del tenant (fuente única) → `obtener_menu_formateado(db, restaurante_id)` (cacheado en Redis) → `construir_grafo(menu_texto, restaurante_nombre, zonas_a_texto(zonas_cobertura))`.
8. **Estado inicial** — dict `AgentState` con `restaurante_id`, `cliente_id`, `conversacion_id`, etc. Nota: `pedido_listo` e `intencion` se fuerzan (`False`/`""`) y `requiere_escalamiento=False`. El link de pago NO vive en el estado (campo `link_pago` eliminado, ex-B-2): se genera en el webhook tras persistir.
9. **Invocar grafo** — `await asyncio.to_thread(grafo.invoke, estado, lf_config)` dentro de `langfuse_context(session_id=conv.id, user_id=telefono, ...)`. Errores → mensaje de disculpa + return.
10. **Red de seguridad de cobertura** (líneas 388-414) — si `etapa=="finalizado"` + `tipo_pedido=="domicilio"` + dirección no cubierta (`validar_zona_domicilio(direccion, zonas_cobertura)`, mismas zonas del tenant que los prompts) → aborta finalización, pide otra dirección/llevar y `return` (no persiste comanda).
11. **Persistir** — `guardar_mensajes` + `guardar_estado_pedido` (envuelto en try/except). Si `etapa=="finalizado"` y hay comanda → `pedido, pedido_creado = crear_pedido(db, comanda, cliente.id, restaurante_id, conversacion_id=conversacion.id)` (`whatsapp.py:425-429`). `crear_pedido` deduplica por `conversacion_id` (1 conversación = 1 pedido, ex-B-8): si ya existía un pedido para esa conversación devuelve `(existente, False)`. Luego `finalizar_conversacion` / `escalar_conversacion` según etapa.
12. **Responder** — envía la respuesta del agente (`enviar_mensaje` vía `to_thread`); fallback si vacía.
13. **Pago** — solo si `etapa=="finalizado" and pedido and pedido_creado` (`whatsapp.py:453`): método `!="caja"` → `generar_link_pago` + envía link Wompi; `"caja"` → envía número de pedido. **Un pedido idempotente (duplicado por reintento) NO reenvía cobro/confirmación** porque `pedido_creado=False`.

### 1.2 Webhook Wompi — `POST /webhooks/wompi` (`app/routers/wompi.py`)

Síncrono, con `Depends(get_db)`:

1. Parsea JSON (400 si falla).
2. **Checksum** — `_validar_checksum_wompi` (líneas 36-67). SHA256 de `concat(valores de signature.properties resueltos por dot-path en data) + timestamp + events_secret`, comparado en mayúsculas. Si `wompi_events_secret` vacío → `True` (desarrollo).
3. Solo procesa `event == "transaction.updated"`; otros → 200 sin acción.
4. Solo `status == "APPROVED"`; otros → 200.
5. `obtener_pedido_por_referencia`. Si no existe → 200 (para que Wompi no reintente). Si ya `pagado` → 200 (idempotencia).
6. `marcar_pedido_pagado` → crea registro `Pago` (monto = centavos // 100).
7. Busca `Cliente` y envía confirmación + recibo (`enviar_recibo`). El recibo resuelve nombres legibles desde `menu` (dict `producto_id→nombre`, líneas 160-188), con fallback al UUID solo si el ítem ya no existe en el menú (ex-B-7).
8. Finaliza la conversación activa del cliente **filtrando por `pedido.restaurante_id`** (líneas 181-189), para no cerrar la conversación de otro tenant (R-2 corregido).

### 1.3 Auth / Admin / Pedidos (JWT)

- `POST /auth/login` (`auth.py`) — verifica `Operador` activo + `verify_password` (PBKDF2-HMAC-SHA256) → JWT `{sub: email, exp}`. `get_current_operador` es la dependencia que valida el Bearer y carga el operador.
- `POST /auth/refresh` (`auth.py:142`) — renueva el JWT a partir de un token vigente. Protegido por `get_current_operador` (no acepta tokens expirados/inválidos: `jwt.ExpiredSignatureError`→401); emite un token nuevo con `create_access_token(operador.email)`. No rota ni revoca el token previo (sigue válido hasta su `exp`).
- `GET /admin/me` — perfil + restaurante del operador.
- `GET /admin/pedidos` — paginado + filtros (estado/metodo_pago/fecha), **filtrado por `operador.restaurante_id`**.
- `GET/POST/PATCH/DELETE /admin/menu` — CRUD menú filtrado por `operador.restaurante_id`; cada mutación llama `invalidar_menu(restaurante_id)`.
- `PATCH /pedidos/{referencia}/estado` (`pedidos.py`) — valida `pedido.restaurante_id == operador.restaurante_id` (404 si no coincide); `estado` validado por enum `EstadoPedido`.

### 1.4 Startup (`app/main.py` lifespan)

`_seed_restaurante_default()` (siempre) → `_seed_admin()` (solo si `ADMIN_PASSWORD` seteada) → health checks `_check_db` / `_check_redis` / `_check_langfuse` (loguean estado; DB caída = error, Redis caída = warning, no detienen el arranque).

---

## 2. Topología del grafo (`app/agent/graph.py` + `nodes.py`)

`construir_grafo(menu_texto, restaurante_nombre, zonas_texto)` compila un `StateGraph(AgentState)`. `nodo_conversar` y `nodo_faq` se envuelven con `partial` para inyectar `menu_texto`, `restaurante_nombre` y `zonas_texto` (zonas del tenant ya formateadas; vacío → los nodos usan el default `_ZONAS_TEXTO`).

```
START → nodo_clasificar ──(_router_clasificar por intencion)──┐
   ├─ "pedir"         → nodo_conversar ──(_router_conversar)──┐
   │                       ├─ pedido_listo=True           → nodo_generar_comanda → nodo_pago → END
   │                       ├─ esperando_confirmacion=True → nodo_confirmar → END
   │                       └─ (else)                      → END
   ├─ "faq"           → nodo_faq → END
   ├─ "estado_pedido" → nodo_estado_pedido → END
   └─ "escalamiento"  → nodo_escalamiento → END
```

**Routers:**
- `_router_clasificar` (graph.py:42) — mapea `intencion` → nodo. Default `nodo_conversar`.
- `_router_conversar` (graph.py:55) — prioridad: `pedido_listo` > `esperando_confirmacion` > `END`.

**Nodos** (`nodes.py`):

| Nodo | Línea | Qué hace |
| --- | --- | --- |
| `nodo_clasificar` | 177 | Clasifica intención del último `HumanMessage` con `_llm_classifier` (temp 0, max 5 tokens). **Atajo:** si ya hay `items` o `etapa ∈ {confirmando, pagando}`, fuerza `intencion="pedir"` sin llamar al LLM. Normaliza a {pedir, faq, estado_pedido, escalamiento}, default `pedir`. |
| `nodo_conversar` | 211 | LLM principal en JSON mode (`_llm_json`, temp 0.1). Inyecta `SYSTEM_PROMPT` (menú, restaurante, zonas). Parsea el JSON (`_parse_llm_json`, con reparación `_repair_json` si no es válido). Emite `respuesta`, `items` (acumulativo), `tipo_pedido`, `direccion_entrega`, `metodo_pago`, `pedido_listo`, `esperando_confirmacion`; recalcula `etapa`. |
| `nodo_confirmar` | 269 | No-op funcional: solo devuelve `{"etapa": "confirmando"}`. El mensaje de resumen ya lo generó `nodo_conversar`. Termina en END. |
| `nodo_faq` | 283 | Cache Redis (`faq_cache_key`) por pregunta+tenant. Si miss, `_llm` con `FAQ_PROMPT` (inyecta menú, restaurante y `{zonas}` del tenant) y cachea (`cache_faq_ttl`). |
| `nodo_estado_pedido` | 329 | Lee `state["comanda"]` (poblada por el webhook desde DB) y formatea el estado; si no hay, `MSG_ESTADO_SIN_PEDIDO`. |
| `nodo_escalamiento` | 366 | Marca `etapa="escalado"`, `requiere_escalamiento=True`, mensaje fijo. |
| `nodo_generar_comanda` | 387 | Construye dict `comanda` en memoria (`referencia="PENDIENTE"`, total, items, tipo, metodo_pago, estado). NO toca DB. `etapa="pagando"`. |
| `nodo_pago` | 429 | Mensaje final según `metodo_pago` (`caja` vs `online`). `etapa="finalizado"`. El link real lo genera el webhook. |

**LLMs lazy** (`nodes.py:44-98`): `_get_llm` (conversación/FAQ, temp 0.3), `_get_llm_json` (extracción, temp 0.1, `response_format=json_object` solo en modelos OpenAI conocidos), `_get_classifier_llm` (temp 0, max 5 tokens). `_base_llm_kwargs` usa OpenRouter si `OPENROUTER_API_KEY` está seteada (base_url `https://openrouter.ai/api/v1`); OpenRouter NO usa JSON mode.

---

## 3. Modelos / tablas y relaciones

| Tabla | Modelo | PK | Claves multi-tenant / notas |
| --- | --- | --- | --- |
| `restaurantes` | `Restaurante` | `id` (slug string) | `numero_whatsapp` UNIQUE+index, `prefijo` (4) UNIQUE, `activo`, `config_json` (JSONB, **reservado/no usado**) |
| `clientes` | `Cliente` | `id` (uuid) | `telefono` UNIQUE. **Global** (sin `restaurante_id`) |
| `conversaciones` | `Conversacion` | `id` (uuid) | `cliente_id` FK, `restaurante_id` (default "default", index), `mensajes` JSONB, `estado` (activa/finalizada/escalada/expirada), `updated_at` |
| `menu` | `Menu` | `id` (uuid) | `restaurante_id` (index), `slug` (index, **no unique**), `precio` (COP int), `disponible` |
| `pedidos` | `Pedido` | `id` (uuid) | `cliente_id` FK, `restaurante_id`, `conversacion_id` FK→conversaciones (nullable, **índice único parcial** `uq_pedidos_conversacion_id` WHERE NOT NULL = idempotencia ex-B-8), `referencia` UNIQUE, `estado`, `tipo`, `metodo_pago` (online/caja), `total` |
| `items_pedido` | `ItemPedido` | `id` (uuid) | `pedido_id` FK, `producto_id` **FK → menu.id (UUID)**, `cantidad`, `modificadores` JSONB, `precio_unitario` |
| `pagos` | `Pago` | `id` (uuid) | `pedido_id` FK, `metodo`, `estado`, `referencia_wompi`, `monto` (COP) |
| `operadores` | `Operador` | `id` (uuid) | `email` UNIQUE+index, `hashed_password`, `restaurante_id` (index) |
| `contadores` | `Contador` | `nombre` (string) | `valor` int. Secuencia de pedidos por tenant: `nombre = "pedidos:{restaurante_id}"` |

**Enums** (`pedido.py`): `EstadoPedido` = pendiente/confirmado/pagado/preparando/en_camino/entregado; `TipoPedido` = llevar/domicilio.

**Referencia de pedido:** `{PREFIJO}-{NNNN}` (`order_service._siguiente_numero_pedido` hace `INSERT ... ON CONFLICT DO NOTHING` + `UPDATE ... RETURNING` atómico por tenant; `_prefijo_restaurante` con fallback "NEXO").

**Migraciones** (`alembic/versions/`, 6 — head único `d4e5f6a7b8c9`):
- `059ec95bac54_initial` — clientes, menu (**ya con `restaurante_id`**), conversaciones, pedidos (**ya con `restaurante_id`**), items_pedido, pagos.
- `a1b2c3d4e5f6` — tabla `contadores` y `pedidos.metodo_pago`. **Ya NO siembra la fila legacy `('pedidos',0)`** (ex-B-6: el INSERT se reemplazó por un comentario; el contador es per-tenant).
- `f1a2b3c4d5e6` — tabla `operadores` (**sin `restaurante_id`**).
- `b2c3d4e5f6a7_add_multitenant` — tabla `restaurantes`, backfill tenant default desde `.env`, y agrega `restaurante_id` a `conversaciones` y `operadores`.
- `c3d4e5f6a7b8_drop_legacy_pedidos_counter` — `DELETE FROM contadores WHERE nombre='pedidos'` (borra la fila legacy de DBs existentes; downgrade la re-inserta). Ex-B-6.
- `d4e5f6a7b8c9_add_pedido_conversacion_id` — añade `pedidos.conversacion_id` + FK `fk_pedidos_conversacion_id` + índice único parcial `uq_pedidos_conversacion_id` (postgresql_where `conversacion_id IS NOT NULL`). Ex-B-8.

> **Nota:** `menu.restaurante_id` y `pedidos.restaurante_id` nacen en la migración inicial, no en la multi-tenant. La fila legacy `contadores('pedidos',0)` ya fue eliminada del seed (a1b2c3d4e5f6) y se purga de DBs existentes (c3d4e5f6a7b8); el runtime usa `pedidos:{restaurante_id}`.

---

## 4. Puntos críticos / invariantes

1. **No hay checkpointer de LangGraph — decisión de diseño deliberada (NO es un olvido).** Se evaluó implementar un checkpointer y se decidió **cancelarlo** (2026-06-09): añade infraestructura de persistencia paralela (otra tabla/store de LangGraph) que duplicaría lo que ya hace `conversaciones.mensajes`. En su lugar, el grafo corre una sola vez por mensaje hasta END y todo el estado se rehidrata **manualmente** desde `conversaciones.mensajes` (JSONB) en cada invocación. El estado del pedido vive en una entrada especial `{"role": "__estado__", "content": {...}}` dentro del mismo array de mensajes (`conversation_service.guardar/restaurar_estado_pedido`). Invariante: cualquier campo nuevo de `AgentState` que deba sobrevivir entre turnos tiene que persistirse explícitamente en esa entrada `__estado__` y volver a leerse en `_procesar_mensaje` (paso 8); no basta con declararlo en el TypedDict.
2. **El grafo se invoca una vez por mensaje.** `nodo_confirmar` y los nodos de respuesta terminan en END; el siguiente turno (sí/no del cliente) es otra invocación del webhook.
3. **Cola por `(teléfono, tenant)` garantiza orden** por cliente dentro de cada restaurante y evita procesamiento concurrente del mismo cliente en el mismo tenant. La clave-tupla (`_ConversationKey`) aísla al cliente que escribe a dos restaurantes. La sesión DB es del worker (una por mensaje).
4. **Degradación graciosa Redis/Langfuse:** caídas loguean y continúan sin caché/tracing.
5. **`restaurante_id` se propaga** a menú, conversación, pedido, contador y caché. `clientes` es la única tabla global.
6. **Idempotencia de pedidos (1 conversación = 1 pedido, ex-B-8):** `crear_pedido` (`order_service.py:82-95`) deduplica **primero por `conversacion_id`** — si ya existe un pedido para esa conversación, devuelve `(existente, False)` sin crear otro. La guarda por referencia ≠ PENDIENTE se conserva como respaldo legacy. La dedupe se refuerza a nivel DB con el índice único parcial `uq_pedidos_conversacion_id` (protege contra carreras entre workers). `crear_pedido` ahora retorna **`tuple[Pedido, bool]`** (`(pedido, fue_creado)`); el webhook usa `fue_creado` para no reenviar cobro en duplicados (paso 13). Además Wompi no reprocesa pedidos ya `pagado`.
7. **El LLM no usa function-calling real:** decide dentro del prompt; el código ejecuta acciones fijas alrededor. La validación del JSON depende de `_parse_llm_json`/`_repair_json` (no Pydantic).
8. **Red de seguridad de cobertura** revalida domicilio en el webhook aunque el prompt ya lo instruya.

---

## 5. Bugs y riesgos detectados (2026-06-06)

> **Revisión 2026-06-09 (segunda pasada):** se resolvieron en código los bugs **B-3, B-4, B-6 y B-8** (antes diferidos). **127 tests pasan; head único de alembic = `d4e5f6a7b8c9`.** Detalle de cada fix en sus entradas abajo. **Único abierto/diferido restante: R-5** (`/auth/refresh` no revoca el token previo). No se detectaron bugs nuevos en esta relectura.
>
> **Revisión 2026-06-09 (primera pasada):** sesión de saneamiento de severidad baja. **Resueltos y verificados contra el código:** B-1 (docstring del grafo ahora fiel: `nodo_confirmar → END`, router a `nodo_generar_comanda`/`nodo_confirmar`), B-2 (campo `link_pago` eliminado de `AgentState` y de todas sus referencias en `whatsapp.py`/`conftest.py`/`scripts/test_agent.py`; `tests/test_grafo.py:417` asierta `"link_pago" not in result`), B-5 (`Operador` ahora importado y exportado en `app/models/__init__.py`), B-7 (`enviar_recibo` en `wompi.py` resuelve nombres legibles del menú vía dict `producto_id→nombre`, fallback al UUID solo si el ítem fue borrado). Decisión registrada: **no se implementa checkpointer de LangGraph** (ver §4.1).
>
> **Revisión 2026-06-07:** relectura completa tras añadir `POST /auth/refresh` y eliminar `.github/prompts/plan-nexoMvpBackend.prompt.md` (plan histórico cumplido). Verificadas las correcciones A-1/A-2/R-1..R-4 contra el código actual (siguen vigentes). **R-5 (nuevo, baja):** `/auth/refresh` no rota ni revoca el token previo; al renovar, ambos tokens quedan válidos hasta su `exp`. Aceptable para el MVP (sin blacklist/refresh-tokens), pero documentado por si se endurece la sesión.

### Severidad ALTA

- **A-1 · ~~Cola/worker keyed solo por teléfono, ignora el tenant.~~ ✅ CORREGIDO (2026-06-06).** `app/routers/whatsapp.py`. La cola y el worker ahora se indexan por la tupla `(telefono, numero_destino)` (`_ConversationKey`); se encola solo `mensaje`. Un mismo cliente que escribe a dos restaurantes distintos obtiene worker/cola separados por tenant, evitando la serialización/mezcla entre tenants.

- **A-2 · ~~FAQ contradice la lógica de cobertura.~~ ✅ CORREGIDO (2026-06-07).** Antes el `FAQ_PROMPT` decía *"toda la ciudad"* mientras `SYSTEM_PROMPT` y `validar_zona_domicilio` restringían a las zonas. **Fuente única:** `obtener_zonas_cobertura(restaurante)` (`app/utils/delivery_utils.py`) resuelve las zonas del tenant desde `restaurantes.config_json["zonas_cobertura"]` (fallback a `ZONAS_COBERTURA`). El webhook (`whatsapp.py`) formatea ese texto una vez (`zonas_a_texto`) y lo inyecta a la vez en `SYSTEM_PROMPT`, `FAQ_PROMPT` (nuevo placeholder `{zonas}`) y en `validar_zona_domicilio`, de modo que los tres no pueden contradecirse. **Per-tenant:** cada restaurante puede definir sus propias zonas vía `config_json`; si no, hereda el default. Para activar zonas custom en un tenant, setear `config_json = {"zonas_cobertura": [...]}` en su fila de `restaurantes`.

### Severidad MEDIA

- **R-1 · ~~`enviar_mensaje` síncrono en el handler del webhook.~~ ✅ CORREGIDO (2026-06-07).** `app/routers/whatsapp.py`. Las tres respuestas tempranas (rate-limit / multimedia / vacío) ahora llaman `enviar_mensaje` vía `await asyncio.to_thread(...)`, igual que `_procesar_mensaje`, para no bloquear el event loop.

- **R-2 · ~~Wompi finaliza conversación sin filtrar por tenant.~~ ✅ CORREGIDO (2026-06-07).** `app/routers/wompi.py`. La query de la conversación activa ahora filtra además por `Conversacion.restaurante_id == pedido.restaurante_id`, evitando cerrar la conversación de otro tenant.

- **R-3 · ~~`_repair_json` fallback incluye `metodo_pago` pero el contrato lo omitía.~~ ✅ CORREGIDO (2026-06-07).** `app/agent/nodes.py`. El prompt de reparación ahora lista `metodo_pago` entre las claves requeridas y pasa el valor actual como contexto, coherente con el dict de fallback.

- **R-4 · ~~`_rate_limits` crece sin purga.~~ ✅ CORREGIDO (2026-06-07).** `app/routers/whatsapp.py`. Nuevo `_purgar_rate_limits(ahora)` barre el dict a lo sumo una vez por ventana (60 s) eliminando los teléfonos cuya ventana expiró por completo; se invoca al inicio de `_esta_bajo_limite`.

### Severidad BAJA

- **B-1 · ~~Docstring del grafo desactualizado.~~ ✅ CORREGIDO (2026-06-09).** `app/agent/graph.py:1-17`. El docstring de la topología ahora refleja el grafo real: `nodo_confirmar → END` (línea 140), router_conversar mapea `pedido_listo → nodo_generar_comanda` y `esperando_confirmacion → nodo_confirmar`. Ya no menciona ningún "loop".

- **B-2 · ~~`link_pago` en `AgentState` nunca se usa.~~ ✅ CORREGIDO (2026-06-09).** El campo muerto se eliminó de `app/agent/state.py` (el comentario en líneas 30-32 ahora documenta que el link NO vive en el estado) y de todas sus referencias: estado inicial en `app/routers/whatsapp.py`, `tests/conftest.py` y `scripts/test_agent.py`. `tests/test_grafo.py:417` asierta `"link_pago" not in result`. El link se genera en el webhook (`whatsapp.py:451`) tras persistir el pedido.

- **B-3 · ~~Docstrings de `guardar/restaurar_estado_pedido` incompletos.~~ ✅ CORREGIDO (2026-06-09).** `app/services/conversation_service.py:75-76,101-102`. Los docstrings ahora listan los 7 campos persistidos en la entrada `__estado__`: items, tipo_pedido, direccion_entrega, etapa, esperando_confirmacion, comanda, metodo_pago.

- **B-4 · ~~Código legacy duplicado en la raíz.~~ ✅ CORREGIDO (2026-06-09).** Se eliminó el clúster legacy de la raíz: `cli_demo.py`, `cli_prompts.py` y el directorio `utils/` completo (`input_utils.py`, `menu_utils.py`, `order_utils.py`). Solo el propio `cli_demo.py` raíz los importaba. Nueva demo creada en `scripts/cli_demo.py` que corre el agente **real** (`app.agent.graph.construir_grafo`), resolviendo menú/nombre/zonas del tenant `default` desde DB igual que el webhook. El paquete `app/utils/` (distinto del `utils/` raíz borrado) sigue intacto y es el que usa el runtime.

- **B-5 · ~~`app/models/operador.py` no se importa en `app/models/__init__.py`.~~ ✅ CORREGIDO (2026-06-09).** `app/models/__init__.py:11,24`. `Operador` ahora se importa y se exporta en `__all__`, de modo que alembic autogenerate (que carga `models/__init__`) lo vea. Las referencias directas desde `main.py`/`auth.py`/`admin.py`/`pedidos.py` siguen funcionando igual.

- **B-6 · ~~Fila `contadores('pedidos',0)` legacy.~~ ✅ CORREGIDO (2026-06-09).** La migración `a1b2c3d4e5f6` ya **no** siembra la fila (el INSERT se reemplazó por un comentario explicando que el contador es per-tenant). Nueva migración `c3d4e5f6a7b8_drop_legacy_pedidos_counter.py:22` ejecuta `DELETE FROM contadores WHERE nombre='pedidos'` para DBs existentes (downgrade la re-inserta). El runtime sigue usando `pedidos:{restaurante_id}`.

- **B-7 · ~~`enviar_recibo` usa `producto_id` (UUID) como `nombre`.~~ ✅ CORREGIDO (2026-06-09).** `app/routers/wompi.py:160-188`. El recibo ahora resuelve el nombre legible consultando la tabla `menu` (dict `producto_id→nombre` vía `db.query(Menu).filter(Menu.id.in_(ids_productos))`), con fallback al UUID solo si el ítem fue borrado del menú (`nombres_por_id.get(i.producto_id, i.producto_id)`).

- **B-8 · ~~Idempotencia de `crear_pedido` solo por referencia ≠ PENDIENTE.~~ ✅ CORREGIDO (2026-06-09).** Idempotencia por `conversacion_id` (1 conversación = 1 pedido):
  - `app/models/pedido.py:37-39` — nueva columna `conversacion_id` (FK→conversaciones.id, nullable, index).
  - Migración `d4e5f6a7b8c9_add_pedido_conversacion_id.py` — columna + FK `fk_pedidos_conversacion_id` + índice único parcial `uq_pedidos_conversacion_id` (postgresql_where `conversacion_id IS NOT NULL`), que bloquea duplicados a nivel DB ante carreras entre workers.
  - `app/services/order_service.py:55-95` — `crear_pedido` acepta `conversacion_id: str | None = None` y devuelve **`tuple[Pedido, bool]`** (`(pedido, fue_creado)`). Chequea dedupe por conversación (líneas 82-95) **antes** que por referencia; si ya existe devuelve `(existente, False)`.
  - `app/routers/whatsapp.py:425-429,453` — pasa `conversacion_id=conversacion.id`, desempaca `pedido, pedido_creado`, inicializa `pedido_creado=False`, y solo envía link/confirmación si `etapa=="finalizado" and pedido and pedido_creado` (no reenvía cobro en duplicados).
  - Tests: 3 call sites actualizados al retorno tupla + nuevo `test_crear_pedido_idempotente_por_conversacion` en `tests/test_wompi.py`.

- **R-5 · `/auth/refresh` no rota ni revoca el token previo.** DIFERIDO (deuda registrada). `app/routers/auth.py`. Al renovar, el token anterior sigue válido hasta su `exp`. Un fix real exige arquitectura de refresh-tokens o blacklist (p.ej. `jti` en Redis), lo que va contra la simplicidad del MVP actual. Aceptado como deuda.

---

## 6. Mapa de archivos comentado

```
app/
├── main.py            # FastAPI + lifespan: seed restaurante default + admin, health checks, CORS localhost:3000, /health
├── config.py          # Settings (pydantic-settings). NO exporta a os.environ (observability lo hace para Langfuse)
├── database.py        # engine (pool_pre_ping) + SessionLocal + Base + get_db()
├── cache.py           # Redis helpers + claves menu/faq + invalidar_menu. Lazy client, flag _unavailable
├── observability.py   # make_langfuse_handler (Langfuse 4.x, lee env) + langfuse_context (propagate_attributes / nullcontext)
├── models/
│   ├── __init__.py    # Agregador (incluye Operador, ex-B-5)
│   ├── restaurante.py # Tenant: id slug, numero_whatsapp unique, prefijo unique, config_json reservado
│   ├── cliente.py     # Global, telefono unique
│   ├── conversacion.py# JSONB mensajes (+ entrada __estado__), restaurante_id, estado
│   ├── menu.py        # restaurante_id + slug (no unique)
│   ├── pedido.py      # Enums EstadoPedido/TipoPedido, referencia unique, metodo_pago, conversacion_id FK (idempotencia, ex-B-8)
│   ├── item_pedido.py # producto_id FK→menu.id (UUID), modificadores JSONB
│   ├── pago.py        # registro Wompi
│   ├── operador.py    # auth admin, restaurante_id (ahora sí en __init__, ex-B-5)
│   └── contador.py    # secuencias (pedidos:{restaurante_id})
├── agent/
│   ├── graph.py       # construir_grafo + routers _router_clasificar/_router_conversar (docstring fiel al grafo, ex-B-1)
│   ├── nodes.py       # 8 nodos + 3 LLMs lazy + parse/repair JSON + soporte OpenRouter
│   ├── state.py       # AgentState TypedDict (sin link_pago, ex-B-2: el link se genera en el webhook)
│   └── prompts.py     # SYSTEM_PROMPT (JSON) y FAQ_PROMPT con placeholder {zonas}, CLASSIFICATION_PROMPT, ZONAS_COBERTURA (default), _ZONAS_TEXTO (default formateado), zonas_a_texto
├── routers/
│   ├── whatsapp.py    # Webhook + rate limit + cola/worker por (teléfono, tenant) + _procesar_mensaje (núcleo)
│   ├── wompi.py       # Webhook pagos: checksum SHA256, idempotencia, recibo
│   ├── pedidos.py     # PATCH estado (aislado por tenant)
│   ├── auth.py        # login + refresh JWT + PBKDF2 + get_current_operador
│   └── admin.py       # /me, /pedidos (paginado), CRUD /menu (invalida caché), todo por tenant
├── services/
│   ├── restaurante_service.py # resolver_restaurante_por_numero (cacheado), obtener_restaurante, RESTAURANTE_DEFAULT
│   ├── client_service.py      # obtener_o_crear_cliente
│   ├── conversation_service.py# CRUD conversación + estado pedido en JSONB + timeout
│   ├── menu_service.py        # obtener_menu(_formateado) cacheado, buscar_producto_por_slug
│   ├── order_service.py       # crear_pedido → tuple[Pedido,bool] (dedupe por conversacion_id, +contador por tenant, ex-B-8), obtener_ultimo/por_referencia, marcar_pagado
│   ├── payment_service.py     # generar_link_pago (checkout Wompi)
│   └── whatsapp_service.py    # enviar_mensaje / enviar_recibo (Twilio, cliente lazy)
└── utils/
    ├── input_utils.py    # limitar_entrada_usuario
    ├── menu_utils.py     # formatear_menu (acepta dict legacy o list ORM)
    └── delivery_utils.py # obtener_zonas_cobertura (per-tenant vía config_json, fuente única) + validar_zona_domicilio (substring)

alembic/versions/  # 6 migraciones, head único d4e5f6a7b8c9 (ver §3)
scripts/seed_menu.py  # --restaurante-id; MENU_SEED (11 ítems hamburguesería)
scripts/test_agent.py # demo/prueba del agente
scripts/cli_demo.py   # Demo CLI del agente REAL (construir_grafo + tenant default desde DB), ex-B-4
tests/             # conftest + test_grafo, test_whatsapp, test_wompi, test_admin, test_multitenant (5 suites)
docs/              # analisis-capacidades-agente-nexo.md, pro-futuro.md, este codebase-map.md
```
