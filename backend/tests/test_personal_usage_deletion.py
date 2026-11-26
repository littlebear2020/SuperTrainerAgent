"""删除作品/会话前写用量快照，「我的用量」保留已发生的费用（BUG-01）。"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select

from app.gateway._shared.personal_usage.tombstones import UsageTombstoneRow, record_task_tombstone, record_thread_tombstone
from app.gateway._shared.personal_usage.types import make_usage_window
from app.gateway._shared.task_framework.models import TaskRow
from app.gateway._shared.task_framework.repository import TaskRepository
from app.gateway._shared.task_framework.service import BaseTaskService
from app.gateway.routers import threads as threads_router
from app.gateway.routers.console import invalidate_media_pricing_cache
from deerflow.persistence.engine import close_engine, get_session_factory, init_engine
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
