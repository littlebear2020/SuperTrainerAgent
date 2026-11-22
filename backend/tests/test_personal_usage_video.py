from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.gateway._shared.personal_usage.service import get_personal_usage
from app.gateway._shared.personal_usage.types import make_usage_window
from app.gateway._shared.personal_usage.video import aggregate_video_usage
from app.gateway._shared.task_framework.models import TaskRow
from app.gateway.routers.console import invalidate_media_pricing_cache
from deerflow.persistence.engine import close_engine, get_session_factory, init_engine
from deerflow.persistence.external_api_calls.model import ExternalApiCallRow
from deerflow.persistence.media_pricing.model import MediaPricingRow
from deerflow.persistence.model_pricing.model import ModelPricingRow
from deerflow.persistence.run.model import RunRow
from deerflow.persistence.thread_meta.model import ThreadMetaRow

USER = "user-1"
NOW = datetime(2026, 11, 21, 12, tzinfo=UTC)
WINDOW = make_usage_window(7, 0, now=NOW)
IN_WINDOW = datetime(2026, 11, 20, 12, tzinfo=UTC)
OUTSIDE = datetime(2026, 11, 1, 12, tzinfo=UTC)
MISSING = object()


@pytest.fixture()
async def db(tmp_path, monkeypatch):
    monkeypatch.setattr("app.gateway.routers.console.get_app_config", lambda: SimpleNamespace(models=[]))
    monkeypatch.setattr(
        "app.gateway._shared.personal_usage.service.make_usage_window",
        lambda days, tz_offset_minutes: make_usage_window(days, tz_offset_minutes, now=NOW),
    )
    await init_engine("sqlite", url=f"sqlite+aiosqlite:///{tmp_path / 'personal_usage_video.db'}", sqlite_dir=str(tmp_path))
    invalidate_media_pricing_cache()
    yield get_session_factory()
    await close_engine()
    invalidate_media_pricing_cache()


def task(
    task_id: str,
    task_type: str = "ai_image",
    *,
    user_id: str = USER,
    status: str = "ready",
    title: str | None = None,
    created_at: datetime = IN_WINDOW,
    updated_at: datetime = IN_WINDOW,
) -> TaskRow:
    return TaskRow(
        id=task_id,
        user_id=user_id,
        domain="video_gen",
        task_type=task_type,
        status=status,
        title=title or task_id,
        payload="{}",
        result="{}",
        created_at=created_at,
        updated_at=updated_at,
    )


def bucket(input_tokens: int, output_tokens: int, *, amount=MISSING) -> dict:
    value = {"input_tokens": input_tokens, "output_tokens": output_tokens, "total_tokens": 999}
    if amount is not MISSING:
        value["amount"] = amount
    return value


def llm_run(
    run_id: str,
    task_id: str,
    *,
    model: str = "priced-model",
    input_tokens: int,
    output_tokens: int,
    amount=MISSING,
    user_id: str | None = USER,
    created_at: datetime = IN_WINDOW,
) -> RunRow:
    return RunRow(
        run_id=run_id,
        thread_id=task_id,
        user_id=user_id,
        status="success",
        operation_kind="llm_bookkeeping",
        model_name=model,
        total_input_tokens=input_tokens,
        total_output_tokens=output_tokens,
        total_tokens=999,
        token_usage_by_model={model: bucket(input_tokens, output_tokens, amount=amount)},
        metadata_json={"domain": "video_gen"},
        created_at=created_at,
        updated_at=created_at,
    )


def agent_run(run_id: str, thread_id: str, input_tokens: int, output_tokens: int, created_at: datetime) -> RunRow:
    return RunRow(
        run_id=run_id,
        thread_id=thread_id,
        user_id=USER,
        status="success",
        operation_kind="run",
        assistant_id="course-video",
        model_name="priced-model",
        total_input_tokens=input_tokens,
        total_output_tokens=output_tokens,
        total_tokens=999,
        token_usage_by_model={"priced-model": bucket(input_tokens, output_tokens)},
        metadata_json={},
        created_at=created_at,
        updated_at=created_at,
    )


def media(
    call_id: str,
    task_id: str,
    *,
    amount=MISSING,
    user_id: str | None = USER,
    provider: str = "media-provider",
    created_at: datetime = IN_WINDOW,
) -> ExternalApiCallRow:
    return ExternalApiCallRow(
        call_id=call_id,
        user_id=user_id,
        task_id=task_id,
        provider=provider,
        method="generate_image",
        status="ok",
        metadata_json={} if amount is MISSING else {"amount": amount},
        created_at=created_at,
    )


async def price(session, model: str = "priced-model", currency: str = "CNY") -> None:
    session.add(ModelPricingRow(model_name=model, currency=currency, input_per_million=10, output_per_million=30))


async def videos(session_factory, capability="all"):
    async with session_factory() as session:
        return await aggregate_video_usage(session, user_id=USER, window=WINDOW, capability=capability)


@pytest.mark.anyio
async def test_video_agent_summary_and_activity_contract(db):
    async with db() as session:
        await price(session)
        session.add_all(
            [
                ThreadMetaRow(
                    thread_id="agent-1",
                    user_id=USER,
                    assistant_id="course-video",
                    display_name="Agent 任务",
                    status="idle",
                    metadata_json={},
                    created_at=IN_WINDOW - timedelta(days=2),
                    updated_at=IN_WINDOW,
                ),
                agent_run("agent-run-1", "agent-1", 60, 40, IN_WINDOW - timedelta(days=1)),
                agent_run("agent-run-2", "agent-1", 10, 10, IN_WINDOW - timedelta(hours=2)),
                task("video-1", title="图片任务", created_at=IN_WINDOW - timedelta(days=2), updated_at=IN_WINDOW + timedelta(days=2)),
                llm_run("video-1-llm", "video-1", input_tokens=120, output_tokens=80, created_at=IN_WINDOW - timedelta(days=1)),
                media("video-1-media", "video-1", amount=0.5, created_at=IN_WINDOW - timedelta(hours=1)),
                task("video-2", "ai_video", status="running", created_at=IN_WINDOW),
                task("unsupported", "stock_footage"),
                llm_run("unsupported-llm", "unsupported", input_tokens=999, output_tokens=0),
                task("other-user", user_id="user-2"),
                llm_run("domain-fallback", "video_gen", input_tokens=999, output_tokens=0),
            ]
        )
        await session.commit()

    video_tasks = await videos(db)
    by_id = {item.source_id: item for item in video_tasks}
    assert set(by_id) == {"video-1", "video-2"}
    assert by_id["video-1"].task_key == "video:video-1"
    assert by_id["video-1"].capability == "ai_image"
    assert by_id["video-1"].title == "图片任务"
    assert by_id["video-1"].status == "completed"
    assert by_id["video-1"].total_tokens == 200
    assert by_id["video-1"].llm_cost == pytest.approx(0.0036, abs=1e-9)
    assert by_id["video-1"].media_cost == pytest.approx(0.5, abs=1e-9)
    assert by_id["video-1"].cost == pytest.approx(0.5036, abs=1e-9)
    assert by_id["video-1"].cost_source == "mixed"
    assert by_id["video-1"].models[0].total_tokens == 200
    assert by_id["video-2"].status == "running"
    assert by_id["video-2"].cost is None
    assert by_id["video-2"].total_tokens is None
    assert by_id["video-2"].media_cost is None
    assert by_id["video-1"].updated_at == IN_WINDOW + timedelta(days=2)
    assert video_tasks[0].source_id == "video-2"

    result = await get_personal_usage(db, user_id=USER, days=7, tz_offset_minutes=0)
    assert result.summary.total_tokens == 320
    assert result.summary.cost == pytest.approx(0.5058, abs=1e-9)
    assert result.summary.currency == "CNY"
    assert result.summary.cost_source == "mixed"
    assert result.total_tasks == 3
    assert {item.source_id for item in result.tasks} == {"agent-1", "video-1", "video-2"}


@pytest.mark.anyio
async def test_video_capability_filter_and_window_membership(db):
    async with db() as session:
        session.add_all(
            [
                task("outside-only", created_at=OUTSIDE),
                llm_run("outside-only-llm", "outside-only", input_tokens=10, output_tokens=10, created_at=OUTSIDE),
                task("old-with-new-media", created_at=OUTSIDE),
                media("old-with-new-media-call", "old-with-new-media", amount=0.5),
                task("image"),
                task("movie", "ai_video"),
            ]
        )
        await session.commit()

    assert {item.source_id for item in await videos(db)} == {"old-with-new-media", "image", "movie"}
    assert {item.source_id for item in await videos(db, "ai_image")} == {"old-with-new-media", "image"}
    assert {item.source_id for item in await videos(db, "ai_video")} == {"movie"}
    filtered = await get_personal_usage(db, user_id=USER, days=7, tz_offset_minutes=0, capability="ai_video")
    assert filtered.total_tasks == 1
    assert filtered.tasks[0].source_id == "movie"
    assert filtered.summary.cost is None
    assert filtered.summary.total_tokens is None


@pytest.mark.anyio
async def test_video_unknown_real_zero_and_unpriced_media(db):
    async with db() as session:
        session.add_all(
            [
                task("real"),
                llm_run("real-llm", "real", model="real-model", input_tokens=1, output_tokens=2, amount=1.5),
                task("unpriced"),
                llm_run("unpriced-llm", "unpriced", model="unknown-model", input_tokens=5, output_tokens=7),
                task("unpriced-media"),
                media("unpriced-media-call", "unpriced-media"),
                task("zero-media"),
                media("zero-media-call", "zero-media", amount=0),
            ]
        )
        await session.commit()

    tasks = {item.source_id: item for item in await videos(db)}
    assert tasks["real"].llm_cost == pytest.approx(1.5, abs=1e-9)
    assert tasks["real"].currency == "CNY"
    assert tasks["real"].cost_source == "real"
    assert tasks["unpriced"].total_tokens == 12
    assert tasks["unpriced"].llm_cost is None
    assert tasks["unpriced"].cost is None
    assert tasks["unpriced"].media_cost is None
    assert tasks["unpriced-media"].media_cost is None
    assert tasks["unpriced-media"].cost is None
    assert tasks["zero-media"].media_cost == pytest.approx(0, abs=1e-9)
    assert tasks["zero-media"].cost == pytest.approx(0, abs=1e-9)
    assert tasks["zero-media"].currency == "CNY"
    assert tasks["zero-media"].cost_source == "real"


@pytest.mark.anyio
async def test_video_mixed_currency_is_not_summed(db):
    async with db() as session:
        await price(session)
        session.add(
            MediaPricingRow(
                provider="usd-provider",
                media_type="image",
                unit="per_image",
                unit_price=1,
                currency="USD",
            )
        )
        session.add_all(
            [
                task("mixed-currency"),
                llm_run("mixed-currency-llm", "mixed-currency", input_tokens=10, output_tokens=10),
                media("mixed-currency-media", "mixed-currency", amount=0.5, provider="usd-provider"),
            ]
        )
        await session.commit()
        invalidate_media_pricing_cache()

    task_result = (await videos(db))[0]
    assert task_result.llm_cost == pytest.approx(0.0004, abs=1e-9)
    assert task_result.media_cost == pytest.approx(0.5, abs=1e-9)
    assert task_result.cost is None
    assert task_result.currency is None


@pytest.mark.anyio
async def test_personal_usage_summary_reconciles_across_pagination(db):
    async with db() as session:
        await price(session)
        for index in range(21):
            task_id = f"bulk-{index:02d}"
            session.add(task(task_id, created_at=IN_WINDOW + timedelta(seconds=index)))
            session.add(llm_run(f"bulk-llm-{index:02d}", task_id, input_tokens=1, output_tokens=1))
        await session.commit()

    first = await get_personal_usage(db, user_id=USER, days=7, tz_offset_minutes=0, offset=0, limit=20)
    second = await get_personal_usage(db, user_id=USER, days=7, tz_offset_minutes=0, offset=20, limit=20)

    assert first.total_tasks == 21
    assert len(first.tasks) == 20
    assert first.has_more is True
    assert len(second.tasks) == 1
    assert second.has_more is False
    assert {item.source_id for item in first.tasks}.isdisjoint({item.source_id for item in second.tasks})
    for result in (first, second):
        assert result.summary.total_tokens == 42
        assert result.summary.cost == pytest.approx(0.00084, abs=1e-9)
        assert result.summary.currency == "CNY"
        assert result.summary.cost_source == "estimated"


@pytest.mark.anyio
async def test_video_missing_title_returns_none_instead_of_task_id(db):
    missing = task("missing")
    missing.title = None
    async with db() as session:
        session.add(missing)
        await session.commit()

    result = (await videos(db))[0]
    assert result.title is None
