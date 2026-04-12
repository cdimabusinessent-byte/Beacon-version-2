"""Add managed MT5 workers and job affinity fields.

Revision ID: 20260411_000008
Revises: 20260411_000007
Create Date: 2026-04-11 00:00:08
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260411_000008"
down_revision = "20260411_000007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_names = set(inspector.get_table_names())

    columns = {column["name"] for column in inspector.get_columns("mt5_execution_jobs")} if "mt5_execution_jobs" in table_names else set()
    indexes = {item["name"] for item in inspector.get_indexes("mt5_execution_jobs")} if "mt5_execution_jobs" in table_names else set()

    if "mt5_execution_jobs" in table_names:
        if "assigned_worker_key" not in columns:
            op.add_column("mt5_execution_jobs", sa.Column("assigned_worker_key", sa.String(length=100), nullable=True))
        if "claimed_by_worker_key" not in columns:
            op.add_column("mt5_execution_jobs", sa.Column("claimed_by_worker_key", sa.String(length=100), nullable=True))
        if "ix_mt5_execution_jobs_assigned_worker_key" not in indexes:
            op.create_index("ix_mt5_execution_jobs_assigned_worker_key", "mt5_execution_jobs", ["assigned_worker_key"])
        if "ix_mt5_execution_jobs_claimed_by_worker_key" not in indexes:
            op.create_index("ix_mt5_execution_jobs_claimed_by_worker_key", "mt5_execution_jobs", ["claimed_by_worker_key"])

    if "mt5_workers" not in table_names:
        op.create_table(
            "mt5_workers",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("worker_key", sa.String(length=100), nullable=False),
            sa.Column("owner_id", sa.String(length=64), nullable=False),
            sa.Column("profile_id", sa.Integer(), nullable=True),
            sa.Column("label", sa.String(length=100), nullable=True),
            sa.Column("terminal_path", sa.String(length=255), nullable=True),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="ONLINE"),
            sa.Column("last_error", sa.String(length=255), nullable=True),
            sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_claimed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_mt5_workers_id", "mt5_workers", ["id"])
        op.create_index("ix_mt5_workers_worker_key", "mt5_workers", ["worker_key"], unique=True)
        op.create_index("ix_mt5_workers_owner_id", "mt5_workers", ["owner_id"])
        op.create_index("ix_mt5_workers_profile_id", "mt5_workers", ["profile_id"])
        op.create_index("ix_mt5_workers_status", "mt5_workers", ["status"])
        op.create_index("ix_mt5_workers_heartbeat_at", "mt5_workers", ["heartbeat_at"])
        op.create_index("ix_mt5_workers_last_claimed_at", "mt5_workers", ["last_claimed_at"])
        op.create_index("ix_mt5_workers_created_at", "mt5_workers", ["created_at"])
        op.create_index("ix_mt5_workers_updated_at", "mt5_workers", ["updated_at"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_names = set(inspector.get_table_names())

    if "mt5_workers" in table_names:
        op.drop_index("ix_mt5_workers_updated_at", table_name="mt5_workers")
        op.drop_index("ix_mt5_workers_created_at", table_name="mt5_workers")
        op.drop_index("ix_mt5_workers_last_claimed_at", table_name="mt5_workers")
        op.drop_index("ix_mt5_workers_heartbeat_at", table_name="mt5_workers")
        op.drop_index("ix_mt5_workers_status", table_name="mt5_workers")
        op.drop_index("ix_mt5_workers_profile_id", table_name="mt5_workers")
        op.drop_index("ix_mt5_workers_owner_id", table_name="mt5_workers")
        op.drop_index("ix_mt5_workers_worker_key", table_name="mt5_workers")
        op.drop_index("ix_mt5_workers_id", table_name="mt5_workers")
        op.drop_table("mt5_workers")

    if "mt5_execution_jobs" in table_names:
        indexes = {item["name"] for item in inspector.get_indexes("mt5_execution_jobs")}
        columns = {column["name"] for column in inspector.get_columns("mt5_execution_jobs")}
        if "ix_mt5_execution_jobs_claimed_by_worker_key" in indexes:
            op.drop_index("ix_mt5_execution_jobs_claimed_by_worker_key", table_name="mt5_execution_jobs")
        if "ix_mt5_execution_jobs_assigned_worker_key" in indexes:
            op.drop_index("ix_mt5_execution_jobs_assigned_worker_key", table_name="mt5_execution_jobs")
        if "claimed_by_worker_key" in columns:
            op.drop_column("mt5_execution_jobs", "claimed_by_worker_key")
        if "assigned_worker_key" in columns:
            op.drop_column("mt5_execution_jobs", "assigned_worker_key")
