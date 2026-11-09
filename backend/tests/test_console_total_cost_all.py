"""C3+C5（2026-08-27 成本可信化批次）：总花费口径合并 + 聚合收口。

C3：
- ``GET /api/console/stats`` 新增 ``total_cost_all``（LLM+媒体）与
  ``cost_breakdown {llm, media}``——老 ``total_cost``（LLM-only）不动（A12 增量字段）；
- 无媒体时 ``total_cost_all == total_cost``；媒体估算计入（真账优先）。

C5：
- 共享聚合 ``aggregate_media_cost(session, *, scope, window_start, include_estimates,
  include_tts)``——含估算与 TTS（对照旧「只算 amount 行」口径的差值即漏项证据）；
- 三条链同源：stats ``total_cost_all`` == usage ``total_cost_all`` == external-calls
  面板 Σprovider（real+est）；
- stats 新增 ``days`` 查询参数：不传=全时段（向后兼容），传了按窗口过滤
  （统计卡随天数选择器，A5/A17）；
- ``unpriced_calls`` 透出到 stats 与 usage 响应。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
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
    await init_engine("sqlite", url=f"sqlite+aiosqlite:///{tmp_path / 'total_cost_all.db'}", sqlite_dir=str(tmp_path))
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


async def _add_llm_run(*, thread_id: str, input_tokens: int = 1_000_000, created_days_ago: int = 0) -> None:
    """一条 llm_bookkeeping 行（10 元/百万 输入）——经 model_pricing 表定价。"""
    from deerflow.persistence.engine import get_session_factory
    from deerflow.persistence.model_pricing.model import ModelPricingRow
    from deerflow.persistence.run.model import RunRow

    sf = get_session_factory()
    now = datetime.now(UTC)
    async with sf() as session:
        session.add(
            RunRow(
                run_id=f"run-{uuid4().hex[:8]}",
                thread_id=thread_id,
                user_id=ADMIN_ID,
                status="success",
                operation_kind="llm_bookkeeping",
                model_name="vendor/m",
                total_input_tokens=input_tokens,
                total_output_tokens=0,
                total_tokens=input_tokens,
                created_at=now - timedelta(days=created_days_ago),
                updated_at=now,
            )
        )
        # 幂等写价（同任务多 run 复用）
        from sqlalchemy import select

        existing = (await session.execute(select(ModelPricingRow).where(ModelPricingRow.model_name == "m"))).scalars().first()
        if existing is None:
            session.add(ModelPricingRow(model_name="m", currency="CNY", input_per_million=10.0, output_per_million=0.0))
        await session.commit()


async def _add_media_call(*, provider: str = "seedance", method: str = "generate_video", status: str = "ok", metadata: dict | None = None, created_days_ago: int = 0, user_id: str = ADMIN_ID) -> None:
    from deerflow.persistence.engine import get_session_factory
    from deerflow.persistence.external_api_calls.model import ExternalApiCallRow

    sf = get_session_factory()
    now = datetime.now(UTC)
    async with sf() as session:
        session.add(
            ExternalApiCallRow(
                call_id=f"call-{uuid4().hex[:12]}",
                user_id=user_id,
                task_id=None,
                provider=provider,
                method=method,
                status=status,
                metadata_json=metadata,
                created_at=now - timedelta(days=created_days_ago),
            )
        )
        await session.commit()


async def _add_media_pricing(provider: str, media_type: str, unit: str, unit_price: float) -> None:
    from deerflow.persistence.engine import get_session_factory
    from deerflow.persistence.media_pricing.model import MediaPricingRow

    sf = get_session_factory()
    async with sf() as session:
        session.add(MediaPricingRow(provider=provider, model_id=None, media_type=media_type, unit=unit, unit_price=unit_price, currency="CNY"))
        await session.commit()
    console.invalidate_media_pricing_cache()


# ── C3：stats total_cost_all / cost_breakdown ───────────────────────────────


class TestStatsTotalCostAll:
    @pytest.mark.anyio
    async def test_total_cost_all_sums_llm_and_media(self, db) -> None:
        await _add_llm_run(thread_id="t1")  # ¥10
        await _add_media_pricing("seedance", "video", "per_kilo_token", 0.046)
        await _add_media_call(metadata={"model": "seedance", "total_tokens": 100_000})  # ¥4.6 估算

        with _client() as client:
            body = client.get("/api/console/stats").json()

        # 老字段=对话 run 口径（本测试只有 bookkeeping 行 → 0.0，A12 不动）
        assert body["total_cost"] == pytest.approx(0.0)
        # 新字段=全链路（对话 + llm_bookkeeping + 媒体，A7）
        assert body["total_cost_all"] == pytest.approx(14.6)
        assert body["cost_breakdown"]["llm"] == pytest.approx(10.0)
        assert body["cost_breakdown"]["media"] == pytest.approx(4.6)

    @pytest.mark.anyio
    async def test_no_media_equals_total_cost(self, db) -> None:
        """只有对话 run、无媒体、无 bookkeeping → total_cost_all == 老 total_cost。"""
        from sqlalchemy import select

        from deerflow.persistence.engine import get_session_factory
        from deerflow.persistence.model_pricing.model import ModelPricingRow
        from deerflow.persistence.run.model import RunRow

        sf = get_session_factory()
        now = datetime.now(UTC)
        async with sf() as session:
            if (await session.execute(select(ModelPricingRow).where(ModelPricingRow.model_name == "m"))).scalars().first() is None:
                session.add(ModelPricingRow(model_name="m", currency="CNY", input_per_million=10.0, output_per_million=0.0))
            session.add(
                RunRow(
                    run_id=f"run-{uuid4().hex[:8]}",
                    thread_id="t-chat-thread",
                    user_id=ADMIN_ID,
                    status="success",
                    operation_kind="run",
                    model_name="vendor/m",
                    total_input_tokens=1_000_000,
                    total_output_tokens=0,
                    total_tokens=1_000_000,
                    created_at=now,
                    updated_at=now,
                )
            )
            await session.commit()
        with _client() as client:
            body = client.get("/api/console/stats").json()
        assert body["total_cost_all"] == pytest.approx(body["total_cost"])  # 各 ¥10
        assert body["total_cost"] == pytest.approx(10.0)
        assert body["cost_breakdown"]["llm"] == pytest.approx(10.0)
        assert body["cost_breakdown"]["media"] == pytest.approx(0.0)

    @pytest.mark.anyio
    async def test_media_real_bill_wins_over_estimate(self, db) -> None:
        """真账优先：amount 直接计账，估算不叠加。"""
        await _add_media_pricing("seedance", "video", "per_kilo_token", 0.046)
        await _add_media_call(metadata={"amount": 1.5, "total_tokens": 100_000})
        with _client() as client:
            body = client.get("/api/console/stats").json()
        assert body["cost_breakdown"]["media"] == pytest.approx(1.5)

    @pytest.mark.anyio
    async def test_usage_response_carries_same_fields(self, db) -> None:
        """usage（趋势）响应同字段同口径——AC6 三处同源的前置。"""
        await _add_llm_run(thread_id="t3")
        await _add_media_pricing("seedance", "video", "per_kilo_token", 0.046)
        await _add_media_call(metadata={"model": "seedance", "total_tokens": 50_000})
        with _client() as client:
            body = client.get("/api/console/usage?days=14").json()
        assert body["total_cost"] == pytest.approx(10.0)
        assert body["total_cost_all"] == pytest.approx(10.0 + 2.3)
        assert body["cost_breakdown"]["media"] == pytest.approx(2.3)


# ── C5：共享聚合 + 三链同源 + days 窗口 + unpriced ─────────────────────────


class TestAggregateMediaCost:
    @pytest.mark.anyio
    async def test_includes_estimates_and_tts_vs_amount_only(self, db) -> None:
        """含估算+TTS vs 只算 amount 行——差值即旧口径漏项证据。"""
        from app.gateway._shared.task_cost import aggregate_media_cost
        from deerflow.persistence.engine import get_session_factory

        await _add_media_pricing("seedance", "video", "per_kilo_token", 0.046)
        await _add_media_pricing("qihu_tts", "tts", "per_kilo_char", 0.3)
        await _add_media_call(metadata={"amount": 1.0})  # 真账
        await _add_media_call(metadata={"model": "seedance", "total_tokens": 100_000})  # 估算 ¥4.6
        await _add_media_call(method="synthesize_sse", provider="qihu_tts", metadata={"char_count": 2000})  # TTS ¥0.6

        sf = get_session_factory()
        async with sf() as session:
            full = await aggregate_media_cost(session, scope=console._Scope(is_admin=True, filter_user_id=None))
            real_only = await aggregate_media_cost(session, scope=console._Scope(is_admin=True, filter_user_id=None), include_estimates=False)

        assert real_only["total"] == pytest.approx(1.0), "amount-only 口径=1.0"
        assert full["total"] == pytest.approx(1.0 + 4.6 + 0.6), "全口径含估算与 TTS"
        assert full["total_estimated"] == pytest.approx(5.2)
        assert full["by_provider"]["seedance"]["cost_estimated"] == pytest.approx(4.6)

    @pytest.mark.anyio
    async def test_unpriced_and_failed_semantics(self, db) -> None:
        """unpriced=可计费但无价（可见计数）；失败行不计费不计 unpriced。"""
        from app.gateway._shared.task_cost import aggregate_media_cost
        from deerflow.persistence.engine import get_session_factory

        await _add_media_call(provider="no-such-provider", metadata={})  # unpriced
        await _add_media_call(provider="seedance", status="error", metadata={})  # 失败
        await _add_media_call(method="voice_clone_upload", provider="volcengine", metadata={})  # 免费

        sf = get_session_factory()
        async with sf() as session:
            agg = await aggregate_media_cost(session, scope=console._Scope(is_admin=True, filter_user_id=None))
        assert agg["unpriced_calls"] == 1
        assert agg["total"] == pytest.approx(0.0)


class TestThreeChainsSameSource:
    @pytest.mark.anyio
    async def test_stats_usage_external_calls_agree(self, db) -> None:
        """AC6：三处总成本数字相等（同窗口、同 scope）。"""
        await _add_llm_run(thread_id="t4")
        await _add_media_pricing("seedance", "video", "per_kilo_token", 0.046)
        await _add_media_call(metadata={"amount": 1.0})
        await _add_media_call(metadata={"model": "seedance", "total_tokens": 100_000})

        with _client() as client:
            stats = client.get("/api/console/stats?days=14").json()
            usage = client.get("/api/console/usage?days=14").json()
            ext = client.get("/api/console/external-calls?days=14").json()

        ext_total = sum((p["cost"] or 0.0) for p in ext["by_provider"])
        assert stats["total_cost_all"] == pytest.approx(10.0 + 1.0 + 4.6)
        assert usage["total_cost_all"] == pytest.approx(stats["total_cost_all"])
        assert ext_total == pytest.approx(1.0 + 4.6), "外部调用面板 Σprovider(real+est) 与媒体分量一致"


class TestStatsDaysWindow:
    @pytest.mark.anyio
    async def test_days_filters_media_and_llm(self, db) -> None:
        await _add_llm_run(thread_id="t-old", created_days_ago=30)  # 窗口外
        await _add_llm_run(thread_id="t-new", created_days_ago=1)
        await _add_media_pricing("seedance", "video", "per_kilo_token", 0.046)
        await _add_media_call(metadata={"model": "seedance", "total_tokens": 100_000}, created_days_ago=30)  # 窗口外
        await _add_media_call(metadata={"model": "seedance", "total_tokens": 50_000}, created_days_ago=1)

        with _client() as client:
            w7 = client.get("/api/console/stats?days=7").json()
            full = client.get("/api/console/stats").json()

        assert w7["cost_breakdown"]["llm"] == pytest.approx(10.0), "窗口外 LLM 不计"
        assert w7["cost_breakdown"]["media"] == pytest.approx(2.3), "窗口外媒体不计"
        assert full["cost_breakdown"]["llm"] == pytest.approx(20.0), "不传 days=全时段（向后兼容）"
        assert full["cost_breakdown"]["media"] == pytest.approx(6.9)

    @pytest.mark.anyio
    async def test_stats_days_matches_usage_days(self, db) -> None:
        """同 days 两端点同口径（统计卡随选择器，A5）。"""
        await _add_llm_run(thread_id="t5", created_days_ago=2)
        with _client() as client:
            s7 = client.get("/api/console/stats?days=7").json()
            u7 = client.get("/api/console/usage?days=7").json()
        assert s7["cost_breakdown"]["llm"] == pytest.approx(u7["cost_breakdown"]["llm"])


class TestUnpricedExposure:
    @pytest.mark.anyio
    async def test_stats_and_usage_expose_unpriced(self, db) -> None:
        await _add_media_call(provider="mystery", metadata={})
        with _client() as client:
            stats = client.get("/api/console/stats").json()
            usage = client.get("/api/console/usage?days=14").json()
        assert stats["unpriced_calls"] == 1
        assert usage["unpriced_calls"] == 1
