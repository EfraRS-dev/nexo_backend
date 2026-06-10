"""drop_legacy_pedidos_counter

Elimina la fila legacy `contadores('pedidos', 0)` que sembraba la migración
a1b2c3d4e5f6. El runtime usa contadores por tenant ('pedidos:{restaurante_id}'),
por lo que la fila global 'pedidos' nunca se usa (ver bug B-6).

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-06-09 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op

revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("DELETE FROM contadores WHERE nombre = 'pedidos'")


def downgrade() -> None:
    # Restaura la fila legacy para mantener la simetría con el estado previo.
    op.execute(
        "INSERT INTO contadores (nombre, valor) VALUES ('pedidos', 0)"
        " ON CONFLICT (nombre) DO NOTHING"
    )
