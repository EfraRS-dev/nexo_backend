"""
NEXO — Agente conversacional para restaurantes
Prototipo con LangGraph + OpenAI API

Flujo: recibir → conversar → comandar → cobrar
"""

import json
from typing import Annotated, TypedDict, Optional
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage
from dotenv import load_dotenv
import os
from cli_prompts import SYSTEM_PROMPT

load_dotenv()
from utils.menu_utils import formatear_menu
from utils.order_utils import generar_comanda, generar_link_pago
from utils.input_utils import limitar_entrada_usuario

# Variables de entorno y configuración
load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
AI_MODEL = os.getenv("AI_MODEL")
# ~4 caracteres por token en espanol => 360 chars ~ 90 tokens.
MAX_INPUT_CHARS = int(os.getenv("MAX_INPUT_CHARS", "360"))
MAX_OUTPUT_TOKENS = int(os.getenv("MAX_OUTPUT_TOKENS", "180"))

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

# ─────────────────────────────────────────────
# ESTADO DEL AGENTE
# ─────────────────────────────────────────────

class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    items: list[dict]
    pedido_listo: bool
    esperando_confirmacion: bool
    comanda: Optional[dict]
    link_pago: Optional[str]
    telefono_cliente: str
    etapa: str  # "conversando" | "confirmando" | "pagando" | "finalizado"


# ─────────────────────────────────────────────
# NODOS DEL GRAFO
# ─────────────────────────────────────────────

llm = ChatOpenAI(
    model=AI_MODEL,
    temperature=0.3,
    max_tokens=MAX_OUTPUT_TOKENS,
)
    

def nodo_conversar(state: AgentState) -> AgentState:
    """Llama a ChatGPT con el historial y obtiene respuesta + estado del pedido."""
    system = SYSTEM_PROMPT.format(menu=formatear_menu(MENU))
    
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


def nodo_generar_comanda(state: AgentState) -> AgentState:
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

def router(state: AgentState) -> str:
    if state.get("pedido_listo"):
        return "generar_comanda"
    return END


# ─────────────────────────────────────────────
# CONSTRUCCIÓN DEL GRAFO
# ─────────────────────────────────────────────

def construir_grafo() -> StateGraph:
    builder = StateGraph(AgentState)
    
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
    
    estado: AgentState = {
        "messages": [],
        "items": [],
        "pedido_listo": False,
        "esperando_confirmacion": False,
        "comanda": None,
        "link_pago": None,
        "telefono_cliente": telefono,
        "etapa": "conversando",
    }

    print("\n" + "="*57)
    _nombre = os.getenv("RESTAURANTE_NOMBRE", "Tu Restaurante")
    print(f"  🍔 NEXO — Agente de pedidos · {_nombre}")
    print("="*57)
    print("  Escribe tu mensaje | 'salir' para terminar")
    print("="*57 + "\n")

    # Mensaje inicial del cliente para arrancar la conversación
    msg_inicio = limitar_entrada_usuario("Hola, quiero hacer un pedido", MAX_INPUT_CHARS)
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

        entrada_limpia = limitar_entrada_usuario(entrada, MAX_INPUT_CHARS)
        if len(entrada_limpia) < len(entrada):
            print(
                f"Tu mensaje fue recortado a {MAX_INPUT_CHARS} caracteres para optimizar costos."
            )
        estado["messages"] = estado["messages"] + [HumanMessage(content=entrada_limpia)]
        estado = grafo.invoke(estado)
        
        ultimo = estado["messages"][-1]
        print(f"\n[Nexo] {ultimo.content}\n")

    if estado.get("comanda"):
        print("\n" + "="*57)
        print("  📋 COMANDA GENERADA")
        print("="*57)
        print(json.dumps(estado["comanda"], indent=2, ensure_ascii=False))
        print("="*57)


# ─────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────
if __name__ == "__main__":
    iniciar_sesion(telefono="+573001234567")
