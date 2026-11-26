"""删除作品/会话前写用量快照，「我的用量」保留已发生的费用（BUG-01）。"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import func, select

from app.gateway._shared.personal_usage.service import get_personal_usage
from app.gateway._shared.personal_usage.tombstones import UsageTombstoneRow, record_task_tombstone, record_thread_tombstone
from app.gateway._shared.personal_usage.types import make_usage_window
from app.gateway._shared.task_framework.models import TaskRow
from app.gateway._shared.task_framework.repository import TaskRepository
from app.gateway._shared.task_framework.service import BaseTaskService
from app.gateway.routers import threads as threads_router
from app.gateway.routers.console import invalidate_media_pricing_cache
from deerflow.persistence.engine import close_engine, get_session_factory, init_engine
from deerflow.persistence.external_api_calls.model import ExternalApiCallRow
from deerflow.persistence.model_pricing.model import ModelPricingRow
from deerflow.persistence.run.model import RunRow
from deerflow.persistence.thread_meta.model import ThreadMetaRow
from deerflow.persistence.thread_meta.sql import ThreadMetaRepository
from deerflow.runtime.user_context import reset_current_user, set_current_user

USER = "user-1"
NOW = datetime(2026, 11, 21, 12, tzinfo=UTC)
IN_WINDOW = datetime(2026, 11, 20, 12, tzinfo=UTC)


@pytest.fixture()
async def db(tmp_path, monkeypatch):
    monkeypatch.setattr("app.gateway.routers.console.get_app_config", lambda: SimpleNamespace(models=[]))
    monkeypatch.setattr(
        "app.gateway._shared.personal_usage.service.make_usage_window",
        lambda days, tz_offset_minutes: make_usage_window(days, tz_offset_minutes, now=NOW),
    )
    await init_engine("sqlite", url=f"sqlite+aiosqlite:///{tmp_path / 'personal_usage_deletion.db'}", sqlite_dir=str(tmp_path))
    invalidate_media_pricing_cache()
    yield get_session_factory()
    await close_engine()
    invalidate_media_pricing_cache()


@contextmanager
def acting_as(user_id: str):
    """仓库层默认从 contextvar 取当前用户（删除线程元数据按它做归属过滤）。"""
    token = set_current_user(SimpleNamespace(id=user_id, email="usage-test@local"))
    try:
        yield
    finally:
        reset_current_user(token)


class _VideoTaskService(BaseTaskService):
    """最小 video_gen 域服务：只测删除路径，管线空转、submit 不自动启动。"""

    domain = "video_gen"
    id_prefix = "vg"
    max_retries = 0

    async def submit(self, **kwargs):
        kwargs["auto_start"] = False
        return await super().submit(**kwargs)

    async def _run_pipeline(self, task_id, user_id, task_type, payload, task_dir, progress):
        return None


def course_video_thread(
    thread_id: str,
    *,
    user_id: str | None = USER,
    display_name: str | None = None,
    status: str = "idle",
    created_at: datetime = IN_WINDOW,
) -> ThreadMetaRow:
    return ThreadMetaRow(
        thread_id=thread_id,
        user_id=user_id,
        assistant_id="lead_agent",
        display_name=display_name,
        status=status,
        metadata_json={"agent_name": "course-video"},
        created_at=created_at,
        updated_at=created_at,
    )


def agent_run(
    run_id: str,
    thread_id: str,
    *,
    status: str = "success",
    created_at: datetime = IN_WINDOW,
    first_human_message: str | None = None,
) -> RunRow:
    return RunRow(
        run_id=run_id,
        thread_id=thread_id,
        user_id=USER,
        status=status,
        operation_kind="run",
        assistant_id="lead_agent",
        model_name="priced-model",
        total_input_tokens=100,
        total_output_tokens=50,
        total_tokens=150,
        token_usage_by_model={"priced-model": {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150}},
        metadata_json={},
        first_human_message=first_human_message,
        created_at=created_at,
        updated_at=created_at,
    )


async def tombstones(session_factory) -> list[UsageTombstoneRow]:
    async with session_factory() as session:
        return list((await session.execute(select(UsageTombstoneRow).order_by(UsageTombstoneRow.subject_id))).scalars())


def delete_request(thread_store, event_store=None):
    """仿 test_thread_delete_event_store.py：用 request.app.state 上的假依赖驱动路由本体。"""
    app = MagicMock()
    app.state.checkpointer = SimpleNamespace(adelete_thread=AsyncMock())
    app.state.thread_store = thread_store
    app.state.run_event_store = event_store or SimpleNamespace(delete_by_thread=AsyncMock(return_value=0))
    app.state.feedback_repo = SimpleNamespace(delete_by_thread=AsyncMock(return_value=0))
    request = MagicMock()
    request.app = app
    return request


@pytest.fixture()
def no_local_thread_dir(monkeypatch):
    """线程文件目录与本批无关：桩掉，既不碰真实 .deer-flow，也能断言「没走到文件清理」。"""
    stub = MagicMock(return_value=threads_router.ThreadDeleteResponse(success=True, message="stubbed"))
    monkeypatch.setattr(threads_router, "_delete_thread_data", stub)
    return stub


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("owner", "metadata"),
    [
        pytest.param(USER, {}, id="plain-chat"),
        pytest.param(None, {"agent_name": "course-video"}, id="ownerless-thread"),
    ],
)
async def test_thread_outside_usage_records_no_tombstone(db, owner, metadata):
    async with db() as session:
        session.add(
            ThreadMetaRow(
                thread_id="thread-x",
                user_id=owner,
                assistant_id="lead_agent",
                status="idle",
                metadata_json=metadata,
                created_at=IN_WINDOW,
                updated_at=IN_WINDOW,
            )
        )
        session.add(agent_run("run-x", "thread-x"))
        await session.commit()

    assert await record_thread_tombstone("thread-x") is False
    assert await tombstones(db) == []


@pytest.mark.anyio
@pytest.mark.parametrize("task_type", ["remotion_course", "stock_footage"])
async def test_non_usage_task_types_record_no_tombstone(db, task_type):
    task = TaskRow(
        id="vg-other",
        user_id=USER,
        domain="video_gen",
        task_type=task_type,
        status="ready",
        title="不进用量页的任务",
        payload="{}",
        result="{}",
        created_at=IN_WINDOW,
        updated_at=IN_WINDOW,
    )

    assert await record_task_tombstone(task) is False
    assert await tombstones(db) == []


@pytest.mark.anyio
async def test_repeated_thread_delete_keeps_first_tombstone(db):
    """useDeleteThread 对同一线程连发两次 DELETE：第二次不得改写或清空首个快照。"""
    async with db() as session:
        session.add_all([course_video_thread("thread-1", display_name="课程视频草稿"), agent_run("run-1", "thread-1")])
        await session.commit()

    assert await record_thread_tombstone("thread-1") is True
    async with db() as session:
        await session.delete(await session.get(ThreadMetaRow, "thread-1"))
        await session.commit()

    assert await record_thread_tombstone("thread-1") is False
    assert [(row.kind, row.subject_id, row.capability, row.title) for row in await tombstones(db)] == [("agent", "thread-1", "course-video", "课程视频草稿")]


@pytest.mark.anyio
async def test_tombstone_failure_aborts_task_deletion(db, tmp_path, monkeypatch):
    service = _VideoTaskService(TaskRepository(db), storage_dir=str(tmp_path))
    task = await service.submit(user_id=USER, task_type="ai_image", payload={"topic": "不能被误删的任务"})
    task_dir = tmp_path / USER / "tasks" / task.id
    task_dir.mkdir(parents=True)
    (task_dir / "keep.txt").write_text("artifact", encoding="utf-8")
    monkeypatch.setattr(
        "app.gateway._shared.task_framework.service.record_task_tombstone",
        AsyncMock(side_effect=RuntimeError("database unavailable")),
    )

    with pytest.raises(RuntimeError, match="database unavailable"):
        await service.delete_task(task.id, USER)

    assert await service.repo.get(task.id, user_id=USER) is not None
    assert (task_dir / "keep.txt").read_text(encoding="utf-8") == "artifact"


@pytest.mark.anyio
async def test_tombstone_failure_aborts_thread_deletion(db, monkeypatch, no_local_thread_dir):
    async with db() as session:
        session.add_all([course_video_thread("thread-1"), agent_run("run-1", "thread-1")])
        await session.commit()
    monkeypatch.setattr(threads_router, "record_thread_tombstone", AsyncMock(side_effect=RuntimeError("database unavailable")))
    event_store = SimpleNamespace(delete_by_thread=AsyncMock(return_value=0))
    request = delete_request(ThreadMetaRepository(db), event_store)

    with acting_as(USER), pytest.raises(RuntimeError, match="database unavailable"):
        await threads_router.delete_thread_data.__wrapped__("thread-1", request)

    no_local_thread_dir.assert_not_called()
    event_store.delete_by_thread.assert_not_awaited()
    async with db() as session:
        assert await session.get(ThreadMetaRow, "thread-1") is not None


# ── 统计口径：删除后费用与 Token 不变 ─────────────────────────────────────────


def task_row(task_id: str, task_type: str = "ai_image", *, status: str = "ready", title: str | None = None) -> TaskRow:
    return TaskRow(
        id=task_id,
        user_id=USER,
        domain="video_gen",
        task_type=task_type,
        status=status,
        title=title or task_id,
        payload="{}",
        result="{}",
        created_at=IN_WINDOW,
        updated_at=IN_WINDOW,
    )


def llm_ledger(run_id: str, task_id: str, *, input_tokens: int = 120, output_tokens: int = 80) -> RunRow:
    usage = {"input_tokens": input_tokens, "output_tokens": output_tokens, "total_tokens": input_tokens + output_tokens}
    return RunRow(
        run_id=run_id,
        thread_id=task_id,
        user_id=USER,
        status="success",
        operation_kind="llm_bookkeeping",
        model_name="priced-model",
        total_input_tokens=input_tokens,
        total_output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
        token_usage_by_model={"priced-model": usage},
        metadata_json={"domain": "video_gen"},
        created_at=IN_WINDOW,
        updated_at=IN_WINDOW,
    )


def media_ledger(call_id: str, task_id: str, *, amount: float = 0.5) -> ExternalApiCallRow:
    return ExternalApiCallRow(
        call_id=call_id,
        user_id=USER,
        task_id=task_id,
        provider="media-provider",
        method="generate_image",
        status="ok",
        metadata_json={"amount": amount},
        created_at=IN_WINDOW,
    )


def priced_model() -> ModelPricingRow:
    return ModelPricingRow(model_name="priced-model", currency="CNY", input_per_million=10, output_per_million=30)


def video_tombstone(subject_id: str, *, title: str = "快照标题", status: str = "incomplete") -> UsageTombstoneRow:
    return UsageTombstoneRow(
        kind="video",
        subject_id=subject_id,
        user_id=USER,
        capability="ai_image",
        title=title,
        status=status,
        subject_created_at=IN_WINDOW,
        deleted_at=IN_WINDOW,
    )


async def usage(session_factory, **kwargs):
    return await get_personal_usage(session_factory, user_id=USER, days=7, tz_offset_minutes=0, **kwargs)


async def ledger_row_counts(session_factory, subject_id: str) -> tuple[int, int]:
    async with session_factory() as session:
        llm = (await session.execute(select(func.count()).select_from(RunRow).where(RunRow.thread_id == subject_id, RunRow.operation_kind == "llm_bookkeeping"))).scalar_one()
        media = (await session.execute(select(func.count()).select_from(ExternalApiCallRow).where(ExternalApiCallRow.task_id == subject_id))).scalar_one()
    return llm, media


@pytest.mark.anyio
async def test_deleting_ai_image_task_keeps_usage_totals(db, tmp_path):
    async with db() as session:
        session.add_all(
            [
                priced_model(),
                task_row("vg-img", status="pending", title="删除前标题"),
                llm_ledger("vg-img-llm", "vg-img"),
                media_ledger("vg-img-media", "vg-img"),
            ]
        )
        await session.commit()
    service = _VideoTaskService(TaskRepository(db), storage_dir=str(tmp_path))

    before = await usage(db)
    assert await service.delete_task("vg-img", USER) is True
    after = await usage(db)

    assert before.summary.cost is not None
    assert after.summary.cost == pytest.approx(before.summary.cost)
    assert after.summary.total_tokens == before.summary.total_tokens
    assert after.total_tasks == before.total_tasks == 1
    assert before.tasks[0].deleted is False
    assert before.tasks[0].status == "running"
    entry = after.tasks[0]
    assert (entry.deleted, entry.capability, entry.title, entry.status) == (True, "ai_image", "删除前标题", "incomplete")
    async with db() as session:
        assert await session.get(TaskRow, "vg-img") is None
    assert await ledger_row_counts(db, "vg-img") == (1, 1)


@pytest.mark.anyio
async def test_deleted_ai_video_task_stays_in_its_capability_filter(db, tmp_path):
    async with db() as session:
        session.add_all([priced_model(), task_row("vg-vid", "ai_video"), llm_ledger("vg-vid-llm", "vg-vid"), media_ledger("vg-vid-media", "vg-vid")])
        await session.commit()
    service = _VideoTaskService(TaskRepository(db), storage_dir=str(tmp_path))

    before = await usage(db)
    assert await service.delete_task("vg-vid", USER) is True
    after = await usage(db)

    assert after.summary.cost == pytest.approx(before.summary.cost)
    assert after.summary.total_tokens == before.summary.total_tokens
    assert (await usage(db, capability="ai_video")).total_tasks == 1
    assert (await usage(db, capability="ai_image")).total_tasks == 0


@pytest.mark.anyio
async def test_deleting_agent_thread_keeps_usage_totals(db, no_local_thread_dir):
    async with db() as session:
        session.add_all(
            [
                priced_model(),
                course_video_thread("thread-1", display_name="课程视频草稿"),
                agent_run("run-1", "thread-1"),
                media_ledger("thread-1-media", "thread-1", amount=0.25),
            ]
        )
        await session.commit()

    before = await usage(db)
    with acting_as(USER):
        await threads_router.delete_thread_data.__wrapped__("thread-1", delete_request(ThreadMetaRepository(db)))
    after = await usage(db)

    assert before.summary.cost is not None
    assert after.summary.cost == pytest.approx(before.summary.cost)
    assert after.summary.total_tokens == before.summary.total_tokens
    assert after.total_tasks == before.total_tasks == 1
    entry = after.tasks[0]
    assert (entry.deleted, entry.capability, entry.title) == (True, "course-video", "课程视频草稿")
    async with db() as session:
        assert await session.get(ThreadMetaRow, "thread-1") is None


@pytest.mark.anyio
async def test_late_ledger_rows_after_deletion_are_counted(db, tmp_path):
    async with db() as session:
        session.add_all([priced_model(), task_row("vg-img"), llm_ledger("vg-img-llm", "vg-img"), media_ledger("vg-img-media", "vg-img")])
        await session.commit()
    service = _VideoTaskService(TaskRepository(db), storage_dir=str(tmp_path))

    before = await usage(db)
    assert await service.delete_task("vg-img", USER) is True
    async with db() as session:  # 删除时仍在途的供应商调用，删除后才落账
        session.add(media_ledger("vg-img-late", "vg-img", amount=0.75))
        await session.commit()
    after = await usage(db)

    assert after.summary.cost == pytest.approx(before.summary.cost + 0.75)
    assert after.total_tasks == 1


@pytest.mark.anyio
async def test_deleted_task_without_usage_is_not_listed(db, tmp_path):
    async with db() as session:
        session.add(task_row("vg-empty", title="没有任何账本行"))
        await session.commit()
    service = _VideoTaskService(TaskRepository(db), storage_dir=str(tmp_path))

    assert (await usage(db)).total_tasks == 1  # 窗口内创建的现存任务照常列出（现存规则不变）
    assert await service.delete_task("vg-empty", USER) is True
    after = await usage(db)

    assert after.total_tasks == 0
    assert after.tasks == []


@pytest.mark.anyio
async def test_live_row_wins_over_stale_tombstone(db):
    """快照已写入但删除失败（任务仍在）：只列现存的一条，费用不翻倍。"""
    async with db() as session:
        session.add_all(
            [
                priced_model(),
                task_row("vg-live", title="现存标题"),
                llm_ledger("vg-live-llm", "vg-live"),
                media_ledger("vg-live-media", "vg-live"),
                video_tombstone("vg-live"),
            ]
        )
        await session.commit()

    result = await usage(db)

    assert result.total_tasks == 1
    assert (result.tasks[0].deleted, result.tasks[0].title) == (False, "现存标题")
    assert result.summary.cost == pytest.approx(0.5036)


@pytest.mark.anyio
async def test_summary_with_deleted_subjects_reconciles_across_pagination(db):
    async with db() as session:
        session.add_all(
            [
                priced_model(),
                task_row("vg-a"),
                llm_ledger("vg-a-llm", "vg-a", input_tokens=100, output_tokens=0),
                task_row("vg-b"),
                llm_ledger("vg-b-llm", "vg-b", input_tokens=200, output_tokens=0),
                llm_ledger("vg-gone-llm", "vg-gone", input_tokens=300, output_tokens=0),
                video_tombstone("vg-gone"),
            ]
        )
        await session.commit()

    full = await usage(db)
    pages = [await usage(db, offset=index, limit=1) for index in range(3)]

    assert full.total_tasks == 3
    assert [page.total_tasks for page in pages] == [3, 3, 3]
    assert {page.tasks[0].source_id for page in pages} == {"vg-a", "vg-b", "vg-gone"}
    assert sum(page.tasks[0].cost for page in pages) == pytest.approx(full.summary.cost)
    assert all(page.summary.cost == pytest.approx(full.summary.cost) for page in pages)
