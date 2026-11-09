"""Graceful finish when a run exhausts its LangGraph recursion budget.

``GraphRecursionError`` means the graph burned through its super-step budget
(``recursion_limit``) without hitting a stop condition. The thread checkpoint
stays intact, so the user can resume from the break point by simply replying —
the worker must say that instead of surfacing raw langgraph exception text.
"""

from __future__ import annotations

import asyncio

import pytest
from langgraph.errors import GraphRecursionError

from deerflow.runtime.runs.manager import RunManager
from deerflow.runtime.runs.schemas import RunStatus
from deerflow.runtime.runs.worker import run_agent


class _Bridge:
    """Captures every SSE event run_agent publishes."""

    def __init__(self):
        self.published: list[tuple[str, str, object]] = []

    async def publish(self, run_id, event, data):
        self.published.append((run_id, event, data))

    async def publish_end(self, run_id):
        self.published.append((run_id, "end", None))

    async def cleanup(self, run_id, *, delay=60):
        pass


class _Ctx:
    checkpointer = None
    store = None
    event_store = None
    run_events_config = None
    thread_store = None
    app_config = None


def _make_agent_factory(exc: BaseException):
    """Build a factory whose agent's astream raises *exc* on the first chunk.

    run_agent calls agent_factory(...) synchronously, matching real factories
    like make_lead_agent — a plain def, not async.
    """

    class _Agent:
        checkpointer = None
        store = None

        async def astream(self, *a, **kw):
            raise exc
            yield  # pragma: no cover - marks this as an async generator

    def _factory(config=None):
        return _Agent()

    return _factory


@pytest.fixture()
def _fast_run_cleanup(monkeypatch):
    """Keep the finally block's delayed eviction from outliving the test loop."""

    def fake_cleanup(self, run_id, *, delay=300):
        return asyncio.sleep(0)

    monkeypatch.setattr(RunManager, "cleanup", fake_cleanup)


@pytest.mark.asyncio()
async def test_graph_recursion_error_surfaces_user_actionable_message(_fast_run_cleanup):
    mgr = RunManager(store=None)
    record = await mgr.create("t1")
    bridge = _Bridge()

    await run_agent(
        bridge,
        mgr,
        record,
        ctx=_Ctx(),
        agent_factory=_make_agent_factory(GraphRecursionError("Recursion limit of 2500 reached without hitting a stop condition")),
        graph_input={},
        config={"recursion_limit": 2500},
    )

    assert record.status == RunStatus.error
    # User-actionable guidance, not raw exception text alone.
    assert record.error is not None
    assert "步数预算" in record.error
    assert "继续" in record.error
    # Raw langgraph error stays appended for diagnosability.
    assert "Recursion limit of 2500" in record.error

    error_events = [data for (_rid, event, data) in bridge.published if event == "error"]
    assert len(error_events) == 1
    assert error_events[0]["message"] == record.error
    assert error_events[0]["name"] == "GraphRecursionError"

    # The stream must still terminate cleanly for the SSE client.
    assert any(event == "end" for (_rid, event, _data) in bridge.published)


@pytest.mark.asyncio()
async def test_generic_exception_still_uses_raw_text(_fast_run_cleanup):
    """The recursion branch must not swallow other failures: a plain runtime
    error keeps the old raw-text behaviour."""
    mgr = RunManager(store=None)
    record = await mgr.create("t1")
    bridge = _Bridge()

    await run_agent(
        bridge,
        mgr,
        record,
        ctx=_Ctx(),
        agent_factory=_make_agent_factory(RuntimeError("provider exploded")),
        graph_input={},
        config={},
    )

    assert record.status == RunStatus.error
    assert record.error == "provider exploded"
    error_events = [data for (_rid, event, data) in bridge.published if event == "error"]
    assert len(error_events) == 1
    assert error_events[0]["name"] == "RuntimeError"
