"""
Demo del agente Nexo por consola (CLI), sin WhatsApp ni servidor HTTP.

Ejercita el MISMO grafo LangGraph que usa el webhook de producción
(`app.agent.graph`), resolviendo el menú, el nombre del restaurante y las
zonas de cobertura del tenant `default` desde la base de datos — igual que
`app/routers/whatsapp.py`.

Reemplaza al antiguo prototipo de la raíz (`cli_demo.py` + `utils/` +
`cli_prompts.py`), que corría un grafo y un menú hardcodeados ya obsoletos.

Uso:
    python scripts/cli_demo.py

Requiere modelos LLM configurados (.env) y Postgres con migraciones + seed:
    docker compose up -d
    python -m alembic upgrade head
    python scripts/seed_menu.py

Escribe 'salir' para terminar la sesión.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

from langchain_core.messages import HumanMessage
from sqlalchemy.exc import OperationalError

from app.agent.graph import construir_grafo
from app.agent.prompts import zonas_a_texto
from app.agent.state import AgentState
from app.config import settings
from app.database import SessionLocal
from app.services.menu_service import obtener_menu_formateado
from app.services.restaurante_service import RESTAURANTE_DEFAULT, obtener_restaurante
from app.utils.delivery_utils import obtener_zonas_cobertura
from app.utils.input_utils import limitar_entrada_usuario

ANCHO = 60


def _construir_grafo_del_tenant(db):
    """Resuelve menú, nombre y zonas del restaurante default y compila el grafo."""
    restaurante = obtener_restaurante(db, RESTAURANTE_DEFAULT)
    restaurante_nombre = (
        restaurante.nombre if restaurante else settings.restaurante_nombre
    )
    menu_texto = obtener_menu_formateado(db, RESTAURANTE_DEFAULT)
    zonas_texto = zonas_a_texto(obtener_zonas_cobertura(restaurante))
    grafo = construir_grafo(menu_texto, restaurante_nombre, zonas_texto)
    return grafo, restaurante_nombre


def _estado_inicial(telefono: str) -> AgentState:
    return {
        "messages": [],
        "items": [],
        "pedido_listo": False,
        "esperando_confirmacion": False,
        "intencion": "",
        "tipo_pedido": "",
        "direccion_entrega": None,
        "metodo_pago": "",
        "comanda": None,
        "telefono_cliente": telefono,
        "cliente_id": None,
        "conversacion_id": None,
        "restaurante_id": RESTAURANTE_DEFAULT,
        "etapa": "conversando",
        "requiere_escalamiento": False,
    }


def main() -> int:
    telefono = "+573001234567"

    db = SessionLocal()
    try:
        grafo, restaurante_nombre = _construir_grafo_del_tenant(db)
    except OperationalError:
        print("No se pudo leer la base de datos. Aplica migraciones y seed primero:")
        print("  docker compose up -d")
        print("  python -m alembic upgrade head && python scripts/seed_menu.py")
        return 1
    finally:
        db.close()

    estado = _estado_inicial(telefono)

    print("\n" + "=" * ANCHO)
    print(f"  🍔 NEXO — Demo de pedidos · {restaurante_nombre}")
    print("  Escribe tu mensaje | 'salir' para terminar")
    print("=" * ANCHO + "\n")

    max_chars = settings.max_input_chars

    # Mensaje inicial para arrancar la conversación
    entrada_inicio = "Hola, quiero hacer un pedido"
    print(f"[Tú]   {entrada_inicio}")
    estado["messages"] = [HumanMessage(content=entrada_inicio)]
    estado = grafo.invoke(estado)
    print(f"[Nexo] {estado['messages'][-1].content}\n")

    # Loop de conversación (el grafo corre una vez por mensaje, como en el webhook)
    while estado["etapa"] not in ("finalizado", "escalado"):
        try:
            entrada = input("[Tú]   ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nSesión interrumpida.")
            break

        if not entrada:
            continue
        if entrada.lower() in ("salir", "exit", "quit"):
            print("[Nexo] ¡Hasta pronto! 👋")
            break

        entrada_limpia = limitar_entrada_usuario(entrada, max_chars)
        if len(entrada_limpia) < len(entrada):
            print(f"(Mensaje recortado a {max_chars} caracteres para optimizar costos.)")
        estado["messages"] = estado["messages"] + [HumanMessage(content=entrada_limpia)]
        estado = grafo.invoke(estado)
        print(f"[Nexo] {estado['messages'][-1].content}\n")

    # Resumen final
    if estado.get("comanda"):
        print("\n" + "=" * ANCHO)
        print("  📋 COMANDA GENERADA")
        print("=" * ANCHO)
        print(json.dumps(estado["comanda"], indent=2, ensure_ascii=False))
        print("=" * ANCHO)

    print(f"\n  Etapa final: {estado['etapa']}")
    print(f"  Intención: {estado.get('intencion') or '—'}")
    print(f"  Tipo de pedido: {estado.get('tipo_pedido') or '—'}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
