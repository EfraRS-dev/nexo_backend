import uuid
from datetime import datetime, timezone
from sqlalchemy import String, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class Conversacion(Base):
    __tablename__ = "conversaciones"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    cliente_id: Mapped[str] = mapped_column(
        String, ForeignKey("clientes.id"), nullable=False, index=True
    )
    # Array de mensajes: [{"role": "user"|"assistant", "content": "..."}]
    mensajes: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    # activa | finalizada | escalada
    estado: Mapped[str] = mapped_column(String, nullable=False, default="activa")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
