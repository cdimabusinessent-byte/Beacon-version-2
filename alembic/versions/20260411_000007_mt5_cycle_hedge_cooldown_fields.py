"""Add anti-churn hedge cooldown fields to mt5_trade_cycles.

Revision ID: 20260411_000007
Revises: 20260411_000006
Create Date: 2026-04-11 00:00:07
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260411_000007"
down_revision = "20260411_000006"
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

    if not _column_exists(inspector, "mt5_trade_cycles", "hedge_cooldown_until"):
        op.add_column(
            "mt5_trade_cycles",
            sa.Column("hedge_cooldown_until", sa.DateTime(timezone=True), nullable=True),
        )

    if not _column_exists(inspector, "mt5_trade_cycles", "hedge_attempt_count"):
        op.add_column(
            "mt5_trade_cycles",
            sa.Column("hedge_attempt_count", sa.Integer(), nullable=False, server_default="0"),
        )

    if not _column_exists(inspector, "mt5_trade_cycles", "hedge_last_action_at"):
        op.add_column(
            "mt5_trade_cycles",
            sa.Column("hedge_last_action_at", sa.DateTime(timezone=True), nullable=True),
        )

    if not _column_exists(inspector, "mt5_trade_cycles", "hedge_last_action_price"):
        op.add_column(
            "mt5_trade_cycles",
            sa.Column("hedge_last_action_price", sa.Float(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _table_exists(inspector, "mt5_trade_cycles"):
        return

    if _column_exists(inspector, "mt5_trade_cycles", "hedge_last_action_price"):
        op.drop_column("mt5_trade_cycles", "hedge_last_action_price")

    if _column_exists(inspector, "mt5_trade_cycles", "hedge_last_action_at"):
        op.drop_column("mt5_trade_cycles", "hedge_last_action_at")

    if _column_exists(inspector, "mt5_trade_cycles", "hedge_attempt_count"):
        op.drop_column("mt5_trade_cycles", "hedge_attempt_count")

    if _column_exists(inspector, "mt5_trade_cycles", "hedge_cooldown_until"):
        op.drop_column("mt5_trade_cycles", "hedge_cooldown_until")
