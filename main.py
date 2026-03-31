"""
NEXO — Agente conversacional para restaurantes
Prototipo con LangGraph + Claude API

Flujo: recibir → conversar → comandar → cobrar
"""

import json
import uuid
from datetime import datetime
from typing import Annotated, TypedDict, Optional
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage
from dotenv import load_dotenv
import os

load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# ─────────────────────────────────────────────
# MODELOS DE DATOS
# ─────────────────────────────────────────────

MENU = {
    "hamburguesa_clasica": {"nombre": "Hamburguesa Clásica", "precio": 18000, "disponible": True},
    "hamburguesa_doble":   {"nombre": "Hamburguesa Doble",   "precio": 24000, "disponible": True},
    "perro_caliente":      {"nombre": "Perro Caliente",      "precio": 12000, "disponible": True},
    "papas_medianas":      {"nombre": "Papas Medianas",      "precio": 8000,  "disponible": True},
    "papas_grandes":       {"nombre": "Papas Grandes",       "precio": 10000, "disponible": True},
    "gaseosa_personal":    {"nombre": "Gaseosa Personal",    "precio": 5000,  "disponible": True},
    "gaseosa_grande":      {"nombre": "Gaseosa Grande",      "precio": 7000,  "disponible": False},
    "jugo_natural":        {"nombre": "Jugo Natural",        "precio": 9000,  "disponible": True},
    "combo_1":             {"nombre": "Combo 1 (Hambur. + Papas + Gaseosa)", "precio": 28000, "disponible": True},
    "combo_2":             {"nombre": "Combo 2 (Doble + Papas + Jugo)",      "precio": 36000, "disponible": True},
}

SYSTEM_PROMPT = """Eres Nexo, un agente de IA diseñado para ayudar en la toma de pedidos. Eres amable, eficiente y conciso.

MENÚ DISPONIBLE:
{menu}

INSTRUCCIONES:
1. Saluda al cliente y pregunta qué desea pedir.
2. Toma el pedido en lenguaje natural. Puedes sugerir combos si es conveniente.
3. Si un producto NO está disponible, informa al cliente y ofrece alternativas.
4. Cuando tengas el pedido completo, confirma los ítems y el total antes de proceder.
5. Una vez confirmado, indica que generarás el link de pago.

RESPONDE SIEMPRE en este formato JSON (sin markdown, sin texto extra):
{{
  "respuesta": "<mensaje para el cliente>",
  "items": [
    {{"id": "<id_producto>", "nombre": "<nombre>", "cantidad": <int>, "precio_unitario": <int>}}
  ],
  "pedido_listo": <true|false>,
  "esperando_confirmacion": <true|false>
}}

- "items": lista de lo que el cliente ha pedido hasta ahora. Vacío [] si aún no ha pedido nada.
- "pedido_listo": true solo cuando el cliente haya CONFIRMADO su pedido explícitamente.
- "esperando_confirmacion": true cuando hayas presentado el resumen y estés esperando que el cliente confirme.
"""

# ─────────────────────────────────────────────
# ESTADO DEL AGENTE
# ─────────────────────────────────────────────

class NexoState(TypedDict):
    messages: Annotated[list, add_messages]
    items: list[dict]
    pedido_listo: bool
    esperando_confirmacion: bool
    comanda: Optional[dict]
    link_pago: Optional[str]
    telefono_cliente: str
    etapa: str  # "conversando" | "confirmando" | "pagando" | "finalizado"


# ─────────────────────────────────────────────
# UTILIDADES
# ─────────────────────────────────────────────

def formatear_menu() -> str:
    lineas = []
    for item_id, item in MENU.items():
        estado = "✅" if item["disponible"] else "❌ NO DISPONIBLE"
        lineas.append(f"- {item['nombre']} (${item['precio']:,} COP) [{estado}] — id: {item_id}")
    return "\n".join(lineas)


def calcular_total(items: list[dict]) -> int:
    return sum(i["cantidad"] * i["precio_unitario"] for i in items)


def generar_comanda(items: list[dict], telefono: str) -> dict:
    return {
        "id": str(uuid.uuid4())[:8].upper(),
        "timestamp": datetime.now().isoformat(),
        "telefono": telefono,
        "items": items,
        "total": calcular_total(items),
        "estado": "pendiente_pago",
    }


def generar_link_pago(comanda: dict) -> str:
    # En producción: llamada real a Wompi
    return f"https://checkout.wompi.co/p/?public-key=DEMO&amount-in-cents={comanda['total'] * 100}&reference={comanda['id']}"


# ─────────────────────────────────────────────
# NODOS DEL GRAFO
# ─────────────────────────────────────────────

llm = ChatOpenAI(model="gpt-4", temperature=0.3)
    

def nodo_conversar(state: NexoState) -> NexoState:
    """Llama a ChatGPT con el historial y obtiene respuesta + estado del pedido."""
    system = SYSTEM_PROMPT.format(menu=formatear_menu())
    
    response = llm.invoke(
        [SystemMessage(content=system)] + state["messages"]
    )
    
    raw = response.content.strip()
    
    # Parsear JSON de ChatGPT
    try:
        # Limpiar posibles backticks
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = {
            "respuesta": raw,
            "items": state.get("items", []),
            "pedido_listo": False,
            "esperando_confirmacion": False,
        }
    
    respuesta_texto = data.get("respuesta", "")
    items = data.get("items", state.get("items", []))
    pedido_listo = data.get("pedido_listo", False)
    esperando = data.get("esperando_confirmacion", False)

    etapa = state.get("etapa", "conversando")
    if pedido_listo:
        etapa = "pagando"
    elif esperando:
        etapa = "confirmando"

    return {
        **state,
        "messages": state["messages"] + [AIMessage(content=respuesta_texto)],
        "items": items,
        "pedido_listo": pedido_listo,
        "esperando_confirmacion": esperando,
        "etapa": etapa,
    }


def nodo_generar_comanda(state: NexoState) -> NexoState:
    """Genera la comanda y el link de pago una vez confirmado el pedido."""
    comanda = generar_comanda(state["items"], state["telefono_cliente"])
    link = generar_link_pago(comanda)
    
    msg_pago = (
        f"✅ *Pedido confirmado* — Comanda #{comanda['id']}\n\n"
        + "\n".join(f"• {i['cantidad']}x {i['nombre']} = ${i['cantidad']*i['precio_unitario']:,} COP" for i in comanda["items"])
        + f"\n\n*Total: ${comanda['total']:,} COP*\n\n"
        f"💳 Paga aquí:\n{link}\n\n"
        "Una vez confirmado el pago, recibirás tu comanda. ¡Gracias por tu pedido! 🍔"
    )
    
    return {
        **state,
        "messages": state["messages"] + [AIMessage(content=msg_pago)],
        "comanda": comanda,
        "link_pago": link,
        "etapa": "finalizado",
    }


# ─────────────────────────────────────────────
# ROUTER — decide si ya generar comanda o seguir conversando
# ─────────────────────────────────────────────

def router(state: NexoState) -> str:
    if state.get("pedido_listo"):
        return "generar_comanda"
    return END


# ─────────────────────────────────────────────
# CONSTRUCCIÓN DEL GRAFO
# ─────────────────────────────────────────────

def construir_grafo() -> StateGraph:
    builder = StateGraph(NexoState)
    
    builder.add_node("conversar", nodo_conversar)
    builder.add_node("generar_comanda", nodo_generar_comanda)
    
    builder.set_entry_point("conversar")
    builder.add_conditional_edges("conversar", router, {
        "generar_comanda": "generar_comanda",
        END: END,
    })
    builder.add_edge("generar_comanda", END)
    
    return builder.compile()


# ─────────────────────────────────────────────
# SESIÓN INTERACTIVA (CLI)
# ─────────────────────────────────────────────

def iniciar_sesion(telefono: str = "+57300000000"):
    grafo = construir_grafo()
    
    estado: NexoState = {
        "messages": [],
        "items": [],
        "pedido_listo": False,
        "esperando_confirmacion": False,
        "comanda": None,
        "link_pago": None,
        "telefono_cliente": telefono,
        "etapa": "conversando",
    }

    print("\n" + "="*55)
    print("  🍔 NEXO — Agente de pedidos NexoBurger")
    print("="*55)
    print("  Escribe tu mensaje | 'salir' para terminar")
    print("="*55 + "\n")

    # Mensaje inicial del cliente para arrancar la conversación
    msg_inicio = "Hola, quiero hacer un pedido"
    print(f"[Tú] {msg_inicio}")
    estado["messages"] = [HumanMessage(content=msg_inicio)]
    
    estado = grafo.invoke(estado)
    ultimo = estado["messages"][-1]
    print(f"\n[Nexo] {ultimo.content}\n")

    while estado["etapa"] != "finalizado":
        try:
            entrada = input("[Tú] ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nSesión terminada.")
            break
        
        if entrada.lower() in ("salir", "exit", "quit"):
            print("\n[Nexo] ¡Hasta pronto! 👋")
            break
        
        if not entrada:
            continue

        estado["messages"] = estado["messages"] + [HumanMessage(content=entrada)]
        estado = grafo.invoke(estado)
        
        ultimo = estado["messages"][-1]
        print(f"\n[Nexo] {ultimo.content}\n")

    if estado.get("comanda"):
        print("\n" + "="*55)
        print("  📋 COMANDA GENERADA")
        print("="*55)
        print(json.dumps(estado["comanda"], indent=2, ensure_ascii=False))
        print("="*55)


# ─────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────

if __name__ == "__main__":
    iniciar_sesion(telefono="+573001234567")
