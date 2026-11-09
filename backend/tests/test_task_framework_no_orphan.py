"""P1-2: recover_stale_tasks 不得覆盖仍在跑的 handle 造孤儿。"""

import asyncio
from unittest.mock import MagicMock

import pytest

from app.gateway._shared.task_framework.service import BaseTaskService


class _FakeRepo:
    """In-memory fake TaskRepository for retry tests."""

    def __init__(self):
        self._tasks = {}

    async def get(self, task_id, user_id=None):
        return self._tasks.get(task_id)

    async def update(self, task_id, **kwargs):
        task = self._tasks.get(task_id)
        if task:
            for k, v in kwargs.items():
                setattr(task, k, v)

    async def create(self, **kwargs):
        task = MagicMock()
        task.id = kwargs["id"]
        task.user_id = kwargs["user_id"]
        task.domain = kwargs["domain"]
        task.task_type = kwargs["task_type"]
        task.payload = kwargs["payload"]
        task.status = "pending"
        task.progress = 0
        task.result = "{}"
        task.retry_count = 0
        self._tasks[task.id] = task
        return task

    async def delete(self, task_id):
        self._tasks.pop(task_id, None)

    async def list_by_user(self, user_id, domain=None, limit=100):
        return list(self._tasks.values())

    async def list_stale_for_recovery(self, domain, statuses):
        return [t for t in self._tasks.values() if t.status in statuses and t.domain == domain]


class _Svc(BaseTaskService):
    """Concrete subclass for testing recover_stale_tasks."""

    domain = "test"
    id_prefix = "ts"
    max_retries = 2
    GATEWAY_STAGES = ("pending", "running")

    def __init__(self, repo, storage_dir):
        self.repo = repo
        self._storage_dir = storage_dir
        self._active_tasks = {}
        self._concurrency = asyncio.Semaphore(5)
        self._max_tasks_per_user = 3
        self._user_sems: dict[str, asyncio.Semaphore] = {}
        self._deleting: set[str] = set()
        self._recovering: set[str] = set()
        self._reaper_task = None

    def _get_user_sem(self, user_id: str) -> asyncio.Semaphore:
        sem = self._user_sems.get(user_id)
        if sem is None:
            sem = asyncio.Semaphore(self._max_tasks_per_user)
            self._user_sems[user_id] = sem
        return sem

    async def _run_pipeline(self, task_id, user_id, task_type, payload, task_dir, progress):
        raise NotImplementedError

    def _should_handoff_to_worker(self, task_type):
        return False

    async def _cleanup_task(self, task):
        pass


@pytest.mark.asyncio
async def test_recover_cancels_existing_handle_before_replacing(tmp_path):
    """recover_stale_tasks 必须先 cancel 旧 handle，不得留下孤儿 task 并发跑两遍。"""
    started: list[str] = []

    class _LongSvc(_Svc):
        async def _run_pipeline(self, task_id, user_id, task_type, payload, task_dir, progress):
            started.append(task_id)
            await asyncio.sleep(10)  # 长跑，模拟仍在渲染

    repo = _FakeRepo()
    task = MagicMock(
        id="ts_orphan",
        user_id="u1",
        domain="test",
        task_type="ppt_gen",
        payload="{}",
        status="pending",
        progress=0,
        result="{}",
        retry_count=0,
    )
    repo._tasks[task.id] = task
    svc = _LongSvc(repo, str(tmp_path))

    # 模拟 process 启动了一个任务（_active_tasks 有 handle）
    old_task = asyncio.create_task(svc._wrap_pipeline(task.id, task.user_id, task.task_type, {}))
    svc._active_tasks[task.id] = old_task

    # 等待 pipeline 真正开始
    await asyncio.sleep(0.05)
    assert not old_task.done(), "前置条件：旧 task 应在跑"

    # recover_stale_tasks 应先 cancel 旧 handle 再创建新的
    count = await svc.recover_stale_tasks()

    # 旧 handle 应被 cancel
    assert old_task.cancelled() or old_task.done(), "旧 handle 必须被 cancel"

    # 2026-08-08 P1-2/P1-4：等被调度的 pipeline 起跑后断言执行次数。
    # recover 竞态（修前）：recover 先把 status 置回 pending，旧 handle 的 B1
    # 自愈分支见非终态 → 自我重排；recover 又建一个新 task → started 出现
    # 第 3 次执行（双跑）。修后 B1 见 _recovering 标记放弃重排。
    await asyncio.sleep(0.1)
    runs_after_recover = started.count(task.id)
    assert runs_after_recover == 2, f"pipeline 应恰好执行 2 次（旧 1 + recover 新 1），实际 {runs_after_recover} 次——>2 说明 recover 与旧 handle 的 B1 自愈分支双跑"

    # 清理新创建的 task：必须先登记 ``_deleting``（显式删除语义）再 cancel——
    # 裸 cancel 会被 _wrap_pipeline 的 B1 自愈分支当作瞬时取消吸收并重排一个
    # 新 task（retry 预算未耗尽时），残留 task 再跑满 _run_pipeline 的
    # sleep(10)（实测把本用例 teardown 拖满 10s）。对齐 delete_task/cancel_task。
    svc._deleting.add(task.id)
    new_task = svc._active_tasks.get(task.id)
    if new_task and not new_task.done():
        new_task.cancel()
        try:
            await new_task
        except (asyncio.CancelledError, Exception):
            pass
    svc._deleting.discard(task.id)

    assert count >= 1, "recover 应至少处理 1 个 stale task"
