"""2026-09-01 全模型计价审计：seedance-mini 单价 0.0092 → 0.023（字节官方价）。

0.0092 来源不明且低于官方 60%——字节官方公开价（财联社/新浪财经 2026-06-16）：
Seedance 2.0 Mini 图生视频 0.023 元/千 tokens、视频生视频 0.014 元/千 tokens。
本项目用法全为图生视频（i2v 首帧 / r2v 参考图），取 0.023。
用户拍板原则：360 价与字节官方相差特别远时按字节公开市场价计价。

迁移 n7b8c9d0e1f2（幂等/可回滚/模型级行定点）。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa

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

_MINI = "bytedance/doubao-seedance-2-0-mini"


def _load_migration_module():
    path = Path(__file__).resolve().parent.parent / "packages" / "harness" / "deerflow" / "persistence" / "migrations" / "versions" / "n7b8c9d0e1f2_fix_seedance_mini_official_price.py"
    assert path.exists(), f"mini migration file missing: {path}"
    spec = importlib.util.spec_from_file_location("mini_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def mini_db():
    """C2 落库后形态：mini 模型级行 0.0092 + fast 模型级行（迁移不得触碰）。"""
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(sa.text(_NEW_TABLE_DDL))
        conn.execute(
            sa.text(
                "INSERT INTO media_pricing (provider, model_id, media_type, unit, unit_price, currency, updated_by) VALUES "
                "('seedance', :mini, 'video', 'per_kilo_token', 0.0092, 'CNY', 'c2(estimated)'), "
                "('seedance', 'bytedance/seedance-2-0-fast', 'video', 'per_kilo_token', 0.02775, 'CNY', 'c2(estimated)')"
            ),
            {"mini": _MINI},
        )
    yield engine
    engine.dispose()


def _model_rows(engine) -> dict[tuple[str, str], dict]:
    with engine.connect() as conn:
        rows = [dict(r._mapping) for r in conn.execute(sa.text("SELECT * FROM media_pricing WHERE model_id IS NOT NULL"))]
    return {(r["provider"], r["model_id"]): r for r in rows}


class TestMiniMigration:
    def test_updates_preexisting_row(self, mini_db) -> None:
        mod = _load_migration_module()
        with mini_db.begin() as conn:
            mod.apply_seedance_mini_official_price(conn)
        row = _model_rows(mini_db)[("seedance", _MINI)]
        assert row["unit_price"] == pytest.approx(0.023)
        assert row["unit"] == "per_kilo_token", "单位不变（官方 mini 也是 token 计价）"
        assert "mini" in row["updated_by"]

    def test_inserts_absent_row_on_fresh_db(self) -> None:
        engine = sa.create_engine("sqlite://")
        with engine.begin() as conn:
            conn.execute(sa.text(_NEW_TABLE_DDL))
        mod = _load_migration_module()
        with engine.begin() as conn:
            mod.apply_seedance_mini_official_price(conn)
        row = _model_rows(engine)[("seedance", _MINI)]
        assert row["unit_price"] == pytest.approx(0.023)
        engine.dispose()

    def test_idempotent_rerun(self, mini_db) -> None:
        mod = _load_migration_module()
        for _ in range(2):
            with mini_db.begin() as conn:
                mod.apply_seedance_mini_official_price(conn)
        rows = _model_rows(mini_db)
        assert sum(1 for k in rows if k == ("seedance", _MINI)) == 1

    def test_fast_row_untouched(self, mini_db) -> None:
        """fast 模型级行（0.02775，与 360 广场截图一致）不被覆写。"""
        mod = _load_migration_module()
        with mini_db.begin() as conn:
            mod.apply_seedance_mini_official_price(conn)
        row = _model_rows(mini_db)[("seedance", "bytedance/seedance-2-0-fast")]
        assert row["unit_price"] == pytest.approx(0.02775)

    def test_rollback_restores_value(self, mini_db) -> None:
        mod = _load_migration_module()
        with mini_db.begin() as conn:
            mod.apply_seedance_mini_official_price(conn)
        with mini_db.begin() as conn:
            mod.rollback_seedance_mini_official_price(conn)
        row = _model_rows(mini_db)[("seedance", _MINI)]
        assert row["unit_price"] == pytest.approx(0.0092)

    def test_revision_chain(self) -> None:
        mod = _load_migration_module()
        assert mod.revision == "n7b8c9d0e1f2"
        assert mod.down_revision == "m6a7b8c9d0e1"
