import enum
import uuid
from datetime import datetime, timezone
from sqlalchemy import String, Integer, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class EstadoPedido(str, enum.Enum):
    pendiente = "pendiente"
    confirmado = "confirmado"
    pagado = "pagado"
    preparando = "preparando"
    en_camino = "en_camino"
    entregado = "entregado"


class TipoPedido(str, enum.Enum):
    llevar = "llevar"
    domicilio = "domicilio"


class Pedido(Base):
    __tablename__ = "pedidos"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    cliente_id: Mapped[str] = mapped_column(
        String, ForeignKey("clientes.id"), nullable=False, index=True
    )
    restaurante_id: Mapped[str] = mapped_column(
        String, nullable=False, default="default"
    )
    referencia: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    estado: Mapped[str] = mapped_column(
        String, nullable=False, default=EstadoPedido.pendiente
    )
    tipo: Mapped[str] = mapped_column(
        String, nullable=False, default=TipoPedido.llevar
    )
    direccion_entrega: Mapped[str | None] = mapped_column(String, nullable=True)
    total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # COP
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
