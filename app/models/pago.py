import uuid
from sqlalchemy import String, Integer, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class Pago(Base):
    __tablename__ = "pagos"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    pedido_id: Mapped[str] = mapped_column(
        String, ForeignKey("pedidos.id"), nullable=False, index=True
    )
    metodo: Mapped[str | None] = mapped_column(String, nullable=True)
    # pendiente | completado | fallido
    estado: Mapped[str] = mapped_column(String, nullable=False, default="pendiente")
    referencia_wompi: Mapped[str | None] = mapped_column(String, nullable=True)
    monto: Mapped[int] = mapped_column(Integer, nullable=False)  # COP
