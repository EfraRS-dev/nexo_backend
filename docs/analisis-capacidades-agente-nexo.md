# Análisis de Capacidades de Agente en Nexo

## Resumen Ejecutivo

Nexo hoy sí es un agente conversacional, pero todavía está más cerca de un flujo guiado con LLM que de una plataforma de agentes más completa.

## Lo Que Nexo Ya Tiene

- Flujo orquestado con estados y rutas explícitas en `app/agent/graph.py`.
- Memoria de corto plazo y estado transaccional persistido por conversación en `app/services/conversation_service.py` y rehidratado desde `app/routers/whatsapp.py`.
- Un nodo principal de conversación, más nodos de FAQ, estado de pedido, escalamiento, comanda y pago en `app/agent/nodes.py`.
- Resiliencia básica: reparación de JSON si el modelo responde mal en `app/agent/nodes.py`, cola por teléfono para procesar mensajes en orden en `app/routers/whatsapp.py`, y fallback si la respuesta sale vacía en `app/routers/whatsapp.py`.

## Lo Que Falta, Ordenado por Importancia Real para Este Proyecto

### 1. RAG de verdad

Hoy no existe. El menú se lee completo desde la DB y se inyecta al prompt como texto plano en `app/services/menu_service.py` y `app/utils/menu_utils.py`. Eso no es RAG; es "context stuffing".

RAG haría falta si quieres:

- FAQ largas o cambiantes
- políticas del restaurante por sede
- promos, cobertura, horarios especiales, alérgenos
- documentos externos o base de conocimiento editable

Para el tamaño actual del sistema, RAG no parece lo más urgente. Con un menú pequeño, meter todo el contexto directo es razonable.

### 2. Caché

No vi caché de ningún tipo.

Falta:

- caché del menú formateado
- caché de respuestas FAQ frecuentes
- caché de llamadas LLM iguales o semánticamente parecidas
- caché de lectura de datos operativos si luego agregan más consultas

Ahora mismo cada mensaje vuelve a:

- leer conversación
- leer menú
- reconstruir el grafo
- llamar al modelo

Eso funciona, pero escala peor de lo necesario. Para Nexo, caché probablemente tiene más ROI inmediato que RAG.

### 3. Herramientas reales o function calling

Hoy el modelo no "usa herramientas" en runtime. Decide dentro del prompt, y luego el código ejecuta acciones fijas alrededor del resultado.

Ejemplos:

- el menú no se consulta como tool; se preinyecta al prompt
- el estado del pedido no se consulta dinámicamente por una herramienta del modelo; va por un nodo separado
- el pago no se invoca por tool call del LLM; lo hace el backend luego del grafo

Eso está bien para un MVP, pero limita mucho. Faltaría una capa de tools para cosas como:

- consultar cobertura por dirección
- validar disponibilidad/stock en vivo
- calcular tiempo estimado
- buscar promociones
- recuperar último pedido del cliente
- verificar estado de pago

### 4. Memoria de largo plazo o personalización

Sí hay memoria conversacional, pero no memoria del cliente como usuario.

Se persiste:

- historial del chat
- estado del pedido en curso

No vi memoria reutilizable de:

- dirección favorita
- método de pago preferido
- productos frecuentes
- restricciones alimentarias
- historial resumido de compras
- notas del cliente

Eso hace que Nexo "recuerde la conversación", pero no "recuerde al cliente".

### 5. Compresión de contexto

En cada turno se vuelve a pasar el historial completo al modelo desde `app/routers/whatsapp.py` y `app/agent/nodes.py`.

Falta:

- resumen de conversación
- truncado inteligente
- selección de mensajes relevantes
- memoria por slots ya resueltos

Mientras la conversación sea corta no pasa nada. En chats largos va a subir costo, latencia y riesgo de deriva.

### 6. Guardrails estructurados más fuertes

Tienen un buen comienzo porque obligan JSON en el prompt y reparan respuestas no válidas en `app/agent/nodes.py`. Pero todavía falta:

- validación formal del output con Pydantic
- validación fuerte de enums y campos requeridos
- validación de negocio antes de aceptar items
- rechazo explícito de combinaciones inválidas
- normalización robusta de slugs, cantidades y modificadores

Hoy parte de la robustez depende todavía del prompt y de fallbacks.

### 7. Observabilidad de agentes

No vi tracing específico de agentes, evaluaciones ni telemetría de prompts.

Falta:

- trazas por nodo y por modelo
- token usage y costo por turno
- latencia por etapa
- versionado de prompts
- replay de conversaciones
- dataset de evals
- métricas de éxito del pedido, abandono, escalamiento y error de parsing

Hoy hay logs generales en `app/main.py`, pero eso no alcanza para operar bien un agente en producción.

### 8. Estrategia de fallback de modelos

Tienen soporte para OpenAI/OpenRouter en `app/agent/nodes.py`, pero sigue siendo un solo modelo activo.

Falta:

- fallback automático entre modelos
- usar modelo barato para clasificación y otro mejor para extracción
- circuit breaker cuando un proveedor falla
- retry policy diferenciada por error
- canary rollout de nuevos modelos

### 9. Capa de conocimiento editable

Toda la "inteligencia" operativa está embebida en prompts estáticos en `app/agent/prompts.py`.

Falta una capa donde negocio pueda cambiar sin tocar código:

- FAQs
- políticas
- horarios por festivo
- cobertura
- promociones
- mensajes de marca

Eso no exige RAG necesariamente, pero sí una fuente de verdad externa al código.

### 10. Handoff humano más completo

Sí existe escalamiento en `app/agent/graph.py` y `app/agent/nodes.py`, pero no vi:

- bandeja para operadores
- reasignación manual
- notas internas
- devolución del caso al bot
- SLA o prioridad
- contexto resumido para el humano

## Lectura Práctica

Mi lectura práctica es esta:

- Si preguntas "¿falta RAG?", la respuesta es sí, técnicamente no existe.
- Si preguntas "¿deberíamos implementar RAG ya?", mi respuesta es no necesariamente.
- Para Nexo, antes de RAG yo priorizaría:

1. validación estructurada fuerte del output del LLM
2. caché de menú/FAQ y quizá caché LLM
3. memoria de cliente
4. observabilidad/evals
5. tools reales para consultas operativas

RAG subiría de prioridad cuando el conocimiento deje de caber bien en un prompt corto y empiecen a manejar varias sedes, políticas cambiantes o una base documental amplia.
