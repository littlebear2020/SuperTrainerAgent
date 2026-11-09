"""add_retry_count_to_unified_tasks

Revision ID: d4f8a9123b5e
Revises: e375427626f3
Create Date: 2026-06-08 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4f8a9123b5e"
down_revision: str | None = "e375427626f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("unified_tasks") as batch_op:
        batch_op.add_column(sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    with op.batch_alter_table("unified_tasks") as batch_op:
        batch_op.drop_column("retry_count")
