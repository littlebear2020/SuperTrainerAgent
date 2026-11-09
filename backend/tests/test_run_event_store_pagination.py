"""Tests for paginated list_messages_by_run on both RunEventStore backends.

MemoryRunEventStore is covered via the module-level ``base_store`` fixture;
DbRunEventStore (SQL) is covered by ``TestDbRunEventStorePagination`` against
a tmp sqlite file, mirroring the ``TestDbRunEventStore`` setup pattern in
tests/test_run_event_store.py. Both backends share the contract pinned here:
without ``after_seq`` the latest ``limit`` rows come back ascending, and
``after_seq`` pages forward from the cursor (asc + limit).
"""

import pytest

from deerflow.runtime.events.store.memory import MemoryRunEventStore


@pytest.fixture
def base_store():
    return MemoryRunEventStore()


@pytest.mark.anyio
async def test_list_messages_by_run_default_returns_all(base_store):
    store = base_store
    for i in range(7):
        await store.put(
            thread_id="t1",
            run_id="run-a",
            event_type="human_message" if i % 2 == 0 else "ai_message",
            category="message",
            content=f"msg-a-{i}",
        )
    for i in range(3):
        await store.put(
            thread_id="t1",
            run_id="run-b",
            event_type="human_message",
            category="message",
            content=f"msg-b-{i}",
        )
    await store.put(thread_id="t1", run_id="run-a", event_type="tool_call", category="trace", content="trace")

    msgs = await store.list_messages_by_run("t1", "run-a")
    assert len(msgs) == 7
    assert all(m["category"] == "message" for m in msgs)
    assert all(m["run_id"] == "run-a" for m in msgs)


@pytest.mark.anyio
async def test_list_messages_by_run_with_limit(base_store):
    store = base_store
    for i in range(7):
        await store.put(
            thread_id="t1",
            run_id="run-a",
            event_type="human_message" if i % 2 == 0 else "ai_message",
            category="message",
            content=f"msg-a-{i}",
        )

    msgs = await store.list_messages_by_run("t1", "run-a", limit=3)
    assert len(msgs) == 3
    seqs = [m["seq"] for m in msgs]
    assert seqs == sorted(seqs)


@pytest.mark.anyio
async def test_list_messages_by_run_after_seq(base_store):
    store = base_store
    for i in range(7):
        await store.put(
            thread_id="t1",
            run_id="run-a",
            event_type="human_message" if i % 2 == 0 else "ai_message",
            category="message",
            content=f"msg-a-{i}",
        )

    all_msgs = await store.list_messages_by_run("t1", "run-a")
    cursor_seq = all_msgs[2]["seq"]
    msgs = await store.list_messages_by_run("t1", "run-a", after_seq=cursor_seq, limit=50)
    assert all(m["seq"] > cursor_seq for m in msgs)
    assert len(msgs) == 4


@pytest.mark.anyio
async def test_list_messages_by_run_before_seq(base_store):
    store = base_store
    for i in range(7):
        await store.put(
            thread_id="t1",
            run_id="run-a",
            event_type="human_message" if i % 2 == 0 else "ai_message",
            category="message",
            content=f"msg-a-{i}",
        )

    all_msgs = await store.list_messages_by_run("t1", "run-a")
    cursor_seq = all_msgs[4]["seq"]
    msgs = await store.list_messages_by_run("t1", "run-a", before_seq=cursor_seq, limit=50)
    assert all(m["seq"] < cursor_seq for m in msgs)
    assert len(msgs) == 4


@pytest.mark.anyio
async def test_list_messages_by_run_does_not_include_other_run(base_store):
    store = base_store
    for i in range(7):
        await store.put(
            thread_id="t1",
            run_id="run-a",
            event_type="human_message",
            category="message",
            content=f"msg-a-{i}",
        )
    for i in range(3):
        await store.put(
            thread_id="t1",
            run_id="run-b",
            event_type="human_message",
            category="message",
            content=f"msg-b-{i}",
        )

    msgs = await store.list_messages_by_run("t1", "run-b")
    assert len(msgs) == 3
    assert all(m["run_id"] == "run-b" for m in msgs)


@pytest.mark.anyio
async def test_list_messages_by_run_empty_run(base_store):
    store = base_store
    msgs = await store.list_messages_by_run("t1", "nonexistent")
    assert msgs == []


# ---------------------------------------------------------------------------
# DbRunEventStore (SQL) — same contract, tmp sqlite
# ---------------------------------------------------------------------------


class TestDbRunEventStorePagination:
    """The SQL backend honors the same limit/after_seq/before_seq contract."""

    @staticmethod
    async def _make_store(tmp_path):
        from deerflow.persistence.engine import close_engine, get_session_factory, init_engine
        from deerflow.runtime.events.store.db import DbRunEventStore

        url = f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"
        await init_engine("sqlite", url=url, sqlite_dir=str(tmp_path))
        return DbRunEventStore(get_session_factory()), close_engine

    @staticmethod
    async def _seed(store) -> None:
        # 7 messages on run-a (seq 1..7), 3 on run-b (seq 8..10), 1 trace row
        # on run-a (seq 11) — seq is a per-thread counter across runs.
        for i in range(7):
            await store.put(thread_id="t1", run_id="run-a", event_type="human_message", category="message", content=f"msg-a-{i}")
        for i in range(3):
            await store.put(thread_id="t1", run_id="run-b", event_type="human_message", category="message", content=f"msg-b-{i}")
        await store.put(thread_id="t1", run_id="run-a", event_type="tool_call", category="trace", content="trace")

    @pytest.mark.anyio
    async def test_db_list_messages_by_run_limit_and_cursors(self, tmp_path):
        store, close_engine = await self._make_store(tmp_path)
        try:
            await self._seed(store)

            # Run isolation + category filter (the trace row must not appear).
            msgs = await store.list_messages_by_run("t1", "run-a")
            assert [m["content"] for m in msgs] == [f"msg-a-{i}" for i in range(7)]
            msgs = await store.list_messages_by_run("t1", "run-b")
            assert [m["content"] for m in msgs] == [f"msg-b-{i}" for i in range(3)]

            # Default (no after_seq): the LATEST ``limit`` rows, ascending.
            msgs = await store.list_messages_by_run("t1", "run-a", limit=3)
            assert [m["content"] for m in msgs] == ["msg-a-4", "msg-a-5", "msg-a-6"]

            all_msgs = await store.list_messages_by_run("t1", "run-a")

            # after_seq: forward from the cursor (strictly greater).
            cursor_seq = all_msgs[2]["seq"]
            msgs = await store.list_messages_by_run("t1", "run-a", after_seq=cursor_seq, limit=50)
            assert [m["content"] for m in msgs] == [f"msg-a-{i}" for i in range(3, 7)]

            # before_seq: the latest rows strictly below the cursor.
            cursor_seq = all_msgs[4]["seq"]
            msgs = await store.list_messages_by_run("t1", "run-a", before_seq=cursor_seq, limit=2)
            assert [m["content"] for m in msgs] == ["msg-a-2", "msg-a-3"]

            assert await store.list_messages_by_run("t1", "nonexistent") == []
        finally:
            await close_engine()

    @pytest.mark.anyio
    async def test_db_list_messages_by_run_after_seq_pages_forward(self, tmp_path):
        """after_seq + limit returns the FIRST ``limit`` rows after the cursor
        (asc + limit), so repeated calls walk the run forward without overlap."""
        store, close_engine = await self._make_store(tmp_path)
        try:
            await self._seed(store)

            first = await store.list_messages_by_run("t1", "run-a", limit=3)
            assert [m["content"] for m in first] == ["msg-a-4", "msg-a-5", "msg-a-6"]

            page = await store.list_messages_by_run("t1", "run-a", after_seq=first[0]["seq"], limit=2)
            assert [m["content"] for m in page] == ["msg-a-5", "msg-a-6"]

            page = await store.list_messages_by_run("t1", "run-a", after_seq=page[-1]["seq"], limit=2)
            assert [m["content"] for m in page] == []
        finally:
            await close_engine()
