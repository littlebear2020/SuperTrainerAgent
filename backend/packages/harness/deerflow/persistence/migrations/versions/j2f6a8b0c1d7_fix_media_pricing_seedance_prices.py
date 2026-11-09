"""fix_media_pricing_seedance_prices (C0 止血, 2026-08-27 成本可信化批次)

存量 ``media_pricing`` 行的单价修正（读时重算——迁移执行后全部任务成本显示
立即回归合理，vg_b9fab64bfa84 ¥10,949 → ~¥440）：

1. 先把现值整体快照进 ``media_pricing_bak`` 备份表（回滚依据，A9）；
2. ``seedance-ark/video`` → per_kilo_token **0.046**（Ark 官网 2026-08-26 截图，
   46 元/百万 tokens，不含视频输入档——我们的 i2v 参考图属此档）；
3. ``seedance/video`` → per_kilo_token 0.046（provider 级默认按 2-0，A2 用户拍板；
   fast/mini 型号分键随 C2 模型级化落地）；
4. ``happyhorse`` 弃用（A3 用户拍板：删除行，备份已留）。

连锁（v4.2 A13 用户拍板）：seedance 行单位切到 per_kilo_token 后，ai_video 闸的
查价逻辑（visual_factory._resolve_unit_cost 只认 per_image/per_second）回落兜底
常量——金额闸默认已关闭（ai_visual_budget_cny 代码默认 None，次数闸为唯一硬顶），
对默认部署的拦截行为零影响。禁止手工直接改生产库（本迁移随 alembic 自动执行）。

Revision ID: j2f6a8b0c1d7
Revises: b3d4e5f6a7c8
Create Date: 2026-08-27 12:00:00.000000
"""

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "j2f6a8b0c1d7"
down_revision: str | None = "b3d4e5f6a7c8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: A1 用户拍板：Ark 官网 46 元/百万 tokens（不含视频输入档）= 0.046/千。
SEEDANCE_ARK_PER_KILO_TOKEN = 0.046
#: A2 用户拍板：provider 级默认按 2-0 档 = 0.046/千（fast/mini 分键随 C2）。
SEEDANCE_PROVIDER_PER_KILO_TOKEN = 0.046
#: 迁移执行者标记（updated_by 追溯）。
ACTOR = "c0-migration"

_BAK_DDL = sa.text(
    """
    CREATE TABLE IF NOT EXISTS media_pricing_bak (
        provider VARCHAR(32) NOT NULL,
        media_type VARCHAR(16) NOT NULL,
        unit VARCHAR(16) NOT NULL,
        unit_price FLOAT NOT NULL,
        currency VARCHAR(8) NOT NULL,
        updated_at DATETIME,
        updated_by VARCHAR(64) NOT NULL,
        backed_up_at DATETIME NOT NULL
    )
    """
)


def apply_fix(conn: sa.Connection) -> None:
    """C0 数据修正本体——alembic ``upgrade()`` 与单测共用这一条路径。

    幂等：备份仅在 ``media_pricing_bak`` 为空时写入（重放不覆盖原快照）。
    """
    now = datetime.now(UTC)
    conn.execute(_BAK_DDL)

    bak_count = conn.execute(sa.text("SELECT COUNT(*) FROM media_pricing_bak")).scalar()
    if not bak_count:
        conn.execute(
            sa.text(
                "INSERT INTO media_pricing_bak "
                "(provider, media_type, unit, unit_price, currency, updated_at, updated_by, backed_up_at) "
                "SELECT provider, media_type, unit, unit_price, currency, updated_at, updated_by, :now "
                "FROM media_pricing"
            ),
            {"now": now},
        )

    for provider, price in (
        ("seedance-ark", SEEDANCE_ARK_PER_KILO_TOKEN),
        ("seedance", SEEDANCE_PROVIDER_PER_KILO_TOKEN),
    ):
        conn.execute(
            sa.text("UPDATE media_pricing SET unit = 'per_kilo_token', unit_price = :price, updated_at = :now, updated_by = :actor WHERE provider = :provider AND media_type = 'video'"),
            {"price": price, "now": now, "actor": ACTOR, "provider": provider},
        )
    # A3 用户拍板：happyhorse 弃用（未实测、无在册任务）——删除行，unpriced 计数可见。
    conn.execute(sa.text("DELETE FROM media_pricing WHERE provider = 'happyhorse'"))


def restore_from_bak(conn: sa.Connection) -> None:
    """回滚：以备份表整体恢复 ``media_pricing``（备份表保留作审计线索）。"""
    conn.execute(sa.text("DELETE FROM media_pricing"))
    conn.execute(sa.text("INSERT INTO media_pricing (provider, media_type, unit, unit_price, currency, updated_at, updated_by) SELECT provider, media_type, unit, unit_price, currency, updated_at, updated_by FROM media_pricing_bak"))


def upgrade() -> None:
    apply_fix(op.get_bind())


def downgrade() -> None:
    restore_from_bak(op.get_bind())
