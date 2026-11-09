"""add_writer_doc_shares

Revision ID: 4866c439a067
Revises: d4f8a9123b5e
Create Date: 2026-06-10 11:45:44.698051

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4866c439a067"
down_revision: str | None = "d4f8a9123b5e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "writer_doc_shares",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("doc_id", sa.String(length=64), nullable=False),
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column("share_token", sa.String(length=32), nullable=False),
        sa.Column("permission", sa.String(length=20), nullable=False, server_default="view"),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("writer_doc_shares", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_writer_doc_shares_doc_id"), ["doc_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_writer_doc_shares_share_token"), ["share_token"], unique=True)


def downgrade() -> None:
    with op.batch_alter_table("writer_doc_shares", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_writer_doc_shares_share_token"))
        batch_op.drop_index(batch_op.f("ix_writer_doc_shares_doc_id"))
    op.drop_table("writer_doc_shares")
