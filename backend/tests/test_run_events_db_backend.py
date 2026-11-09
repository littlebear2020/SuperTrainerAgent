"""Verify run_events.backend=db works on sqlite: table auto-creates,
events persist, delete_by_thread clears them. This is the config change
that stops MemoryRunEventStore from holding all events in process RAM
(gateway OOM root cause #1)."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_db_run_event_store_roundtrip(tmp_path):
    from deerflow.config.database_config import DatabaseConfig
    from deerflow.config.run_events_config import RunEventsConfig
    from deerflow.persistence import engine as engine_mod
    from deerflow.runtime.events.store import make_run_event_store

    # Minimal sqlite database config — satisfies the precondition for
    # run_events.backend=db (get_session_factory() must not return None).
    db_cfg = DatabaseConfig(backend="sqlite", sqlite_dir=str(tmp_path))
    await engine_mod.init_engine_from_config(db_cfg)
    try:
        cfg = RunEventsConfig(backend="db", max_trace_content=10240, track_token_usage=True)
        store = make_run_event_store(cfg)

        # put an event
        await store.put(
            thread_id="t1",
            run_id="r1",
            event_type="message",
            category="message",
            content={"role": "assistant", "content": "hello"},
        )
        msgs = await store.list_messages("t1", limit=10)
        assert len(msgs) == 1
        assert msgs[0]["run_id"] == "r1"

        # delete_by_thread clears them (this is what the thread-delete fix relies on)
        removed = await store.delete_by_thread("t1")
        assert removed == 1
        msgs_after = await store.list_messages("t1", limit=10)
        assert len(msgs_after) == 0
    finally:
        await engine_mod.close_engine()


@pytest.mark.asyncio
async def test_db_backend_falls_back_to_memory_when_no_database(monkeypatch):
    """Precondition guard: if database.backend=memory, run_events.backend=db
    silently falls back to MemoryRunEventStore. Documents the hidden dependency
    that database.backend must be non-memory for db to take effect."""
    import deerflow.persistence.engine as engine_mod
    from deerflow.runtime.events.store import make_run_event_store
    from deerflow.runtime.events.store.memory import MemoryRunEventStore

    # Force get_session_factory() to return None (simulates database.backend=memory)
    monkeypatch.setattr(engine_mod, "get_session_factory", lambda: None)

    from deerflow.config.run_events_config import RunEventsConfig

    cfg = RunEventsConfig(backend="db")
    store = make_run_event_store(cfg)
    assert isinstance(store, MemoryRunEventStore), "db backend must fall back to memory when no SQL session factory"
