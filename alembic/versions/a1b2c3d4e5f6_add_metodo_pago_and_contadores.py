"""add_metodo_pago_and_contadores

Revision ID: a1b2c3d4e5f6
Revises: 059ec95bac54
Create Date: 2026-04-09 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "059ec95bac54"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Tabla de contadores para referencias secuenciales
    op.create_table(
        "contadores",
        sa.Column("nombre", sa.String(), nullable=False),
        sa.Column("valor", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("nombre"),
    )
    # Nota: el contador se inicializa por tenant en runtime con la clave
    # 'pedidos:{restaurante_id}' (ver order_service._siguiente_numero_pedido);
    # no se siembra ninguna fila global aquí.

    # Columna metodo_pago en pedidos
    op.add_column(
        "pedidos",
        sa.Column("metodo_pago", sa.String(), nullable=False, server_default="online"),
    )


def downgrade() -> None:
    op.drop_column("pedidos", "metodo_pago")
    op.drop_table("contadores")
