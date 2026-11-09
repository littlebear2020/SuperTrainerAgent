"""P1-2: a subagent that exhausts its turn budget must not lose completed work.

Before this fix ``GraphRecursionError`` fell into the generic ``except
Exception`` handler: the run was marked FAILED with the raw
"Recursion limit ... reached" text and every partial result streamed so far was
discarded. Locally ``max_turns`` defaults lower than upstream, so long research
tasks hit this often.

Delivery honesty is the constraint that shapes the fix: recovered partial work
must be clearly labelled as truncated, never presented as a clean completion.
"""

from __future__ import annotations

import asyncio
import sys
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.errors import GraphRecursionError

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


@pytest.fixture(scope="module")
def executor_mod():
    """Import the real executor module past conftest's circular-import mocks."""
    original = {name: sys.modules.get(name) for name in _MOCKED_MODULE_NAMES}
    original_executor = sys.modules.get("deerflow.subagents.executor")
    sys.modules.pop("deerflow.subagents.executor", None)

    for name in _MOCKED_MODULE_NAMES:
        sys.modules[name] = MagicMock()

    import importlib

    module = importlib.import_module("deerflow.subagents.executor")
    yield module

    for name, mod in original.items():
        if mod is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = mod
    if original_executor is not None:
        sys.modules["deerflow.subagents.executor"] = original_executor
    else:
        sys.modules.pop("deerflow.subagents.executor", None)


def _make_executor(executor_mod, max_turns: int = 3):
    from deerflow.subagents.config import SubagentConfig

    config = SubagentConfig(
        name="researcher",
        description="d",
        system_prompt="p",
        max_turns=max_turns,
    )
    return executor_mod.SubagentExecutor(config=config, tools=[])


class _StreamThenRecurse:
    """Fake agent: streams chunks, then raises GraphRecursionError."""

    def __init__(self, chunks: list[dict]) -> None:
        self._chunks = chunks

    def astream(self, *_a, **_kw):
        chunks = self._chunks

        async def gen():
            for chunk in chunks:
                yield chunk
            raise GraphRecursionError("Recursion limit of 3 reached without hitting a stop condition")

        return gen()


def _run(executor, agent):
    with patch.object(executor, "_create_agent", return_value=agent):
        return asyncio.run(executor._aexecute("research the topic"))


def test_partial_work_is_recovered_not_discarded(executor_mod) -> None:
    executor = _make_executor(executor_mod)
    partial = "Found three sources: A, B, C. Summary of A: ..."
    agent = _StreamThenRecurse(
        [
            {"messages": [HumanMessage(content="research the topic"), AIMessage(content=partial)]},
        ]
    )

    result = _run(executor, agent)

    assert result.result is not None
    assert partial in result.result, "completed work must survive the turn-limit abort"


def test_partial_result_is_flagged_as_truncated(executor_mod) -> None:
    """Delivery honesty: a truncated run must not look like a clean completion."""
    executor = _make_executor(executor_mod)
    agent = _StreamThenRecurse([{"messages": [AIMessage(content="partial findings here")]}])

    result = _run(executor, agent)

    assert result.stop_reason == "turn_capped"
    assert result.truncated is True
    assert "max_turns=3" in (result.error or ""), "the turn-limit cause must stay visible to the lead"


def test_tool_call_only_envelopes_are_skipped(executor_mod) -> None:
    """The last AIMessage may be an empty tool-call envelope — skip to real text."""
    executor = _make_executor(executor_mod)
    real_text = "Analysis so far: the API returns 404 for missing rows."
    agent = _StreamThenRecurse(
        [
            {
                "messages": [
                    AIMessage(content=real_text),
                    AIMessage(content="", tool_calls=[{"name": "bash", "args": {"cmd": "ls"}, "id": "c1"}]),
                    ToolMessage(content="file.txt", tool_call_id="c1"),
                ]
            }
        ]
    )

    result = _run(executor, agent)

    assert real_text in (result.result or "")


def test_no_partial_work_reports_failure(executor_mod) -> None:
    """Nothing usable streamed: this is a genuine failure, not a fake success."""
    executor = _make_executor(executor_mod)
    agent = _StreamThenRecurse([{"messages": [HumanMessage(content="research the topic")]}])

    result = _run(executor, agent)

    assert result.status is executor_mod.SubagentStatus.FAILED
    assert result.result is None
    assert "max_turns=3" in (result.error or "")


def test_list_content_blocks_are_flattened(executor_mod) -> None:
    """Thinking/multimodal models emit block lists; recover their text too."""
    executor = _make_executor(executor_mod)
    agent = _StreamThenRecurse(
        [
            {
                "messages": [
                    AIMessage(content=[{"type": "text", "text": "block one"}, {"type": "text", "text": "block two"}]),
                ]
            }
        ]
    )

    result = _run(executor, agent)

    assert "block one" in (result.result or "")
    assert "block two" in (result.result or "")


def test_clean_completion_is_not_flagged_truncated(executor_mod) -> None:
    """No false positives: a normal run keeps stop_reason/truncated unset."""
    executor = _make_executor(executor_mod)

    class _CleanAgent:
        def astream(self, *_a, **_kw):
            async def gen():
                yield {"messages": [AIMessage(content="the complete answer")]}

            return gen()

    result = _run(executor, _CleanAgent())

    assert result.status is executor_mod.SubagentStatus.COMPLETED
    assert result.result == "the complete answer"
    assert result.truncated is False
    assert result.stop_reason is None
