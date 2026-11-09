"""add_dirty_frames_to_unified_tasks

Revision ID: c0d1e2f3a4b5
Revises: f7a1b3c5d9e2
Create Date: 2026-07-03 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from deerflow.persistence.migrations._helpers import safe_add_column

# revision identifiers, used by Alembic.
revision: str = "c0d1e2f3a4b5"
down_revision: str | None = "f7a1b3c5d9e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # P0-5: stamp 点前移后本 migration 会在「表已含该列」的库上重放，故须幂等。
    safe_add_column("unified_tasks", sa.Column("dirty_frames", sa.Text(), nullable=False, server_default="[]"))


def downgrade() -> None:
    with op.batch_alter_table("unified_tasks") as batch_op:
        batch_op.drop_column("dirty_frames")
