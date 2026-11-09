"""add slow_llm_call_count to runs

Revision ID: o8b9c0d1e2f3
Revises: n7b8c9d0e1f2
Create Date: 2026-09-22 00:00:00.000000

W6 slow-LLM-call observability: SlowLLMCallMiddleware stamps the number of
over-threshold LLM calls into ``runtime.context`` and the run worker persists
it via ``update_run_completion(slow_llm_call_count=...)``. The column must exist
before the worker passes the kwarg, or the SQL store raises and the RunManager
swallow drops ALL completion persistence on every run — the exact failure mode
``c8d9e0f1a2b3`` (llm_error_fallback) had to repair.

``safe_add_column`` keeps it idempotent on DBs that already have the column via
``create_all`` (empty branch) or a manual ALTER. Semantics note: the worker only
writes the kwarg when the count is non-zero (``if slow_llm_call_count:``), so
NULL means "no slow calls observed OR run predates tracking" — the two states
are deliberately conflated; a positive integer always means slow calls occurred.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from deerflow.persistence.migrations._helpers import safe_add_column

revision: str = "o8b9c0d1e2f3"
down_revision: str | None = "n7b8c9d0e1f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    safe_add_column("runs", sa.Column("slow_llm_call_count", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("runs") as batch_op:
        batch_op.drop_column("slow_llm_call_count")
