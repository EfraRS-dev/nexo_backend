"""
Script de prueba del agente Nexo — Opción B (REPL sin servidor HTTP).

Uso:
    python scripts/test_agent.py

Requiere conexión a modelos y Docker corriendo (postgres).
Escribe 'salir' para terminar la sesión.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from langchain_core.messages import HumanMessage
from sqlalchemy.exc import OperationalError

from app.database import SessionLocal
from app.services.menu_service import obtener_menu_formateado
from app.agent.graph import construir_grafo
from app.agent.state import AgentState

def main() -> int:
    # ── Estado inicial de la sesión ──────────────────────────────────────────
    telefono = "+573001234567"

    db = SessionLocal()
    try:
        menu_texto = obtener_menu_formateado(db)
    except OperationalError:
        print("No se pudo leer la tabla 'menu'. Ejecuta migraciones y seed antes de correr este script.")
        print("Sugerido: python -m alembic upgrade head && python scripts/seed_menu.py")
        return 1
    finally:
        db.close()

    grafo = construir_grafo(menu_texto)

    estado: AgentState = {
        "messages": [],
        "items": [],
        "pedido_listo": False,
        "esperando_confirmacion": False,
        "intencion": "",
        "tipo_pedido": "",
        "direccion_entrega": None,
        "metodo_pago": "",
        "comanda": None,
        "link_pago": None,
        "telefono_cliente": telefono,
        "cliente_id": None,
        "conversacion_id": None,
        "etapa": "conversando",
        "requiere_escalamiento": False,
    }

    print("\n" + "=" * 60)
    print("  NEXO — Test del agente (sin servidor)")
    print("  Escribe 'salir' para terminar")
    print("=" * 60 + "\n")

    # Mensaje inicial
    entrada_inicio = "Hola, quiero hacer un pedido"
    print(f"[Tú]   {entrada_inicio}")
    estado["messages"] = [HumanMessage(content=entrada_inicio)]
    estado = grafo.invoke(estado)
    print(f"[Nexo] {estado['messages'][-1].content}\n")

    # ── Loop de conversación ─────────────────────────────────────────────────
    while estado["etapa"] not in ("finalizado", "escalado"):
        try:
            entrada = input("[Tú]   ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nSesión interrumpida.")
            break

        if not entrada:
            continue
        if entrada.lower() in ("salir", "exit", "quit"):
            print("[Nexo] ¡Hasta pronto!")
            break

        estado["messages"] = estado["messages"] + [HumanMessage(content=entrada)]
        estado = grafo.invoke(estado)
        ultimo = estado["messages"][-1]
        print(f"[Nexo] {ultimo.content}\n")

    # ── Resumen final ─────────────────────────────────────────────────────────
    if estado.get("comanda"):
        import json
        print("\n" + "=" * 60)
        print("  COMANDA GENERADA")
        print("=" * 60)
        print(json.dumps(estado["comanda"], indent=2, ensure_ascii=False))

    if estado.get("items"):
        print("\n  Ítems detectados:")
        for item in estado["items"]:
            mods = item.get("modificadores", {})
            mod_str = f" {mods}" if mods else ""
            print(f"    • {item['cantidad']}x {item['nombre']} — ${item['precio_unitario']:,} COP{mod_str}")
        total = sum(i["cantidad"] * i["precio_unitario"] for i in estado["items"])
        print(f"  Total: ${total:,} COP")

    print(f"\n  Etapa final: {estado['etapa']}")
    print(f"  Intención detectada: {estado.get('intencion', '—')}")
    print(f"  Tipo de pedido: {estado.get('tipo_pedido') or '—'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
