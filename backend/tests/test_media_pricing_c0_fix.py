"""C0（2026-08-27 成本可信化批次）：错误单价即时修正（止血）。

三件事：
1. **种子修正**——``DEFAULT_MEDIA_PRICING`` 的 seedance-ark（0.023→0.046/千 token）、
   seedance（per_second 0.5 → per_kilo_token 0.046）、happyhorse 弃用（A3 用户拍板）。
2. **随发布数据迁移**——存量 DB 行同样修正，且先把现值快照进 ``media_pricing_bak``
   备份表（回滚依据）；迁移逻辑与 alembic ``upgrade()`` 共用 ``apply_fix()``，
   禁止手工改生产库（A9/C0 改动形态）。
3. **A13 金额闸默认关闭**——``ai_visual_budget_cny`` 代码默认 400.0 → None；
   次数闸（600/240）为唯一硬顶；显式配置金额时仍拦截。

验收锚（AC1 推导）：单调用 180,720 tokens × 0.046/千 ≈ ¥8.31。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa

from app.gateway.routers.console import _estimate_call_cost, invalidate_media_pricing_cache


def _load_migration_module():
    """Import the C0 migration file by path (versions/ is not a package)."""
    path = Path(__file__).resolve().parent.parent / "packages" / "harness" / "deerflow" / "persistence" / "migrations" / "versions" / "j2f6a8b0c1d7_fix_media_pricing_seedance_prices.py"
    assert path.exists(), f"C0 migration file missing: {path}"
    spec = importlib.util.spec_from_file_location("c0_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ── 1. 种子修正 ─────────────────────────────────────────────────────────────


class TestSeedConstants:
    """DEFAULT_MEDIA_PRICING 与 §一 价格主数据一致（新部署 + GET 兜底层）。"""

    @staticmethod
    def _seed():
        from app.gateway.routers.media_pricing import DEFAULT_MEDIA_PRICING

        return {(e["provider"], e["media_type"]): e for e in DEFAULT_MEDIA_PRICING}

    def test_seedance_ark_corrected(self) -> None:
        entry = self._seed()[("seedance-ark", "video")]
        assert entry["unit"] == "per_kilo_token"
        assert entry["unit_price"] == pytest.approx(0.046)

    def test_seedance_provider_level_corrected(self) -> None:
        entry = self._seed()[("seedance", "video")]
        assert entry["unit"] == "per_kilo_token"
        assert entry["unit_price"] == pytest.approx(0.046)

    def test_happyhorse_deprecated_removed_from_seed(self) -> None:
        assert ("happyhorse", "video") not in self._seed()

    def test_ac1_anchor_180720_tokens(self) -> None:
        """单调用 180,720 tokens × 0.046/千 ≈ ¥8.31（vg_b9fab64bfa84 推导锚）。"""
        entry = {"unit": "per_kilo_token", "unit_price": 0.046, "currency": "CNY"}
        cost = _estimate_call_cost({"total_tokens": 180_720}, "generate_video", entry)
        assert cost == pytest.approx(8.313_12, rel=1e-6)


# ── 2. 数据迁移（apply_fix 与 alembic upgrade 共用一条路径） ────────────────


_OLD_TABLE_DDL = """
CREATE TABLE media_pricing (
    provider VARCHAR(32) NOT NULL,
    media_type VARCHAR(16) NOT NULL,
    unit VARCHAR(16) NOT NULL,
    unit_price FLOAT NOT NULL,
    currency VARCHAR(8) NOT NULL,
    updated_at DATETIME,
    updated_by VARCHAR(64) NOT NULL
)
"""


@pytest.fixture()
def legacy_db():
    """Old-schema sqlite DB seeded with the production bad values."""
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(sa.text(_OLD_TABLE_DDL))
        conn.execute(
            sa.text(
                "INSERT INTO media_pricing (provider, media_type, unit, unit_price, currency, updated_by) VALUES "
                "('seedance-ark', 'video', 'per_kilo_token', 1.5, 'CNY', 'operator'), "
                "('seedance', 'video', 'per_second', 0.5, 'CNY', 'operator'), "
                "('happyhorse', 'video', 'per_second', 0.3, 'CNY', 'operator'), "
                "('seedream', 'image', 'per_image', 0.22, 'CNY', 'operator')"
            )
        )
    yield engine
    engine.dispose()


def _rows(engine, table: str) -> list[dict]:
    with engine.connect() as conn:
        return [dict(r._mapping) for r in conn.execute(sa.text(f"SELECT * FROM {table}"))]


class TestC0Migration:
    def test_fix_corrects_prices_and_backs_up(self, legacy_db) -> None:
        mod = _load_migration_module()
        with legacy_db.begin() as conn:
            mod.apply_fix(conn)

        rows = {(r["provider"], r["media_type"]): r for r in _rows(legacy_db, "media_pricing")}
        assert rows[("seedance-ark", "video")]["unit"] == "per_kilo_token"
        assert rows[("seedance-ark", "video")]["unit_price"] == pytest.approx(0.046)
        assert rows[("seedance", "video")]["unit"] == "per_kilo_token"
        assert rows[("seedance", "video")]["unit_price"] == pytest.approx(0.046)
        assert ("happyhorse", "video") not in rows, "A3：happyhorse 弃用（删除行，备份已留）"

        bak = {(r["provider"], r["media_type"]): r for r in _rows(legacy_db, "media_pricing_bak")}
        assert len(bak) == 4, "备份须含改前全部行"
        assert bak[("seedance-ark", "video")]["unit_price"] == pytest.approx(1.5), "备份=改前原值（¥1.5 事故值）"
        assert bak[("seedance", "video")]["unit"] == "per_second"
        assert bak[("happyhorse", "video")]["unit_price"] == pytest.approx(0.3)

    def test_fix_preserves_unrelated_rows(self, legacy_db) -> None:
        mod = _load_migration_module()
        with legacy_db.begin() as conn:
            mod.apply_fix(conn)

        rows = {(r["provider"], r["media_type"]): r for r in _rows(legacy_db, "media_pricing")}
        assert rows[("seedream", "image")]["unit_price"] == pytest.approx(0.22), "无关行不动"

    def test_fix_idempotent_rerun_keeps_original_backup(self, legacy_db) -> None:
        mod = _load_migration_module()
        with legacy_db.begin() as conn:
            mod.apply_fix(conn)
        with legacy_db.begin() as conn:
            mod.apply_fix(conn)  # 重放不得用修正值覆盖备份

        bak = {(r["provider"], r["media_type"]): r for r in _rows(legacy_db, "media_pricing_bak")}
        assert bak[("seedance-ark", "video")]["unit_price"] == pytest.approx(1.5)
        assert len(bak) == 4

    def test_fix_on_empty_table_creates_empty_backup(self) -> None:
        engine = sa.create_engine("sqlite://")
        with engine.begin() as conn:
            conn.execute(sa.text(_OLD_TABLE_DDL))
        mod = _load_migration_module()
        with engine.begin() as conn:
            mod.apply_fix(conn)
        assert _rows(engine, "media_pricing") == []
        assert _rows(engine, "media_pricing_bak") == []
        engine.dispose()

    def test_restore_from_bak_rolls_back(self, legacy_db) -> None:
        mod = _load_migration_module()
        with legacy_db.begin() as conn:
            mod.apply_fix(conn)
        with legacy_db.begin() as conn:
            mod.restore_from_bak(conn)

        rows = {(r["provider"], r["media_type"]): r for r in _rows(legacy_db, "media_pricing")}
        assert rows[("seedance-ark", "video")]["unit_price"] == pytest.approx(1.5), "回滚=备份原值"
        assert ("happyhorse", "video") in rows

    def test_revision_chain(self) -> None:
        """C0 迁移链接在当前 head（b3d4e5f6a7c8）之后。"""
        mod = _load_migration_module()
        assert mod.revision == "j2f6a8b0c1d7"
        assert mod.down_revision == "b3d4e5f6a7c8"


# ── 3. A13：金额闸默认关闭，次数闸为唯一硬顶 ────────────────────────────────


class TestA13BudgetGateDefaultOff:
    def test_default_config_disables_cost_cap(self) -> None:
        from app.gateway.video_gen.config import VideoGenConfig

        assert VideoGenConfig().ai_visual_budget_cny is None

    def test_default_budget_cost_dimension_unlimited(self) -> None:
        """默认配置下金额维度不限：300 次 × ¥1.5 = ¥450 > 旧默认 ¥400 仍放行；
        次数闸放大到 10000 以隔离「金额维度」这一个变量。"""
        from app.gateway.video_gen.config import VideoGenConfig
        from app.gateway.video_gen.visual_factory import make_visual_budget
        from app.gateway.video_gen.visual_sources.budget import VisualBudgetExceeded

        invalidate_media_pricing_cache()  # 空 cache → unit_cost 回落常量 1.5，确定性
        cfg = VideoGenConfig(ai_video_max_gen=10_000)
        budget = make_visual_budget("video", config=cfg)
        assert budget._max_cost_cny is None
        try:
            for _ in range(300):
                budget.charge()
        except VisualBudgetExceeded as exc:  # 旧默认 400.0 会在第 267 次金额拦截
            pytest.fail(f"默认配置不得按金额拦截（A13）: {exc}")

    def test_call_cap_still_enforced_by_default(self) -> None:
        from app.gateway.video_gen.config import VideoGenConfig
        from app.gateway.video_gen.visual_factory import make_visual_budget
        from app.gateway.video_gen.visual_sources.budget import VisualBudgetExceeded

        invalidate_media_pricing_cache()
        budget = make_visual_budget("video", config=VideoGenConfig())
        with pytest.raises(VisualBudgetExceeded, match="calls"):
            for _ in range(241):
                budget.charge()

    def test_explicit_budget_still_blocks(self) -> None:
        """运营显式配置金额时仍拦截（能力保留，A13）。"""
        from app.gateway.video_gen.config import VideoGenConfig
        from app.gateway.video_gen.visual_factory import make_visual_budget
        from app.gateway.video_gen.visual_sources.budget import VisualBudgetExceeded

        invalidate_media_pricing_cache()
        cfg = VideoGenConfig(ai_visual_budget_cny=0.01, ai_video_max_gen=10_000)
        budget = make_visual_budget("video", config=cfg)
        with pytest.raises(VisualBudgetExceeded, match="cost exceeded"):
            budget.charge()
