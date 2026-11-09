"""fix_seedance_mini_official_price (2026-09-01 全模型计价审计)

``seedance/bytedance-doubao-seedance-2-0-mini``（模型级行）单价修正：
per_kilo_token **0.0092** → **0.023**。

审计发现（2026-09-01 全模型计价核对）：0.0092 来源不明，且与字节官方公开
价相差 60%——[财联社 2026-06-16](https://www.cls.cn/detail/2400809)：
Seedance 2.0 Mini **图生视频 0.023 元/千 tokens**、视频生视频 0.014 元/千
tokens。本项目用法全为图生视频（i2v 首帧 / r2v 参考图），取图生视频档
0.023。用户拍板原则（2026-09-01）：「360 价与字节官方相差特别远时，按字节
公开市场价计价」。

幂等：按 ``(provider='seedance', model_id='bytedance/doubao-seedance-2-0-mini',
media_type='video')`` 键 UPDATE→无行则 INSERT（模型级行形态与其他 seedance
分键一致）；其他行不触碰。回滚恢复 0.0092（历史值，仅留还原能力）。

Revision ID: n7b8c9d0e1f2
Revises: m6a7b8c9d0e1
Create Date: 2026-09-01 23:30:00.000000
"""

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "n7b8c9d0e1f2"
down_revision: str | None = "m6a7b8c9d0e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MINI_MODEL_ID = "bytedance/doubao-seedance-2-0-mini"
_ACTOR = "mini-official-0.023(estimated)"
_ROLLBACK_PRICE = 0.0092


def apply_seedance_mini_official_price(conn: sa.Connection) -> None:
    """mini 官方价本体——alembic ``upgrade()`` 与单测共用一条路径（幂等）。"""
    now = datetime.now(UTC)
    result = conn.execute(
        sa.text("UPDATE media_pricing SET unit_price = 0.023, updated_at = :now, updated_by = :actor WHERE provider = 'seedance' AND model_id = :mid AND media_type = 'video'"),
        {"now": now, "actor": _ACTOR, "mid": _MINI_MODEL_ID},
    )
    if result.rowcount == 0:
        conn.execute(
            sa.text("INSERT INTO media_pricing (provider, model_id, media_type, unit, unit_price, currency, updated_at, updated_by) VALUES ('seedance', :mid, 'video', 'per_kilo_token', 0.023, 'CNY', :now, :actor)"),
            {"now": now, "actor": _ACTOR, "mid": _MINI_MODEL_ID},
        )


def rollback_seedance_mini_official_price(conn: sa.Connection) -> None:
    """回滚：恢复历史值（行不在则 no-op）。"""
    conn.execute(
        sa.text("UPDATE media_pricing SET unit_price = :price, updated_at = :now, updated_by = 'mini-rollback' WHERE provider = 'seedance' AND model_id = :mid AND media_type = 'video'"),
        {"price": _ROLLBACK_PRICE, "now": datetime.now(UTC), "mid": _MINI_MODEL_ID},
    )


def upgrade() -> None:
    apply_seedance_mini_official_price(op.get_bind())


def downgrade() -> None:
    rollback_seedance_mini_official_price(op.get_bind())
