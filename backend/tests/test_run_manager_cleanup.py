"""RunManager cleanup: completed runs must be evicted from in-memory _runs
to prevent unbounded memory growth (gateway OOM root cause #2)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

# NOTE: DisconnectMode / RunStatus live in runs/schemas.py and are re-exported
# by manager.py (which imports them at module level). There is no runs/types.py.
from deerflow.runtime.runs.manager import DisconnectMode, RunManager, RunRecord, RunStatus


def _make_record(run_id: str = "run-1", thread_id: str = "thread-1") -> RunRecord:
    return RunRecord(
        run_id=run_id,
        thread_id=thread_id,
        assistant_id="agent",
        status=RunStatus.pending,
        on_disconnect=DisconnectMode.continue_,
    )


@pytest.mark.asyncio
async def test_cleanup_removes_run_from_memory_after_delay():
    """RunManager.cleanup(run_id) evicts the RunRecord from _runs."""
    mgr = RunManager(store=None)
    record = _make_record()
    async with mgr._lock:
        mgr._runs[record.run_id] = record

    # Before cleanup: present
    assert await mgr.get(record.run_id) is not None

    # cleanup with delay=0 removes immediately
    await mgr.cleanup(record.run_id, delay=0)

    assert record.run_id not in mgr._runs
    # store=None -> get returns None after eviction (no SQL fallback in this unit test)
    assert await mgr.get(record.run_id) is None


@pytest.mark.asyncio
async def test_get_falls_back_to_store_after_memory_eviction():
    """After cleanup evicts the in-memory record, get() hydrates from the
    SQL store — this is why evicting completed runs is safe for /runs/{rid}."""
    mgr = RunManager(store=AsyncMock())
    record = _make_record()
    async with mgr._lock:
        mgr._runs[record.run_id] = record

    # Simulate store having the row (as it would for a completed+persisted run)
    mgr._store.get = AsyncMock(
        return_value={
            "run_id": record.run_id,
            "thread_id": record.thread_id,
            "assistant_id": record.assistant_id,
            "status": "success",
            "multitask_strategy": "reject",
            "metadata": {},
            "kwargs": {},
            "created_at": "2026-07-07T00:00:00+00:00",
        }
    )

    await mgr.cleanup(record.run_id, delay=0)
    assert record.run_id not in mgr._runs

    # get() should hydrate from store, NOT return None
    hydrated = await mgr.get(record.run_id)
    assert hydrated is not None
    assert hydrated.run_id == record.run_id


@pytest.mark.asyncio
async def test_run_agent_schedules_run_manager_cleanup(monkeypatch):
    """run_agent's finally block must schedule run_manager.cleanup so
    completed RunRecords don't leak (OOM root cause #2)."""
    scheduled: list = []

    real_create_task = asyncio.create_task

    def spy_create_task(coro, **kw):
        scheduled.append(coro)
        return real_create_task(coro, **kw)

    mgr = RunManager(store=None)
    record = _make_record()
    record.status = RunStatus.running
    record.task = real_create_task(asyncio.sleep(0))  # dummy task
    async with mgr._lock:
        mgr._runs[record.run_id] = record

    cleanup_called: list = []

    # NOTE: fake_cleanup is a plain `def` (not async). run_agent schedules it
    # via `asyncio.create_task(run_manager.cleanup(...))`; if it were async,
    # calling it would only return a coroutine and the append below would not
    # run until that coroutine is awaited (i.e. after the test asserts). A
    # plain def records the call at call-time and returns a coroutine for
    # create_task to schedule.
    def fake_cleanup(run_id, *, delay=300):
        cleanup_called.append((run_id, delay))
        return asyncio.sleep(0)  # coroutine for asyncio.create_task

    monkeypatch.setattr(mgr, "cleanup", fake_cleanup)

    from deerflow.runtime.runs.worker import run_agent

    class _StubAgent:
        def __init__(self):
            self.checkpointer = None
            self.store = None

        async def astream(self, *a, **kw):
            if False:  # never yields
                yield {}

    # run_agent calls agent_factory(...) SYNCHRONOUSLY (worker.py:212-214, no await),
    # matching real factories like make_lead_agent. A regular def, not async.
    def _stub_factory(config=None, app_config=None):
        return _StubAgent()

    class _Ctx:
        checkpointer = None
        store = None
        event_store = None
        run_events_config = None
        thread_store = None
        app_config = None

    class _Bridge:
        published = []

        async def publish(self, run_id, event, data):
            self.published.append((run_id, event))

        async def publish_end(self, run_id):
            self.published.append((run_id, "end"))

        async def cleanup(self, run_id, *, delay=60):
            self.published.append((run_id, "bridge_cleanup"))

    bridge = _Bridge()
    monkeypatch.setattr(asyncio, "create_task", spy_create_task)

    await run_agent(
        bridge,
        mgr,
        record,
        ctx=_Ctx(),
        agent_factory=_stub_factory,
        graph_input={},
        config={},
    )

    assert len(cleanup_called) == 1, f"run_manager.cleanup must be scheduled once, got {cleanup_called}"
    rid, delay = cleanup_called[0]
    assert rid == record.run_id
    assert delay == 60, "cleanup delay must be 60s (aligns with bridge.cleanup; post-stream endpoints use event_store/run_store, not _runs)"
