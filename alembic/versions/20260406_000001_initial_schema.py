"""Initial Beacon schema baseline.

Revision ID: 20260406_000001
Revises:
Create Date: 2026-04-06 00:00:01
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260406_000001"
down_revision = None
branch_labels = None
depends_on = None


def _table_exists(inspector: sa.Inspector, table_name: str) -> bool:
    return table_name in set(inspector.get_table_names())


def _column_names(inspector: sa.Inspector, table_name: str) -> set[str]:
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _table_exists(inspector, "trades"):
        op.create_table(
            "trades",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("symbol", sa.String(length=20), nullable=False),
            sa.Column("signal_symbol", sa.String(length=20), nullable=True),
            sa.Column("execution_symbol", sa.String(length=20), nullable=True),
            sa.Column("side", sa.String(length=8), nullable=False),
            sa.Column("quantity", sa.Float(), nullable=False),
            sa.Column("price", sa.Float(), nullable=False),
            sa.Column("quote_amount", sa.Float(), nullable=False),
            sa.Column("rsi_value", sa.Float(), nullable=False),
            sa.Column("signal", sa.String(length=12), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False),
            sa.Column("exchange_order_id", sa.String(length=64), nullable=True),
            sa.Column("broker_position_id", sa.String(length=64), nullable=True),
            sa.Column("intended_price", sa.Float(), nullable=True),
            sa.Column("fill_price", sa.Float(), nullable=True),
            sa.Column("slippage_pct", sa.Float(), nullable=True),
            sa.Column("fee_amount", sa.Float(), nullable=True),
            sa.Column("entry_stop_loss", sa.Float(), nullable=True),
            sa.Column("entry_take_profit", sa.Float(), nullable=True),
            sa.Column("strategy_weights", sa.Text(), nullable=True),
            sa.Column("confidence", sa.Float(), nullable=True),
            sa.Column("equity_before", sa.Float(), nullable=True),
            sa.Column("equity_after", sa.Float(), nullable=True),
            sa.Column("realized_pnl", sa.Float(), nullable=True),
            sa.Column("realized_pnl_pct", sa.Float(), nullable=True),
            sa.Column("outcome", sa.String(length=16), nullable=True),
            sa.Column("reconciliation_status", sa.String(length=20), nullable=True),
            sa.Column("is_dry_run", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("notes", sa.String(length=255), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_trades_id", "trades", ["id"])
        op.create_index("ix_trades_symbol", "trades", ["symbol"])
        op.create_index("ix_trades_signal_symbol", "trades", ["signal_symbol"])
        op.create_index("ix_trades_execution_symbol", "trades", ["execution_symbol"])
        op.create_index("ix_trades_side", "trades", ["side"])
        op.create_index("ix_trades_created_at", "trades", ["created_at"])
    else:
        existing_columns = _column_names(inspector, "trades")
        pending_columns = {
            "signal_symbol": sa.Column("signal_symbol", sa.String(length=20), nullable=True),
            "execution_symbol": sa.Column("execution_symbol", sa.String(length=20), nullable=True),
            "broker_position_id": sa.Column("broker_position_id", sa.String(length=64), nullable=True),
            "intended_price": sa.Column("intended_price", sa.Float(), nullable=True),
            "fill_price": sa.Column("fill_price", sa.Float(), nullable=True),
            "slippage_pct": sa.Column("slippage_pct", sa.Float(), nullable=True),
            "fee_amount": sa.Column("fee_amount", sa.Float(), nullable=True),
            "entry_stop_loss": sa.Column("entry_stop_loss", sa.Float(), nullable=True),
            "entry_take_profit": sa.Column("entry_take_profit", sa.Float(), nullable=True),
            "strategy_weights": sa.Column("strategy_weights", sa.Text(), nullable=True),
            "confidence": sa.Column("confidence", sa.Float(), nullable=True),
            "equity_before": sa.Column("equity_before", sa.Float(), nullable=True),
            "equity_after": sa.Column("equity_after", sa.Float(), nullable=True),
            "realized_pnl": sa.Column("realized_pnl", sa.Float(), nullable=True),
            "realized_pnl_pct": sa.Column("realized_pnl_pct", sa.Float(), nullable=True),
            "outcome": sa.Column("outcome", sa.String(length=16), nullable=True),
            "reconciliation_status": sa.Column("reconciliation_status", sa.String(length=20), nullable=True),
        }
        for column_name, column in pending_columns.items():
            if column_name not in existing_columns:
                op.add_column("trades", column)

    if not _table_exists(inspector, "mt5_profiles"):
        op.create_table(
            "mt5_profiles",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("owner_id", sa.String(length=64), nullable=False),
            sa.Column("label", sa.String(length=100), nullable=False),
            sa.Column("login", sa.Integer(), nullable=False),
            sa.Column("password_encrypted", sa.Text(), nullable=False),
            sa.Column("server", sa.String(length=100), nullable=False),
            sa.Column("terminal_path", sa.String(length=255), nullable=True),
            sa.Column("symbols_csv", sa.Text(), nullable=False, server_default=""),
            sa.Column("volume_lots", sa.Float(), nullable=False, server_default="0.01"),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("last_connection_ok", sa.Boolean(), nullable=True),
            sa.Column("last_connection_error", sa.String(length=255), nullable=True),
            sa.Column("last_validated_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_mt5_profiles_id", "mt5_profiles", ["id"])
        op.create_index("ix_mt5_profiles_owner_id", "mt5_profiles", ["owner_id"])
        op.create_index("ix_mt5_profiles_label", "mt5_profiles", ["label"])
        op.create_index("ix_mt5_profiles_login", "mt5_profiles", ["login"])
        op.create_index("ix_mt5_profiles_server", "mt5_profiles", ["server"])
        op.create_index("ix_mt5_profiles_is_active", "mt5_profiles", ["is_active"])
        op.create_index("ix_mt5_profiles_created_at", "mt5_profiles", ["created_at"])
        op.create_index("ix_mt5_profiles_updated_at", "mt5_profiles", ["updated_at"])

    if not _table_exists(inspector, "app_users"):
        op.create_table(
            "app_users",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("owner_id", sa.String(length=64), nullable=False),
            sa.Column("email", sa.String(length=255), nullable=False),
            sa.Column("display_name", sa.String(length=100), nullable=False),
            sa.Column("password_hash", sa.Text(), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("owner_id"),
            sa.UniqueConstraint("email"),
        )
        op.create_index("ix_app_users_id", "app_users", ["id"])
        op.create_index("ix_app_users_owner_id", "app_users", ["owner_id"])
        op.create_index("ix_app_users_email", "app_users", ["email"])
        op.create_index("ix_app_users_is_active", "app_users", ["is_active"])
        op.create_index("ix_app_users_created_at", "app_users", ["created_at"])
        op.create_index("ix_app_users_updated_at", "app_users", ["updated_at"])

    if not _table_exists(inspector, "mobile_sessions"):
        op.create_table(
            "mobile_sessions",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("owner_id", sa.String(length=64), nullable=False),
            sa.Column("token_hash", sa.String(length=128), nullable=False),
            sa.Column("device_name", sa.String(length=120), nullable=True),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("token_hash"),
        )
        op.create_index("ix_mobile_sessions_id", "mobile_sessions", ["id"])
        op.create_index("ix_mobile_sessions_owner_id", "mobile_sessions", ["owner_id"])
        op.create_index("ix_mobile_sessions_token_hash", "mobile_sessions", ["token_hash"])
        op.create_index("ix_mobile_sessions_expires_at", "mobile_sessions", ["expires_at"])
        op.create_index("ix_mobile_sessions_created_at", "mobile_sessions", ["created_at"])

    if not _table_exists(inspector, "execution_requests"):
        op.create_table(
            "execution_requests",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("idempotency_key", sa.String(length=128), nullable=False),
            sa.Column("client_order_id", sa.String(length=64), nullable=False),
            sa.Column("signal_symbol", sa.String(length=20), nullable=False),
            sa.Column("execution_symbol", sa.String(length=20), nullable=False),
            sa.Column("action", sa.String(length=8), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False),
            sa.Column("broker_order_id", sa.String(length=64), nullable=True),
            sa.Column("error", sa.String(length=255), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint("idempotency_key", name="uq_execution_requests_idempotency_key"),
        )
        op.create_index("ix_execution_requests_id", "execution_requests", ["id"])
        op.create_index("ix_execution_requests_idempotency_key", "execution_requests", ["idempotency_key"])
        op.create_index("ix_execution_requests_client_order_id", "execution_requests", ["client_order_id"])
        op.create_index("ix_execution_requests_signal_symbol", "execution_requests", ["signal_symbol"])
        op.create_index("ix_execution_requests_execution_symbol", "execution_requests", ["execution_symbol"])
        op.create_index("ix_execution_requests_action", "execution_requests", ["action"])
        op.create_index("ix_execution_requests_status", "execution_requests", ["status"])
        op.create_index("ix_execution_requests_created_at", "execution_requests", ["created_at"])

    if not _table_exists(inspector, "broker_fill_journal"):
        op.create_table(
            "broker_fill_journal",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("provider", sa.String(length=20), nullable=False),
            sa.Column("execution_symbol", sa.String(length=20), nullable=False),
            sa.Column("broker_fill_id", sa.String(length=128), nullable=False),
            sa.Column("side", sa.String(length=8), nullable=True),
            sa.Column("quantity", sa.Float(), nullable=True),
            sa.Column("price", sa.Float(), nullable=True),
            sa.Column("raw_payload", sa.Text(), nullable=False),
            sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("provider", "execution_symbol", "broker_fill_id", name="uq_fill_journal_entry"),
        )
        op.create_index("ix_broker_fill_journal_id", "broker_fill_journal", ["id"])
        op.create_index("ix_broker_fill_journal_provider", "broker_fill_journal", ["provider"])
        op.create_index("ix_broker_fill_journal_execution_symbol", "broker_fill_journal", ["execution_symbol"])
        op.create_index("ix_broker_fill_journal_broker_fill_id", "broker_fill_journal", ["broker_fill_id"])
        op.create_index("ix_broker_fill_journal_observed_at", "broker_fill_journal", ["observed_at"])

    if not _table_exists(inspector, "broker_position_journal"):
        op.create_table(
            "broker_position_journal",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("provider", sa.String(length=20), nullable=False),
            sa.Column("execution_symbol", sa.String(length=20), nullable=False),
            sa.Column("quantity", sa.Float(), nullable=False),
            sa.Column("raw_payload", sa.Text(), nullable=False),
            sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_broker_position_journal_id", "broker_position_journal", ["id"])
        op.create_index("ix_broker_position_journal_provider", "broker_position_journal", ["provider"])
        op.create_index("ix_broker_position_journal_execution_symbol", "broker_position_journal", ["execution_symbol"])
        op.create_index("ix_broker_position_journal_observed_at", "broker_position_journal", ["observed_at"])


def downgrade() -> None:
    pass