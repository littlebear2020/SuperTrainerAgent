"""批次 C Phase B/C (2026-08-13)：每任务成本（LLM token + 媒体 API）。

``compute_task_costs`` 从 runs 表 ``operation_kind="llm_bookkeeping"`` 行
（thread_id = task_id，llm_usage_sink 写入）聚合每任务 LLM 花费，走 console
定价 map（yaml + model_pricing DB 覆盖层，含 Phase A 别名）——用 config name
定价、bucket 用厂商 id 正是 doubao 暴露的别名缺口场景。Phase C 追加
external_api_calls.task_id 媒体成本（真账优先 + 估算 + 混币置 None）。

断言口径：计价任务 (cost, currency) 精确；未计价任务 (None, None)（区别于 0）。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.gateway._shared.llm_usage_sink import (
    AccountingContext,
    _accounting_ctx,
    record_external_api_call,
)
from app.gateway._shared.task_cost import attach_task_costs, compute_task_costs
from app.gateway._shared.task_framework.schemas import TaskBrief
from app.gateway.routers.console import invalidate_media_pricing_cache
from deerflow.persistence.external_api_calls.model import ExternalApiCallRow
from deerflow.persistence.media_pricing.model import MediaPricingRow
from deerflow.persistence.model_pricing.model import ModelPricingRow
from deerflow.persistence.run.model import RunRow

NAME = "test-model-name"
MODEL = "vendor/test-model"


@pytest.fixture()
async def db(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from deerflow.persistence.base import Base
    from deerflow.persistence.engine import close_engine, get_engine, init_engine

    monkeypatch.setattr(
        "app.gateway.routers.console.get_app_config",
        lambda: SimpleNamespace(models=[SimpleNamespace(name=NAME, model=MODEL)]),
    )
    await init_engine("sqlite", url=f"sqlite+aiosqlite:///{tmp_path / 'task_cost.db'}", sqlite_dir=str(tmp_path))
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    invalidate_media_pricing_cache()
    yield
    await close_engine()


async def _add_bookkeeping_run(
    *,
    run_id: str,
    thread_id: str,
    bucket_model: str,
    input_tokens: int = 1_000_000,
    output_tokens: int = 0,
) -> None:
    from deerflow.persistence.engine import get_session_factory

    sf = get_session_factory()
    now = datetime.now(UTC)
    async with sf() as session:
        session.add(
            RunRow(
                run_id=run_id,
                thread_id=thread_id,
                user_id="u-1",
                status="success",
                operation_kind="llm_bookkeeping",
                model_name=bucket_model,
                total_input_tokens=input_tokens,
                total_output_tokens=output_tokens,
                total_tokens=input_tokens + output_tokens,
                created_at=now - timedelta(hours=1),
                updated_at=now,
                token_usage_by_model={bucket_model: {"input_tokens": input_tokens, "output_tokens": output_tokens, "total_tokens": input_tokens + output_tokens}},
            )
        )
        await session.commit()


async def _write_pricing_row(model_name: str, input_per_million: float = 10.0) -> None:
    from deerflow.persistence.engine import get_session_factory

    sf = get_session_factory()
    async with sf() as session:
        session.add(
            ModelPricingRow(
                model_name=model_name,
                currency="CNY",
                input_per_million=input_per_million,
                output_per_million=0.0,
            )
        )
        await session.commit()


@pytest.mark.anyio
async def test_prices_bookkeeping_via_config_name_alias(db) -> None:
    """Phase A 别名路径：按 config name 写价，bucket 用厂商 id → 成本非零。"""
    from deerflow.persistence.engine import get_session_factory

    await _write_pricing_row(NAME, input_per_million=10.0)
    await _add_bookkeeping_run(run_id="r1", thread_id="task-1", bucket_model=MODEL, input_tokens=1_000_000)

    sf = get_session_factory()
    async with sf() as session:
        costs = await compute_task_costs(session, ["task-1"])

    assert costs["task-1"][0] == pytest.approx(10.0)
    assert costs["task-1"][1] == "CNY"


@pytest.mark.anyio
async def test_unpriced_task_is_none_not_zero(db) -> None:
    """无法计价的任务 cost=None（前端显示 "—"），不是 0（"免费"）。"""
    from deerflow.persistence.engine import get_session_factory

    await _add_bookkeeping_run(run_id="r2", thread_id="task-2", bucket_model="no-such-model", input_tokens=100)

    sf = get_session_factory()
    async with sf() as session:
        costs = await compute_task_costs(session, ["task-2"])

    assert costs["task-2"] == (None, None, None)


@pytest.mark.anyio
async def test_no_bookkeeping_rows_task_is_none(db) -> None:
    from deerflow.persistence.engine import get_session_factory

    sf = get_session_factory()
    async with sf() as session:
        costs = await compute_task_costs(session, ["task-never-ran"])

    assert costs["task-never-ran"] == (None, None, None)


@pytest.mark.anyio
async def test_multiple_runs_accumulate_per_task(db) -> None:
    """同一任务多条 bookkeeping 行累加；不同任务互不串账。"""
    from deerflow.persistence.engine import get_session_factory

    await _write_pricing_row(NAME, input_per_million=10.0)
    await _add_bookkeeping_run(run_id="r3a", thread_id="task-3", bucket_model=MODEL, input_tokens=1_000_000)
    await _add_bookkeeping_run(run_id="r3b", thread_id="task-3", bucket_model=MODEL, input_tokens=500_000)
    await _add_bookkeeping_run(run_id="r4", thread_id="task-4", bucket_model=MODEL, input_tokens=100_000)

    sf = get_session_factory()
    async with sf() as session:
        costs = await compute_task_costs(session, ["task-3", "task-4"])

    assert costs["task-3"][0] == pytest.approx(15.0)
    assert costs["task-4"][0] == pytest.approx(1.0)


@pytest.mark.anyio
async def test_attach_task_costs_assigns_fields(db) -> None:
    """attach_task_costs 对 TaskBrief 列表事后赋值 cost/currency。"""
    await _write_pricing_row(NAME, input_per_million=10.0)
    await _add_bookkeeping_run(run_id="r5", thread_id="task-5", bucket_model=MODEL, input_tokens=1_000_000)

    brief = TaskBrief(
        id="task-5",
        user_id="u-1",
        domain="video_gen",
        task_type="ai_image",
        status="ready",
        progress=100,
    )
    await attach_task_costs([brief])

    assert brief.cost == pytest.approx(10.0)
    assert brief.currency == "CNY"


@pytest.mark.anyio
async def test_attach_task_costs_unpriced_stays_none(db) -> None:
    brief = TaskBrief(
        id="task-6",
        user_id="u-1",
        domain="video_gen",
        task_type="ai_image",
        status="ready",
        progress=100,
    )
    await attach_task_costs([brief])

    assert brief.cost is None
    assert brief.currency is None


# ── Phase C: record_external_api_call task_id 写入 ─────────────────────────


async def _read_external_rows() -> list[ExternalApiCallRow]:
    from sqlalchemy import select

    from deerflow.persistence.engine import get_session_factory

    sf = get_session_factory()
    async with sf() as session:
        return (await session.execute(select(ExternalApiCallRow))).scalars().all()


@pytest.mark.anyio
async def test_record_external_api_call_writes_task_id_from_ctx(db) -> None:
    """ctx 存在时写入 task_id；ctx 外调用 task_id=None（音色克隆上传等正确行为）。"""
    # 泄漏源已修（test_llm_usage_sink.py / test_external_api_calls.py 均有 autouse
    # reset）；本测试自管 token 复位，不再需要开局防御性清空。
    token = _accounting_ctx.set(AccountingContext(task_id="task-ctx-1", user_id="u-1", domain="video_gen"))
    try:
        await record_external_api_call(provider="p1", method="generate_image", status="ok")
    finally:
        _accounting_ctx.reset(token)
    await record_external_api_call(provider="p1", method="voice_clone_upload", status="ok")

    rows = await _read_external_rows()
    by_method = {r.method: r for r in rows}
    assert by_method["generate_image"].task_id == "task-ctx-1"
    assert by_method["voice_clone_upload"].task_id is None, "ctx 外的调用 task_id 应为 None"


# ── Phase C: 媒体成本聚合 ──────────────────────────────────────────────────


async def _add_media_pricing(provider: str, media_type: str, unit: str, unit_price: float, currency: str = "CNY") -> None:
    from deerflow.persistence.engine import get_session_factory

    sf = get_session_factory()
    async with sf() as session:
        session.add(
            MediaPricingRow(
                provider=provider,
                media_type=media_type,
                unit=unit,
                unit_price=unit_price,
                currency=currency,
            )
        )
        await session.commit()
    invalidate_media_pricing_cache()


async def _add_external_call(*, task_id: str, method: str = "generate_image", status: str = "ok", metadata: dict | None = None, provider: str = "p1") -> None:
    from uuid import uuid4

    from deerflow.persistence.engine import get_session_factory

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


@pytest.mark.anyio
async def test_media_cost_real_bill_amount(db) -> None:
    """真账路径：metadata_json.amount 直接计账，不需要 media_pricing。"""
    from deerflow.persistence.engine import get_session_factory

    await _add_external_call(task_id="task-m1", metadata={"amount": 1.23})

    sf = get_session_factory()
    async with sf() as session:
        costs = await compute_task_costs(session, ["task-m1"])

    assert costs["task-m1"][0] == pytest.approx(1.23)
    assert costs["task-m1"][1] == "CNY"


@pytest.mark.anyio
async def test_media_cost_estimated_from_pricing(db) -> None:
    """估算路径：无 amount → per_image 定价估算；失败调用不估算。"""
    from deerflow.persistence.engine import get_session_factory

    await _add_media_pricing("p1", "image", "per_image", 2.5)
    await _add_external_call(task_id="task-m2", method="generate_image", status="ok")
    await _add_external_call(task_id="task-m2", method="generate_image", status="error")

    sf = get_session_factory()
    async with sf() as session:
        costs = await compute_task_costs(session, ["task-m2"])

    # 只计成功调用的一次 per_image
    assert costs["task-m2"][0] == pytest.approx(2.5)
    assert costs["task-m2"][1] == "CNY"


@pytest.mark.anyio
async def test_llm_and_media_costs_sum(db) -> None:
    """同币种 LLM + 媒体成本相加。"""
    from deerflow.persistence.engine import get_session_factory

    await _write_pricing_row(NAME, input_per_million=10.0)
    await _add_bookkeeping_run(run_id="r7", thread_id="task-m3", bucket_model=MODEL, input_tokens=1_000_000)
    await _add_media_pricing("p1", "image", "per_image", 2.5)
    await _add_external_call(task_id="task-m3", method="generate_image", status="ok")

    sf = get_session_factory()
    async with sf() as session:
        costs = await compute_task_costs(session, ["task-m3"])

    assert costs["task-m3"][0] == pytest.approx(12.5)
    assert costs["task-m3"][1] == "CNY"


@pytest.mark.anyio
async def test_mixed_currency_task_cost_is_none(db, caplog) -> None:
    """P3-1 混币：LLM CNY + 媒体 USD → warning + cost 置 None（不静默相加）。"""
    from deerflow.persistence.engine import get_session_factory

    await _write_pricing_row(NAME, input_per_million=10.0)
    await _add_bookkeeping_run(run_id="r8", thread_id="task-m4", bucket_model=MODEL, input_tokens=1_000_000)
    await _add_media_pricing("p1", "image", "per_image", 1.0, currency="USD")
    await _add_external_call(task_id="task-m4", method="generate_image", status="ok")

    sf = get_session_factory()
    async with sf() as session:
        costs = await compute_task_costs(session, ["task-m4"])

    assert costs["task-m4"] == (None, None, None)
    assert any("mixes currencies" in r.message for r in caplog.records), "混币必须留 warning"


@pytest.mark.anyio
async def test_media_rows_in_mixed_currencies_task_cost_is_none(db, caplog) -> None:
    """D2：媒体行之间混币——同任务两行媒体不同币 → (None, None) + warning。"""
    from deerflow.persistence.engine import get_session_factory

    await _add_media_pricing("p1", "image", "per_image", 1.0, currency="CNY")
    await _add_media_pricing("p2", "image", "per_image", 1.0, currency="USD")
    await _add_external_call(task_id="task-m5", provider="p1", method="generate_image", status="ok")
    await _add_external_call(task_id="task-m5", provider="p2", method="generate_image", status="ok")

    sf = get_session_factory()
    async with sf() as session:
        costs = await compute_task_costs(session, ["task-m5"])

    assert costs["task-m5"] == (None, None, None)
    assert any("mixes currencies" in r.message for r in caplog.records), "媒体行混币必须留 warning"


@pytest.mark.anyio
async def test_partially_priced_task_returns_partial_sum(db) -> None:
    """D1：部分计价 = 部分和——同任务部分 run 有价部分无价 → 返回已计价之和，非 None。"""
    from deerflow.persistence.engine import get_session_factory

    await _write_pricing_row(NAME, input_per_million=10.0)
    await _add_bookkeeping_run(run_id="r-p1", thread_id="task-p1", bucket_model=MODEL, input_tokens=1_000_000)
    await _add_bookkeeping_run(run_id="r-p2", thread_id="task-p1", bucket_model="no-such-model", input_tokens=100)

    sf = get_session_factory()
    async with sf() as session:
        costs = await compute_task_costs(session, ["task-p1"])

    assert costs["task-p1"][0] == pytest.approx(10.0)
    assert costs["task-p1"][1] == "CNY"


@pytest.mark.anyio
async def test_empty_task_ids_returns_empty_without_queries() -> None:
    """D3：空 task_ids → 空 dict，且不触碰 session（不发任何 SQL）。"""
    from unittest.mock import MagicMock

    session = MagicMock()
    costs = await compute_task_costs(session, [])
    assert costs == {}
    session.execute.assert_not_called()
