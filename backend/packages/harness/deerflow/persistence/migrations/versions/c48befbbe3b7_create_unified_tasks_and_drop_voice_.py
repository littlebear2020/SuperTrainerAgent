"""create_unified_tasks_and_drop_voice_production

Revision ID: c48befbbe3b7
Revises: a37e7c2b866e
Create Date: 2026-06-07 21:55:04.806833

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c48befbbe3b7"
down_revision: str | None = "a37e7c2b866e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "unified_tasks",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=False, index=True),
        sa.Column("domain", sa.String(32), nullable=False),
        sa.Column("task_type", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("progress", sa.Integer(), server_default="0"),
        sa.Column("priority", sa.Integer(), server_default="50"),
        sa.Column("payload", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("result", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("error_message", sa.String(500)),
        sa.Column("claimed_by", sa.String(64)),
        sa.Column("claimed_at", sa.DateTime(timezone=True)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index(
        "ix_unified_tasks_user_domain_created",
        "unified_tasks",
        ["user_id", "domain", "created_at"],
    )
    op.create_index(
        "ix_unified_tasks_claim",
        "unified_tasks",
        ["status", "claimed_by", "domain"],
    )

    # Drop old voice_production_tasks table (feature not yet launched, no production data).
    # Guard: in fresh installs the table may not exist.
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if "voice_production_tasks" in inspector.get_table_names():
        op.drop_index("ix_voice_production_tasks_claimed_by", table_name="voice_production_tasks")
        op.drop_table("voice_production_tasks")


def downgrade() -> None:
    # Re-create voice_production_tasks
    op.create_table(
        "voice_production_tasks",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("task_type", sa.String(32), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("voice_id", sa.String(64), nullable=False),
        sa.Column("voice_name", sa.String(100), nullable=False),
        sa.Column("options", sa.String(), server_default="{}"),
        sa.Column("input_path", sa.String(512)),
        sa.Column("output_path", sa.String(512)),
        sa.Column("progress", sa.Integer(), server_default="0"),
        sa.Column("slide_count", sa.Integer()),
        sa.Column("error_message", sa.String(500)),
        sa.Column("file_size", sa.Integer()),
        sa.Column("claimed_by", sa.String(64)),
        sa.Column("claimed_at", sa.DateTime()),
        sa.Column("lease_expires_at", sa.DateTime()),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index(
        "ix_voice_production_tasks_claimed_by",
        "voice_production_tasks",
        ["claimed_by"],
    )
    op.create_index(
        "ix_voice_production_tasks_user_id",
        "voice_production_tasks",
        ["user_id"],
    )

    # Drop unified_tasks
    op.drop_index("ix_unified_tasks_claim", table_name="unified_tasks")
    op.drop_index("ix_unified_tasks_user_domain_created", table_name="unified_tasks")
    op.drop_table("unified_tasks")
