"""add_task_id_to_external_api_calls

批次 C Phase C (2026-08-13)：external_api_calls 加 task_id 列——每任务媒体成本
归因。写入点在 ``record_external_api_call`` 内读 accounting ctx
（``get_llm_accounting_context``）；音色克隆上传等任务 ctx 外的调用
task_id=None（正确行为）。

历史数据说明：迁移前存量行 task_id 为 NULL，历史媒体花费无法归因到任务
（前向有效，符合预期）——老任务只显示 LLM 成本是正常现象，不是 bug。

Revision ID: b3d4e5f6a7c8
Revises: i1e5f7a9b4c6
Create Date: 2026-08-13 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from deerflow.persistence.migrations._helpers import (
    safe_add_column,
    safe_create_index,
    safe_drop_column,
    safe_drop_index,
)

# revision identifiers, used by Alembic.
revision: str = "b3d4e5f6a7c8"
down_revision: str | None = "i1e5f7a9b4c6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    safe_add_column(
        "external_api_calls",
        sa.Column("task_id", sa.String(64), nullable=True),
    )
    safe_create_index("external_api_calls", "ix_external_api_calls_task_id", ["task_id"])


def downgrade() -> None:
    safe_drop_index("external_api_calls", "ix_external_api_calls_task_id")
    safe_drop_column("external_api_calls", "task_id")
