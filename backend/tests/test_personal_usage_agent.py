from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import event

from app.gateway._shared.personal_usage.agent import aggregate_agent_usage
from app.gateway._shared.personal_usage.types import make_usage_window
from app.gateway.routers.console import invalidate_media_pricing_cache
from deerflow.persistence.engine import close_engine, get_engine, get_session_factory, init_engine
from deerflow.persistence.external_api_calls.model import ExternalApiCallRow
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
    await init_engine("sqlite", url=f"sqlite+aiosqlite:///{tmp_path / 'personal_usage_agent.db'}", sqlite_dir=str(tmp_path))
    invalidate_media_pricing_cache()
    yield get_session_factory()
    await close_engine()
    invalidate_media_pricing_cache()


def thread(
    thread_id: str,
    *,
    assistant_id: str | None = "course-video",
    user_id: str | None = USER,
    title: str | None = None,
    status: str = "idle",
    created_at: datetime = IN_WINDOW,
    updated_at: datetime = IN_WINDOW,
    metadata: dict | None = None,
) -> ThreadMetaRow:
    return ThreadMetaRow(
        thread_id=thread_id,
        assistant_id=assistant_id,
        user_id=user_id,
        display_name=title or thread_id,
        status=status,
        metadata_json=metadata or {},
        created_at=created_at,
        updated_at=updated_at,
    )


def run(
    run_id: str,
    thread_id: str,
    *,
    user_id: str | None = USER,
    status: str = "success",
    assistant_id: str | None = "course-video",
    model_name: str | None = "priced-model",
    input_tokens: int = 0,
    output_tokens: int = 0,
    total_tokens: int | None = None,
    token_usage_by_model: dict | None = None,
    created_at: datetime = IN_WINDOW,
    updated_at: datetime = IN_WINDOW,
) -> RunRow:
    return RunRow(
        run_id=run_id,
        thread_id=thread_id,
        user_id=user_id,
        status=status,
        assistant_id=assistant_id,
        operation_kind="run",
        model_name=model_name,
        total_input_tokens=input_tokens,
        total_output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens if total_tokens is None else total_tokens,
        token_usage_by_model=token_usage_by_model or {},
        metadata_json={},
        created_at=created_at,
        updated_at=updated_at,
    )


def bucket(input_tokens: int, output_tokens: int, *, cache=MISSING, amount=MISSING, total_tokens: int | None = None) -> dict:
    value = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens if total_tokens is None else total_tokens,
    }
    if cache is not MISSING:
        value["cache_read_tokens"] = cache
    if amount is not MISSING:
        value["amount"] = amount
    return value


def media(call_id: str, task_id: str, *, amount=0.5, user_id: str | None = USER, created_at: datetime = IN_WINDOW) -> ExternalApiCallRow:
    return ExternalApiCallRow(
        call_id=call_id,
        user_id=user_id,
        task_id=task_id,
        provider="media-provider",
        method="generate_image",
        status="ok",
        metadata_json={} if amount is MISSING else {"amount": amount},
        created_at=created_at,
    )


async def add_pricing(session, model_name: str = "priced-model", input_price: float = 10, output_price: float = 30) -> None:
    session.add(
        ModelPricingRow(
            model_name=model_name,
            currency="CNY",
            input_per_million=input_price,
            output_per_million=output_price,
        )
    )


async def aggregate(session_factory, capability="all"):
    async with session_factory() as session:
        return await aggregate_agent_usage(session, user_id=USER, window=WINDOW, capability=capability)


@pytest.mark.anyio
async def test_agent_groups_runs_and_uses_all_time_latest_status(db):
    async with db() as session:
        session.add_all(
            [
                thread("completed", title="已完成课程"),
                thread("running"),
                thread("incomplete"),
                run("completed-old", "completed", status="error", created_at=IN_WINDOW - timedelta(days=2)),
                run(
                    "completed-new",
                    "completed",
                    status="success",
                    created_at=IN_WINDOW - timedelta(days=1),
                    updated_at=IN_WINDOW + timedelta(hours=2),
                ),
                run("running-old", "running", status="success", created_at=IN_WINDOW - timedelta(days=2)),
                run("running-new", "running", status="running", created_at=IN_WINDOW, updated_at=IN_WINDOW + timedelta(hours=1)),
                run("incomplete-old", "incomplete", status="running", created_at=IN_WINDOW - timedelta(days=2)),
                run("incomplete-new", "incomplete", status="failed", created_at=IN_WINDOW + timedelta(hours=3)),
            ]
        )
        await session.commit()

    tasks = {task.source_id: task for task in await aggregate(db)}
    assert tasks["completed"].task_key == "agent:completed"
    assert tasks["completed"].title == "已完成课程"
    assert tasks["completed"].status == "completed"
    assert tasks["completed"].updated_at == IN_WINDOW + timedelta(hours=2)
    assert tasks["running"].status == "running"
    assert tasks["incomplete"].status == "incomplete"


@pytest.mark.anyio
async def test_agent_window_membership_alias_activity_and_updated_at(db):
    async with db() as session:
        session.add_all(
            [
                thread("old-alias", assistant_id="course-ppt", title="旧 PPT", created_at=IN_WINDOW - timedelta(days=1)),
                thread("outside-only", created_at=OUTSIDE - timedelta(days=1)),
                thread("new-media", created_at=OUTSIDE - timedelta(days=1)),
                thread("old-run", created_at=OUTSIDE - timedelta(days=1)),
                thread("ping-order", created_at=IN_WINDOW - timedelta(days=2)),
                run("outside-run", "outside-only", created_at=OUTSIDE),
                run(
                    "new-media-latest-outside",
                    "new-media",
                    status="success",
                    created_at=OUTSIDE,
                    updated_at=OUTSIDE + timedelta(hours=1),
                ),
                run("old-run-inside", "old-run", created_at=IN_WINDOW - timedelta(hours=1)),
                run("ping-order-run", "ping-order", created_at=IN_WINDOW - timedelta(days=1), updated_at=IN_WINDOW + timedelta(days=1)),
                media("new-media-call", "new-media", created_at=IN_WINDOW + timedelta(hours=2)),
            ]
        )
        await session.commit()

    tasks = await aggregate(db)
    by_id = {task.source_id: task for task in tasks}
    assert "outside-only" not in by_id
    assert by_id["old-alias"].capability == "ppt-master"
    assert by_id["old-alias"].status == "incomplete"
    assert by_id["old-alias"].cost is None
    assert by_id["old-alias"].total_tokens is None
    assert by_id["new-media"].status == "completed"
    assert by_id["new-media"].updated_at == OUTSIDE + timedelta(hours=1)
    assert by_id["new-media"].media_cost == pytest.approx(0.5, abs=1e-9)
    assert by_id["new-media"].total_tokens is None
    assert tasks[0].source_id == "new-media"
    assert [task.source_id for task in tasks].index("old-run") < [task.source_id for task in tasks].index("ping-order")


@pytest.mark.anyio
async def test_agent_ownership_null_history_and_capability_filter(db):
    async with db() as session:
        session.add_all(
            [
                thread("null-history", created_at=OUTSIDE),
                thread("polluted", created_at=OUTSIDE),
                thread("other-owner", user_id="user-2"),
                thread("not-supported", assistant_id="lead_agent"),
                thread("poster", assistant_id="card-poster"),
                run("null-history-run", "null-history", user_id=None, input_tokens=1, output_tokens=2),
                run("polluted-run", "polluted", user_id="user-2", input_tokens=100, output_tokens=100),
                run("other-owner-run", "other-owner", user_id="user-2"),
                run("not-supported-run", "not-supported", assistant_id="lead_agent"),
                run("poster-run", "poster", assistant_id="card-poster"),
            ]
        )
        await session.commit()

    tasks = await aggregate(db)
    by_id = {task.source_id: task for task in tasks}
    assert by_id["null-history"].total_tokens == 3
    assert "polluted" not in by_id
    assert "other-owner" not in by_id
    assert "not-supported" not in by_id
    assert by_id["poster"].capability == "creative-studio"
    assert {task.source_id for task in await aggregate(db, "creative-studio")} == {"poster"}
    assert await aggregate(db, "ai_image") == []


@pytest.mark.anyio
async def test_agent_cost_tokens_and_complete_model_breakdown(db):
    async with db() as session:
        await add_pricing(session)
        session.add_all(
            [
                thread("priced"),
                run(
                    "priced-1",
                    "priced",
                    input_tokens=60,
                    output_tokens=40,
                    total_tokens=999,
                    token_usage_by_model={"priced-model": bucket(60, 40, total_tokens=998)},
                    created_at=IN_WINDOW - timedelta(days=1),
                ),
                run(
                    "priced-2",
                    "priced",
                    input_tokens=10,
                    output_tokens=10,
                    token_usage_by_model={"priced-model": bucket(10, 10, cache=0)},
                ),
                run(
                    "priced-outside",
                    "priced",
                    input_tokens=999,
                    output_tokens=0,
                    token_usage_by_model={"priced-model": bucket(999, 0)},
                    created_at=OUTSIDE,
                ),
                thread("zero-cache"),
                run(
                    "zero-cache-run",
                    "zero-cache",
                    input_tokens=1,
                    output_tokens=0,
                    token_usage_by_model={"priced-model": bucket(1, 0, cache=0)},
                ),
            ]
        )
        await session.commit()

    tasks = {task.source_id: task for task in await aggregate(db)}
    priced = tasks["priced"]
    assert priced.total_tokens == 120
    assert priced.llm_cost == pytest.approx(0.0022, abs=1e-9)
    assert priced.cost == pytest.approx(0.0022, abs=1e-9)
    assert priced.media_cost is None
    assert priced.currency == "CNY"
    assert priced.cost_source == "estimated"
    assert len(priced.models) == 1
    assert priced.models[0].input_tokens == 70
    assert priced.models[0].output_tokens == 50
    assert priced.models[0].total_tokens == 120
    assert priced.models[0].cache_read_tokens is None
    assert priced.models[0].cost == pytest.approx(0.0022, abs=1e-9)
    assert tasks["zero-cache"].models[0].cache_read_tokens == 0


@pytest.mark.anyio
async def test_agent_real_amount_without_pricing_is_real_cny(db):
    async with db() as session:
        session.add_all(
            [
                thread("real"),
                run(
                    "real-run",
                    "real",
                    model_name="real-model",
                    input_tokens=1,
                    output_tokens=2,
                    token_usage_by_model={"real-model": bucket(1, 2, amount=1.5)},
                ),
            ]
        )
        await session.commit()

    task = (await aggregate(db))[0]
    assert task.llm_cost == pytest.approx(1.5, abs=1e-9)
    assert task.cost == pytest.approx(1.5, abs=1e-9)
    assert task.currency == "CNY"
    assert task.cost_source == "real"
    assert task.models[0].cost_source == "real"


@pytest.mark.anyio
async def test_agent_mixed_and_invalid_amount_sources(db):
    async with db() as session:
        await add_pricing(session)
        await add_pricing(session, "eight-model", input_price=8, output_price=0)
        session.add_all(
            [
                thread("mixed"),
                run(
                    "mixed-run",
                    "mixed",
                    input_tokens=1_000_001,
                    output_tokens=1,
                    token_usage_by_model={
                        "real-model": bucket(1, 1, amount=3),
                        "eight-model": bucket(1_000_000, 0),
                    },
                ),
                thread("invalid-amount"),
                run(
                    "invalid-amount-run",
                    "invalid-amount",
                    input_tokens=60,
                    output_tokens=40,
                    token_usage_by_model={"priced-model": bucket(60, 40, amount="not-a-number")},
                ),
            ]
        )
        await session.commit()

    tasks = {task.source_id: task for task in await aggregate(db)}
    assert tasks["mixed"].llm_cost == pytest.approx(11, abs=1e-9)
    assert tasks["mixed"].cost_source == "mixed"
    assert {model.cost_source for model in tasks["mixed"].models} == {"real", "estimated"}
    assert tasks["invalid-amount"].llm_cost == pytest.approx(0.0018, abs=1e-9)
    assert tasks["invalid-amount"].cost_source == "estimated"


@pytest.mark.anyio
async def test_agent_failed_unpriced_and_incomplete_models_keep_task_totals(db):
    async with db() as session:
        await add_pricing(session)
        session.add_all(
            [
                thread("failed"),
                run(
                    "failed-run",
                    "failed",
                    status="error",
                    input_tokens=10,
                    output_tokens=10,
                    token_usage_by_model={"priced-model": bucket(10, 10)},
                ),
                thread("no-breakdown"),
                run("no-breakdown-run", "no-breakdown", input_tokens=10, output_tokens=10),
                thread("mixed-breakdown"),
                run(
                    "mixed-breakdown-1",
                    "mixed-breakdown",
                    input_tokens=10,
                    output_tokens=10,
                    token_usage_by_model={"priced-model": bucket(10, 10)},
                ),
                run("mixed-breakdown-2", "mixed-breakdown", input_tokens=5, output_tokens=5),
                thread("mismatched-breakdown"),
                run(
                    "mismatched-breakdown-run",
                    "mismatched-breakdown",
                    input_tokens=10,
                    output_tokens=10,
                    token_usage_by_model={"priced-model": bucket(9, 10)},
                ),
                thread("unpriced"),
                run(
                    "unpriced-run",
                    "unpriced",
                    model_name="unknown-model",
                    input_tokens=5,
                    output_tokens=7,
                    token_usage_by_model={"unknown-model": bucket(5, 7)},
                ),
            ]
        )
        await session.commit()

    tasks = {task.source_id: task for task in await aggregate(db)}
    assert tasks["failed"].status == "incomplete"
    assert tasks["failed"].total_tokens == 20
    assert tasks["failed"].llm_cost == pytest.approx(0.0004, abs=1e-9)
    assert tasks["no-breakdown"].total_tokens == 20
    assert tasks["no-breakdown"].llm_cost == pytest.approx(0.0004, abs=1e-9)
    assert tasks["no-breakdown"].models == []
    assert tasks["mixed-breakdown"].total_tokens == 30
    assert tasks["mixed-breakdown"].models == []
    assert tasks["mismatched-breakdown"].total_tokens == 20
    assert tasks["mismatched-breakdown"].models == []
    assert tasks["unpriced"].total_tokens == 12
    assert tasks["unpriced"].llm_cost is None
    assert tasks["unpriced"].cost is None
    assert tasks["unpriced"].cost_source is None
    assert tasks["unpriced"].models[0].cost is None


@pytest.mark.anyio
async def test_agent_amount_only_row_invalidates_partial_models(db):
    async with db() as session:
        await add_pricing(session)
        session.add_all(
            [
                thread("amount-only-partial"),
                run(
                    "amount-only-normal",
                    "amount-only-partial",
                    input_tokens=1,
                    output_tokens=1,
                    token_usage_by_model={"priced-model": bucket(1, 1)},
                ),
                run(
                    "amount-only-real",
                    "amount-only-partial",
                    model_name="amount-only-model",
                    token_usage_by_model={"amount-only-model": {"amount": 3}},
                ),
            ]
        )
        await session.commit()

    task = (await aggregate(db))[0]
    assert task.total_tokens == 2
    assert task.llm_cost == pytest.approx(3.00004, abs=1e-9)
    assert task.cost_source == "mixed"
    assert task.models == []


@pytest.mark.anyio
async def test_agent_zero_token_run_with_positive_buckets_invalidates_models(db):
    async with db() as session:
        await add_pricing(session)
        session.add_all(
            [
                thread("zero-positive-buckets"),
                run(
                    "zero-positive-buckets-run",
                    "zero-positive-buckets",
                    input_tokens=0,
                    output_tokens=0,
                    token_usage_by_model={"priced-model": bucket(10, 4)},
                ),
            ]
        )
        await session.commit()

    task = (await aggregate(db))[0]
    assert task.total_tokens == 0
    assert task.llm_cost == pytest.approx(0.00022, abs=1e-9)
    assert task.models == []


@pytest.mark.anyio
async def test_agent_malformed_bucket_never_breaks_usage(db):
    async with db() as session:
        await add_pricing(session)
        session.add_all(
            [
                thread("malformed-bucket"),
                run(
                    "malformed-bucket-run",
                    "malformed-bucket",
                    input_tokens=10,
                    output_tokens=10,
                    token_usage_by_model={"priced-model": {"input_tokens": "n/a", "output_tokens": 10}},
                ),
            ]
        )
        await session.commit()

    task = (await aggregate(db))[0]
    assert task.total_tokens == 20
    assert task.llm_cost == pytest.approx(0.0003, abs=1e-9)
    assert task.cost_source == "estimated"
    assert task.models == []


@pytest.mark.anyio
async def test_agent_latest_status_uses_one_row_per_thread_window_function(db):
    async with db() as session:
        session.add_all(
            [
                thread("latest-window"),
                run("latest-window-old", "latest-window", created_at=IN_WINDOW - timedelta(days=1)),
                run("latest-window-new", "latest-window"),
            ]
        )
        await session.commit()

    statements: list[str] = []

    def capture_sql(_connection, _cursor, statement, _parameters, _context, _executemany):
        statements.append(" ".join(statement.lower().split()))

    engine = get_engine()
    event.listen(engine.sync_engine, "before_cursor_execute", capture_sql)
    try:
        await aggregate(db)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", capture_sql)

    assert any("row_number() over" in statement and "partition by" in statement for statement in statements), statements


@pytest.mark.anyio
async def test_agent_missing_title_uses_first_human_then_none(db):
    missing = thread("missing")
    missing.display_name = None
    placeholder = thread("placeholder")
    fallback = thread("fallback")
    fallback.display_name = None
    fallback_run = run("fallback-run", "fallback")
    fallback_run.first_human_message = "首条用户消息"
    async with db() as session:
        session.add_all([missing, placeholder, fallback, fallback_run])
        await session.commit()

    tasks = {task.source_id: task for task in await aggregate(db)}
    assert tasks["fallback"].title == "首条用户消息"
    assert tasks["placeholder"].title is None
    assert tasks["missing"].title is None
