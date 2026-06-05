from sqlalchemy import String, Boolean
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class Restaurante(Base):
    """
    Tenant de la plataforma. Cada restaurante se resuelve por su número de
    WhatsApp (numero_whatsapp) y numera sus pedidos con su propio prefijo.
    """
    __tablename__ = "restaurantes"

    # Slug o identificador legible (ej: "default", "kikes-plaza")
    id: Mapped[str] = mapped_column(String, primary_key=True)
    nombre: Mapped[str] = mapped_column(String, nullable=False)
    # Número Twilio asociado (formato E.164, ej: +14155238886)
    numero_whatsapp: Mapped[str] = mapped_column(
        String, nullable=False, unique=True, index=True
    )
    # Prefijo de 4 letras para las referencias de pedido (ej: "KIKE" → KIKE-0001)
    prefijo: Mapped[str] = mapped_column(String(4), nullable=False, unique=True)
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Configuración extendida reservada (zonas, horarios, etc.)
    config_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
