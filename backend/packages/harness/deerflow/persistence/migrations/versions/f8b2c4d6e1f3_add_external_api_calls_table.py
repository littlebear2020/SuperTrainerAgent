"""add_external_api_calls_table

Tracks per-call metrics for external pay-per-call APIs (ai_image, ai_video,
ViMax bridge, TTS). Counts only — no cost column yet (operators will supply
unit prices separately before cost is computable).

Revision ID: f8b2c4d6e1f3
Revises: d1e2f3a4b5c6
Create Date: 2026-08-08 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f8b2c4d6e1f3"
down_revision: str | None = "d1e2f3a4b5c6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if "external_api_calls" in sa.inspect(bind).get_table_names():
        return
    op.create_table(
        "external_api_calls",
        sa.Column("call_id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=True, index=True),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("method", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("elapsed_ms", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    # Column(index=True) on user_id IS honored here (op.create_table builds a
    # real Table whose DDL includes its indexes), but the __table_args__ Index
    # on created_at only exists in the ORM model — create it explicitly so
    # the deployed schema matches the model.
    op.create_index("ix_external_api_calls_created_at", "external_api_calls", ["created_at"])


def downgrade() -> None:
    op.drop_table("external_api_calls")
