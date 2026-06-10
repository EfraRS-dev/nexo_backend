"""add_pedido_conversacion_id

Añade `pedidos.conversacion_id` como clave de idempotencia: una conversación
finaliza en a lo sumo un pedido. Índice único PARCIAL (solo cuando no es NULL)
para permitir pedidos legacy sin conversación y bloquear duplicados a nivel DB
ante carreras entre workers (ver bug B-8).

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-06-09 00:10:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, None] = "c3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "pedidos",
        sa.Column("conversacion_id", sa.String(), nullable=True),
    )
    op.create_foreign_key(
        "fk_pedidos_conversacion_id",
        "pedidos",
        "conversaciones",
        ["conversacion_id"],
        ["id"],
    )
    # Índice único parcial: a lo sumo un pedido por conversación, ignorando NULLs.
    op.create_index(
        "uq_pedidos_conversacion_id",
        "pedidos",
        ["conversacion_id"],
        unique=True,
        postgresql_where=sa.text("conversacion_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_pedidos_conversacion_id", table_name="pedidos")
    op.drop_constraint("fk_pedidos_conversacion_id", "pedidos", type_="foreignkey")
    op.drop_column("pedidos", "conversacion_id")
