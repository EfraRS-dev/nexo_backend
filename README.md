# Nexo Backend

Agente conversacional de IA para restaurantes — del mensaje del cliente a la comanda de cocina, en segundos.

## Stack

- **FastAPI** — API HTTP y webhooks
- **LangGraph + OpenAI / OpenRouter** — orquestación del agente conversacional
- **PostgreSQL** — persistencia (restaurantes, clientes, menú, pedidos, conversaciones, pagos, operadores)
- **Redis** — caché de menú formateado y respuestas FAQ (degradación graciosa)
- **Twilio** — canal WhatsApp
- **Wompi** — pagos
- **Langfuse** — observabilidad LLM (opcional)

Plataforma **multi-tenant**: un mismo despliegue atiende a varios restaurantes; cada uno se resuelve por su número de WhatsApp. Ver [Multi-tenant](#multi-tenant).

## Requisitos previos

- Python 3.11+
- Docker Desktop

## Setup inicial

```bash
# 1. Clonar y crear el entorno virtual
python -m venv venv

# Windows
.\venv\Scripts\Activate.ps1
# macOS / Linux
source venv/bin/activate

# 2. Instalar dependencias
pip install -r requirements.txt

# 3. Copiar variables de entorno
cp .env.example .env
# Editar .env con tus credenciales (OPENAI_API_KEY mínimo)
```

## Levantar la base de datos

```bash
docker compose up -d                          # Inicia PostgreSQL + Redis
python -m alembic upgrade head                # Aplica migraciones
python scripts/seed_menu.py                   # Carga el menú del restaurante "default"
```

Al arrancar, el servidor crea automáticamente el restaurante `default` (tenant inicial)
con los datos de tu `.env` (`RESTAURANTE_NOMBRE`, `TWILIO_WHATSAPP_NUMBER`).

## Multi-tenant

Un mismo despliegue puede atender a varios restaurantes. Cada mensaje entrante se asocia
a un tenant resolviendo el **número de WhatsApp destino** (`To` del payload de Twilio)
contra la tabla `restaurantes`. Todos los datos (menú, pedidos, conversaciones) quedan
aislados por `restaurante_id`, y cada operador del panel admin solo ve los datos de su
restaurante.

**Dar de alta un restaurante nuevo:**

1. Inserta una fila en la tabla `restaurantes`:

   | Campo | Ejemplo | Notas |
   | --- | --- | --- |
   | `id` | `kikes-plaza` | Slug único del tenant |
   | `nombre` | `Kike's Plaza` | Nombre que usa el agente |
   | `numero_whatsapp` | `+14155238886` | Número Twilio asociado (único) |
   | `prefijo` | `KIKE` | 4 letras únicas para las referencias de pedido |
   | `activo` | `true` | |

2. Carga su menú con el seed parametrizado:

   ```bash
   python scripts/seed_menu.py --restaurante-id kikes-plaza
   ```

3. Crea un operador con `restaurante_id = kikes-plaza` para que administre ese tenant.

**Referencia de pedido:** cada restaurante numera sus pedidos con su propio prefijo y
secuencia, en formato `{PREFIJO}-{NNNN}` (ej. `KIKE-0001`, `KIKE-0002`, …).

> Si un mensaje llega a un número sin restaurante asociado, se usa el tenant `default`
> (con un warning en los logs).

## Correr el servidor

```bash
uvicorn app.main:app --reload
```

API disponible en `http://localhost:8000` · Docs en `http://localhost:8000/docs`

El servidor crea el operador admin automáticamente al iniciar si `ADMIN_PASSWORD` está definido en `.env`.

## Correr los tests

```bash
pytest                    # Todos los tests (124 en total)
pytest tests/test_grafo.py       # Tests del agente LangGraph
pytest tests/test_whatsapp.py    # Tests del webhook WhatsApp
pytest tests/test_wompi.py       # Tests del webhook Wompi
pytest tests/test_admin.py       # Tests del panel de administración
pytest tests/test_multitenant.py # Tests de multi-tenant (resolución, referencias, caché)
```

## Exponer el servidor (desarrollo)

Twilio necesita una URL pública para enviar webhooks. Usa un túnel:

```bash
# Con devtunnel (VS Code)
devtunnel host -p 8000 --allow-anonymous

# Con ngrok
ngrok http 8000
```

Actualiza `BASE_URL` en `.env` con la URL del túnel y reinicia uvicorn.

## Estructura del proyecto

```bash
app/
├── main.py               # FastAPI app + CORS + seed admin en startup
├── config.py             # Variables de entorno (pydantic-settings)
├── database.py           # SQLAlchemy engine y sesión
├── models/               # Modelos ORM (restaurantes, clientes, menú, pedidos, conversaciones, pagos, operadores)
├── agent/                # Grafo LangGraph
│   ├── graph.py          # Construcción del grafo con routing condicional
│   ├── nodes.py          # Nodos: clasificar, conversar, confirmar, faq, escalamiento, pago, etc.
│   ├── state.py          # AgentState TypedDict
│   └── prompts.py        # System prompts + zonas de cobertura
├── routers/
│   ├── whatsapp.py       # POST /webhooks/whatsapp (cola por teléfono, validación HMAC)
│   ├── wompi.py          # POST /webhooks/wompi (validación checksum SHA256)
│   ├── pedidos.py        # PATCH /pedidos/{referencia}/estado
│   ├── auth.py           # POST /auth/login (JWT Bearer)
│   └── admin.py          # GET|POST|PATCH|DELETE /admin/menu · GET /admin/pedidos
├── services/
│   ├── client_service.py       # Upsert de clientes por teléfono
│   ├── conversation_service.py # Historial + estado de pedido en JSONB
│   ├── menu_service.py         # Consulta y formato del menú (por restaurante)
│   ├── order_service.py        # Crear pedido, marcar pagado, consultar estado
│   ├── payment_service.py      # Generar link de checkout Wompi
│   ├── restaurante_service.py  # Resolución de tenant por número de WhatsApp
│   └── whatsapp_service.py     # Envío de mensajes y recibos vía Twilio
└── utils/
    ├── input_utils.py    # Sanitización y límite de entrada
    └── delivery_utils.py # Validación de zona de cobertura de domicilio
scripts/
└── seed_menu.py          # Poblar tabla menú
tests/
├── conftest.py           # Fixtures compartidas
├── test_grafo.py         # 41 tests del agente LangGraph
├── test_whatsapp.py      # 36 tests del webhook y servicios WhatsApp
├── test_wompi.py         # 17 tests del webhook Wompi
└── test_admin.py         # 21 tests de autenticación y panel de administración
docs/
├── pro-futuro.md                      # Funcionalidades excluidas del MVP Starter
└── deuda-tecnica-multi-restaurante.md # Hoja de ruta hacia multi-tenancy
```

## Variables de entorno

Copia `.env.example` a `.env` y completa las credenciales. Las más relevantes:

| Variable | Descripción |
| --- | --- |
| `OPENAI_API_KEY` | Clave de API de OpenAI |
| `OPENROUTER_API_KEY` | Alternativa a OpenAI vía OpenRouter |
| `AI_MODEL` | Modelo a usar (por defecto `gpt-4o-mini`) |
| `DATABASE_URL` | Conexión PostgreSQL |
| `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` | Credenciales Twilio |
| `TWILIO_WHATSAPP_NUMBER` | Número sandbox (`+14155238886`) |
| `BASE_URL` | URL pública del servidor (túnel en desarrollo) |
| `WOMPI_PUBLIC_KEY` / `WOMPI_EVENTS_SECRET` | Credenciales Wompi |
| `SECRET_KEY` | Clave para firma de JWT |
| `ADMIN_EMAIL` / `ADMIN_PASSWORD` | Credenciales del operador admin inicial |
| `ADMIN_RESTAURANTE_ID` | Tenant asignado al operador admin inicial (default `default`) |
| `RESTAURANTE_NOMBRE` | Nombre del restaurante `default` (seed inicial) |
| `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `LANGFUSE_HOST` | Observabilidad LLM (opcional) |
| `LOG_LEVEL` | Nivel de log: `DEBUG` / `INFO` / `WARNING` / `ERROR` |

## Endpoints principales

| Método | Ruta | Descripción |
| --- | --- | --- |
| `POST` | `/webhooks/whatsapp` | Webhook Twilio — recibe mensajes de WhatsApp |
| `POST` | `/webhooks/wompi` | Webhook Wompi — procesa eventos de pago |
| `POST` | `/auth/login` | Login operador → JWT Bearer |
| `GET` | `/admin/me` | Perfil del operador + su restaurante (requiere JWT) |
| `GET` | `/admin/pedidos` | Lista paginada de pedidos (requiere JWT) |
| `PATCH` | `/pedidos/{referencia}/estado` | Actualiza estado de un pedido (requiere JWT) |
| `GET` | `/admin/menu` | Lista ítems del menú (requiere JWT) |
| `POST` | `/admin/menu` | Crea ítem de menú (requiere JWT) |
| `PATCH` | `/admin/menu/{id}` | Edita ítem de menú (requiere JWT) |
| `DELETE` | `/admin/menu/{id}` | Elimina ítem de menú (requiere JWT) |
| `GET` | `/health` | Health check |

## Fases de implementación

| Fase | Estado | Descripción |
| --- | --- | --- |
| 1 | ✅ | Infraestructura: FastAPI, PostgreSQL, Alembic, Docker |
| 2 | ✅ | Agente LangGraph: 8 nodos, grafo con routing condicional |
| 3 | ✅ | Integración WhatsApp vía Twilio (webhook + cola por teléfono) |
| 4 | ✅ | Pagos Wompi, comanda persistida, referencia secuencial |
| 5 | ✅ | Tipos de pedido, cobertura de domicilio, escalamiento, rate limiting |
| 6 | ✅ | Panel de administración: pedidos, menú, autenticación JWT |
| 7 | ✅ | Migración multi-tenant: restaurantes por número de WhatsApp, datos aislados, referencias por restaurante |
