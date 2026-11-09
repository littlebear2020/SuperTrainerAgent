"""add_claim_lease_fields_to_production_task

Revision ID: a37e7c2b866e
Revises:
Create Date: 2026-06-07 11:07:39.063561

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a37e7c2b866e"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Guard: on a fresh DB this table does not exist (it was dropped by the
    # next revision c48befbbe3b7). Skip silently so the chain is replayable
    # from scratch without seeding voice_production_tasks first.
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if "voice_production_tasks" not in inspector.get_table_names():
        return
    with op.batch_alter_table("voice_production_tasks") as batch_op:
        batch_op.add_column(sa.Column("claimed_by", sa.String(64), nullable=True))
        batch_op.add_column(sa.Column("claimed_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("lease_expires_at", sa.DateTime(), nullable=True))
        batch_op.create_index("ix_voice_production_tasks_claimed_by", ["claimed_by"])


def downgrade() -> None:
    with op.batch_alter_table("voice_production_tasks") as batch_op:
        batch_op.drop_index("ix_voice_production_tasks_claimed_by")
        batch_op.drop_column("lease_expires_at")
        batch_op.drop_column("claimed_at")
        batch_op.drop_column("claimed_by")
