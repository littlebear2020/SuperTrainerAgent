"""add_model_pricing_table

Operator-editable per-model prices for the console's spend figures. Kept in
the DB rather than ``config.yaml`` because that file is mounted read-only in
the deployment, so editing it from an admin UI would require a compose change.

``input_cache_hit_per_million`` is nullable on purpose: ``null`` means "no hit
price configured, bill cache hits at the full miss price", which is a
different statement from ``0`` ("hits are free").

Revision ID: c7d8e9f0a1b2
Revises: b2c3d4e5f6a7
Create Date: 2026-08-04 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c7d8e9f0a1b2"
down_revision: str | None = "b2c3d4e5f6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # P0-5: stamp 点前移后本 migration 会在「表已存在」的库上重放，故须幂等。
    bind = op.get_bind()
    if "model_pricing" in sa.inspect(bind).get_table_names():
        return
    op.create_table(
        "model_pricing",
        sa.Column("model_name", sa.String(200), primary_key=True),
        sa.Column("currency", sa.String(8), nullable=False, server_default="CNY"),
        sa.Column("input_per_million", sa.Float(), nullable=False, server_default="0"),
        sa.Column("output_per_million", sa.Float(), nullable=False, server_default="0"),
        # Nullable: null = "bill hits at the miss price", 0 = "hits are free".
        sa.Column("input_cache_hit_per_million", sa.Float(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("updated_by", sa.String(64), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_table("model_pricing")
