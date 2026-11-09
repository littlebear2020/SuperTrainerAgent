"""add llm_error_fallback to runs

Revision ID: c8d9e0f1a2b3
Revises: c7d8e9f0a1b2
Create Date: 2026-08-06 00:01:00.000000

P1-9 (batch C, commit 7b709ac4f) added a ``llm_error_fallback`` Boolean column
to ``RunRow`` so a run that ended only because the LLM-error middleware
substituted a fallback AIMessage is not journaled as success. The column
shipped *without* a revision, so存量库 on the legacy/versioned bootstrap
branches (which reach schema via ``alembic upgrade head``, not ``create_all``)
never received it. ``RunRepository.update_run_completion`` then raised
``OperationalError("no such column: llm_error_fallback")``, which RunManager
and the worker swallow — silently dropping token/status/stop_reason persistence
on every run completion. This revision closes that gap.

``safe_add_column`` keeps it idempotent on DBs that already have the column via
``create_all`` (empty branch) or a manual ALTER.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from deerflow.persistence.migrations._helpers import safe_add_column

revision: str = "c8d9e0f1a2b3"
down_revision: str | None = "c7d8e9f0a1b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    safe_add_column("runs", sa.Column("llm_error_fallback", sa.Boolean(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("runs") as batch_op:
        batch_op.drop_column("llm_error_fallback")
