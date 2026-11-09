"""add stop_reason and token_usage_by_model to runs

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-08-03 00:02:00.000000

stop_reason: records why a run ended (safety_filter / model_length / normal / etc.)
  Needed to detect truncated tool_calls from 豆包/360 safety gateways before
  they are dispatched and corrupt files (T10 prerequisite).

token_usage_by_model: JSON map of {model_name: {input, output}} for per-model
  cost attribution in the operations console (T21 prerequisite).

Both columns use safe_add_column so this revision is idempotent on DBs that
already have the columns from a manual ALTER (the dev DB does not, but future
deployments may).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from deerflow.persistence.migrations._helpers import safe_add_column

revision: str = "f6a7b8c9d0e1"
down_revision: str | None = "e5f6a7b8c9d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    safe_add_column("runs", sa.Column("stop_reason", sa.String(length=32), nullable=True))
    safe_add_column(
        "runs",
        sa.Column("token_usage_by_model", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )


def downgrade() -> None:
    with op.batch_alter_table("runs") as batch_op:
        batch_op.drop_column("token_usage_by_model")
        batch_op.drop_column("stop_reason")
