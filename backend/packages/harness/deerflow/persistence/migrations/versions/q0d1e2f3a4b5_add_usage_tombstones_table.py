"""add_usage_tombstones_table

删除作品/会话前写入的用量快照（类型、标题、状态），让「我的用量」在原对象删除后
继续计入已发生的费用与 Token（账本本身分不出类型）。只记录上线后的删除，不回填。

Revision ID: q0d1e2f3a4b5
Revises: p9c0d1e2f3a4
Create Date: 2026-11-26 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "q0d1e2f3a4b5"
down_revision: str | None = "p9c0d1e2f3a4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if "usage_tombstones" in sa.inspect(bind).get_table_names():
        return
    op.create_table(
        "usage_tombstones",
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("subject_id", sa.String(64), nullable=False),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("capability", sa.String(32), nullable=False),
        sa.Column("title", sa.String(256), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("subject_created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("kind", "subject_id"),
    )
    op.create_index("ix_usage_tombstones_user_id", "usage_tombstones", ["user_id"])


def downgrade() -> None:
    op.drop_table("usage_tombstones")
