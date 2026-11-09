"""add_model_id_to_media_pricing (C2, 2026-08-27 成本可信化批次)

``media_pricing`` 模型级定价键：

1. **表重建**（SQLite 不能改 PK，手工重建最确定）：新表带 surrogate ``id`` PK +
   nullable ``model_id`` 列；旧行全部保留为 provider 级（model_id NULL）。
2. **两个部分唯一索引**（SQLite/Postgres 对 NULL 的唯一语义都兜住）：
   - ``(provider, media_type) WHERE model_id IS NULL``——provider 级行唯一；
   - ``(provider, model_id, media_type) WHERE model_id IS NOT NULL``——模型级行唯一。
3. **§一 已核实 seedance 系模型级价目插入**（Ark 官网 2026-08-26 截图，非待核实项；
   运营可经定价面板改）：fast 0.02775 / mini 0.0092 / seedance-ark 模型行 0.046
   （2-0 档由 provider 级行 0.046 承载，A2）。

链路保证（P0-2 收口的另一半）：provider 级行的扁平 ``(provider, media_type)`` 键形
不变——闸/指标 4 处消费点（visual_factory/pricing/provider_bridge×2）零改动；
模型级行仅经 ``_shared/media_pricing_resolver.resolve_media_price`` 命中。

Revision ID: k3d7f9e8a1b5
Revises: j2f6a8b0c1d7
Create Date: 2026-08-27 12:30:00.000000
"""

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "k3d7f9e8a1b5"
down_revision: str | None = "j2f6a8b0c1d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ACTOR = "c2-migration"

#: §一 已核实的 seedance 系模型级行（per_kilo_token，CNY）。
_SEED_MODEL_ROWS: list[tuple[str, str, str, str, float]] = [
    ("seedance", "bytedance/seedance-2-0-fast", "video", "per_kilo_token", 0.02775),
    ("seedance", "bytedance/doubao-seedance-2-0-mini", "video", "per_kilo_token", 0.0092),
    ("seedance-ark", "doubao-seedance-2-0-260128", "video", "per_kilo_token", 0.046),
]

_NEW_TABLE_DDL = sa.text(
    """
    CREATE TABLE media_pricing_new (
        id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
        provider VARCHAR(32) NOT NULL,
        model_id VARCHAR(64),
        media_type VARCHAR(16) NOT NULL,
        unit VARCHAR(16) NOT NULL,
        unit_price FLOAT NOT NULL,
        currency VARCHAR(8) NOT NULL,
        updated_at DATETIME,
        updated_by VARCHAR(64) NOT NULL
    )
    """
)

_COPY_ROWS = sa.text(
    """
    INSERT INTO media_pricing_new (provider, model_id, media_type, unit, unit_price, currency, updated_at, updated_by)
    SELECT provider, NULL, media_type, unit, unit_price, currency, updated_at, updated_by FROM media_pricing
    """
)


def apply_schema_and_seed(conn: sa.Connection) -> None:
    """C2 迁移本体——alembic ``upgrade()`` 与单测共用这一条路径（幂等）。"""
    has_model_id = conn.execute(sa.text("SELECT COUNT(*) FROM pragma_table_info('media_pricing') WHERE name = 'model_id'")).scalar()
    if has_model_id:
        pass  # 已重建过（重放）——跳过表重建，种子仍走下方 NOT EXISTS 守护
    else:
        conn.execute(_NEW_TABLE_DDL)
        conn.execute(_COPY_ROWS)
        conn.execute(sa.text("DROP TABLE media_pricing"))
        conn.execute(sa.text("ALTER TABLE media_pricing_new RENAME TO media_pricing"))

    conn.execute(sa.text("CREATE UNIQUE INDEX IF NOT EXISTS uq_media_pricing_provider_level ON media_pricing (provider, media_type) WHERE model_id IS NULL"))
    conn.execute(sa.text("CREATE UNIQUE INDEX IF NOT EXISTS uq_media_pricing_model_level ON media_pricing (provider, model_id, media_type) WHERE model_id IS NOT NULL"))

    now = datetime.now(UTC)
    for provider, model_id, media_type, unit, price in _SEED_MODEL_ROWS:
        conn.execute(
            sa.text(
                "INSERT INTO media_pricing (provider, model_id, media_type, unit, unit_price, currency, updated_at, updated_by) "
                "SELECT :provider, :model_id, :media_type, :unit, :price, 'CNY', :now, :actor "
                "WHERE NOT EXISTS ("
                "  SELECT 1 FROM media_pricing WHERE provider = :provider AND model_id = :model_id AND media_type = :media_type"
                ")"
            ),
            {"provider": provider, "model_id": model_id, "media_type": media_type, "unit": unit, "price": price, "now": now, "actor": ACTOR},
        )


def upgrade() -> None:
    apply_schema_and_seed(op.get_bind())


def downgrade() -> None:
    """回滚到 C2 前形态：去掉模型级行与索引，恢复复合 PK 表（备份表不受影响）。"""
    conn = op.get_bind()
    conn.execute(sa.text("DROP INDEX IF EXISTS uq_media_pricing_model_level"))
    conn.execute(sa.text("DROP INDEX IF EXISTS uq_media_pricing_provider_level"))
    conn.execute(sa.text("DELETE FROM media_pricing WHERE model_id IS NOT NULL"))
    conn.execute(
        sa.text(
            """
            CREATE TABLE media_pricing_old (
                provider VARCHAR(32) NOT NULL,
                media_type VARCHAR(16) NOT NULL,
                unit VARCHAR(16) NOT NULL,
                unit_price FLOAT NOT NULL,
                currency VARCHAR(8) NOT NULL,
                updated_at DATETIME,
                updated_by VARCHAR(64) NOT NULL,
                PRIMARY KEY (provider, media_type)
            )
            """
        )
    )
    conn.execute(sa.text("INSERT INTO media_pricing_old (provider, media_type, unit, unit_price, currency, updated_at, updated_by) SELECT provider, media_type, unit, unit_price, currency, updated_at, updated_by FROM media_pricing"))
    conn.execute(sa.text("DROP TABLE media_pricing"))
    conn.execute(sa.text("ALTER TABLE media_pricing_old RENAME TO media_pricing"))
