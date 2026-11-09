"""C1（2026-08-27 成本可信化批次）：五项核实单价填实（迁移 l4e8f0a9b2c6）。

1. **种子同步**——``DEFAULT_MEDIA_PRICING`` 与 §一/C1 拍板价一致（gpt-image-2
   0.204/千 token、seedream-360 0.5/张（A18 取高价侧）、seedream 0.5/张、
   doubao_tts 0.12/千字（A19 截图折算）、minimax-h3 0.0008/千 token）；
   dashscope 不落种子（无在册调用，落 0 价=¥0 假账）。
2. **随发布数据迁移**——``apply_c1_prices()`` 与 alembic ``upgrade()`` 共用
   一条路径：按 (provider, model_id IS NULL, media_type) 键 UPDATE→无行则
   INSERT；幂等；不动模型级行；回滚可逆。
3. **估算联动**——新单位下 ``_estimate_call_cost`` 命中预期值。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa

from app.gateway.routers.console import _estimate_call_cost


def _load_migration_module():
    """Import the C1 migration file by path (versions/ is not a package)."""
    path = Path(__file__).resolve().parent.parent / "packages" / "harness" / "deerflow" / "persistence" / "migrations" / "versions" / "l4e8f0a9b2c6_fill_c1_verified_prices.py"
    assert path.exists(), f"C1 migration file missing: {path}"
    spec = importlib.util.spec_from_file_location("c1_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ── 1. 种子同步 ─────────────────────────────────────────────────────────────


class TestC1SeedConstants:
    @staticmethod
    def _seed():
        from app.gateway.routers.media_pricing import DEFAULT_MEDIA_PRICING

        return {(e["provider"], e["media_type"]): e for e in DEFAULT_MEDIA_PRICING}

    def test_gpt_image2_token_billing(self) -> None:
        entry = self._seed()[("gpt-image-2", "image")]
        assert entry["unit"] == "per_kilo_token"
        assert entry["unit_price"] == pytest.approx(0.204)

    def test_seedream_360_ark_benchmark(self) -> None:
        """A18 用户拍板：360 折算价远低于 Ark → 取高价侧 Ark 价（0.5/张）。"""
        entry = self._seed()[("seedream-360", "image")]
        assert entry["unit"] == "per_image"
        assert entry["unit_price"] == pytest.approx(0.5)

    def test_seedream_ark_direct(self) -> None:
        entry = self._seed()[("seedream", "image")]
        assert entry["unit"] == "per_image"
        assert entry["unit_price"] == pytest.approx(0.5)

    def test_doubao_tts_screenshot_converted(self) -> None:
        """A19 用户截图：120 元/百万 token 输入 ÷ 1 字≈1 token = 0.12/千字。"""
        entry = self._seed()[("doubao_tts", "tts")]
        assert entry["unit"] == "per_kilo_char"
        assert entry["unit_price"] == pytest.approx(0.12)

    def test_minimax_h3_360_pricelist(self) -> None:
        """C1 曾按 360 文本表价落 0.0008/千 token——2026-09-01 T3 修正为按秒
        价 per_second 0.58（MiniMax 官方 768P $0.08/秒 ×7.2；千 token 价是
        文本表价误配且 H3 轮询无 usage 面可记，详见 test_media_pricing_h3_per_second.py）。"""
        entry = self._seed()[("minimax-h3", "video")]
        assert entry["unit"] == "per_second"
        assert entry["unit_price"] == pytest.approx(0.58)

    def test_dashscope_not_seeded(self) -> None:
        """无在册调用的 provider 不落种子——落 0 价会把未来调用算成 ¥0 假账。"""
        assert ("dashscope", "image") not in self._seed()

    def test_gpt_image2_estimate_anchor(self) -> None:
        """C1 实测锚：212 tokens/张 × 0.204/千 ≈ ¥0.0432（真值 ¥0.0405，≤2% 高估）。"""
        entry = {"unit": "per_kilo_token", "unit_price": 0.204, "currency": "CNY"}
        cost = _estimate_call_cost({"total_tokens": 212}, "generate_image", entry)
        assert cost == pytest.approx(0.043248, rel=1e-6)

    def test_seedream_360_per_image_estimate_needs_no_metadata(self) -> None:
        entry = {"unit": "per_image", "unit_price": 0.5, "currency": "CNY"}
        assert _estimate_call_cost({}, "generate_image", entry) == pytest.approx(0.5)


# ── 2. 数据迁移（apply_c1_prices 与 alembic upgrade 共用一条路径） ──────────

#: C2 迁移 k3d7f9e8a1b5 之后的表形态（surrogate id PK + nullable model_id）。
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


@pytest.fixture()
def c2_db():
    """C2 后形态 DB，含两条旧价 provider 级行与一条模型级行。"""
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(sa.text(_NEW_TABLE_DDL))
        conn.execute(
            sa.text(
                "INSERT INTO media_pricing (provider, model_id, media_type, unit, unit_price, currency, updated_by) VALUES "
                "('gpt-image-2', NULL, 'image', 'per_image', 0.04, 'CNY', 'operator'), "
                "('doubao_tts', NULL, 'tts', 'per_kilo_char', 0.5, 'CNY', 'operator'), "
                "('seedance', 'bytedance/seedance-2-0-fast', 'video', 'per_kilo_token', 0.02775, 'CNY', 'c2-migration')"
            )
        )
    yield engine
    engine.dispose()


def _provider_rows(engine) -> dict[tuple[str, str], dict]:
    with engine.connect() as conn:
        rows = [dict(r._mapping) for r in conn.execute(sa.text("SELECT * FROM media_pricing WHERE model_id IS NULL"))]
    return {(r["provider"], r["media_type"]): r for r in rows}


class TestC1Migration:
    def test_updates_preexisting_row_in_place(self, c2_db) -> None:
        mod = _load_migration_module()
        with c2_db.begin() as conn:
            mod.apply_c1_prices(conn)

        rows = _provider_rows(c2_db)
        assert rows[("gpt-image-2", "image")]["unit"] == "per_kilo_token"
        assert rows[("gpt-image-2", "image")]["unit_price"] == pytest.approx(0.204)
        assert rows[("gpt-image-2", "image")]["updated_by"] == "c1-probe(estimated)"
        assert rows[("doubao_tts", "tts")]["unit_price"] == pytest.approx(0.12)
        # 原行 UPDATE 而非新增——provider 级 gpt-image-2 仍只有一行
        assert sum(1 for k in rows if k[0] == "gpt-image-2") == 1

    def test_inserts_absent_provider_rows(self, c2_db) -> None:
        mod = _load_migration_module()
        with c2_db.begin() as conn:
            mod.apply_c1_prices(conn)

        rows = _provider_rows(c2_db)
        assert rows[("seedream-360", "image")]["unit_price"] == pytest.approx(0.5)
        assert rows[("seedream-360", "image")]["updated_by"] == "c1-user-A18(estimated)"
        assert rows[("seedream", "image")]["unit_price"] == pytest.approx(0.5)
        assert rows[("minimax-h3", "video")]["unit_price"] == pytest.approx(0.0008)

    def test_idempotent_rerun(self, c2_db) -> None:
        mod = _load_migration_module()
        with c2_db.begin() as conn:
            mod.apply_c1_prices(conn)
        with c2_db.begin() as conn:
            mod.apply_c1_prices(conn)

        rows = _provider_rows(c2_db)
        # 5 个 C1 键各一行（gpt-image-2/doubao_tts 为 UPDATE，其余 3 个为 INSERT）
        for key in [("gpt-image-2", "image"), ("seedream-360", "image"), ("seedream", "image"), ("doubao_tts", "tts"), ("minimax-h3", "video")]:
            assert sum(1 for k in rows if k == key) == 1, f"{key} 重放后必须仍只有一行"

    def test_model_level_rows_untouched(self, c2_db) -> None:
        mod = _load_migration_module()
        with c2_db.begin() as conn:
            mod.apply_c1_prices(conn)

        with c2_db.connect() as conn:
            model_rows = [dict(r._mapping) for r in conn.execute(sa.text("SELECT * FROM media_pricing WHERE model_id IS NOT NULL"))]
        assert len(model_rows) == 1
        assert model_rows[0]["model_id"] == "bytedance/seedance-2-0-fast"
        assert model_rows[0]["unit_price"] == pytest.approx(0.02775)

    def test_apply_on_empty_table_inserts_all(self) -> None:
        engine = sa.create_engine("sqlite://")
        with engine.begin() as conn:
            conn.execute(sa.text(_NEW_TABLE_DDL))
        mod = _load_migration_module()
        with engine.begin() as conn:
            mod.apply_c1_prices(conn)
        rows = _provider_rows(engine)
        assert len(rows) == 5
        engine.dispose()

    def test_rollback_restores_and_deletes(self, c2_db) -> None:
        mod = _load_migration_module()
        with c2_db.begin() as conn:
            mod.apply_c1_prices(conn)
        with c2_db.begin() as conn:
            mod.rollback_c1_prices(conn)

        rows = _provider_rows(c2_db)
        assert rows[("gpt-image-2", "image")]["unit"] == "per_image"
        assert rows[("gpt-image-2", "image")]["unit_price"] == pytest.approx(0.04)
        assert rows[("doubao_tts", "tts")]["unit_price"] == pytest.approx(0.5)
        assert ("seedream-360", "image") not in rows
        assert ("minimax-h3", "video") not in rows

    def test_revision_chain(self) -> None:
        mod = _load_migration_module()
        assert mod.revision == "l4e8f0a9b2c6"
        assert mod.down_revision == "k3d7f9e8a1b5"
