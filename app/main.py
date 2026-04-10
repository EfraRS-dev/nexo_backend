import datetime as dt
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import settings
from app.routers import whatsapp, wompi, pedidos

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    yield
    # Shutdown


app = FastAPI(
    title="Nexo API",
    description=f"Agente conversacional para {settings.restaurante_nombre} — del mensaje a la comanda, en segundos.",
    version="0.1.0",
    lifespan=lifespan,
)

# ── Routers ───────────────────────────────────────────────────────────────
app.include_router(whatsapp.router)
app.include_router(wompi.router)
app.include_router(pedidos.router)


@app.get("/health", tags=["infra"])
def health():
    return {"status": "ok", "datetime": dt.datetime.now().isoformat()}
