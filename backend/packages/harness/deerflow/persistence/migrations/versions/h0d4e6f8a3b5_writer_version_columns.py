"""writer_version_columns

Revision ID: h0d4e6f8a3b5
Revises: g9c3d5e7f2a4
Create Date: 2026-08-10 16:00:00.000000

Adds:
- writer_docs.version (Integer, nullable for SQLite ADD COLUMN, backfilled to 0)
- writer_doc_versions.doc_type (String(20), nullable, NOT backfilled --
  old version rows have unknown type; backfilling 'general' would cause
  restore to revert an article back to general type)
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from deerflow.persistence.migrations._helpers import _inspector, safe_add_column, safe_drop_column

# revision identifiers, used by Alembic.
revision: str = "h0d4e6f8a3b5"
down_revision: str | None = "g9c3d5e7f2a4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # version column: nullable on add (SQLite can't ADD COLUMN NOT NULL on
    # non-empty tables), backfilled to 0. New DBs get NOT NULL via create_all
    # (model defines nullable=False, default=0).
    safe_add_column("writer_docs", sa.Column("version", sa.Integer(), nullable=True))
    # 2026-09-14 基线债修复：老形态 legacy 库（仅 unified_tasks，无 writer 表）经
    # legacy 分支 stamp 跳过建表链后，裸 UPDATE 会以 no such table 崩掉整个
    # bootstrap。守卫表存在才回填——与 safe_add_column 的幂等哲学一致
    # （table 存在时行为不变：列刚加/已加，回填 0）。
    if _inspector().has_table("writer_docs"):
        op.execute("UPDATE writer_docs SET version = 0 WHERE version IS NULL")

    # doc_type snapshot column: nullable, NOT backfilled. Old version rows
    # have unknown type -- restore must NOT revert an article to general.
    safe_add_column("writer_doc_versions", sa.Column("doc_type", sa.String(20), nullable=True))


def downgrade() -> None:
    safe_drop_column("writer_doc_versions", "doc_type")
    safe_drop_column("writer_docs", "version")
