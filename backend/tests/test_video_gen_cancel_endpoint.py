"""Task 22: POST /tasks/{id}/cancel —— 长任务可显式取消。

核实：修前**无任何取消端点**。`DELETE /tasks/{id}` 会连带删除任务与产物目录，
对"我只想停下来、别烧配额，但记录留着"这个诉求过于粗暴；而 worker 侧更是
从不回查取消标志（见 docker/production-worker/tests/test_worker_cancellation.py）。

本文件测 gateway 侧端点语义：置 cancelled + 断掉 gateway 内的 asyncio task，
终态任务拒绝取消（409），他人任务不可见（404）。
"""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException


def _make_request(svc):
    class _Req:
        class app:
            class state:
                video_gen_service = svc

    return _Req()


class _FakeSvc:
    def __init__(self, task):
        self._task = task
        self.cancelled: list[str] = []

    async def get_task(self, task_id, user_id):
        if self._task is None or self._task.id != task_id:
            return None
        return self._task

    async def cancel_task(self, task_id, user_id):
        if self._task is None or self._task.id != task_id:
            return False
        if self._task.status in {"ready", "failed", "cancelled"}:
            raise ValueError("terminal")
        self.cancelled.append(task_id)
        self._task.status = "cancelled"
        return True


def _task(status="composing", task_id="vg_1"):
    return SimpleNamespace(id=task_id, status=status, user_id="test-user-autouse")


@pytest.mark.asyncio
async def test_cancel_running_task_returns_ok():
    from app.gateway.routers.video_gen import cancel_video_task

    svc = _FakeSvc(_task("composing"))
    res = await cancel_video_task(_make_request(svc), "vg_1")

    assert res == {"ok": True, "status": "cancelled"}
    assert svc.cancelled == ["vg_1"]


@pytest.mark.asyncio
async def test_cancel_unknown_task_404():
    from app.gateway.routers.video_gen import cancel_video_task

    svc = _FakeSvc(None)
    with pytest.raises(HTTPException) as exc:
        await cancel_video_task(_make_request(svc), "vg_missing")
    assert exc.value.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["ready", "failed", "cancelled"])
async def test_cancel_terminal_task_409(status):
    """终态任务不可取消——已完成的任务"取消"没有意义，静默成功会误导用户。"""
    from app.gateway.routers.video_gen import cancel_video_task

    svc = _FakeSvc(_task(status))
    with pytest.raises(HTTPException) as exc:
        await cancel_video_task(_make_request(svc), "vg_1")
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_service_cancel_task_exists():
    """服务层须有 cancel_task（与 delete_task 并列），供端点与其他调用方复用。"""
    from app.gateway._shared.task_framework.service import BaseTaskService

    assert hasattr(BaseTaskService, "cancel_task")


# ── 审查发现 3：cancelled 不在 TERMINAL_STATUSES，取消可被推翻 ──────────────
#
# _wrap_pipeline 的 CancelledError 分支（service.py:454）用 TERMINAL_STATUSES
# 判定「是否尊重终态、不自动重排」。该集合只有 {ready, failed}，不含 cancelled。
# 若 cancel_task 的 finally 已把 task_id 移出 _deleting，而被取消的协程此刻才
# 处理 CancelledError（任务卡在 CPU 密集段、未在 await 点），就会走进重排分支 →
# status 被改回 pending → **用户的取消被推翻、任务继续烧配额**。
#
# 同一集合还被 SSE 的 _render_is_terminal 消费（routers/video_gen.py:338）：
# cancelled 不算终态 → 取消后 SSE 流不结束，前端一直转圈。两个消费者都需要它。


def test_cancelled_render_is_terminal():
    """cancelled 必须算终态：既防重排推翻取消，也让 SSE 正常收流。"""
    from app.gateway._shared.task_framework.schemas import TERMINAL_STATUSES

    assert "cancelled" in TERMINAL_STATUSES


def test_sse_treats_cancelled_as_terminal():
    """取消后 SSE 必须发终止事件，否则前端永远转圈。"""
    from app.gateway.routers.video_gen import _render_is_terminal

    assert _render_is_terminal("cancelled") is True
    assert _render_is_terminal("ready") is True
    assert _render_is_terminal("failed") is True
    assert _render_is_terminal("composing") is False


def test_cancel_task_still_rejects_terminal():
    """cancel_task 的终态拒绝逻辑不得因集合变化而改变语义（仍拒 cancelled）。"""
    import inspect

    from app.gateway._shared.task_framework.service import BaseTaskService

    src = inspect.getsource(BaseTaskService.cancel_task)
    assert "TERMINAL_STATUSES" in src, "终态判定应继续复用同一集合"
