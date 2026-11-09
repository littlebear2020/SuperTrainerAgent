"""add_metadata_json_to_external_api_calls

B1: 媒体 amount 全链路捕获——external_api_calls 加 metadata_json (JSON) 列
存放供应商返回的稀疏字段（如 ``amount`` 真账金额）。

Revision ID: g9c3d5e7f2a4
Revises: f8b2c4d6e1f3
Create Date: 2026-08-09 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from deerflow.persistence.migrations._helpers import safe_add_column

# revision identifiers, used by Alembic.
revision: str = "g9c3d5e7f2a4"
down_revision: str | None = "f8b2c4d6e1f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    safe_add_column(
        "external_api_calls",
        sa.Column("metadata_json", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("external_api_calls", "metadata_json")
