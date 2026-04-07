import uuid
from sqlalchemy import String, Integer, ForeignKey
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class ItemPedido(Base):
    __tablename__ = "items_pedido"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    pedido_id: Mapped[str] = mapped_column(
        String, ForeignKey("pedidos.id"), nullable=False, index=True
    )
    producto_id: Mapped[str] = mapped_column(
        String, ForeignKey("menu.id"), nullable=False
    )
    cantidad: Mapped[int] = mapped_column(Integer, nullable=False)
    # Keyed por nombre de sub-producto para identificar qué lleva la modificación.
    # Ej: {"Hamburguesa Doble": {"sin": ["lechuga"]}, "Papas Grandes": {"sin": ["salsa de tomate"]}}
    modificadores: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    precio_unitario: Mapped[int] = mapped_column(Integer, nullable=False)  # COP
