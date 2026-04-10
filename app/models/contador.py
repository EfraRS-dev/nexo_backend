from sqlalchemy import String, Integer
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class Contador(Base):
    __tablename__ = "contadores"

    nombre: Mapped[str] = mapped_column(String, primary_key=True)
    valor: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
