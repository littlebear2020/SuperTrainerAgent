"""drop 5 orphan tables: budget_ledger, model_prices, usage_snapshots, user_balances, voice_production_tasks

Revision ID: e5f6a7b8c9d0
Revises: d9e0f1a2b3c4
Create Date: 2026-08-03 00:01:00.000000

These five tables have zero ORM references and zero application code references.
The first four have 0 rows; voice_production_tasks has 4 rows of stale data from
a feature that was superseded by unified_tasks (see revision c48befbbe3b7).

All DROP operations are guarded with existence checks so this revision is safe
to run against a fresh DB that never had these tables (e.g. the empty branch
of bootstrap_schema which runs create_all + stamp head, skipping all revisions).

checkpoints / writes are LangGraph-owned and must NOT be touched.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from deerflow.persistence.migrations._helpers import safe_drop_table

revision: str = "e5f6a7b8c9d0"
down_revision: str | None = "d9e0f1a2b3c4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Drop indexes before tables (SQLite batch mode handles this automatically,
    # but explicit drops are clearer and work on both SQLite and Postgres).
    conn = op.get_bind()
    insp = sa.inspect(conn)
    existing = set(insp.get_table_names())

    if "budget_ledger" in existing:
        for idx in ("ix_ledger_run", "ix_ledger_status", "ix_ledger_user"):
            existing_idx = {i["name"] for i in insp.get_indexes("budget_ledger")}
            if idx in existing_idx:
                op.drop_index(idx, table_name="budget_ledger")
        op.drop_table("budget_ledger")

    safe_drop_table("model_prices")
    safe_drop_table("usage_snapshots")
    safe_drop_table("user_balances")

    if "voice_production_tasks" in existing:
        for idx in ("ix_voice_production_tasks_claimed_by", "ix_voice_production_tasks_user_id"):
            existing_idx = {i["name"] for i in insp.get_indexes("voice_production_tasks")}
            if idx in existing_idx:
                op.drop_index(idx, table_name="voice_production_tasks")
        op.drop_table("voice_production_tasks")


def downgrade() -> None:
    # Restore the tables for rollback. voice_production_tasks is restored
    # without the 4 stale rows (data loss is acceptable for a superseded table).
    op.create_table(
        "voice_production_tasks",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("task_type", sa.String(32), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("voice_id", sa.String(64), nullable=False),
        sa.Column("voice_name", sa.String(100), nullable=False),
        sa.Column("options", sa.String(), server_default="{}"),
        sa.Column("input_path", sa.String(512)),
        sa.Column("output_path", sa.String(512)),
        sa.Column("progress", sa.Integer(), server_default="0"),
        sa.Column("slide_count", sa.Integer()),
        sa.Column("error_message", sa.String(500)),
        sa.Column("file_size", sa.Integer()),
        sa.Column("claimed_by", sa.String(64)),
        sa.Column("claimed_at", sa.DateTime()),
        sa.Column("lease_expires_at", sa.DateTime()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("ix_voice_production_tasks_claimed_by", "voice_production_tasks", ["claimed_by"])
    op.create_index("ix_voice_production_tasks_user_id", "voice_production_tasks", ["user_id"])

    op.create_table(
        "user_balances",
        sa.Column("user_id", sa.String(64), primary_key=True),
        sa.Column("balance", sa.Numeric(12, 4), nullable=False),
        sa.Column("total_granted", sa.Numeric(12, 4), nullable=False),
        sa.Column("total_consumed", sa.Numeric(12, 4), nullable=False),
        sa.Column("total_overdrawn", sa.Numeric(12, 4), nullable=False),
        sa.Column("overdraft_limit", sa.Numeric(10, 4), nullable=False),
        sa.Column("per_run_limit", sa.Numeric(10, 4)),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "usage_snapshots",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("period_type", sa.String(20), nullable=False),
        sa.Column("period_start", sa.DateTime(), nullable=False),
        sa.Column("period_end", sa.DateTime(), nullable=False),
        sa.Column("total_cost", sa.Numeric(10, 4), nullable=False),
        sa.Column("total_tokens", sa.BigInteger(), nullable=False),
        sa.Column("by_model", sa.JSON(), nullable=False),
        sa.Column("by_caller", sa.JSON(), nullable=False),
        sa.Column("run_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_snapshots_unique", "usage_snapshots", ["user_id", "period_type", "period_start"], unique=True)

    op.create_table(
        "model_prices",
        sa.Column("model_name", sa.String(128), primary_key=True),
        sa.Column("effective_from", sa.DateTime(), primary_key=True),
        sa.Column("display_name", sa.String(256)),
        sa.Column("input_price_per_million", sa.Numeric(10, 4), nullable=False),
        sa.Column("output_price_per_million", sa.Numeric(10, 4), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "budget_ledger",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("amount", sa.Numeric(12, 4), nullable=False),
        sa.Column("action_type", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("run_id", sa.String(64)),
        sa.Column("reason", sa.Text()),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("requested_by", sa.String(64)),
        sa.Column("processed_by", sa.String(64)),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("settled_at", sa.DateTime()),
    )
    op.create_index("ix_ledger_run", "budget_ledger", ["run_id"], unique=True)
    op.create_index("ix_ledger_status", "budget_ledger", ["status", "action_type"])
    op.create_index("ix_ledger_user", "budget_ledger", ["user_id", "created_at"])
