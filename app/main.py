import datetime as dt
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routers import admin, auth, pedidos, whatsapp, wompi

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logging.getLogger("twilio.http_client").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


def _seed_admin() -> None:
    """Crea el operador admin inicial si ADMIN_PASSWORD está configurado y no existe."""
    from app.database import SessionLocal
    from app.models.operador import Operador
    from app.routers.auth import hash_password

    db = SessionLocal()
    try:
        if not db.query(Operador).filter(Operador.email == settings.admin_email).first():
            op = Operador(
                email=settings.admin_email,
                hashed_password=hash_password(settings.admin_password),
                nombre="Admin",
            )
            db.add(op)
            db.commit()
            logger.info("Operador admin creado: %s", settings.admin_email)
    except Exception as exc:  # noqa: BLE001
        logger.warning("No se pudo crear el operador admin inicial: %s", exc)
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    if settings.admin_password:
        _seed_admin()
    yield
    # Shutdown


app = FastAPI(
    title="Nexo API",
    description=f"Agente conversacional para {settings.restaurante_nombre} — del mensaje a la comanda, en segundos.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ───────────────────────────────────────────────────────────────
app.include_router(whatsapp.router)
app.include_router(wompi.router)
app.include_router(pedidos.router)
app.include_router(auth.router)
app.include_router(admin.router)


@app.get("/health", tags=["infra"])
def health():
    return {"status": "ok", "datetime": dt.datetime.now().isoformat()}
