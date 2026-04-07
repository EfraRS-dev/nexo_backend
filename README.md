# Nexo Backend

Agente conversacional de IA para restaurantes — del mensaje del cliente a la comanda de cocina, en segundos.

## Stack

- **FastAPI** — API HTTP y webhooks
- **LangGraph + OpenAI** — orquestación del agente conversacional
- **PostgreSQL** — persistencia (clientes, menú, pedidos, conversaciones, pagos)
- **Redis** — cola de mensajes (fases futuras)
- **Twilio** — canal WhatsApp
- **Wompi** — pagos

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
docker compose up -d          # Inicia PostgreSQL + Redis
python -m alembic upgrade head # Aplica migraciones
python scripts/seed_menu.py   # Carga el menú inicial
```

## Correr el servidor

```bash
uvicorn app.main:app --reload
```

API disponible en `http://localhost:8000` · Docs en `http://localhost:8000/docs`

## Estructura del proyecto

```bash
app/
├── main.py               # FastAPI app + logging
├── config.py             # Variables de entorno (pydantic-settings)
├── database.py           # SQLAlchemy engine y sesión
├── models/               # Modelos ORM (clientes, menú, pedidos, conversaciones, pagos)
├── agent/                # Grafo LangGraph
│   ├── graph.py          # Construcción del grafo con routing condicional
│   ├── nodes.py          # Nodos: clasificar, conversar, confirmar, pago, etc.
│   ├── state.py          # AgentState TypedDict
│   └── prompts.py        # System prompts
├── routers/
│   └── whatsapp.py       # POST /webhooks/whatsapp
├── services/
│   ├── client_service.py       # Upsert de clientes por teléfono
│   ├── conversation_service.py # Historial + estado de pedido en JSONB
│   ├── menu_service.py         # Consulta y formato del menú
│   └── whatsapp_service.py     # Envío de mensajes vía Twilio
└── utils/
    └── input_utils.py    # Sanitización de entrada
scripts/
└── seed_menu.py          # Poblar tabla menú
tests/
├── conftest.py           # Fixtures compartidas
├── test_grafo.py         # 41 tests del agente LangGraph
└── test_whatsapp.py      # 17 tests de servicios y webhook
```

## Variables de entorno

Copia `.env.example` a `.env` y completa las credenciales. Las más relevantes:

| Variable | Descripción |
| --- | --- |
| `OPENAI_API_KEY` | Clave de API de OpenAI |
| `DATABASE_URL` | Conexión PostgreSQL |
| `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` | Credenciales Twilio |
| `TWILIO_WHATSAPP_NUMBER` | Número sandbox (`+14155238886`) |
| `BASE_URL` | URL pública del servidor (ngrok en desarrollo) |
| `LOG_LEVEL` | Nivel de log: `DEBUG` / `INFO` / `WARNING` / `ERROR` |

## Fases de implementación

| Fase | Estado | Descripción |
| --- | --- | --- |
| 1 | ✅ | Infraestructura: FastAPI, PostgreSQL, Alembic, Docker |
| 2 | ✅ | Agente LangGraph: 8 nodos, grafo con routing condicional |
| 3 | ✅ | Integración WhatsApp vía Twilio (webhook + persistencia) |
| 4 | 🔜 | Pagos Wompi y comanda persistida |
| 5 | 🔜 | Tipos de pedido, estado y escalamiento |
