"""C2（2026-08-27 成本可信化批次）：模型级定价键 + 别名归一 + 查价收口单点 resolver。

覆盖计划 C2 的六组断言：
1. ``media_pricing`` 新增 ``model_id`` 列（null=provider 级默认行），部分唯一索引
   保证两个层级各自不重（SQLite NULL 语义用部分索引兜底）；
2. **扁平键形守护**——``_load_media_pricing()`` 只返回 provider 级行的
   ``(provider, media_type)`` 扁平键（闸/指标 4 处消费点零改动的结构保证）；
3. ``resolve_media_price(provider, model_id, media_type)``：模型级（先经别名归一）
   → provider 级 → None；三套命名空间（注册名 / 真实模型 id / ViMax backend 名）
   各一例（step 0 实测值域为输入）；
4. ``compute_task_costs`` 估算按 metadata.model 命中模型级行（AC3 前置）；
5. C2 schema/数据迁移：老表重建 + seedance 系四行模型级价目（§一 已核实价）；
6. admin 契约扩展：GET/PUT 读写 model_id，payload 内重复键 422。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa

from app.gateway.routers.console import invalidate_media_pricing_cache


@pytest.fixture()
async def db(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from deerflow.persistence.base import Base
    from deerflow.persistence.engine import close_engine, get_engine, init_engine

    monkeypatch.setattr(
        "app.gateway.routers.console.get_app_config",
        lambda: SimpleNamespace(models=[SimpleNamespace(name="m", model="vendor/m")]),
    )
    await init_engine("sqlite", url=f"sqlite+aiosqlite:///{tmp_path / 'model_level.db'}", sqlite_dir=str(tmp_path))
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    invalidate_media_pricing_cache()
    yield
    await close_engine()


async def _add_media_pricing(provider: str, media_type: str, unit: str, unit_price: float, model_id: str | None = None, currency: str = "CNY") -> None:
    from deerflow.persistence.engine import get_session_factory
    from deerflow.persistence.media_pricing.model import MediaPricingRow

    sf = get_session_factory()
    async with sf() as session:
        session.add(
            MediaPricingRow(
                provider=provider,
                model_id=model_id,
                media_type=media_type,
                unit=unit,
                unit_price=unit_price,
                currency=currency,
            )
        )
        await session.commit()
    invalidate_media_pricing_cache()


async def _add_external_call(*, task_id: str, provider: str, method: str = "generate_video", status: str = "ok", metadata: dict | None = None) -> None:
    from uuid import uuid4

    from deerflow.persistence.engine import get_session_factory
    from deerflow.persistence.external_api_calls.model import ExternalApiCallRow

    sf = get_session_factory()
    async with sf() as session:
        session.add(
            ExternalApiCallRow(
                call_id=f"call-{uuid4().hex[:12]}",
                user_id="u-1",
                task_id=task_id,
                provider=provider,
                method=method,
                status=status,
                metadata_json=metadata,
            )
        )
        await session.commit()


async def _seed_family_rows() -> None:
    """§一 已核实的 seedance 系价目（provider 级 + 三档模型级 + ark 模型级）。"""
    await _add_media_pricing("seedance", "video", "per_kilo_token", 0.046)
    await _add_media_pricing("seedance", "video", "per_kilo_token", 0.02775, model_id="bytedance/seedance-2-0-fast")
    await _add_media_pricing("seedance", "video", "per_kilo_token", 0.0092, model_id="bytedance/doubao-seedance-2-0-mini")
    await _add_media_pricing("seedance-ark", "video", "per_kilo_token", 0.046)
    await _add_media_pricing("seedance-ark", "video", "per_kilo_token", 0.046, model_id="doubao-seedance-2-0-260128")


# ── 1. schema：model_id 列 + 部分唯一索引 ──────────────────────────────────


class TestModelIdColumn:
    @pytest.mark.anyio
    async def test_provider_level_row_has_null_model_id(self, db) -> None:
        from sqlalchemy import select

        from deerflow.persistence.engine import get_session_factory
        from deerflow.persistence.media_pricing.model import MediaPricingRow

        await _add_media_pricing("seedance", "video", "per_kilo_token", 0.046)
        sf = get_session_factory()
        async with sf() as session:
            row = (await session.execute(select(MediaPricingRow))).scalars().one()
        assert row.model_id is None
        assert hasattr(MediaPricingRow, "model_id"), "C2：模型级行依赖 model_id 列"

    @pytest.mark.anyio
    async def test_provider_level_duplicate_rejected(self, db) -> None:
        from sqlalchemy.exc import IntegrityError

        await _add_media_pricing("seedance", "video", "per_kilo_token", 0.046)
        with pytest.raises(IntegrityError):
            await _add_media_pricing("seedance", "video", "per_kilo_token", 0.9)

    @pytest.mark.anyio
    async def test_model_level_duplicate_rejected(self, db) -> None:
        from sqlalchemy.exc import IntegrityError

        await _add_media_pricing("seedance", "video", "per_kilo_token", 0.02775, model_id="bytedance/seedance-2-0-fast")
        with pytest.raises(IntegrityError):
            await _add_media_pricing("seedance", "video", "per_kilo_token", 0.03, model_id="bytedance/seedance-2-0-fast")

    @pytest.mark.anyio
    async def test_provider_and_model_level_coexist(self, db) -> None:
        """同 provider 同 media_type：一行 provider 级 + N 行模型级互不冲突。"""
        from sqlalchemy import select

        from deerflow.persistence.engine import get_session_factory
        from deerflow.persistence.media_pricing.model import MediaPricingRow

        await _seed_family_rows()
        sf = get_session_factory()
        async with sf() as session:
            rows = (await session.execute(select(MediaPricingRow))).scalars().all()
        assert len(rows) == 5


# ── 2. 扁平键形守护（闸 4 处消费点零改动的结构保证） ────────────────────────


class TestFlatKeyShapeGuard:
    @pytest.mark.anyio
    async def test_load_media_pricing_returns_provider_level_flat_keys_only(self, db) -> None:
        from app.gateway.routers.console import _load_media_pricing

        await _seed_family_rows()
        flat = await _load_media_pricing()
        assert all(len(k) == 2 for k in flat), "扁平键形必须是 (provider, media_type) 二元组"
        assert ("seedance", "video") in flat
        assert flat[("seedance", "video")]["unit_price"] == pytest.approx(0.046), "模型级行不得倒灌扁平键"
        assert ("seedance-ark", "video") in flat

    def test_sync_accessor_shape_unchanged(self) -> None:
        from app.gateway.routers.console import _load_media_pricing_sync

        invalidate_media_pricing_cache()
        assert _load_media_pricing_sync() == {}


# ── 3. resolve_media_price：别名归一 + 三套命名空间 ─────────────────────────


class TestResolveMediaPrice:
    @pytest.mark.anyio
    async def test_registry_name_namespace(self, db) -> None:
        """throttle 路径：metadata.model=注册名（如 seedance-fast，实测在场）。"""
        from app.gateway._shared.media_pricing_resolver import resolve_media_price

        await _seed_family_rows()
        await _load()  # noqa: F821 → 用下方导入
        entry = resolve_media_price("seedance", "seedance-fast", "video")
        assert entry is not None and entry["unit_price"] == pytest.approx(0.02775)

    @pytest.mark.anyio
    async def test_real_model_id_namespace(self, db) -> None:
        """真实模型 id（如 bytedance/seedance-2-0-fast，provider 下发）。"""
        from app.gateway._shared.media_pricing_resolver import resolve_media_price

        await _seed_family_rows()
        await _load()
        entry = resolve_media_price("seedance", "bytedance/seedance-2-0-fast", "video")
        assert entry is not None and entry["unit_price"] == pytest.approx(0.02775)

    @pytest.mark.anyio
    async def test_backend_name_namespace(self, db) -> None:
        """ViMax video 桥：metadata.model=backend 名（seedance-ark，实测在场）。"""
        from app.gateway._shared.media_pricing_resolver import resolve_media_price

        await _seed_family_rows()
        await _load()
        entry = resolve_media_price("seedance-ark", "seedance-ark", "video")
        assert entry is not None and entry["unit_price"] == pytest.approx(0.046)
        assert entry.get("model_id") == "doubao-seedance-2-0-260128"

    @pytest.mark.anyio
    async def test_unknown_model_falls_back_to_provider_level(self, db) -> None:
        from app.gateway._shared.media_pricing_resolver import resolve_media_price

        await _seed_family_rows()
        await _load()
        entry = resolve_media_price("seedance", "mystery-model", "video")
        assert entry is not None and entry["unit_price"] == pytest.approx(0.046)

    @pytest.mark.anyio
    async def test_default_registry_name_uses_provider_level(self, db) -> None:
        """实测：seedance 默认档 metadata.model='seedance'（注册名）→ provider 级 0.046（A2）。"""
        from app.gateway._shared.media_pricing_resolver import resolve_media_price

        await _seed_family_rows()
        await _load()
        entry = resolve_media_price("seedance", "seedance", "video")
        assert entry is not None and entry["unit_price"] == pytest.approx(0.046)

    @pytest.mark.anyio
    async def test_preset_voice_id_is_not_a_model(self, db) -> None:
        """实测：qihu_tts 的 metadata.model='preset:153'（音色 id）→ 必须走 provider 级，
        别名表不得映射 preset:*。"""
        from app.gateway._shared.media_pricing_resolver import resolve_media_price

        await _add_media_pricing("qihu_tts", "tts", "per_kilo_char", 0.3)
        await _load()
        entry = resolve_media_price("qihu_tts", "preset:153", "tts")
        assert entry is not None and entry["unit_price"] == pytest.approx(0.3)

    @pytest.mark.anyio
    async def test_legacy_qihu_row_maps_via_alias(self, db) -> None:
        """A15 实测修正：存量 provider='qihu' 行的 model='minimax-h3' → 经别名命中
        (minimax-h3, minimax/MiniMax-H3) 模型级行（provider 列被规范化接管）。"""
        from app.gateway._shared.media_pricing_resolver import resolve_media_price

        await _add_media_pricing("minimax-h3", "video", "per_second", 0.4, model_id="minimax/MiniMax-H3")
        await _load()
        entry = resolve_media_price("qihu", "minimax-h3", "video")
        assert entry is not None and entry["unit_price"] == pytest.approx(0.4)

    @pytest.mark.anyio
    async def test_no_pricing_returns_none(self, db) -> None:
        from app.gateway._shared.media_pricing_resolver import resolve_media_price

        await _seed_family_rows()
        await _load()
        assert resolve_media_price("no-such-provider", None, "video") is None

    def test_alias_table_has_no_preset_entries(self) -> None:
        from app.gateway._shared.media_constants import PROVIDER_MODEL_ALIASES

        assert not any(k.startswith("preset:") or v[1].startswith("preset:") for k, v in PROVIDER_MODEL_ALIASES.items())
        # 三套命名空间各在册（step 0 实测 + 代码事实）
        for key in ("seedance-fast", "bytedance/seedance-2-0-fast", "seedance-ark", "doubao-seedance-2-0-260128", "minimax-h3", "minimax/MiniMax-H3", "openai/gpt-image-2", "gpt-image-2"):
            assert key in PROVIDER_MODEL_ALIASES, f"别名表缺 {key}"


async def _load() -> None:
    from app.gateway.routers.console import _load_media_pricing

    await _load_media_pricing()


# ── 4. compute_task_costs 按模型计价（AC3 前置） ────────────────────────────


class TestTaskCostModelAware:
    @pytest.mark.anyio
    async def test_registry_name_priced_at_model_level(self, db) -> None:
        from deerflow.persistence.engine import get_session_factory

        await _seed_family_rows()
        await _add_external_call(task_id="t-fast", provider="seedance", metadata={"model": "seedance-fast", "total_tokens": 100_000})

        sf = get_session_factory()
        async with sf() as session:
            costs = await _compute(session, ["t-fast"])
        assert costs["t-fast"][0] == pytest.approx(100 * 0.02775)

    @pytest.mark.anyio
    async def test_mini_real_id_priced_at_model_level(self, db) -> None:
        from deerflow.persistence.engine import get_session_factory

        await _seed_family_rows()
        await _add_external_call(task_id="t-mini", provider="seedance", metadata={"model": "bytedance/doubao-seedance-2-0-mini", "total_tokens": 100_000})

        sf = get_session_factory()
        async with sf() as session:
            costs = await _compute(session, ["t-mini"])
        assert costs["t-mini"][0] == pytest.approx(100 * 0.0092)

    @pytest.mark.anyio
    async def test_default_seedance_provider_level(self, db) -> None:
        from deerflow.persistence.engine import get_session_factory

        await _seed_family_rows()
        await _add_external_call(task_id="t-2-0", provider="seedance", metadata={"model": "seedance", "total_tokens": 100_000})

        sf = get_session_factory()
        async with sf() as session:
            costs = await _compute(session, ["t-2-0"])
        assert costs["t-2-0"][0] == pytest.approx(100 * 0.046)

    @pytest.mark.anyio
    async def test_legacy_qihu_row_priced_via_alias(self, db) -> None:
        from deerflow.persistence.engine import get_session_factory

        await _add_media_pricing("minimax-h3", "video", "per_second", 0.4, model_id="minimax/MiniMax-H3")
        await _add_external_call(task_id="t-h3", provider="qihu", metadata={"model": "minimax-h3", "duration": 8.0})

        sf = get_session_factory()
        async with sf() as session:
            costs = await _compute(session, ["t-h3"])
        assert costs["t-h3"][0] == pytest.approx(8.0 * 0.4)


async def _compute(session, task_ids):
    from app.gateway._shared.task_cost import compute_task_costs

    return await compute_task_costs(session, task_ids)


# ── 5. C2 迁移：老表重建 + seedance 系模型级行 ─────────────────────────────


_LEGACY_DDL = """
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


def _load_c2_migration_module():
    path = Path(__file__).resolve().parent.parent / "packages" / "harness" / "deerflow" / "persistence" / "migrations" / "versions" / "k3d7f9e8a1b5_add_model_id_to_media_pricing.py"
    assert path.exists(), f"C2 migration file missing: {path}"
    spec = importlib.util.spec_from_file_location("c2_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestC2Migration:
    def _legacy_engine(self):
        engine = sa.create_engine("sqlite://")
        with engine.begin() as conn:
            conn.execute(sa.text(_LEGACY_DDL))
            conn.execute(
                sa.text(
                    "INSERT INTO media_pricing (provider, media_type, unit, unit_price, currency, updated_by) VALUES "
                    "('seedance', 'video', 'per_kilo_token', 0.046, 'CNY', 'c0-migration'), "
                    "('seedance-ark', 'video', 'per_kilo_token', 0.046, 'CNY', 'c0-migration'), "
                    "('gpt-image-2', 'image', 'per_image', 0.6, 'CNY', 'operator')"
                )
            )
        return engine

    def test_migration_adds_model_id_and_seeds_family_rows(self) -> None:
        mod = _load_c2_migration_module()
        engine = self._legacy_engine()
        with engine.begin() as conn:
            mod.apply_schema_and_seed(conn)

        rows = engine.connect().execute(sa.text("SELECT provider, model_id, unit_price FROM media_pricing ORDER BY provider, model_id")).all()
        by_key = {(r[0], r[1]): r[2] for r in rows}
        # 老行保留为 provider 级（model_id NULL）
        assert by_key[("seedance", None)] == pytest.approx(0.046)
        assert by_key[("seedance-ark", None)] == pytest.approx(0.046)
        assert by_key[("gpt-image-2", None)] == pytest.approx(0.6)
        # §一 已核实 seedance 系模型级行插入
        assert by_key[("seedance", "bytedance/seedance-2-0-fast")] == pytest.approx(0.02775)
        assert by_key[("seedance", "bytedance/doubao-seedance-2-0-mini")] == pytest.approx(0.0092)
        assert by_key[("seedance-ark", "doubao-seedance-2-0-260128")] == pytest.approx(0.046)

    def test_migration_idempotent(self) -> None:
        mod = _load_c2_migration_module()
        engine = self._legacy_engine()
        with engine.begin() as conn:
            mod.apply_schema_and_seed(conn)
        with engine.begin() as conn:
            mod.apply_schema_and_seed(conn)  # 重放不炸、不重播种子
        n = engine.connect().execute(sa.text("SELECT COUNT(*) FROM media_pricing")).scalar()
        assert n == 6

    def test_migration_provider_level_unique_enforced(self) -> None:
        mod = _load_c2_migration_module()
        engine = self._legacy_engine()
        with engine.begin() as conn:
            mod.apply_schema_and_seed(conn)
        with pytest.raises(Exception, match="UNIQUE|Integrity"):
            with engine.begin() as conn:
                conn.execute(sa.text("INSERT INTO media_pricing (provider, media_type, unit, unit_price, currency, updated_by) VALUES ('seedance', 'video', 'per_kilo_token', 9.0, 'CNY', 'x')"))

    def test_revision_chain(self) -> None:
        mod = _load_c2_migration_module()
        assert mod.revision == "k3d7f9e8a1b5"
        assert mod.down_revision == "j2f6a8b0c1d7"


# ── 6. admin 契约：model_id 读写 ────────────────────────────────────────────


def _admin_client():
    from _router_auth_helpers import make_authed_test_app

    from app.gateway.auth.models import User
    from app.gateway.routers import media_pricing

    admin_id = "33333333-3333-3333-3333-333333333333"

    def factory() -> User:
        return User(email="a@example.com", password_hash="x", system_role="admin", id=admin_id)

    from fastapi.testclient import TestClient

    app = make_authed_test_app(user_factory=factory)
    app.include_router(media_pricing.router)
    return TestClient(app)


class TestAdminContractModelId:
    @pytest.mark.anyio
    async def test_put_with_model_id_round_trips(self, db) -> None:
        payload = {
            "pricing": [
                {"provider": "seedance", "media_type": "video", "unit": "per_kilo_token", "unit_price": 0.046},
                {"provider": "seedance", "model_id": "bytedance/seedance-2-0-fast", "media_type": "video", "unit": "per_kilo_token", "unit_price": 0.02775},
            ]
        }
        with _admin_client() as client:
            assert client.put("/api/admin/media-pricing", json=payload).status_code == 200
            body = client.get("/api/admin/media-pricing").json()

        entries = {(e["provider"], e.get("model_id")): e for e in body["pricing"]}
        assert entries[("seedance", None)]["unit_price"] == pytest.approx(0.046)
        assert entries[("seedance", "bytedance/seedance-2-0-fast")]["unit_price"] == pytest.approx(0.02775)

    @pytest.mark.anyio
    async def test_put_duplicate_model_key_422(self, db) -> None:
        payload = {
            "pricing": [
                {"provider": "seedance", "model_id": "bytedance/seedance-2-0-fast", "media_type": "video", "unit": "per_kilo_token", "unit_price": 0.02775},
                {"provider": "seedance", "model_id": "bytedance/seedance-2-0-fast", "media_type": "video", "unit": "per_kilo_token", "unit_price": 0.03},
            ]
        }
        with _admin_client() as client:
            resp = client.put("/api/admin/media-pricing", json=payload)
        assert resp.status_code == 422, "payload 内重复 (provider, model_id, media_type) 必须 422 而非 500"
