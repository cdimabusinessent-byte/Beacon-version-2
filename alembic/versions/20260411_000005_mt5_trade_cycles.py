"""Add MT5 ATR trade cycles.

Revision ID: 20260411_000005
Revises: 20260408_000004
Create Date: 2026-04-11 00:00:05
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260411_000005"
down_revision = "20260408_000004"
branch_labels = None
depends_on = None


def _table_exists(inspector: sa.Inspector, table_name: str) -> bool:
    return table_name in set(inspector.get_table_names())


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _table_exists(inspector, "mt5_trade_cycles"):
        return

    op.create_table(
        "mt5_trade_cycles",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("owner_id", sa.String(length=64), nullable=False, server_default="local"),
        sa.Column("signal_symbol", sa.String(length=20), nullable=False),
        sa.Column("execution_symbol", sa.String(length=20), nullable=False),
        sa.Column("cycle_type", sa.String(length=32), nullable=False, server_default="ATR_RECOVERY"),
        sa.Column("base_direction", sa.String(length=8), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="OPEN"),
        sa.Column("atr_recovery_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("entry_price", sa.Float(), nullable=True),
        sa.Column("latest_price", sa.Float(), nullable=True),
        sa.Column("atr_pct", sa.Float(), nullable=True),
        sa.Column("atr_value", sa.Float(), nullable=True),
        sa.Column("stop_loss", sa.Float(), nullable=True),
        sa.Column("take_profit", sa.Float(), nullable=True),
        sa.Column("hedge_trigger", sa.Float(), nullable=True),
        sa.Column("trailing_activation_price", sa.Float(), nullable=True),
        sa.Column("reversal_confirmation_price", sa.Float(), nullable=True),
        sa.Column("overlay_active", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("planned_hedge_only", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("linked_trade_id", sa.Integer(), nullable=True),
        sa.Column("close_reason", sa.String(length=64), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_mt5_trade_cycles_owner_id", "mt5_trade_cycles", ["owner_id"], unique=False)
    op.create_index("ix_mt5_trade_cycles_signal_symbol", "mt5_trade_cycles", ["signal_symbol"], unique=False)
    op.create_index("ix_mt5_trade_cycles_execution_symbol", "mt5_trade_cycles", ["execution_symbol"], unique=False)
    op.create_index("ix_mt5_trade_cycles_cycle_type", "mt5_trade_cycles", ["cycle_type"], unique=False)
    op.create_index("ix_mt5_trade_cycles_base_direction", "mt5_trade_cycles", ["base_direction"], unique=False)
    op.create_index("ix_mt5_trade_cycles_status", "mt5_trade_cycles", ["status"], unique=False)
    op.create_index("ix_mt5_trade_cycles_linked_trade_id", "mt5_trade_cycles", ["linked_trade_id"], unique=False)
    op.create_index("ix_mt5_trade_cycles_opened_at", "mt5_trade_cycles", ["opened_at"], unique=False)
    op.create_index("ix_mt5_trade_cycles_updated_at", "mt5_trade_cycles", ["updated_at"], unique=False)
    op.create_index("ix_mt5_trade_cycles_closed_at", "mt5_trade_cycles", ["closed_at"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not _table_exists(inspector, "mt5_trade_cycles"):
        return

    op.drop_index("ix_mt5_trade_cycles_closed_at", table_name="mt5_trade_cycles")
    op.drop_index("ix_mt5_trade_cycles_updated_at", table_name="mt5_trade_cycles")
    op.drop_index("ix_mt5_trade_cycles_opened_at", table_name="mt5_trade_cycles")
    op.drop_index("ix_mt5_trade_cycles_linked_trade_id", table_name="mt5_trade_cycles")
    op.drop_index("ix_mt5_trade_cycles_status", table_name="mt5_trade_cycles")
    op.drop_index("ix_mt5_trade_cycles_base_direction", table_name="mt5_trade_cycles")
    op.drop_index("ix_mt5_trade_cycles_cycle_type", table_name="mt5_trade_cycles")
    op.drop_index("ix_mt5_trade_cycles_execution_symbol", table_name="mt5_trade_cycles")
    op.drop_index("ix_mt5_trade_cycles_signal_symbol", table_name="mt5_trade_cycles")
    op.drop_index("ix_mt5_trade_cycles_owner_id", table_name="mt5_trade_cycles")
    op.drop_table("mt5_trade_cycles")