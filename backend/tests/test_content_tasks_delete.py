"""R2-4（2026-10-07）: DELETE /api/content/tasks/{task_id} —— content 任务补删除端点。

修前 content_tasks.py 仅 3 路由（POST / GET / GET download）——ppt_gen/pdf_gen
任务只能等其自灭，产物无任何清除通道（对照 video task delete 早已存在）。
本文件测端点语义 + 真实 ContentTaskService 集成（repo 行删除 + task_dir 清除）。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.gateway._shared.task_framework.models import TaskRow
from app.gateway._shared.task_framework.repository import TaskRepository
from app.gateway.content.ppt_service import ContentTaskService
from app.gateway.routers import content_tasks


def _make_request(svc):
    class _Req:
        class app:
            class state:
                content_task_service = svc

    return _Req()


class _FakeSvc:
    def __init__(self, deleted: bool = True):
        self.delete_task = AsyncMock(return_value=deleted)


@pytest.mark.asyncio
async def test_delete_task_ok():
    svc = _FakeSvc(deleted=True)
    result = await content_tasks.delete_task(_make_request(svc), "ct_1")
    assert result == {"ok": True}
    svc.delete_task.assert_awaited_once()


@pytest.mark.asyncio
async def test_delete_task_not_found_404():
    svc = _FakeSvc(deleted=False)
    with pytest.raises(HTTPException) as exc_info:
        await content_tasks.delete_task(_make_request(svc), "ct_missing")
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_delete_task_service_unavailable_503():
    class _Req:
        class app:
            class state:
                content_task_service = None

    with pytest.raises(HTTPException) as exc_info:
        await content_tasks.delete_task(_Req, "ct_1")
    assert exc_info.value.status_code == 503


# ── Integration: real ContentTaskService deletes row + task_dir ────────────


@pytest_asyncio.fixture(loop_scope="function")
async def engine():
    eng = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with eng.begin() as conn:
        await conn.run_sync(TaskRow.__table__.create)
    yield eng
    await eng.dispose()


@pytest.fixture()
def session_factory(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest.mark.asyncio
async def test_content_delete_integration_removes_row_and_artifacts(tmp_path: Path, session_factory, monkeypatch):
    """口径对齐 video task delete：repo 行删除 + 产物目录 rmtree。

    ContentTaskService 未覆写 delete_task——继承 BaseTaskService 语义
    （cancel + _cleanup_task rmtree task_dir + repo.delete），本用例钉扎
    该继承链在 content 域可用（server 残留 ct_ffe004a2e2dc 部署后经本端点清理）。
    """
    monkeypatch.setattr("deerflow.runtime.user_context.get_effective_user_id", lambda: "u_r24")
    repo = TaskRepository(session_factory=session_factory)
    svc = ContentTaskService(repo, storage_dir=str(tmp_path))

    task = await svc.submit(user_id="u_r24", task_type="ppt_gen", payload={}, auto_start=False)
    task_dir = tmp_path / "u_r24" / "tasks" / task.id
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "output.pptx").write_bytes(b"pptx-bytes")

    deleted = await svc.delete_task(task.id, "u_r24")
    assert deleted is True
    assert not task_dir.exists()
    assert await svc.get_task(task.id, "u_r24") is None
