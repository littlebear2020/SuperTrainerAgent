"""Tests for DbRunEventStore serialization (P0: list-type poisoning).

`on_chain_end` passes the chain `outputs` — frequently a list — as event
content. DbRunEventStore only serialized dict content, so a list raised
`sqlite3.ProgrammingError('type list is not supported')` and rolled back the
ENTIRE put_batch transaction — every message event for the run was lost
(run_events had 0 rows vs 53k checkpoints).
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from deerflow.persistence.models.run_event import RunEventRow
from deerflow.runtime.events.store.db import DbRunEventStore


@pytest_asyncio.fixture(loop_scope="function")
async def store():
    """In-memory async SQLite with the run_events table.

    Async fixture (mirrors tests/test_task_framework.py) — a sync create on
    aiosqlite raises MissingGreenlet, and the known asyncio-contamination means
    run_until_complete can't be used. pytest_asyncio gives the fixture its loop.
    """
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(RunEventRow.__table__.create)
    sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield DbRunEventStore(sf, max_trace_content=10240)  # max_trace_content is keyword-only (db.py:24)
    await engine.dispose()


@pytest.mark.asyncio
async def test_put_batch_persists_dict_content(store):
    events = [
        {
            "thread_id": "t1",
            "run_id": "r1",
            "event_type": "llm.ai.response",
            "category": "message",
            "content": {"role": "assistant", "content": "hi"},
            "metadata": {},
            "created_at": None,
        }
    ]
    await store.put_batch(events)
    msgs = await store.list_messages("t1", limit=10)
    assert len(msgs) == 1
    assert msgs[0]["content"] == {"role": "assistant", "content": "hi"}


@pytest.mark.asyncio
async def test_put_batch_persists_list_content(store):
    """The P0 bug: chain outputs arrive as a list and rolled back the whole batch."""
    events = [
        {
            "thread_id": "t1",
            "run_id": "r1",
            "event_type": "run.end",
            "category": "outputs",
            "content": [{"foo": 1}, {"bar": 2}],
            "metadata": {"status": "success"},
            "created_at": None,
        },
        {
            "thread_id": "t1",
            "run_id": "r1",
            "event_type": "llm.ai.response",
            "category": "message",
            "content": {"role": "assistant", "content": "after"},
            "metadata": {},
            "created_at": None,
        },
    ]
    await store.put_batch(events)
    # Both events must survive — previously the list content rolled back the batch.
    count = await store.count_messages("t1")
    assert count == 1, "message event lost because sibling list-content event poisoned the batch"


@pytest.mark.asyncio
async def test_list_content_roundtrips_as_list(store):
    events = [
        {
            "thread_id": "t1",
            "run_id": "r1",
            "event_type": "run.end",
            "category": "outputs",
            "content": [1, 2, 3],
            "metadata": {},
            "created_at": None,
        }
    ]
    await store.put_batch(events)
    rows = await store.list_events("t1", "r1")
    assert len(rows) == 1
    assert rows[0]["content"] == [1, 2, 3]
