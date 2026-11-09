"""add_media_pricing_table

C2: Media pricing table for operator-editable unit prices.
PK = (provider, media_type) composite key — same provider can serve different
media types at different rates (e.g. qihu image vs video).

Revision ID: i1e5f7a9b4c6
Revises: h0d4e6f8a3b5
Create Date: 2026-08-12 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "i1e5f7a9b4c6"
down_revision: str | None = "h0d4e6f8a3b5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "media_pricing",
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("media_type", sa.String(16), nullable=False),
        sa.Column("unit", sa.String(16), nullable=False),
        sa.Column("unit_price", sa.Float, nullable=False, server_default="0"),
        sa.Column("currency", sa.String(8), nullable=False, server_default="CNY"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_by", sa.String(64), nullable=False, server_default=""),
        sa.PrimaryKeyConstraint("provider", "media_type"),
    )


def downgrade() -> None:
    op.drop_table("media_pricing")
