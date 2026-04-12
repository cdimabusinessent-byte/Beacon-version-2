"""Scope execution requests by account.

Revision ID: 20260408_000004
Revises: 20260406_000003
Create Date: 2026-04-08 00:00:04
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260408_000004"
down_revision = "20260406_000003"
branch_labels = None
depends_on = None


def _table_exists(inspector: sa.Inspector, table_name: str) -> bool:
    return table_name in set(inspector.get_table_names())


def _column_names(inspector: sa.Inspector, table_name: str) -> set[str]:
    return {column["name"] for column in inspector.get_columns(table_name)}


def _index_names(inspector: sa.Inspector, table_name: str) -> set[str]:
    return {index["name"] for index in inspector.get_indexes(table_name) if index.get("name")}


def _unique_names(inspector: sa.Inspector, table_name: str) -> set[str]:
    return {constraint["name"] for constraint in inspector.get_unique_constraints(table_name) if constraint.get("name")}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _table_exists(inspector, "execution_requests"):
        return

    columns = _column_names(inspector, "execution_requests")
    if "account_scope" not in columns:
        op.add_column(
            "execution_requests",
            sa.Column("account_scope", sa.String(length=160), nullable=False, server_default="default"),
        )

    inspector = sa.inspect(bind)
    unique_names = _unique_names(inspector, "execution_requests")
    index_names = _index_names(inspector, "execution_requests")

    with op.batch_alter_table("execution_requests") as batch_op:
        if "uq_execution_requests_idempotency_key" in unique_names:
            batch_op.drop_constraint("uq_execution_requests_idempotency_key", type_="unique")
        if "uq_execution_requests_account_scope_idempotency_key" not in unique_names:
            batch_op.create_unique_constraint(
                "uq_execution_requests_account_scope_idempotency_key",
                ["account_scope", "idempotency_key"],
            )
        if "ix_execution_requests_account_scope" not in index_names:
            batch_op.create_index("ix_execution_requests_account_scope", ["account_scope"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _table_exists(inspector, "execution_requests"):
        return

    columns = _column_names(inspector, "execution_requests")
    unique_names = _unique_names(inspector, "execution_requests")
    index_names = _index_names(inspector, "execution_requests")

    with op.batch_alter_table("execution_requests") as batch_op:
        if "uq_execution_requests_account_scope_idempotency_key" in unique_names:
            batch_op.drop_constraint("uq_execution_requests_account_scope_idempotency_key", type_="unique")
        if "uq_execution_requests_idempotency_key" not in unique_names:
            batch_op.create_unique_constraint("uq_execution_requests_idempotency_key", ["idempotency_key"])
        if "ix_execution_requests_account_scope" in index_names:
            batch_op.drop_index("ix_execution_requests_account_scope")
        if "account_scope" in columns:
            batch_op.drop_column("account_scope")