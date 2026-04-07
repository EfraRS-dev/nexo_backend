# Plan: Nexo MVP Backend — Starter Plan

Migrar el prototipo CLI actual (LangGraph + OpenAI) a un backend **FastAPI** que recibe mensajes de WhatsApp vía **Twilio**, orquesta la conversación con **LangGraph**, persiste datos en **PostgreSQL**, genera comandas automáticas y gestiona cobros con **Wompi**. Cubre los 16 requerimientos funcionales del plan Starter.

---

## Fase 1: Infraestructura y Base de Datos

1. **Docker Compose** — Crear `docker-compose.yml` con `postgres:16` y `redis:7`, volúmenes persistentes y credenciales vía env vars
2. **Reestructurar proyecto** — Reorganizar a estructura modular FastAPI:
   - `app/main.py` (FastAPI app), `app/config.py` (pydantic-settings), `app/database.py`
   - `app/models/` — SQLAlchemy models (cliente, menu, pedido, item_pedido, conversacion, pago)
   - `app/routers/` — whatsapp.py, wompi.py
   - `app/agent/` — graph.py, nodes.py, state.py, prompts.py (migrar grafo actual)
   - `app/services/` — whatsapp_service, payment_service, order_service, menu_service
   - Renombrar `main.py` actual a `cli_demo.py` como referencia
3. **Modelos SQLAlchemy** según esquema del doc técnico §5.1 — 6 tablas: `clientes`, `menu`, `pedidos`, `items_pedido`, `conversaciones`, `pagos`. Reusar estructura de campos definida en el documento
4. **Alembic** — Init + migración inicial con todas las tablas
5. **Config .env** — `DATABASE_URL`, `OPENAI_API_KEY`, `TWILIO_*`, `WOMPI_*` via pydantic-settings
6. **Seed del menú** — Script que carga el menú hardcoded de `main.py` (`MENU` dict) a la tabla `menu`

**Verificación:** Docker up → Alembic migrate → seed menú → FastAPI arranca sin errores

---

## Fase 2: Agente LangGraph (evolución del grafo) — paralela parcialmente con Fase 3 pasos 11-12

7. **AgentState ampliado** — Extender el `AgentState` actual con: `session_id`, `cliente_id`, `tipo_pedido`, `direccion_entrega`, `esperando_confirmacion`, `es_faq`, `requiere_escalamiento`
8. **Nuevos nodos del grafo** — Evolucionar los 3 nodos actuales (`nodo_conversar`, `nodo_generar_comanda`, router) a 7 nodos:
   - `nodo_clasificar` — clasifica intención: pedir / FAQ / estado / escalamiento *(RF-08, RF-10, RF-11)*
   - `nodo_conversar` — evolución del actual, lee menú de DB, soporta modificadores/exclusiones *(RF-03, RF-04, RF-09)*
   - `nodo_confirmar` — resumen + confirmación explícita *(RF-07)*
   - `nodo_faq` — responde horarios, cobertura, métodos de pago *(RF-08)*
   - `nodo_estado_pedido` — consulta estado en DB *(RF-10)*
   - `nodo_escalamiento` — marca conversación como escalada *(RF-11)*
   - `nodo_generar_comanda` — persiste pedido en DB *(RF-13, RF-14)*
   - `nodo_pago` — genera link Wompi *(RF-17)*
9. **Grafo con routing condicional:**
   ```
   START → clasificar → pedir → conversar ↔ confirmar → comanda → pago → END
                       → faq → END
                       → estado → END
                       → escalamiento → END
   ```
10. **System prompt ampliado** — Expandir `prompts.py` para soportar modificadores, tipo de pedido, detección de agotados con alternativas. Agregar campos al JSON de respuesta

**Verificación:** Tests unitarios del grafo con mensajes simulados para cada flujo (pedido, FAQ, estado, escalamiento)

---

## Fase 3: Integración WhatsApp (Twilio) — RF-01, RF-22, RF-23

11. **Configurar Twilio** — Crear cuenta trial, activar WhatsApp Sandbox, anotar credenciales *(paralelo con Fase 2)*
12. **ngrok** — Instalar y configurar túnel `ngrok http 8000`, registrar URL en Twilio como webhook *(paralelo con Fase 2)*
13. **Webhook endpoint** `POST /webhooks/whatsapp` — Validar firma HMAC de Twilio, extraer teléfono + mensaje, buscar/crear cliente *(RF-22)*, recuperar conversación activa, ejecutar grafo LangGraph, persistir en DB *(RF-23)*, enviar respuesta via Twilio
14. **Servicio WhatsApp** — `enviar_mensaje(telefono, texto)` y `enviar_recibo(telefono, comanda)` *(RF-19)* usando Twilio REST API
15. **Gestión de sesiones** — 1 conversación activa por cliente, finaliza al completar pedido, historial en JSONB

**Verificación:** Enviar mensaje WhatsApp → recibir respuesta del agente, verificar cliente y conversación en DB

---

## Fase 4: Pagos y Comanda — RF-17, RF-18, RF-19, RF-13, RF-14 (depende de Fases 2 y 3)

16. **Payment service** — `generar_link_pago(pedido)` via API Wompi sandbox. Evolucionar `generar_link_pago()` de `utils/order_utils.py` a llamada real
17. **Webhook Wompi** `POST /webhooks/wompi` — Validar HMAC, procesar `transaction.updated`, si APPROVED: actualizar pedido→pagado, enviar confirmación + recibo por WhatsApp *(RF-18, RF-19)*
18. **Comanda persistida** — Reusar `calcular_total()` y `generar_comanda()` de `utils/order_utils.py`, crear registros en `pedidos` + `items_pedido` con modificadores JSONB, ID legible "NEX-XXXX" *(RF-13, RF-14)*

**Verificación:** Flujo end-to-end: WhatsApp → conversación → confirmar → link Wompi → pago sandbox → webhook → pedido pagado → recibo enviado

---

## Fase 5: Pulido y Edge Cases — RF-06, RF-10, RF-11

19. **Tipos de pedido** — Detectar llevar/domicilio, solicitar dirección, validar cobertura contra lista de zonas *(RF-06)*
20. **Estado de pedido completo** — Consulta con estados: pendiente → confirmado → pagado → preparando → en_camino → entregado
21. **Escalamiento humano** — Marcar en DB, notificar (log por ahora), responder al cliente
22. **Manejo de errores** — Mensajes vacíos/emojis/audios, timeout de 30 min, rate limiting básico por teléfono

**Verificación:** Tests de domicilio con validación de zona, consulta de estado, escalamiento, envío de audio rechazado

---

## Archivos relevantes (actuales)

- `main.py` — Reusar: `AgentState`, `MENU`, `nodo_conversar`, `nodo_generar_comanda`, routing logic
- `prompts.py` — Migrar `SYSTEM_PROMPT`, expandir con modificadores y tipos de pedido
- `utils/order_utils.py` — Reusar `calcular_total()`, `generar_comanda()`, `generar_link_pago()`
- `utils/menu_utils.py` — Reusar `formatear_menu()`, adaptar para leer de DB
- `utils/input_utils.py` — Reusar `limitar_entrada_usuario()`

## Nuevas dependencias

`fastapi`, `uvicorn[standard]`, `sqlalchemy`, `psycopg2-binary`, `alembic`, `twilio`, `pydantic-settings`

## Verificación global (end-to-end)

1. `docker compose up -d` + `alembic upgrade head` + seed
2. `uvicorn app.main:app --reload` + `ngrok http 8000`
3. WhatsApp: "Hola" → respuesta del agente
4. Pedir 2 hamburguesas sin cebolla para domicilio → confirmar → link Wompi
5. Pagar en sandbox → confirmación + recibo por WhatsApp
6. "¿Cómo va mi pedido?" → estado correcto
7. Verificar DB: 6 tablas con datos consistentes

## Excluido (Pro/Futuro)

Canal web, pedidos agendados, recuperador de ventas, multi-estación, impresión ESC/POS, múltiples métodos de pago, movimientos de dinero, analítica, reseñas, panel admin Next.js, deploy Railway, multi-tenancy
