import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import settings
from app.routers import whatsapp

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
    description="Agente conversacional para restaurantes — del mensaje a la comanda, en segundos.",
    version="0.1.0",
    lifespan=lifespan,
)

# ── Routers ───────────────────────────────────────────────────────────────
app.include_router(whatsapp.router)


@app.get("/health", tags=["infra"])
def health():
    return {"status": "ok", "service": "nexo"}
