"""add operation_kind to runs

Revision ID: b2c3d4e5f6a7
Revises: a0b1c2d3e4f5
Create Date: 2026-08-03 20:10:00.000000

The operations console (T21) reports on *agent runs*, not on the bookkeeping
rows a thread may acquire for non-run work (checkpoint writes, artifact
writes).  Upstream discriminates the two with ``runs.operation_kind`` and
filters every console query on ``operation_kind == "run"``; this revision adds
the column so that filter is meaningful here too.

``server_default='run'`` backfills every historical row as a real run, which
is accurate: nothing in this deployment has ever created a ``runs`` row for
anything else.

NOT NULL + server default is safe on a non-empty SQLite table (unlike a bare
NOT NULL add) because the default supplies a value for existing rows. This is
the shape the ``ensure_*_schema`` patches could not use, and the reason they
left nullable columns behind — see ``_helpers.safe_add_column``'s drift note.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from deerflow.persistence.migrations._helpers import safe_add_column, safe_drop_column

revision: str = "b2c3d4e5f6a7"
down_revision: str | None = "a0b1c2d3e4f5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    safe_add_column(
        "runs",
        sa.Column("operation_kind", sa.String(length=32), nullable=False, server_default=sa.text("'run'")),
    )


def downgrade() -> None:
    safe_drop_column("runs", "operation_kind")
