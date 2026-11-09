"""T3（2026-09-01 拆帧/计价计划）：minimax-h3 价目 per_kilo_token 0.0008 →
per_second 0.58。

C1 落库的千 token 价是 360 模型广场**文本**表价（¥0.8/1M）误配到视频模型；
MiniMax 官方视频按**时长**计价（768P $0.08/秒 × 7.2 ≈ ¥0.58/秒）。且 H3
轮询响应无 usage/tokens 面——per_kilo_token 永远估不出：vg_22ee06b39d19 的
68/68 行 generate_video 无 total_tokens → unpriced 静默不计，~510s 视频分量
≈¥296 整体漏记（账单仅 ¥47.33）。

三层同改（种子展示兜底 / DB 行估算真源 / 迁移交付），``_estimate_call_cost``
的 per_second 分支已存在，零估算器改动。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa

from app.gateway.routers.console import _estimate_call_cost

#: C2 迁移之后的表形态（与 test_media_pricing_c1_fill 同款 DDL）。
_NEW_TABLE_DDL = """
CREATE TABLE media_pricing (
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


def _load_migration_module():
    """Import the H3 per-second migration file by path (versions/ is not a package)."""
    path = Path(__file__).resolve().parent.parent / "packages" / "harness" / "deerflow" / "persistence" / "migrations" / "versions" / "m6a7b8c9d0e1_fix_h3_video_per_second_pricing.py"
    assert path.exists(), f"H3 per-second migration file missing: {path}"
    spec = importlib.util.spec_from_file_location("h3_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ── 1. 种子同步（展示兜底层与拍板价一致） ────────────────────────────────────


class TestH3Seed:
    @staticmethod
    def _seed():
        from app.gateway.routers.media_pricing import DEFAULT_MEDIA_PRICING

        return {(e["provider"], e["media_type"]): e for e in DEFAULT_MEDIA_PRICING}

    def test_seed_per_second(self) -> None:
        """MiniMax 官方 768P $0.08/秒 × 汇率 7.2 = ¥0.58/秒（用户拍板值；
        360 网关若另有定价，运营可经 /api/admin/media-pricing 后调，不阻塞）。"""
        entry = self._seed()[("minimax-h3", "video")]
        assert entry["unit"] == "per_second"
        assert entry["unit_price"] == pytest.approx(0.58)

    def test_estimate_anchor_video_component(self) -> None:
        """账单拆解锚：~510s 视频分量 × 0.58 ≈ ¥295.8（vg_22ee06b39d19
        漏记的真值量级；修前 unpriced 静默 ¥0）。"""
        entry = self._seed()[("minimax-h3", "video")]
        assert _estimate_call_cost({"duration": 510.0}, "generate_video", entry) == pytest.approx(295.8)

    def test_estimate_without_duration_is_none(self) -> None:
        """无 duration 元数据的行不硬估——unpriced 计数可见（口径：duration
        是请求回声，缺失时宁可不估）。"""
        entry = self._seed()[("minimax-h3", "video")]
        assert _estimate_call_cost({}, "generate_video", entry) is None


# ── 2. 数据迁移（apply 与 alembic upgrade 共用一条路径） ────────────────────


@pytest.fixture()
def c1_db():
    """C1 落库后形态：provider 级 minimax-h3 行（per_kilo_token 0.0008）+ 一条
    模型级行（迁移不得触碰）。"""
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(sa.text(_NEW_TABLE_DDL))
        conn.execute(
            sa.text(
                "INSERT INTO media_pricing (provider, model_id, media_type, unit, unit_price, currency, updated_by) VALUES "
                "('minimax-h3', NULL, 'video', 'per_kilo_token', 0.0008, 'CNY', 'c1-360-pricelist(estimated)'), "
                "('minimax-h3', 'minimax/MiniMax-H3', 'video', 'per_second', 0.4, 'CNY', 'operator')"
            )
        )
    yield engine
    engine.dispose()


def _provider_rows(engine) -> dict[tuple[str, str], dict]:
    with engine.connect() as conn:
        rows = [dict(r._mapping) for r in conn.execute(sa.text("SELECT * FROM media_pricing WHERE model_id IS NULL"))]
    return {(r["provider"], r["media_type"]): r for r in rows}


class TestH3Migration:
    def test_updates_preexisting_c1_row(self, c1_db) -> None:
        mod = _load_migration_module()
        with c1_db.begin() as conn:
            mod.apply_h3_per_second(conn)

        row = _provider_rows(c1_db)[("minimax-h3", "video")]
        assert row["unit"] == "per_second"
        assert row["unit_price"] == pytest.approx(0.58)
        assert "h3" in row["updated_by"], "updated_by 须留来源标记"

    def test_inserts_absent_row_on_fresh_db(self) -> None:
        """无该行的库（如未跑过 C1 的旁路部署）→ INSERT 兜底。"""
        engine = sa.create_engine("sqlite://")
        with engine.begin() as conn:
            conn.execute(sa.text(_NEW_TABLE_DDL))
        mod = _load_migration_module()
        with engine.begin() as conn:
            mod.apply_h3_per_second(conn)
        row = _provider_rows(engine)[("minimax-h3", "video")]
        assert row["unit_price"] == pytest.approx(0.58)
        engine.dispose()

    def test_idempotent_rerun(self, c1_db) -> None:
        mod = _load_migration_module()
        with c1_db.begin() as conn:
            mod.apply_h3_per_second(conn)
        with c1_db.begin() as conn:
            mod.apply_h3_per_second(conn)
        rows = _provider_rows(c1_db)
        assert sum(1 for k in rows if k == ("minimax-h3", "video")) == 1, "重放后必须仍只有一行"

    def test_model_level_rows_untouched(self, c1_db) -> None:
        """模型级行（运营手配 per_second 0.4）不被 provider 级迁移覆写。"""
        mod = _load_migration_module()
        with c1_db.begin() as conn:
            mod.apply_h3_per_second(conn)
        with c1_db.connect() as conn:
            model_rows = [dict(r._mapping) for r in conn.execute(sa.text("SELECT * FROM media_pricing WHERE model_id IS NOT NULL"))]
        assert len(model_rows) == 1
        assert model_rows[0]["unit_price"] == pytest.approx(0.4)

    def test_rollback_restores_c1_value(self, c1_db) -> None:
        mod = _load_migration_module()
        with c1_db.begin() as conn:
            mod.apply_h3_per_second(conn)
        with c1_db.begin() as conn:
            mod.rollback_h3_per_second(conn)
        row = _provider_rows(c1_db)[("minimax-h3", "video")]
        assert row["unit"] == "per_kilo_token"
        assert row["unit_price"] == pytest.approx(0.0008)

    def test_revision_chain(self) -> None:
        mod = _load_migration_module()
        assert mod.revision == "m6a7b8c9d0e1"
        assert mod.down_revision == "l4e8f0a9b2c6"
