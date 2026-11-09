"""GET /api/threads/{id}/messages must attach feedback to the last AI message.

Regression for the ``feedback`` field being permanently ``None``: the route
matched ``event_type == "ai_message"``, a string the write side never emits.
``RunJournal`` canonically persists AI messages as ``llm.ai.response``.

The messages here are produced by driving **RunJournal itself** (the real
write path) into a real ``MemoryRunEventStore``. Hand-injecting rows with a
chosen ``event_type`` cannot falsify this bug — it would bake the read side's
own assumption into the fixture.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from _router_auth_helpers import make_authed_test_app
from fastapi.testclient import TestClient

from app.gateway.routers import thread_runs
from deerflow.runtime.events.store.memory import MemoryRunEventStore
from deerflow.runtime.journal import RunJournal

THREAD_ID = "thread-fb"
RUN_ID = "run-fb"


def _ai_response(content: str):
    """Minimal AIMessage-shaped stand-in accepted by RunJournal.on_llm_end."""
    msg = MagicMock()
    msg.type = "ai"
    msg.content = content
    msg.id = f"msg-{id(msg)}"
    msg.tool_calls = []
    msg.invalid_tool_calls = []
    msg.response_metadata = {"model_name": "test-model"}
    msg.usage_metadata = None
    msg.additional_kwargs = {}
    msg.name = None
    msg.model_dump.return_value = {
        "content": content,
        "additional_kwargs": {},
        "response_metadata": {"model_name": "test-model"},
        "type": "ai",
        "name": None,
        "id": msg.id,
        "tool_calls": [],
        "invalid_tool_calls": [],
        "usage_metadata": None,
    }
    gen = MagicMock()
    gen.message = msg
    response = MagicMock()
    response.generations = [[gen]]
    return response


async def _store_with_real_journal_events() -> MemoryRunEventStore:
    """Write two AI responses through the production RunJournal write path."""
    store = MemoryRunEventStore()
    journal = RunJournal(RUN_ID, THREAD_ID, store, flush_threshold=100)
    for text in ("first answer", "second answer"):
        llm_run_id = uuid4()
        journal.on_llm_start({}, [], run_id=llm_run_id, tags=["lead_agent"])
        journal.on_llm_end(_ai_response(text), run_id=llm_run_id, parent_run_id=None, tags=["lead_agent"])
    await journal.flush()
    return store


def _make_app(store: MemoryRunEventStore, feedback: dict) -> object:
    app = make_authed_test_app()
    app.include_router(thread_runs.router)
    app.state.run_event_store = store

    feedback_repo = MagicMock()
    feedback_repo.list_by_thread_grouped = AsyncMock(return_value=feedback)
    app.state.feedback_repo = feedback_repo
    return app


def test_feedback_attached_to_last_ai_message_written_by_journal(anyio_backend=None):
    """Feedback must land on the final ``llm.ai.response`` of the run."""
    import asyncio

    store = asyncio.run(_store_with_real_journal_events())

    # Sanity-check the fixture reflects the real write path, not our guess.
    written = [e["event_type"] for e in store._events[THREAD_ID]]
    assert "llm.ai.response" in written, f"write path changed; got {written}"
    assert "ai_message" not in written, "RunJournal does not emit 'ai_message'"

    fb = {RUN_ID: {"feedback_id": "fb-1", "rating": 1, "comment": "good"}}
    app = _make_app(store, fb)

    with TestClient(app) as client:
        response = client.get(f"/api/threads/{THREAD_ID}/messages")

    assert response.status_code == 200
    messages = response.json()
    ai_messages = [m for m in messages if m["event_type"] == "llm.ai.response"]
    assert len(ai_messages) == 2

    # Only the LAST AI message of the run carries feedback.
    assert ai_messages[-1]["feedback"] == {"feedback_id": "fb-1", "rating": 1, "comment": "good"}
    assert ai_messages[0]["feedback"] is None


def test_no_feedback_row_yields_explicit_none():
    """With no stored feedback the last AI message still reports ``None``."""
    import asyncio

    store = asyncio.run(_store_with_real_journal_events())
    app = _make_app(store, {})

    with TestClient(app) as client:
        response = client.get(f"/api/threads/{THREAD_ID}/messages")

    assert response.status_code == 200
    assert all(m["feedback"] is None for m in response.json())
