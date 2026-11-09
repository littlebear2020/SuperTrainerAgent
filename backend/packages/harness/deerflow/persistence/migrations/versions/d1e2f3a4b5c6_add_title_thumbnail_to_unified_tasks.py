"""add title and thumbnail_url to unified_tasks

Revision ID: d1e2f3a4b5c6
Revises: c8d9e0f1a2b3
Create Date: 2026-08-07 00:00:00.000000

P2-4: 落库 title/thumbnail_url，避免每次列表请求都从 sidecar 文件读。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from deerflow.persistence.migrations._helpers import safe_add_column

revision: str = "d1e2f3a4b5c6"
down_revision: str | None = "c8d9e0f1a2b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    safe_add_column("unified_tasks", sa.Column("title", sa.String(500), nullable=True))
    safe_add_column("unified_tasks", sa.Column("thumbnail_url", sa.String(1024), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("unified_tasks") as batch_op:
        batch_op.drop_column("thumbnail_url")
        batch_op.drop_column("title")
