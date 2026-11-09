"""C4（2026-08-27 成本可信化批次）：真账/估算来源标注透出。

- ``compute_task_costs`` 返回值扩为 ``(cost, currency, amount_source)``——
  real（全真账）/ estimated（全估算）/ mixed（两者皆有）/ None（未计价）；
- ``TaskBrief.amount_source`` 随 ``attach_task_costs`` 事后赋值；
- usage 响应新增 ``media_cost_by_provider_split``（per-provider real/estimated
  拆分，喂前端分色与"估"标）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from _router_auth_helpers import make_authed_test_app
from fastapi.testclient import TestClient

from app.gateway.auth.models import User
from app.gateway.routers import console

ADMIN_ID = "33333333-3333-3333-3333-333333333333"


@pytest.fixture()
async def db(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from deerflow.persistence.base import Base
    from deerflow.persistence.engine import close_engine, get_engine, init_engine

    monkeypatch.setattr(
        "app.gateway.routers.console.get_app_config",
        lambda: SimpleNamespace(models=[SimpleNamespace(name="m", model="vendor/m")]),
    )
    await init_engine("sqlite", url=f"sqlite+aiosqlite:///{tmp_path / 'amount_source.db'}", sqlite_dir=str(tmp_path))
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    console.invalidate_media_pricing_cache()
    yield
    await close_engine()


def _client() -> TestClient:
    def factory() -> User:
        return User(email="a@example.com", password_hash="x", system_role="admin", id=ADMIN_ID)

    app = make_authed_test_app(user_factory=factory)
    app.include_router(console.router)
    return TestClient(app)


async def _add_media_pricing(provider: str, media_type: str, unit: str, unit_price: float) -> None:
    from deerflow.persistence.engine import get_session_factory
    from deerflow.persistence.media_pricing.model import MediaPricingRow

    sf = get_session_factory()
    async with sf() as session:
        session.add(MediaPricingRow(provider=provider, model_id=None, media_type=media_type, unit=unit, unit_price=unit_price, currency="CNY"))
        await session.commit()
    console.invalidate_media_pricing_cache()


async def _add_media_call(*, task_id: str, provider: str = "seedance", metadata: dict | None = None, created_days_ago: int = 0) -> None:
    from deerflow.persistence.engine import get_session_factory
    from deerflow.persistence.external_api_calls.model import ExternalApiCallRow

    sf = get_session_factory()
    now = datetime.now(UTC)
    async with sf() as session:
        session.add(
            ExternalApiCallRow(
                call_id=f"call-{uuid4().hex[:12]}",
                user_id=ADMIN_ID,
                task_id=task_id,
                provider=provider,
                method="generate_video",
                status="ok",
                metadata_json=metadata,
                created_at=now,
            )
        )
        await session.commit()


async def _compute(task_ids: list[str]):
    from app.gateway._shared.task_cost import compute_task_costs
    from deerflow.persistence.engine import get_session_factory

    sf = get_session_factory()
    async with sf() as session:
        return await compute_task_costs(session, task_ids)


class TestTaskCostAmountSource:
    @pytest.mark.anyio
    async def test_real_only_task(self, db) -> None:
        await _add_media_call(task_id="t-real", metadata={"amount": 1.0})
        costs = await _compute(["t-real"])
        assert costs["t-real"][0] == pytest.approx(1.0)
        assert costs["t-real"][2] == "real"

    @pytest.mark.anyio
    async def test_estimated_only_task(self, db) -> None:
        await _add_media_pricing("seedance", "video", "per_kilo_token", 0.046)
        await _add_media_call(task_id="t-est", metadata={"model": "seedance", "total_tokens": 50_000})
        costs = await _compute(["t-est"])
        assert costs["t-est"][0] == pytest.approx(2.3)
        assert costs["t-est"][2] == "estimated"

    @pytest.mark.anyio
    async def test_mixed_task(self, db) -> None:
        await _add_media_pricing("seedance", "video", "per_kilo_token", 0.046)
        await _add_media_call(task_id="t-mix", metadata={"amount": 1.0})
        await _add_media_call(task_id="t-mix", metadata={"model": "seedance", "total_tokens": 50_000})
        costs = await _compute(["t-mix"])
        assert costs["t-mix"][2] == "mixed"

    @pytest.mark.anyio
    async def test_unpriced_task_source_none(self, db) -> None:
        await _add_media_call(task_id="t-none", provider="mystery", metadata={})
        costs = await _compute(["t-none"])
        assert costs["t-none"] == (None, None, None)

    @pytest.mark.anyio
    async def test_attach_sets_brief_amount_source(self, db) -> None:
        from app.gateway._shared.task_cost import attach_task_costs
        from app.gateway._shared.task_framework.schemas import TaskBrief

        await _add_media_call(task_id="t-brief", metadata={"amount": 2.0})
        brief = TaskBrief(id="t-brief", user_id="u", domain="video_gen", task_type="ai_video", status="ready", progress=100)
        await attach_task_costs([brief])
        assert brief.cost == pytest.approx(2.0)
        assert brief.amount_source == "real"

    @pytest.mark.anyio
    async def test_attach_sets_production_response_amount_source(self, db) -> None:
        """回归：attach_task_costs 对 voice_twin ProductionTaskResponse 赋值
        amount_source 不得 ValueError（pydantic 禁写未声明字段——修前列表/
        详情端点 500）。"""
        from app.gateway._shared.task_cost import attach_task_costs
        from app.gateway.voice_twin.production_schemas import ProductionTaskResponse

        await _add_media_call(task_id="t-prod", metadata={"amount": 3.0})
        resp = ProductionTaskResponse(id="t-prod", user_id="u", task_type="ppt", status="ready", progress=100)
        await attach_task_costs([resp])
        assert resp.cost == pytest.approx(3.0)
        assert resp.amount_source == "real"


class TestUsageProviderSplit:
    @pytest.mark.anyio
    async def test_media_cost_by_provider_split(self, db) -> None:
        await _add_media_pricing("seedance", "video", "per_kilo_token", 0.046)
        await _add_media_call(task_id="t-split", metadata={"amount": 1.0})
        await _add_media_call(task_id="t-split", metadata={"model": "seedance", "total_tokens": 100_000})
        with _client() as client:
            body = client.get("/api/console/usage?days=14").json()
        split = body["media_cost_by_provider_split"]["seedance"]
        assert split["real"] == pytest.approx(1.0)
        assert split["estimated"] == pytest.approx(4.6)
        # 老字段（和值）不动
        assert body["media_cost_by_provider"]["seedance"] == pytest.approx(5.6)
