"""fix_h3_video_per_second_pricing (2026-09-01 拆帧/计价计划)

``minimax-h3/video`` 单位修正：per_kilo_token **0.0008** → per_second **0.58**。

C1（l4e8f0a9b2c6）落库的千 token 价是 360 模型广场**文本**表价（¥0.8/1M
tokens）误配到视频模型——MiniMax 官方视频按**时长**计价（768P $0.08/秒 ≈
¥0.58/秒 @7.2，[atlascloud 实测](https://www.atlascloud.ai/zh/blog/guides/
minimax-h3-api-pricing)），且 H3 轮询响应无 usage/tokens 面可记——
per_kilo_token 永远估不出：vg_22ee06b39d19 的 68/68 行 ``generate_video``
无 total_tokens → unpriced 静默不计，~510s 视频分量 ≈¥296 整体漏记（账单
仅 ¥47.33，生图 ¥44 + TTS ¥0.4 + LLM ¥2.9）。

``_estimate_call_cost`` 的 per_second 分支（duration × price）已存在，H3
记账行 metadata 携带 duration（请求回声）——单位切换后立即可估，零估算器
改动。**duration 口径标注**：请求回声而非实测产出（H3 请求 4s 实产 4.46s
量级），估算偏差可接受；存量 ¥0 行不回填（历史数据修复另议）。

幂等：按 ``(provider='minimax-h3', model_id IS NULL, media_type='video')``
键 UPDATE→无行则 INSERT，重放同值无害；模型级行不触碰。回滚恢复 C1 值。

Revision ID: m6a7b8c9d0e1
Revises: l4e8f0a9b2c6
Create Date: 2026-09-01 23:00:00.000000
"""

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "m6a7b8c9d0e1"
down_revision: str | None = "l4e8f0a9b2c6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: updated_by 承载来源语义（价目表无 source 列，沿 C1 惯例）。
_H3_ACTOR = "h3-official-768p(estimated)"
#: 回滚目标：C1 落库的 360 文本表价误配值（历史事实，回滚即还原它）。
_H3_ROLLBACK = ("per_kilo_token", 0.0008)


def apply_h3_per_second(conn: sa.Connection) -> None:
    """H3 按秒计价本体——alembic ``upgrade()`` 与单测共用这一条路径（幂等）。"""
    now = datetime.now(UTC)
    result = conn.execute(
        sa.text("UPDATE media_pricing SET unit = 'per_second', unit_price = 0.58, updated_at = :now, updated_by = :actor WHERE provider = 'minimax-h3' AND media_type = 'video' AND model_id IS NULL"),
        {"now": now, "actor": _H3_ACTOR},
    )
    if result.rowcount == 0:
        conn.execute(
            sa.text("INSERT INTO media_pricing (provider, model_id, media_type, unit, unit_price, currency, updated_at, updated_by) VALUES ('minimax-h3', NULL, 'video', 'per_second', 0.58, 'CNY', :now, :actor)"),
            {"now": now, "actor": _H3_ACTOR},
        )


def rollback_h3_per_second(conn: sa.Connection) -> None:
    """回滚：恢复 C1 值（行不在则 no-op——C1 未跑过的库回滚无事可做）。"""
    unit, price = _H3_ROLLBACK
    conn.execute(
        sa.text("UPDATE media_pricing SET unit = :unit, unit_price = :price, updated_at = :now, updated_by = 'h3-rollback' WHERE provider = 'minimax-h3' AND media_type = 'video' AND model_id IS NULL"),
        {"unit": unit, "price": price, "now": datetime.now(UTC)},
    )


def upgrade() -> None:
    apply_h3_per_second(op.get_bind())


def downgrade() -> None:
    rollback_h3_per_second(op.get_bind())
