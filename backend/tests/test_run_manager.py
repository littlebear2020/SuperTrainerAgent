"""Tests for RunManager."""

import re

import pytest

from deerflow.runtime import RunManager, RunStatus
from deerflow.runtime.runs.store.memory import MemoryRunStore

ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")


@pytest.fixture
def manager() -> RunManager:
    return RunManager()


@pytest.mark.anyio
async def test_create_and_get(manager: RunManager):
    """Created run should be retrievable with new fields."""
    record = await manager.create(
        "thread-1",
        "lead_agent",
        metadata={"key": "val"},
        kwargs={"input": {}},
        multitask_strategy="reject",
    )
    assert record.status == RunStatus.pending
    assert record.thread_id == "thread-1"
    assert record.assistant_id == "lead_agent"
    assert record.metadata == {"key": "val"}
    assert record.kwargs == {"input": {}}
    assert record.multitask_strategy == "reject"
    assert ISO_RE.match(record.created_at)
    assert ISO_RE.match(record.updated_at)

    fetched = await manager.get(record.run_id)
    assert fetched is record


@pytest.mark.anyio
async def test_status_transitions(manager: RunManager):
    """Status should transition pending -> running -> success."""
    record = await manager.create("thread-1")
    assert record.status == RunStatus.pending

    await manager.set_status(record.run_id, RunStatus.running)
    assert record.status == RunStatus.running
    assert ISO_RE.match(record.updated_at)

    await manager.set_status(record.run_id, RunStatus.success)
    assert record.status == RunStatus.success


@pytest.mark.anyio
async def test_cancel(manager: RunManager):
    """Cancel should set abort_event and transition to interrupted."""
    record = await manager.create("thread-1")
    await manager.set_status(record.run_id, RunStatus.running)

    cancelled = await manager.cancel(record.run_id)
    assert cancelled is True
    assert record.abort_event.is_set()
    assert record.status == RunStatus.interrupted


@pytest.mark.anyio
async def test_cancel_not_inflight(manager: RunManager):
    """Cancelling a completed run should return False."""
    record = await manager.create("thread-1")
    await manager.set_status(record.run_id, RunStatus.success)

    cancelled = await manager.cancel(record.run_id)
    assert cancelled is False


@pytest.mark.anyio
async def test_cancel_is_idempotent(manager: RunManager):
    """Cancelling an already-interrupted run reports success, not failure.

    The stop button can fire twice — a double click, or a reconnect racing the
    user. The second call hit the ``status not in (pending, running)`` guard
    and returned False, which the cancel route turns into a 409 and the
    frontend into an unhandled rejection. Cancelling something already
    cancelled achieved the caller's intent, so it is a success.
    """
    record = await manager.create("thread-1")
    await manager.set_status(record.run_id, RunStatus.running)

    assert await manager.cancel(record.run_id) is True
    assert record.status == RunStatus.interrupted

    # Second cancel: same desired end state, so same answer.
    assert await manager.cancel(record.run_id) is True
    assert record.status == RunStatus.interrupted


@pytest.mark.anyio
async def test_repeat_cancel_does_not_overwrite_the_original_action(manager: RunManager):
    """A repeat cancel is a no-op; it must not re-tag the abort action."""
    record = await manager.create("thread-1")
    await manager.set_status(record.run_id, RunStatus.running)

    assert await manager.cancel(record.run_id, action="rollback") is True
    assert record.abort_action == "rollback"

    assert await manager.cancel(record.run_id, action="interrupt") is True
    assert record.abort_action == "rollback"


@pytest.mark.anyio
async def test_cancel_unknown_run_still_returns_false(manager: RunManager):
    """Idempotency must not swallow a genuinely unknown run id (the 404 path)."""
    assert await manager.cancel("no-such-run") is False


@pytest.mark.anyio
async def test_cancel_terminal_states_other_than_interrupted_still_reject(manager: RunManager):
    """Only ``interrupted`` is idempotent — a finished run is still not cancellable."""
    for terminal in (RunStatus.success, RunStatus.error):
        record = await manager.create("thread-1")
        await manager.set_status(record.run_id, terminal)
        assert await manager.cancel(record.run_id) is False


@pytest.mark.anyio
async def test_reconcile_orphaned_inflight_runs_marks_stale_rows_error():
    """Startup recovery should turn persisted active rows into explicit errors."""
    store = MemoryRunStore()
    await store.put("pending-run", thread_id="thread-1", status="pending", created_at="2026-01-01T00:00:00+00:00")
    await store.put("running-run", thread_id="thread-1", status="running", created_at="2026-01-01T00:00:01+00:00")
    await store.put("success-run", thread_id="thread-1", status="success", created_at="2026-01-01T00:00:02+00:00")
    manager = RunManager(store=store)

    recovered = await manager.reconcile_orphaned_inflight_runs(
        error="Gateway restarted before this run reached a durable final state.",
        before="2026-01-01T00:00:02+00:00",
    )

    assert {record.run_id for record in recovered} == {"pending-run", "running-run"}
    pending_row = await store.get("pending-run")
    running_row = await store.get("running-run")
    success_row = await store.get("success-run")
    assert pending_row["status"] == "error"
    assert running_row["status"] == "error"
    assert success_row["status"] == "success"


@pytest.mark.anyio
async def test_reconcile_orphaned_inflight_runs_skips_live_local_run():
    """Startup recovery should not mark an active row orphaned when this worker owns it."""
    store = MemoryRunStore()
    manager = RunManager(store=store)
    record = await manager.create("thread-1")
    await manager.set_status(record.run_id, RunStatus.running)

    recovered = await manager.reconcile_orphaned_inflight_runs(
        error="Gateway restarted before this run reached a durable final state.",
    )

    stored = await store.get(record.run_id)
    assert recovered == []
    assert stored["status"] == "running"


@pytest.mark.anyio
async def test_list_by_thread(manager: RunManager):
    """Same thread should return multiple runs."""
    r1 = await manager.create("thread-1")
    r2 = await manager.create("thread-1")
    await manager.create("thread-2")

    runs = await manager.list_by_thread("thread-1")
    assert len(runs) == 2
    assert runs[0].run_id == r2.run_id
    assert runs[1].run_id == r1.run_id


@pytest.mark.anyio
async def test_list_by_thread_is_stable_when_timestamps_tie(manager: RunManager, monkeypatch: pytest.MonkeyPatch):
    """Ordering should be stable (insertion order) even when timestamps tie."""
    monkeypatch.setattr("deerflow.runtime.runs.manager._now_iso", lambda: "2026-01-01T00:00:00+00:00")

    r1 = await manager.create("thread-1")
    r2 = await manager.create("thread-1")

    runs = await manager.list_by_thread("thread-1")
    assert [run.run_id for run in runs] == [r1.run_id, r2.run_id]


@pytest.mark.anyio
async def test_has_inflight(manager: RunManager):
    """has_inflight should be True when a run is pending or running."""
    record = await manager.create("thread-1")
    assert await manager.has_inflight("thread-1") is True

    await manager.set_status(record.run_id, RunStatus.success)
    assert await manager.has_inflight("thread-1") is False


@pytest.mark.anyio
async def test_cleanup(manager: RunManager):
    """After cleanup, the run should be gone."""
    record = await manager.create("thread-1")
    run_id = record.run_id

    await manager.cleanup(run_id, delay=0)
    assert await manager.get(run_id) is None


@pytest.mark.anyio
async def test_set_status_with_error(manager: RunManager):
    """Error message should be stored on the record."""
    record = await manager.create("thread-1")
    await manager.set_status(record.run_id, RunStatus.error, error="Something went wrong")
    assert record.status == RunStatus.error
    assert record.error == "Something went wrong"


@pytest.mark.anyio
async def test_get_nonexistent(manager: RunManager):
    """Getting a nonexistent run should return None."""
    assert await manager.get("does-not-exist") is None


@pytest.mark.anyio
async def test_create_defaults(manager: RunManager):
    """Create with no optional args should use defaults."""
    record = await manager.create("thread-1")
    assert record.metadata == {}
    assert record.kwargs == {}
    assert record.multitask_strategy == "reject"
    assert record.assistant_id is None


@pytest.mark.anyio
async def test_list_by_thread_store_overlap_not_short_paged():
    """R-14 (2026-08-13): memory and store overlap for the same run_id — the
    old `limit - len(memory)` store fetch under-counted the dedup overlap and
    short-paged."""
    store = MemoryRunStore()
    manager = RunManager(store=store)

    # Three store-only rows from earlier runs.
    await store.put("s1", thread_id="thread-1", status="success", created_at="2026-01-01T00:00:03+00:00")
    await store.put("s2", thread_id="thread-1", status="success", created_at="2026-01-01T00:00:02+00:00")
    await store.put("s3", thread_id="thread-1", status="success", created_at="2026-01-01T00:00:01+00:00")
    # One live run in memory (also persisted to the store, creating overlap).
    r1 = await manager.create("thread-1")

    runs = await manager.list_by_thread("thread-1", limit=3)
    assert len(runs) == 3, f"short page: {[r.run_id for r in runs]}"
    assert {r.run_id for r in runs} == {r1.run_id, "s1", "s2"}


@pytest.mark.anyio
async def test_list_by_thread_other_worker_runs_not_hidden():
    """R-14 (2026-08-13): when memory is older than the store (multi-worker /
    restart), store-only rows newer than memory must still surface — the old
    store_limit = max(0, limit - |memory|) could hit 0 and hide them all."""
    store = MemoryRunStore()
    manager = RunManager(store=store)

    await manager.create("thread-1")
    await manager.create("thread-1")

    # Another worker wrote newer runs directly to the store.
    await store.put("w1", thread_id="thread-1", status="success", created_at="2099-01-01T00:00:02+00:00")
    await store.put("w2", thread_id="thread-1", status="success", created_at="2099-01-01T00:00:01+00:00")

    runs = await manager.list_by_thread("thread-1", limit=2)
    assert {r.run_id for r in runs} == {"w1", "w2"}, f"newer store runs hidden: {[r.run_id for r in runs]}"
