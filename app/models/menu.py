import uuid
from sqlalchemy import String, Integer, Boolean
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class Menu(Base):
    __tablename__ = "menu"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    restaurante_id: Mapped[str] = mapped_column(
        String, nullable=False, default="default", index=True
    )
    slug: Mapped[str] = mapped_column(String, nullable=False, index=True)
    nombre: Mapped[str] = mapped_column(String, nullable=False)
    precio: Mapped[int] = mapped_column(Integer, nullable=False)  # COP enteros
    categoria: Mapped[str | None] = mapped_column(String, nullable=True)
    disponible: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
