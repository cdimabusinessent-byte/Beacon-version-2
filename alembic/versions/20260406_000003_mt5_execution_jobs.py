"""Add MT5 execution jobs table.

Revision ID: 20260406_000003
Revises: 20260406_000002
Create Date: 2026-04-06 00:00:03
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260406_000003"
down_revision = "20260406_000002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_names = set(inspector.get_table_names())

    if "mt5_execution_jobs" in table_names:
        return

    op.create_table(
        "mt5_execution_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column("profile_id", sa.Integer(), nullable=True),
        sa.Column("execution_request_id", sa.Integer(), nullable=True),
        sa.Column("client_order_id", sa.String(length=64), nullable=False),
        sa.Column("signal_symbol", sa.String(length=20), nullable=True),
        sa.Column("execution_symbol", sa.String(length=20), nullable=False),
        sa.Column("action", sa.String(length=8), nullable=False),
        sa.Column("volume", sa.Float(), nullable=False),
        sa.Column("stop_loss", sa.Float(), nullable=True),
        sa.Column("take_profit", sa.Float(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="QUEUED"),
        sa.Column("result_payload", sa.Text(), nullable=True),
        sa.Column("error", sa.String(length=255), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_mt5_execution_jobs_id", "mt5_execution_jobs", ["id"])
    op.create_index("ix_mt5_execution_jobs_owner_id", "mt5_execution_jobs", ["owner_id"])
    op.create_index("ix_mt5_execution_jobs_profile_id", "mt5_execution_jobs", ["profile_id"])
    op.create_index("ix_mt5_execution_jobs_execution_request_id", "mt5_execution_jobs", ["execution_request_id"])
    op.create_index("ix_mt5_execution_jobs_client_order_id", "mt5_execution_jobs", ["client_order_id"])
    op.create_index("ix_mt5_execution_jobs_signal_symbol", "mt5_execution_jobs", ["signal_symbol"])
    op.create_index("ix_mt5_execution_jobs_execution_symbol", "mt5_execution_jobs", ["execution_symbol"])
    op.create_index("ix_mt5_execution_jobs_action", "mt5_execution_jobs", ["action"])
    op.create_index("ix_mt5_execution_jobs_status", "mt5_execution_jobs", ["status"])
    op.create_index("ix_mt5_execution_jobs_claimed_at", "mt5_execution_jobs", ["claimed_at"])
    op.create_index("ix_mt5_execution_jobs_completed_at", "mt5_execution_jobs", ["completed_at"])
    op.create_index("ix_mt5_execution_jobs_submitted_at", "mt5_execution_jobs", ["submitted_at"])
    op.create_index("ix_mt5_execution_jobs_updated_at", "mt5_execution_jobs", ["updated_at"])


def downgrade() -> None:
    op.drop_index("ix_mt5_execution_jobs_updated_at", table_name="mt5_execution_jobs")
    op.drop_index("ix_mt5_execution_jobs_submitted_at", table_name="mt5_execution_jobs")
    op.drop_index("ix_mt5_execution_jobs_completed_at", table_name="mt5_execution_jobs")
    op.drop_index("ix_mt5_execution_jobs_claimed_at", table_name="mt5_execution_jobs")
    op.drop_index("ix_mt5_execution_jobs_status", table_name="mt5_execution_jobs")
    op.drop_index("ix_mt5_execution_jobs_action", table_name="mt5_execution_jobs")
    op.drop_index("ix_mt5_execution_jobs_execution_symbol", table_name="mt5_execution_jobs")
    op.drop_index("ix_mt5_execution_jobs_signal_symbol", table_name="mt5_execution_jobs")
    op.drop_index("ix_mt5_execution_jobs_client_order_id", table_name="mt5_execution_jobs")
    op.drop_index("ix_mt5_execution_jobs_execution_request_id", table_name="mt5_execution_jobs")
    op.drop_index("ix_mt5_execution_jobs_profile_id", table_name="mt5_execution_jobs")
    op.drop_index("ix_mt5_execution_jobs_owner_id", table_name="mt5_execution_jobs")
    op.drop_index("ix_mt5_execution_jobs_id", table_name="mt5_execution_jobs")
    op.drop_table("mt5_execution_jobs")