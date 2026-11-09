"""add_doc_type_to_writer_docs

Revision ID: f7a1b3c5d9e2
Revises: a1b2c3d4e5f6
Create Date: 2026-06-16 22:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f7a1b3c5d9e2"
down_revision: str | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "writer_docs",
        sa.Column("doc_type", sa.String(20), nullable=True),
    )
    op.execute("UPDATE writer_docs SET doc_type = 'general' WHERE doc_type IS NULL")


def downgrade() -> None:
    op.drop_column("writer_docs", "doc_type")
