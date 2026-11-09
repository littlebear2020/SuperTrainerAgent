"""add empty_fallback_count to runs

Revision ID: p9c0d1e2f3a4
Revises: o8b9c0d1e2f3
Create Date: 2026-09-23 00:00:00.000000

2026-09-23 批 1 A4（OBS-02 空响应治理）：TerminalResponseMiddleware 在空终态
兜底时把 ``empty_fallback_count`` 写入 ``runtime.context``，run worker 经
``update_run_completion(empty_fallback_count=...)`` 落到 run 行，供观察期
SQL 周报（日均兜底次数 + 重试恢复率 KPI）直查。

与 ``o8b9c0d1e2f3``（slow_llm_call_count）同一链路与同一语义：worker 只在
计数非零时传 kwarg，NULL = "无兜底发生或早于本列的行"（两种状态刻意合并），
正整数恒表示兜底发生过。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from deerflow.persistence.migrations._helpers import safe_add_column

revision: str = "p9c0d1e2f3a4"
down_revision: str | None = "o8b9c0d1e2f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    safe_add_column("runs", sa.Column("empty_fallback_count", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("runs") as batch_op:
        batch_op.drop_column("empty_fallback_count")
