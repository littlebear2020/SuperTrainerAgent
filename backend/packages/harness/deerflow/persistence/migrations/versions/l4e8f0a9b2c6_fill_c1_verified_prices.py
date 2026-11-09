"""fill_c1_verified_prices (C1, 2026-08-27 成本可信化批次)

五项经"官网价目 → 实测"两级核实并由用户拍板的单价落库（探针原始数据见
``temp/c1_probe_results/RESULTS.md``，五项付费调用合计 ≈¥2.4）：

1. ``gpt-image-2/image`` → per_kilo_token **0.204**（estimated）——360 表价
   输入 ¥34 / 输出 ¥204 每 1M tokens，取输出率；实测 212 tokens/张 ≈ ¥0.0432
   与真值 ¥0.0405 偏差 ≤2%。计价维度由 per_image 改 token（实测响应带 usage，
   throttle 落 ``total_tokens`` 可估）。
2. ``seedream-360/image`` → per_image **0.5**（estimated，**A18 用户拍板**）——
   360 表价折算 ≈¥0.0035/张，远低于 Ark 控制台 ¥0.30~0.60/张；用户规则：
   360 远低于 Ark 取 Ark 价、360 高于 Ark 取 360 价（取高价侧防低估），
   故按 Ark 中值 0.5 落；单位随之落 per_image。
3. ``seedream/image``（Ark 直连，vimax 主力生图）→ per_image **0.5**
   （estimated）——Ark 控制台 ¥0.30~0.60/张取中值；vimax 桥 metadata 不写
   tokens（provider_bridge.py:365），per_kilo_token 在该链估不出。
4. ``doubao_tts/tts`` → per_kilo_char **0.12**（estimated，**A19 用户拍板**）——
   用户提供的火山控制台截图（Doubao-语音播客：输入-文本 120 元/百万 Token、
   输出-音频 100 元/百万 Token），客户端模型 seed-tts-2.0；SSE 响应无 usage
   面（token 不可得），按输入侧 1 字≈1 token 保守折算为千字价，维持字数口径。
5. ``minimax-h3/video`` → per_kilo_token **0.0008**（estimated）——360 表价
   ¥0.8/1M tokens；H3 轮询 usage 面未实测，无 tokens 的行落 unpriced 计数可见。

``dashscope`` **不落行**：全库无在册调用（step 0 值域探针），落 0 价行会把
未来调用算成 ¥0 假账——宁落 unpriced 计数可见。``qihu_tts`` 维持种子 0.3 不动。
``happyhorse`` 弃用（A3，C0 已删行）。

幂等：按 ``(provider, model_id IS NULL, media_type)`` 键 UPDATE→无行则 INSERT，
重放同值无害。回滚：三个可预存键恢复迁移前值，两个新增 provider 行删除。

Revision ID: l4e8f0a9b2c6
Revises: k3d7f9e8a1b5
Create Date: 2026-08-27 15:00:00.000000
"""

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "l4e8f0a9b2c6"
down_revision: str | None = "k3d7f9e8a1b5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: (provider, media_type, unit, unit_price, updated_by 标记)——标记含来源与
#: estimated 语义（价目表无 source 列，以 updated_by 承载 C1 的 real/estimated 承诺）。
_C1_ROWS: list[tuple[str, str, str, float, str]] = [
    ("gpt-image-2", "image", "per_kilo_token", 0.204, "c1-probe(estimated)"),
    ("seedream-360", "image", "per_image", 0.5, "c1-user-A18(estimated)"),
    ("seedream", "image", "per_image", 0.5, "c1-probe(estimated)"),
    ("doubao_tts", "tts", "per_kilo_char", 0.12, "c1-user-A19(estimated)"),
    ("minimax-h3", "video", "per_kilo_token", 0.0008, "c1-360-pricelist(estimated)"),
]

#: 回滚：可预存键的迁移前值（种子时代旧值）。
_ROLLBACK_ROWS: list[tuple[str, str, str, float]] = [
    ("gpt-image-2", "image", "per_image", 0.04),
    ("seedream", "image", "per_image", 0.22),
    ("doubao_tts", "tts", "per_kilo_char", 0.5),
]
#: 回滚：C1 新增的两个 provider 行（迁移前不存在）直接删除。
_ROLLBACK_DELETE_PROVIDERS = ("seedream-360", "minimax-h3")


def apply_c1_prices(conn: sa.Connection) -> None:
    """C1 填价本体——alembic ``upgrade()`` 与单测共用这一条路径（幂等）。"""
    now = datetime.now(UTC)
    for provider, media_type, unit, price, actor in _C1_ROWS:
        result = conn.execute(
            sa.text("UPDATE media_pricing SET unit = :unit, unit_price = :price, updated_at = :now, updated_by = :actor WHERE provider = :provider AND media_type = :media_type AND model_id IS NULL"),
            {"unit": unit, "price": price, "now": now, "actor": actor, "provider": provider, "media_type": media_type},
        )
        if result.rowcount == 0:
            conn.execute(
                sa.text("INSERT INTO media_pricing (provider, model_id, media_type, unit, unit_price, currency, updated_at, updated_by) VALUES (:provider, NULL, :media_type, :unit, :price, 'CNY', :now, :actor)"),
                {"provider": provider, "media_type": media_type, "unit": unit, "price": price, "now": now, "actor": actor},
            )


def rollback_c1_prices(conn: sa.Connection) -> None:
    """回滚：可预存键恢复迁移前值（行不在则 no-op），C1 新增 provider 行删除。"""
    now = datetime.now(UTC)
    for provider, media_type, unit, price in _ROLLBACK_ROWS:
        conn.execute(
            sa.text("UPDATE media_pricing SET unit = :unit, unit_price = :price, updated_at = :now, updated_by = 'c1-rollback' WHERE provider = :provider AND media_type = :media_type AND model_id IS NULL"),
            {"unit": unit, "price": price, "now": now, "provider": provider, "media_type": media_type},
        )
    conn.execute(
        sa.text("DELETE FROM media_pricing WHERE provider IN :providers AND model_id IS NULL").bindparams(sa.bindparam("providers", expanding=True)),
        {"providers": list(_ROLLBACK_DELETE_PROVIDERS)},
    )


def upgrade() -> None:
    apply_c1_prices(op.get_bind())


def downgrade() -> None:
    rollback_c1_prices(op.get_bind())
