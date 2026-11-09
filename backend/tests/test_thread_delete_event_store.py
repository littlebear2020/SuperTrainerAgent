"""Thread deletion must purge the run event store.

OOM root cause #1: MemoryRunEventStore._events grew unbounded because the
DELETE /api/threads/{id} route never called event_store.delete_by_thread.
This test covers both memory and db backends (the route must clean regardless
of backend so the SQL run_events table doesn't grow forever either)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_delete_thread_purges_event_store():
    """DELETE /api/threads/{id} calls event_store.delete_by_thread(thread_id)."""
    from app.gateway.routers import threads as threads_router

    # Fake event store that records delete_by_thread calls
    event_store = MagicMock()
    event_store.delete_by_thread = AsyncMock(return_value=3)

    # Fake thread_store
    thread_store = MagicMock()
    thread_store.delete = AsyncMock()

    # Fake checkpointer with adelete_thread
    checkpointer = MagicMock()
    checkpointer.adelete_thread = AsyncMock()

    app = MagicMock()
    app.state.checkpointer = checkpointer
    app.state.thread_store = thread_store
    app.state.run_event_store = event_store

    request = MagicMock()
    request.app = app

    # NOTE: call the UNWRAPPED route (.__wrapped__). The route is decorated with
    # @router.delete + @require_permission(..., owner_check=True, require_existing=True).
    # The decorated form would either TypeError (request bound positionally while
    # the authz wrapper injects its own stub) or fail auth/owner checks the mock
    # request can't satisfy. @functools.wraps guarantees .__wrapped__ -> the raw
    # route body, which reads checkpointer/thread_store/run_event_store from
    # request.app.state via deps._require (satisfied by our fakes).
    await threads_router.delete_thread_data.__wrapped__("t1", request)

    # event_store.delete_by_thread MUST have been called with the thread_id
    event_store.delete_by_thread.assert_awaited_once_with("t1")
