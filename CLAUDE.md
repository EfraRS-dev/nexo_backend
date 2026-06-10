# CLAUDE.md — Nexo Backend

Guía para trabajar en este repositorio. La documentación de producto/arquitectura vive en `docs/` y `README.md`; este archivo resume lo operativo.

## Qué es

Agente conversacional de IA para restaurantes y cocinas ocultas. Convierte un mensaje de WhatsApp del cliente en una comanda de cocina y un link de pago. Es **multi-tenant**: cada mensaje se asocia a un restaurante resolviendo el número de WhatsApp destino (`To` de Twilio) contra la tabla `restaurantes`; los datos quedan aislados por `restaurante_id`. El restaurante `default` se crea al arrancar desde `.env`.

## Stack

- **FastAPI** (`app/main.py`) — API HTTP + webhooks. Lifespan hace seed del admin y chequea Postgres/Redis/Langfuse al arrancar.
- **LangGraph** (`app/agent/`) — orquestación del agente (grafo con routing condicional).
- **LangChain + langchain-openai** — cliente LLM. Soporta **OpenAI** y **OpenRouter** (si `OPENROUTER_API_KEY` está seteada, se usa OpenRouter como base_url). Modelo por defecto: `gpt-4o-mini`.
- **PostgreSQL** (SQLAlchemy 2 + Alembic) — persistencia.
- **Redis** — caché de menú formateado y respuestas FAQ (degradación graciosa: si Redis cae, el sistema sigue sin caché).
- **Twilio** — canal WhatsApp. **Wompi** — pagos (Colombia, COP).
- **Langfuse 4.x** — observabilidad LLM (tracing por sesión/usuario), opcional.

## Comandos

```bash
# Entorno (Windows PowerShell)
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt

# Infraestructura local
docker compose up -d              # PostgreSQL 16 + Redis 8
python -m alembic upgrade head    # Migraciones
python scripts/seed_menu.py       # Cargar menú inicial

# Servidor
uvicorn app.main:app --reload     # http://localhost:8000  ·  /docs

# Tests
pytest                            # Suite completa
pytest tests/test_grafo.py        # Agente LangGraph
pytest tests/test_whatsapp.py     # Webhook + servicios WhatsApp
pytest tests/test_wompi.py        # Webhook Wompi
pytest tests/test_admin.py        # Auth + panel admin
```

Twilio necesita URL pública en desarrollo: `devtunnel host -p 8000 --allow-anonymous` o `ngrok http 8000`, y actualizar `BASE_URL` en `.env`.

## Arquitectura del agente

El grafo se invoca **una vez por mensaje entrante**. El historial completo se rehidrata desde DB y se pasa en `state["messages"]` en cada invocación. **No hay checkpointer de LangGraph: es una decisión de diseño deliberada (no un olvido)** — se evaluó añadirlo y se descartó para no duplicar la persistencia que ya da `conversaciones` (JSONB). El estado del pedido vive en una entrada especial `{"role": "__estado__", ...}` dentro de `conversaciones.mensajes`. Invariante: todo campo de `AgentState` que deba sobrevivir entre turnos hay que persistirlo y releerlo manualmente (ver `docs/codebase-map.md` §4).

**Topología** (`app/agent/graph.py`):

```
START → nodo_clasificar
          ├─ "pedir"         → nodo_conversar → (router_conversar)
          │                       ├─ pedido_listo            → nodo_generar_comanda → nodo_pago → END
          │                       ├─ esperando_confirmacion  → nodo_confirmar → END
          │                       └─ END  (seguir conversando)
          ├─ "faq"           → nodo_faq → END
          ├─ "estado_pedido" → nodo_estado_pedido → END
          └─ "escalamiento"  → nodo_escalamiento → END
```

- `app/agent/state.py` — `AgentState` (TypedDict): items, flags de flujo, intención, tipo_pedido, dirección, método_pago, comanda, IDs de sesión, etapa. (El link de pago NO vive en el estado: se genera en el webhook tras persistir el pedido.)
- `app/agent/nodes.py` — nodos. Tres LLMs lazy: `_llm` (conversación), `_llm_json` (extracción con JSON mode si el modelo lo soporta) y `_llm_classifier`. Incluye reparación de JSON cuando el modelo responde mal. OpenRouter no usa response_format json_object.
- `app/agent/prompts.py` — `SYSTEM_PROMPT` y `FAQ_PROMPT` (ambos con placeholder `{zonas}`), `CLASSIFICATION_PROMPT`, mensajes fijos, `ZONAS_COBERTURA` (default) y el helper `zonas_a_texto`. **Toda la lógica de negocio operativa vive en estos prompts estáticos** (salvo las zonas de cobertura, ya per-tenant vía `config_json`).

## Flujo del webhook WhatsApp (`app/routers/whatsapp.py`)

Es el corazón del sistema. Por mensaje entrante:

1. Valida firma HMAC de Twilio (se omite si `TWILIO_AUTH_TOKEN` vacío → modo desarrollo).
2. Rate limiting: ventana deslizante de 60 s, máx. 20 msg/teléfono.
3. Rechaza multimedia/vacíos; sanitiza y limita la entrada (`max_input_chars`).
4. **Encola por `(telefono, numero_destino)` y retorna 200 a Twilio de inmediato.** Se encola solo `mensaje`; la clave-tupla (`_ConversationKey`) aísla al mismo cliente cuando escribe a dos restaurantes distintos. Un worker `asyncio` por par drena la cola en orden, con su propia sesión DB. Workers se auto-destruyen tras 300 s de inactividad.
5. `_procesar_mensaje`: resuelve restaurante por `numero_destino` (fallback `default`) → cliente → conversación del tenant (timeout 30 min reinicia conversación) → rehidrata historial + estado → resuelve zonas de cobertura del tenant (`obtener_zonas_cobertura`) → carga menú del tenant (cacheado) → construye grafo con nombre del restaurante + zonas → `grafo.invoke` vía `asyncio.to_thread` envuelto en `langfuse_context`.
6. Red de seguridad: revalida zona de cobertura de domicilio antes de finalizar.
7. Persiste mensajes, estado, comanda (`crear_pedido`) y manda respuesta + link de pago (Wompi) o número de pedido (pago en caja).

## Layout

```
app/
├── main.py            # FastAPI app, lifespan (seed admin + health checks), CORS (localhost:3000), /health
├── config.py          # Settings (pydantic-settings, lee .env)
├── database.py        # engine + SessionLocal
├── cache.py           # Helpers Redis (cache_get/set/delete, claves menu/faq, invalidar_menu)
├── observability.py   # make_langfuse_handler + langfuse_context (no-op si Langfuse no configurado)
├── models/            # ORM: restaurante, cliente, contador, menu, pedido, item_pedido, conversacion, pago, operador
├── agent/             # graph.py, nodes.py, state.py, prompts.py
├── routers/           # whatsapp.py, wompi.py, pedidos.py, auth.py, admin.py
├── services/          # client, conversation, menu, order, payment, restaurante, whatsapp
└── utils/             # input_utils, delivery_utils, menu_utils
alembic/versions/      # 6 migraciones (initial, metodo_pago+contadores, operadores, multitenant, drop-legacy-counter, pedido-conversacion_id)
scripts/seed_menu.py   # Poblar menú (--restaurante-id)  ·  scripts/test_agent.py  ·  scripts/cli_demo.py (demo CLI sobre el agente real)
tests/                 # conftest + 5 suites
docs/                  # Análisis de capacidades, deuda técnica multi-tenant, roadmap pro/futuro
```

> Nota: `app/models/operador.py` ya se importa y exporta en `app/models/__init__.py` (junto al resto de modelos), por lo que alembic autogenerate lo detecta.
> Nota multi-tenant: el restaurante se resuelve por número en `whatsapp.py` (`resolver_restaurante_por_numero`) y se propaga como `restaurante_id` a menú, conversación, pedido y caché. La referencia de pedido es `{PREFIJO}-{NNNN}` con contador `pedidos:{restaurante_id}`. `clientes` es global (un teléfono = una persona); `conversaciones`, `operadores`, `menu` y `pedidos` filtran por `restaurante_id`. Las **zonas de cobertura** ya son per-tenant: `obtener_zonas_cobertura(restaurante)` (`app/utils/delivery_utils.py`) las lee de `restaurantes.config_json["zonas_cobertura"]` (fallback a `ZONAS_COBERTURA` en `prompts.py`) y son la fuente única que alimenta `SYSTEM_PROMPT`, `FAQ_PROMPT` y `validar_zona_domicilio`. Los **horarios** siguen estáticos en `prompts.py` (pendiente moverlos a `config_json` igual que las zonas).

## Endpoints

| Método | Ruta | Notas |
| --- | --- | --- |
| POST | `/webhooks/whatsapp` | Twilio, firma HMAC, cola por (teléfono, tenant) |
| POST | `/webhooks/wompi` | Validación checksum SHA256 |
| POST | `/auth/login` | → JWT Bearer (operador) |
| POST | `/auth/refresh` | Renueva el JWT (requiere token vigente) |
| GET | `/admin/me` | Perfil del operador + restaurante (JWT) |
| GET | `/admin/pedidos` | Paginado, filtros estado/método/fecha (JWT) |
| PATCH | `/pedidos/{referencia}/estado` | (JWT) |
| GET/POST/PATCH/DELETE | `/admin/menu` | CRUD menú (JWT) — invalida caché |
| GET | `/health` | — |

## Configuración (`.env`)

Copiar `.env.example`. Mínimo para arrancar el agente: `OPENAI_API_KEY` (o `OPENROUTER_API_KEY`). Otras clave: `DATABASE_URL`, `REDIS_URL`, `TWILIO_*`, `WOMPI_*`, `BASE_URL`, `SECRET_KEY`, `ADMIN_EMAIL`/`ADMIN_PASSWORD` (si `ADMIN_PASSWORD` está seteada, se crea el operador al iniciar), `LANGFUSE_*`, `RESTAURANTE_NOMBRE`, `LOG_LEVEL`.

## Convenciones

- Código y comentarios en **español**; docstrings en español.
- Money en **COP** (enteros, sin decimales).
- Degradación graciosa: Redis/Langfuse caídos no deben tumbar el flujo; loguear y continuar.
- El backend **no usa tools/function-calling reales** del LLM hoy: el modelo decide dentro del prompt y el código ejecuta acciones fijas alrededor (ver [docs/analisis-capacidades-agente-nexo.md](docs/analisis-capacidades-agente-nexo.md)).
- Frontend (panel admin) vive en el repo hermano `nexo_frontend` (Next.js); consume estos endpoints `/admin` y `/auth`.

## Puntos críticos y bugs conocidos

Invariantes que rompen el sistema si se tocan mal: validación de firma Twilio y checksum Wompi; cola por `(teléfono, tenant)` (orden por cliente y aislamiento de tenants); rehidratación manual del estado JSONB (`__estado__`) — **no hay checkpointer, por decisión**; resolución de restaurante por número destino; degradación graciosa de Redis/Langfuse. Detalle completo en `docs/codebase-map.md` §4.

Bugs/deuda — resumen (detalle y archivo:línea en `docs/codebase-map.md` §5):

- **Resueltos (2026-06-09):** B-1 (docstring del grafo fiel), B-2 (`link_pago` eliminado del estado), B-3 (docstrings de `guardar/restaurar_estado_pedido` listan todos los campos), B-4 (`utils/` raíz + `cli_demo.py` + `cli_prompts.py` legacy eliminados; nueva demo CLI en `scripts/cli_demo.py` sobre el agente real), B-5 (`Operador` en `models/__init__`), B-6 (fila `contadores('pedidos',0)` legacy eliminada vía migración `c3d4e5f6a7b8`), B-7 (recibo Wompi muestra nombres del menú, no UUIDs), B-8 (idempotencia de `crear_pedido` por `conversacion_id`: columna + índice único parcial en migración `d4e5f6a7b8c9`; `crear_pedido` devuelve `(pedido, fue_creado)` y el webhook no reenvía cobro en duplicados).
- **Diferidos (abiertos):** R-5 (`/auth/refresh` no revoca el token previo — requiere blacklist/refresh-tokens).

## Estado / roadmap

Fases 1–6 completas (infra, agente, WhatsApp, pagos Wompi, cobertura/escalamiento/rate-limit, panel admin). La **migración multi-tenant ya está hecha** (commit `a7d2a3d`: tabla `restaurantes`, resolución por número, aislamiento por `restaurante_id`). Pendientes priorizados en `docs/`: validación estructurada del output (Pydantic), caché LLM, memoria de cliente, evals/observabilidad ampliada y tools reales. Mapa técnico vivo del repo en [docs/codebase-map.md](docs/codebase-map.md).
