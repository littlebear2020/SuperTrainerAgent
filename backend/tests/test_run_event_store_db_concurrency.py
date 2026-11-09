"""Concurrency tests for DbRunEventStore write serialization (A3, 2026-10-07).

Local ``put``/``put_batch`` assigned ``seq`` with a bare ``SELECT max(seq)``
and no in-process lock — two coroutines writing the same thread could both
read the same max and collide on ``UNIQUE(thread_id, seq)``; the failed
``put_batch`` then re-raised in the worker finally block and the whole batch
of run events was silently lost. The fix ports upstream's per-thread
``asyncio.Lock`` registry so the seq-assignment critical section (SELECT
max → INSERT → COMMIT) serializes per thread within the process.

The PostgreSQL advisory fence from upstream is deliberately NOT ported
(D8, 2026-10-07): production here is SQLite, where the fence is a no-op;
cross-process protection remains the DB-level UNIQUE constraint.
"""

from __future__ import annotations

import asyncio

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from deerflow.persistence.models.run_event import RunEventRow
from deerflow.runtime.events.store.db import DbRunEventStore


def _event(thread_id: str, run_id: str, marker: str) -> dict:
    return {
        "thread_id": thread_id,
        "run_id": run_id,
        "event_type": "llm.ai.response",
        "category": "message",
        "content": {"marker": marker},
        "metadata": {},
        "created_at": None,
    }


@pytest_asyncio.fixture(loop_scope="function")
async def store():
    """In-memory async SQLite with the run_events table (test_db_run_event_store.py 同款)."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(RunEventRow.__table__.create)
    sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield DbRunEventStore(sf, max_trace_content=10240)
    await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_put_batch_same_thread_no_collision_and_contiguous_seq(store):
    """5 个并发 put_batch 同 thread：零 IntegrityError，seq 精确连续 1..100。

    旧实现（无锁）下两个协程在 ``SELECT max(seq)`` 上读到同值，后者落
    ``UNIQUE(thread_id, seq)`` 撞仓 → IntegrityError（RED）。
    """
    batches = []
    for b in range(5):
        events = [_event("t1", "r1", f"b{b}_e{i}") for i in range(20)]
        batches.append(store.put_batch(events))

    results = await asyncio.gather(*batches)

    all_events = await store.list_events("t1", "r1", limit=1000)
    assert len(all_events) == 100
    seqs = sorted(e["seq"] for e in all_events)
    assert seqs == list(range(1, 101)), f"seq must be contiguous 1..100, got {seqs}"
    # gather 的每个批次自身返回的行也各自单调（批内不重排）。
    for batch_rows in results:
        batch_seqs = [r["seq"] for r in batch_rows]
        assert batch_seqs == sorted(batch_seqs)


@pytest.mark.asyncio
async def test_concurrent_put_and_put_batch_same_thread_serialize(store):
    """低频 put() 与高频 put_batch() 并发同 thread 也串行化，无撞仓。"""
    tasks = [store.put(thread_id="t1", run_id="r1", event_type="human_message", category="message", content="hi")]
    tasks.append(store.put_batch([_event("t1", "r1", f"e{i}") for i in range(10)]))

    await asyncio.gather(*tasks)

    all_events = await store.list_events("t1", "r1", limit=1000)
    assert len(all_events) == 11
    seqs = sorted(e["seq"] for e in all_events)
    assert seqs == list(range(1, 12))


@pytest.mark.asyncio
async def test_put_batch_rejects_mixed_threads(store):
    """混 thread 批次在进锁前显式拒绝（上游同款 ValueError 守卫）。"""
    events = [
        _event("tA", "r1", "a"),
        _event("tB", "r1", "b"),
    ]
    with pytest.raises(ValueError, match="same thread"):
        await store.put_batch(events)


@pytest.mark.asyncio
async def test_cross_thread_writes_do_not_block_on_each_others_locks(store):
    """锁按 thread 粒度：tA 的锁被外部持有时，tB 的写入照常完成。"""
    lock_a = store._get_write_lock("tA")
    async with lock_a:
        # tA 持锁期间并发写 tB——若锁误做成 store 级，这里会超时（RED）。
        await asyncio.wait_for(
            store.put_batch([_event("tB", "rB", f"e{i}") for i in range(5)]),
            timeout=5,
        )
    events = await store.list_events("tB", "rB", limit=100)
    assert len(events) == 5


@pytest.mark.asyncio
async def test_delete_by_thread_retires_write_lock_pin(store):
    """delete_by_thread 退役该 thread 的锁 pin（防长寿命 gateway 锁字典只增不减）。"""
    await store.put_batch([_event("t1", "r1", "e0")])
    assert "t1" in store._write_lock_pins

    await store.delete_by_thread("t1")

    assert "t1" not in store._write_lock_pins
    # 退役后再次写入：锁注册表应给出可用的新一代锁，写入正常。
    await store.put(thread_id="t1", run_id="r2", event_type="human_message", category="message", content="again")
    msgs = await store.list_messages("t1", limit=10)
    assert len(msgs) == 1
