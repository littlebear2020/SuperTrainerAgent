"""Task 0b (闸0b): per-user in-flight task cap.

One user opening 2-3 tasks could otherwise hog all global orchestration slots
(闸0), leaving other users queued behind them. BaseTaskService adds a per-user
asyncio.Semaphore acquired OUTSIDE the global _concurrency inside _wrap_pipeline:
a same-user task over the cap blocks on its user gate without holding a global
slot, so other users keep flowing. No pending poller exists in this codebase,
so we reuse the existing Semaphore-blocking queue (surplus task's coroutine
simply awaits) rather than inventing a scheduler.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.gateway._shared.task_framework.service import BaseTaskService


class _StubService(BaseTaskService):
    """Minimal concrete BaseTaskService for testing the per-user gate."""

    domain = "test"
    id_prefix = "t"

    async def _run_pipeline(self, task_id, user_id, task_type, payload, task_dir, progress):
        return None


def _make(storage_dir, max_tasks_per_user: int = 2) -> _StubService:
    return _StubService(
        repo=MagicMock(),
        storage_dir=str(storage_dir),
        max_tasks_per_user=max_tasks_per_user,
    )


def test_per_user_sem_is_stable_and_isolated(tmp_path):
    svc = _make(tmp_path, max_tasks_per_user=2)
    assert svc._get_user_sem("u1") is svc._get_user_sem("u1")  # same user → same sem
    assert svc._get_user_sem("u1") is not svc._get_user_sem("u2")  # different users → different
    # 信号量容量（=2）的行为证明见下方 test_same_user_capped_at_limit 的 peak == 2 断言——
    # 不再戳 asyncio.Semaphore._value（CPython 私有实现细节）。


@pytest.mark.asyncio
async def test_same_user_capped_at_limit(tmp_path):
    """5 个同用户任务、cap=2：并发峰值恰为 2（≤ 证明不超发；== 证明容量真是 2，
    容量退化为 1 或放宽到 3+ 都会红）。"""
    svc = _make(tmp_path, max_tasks_per_user=2)
    sem = svc._get_user_sem("u1")
    active = 0
    peak = 0
    lock = asyncio.Lock()

    async def hold():
        nonlocal active, peak
        async with sem:
            async with lock:
                active += 1
                peak = max(peak, active)
            await asyncio.sleep(0.02)
            async with lock:
                active -= 1

    await asyncio.gather(*(hold() for _ in range(5)))  # 5 same-user tasks, cap 2
    assert peak == 2


@pytest.mark.asyncio
async def test_wrap_pipeline_caps_same_user_concurrency(tmp_path):
    """Integration: _wrap_pipeline acquires the per-user sem at the real entry
    point, so with cap=1 two same-user pipelines never overlap (second waits for
    the first to finish). Proves the cap actually bites in the orchestration path,
    not just the _get_user_sem primitive."""
    svc = _make(tmp_path, max_tasks_per_user=1)
    # AsyncMock repo so ProgressUpdater's awaited repo writes become no-ops.
    svc.repo = AsyncMock()

    in_flight = 0
    peak = 0
    peak_lock = asyncio.Lock()

    async def pipeline(task_id, user_id, task_type, payload, task_dir, progress):
        nonlocal in_flight, peak
        async with peak_lock:
            in_flight += 1
            peak = max(peak, in_flight)
        await asyncio.sleep(0.02)
        async with peak_lock:
            in_flight -= 1

    svc._run_pipeline = pipeline

    await asyncio.gather(
        svc._wrap_pipeline("t1", "u1", "test", {}),
        svc._wrap_pipeline("t2", "u1", "test", {}),
    )
    assert peak == 1, f"per-user cap=1 should serialize same-user pipelines, peak={peak}"


@pytest.mark.asyncio
async def test_wrap_pipeline_does_not_block_other_users(tmp_path):
    """Different users get independent sems — two users' pipelines run concurrently
    even at cap=1 each (proves isolation, not a single global user gate)."""
    svc = _make(tmp_path, max_tasks_per_user=1)
    svc.repo = AsyncMock()

    started: dict[str, bool] = {}

    async def pipeline(task_id, user_id, task_type, payload, task_dir, progress):
        started[task_id] = True
        await asyncio.sleep(0.02)

    svc._run_pipeline = pipeline

    await asyncio.gather(
        svc._wrap_pipeline("t1", "u1", "test", {}),
        svc._wrap_pipeline("t2", "u2", "test", {}),
    )
    assert len(started) == 2, "different users should not block each other"


@pytest.mark.asyncio
async def test_default_max_tasks_per_user_is_3(tmp_path):
    """Without an explicit kwarg, the per-user cap defaults to 3 (BaseTaskService
    default; VideoGenService injects config.max_tasks_per_user, also default 3).

    行为断言（不戳 Semaphore._value 私有位）：5 个同用户持有者并发，峰值恰为 3——
    默认值改成 2 或 4 都会红（asyncio FIFO 唤醒下确定性成立）。"""
    # Construct WITHOUT the kwarg so the BaseTaskService default kicks in.
    svc = _StubService(repo=MagicMock(), storage_dir=str(tmp_path))
    sem = svc._get_user_sem("any")
    active = 0
    peak = 0
    lock = asyncio.Lock()

    async def hold():
        nonlocal active, peak
        async with sem:
            async with lock:
                active += 1
                peak = max(peak, active)
            await asyncio.sleep(0.02)
            async with lock:
                active -= 1

    await asyncio.gather(*(hold() for _ in range(5)))
    assert peak == 3, f"默认 per-user cap 应为 3，实测峰值 {peak}"
