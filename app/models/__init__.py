# Importar todos los modelos para que SQLAlchemy Base.metadata los registre.
# Este módulo lo usa alembic/env.py para autogenerar migraciones.
from app.models.cliente import Cliente
from app.models.contador import Contador
from app.models.menu import Menu
from app.models.pedido import Pedido, EstadoPedido, TipoPedido
from app.models.item_pedido import ItemPedido
from app.models.conversacion import Conversacion
from app.models.pago import Pago

__all__ = [
    "Cliente",
    "Contador",
    "Menu",
    "Pedido",
    "EstadoPedido",
    "TipoPedido",
    "ItemPedido",
    "Conversacion",
    "Pago",
]
