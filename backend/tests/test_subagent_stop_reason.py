"""Distinguish "subagent finished" from "subagent was killed" (P2-2).

Before this, every terminal subagent path collapsed into a status enum plus a
free-text ``error`` blob. A delegation that was cancelled by the user, one that
burned its wall-clock timeout, and one that exhausted its turn budget were
indistinguishable to the lead agent (and to anyone reading the trace) without
parsing English error text — and a turn-budget exhaustion looked like a plain
crash because ``GraphRecursionError`` fell through the generic
``except Exception`` handler.

``stop_reason`` is the additive channel for that: the status enum keeps its
existing meaning (so nothing downstream breaks), and the machine-readable reason
rides alongside it.
"""

from __future__ import annotations

import asyncio
import importlib
import sys
from concurrent.futures import TimeoutError as FuturesTimeoutError
from enum import Enum
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from deerflow.subagents.config import SubagentConfig

task_tool_module = importlib.import_module("deerflow.tools.builtins.task_tool")


class FakeSubagentStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


def _make_runtime() -> SimpleNamespace:
    return SimpleNamespace(
        state={"sandbox": {"sandbox_id": "local"}, "thread_data": {}},
        context={"thread_id": "thread-1"},
        config={"metadata": {"model_name": "ark-model", "trace_id": "trace-1"}},
    )


def _make_subagent_config() -> SubagentConfig:
    return SubagentConfig(
        name="general-purpose",
        description="General helper",
        system_prompt="Base system prompt",
        max_turns=50,
        timeout_seconds=10,
    )


def _make_result(
    status: FakeSubagentStatus,
    *,
    result: str | None = None,
    error: str | None = None,
    stop_reason: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        status=status,
        ai_messages=[],
        token_records=[],
        result=result,
        error=error,
        stop_reason=stop_reason,
    )


# conftest.py mocks deerflow.subagents.executor to break a circular import, so
# the real module has to be loaded behind that mock — same approach as
# test_subagent_executor.py's session fixture.
_MOCKED_MODULE_NAMES = [
    "deerflow.agents",
    "deerflow.agents.thread_state",
    "deerflow.agents.middlewares",
    "deerflow.agents.middlewares.thread_data_middleware",
    "deerflow.sandbox",
    "deerflow.sandbox.middleware",
    "deerflow.sandbox.security",
    "deerflow.models",
]


@pytest.fixture
def executor_classes():
    """Import the real executor module with its circular deps stubbed out."""
    original_modules = {name: sys.modules.get(name) for name in _MOCKED_MODULE_NAMES}
    original_executor = sys.modules.get("deerflow.subagents.executor")

    if "deerflow.subagents.executor" in sys.modules:
        del sys.modules["deerflow.subagents.executor"]
    for name in _MOCKED_MODULE_NAMES:
        sys.modules[name] = MagicMock()

    import deerflow.subagents.executor as real_executor

    yield real_executor

    for name in _MOCKED_MODULE_NAMES:
        if original_modules[name] is not None:
            sys.modules[name] = original_modules[name]
        elif name in sys.modules:
            del sys.modules[name]
    if original_executor is not None:
        sys.modules["deerflow.subagents.executor"] = original_executor
    elif "deerflow.subagents.executor" in sys.modules:
        del sys.modules["deerflow.subagents.executor"]


def _run_task_tool(**kwargs) -> str:
    coroutine = getattr(task_tool_module.task_tool, "coroutine", None)
    if coroutine is not None:
        return asyncio.run(coroutine(**kwargs))
    return task_tool_module.task_tool.func(**kwargs)


async def _no_sleep(_: float) -> None:
    return None


def _drive_task_tool(monkeypatch, result: SimpleNamespace) -> tuple[str, list[dict]]:
    """Run the tool to completion against a single terminal poll result."""
    config = _make_subagent_config()
    events: list[dict] = []

    class DummyExecutor:
        def __init__(self, **kwargs):
            pass

        def execute_async(self, prompt, task_id=None):
            return task_id or "generated-task-id"

    monkeypatch.setattr(task_tool_module, "SubagentStatus", FakeSubagentStatus)
    monkeypatch.setattr(task_tool_module, "SubagentExecutor", DummyExecutor)
    monkeypatch.setattr(task_tool_module, "get_subagent_config", lambda _: config)
    monkeypatch.setattr(task_tool_module, "get_background_task_result", lambda _: result)
    monkeypatch.setattr(task_tool_module, "get_stream_writer", lambda: events.append)
    monkeypatch.setattr(task_tool_module.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr("deerflow.tools.get_available_tools", MagicMock(return_value=[]))

    output = _run_task_tool(
        runtime=_make_runtime(),
        description="运行子任务",
        prompt="collect diagnostics",
        subagent_type="general-purpose",
        tool_call_id="tc-1",
    )
    return output, events


class TestSubagentResultCarriesStopReason:
    """The dataclass needs the additive field before anything can thread it."""

    def test_result_defaults_to_no_stop_reason(self, executor_classes):
        result = executor_classes.SubagentResult(
            task_id="t1",
            trace_id="tr1",
            status=executor_classes.SubagentStatus.COMPLETED,
        )
        assert result.stop_reason is None

    def test_result_accepts_a_stop_reason(self, executor_classes):
        result = executor_classes.SubagentResult(
            task_id="t1",
            trace_id="tr1",
            status=executor_classes.SubagentStatus.COMPLETED,
            stop_reason="turn_capped",
        )
        assert result.stop_reason == "turn_capped"

    def test_cancellation_stamps_a_distinct_reason(self, executor_classes):
        """A user-initiated stop must be machine-distinguishable from a finish."""
        assert executor_classes.CANCELLED_STOP_REASON == "cancelled_by_user"
        assert executor_classes.TIMEOUT_STOP_REASON == "timed_out"
        assert executor_classes.TURN_CAP_STOP_REASON == "turn_capped"


class TestTaskToolSurfacesStopReason:
    """The lead agent reads the tool's return text, so the cap must land there."""

    def test_completed_subagent_reports_no_cap(self, monkeypatch):
        output, events = _drive_task_tool(monkeypatch, _make_result(FakeSubagentStatus.COMPLETED, result="all done"))

        assert output == "Task Succeeded. Result: all done"
        assert "stop_reason" not in output
        completed = [e for e in events if e["type"] == "task_completed"]
        assert completed and completed[0].get("stop_reason") is None

    def test_turn_capped_completion_is_distinguishable_from_a_clean_finish(self, monkeypatch):
        """Partial work survives, but the lead must know it is partial."""
        output, events = _drive_task_tool(
            monkeypatch,
            _make_result(FakeSubagentStatus.COMPLETED, result="investigated 3 of 5 sources", stop_reason="turn_capped"),
        )

        assert "investigated 3 of 5 sources" in output
        assert "turn_capped" in output
        completed = [e for e in events if e["type"] == "task_completed"]
        assert completed and completed[0]["stop_reason"] == "turn_capped"

    def test_cancelled_subagent_reports_cancellation(self, monkeypatch):
        output, events = _drive_task_tool(
            monkeypatch,
            _make_result(FakeSubagentStatus.CANCELLED, error="Cancelled by user", stop_reason="cancelled_by_user"),
        )

        assert "cancelled_by_user" in output
        cancelled = [e for e in events if e["type"] == "task_cancelled"]
        assert cancelled and cancelled[0]["stop_reason"] == "cancelled_by_user"

    def test_timed_out_subagent_reports_a_different_reason_than_cancelled(self, monkeypatch):
        """The two "was killed" paths must not collapse into one another."""
        timed_out, timed_out_events = _drive_task_tool(
            monkeypatch,
            _make_result(FakeSubagentStatus.TIMED_OUT, error="Execution timed out after 10 seconds", stop_reason="timed_out"),
        )
        cancelled, cancelled_events = _drive_task_tool(
            monkeypatch,
            _make_result(FakeSubagentStatus.CANCELLED, error="Cancelled by user", stop_reason="cancelled_by_user"),
        )

        assert "timed_out" in timed_out
        assert timed_out != cancelled
        assert timed_out_events[-1]["stop_reason"] != cancelled_events[-1]["stop_reason"]

    def test_stop_reason_is_omitted_when_the_executor_does_not_supply_one(self, monkeypatch):
        """Back-compat: results predating the field must not crash the tool."""
        result = SimpleNamespace(
            status=FakeSubagentStatus.COMPLETED,
            ai_messages=[],
            token_records=[],
            result="done",
            error=None,
        )
        output, _events = _drive_task_tool(monkeypatch, result)
        assert output == "Task Succeeded. Result: done"


class TestExecutorStampsStopReason:
    """The executor is the only place that knows *why* a run ended."""

    @pytest.mark.asyncio
    async def test_turn_budget_exhaustion_is_not_reported_as_a_crash(self, executor_classes, monkeypatch):
        """``GraphRecursionError`` means "out of turns", not "broken subagent"."""
        from langgraph.errors import GraphRecursionError

        executor = executor_classes.SubagentExecutor(config=_make_subagent_config(), tools=[])

        class _Agent:
            async def astream(self, *_args, **_kwargs):
                raise GraphRecursionError("Recursion limit of 50 reached")
                yield  # pragma: no cover - generator marker

        monkeypatch.setattr(executor, "_create_agent", lambda: _Agent())
        monkeypatch.setattr(executor, "_build_initial_state", _fake_build_state)

        result = await executor._aexecute("do work")

        assert result.stop_reason == executor_classes.TURN_CAP_STOP_REASON
        assert result.status is executor_classes.SubagentStatus.FAILED

    @pytest.mark.asyncio
    async def test_cooperative_cancellation_stamps_cancelled_reason(self, executor_classes, monkeypatch):
        executor = executor_classes.SubagentExecutor(config=_make_subagent_config(), tools=[])
        holder = executor_classes.SubagentResult(
            task_id="t1",
            trace_id="tr1",
            status=executor_classes.SubagentStatus.RUNNING,
        )
        holder.cancel_event.set()

        monkeypatch.setattr(executor, "_create_agent", lambda: SimpleNamespace())
        monkeypatch.setattr(executor, "_build_initial_state", _fake_build_state)

        result = await executor._aexecute("do work", holder)

        assert result.status is executor_classes.SubagentStatus.CANCELLED
        assert result.stop_reason == executor_classes.CANCELLED_STOP_REASON


async def _fake_build_state(_task):
    return {"messages": []}


@pytest.mark.asyncio
async def test_sync_execute_timeout_stamps_stop_reason(executor_classes, monkeypatch):
    """C5: the sync execute() path must stamp TIMEOUT_STOP_REASON when its
    wall-clock budget fires.

    Previously ``FuturesTimeoutError`` from ``_execute_in_isolated_loop`` fell
    into execute()'s generic ``except Exception`` handler, leaving
    ``stop_reason=None`` so the lead couldn't tell a timeout from a crash. The
    dedicated branch now mirrors the async path's TIMED_OUT stamp.
    """
    executor = executor_classes.SubagentExecutor(config=_make_subagent_config(), tools=[])

    def _raise_timeout(_task, _holder=None):
        raise FuturesTimeoutError()

    monkeypatch.setattr(executor, "_execute_in_isolated_loop", _raise_timeout)
    # execute() runs inside this test's event loop → detects it → calls the
    # (mocked) isolated-loop path → FuturesTimeoutError → C5 branch.
    result = executor.execute("do work")

    assert result.status is executor_classes.SubagentStatus.FAILED
    assert result.stop_reason == executor_classes.TIMEOUT_STOP_REASON
    assert result.error is not None and "timed out" in result.error.lower()
