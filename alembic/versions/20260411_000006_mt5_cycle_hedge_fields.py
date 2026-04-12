"""Add hedge tracking fields to mt5_trade_cycles.

Revision ID: 20260411_000006
Revises: 20260411_000005
Create Date: 2026-04-11 00:00:06
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260411_000006"
down_revision = "20260411_000005"
branch_labels = None
depends_on = None


def _column_exists(inspector: sa.Inspector, table: str, column: str) -> bool:
    return any(col["name"] == column for col in inspector.get_columns(table))


def _table_exists(inspector: sa.Inspector, table: str) -> bool:
    return table in set(inspector.get_table_names())


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _table_exists(inspector, "mt5_trade_cycles"):
        return

    if not _column_exists(inspector, "mt5_trade_cycles", "hedge_position_ticket"):
        op.add_column(
            "mt5_trade_cycles",
            sa.Column("hedge_position_ticket", sa.BigInteger(), nullable=True),
        )
        op.create_index(
            "ix_mt5_trade_cycles_hedge_position_ticket",
            "mt5_trade_cycles",
            ["hedge_position_ticket"],
            unique=False,
        )

    if not _column_exists(inspector, "mt5_trade_cycles", "hedge_placed_at"):
        op.add_column(
            "mt5_trade_cycles",
            sa.Column("hedge_placed_at", sa.DateTime(timezone=True), nullable=True),
        )

    if not _column_exists(inspector, "mt5_trade_cycles", "hedge_sl_last_modified"):
        op.add_column(
            "mt5_trade_cycles",
            sa.Column("hedge_sl_last_modified", sa.Float(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _table_exists(inspector, "mt5_trade_cycles"):
        return

    if _column_exists(inspector, "mt5_trade_cycles", "hedge_sl_last_modified"):
        op.drop_column("mt5_trade_cycles", "hedge_sl_last_modified")

    if _column_exists(inspector, "mt5_trade_cycles", "hedge_placed_at"):
        op.drop_column("mt5_trade_cycles", "hedge_placed_at")

    if _column_exists(inspector, "mt5_trade_cycles", "hedge_position_ticket"):
        op.drop_index(
            "ix_mt5_trade_cycles_hedge_position_ticket",
            table_name="mt5_trade_cycles",
        )
        op.drop_column("mt5_trade_cycles", "hedge_position_ticket")
