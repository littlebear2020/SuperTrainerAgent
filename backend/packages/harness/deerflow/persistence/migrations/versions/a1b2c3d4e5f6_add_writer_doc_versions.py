"""add_writer_doc_versions

Revision ID: a1b2c3d4e5f6
Revises: 4866c439a067
Create Date: 2026-06-10 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "4866c439a067"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "writer_doc_versions",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("doc_id", sa.String(length=64), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("content_json", sa.Text(), nullable=False, server_default=""),
        sa.Column("snapshot_reason", sa.String(length=50), nullable=False, server_default="auto"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("writer_doc_versions", schema=None) as batch_op:
        batch_op.create_index("ix_wdv_doc_version", ["doc_id", "version_number"], unique=False)
        batch_op.create_index(batch_op.f("ix_writer_doc_versions_doc_id"), ["doc_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("writer_doc_versions", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_writer_doc_versions_doc_id"))
        batch_op.drop_index("ix_wdv_doc_version")
    op.drop_table("writer_doc_versions")
