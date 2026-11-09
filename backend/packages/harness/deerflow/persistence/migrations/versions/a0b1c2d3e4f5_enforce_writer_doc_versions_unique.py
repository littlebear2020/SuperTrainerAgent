"""enforce UNIQUE(doc_id, version_number) on writer_doc_versions

Revision ID: a0b1c2d3e4f5
Revises: f6a7b8c9d0e1
Create Date: 2026-08-03 00:03:00.000000

The ORM model (models_version.py) declares:
    UniqueConstraint("doc_id", "version_number", name="uq_wdv_doc_version")

But migration a1b2c3d4e5f6 built the table without this constraint -- it only
created a non-unique index ix_wdv_doc_version. The ensure_writer_versions_unique
startup patch compensated at runtime; that patch was retired in batch-3 when
bootstrap_schema took over schema management. This revision permanently enforces
the constraint via alembic so no runtime patch is needed.

Steps:
1. Deduplicate: if any (doc_id, version_number) pair has duplicates, renumber
   version_number within each doc_id by created_at order (same logic as the
   retired patch).
2. Add the unique index via safe_create_index (no-op if already present, e.g.
   on a fresh DB built from create_all which gets the constraint from the ORM).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from deerflow.persistence.migrations._helpers import safe_create_index

revision: str = "a0b1c2d3e4f5"
down_revision: str | None = "f6a7b8c9d0e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()

    # Legacy DBs may not have this table at all (it is created by a1b2c3d4e5f6,
    # which the legacy branch stamps over rather than replays). The dedup query
    # below would raise "no such table", so bail out early -- a DB without the
    # table cannot have duplicate rows, and create_all will build it with the
    # ORM's UniqueConstraint already in place.
    if "writer_doc_versions" not in sa.inspect(conn).get_table_names():
        return

    # Step 1: deduplicate if needed.
    dups = conn.execute(sa.text("SELECT doc_id, version_number, COUNT(*) c FROM writer_doc_versions GROUP BY doc_id, version_number HAVING c > 1")).fetchall()

    if dups:
        # Renumber version_number within each doc_id by created_at ASC, id ASC.
        conn.execute(
            sa.text(
                """
                UPDATE writer_doc_versions
                SET version_number = (
                    SELECT COUNT(*) FROM writer_doc_versions v2
                    WHERE v2.doc_id = writer_doc_versions.doc_id
                      AND (v2.created_at < writer_doc_versions.created_at
                           OR (v2.created_at = writer_doc_versions.created_at
                               AND v2.id <= writer_doc_versions.id))
                )
                """
            )
        )

    # Step 2: add the unique index (no-op if already present).
    safe_create_index(
        "writer_doc_versions",
        "uq_wdv_doc_version",
        ["doc_id", "version_number"],
        unique=True,
    )


def downgrade() -> None:
    with op.batch_alter_table("writer_doc_versions") as batch_op:
        batch_op.drop_index("uq_wdv_doc_version")
