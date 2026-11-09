"""C7（2026-08-27 成本可信化批次）：价格管理端加固。

A6/A12 用户拍板的三条防线 + 换币死锁修复：
- **幅度告警**：仅对已有键的更新生效--``max(新,旧)/min(新,旧) > 5`` 需
  ``confirm: true`` 二次确认；新键（无旧值）直接放行；≤5× 直接放行；
- **PATCH/DELETE 单项**：不再只有全量替换 PUT；两路径与 PUT 一样调
  ``invalidate_media_pricing_cache()``（漏了最长 60s 旧价）；
- **跨表币种一致性**：media 价目与 LLM model_pricing 已存币种不一致 -> 422；
- **换币死锁修复**：model_pricing 全量替换允许换币（docstring 本就承诺，
  修前 :157-161 一律拒绝，换币永远走不通）。
"""

from __future__ import annotations

import pytest
from _router_auth_helpers import make_authed_test_app
from fastapi.testclient import TestClient

from app.gateway.auth.models import User
from app.gateway.routers import console, media_pricing

ADMIN_ID = "33333333-3333-3333-3333-333333333333"


@pytest.fixture()
async def db(tmp_path):
    from deerflow.persistence.base import Base
    from deerflow.persistence.engine import close_engine, get_engine, init_engine

    await init_engine("sqlite", url=f"sqlite+aiosqlite:///{tmp_path / 'hardening.db'}", sqlite_dir=str(tmp_path))
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    console.invalidate_media_pricing_cache()
    yield
    await close_engine()


def _client():
    def factory() -> User:
        return User(email="a@example.com", password_hash="x", system_role="admin", id=ADMIN_ID)

    app = make_authed_test_app(user_factory=factory)
    app.include_router(media_pricing.router)
    return TestClient(app)


def _row(provider: str, model_id: str | None, media_type: str, price: float, unit: str = "per_kilo_token") -> dict:
    entry = {"provider": provider, "media_type": media_type, "unit": unit, "unit_price": price, "currency": "CNY"}
    if model_id is not None:
        entry["model_id"] = model_id
    return entry


def _seed_existing(client: TestClient, price: float) -> None:
    resp = client.put("/api/admin/media-pricing", json={"pricing": [_row("seedance", None, "video", price)]})
    assert resp.status_code == 200, resp.text


class TestMagnitudeWarning:
    @pytest.mark.anyio
    async def test_update_over_5x_without_confirm_422(self, db) -> None:
        with _client() as client:
            _seed_existing(client, 0.046)
            resp = client.put(
                "/api/admin/media-pricing",
                json={"pricing": [_row("seedance", None, "video", 1.5)]},
            )
        assert resp.status_code == 422, "已有键 >5× 偏差（0.046->1.5 ≈ 33×）无 confirm 必须 422"
        assert "confirm" in resp.json()["detail"].lower()

    @pytest.mark.anyio
    async def test_update_over_5x_with_confirm_passes(self, db) -> None:
        with _client() as client:
            _seed_existing(client, 0.046)
            resp = client.put(
                "/api/admin/media-pricing",
                json={"pricing": [_row("seedance", None, "video", 1.5)], "confirm": True},
            )
        assert resp.status_code == 200

    @pytest.mark.anyio
    async def test_update_within_5x_passes_without_confirm(self, db) -> None:
        with _client() as client:
            _seed_existing(client, 0.046)
            resp = client.put(
                "/api/admin/media-pricing",
                json={"pricing": [_row("seedance", None, "video", 0.1)]},  # 0.1/0.046 ≈ 2.2×
            )
        assert resp.status_code == 200

    @pytest.mark.anyio
    async def test_new_key_passes_without_confirm(self, db) -> None:
        """A12：新键（无旧值）直接放行--40 倍错价的种子价就位时首次校准不该被拦。"""
        with _client() as client:
            resp = client.put(
                "/api/admin/media-pricing",
                json={"pricing": [_row("gpt-image-2", None, "image", 0.204, unit="per_kilo_token")]},
            )
        assert resp.status_code == 200

    @pytest.mark.anyio
    async def test_price_drop_over_5x_also_needs_confirm(self, db) -> None:
        """max/min 对称--大幅**降**价同样要确认（可能是多打/少打一个 0）。"""
        with _client() as client:
            _seed_existing(client, 1.5)
            resp = client.put(
                "/api/admin/media-pricing",
                json={"pricing": [_row("seedance", None, "video", 0.046)]},
            )
        assert resp.status_code == 422


class TestPatchDeleteSingle:
    @pytest.mark.anyio
    async def test_patch_upserts_single_row(self, db) -> None:
        with _client() as client:
            _seed_existing(client, 0.046)
            resp = client.patch(
                "/api/admin/media-pricing",
                json={"entry": _row("seedance", None, "video", 0.05)},
            )
            assert resp.status_code == 200, resp.text
            body = client.get("/api/admin/media-pricing").json()
            seedance = [e for e in body["pricing"] if e["provider"] == "seedance" and e.get("model_id") is None]
            assert len(seedance) == 1
            assert seedance[0]["unit_price"] == pytest.approx(0.05)

    @pytest.mark.anyio
    async def test_patch_invalidates_cache(self, db) -> None:
        """PATCH 必须清进程缓存（否则估算最长 60s 用旧价）。

        失效语义 = cache 清空，下一次 async load 重读 DB 取新价--不是原地改写。
        """
        with _client() as client:
            _seed_existing(client, 0.046)
        flat = await console._load_media_pricing()  # warm
        assert flat.get(("seedance", "video"), {}).get("unit_price") == pytest.approx(0.046)
        with _client() as client:
            resp = client.patch("/api/admin/media-pricing", json={"entry": _row("seedance", None, "video", 0.092)})
            assert resp.status_code == 200, resp.text
        assert console._load_media_pricing_sync() == {}, "PATCH 后进程缓存必须已清空"
        flat2 = await console._load_media_pricing()
        assert flat2[("seedance", "video")]["unit_price"] == pytest.approx(0.092)

    @pytest.mark.anyio
    async def test_patch_magnitude_warning_applies(self, db) -> None:
        with _client() as client:
            _seed_existing(client, 0.046)
            resp = client.patch(
                "/api/admin/media-pricing",
                json={"entry": _row("seedance", None, "video", 1.5)},
            )
            assert resp.status_code == 422
            resp2 = client.patch(
                "/api/admin/media-pricing",
                json={"entry": _row("seedance", None, "video", 1.5), "confirm": True},
            )
            assert resp2.status_code == 200

    @pytest.mark.anyio
    async def test_delete_removes_single_row_and_invalidates(self, db) -> None:
        with _client() as client:
            _seed_existing(client, 0.046)
            client.put(
                "/api/admin/media-pricing",
                json={"pricing": [_row("seedance", None, "video", 0.046), _row("seedance", "bytedance/seedance-2-0-fast", "video", 0.02775)]},
            )
            resp = client.delete(
                "/api/admin/media-pricing",
                params={"provider": "seedance", "model_id": "bytedance/seedance-2-0-fast", "media_type": "video"},
            )
            assert resp.status_code == 200, resp.text
            body = client.get("/api/admin/media-pricing").json()
            fast = [e for e in body["pricing"] if e.get("model_id") == "bytedance/seedance-2-0-fast"]
            assert fast == [], "DELETE 单项生效"
            provider_level = [e for e in body["pricing"] if e["provider"] == "seedance" and e.get("model_id") is None]
            assert len(provider_level) == 1, "其余行不受影响"

    @pytest.mark.anyio
    async def test_delete_provider_level_without_model_id(self, db) -> None:
        with _client() as client:
            _seed_existing(client, 0.046)
            resp = client.delete(
                "/api/admin/media-pricing",
                params={"provider": "seedance", "media_type": "video"},
            )
            assert resp.status_code == 200
            # 响应体是纯 DB 行（无 default 兜底层）--seedance 必须已消失。
            assert [e for e in resp.json()["pricing"] if e["provider"] == "seedance"] == []
            # GET 会再显示 default 兜底行（source="default"），DB 行确已删除。
            body = client.get("/api/admin/media-pricing").json()
            db_rows = [e for e in body["pricing"] if e["provider"] == "seedance" and e["source"] == "db"]
            assert db_rows == []


class TestCrossTableCurrency:
    @pytest.mark.anyio
    async def test_media_currency_conflicting_model_pricing_422(self, db) -> None:
        from deerflow.persistence.engine import get_session_factory
        from deerflow.persistence.model_pricing.model import ModelPricingRow

        sf = get_session_factory()
        async with sf() as session:
            session.add(ModelPricingRow(model_name="m", currency="CNY", input_per_million=10.0, output_per_million=0.0))
            await session.commit()

        with _client() as client:
            resp = client.put(
                "/api/admin/media-pricing",
                json={"pricing": [{**_row("seedance", None, "video", 0.046), "currency": "USD"}]},
            )
        assert resp.status_code == 422, "media 与 LLM model_pricing 币种不一致须 422"
        assert "currency" in resp.json()["detail"].lower()

    @pytest.mark.anyio
    async def test_media_matching_currency_passes(self, db) -> None:
        from deerflow.persistence.engine import get_session_factory
        from deerflow.persistence.model_pricing.model import ModelPricingRow

        sf = get_session_factory()
        async with sf() as session:
            session.add(ModelPricingRow(model_name="m", currency="CNY", input_per_million=10.0, output_per_million=0.0))
            await session.commit()

        with _client() as client:
            resp = client.put("/api/admin/media-pricing", json={"pricing": [_row("seedance", None, "video", 0.046)]})
        assert resp.status_code == 200


class TestModelPricingCurrencySwitch:
    """换币死锁修复（model_pricing.py:157-161 修前全量替换也拒换币）。"""

    @pytest.fixture()
    def model_client(self):
        from app.gateway.routers import model_pricing

        def factory() -> User:
            return User(email="a@example.com", password_hash="x", system_role="admin", id=ADMIN_ID)

        app = make_authed_test_app(user_factory=factory)
        app.include_router(model_pricing.router)
        return TestClient(app)

    @pytest.mark.anyio
    async def test_full_replace_may_switch_currency(self, db, model_client) -> None:
        with model_client as client:
            first = client.put(
                "/api/admin/model-pricing",
                json={"pricing": {"m": {"currency": "CNY", "input_per_million": 8.0, "output_per_million": 32.0}}},
            )
            assert first.status_code == 200
            # 全量替换换币：docstring 承诺的合法操作，修前被 422 拒死。
            switched = client.put(
                "/api/admin/model-pricing",
                json={"pricing": {"m": {"currency": "USD", "input_per_million": 1.2, "output_per_million": 4.8}}},
            )
        assert switched.status_code == 200, switched.text
