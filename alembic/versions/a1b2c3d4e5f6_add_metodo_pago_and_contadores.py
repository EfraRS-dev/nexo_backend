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
    # Fila inicial para el contador de pedidos
    op.execute("INSERT INTO contadores (nombre, valor) VALUES ('pedidos', 0)")

    # Columna metodo_pago en pedidos
    op.add_column(
        "pedidos",
        sa.Column("metodo_pago", sa.String(), nullable=False, server_default="online"),
    )


def downgrade() -> None:
    op.drop_column("pedidos", "metodo_pago")
    op.drop_table("contadores")
