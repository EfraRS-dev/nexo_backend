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


def _prefijo_desde_nombre(nombre: str) -> str:
    """Deriva un prefijo de 4 letras mayúsculas a partir del nombre."""
    import re
    letras = re.sub(r"[^A-Za-z]", "", nombre or "").upper()
    return (letras[:4] or "NEXO").ljust(4, "X")


def _seed_restaurante_default() -> None:
    """Crea el restaurante 'default' (tenant inicial) si no existe. Idempotente."""
    from app.database import SessionLocal
    from app.models.restaurante import Restaurante

    db = SessionLocal()
    try:
        if not db.query(Restaurante).filter(Restaurante.id == "default").first():
            rest = Restaurante(
                id="default",
                nombre=settings.restaurante_nombre,
                numero_whatsapp=settings.twilio_whatsapp_number or "+00000000000",
                prefijo=_prefijo_desde_nombre(settings.restaurante_nombre),
                activo=True,
            )
            db.add(rest)
            db.commit()
            logger.info("Restaurante 'default' creado: %s", settings.restaurante_nombre)
    except Exception as exc:  # noqa: BLE001
        logger.warning("No se pudo crear el restaurante 'default': %s", exc)
    finally:
        db.close()


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
                restaurante_id=settings.admin_restaurante_id,
            )
            db.add(op)
            db.commit()
            logger.info("Operador admin creado: %s", settings.admin_email)
    except Exception as exc:  # noqa: BLE001
        logger.warning("No se pudo crear el operador admin inicial: %s", exc)
    finally:
        db.close()


def _check_db() -> str:
    """Verifica la conexión a PostgreSQL. Retorna 'ok' o el mensaje de error."""
    try:
        from sqlalchemy import text
        from app.database import engine
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return "ok"
    except Exception as exc:
        return str(exc)


def _check_redis() -> str:
    """Verifica la conexión a Redis. Retorna 'ok' o el mensaje de error."""
    try:
        import redis as redis_lib
        r = redis_lib.from_url(settings.redis_url, socket_connect_timeout=2)
        r.ping()
        return "ok"
    except Exception as exc:
        return str(exc)


def _check_langfuse() -> str:
    """Verifica que las claves de Langfuse estén configuradas."""
    if settings.langfuse_public_key and settings.langfuse_secret_key:
        return "ok"
    return "not configured"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ───────────────────────────────────────────────────────────
    _seed_restaurante_default()
    if settings.admin_password:
        _seed_admin()

    db_status    = _check_db()
    redis_status = _check_redis()
    lf_status    = _check_langfuse()

    sep = "─" * 54
    logger.info(sep)
    logger.info("  Nexo API  ·  %s", settings.restaurante_nombre)
    logger.info(sep)
    logger.info("  Model    : %s", settings.ai_model)
    logger.info("  Base URL : %s", settings.base_url)
    logger.info(sep)
    logger.info("  PostgreSQL : %s", db_status)
    logger.info("  Redis      : %s", redis_status)
    logger.info("  Langfuse   : %s", lf_status)
    logger.info(sep)

    if db_status != "ok":
        logger.error("PostgreSQL no disponible — el servidor puede no funcionar correctamente")
    if redis_status != "ok":
        logger.warning("Redis no disponible — caché deshabilitado")

    yield
    # ── Shutdown ──────────────────────────────────────────────────────────
    logger.info("API detenida.")


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
