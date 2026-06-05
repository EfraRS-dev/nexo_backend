"""add_multitenant

Crea la tabla `restaurantes` y agrega `restaurante_id` a `conversaciones`
y `operadores`. Hace backfill del tenant "default" con los datos del .env
actual y rellena las filas existentes antes de forzar NOT NULL.

Revision ID: b2c3d4e5f6a7
Revises: f1a2b3c4d5e6
Create Date: 2026-06-05 00:00:00.000000
"""
import os
import re
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from dotenv import load_dotenv
from sqlalchemy.dialects.postgresql import JSONB

# Cargar .env para que el backfill del tenant "default" use los valores reales
# (RESTAURANTE_NOMBRE, TWILIO_WHATSAPP_NUMBER) y no los fallbacks.
load_dotenv()

revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, None] = "f1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _prefijo_desde_nombre(nombre: str) -> str:
    """Deriva un prefijo de 4 letras mayúsculas a partir del nombre."""
    letras = re.sub(r"[^A-Za-z]", "", nombre or "").upper()
    return (letras[:4] or "NEXO").ljust(4, "X")


def upgrade() -> None:
    # ── Tabla restaurantes ────────────────────────────────────────────
    op.create_table(
        "restaurantes",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("nombre", sa.String(), nullable=False),
        sa.Column("numero_whatsapp", sa.String(), nullable=False),
        sa.Column("prefijo", sa.String(length=4), nullable=False),
        sa.Column("activo", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("config_json", JSONB(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("numero_whatsapp"),
        sa.UniqueConstraint("prefijo"),
    )
    op.create_index(
        op.f("ix_restaurantes_numero_whatsapp"),
        "restaurantes",
        ["numero_whatsapp"],
        unique=True,
    )

    # ── Backfill: tenant "default" desde el .env actual ───────────────
    nombre = os.getenv("RESTAURANTE_NOMBRE", "Nexo")
    numero = os.getenv("TWILIO_WHATSAPP_NUMBER", "+00000000000")
    prefijo = _prefijo_desde_nombre(nombre)
    op.execute(
        sa.text(
            "INSERT INTO restaurantes (id, nombre, numero_whatsapp, prefijo, activo) "
            "VALUES ('default', :nombre, :numero, :prefijo, true)"
        ).bindparams(nombre=nombre, numero=numero, prefijo=prefijo)
    )

    # ── restaurante_id en conversaciones ──────────────────────────────
    op.add_column(
        "conversaciones",
        sa.Column("restaurante_id", sa.String(), nullable=False, server_default="default"),
    )
    op.create_index(
        op.f("ix_conversaciones_restaurante_id"),
        "conversaciones",
        ["restaurante_id"],
    )

    # ── restaurante_id en operadores ──────────────────────────────────
    op.add_column(
        "operadores",
        sa.Column("restaurante_id", sa.String(), nullable=False, server_default="default"),
    )
    op.create_index(
        op.f("ix_operadores_restaurante_id"),
        "operadores",
        ["restaurante_id"],
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_operadores_restaurante_id"), table_name="operadores")
    op.drop_column("operadores", "restaurante_id")
    op.drop_index(op.f("ix_conversaciones_restaurante_id"), table_name="conversaciones")
    op.drop_column("conversaciones", "restaurante_id")
    op.drop_index(op.f("ix_restaurantes_numero_whatsapp"), table_name="restaurantes")
    op.drop_table("restaurantes")
