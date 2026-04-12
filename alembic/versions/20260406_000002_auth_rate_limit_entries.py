"""Add auth rate limit entries table.

Revision ID: 20260406_000002
Revises: 20260406_000001
Create Date: 2026-04-06 00:00:02
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260406_000002"
down_revision = "20260406_000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_names = set(inspector.get_table_names())

    if "auth_rate_limit_entries" in table_names:
        return

    op.create_table(
        "auth_rate_limit_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("key", sa.String(length=255), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("blocked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("key"),
    )
    op.create_index("ix_auth_rate_limit_entries_id", "auth_rate_limit_entries", ["id"])
    op.create_index("ix_auth_rate_limit_entries_key", "auth_rate_limit_entries", ["key"])
    op.create_index("ix_auth_rate_limit_entries_window_started_at", "auth_rate_limit_entries", ["window_started_at"])
    op.create_index("ix_auth_rate_limit_entries_blocked_until", "auth_rate_limit_entries", ["blocked_until"])
    op.create_index("ix_auth_rate_limit_entries_created_at", "auth_rate_limit_entries", ["created_at"])
    op.create_index("ix_auth_rate_limit_entries_updated_at", "auth_rate_limit_entries", ["updated_at"])


def downgrade() -> None:
    op.drop_index("ix_auth_rate_limit_entries_updated_at", table_name="auth_rate_limit_entries")
    op.drop_index("ix_auth_rate_limit_entries_created_at", table_name="auth_rate_limit_entries")
    op.drop_index("ix_auth_rate_limit_entries_blocked_until", table_name="auth_rate_limit_entries")
    op.drop_index("ix_auth_rate_limit_entries_window_started_at", table_name="auth_rate_limit_entries")
    op.drop_index("ix_auth_rate_limit_entries_key", table_name="auth_rate_limit_entries")
    op.drop_index("ix_auth_rate_limit_entries_id", table_name="auth_rate_limit_entries")
    op.drop_table("auth_rate_limit_entries")