from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from typing import get_type_hints
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.constants import TAG_NOSTREAM
from pydantic import ValidationError

from deerflow.agents.memory.summarization_hook import memory_flush_hook
from deerflow.agents.middlewares.summarization_middleware import DEERFLOW_DEFAULT_SUMMARY_PROMPT, DeerFlowSummarizationMiddleware, SummarizationEvent
from deerflow.agents.thread_state import ThreadState
from deerflow.config.memory_config import MemoryConfig
from deerflow.config.summarization_config import SummarizationConfig

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _messages() -> list:
    return [
        HumanMessage(content="user-1"),
        AIMessage(content="assistant-1"),
        HumanMessage(content="user-2"),
        AIMessage(content="assistant-2"),
    ]


def _runtime(thread_id: str | None = "thread-1", agent_name: str | None = None) -> SimpleNamespace:
    context = {}
    if thread_id is not None:
        context["thread_id"] = thread_id
    if agent_name is not None:
        context["agent_name"] = agent_name
    return SimpleNamespace(context=context)


def _middleware(
    *,
    before_summarization=None,
    trigger=("messages", 4),
    keep=("messages", 2),
    skill_file_read_tool_names=None,
    preserve_recent_skill_count: int = 0,
    preserve_recent_skill_tokens: int = 0,
    preserve_recent_skill_tokens_per_skill: int = 0,
    skill_ledger_max_entries: int = 8,
    resummarize_min_new_messages: int = 20,
    resummarize_min_new_tokens: int = 30_000,
    summary_prompt: str | None = None,
) -> DeerFlowSummarizationMiddleware:
    model = MagicMock()
    # The middleware creates _summary_model = model.with_config(tags=...),
    # so we must configure the with_config return value's invoke/ainvoke.
    summary_model = model.with_config.return_value
    summary_model.invoke.return_value = SimpleNamespace(text="compressed summary")
    summary_model.ainvoke = AsyncMock(return_value=SimpleNamespace(text="compressed summary"))
    return DeerFlowSummarizationMiddleware(
        model=model,
        trigger=trigger,
        keep=keep,
        token_counter=len,
        before_summarization=before_summarization,
        skill_file_read_tool_names=skill_file_read_tool_names,
        preserve_recent_skill_count=preserve_recent_skill_count,
        preserve_recent_skill_tokens=preserve_recent_skill_tokens,
        preserve_recent_skill_tokens_per_skill=preserve_recent_skill_tokens_per_skill,
        skill_ledger_max_entries=skill_ledger_max_entries,
        resummarize_min_new_messages=resummarize_min_new_messages,
        resummarize_min_new_tokens=resummarize_min_new_tokens,
        summary_prompt=summary_prompt,
    )


def _model_request(messages: list, runtime: SimpleNamespace | None = None, state: dict | None = None) -> SimpleNamespace:
    """Create a lightweight mock that quacks like ModelRequest."""
    req = SimpleNamespace(messages=messages, runtime=runtime or _runtime(), state=state if state is not None else {"messages": messages})
    captured: list[SimpleNamespace] = []

    def _override(**kwargs):
        new_req = SimpleNamespace(**{**vars(req), **kwargs})
        captured.append(new_req)
        return new_req

    req.override = _override
    req._captured_overrides = captured
    return req


def _recording_handler() -> tuple[MagicMock, list]:
    """Return (handler_mock, received_requests) tracking all calls."""
    received: list = []

    def _handler(request):
        received.append(request)
        return SimpleNamespace(result=[AIMessage(content="ok")])

    mock = MagicMock(side_effect=_handler)
    return mock, received


def _skill_read_call(tool_id: str, skill: str) -> dict:
    return {
        "name": "read_file",
        "id": tool_id,
        "args": {"path": f"/mnt/skills/public/{skill}/SKILL.md"},
    }


def _skill_conversation() -> list:
    return [
        HumanMessage(content="u1"),
        AIMessage(content="", tool_calls=[_skill_read_call("t1", "alpha")]),
        ToolMessage(content="alpha skill body", tool_call_id="t1"),
        HumanMessage(content="u2"),
        AIMessage(content="", tool_calls=[_skill_read_call("t2", "beta")]),
        ToolMessage(content="beta skill body", tool_call_id="t2"),
        HumanMessage(content="u3"),
        AIMessage(content="final"),
    ]


# ---------------------------------------------------------------------------
# before_model / abefore_model are no-ops
# ---------------------------------------------------------------------------


def test_before_model_returns_none() -> None:
    middleware = _middleware()
    result = middleware.before_model({"messages": _messages()}, _runtime())
    assert result is None


@pytest.mark.anyio
async def test_abefore_model_returns_none() -> None:
    middleware = _middleware()
    result = await middleware.abefore_model({"messages": _messages()}, _runtime())
    assert result is None


# ---------------------------------------------------------------------------
# wrap_model_call: core safe summarization
# ---------------------------------------------------------------------------


def test_wrap_model_call_replaces_messages_without_modifying_state() -> None:
    """State messages list must be unchanged; handler receives [summary, preserved]."""
    msgs = _messages()
    original_len = len(msgs)
    middleware = _middleware()
    request = _model_request(msgs)
    handler, received = _recording_handler()

    middleware.wrap_model_call(request, handler)

    # State messages unchanged
    assert len(msgs) == original_len
    # Handler called once with modified request
    assert len(received) == 1
    modified = received[0]
    # First message is the summary, rest are preserved
    assert modified.messages[0].name == "summary"
    assert modified.messages[0].content.startswith("Here is a summary")
    # Preserved messages are the last 2 (keep=("messages", 2))
    assert [m.content for m in modified.messages[1:]] == ["user-2", "assistant-2"]


def test_wrap_model_call_skips_when_below_threshold() -> None:
    middleware = _middleware(trigger=("messages", 10))
    request = _model_request(_messages())
    handler, received = _recording_handler()

    middleware.wrap_model_call(request, handler)

    # Handler called with original request (no override)
    assert len(received) == 1
    assert received[0] is request


def test_wrap_model_call_passes_through_on_summary_failure(caplog: pytest.LogCaptureFixture) -> None:
    """If _create_summary raises, handler receives the original unmodified request."""
    middleware = _middleware()
    # Make the summary model raise
    middleware._summary_model.invoke.side_effect = RuntimeError("LLM unavailable")
    request = _model_request(_messages())
    handler, received = _recording_handler()

    with caplog.at_level("ERROR"):
        middleware.wrap_model_call(request, handler)

    assert "Safe summarization failed" in caplog.text
    # Handler still called, but with original request (no override)
    assert len(received) == 1
    assert received[0] is request


def test_wrap_model_call_passes_through_on_override_failure(caplog: pytest.LogCaptureFixture) -> None:
    """If request.override() raises, handler receives the original request."""
    middleware = _middleware()
    request = _model_request(_messages())
    request.override = MagicMock(side_effect=RuntimeError("override failed"))
    handler, received = _recording_handler()

    with caplog.at_level("ERROR"):
        middleware.wrap_model_call(request, handler)

    assert "Safe summarization failed" in caplog.text
    assert len(received) == 1
    assert received[0] is request


# ---------------------------------------------------------------------------
# wrap_model_call: hooks
# ---------------------------------------------------------------------------


def test_wrap_model_call_fires_before_summarization_hooks() -> None:
    captured: list[SummarizationEvent] = []
    middleware = _middleware(before_summarization=[captured.append])
    request = _model_request(_messages())
    handler, _ = _recording_handler()

    middleware.wrap_model_call(request, handler)

    assert len(captured) == 1
    assert [m.content for m in captured[0].messages_to_summarize] == ["user-1", "assistant-1"]
    assert [m.content for m in captured[0].preserved_messages] == ["user-2", "assistant-2"]
    assert captured[0].thread_id == "thread-1"
    assert captured[0].agent_name is None


def test_wrap_model_call_hooks_not_called_when_below_threshold() -> None:
    captured: list[SummarizationEvent] = []
    middleware = _middleware(before_summarization=[captured.append], trigger=("messages", 10))
    request = _model_request(_messages())
    handler, _ = _recording_handler()

    middleware.wrap_model_call(request, handler)

    assert captured == []


def test_wrap_model_call_hook_exception_does_not_block(caplog: pytest.LogCaptureFixture) -> None:
    def _broken_hook(_: SummarizationEvent) -> None:
        raise RuntimeError("hook failure")

    middleware = _middleware(before_summarization=[_broken_hook])
    request = _model_request(_messages())
    handler, received = _recording_handler()

    with caplog.at_level("ERROR"):
        middleware.wrap_model_call(request, handler)

    assert "before_summarization hook _broken_hook failed" in caplog.text
    # Summarization still succeeded (hook failure is non-blocking)
    assert len(received) == 1
    assert received[0].messages[0].name == "summary"


def test_multiple_hooks_run_in_registration_order() -> None:
    call_order: list[str] = []

    def _hook(name: str):
        return lambda _: call_order.append(name)

    middleware = _middleware(before_summarization=[_hook("first"), _hook("second"), _hook("third")])
    request = _model_request(_messages())
    handler, _ = _recording_handler()

    middleware.wrap_model_call(request, handler)

    assert call_order == ["first", "second", "third"]


# ---------------------------------------------------------------------------
# wrap_model_call: async
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_awrap_model_call_replaces_messages_without_modifying_state() -> None:
    msgs = _messages()
    original_len = len(msgs)
    middleware = _middleware()
    request = _model_request(msgs)
    received: list = []

    async def _handler(request):
        received.append(request)
        return SimpleNamespace(result=[AIMessage(content="ok")])

    await middleware.awrap_model_call(request, _handler)

    assert len(msgs) == original_len
    assert len(received) == 1
    assert received[0].messages[0].name == "summary"


@pytest.mark.anyio
async def test_awrap_model_call_skips_when_below_threshold() -> None:
    middleware = _middleware(trigger=("messages", 10))
    request = _model_request(_messages())
    received: list = []

    async def _handler(request):
        received.append(request)
        return SimpleNamespace(result=[AIMessage(content="ok")])

    await middleware.awrap_model_call(request, _handler)

    assert len(received) == 1
    assert received[0] is request


@pytest.mark.anyio
async def test_awrap_model_call_passes_through_on_failure(caplog: pytest.LogCaptureFixture) -> None:
    middleware = _middleware()
    middleware._summary_model.ainvoke = AsyncMock(side_effect=RuntimeError("async LLM unavailable"))
    request = _model_request(_messages())
    received: list = []

    async def _handler(request):
        received.append(request)
        return SimpleNamespace(result=[AIMessage(content="ok")])

    with caplog.at_level("ERROR"):
        await middleware.awrap_model_call(request, _handler)

    assert "Safe summarization failed" in caplog.text
    assert len(received) == 1
    assert received[0] is request


@pytest.mark.anyio
async def test_awrap_model_call_fires_hooks() -> None:
    captured: list[SummarizationEvent] = []
    middleware = _middleware(before_summarization=[captured.append])
    request = _model_request(_messages())

    async def _handler(request):
        return SimpleNamespace(result=[AIMessage(content="ok")])

    await middleware.awrap_model_call(request, _handler)

    assert len(captured) == 1
    assert [m.content for m in captured[0].messages_to_summarize] == ["user-1", "assistant-1"]


# ---------------------------------------------------------------------------
# wrap_model_call: skill rescue
# ---------------------------------------------------------------------------


def test_wrap_model_call_skill_rescue_keeps_recent_skill_reads() -> None:
    captured: list[SummarizationEvent] = []
    middleware = _middleware(
        before_summarization=[captured.append],
        trigger=("messages", 4),
        keep=("messages", 2),
        preserve_recent_skill_count=5,
        preserve_recent_skill_tokens=10_000,
        preserve_recent_skill_tokens_per_skill=10_000,
    )
    request = _model_request(_skill_conversation())
    handler, received = _recording_handler()

    middleware.wrap_model_call(request, handler)

    assert len(captured) == 1
    preserved = captured[0].preserved_messages
    summarized_ids = {id(m) for m in captured[0].messages_to_summarize}

    assert any(isinstance(m, ToolMessage) and m.content == "alpha skill body" for m in preserved)
    assert any(isinstance(m, ToolMessage) and m.content == "beta skill body" for m in preserved)
    for m in preserved:
        if isinstance(m, ToolMessage) and m.content in {"alpha skill body", "beta skill body"}:
            assert id(m) not in summarized_ids

    # Handler received [summary, rescued, preserved_tail]
    modified = received[0]
    assert modified.messages[0].name == "summary"
    contents = [getattr(m, "content", None) for m in modified.messages[1:]]
    assert contents[-2:] == ["u3", "final"]


def test_wrap_model_call_skill_rescue_respects_count_budget() -> None:
    captured: list[SummarizationEvent] = []
    middleware = _middleware(
        before_summarization=[captured.append],
        trigger=("messages", 4),
        keep=("messages", 2),
        preserve_recent_skill_count=1,
        preserve_recent_skill_tokens=10_000,
        preserve_recent_skill_tokens_per_skill=10_000,
    )
    request = _model_request(_skill_conversation())
    handler, _ = _recording_handler()

    middleware.wrap_model_call(request, handler)

    preserved = captured[0].preserved_messages
    summarized = captured[0].messages_to_summarize
    assert any(isinstance(m, ToolMessage) and m.content == "beta skill body" for m in preserved)
    assert not any(isinstance(m, ToolMessage) and m.content == "alpha skill body" for m in preserved)
    assert any(isinstance(m, ToolMessage) and m.content == "alpha skill body" for m in summarized)


def test_wrap_model_call_skill_rescue_uses_injected_skills_container_path() -> None:
    captured: list[SummarizationEvent] = []
    middleware = _middleware(
        before_summarization=[captured.append],
        preserve_recent_skill_count=5,
        preserve_recent_skill_tokens=10_000,
        preserve_recent_skill_tokens_per_skill=10_000,
    )
    middleware._skills_container_path = "/custom/skills"
    messages = [
        HumanMessage(content="u1"),
        AIMessage(content="", tool_calls=[{"name": "read_file", "id": "t1", "args": {"path": "/custom/skills/demo/SKILL.md"}}]),
        ToolMessage(content="demo skill body", tool_call_id="t1"),
        HumanMessage(content="u2"),
        AIMessage(content="final"),
    ]
    request = _model_request(messages)
    handler, _ = _recording_handler()

    middleware.wrap_model_call(request, handler)

    preserved = captured[0].preserved_messages
    assert any(isinstance(m, ToolMessage) and m.content == "demo skill body" for m in preserved)


def test_wrap_model_call_skill_rescue_uses_configured_skill_read_tool_names() -> None:
    captured: list[SummarizationEvent] = []
    middleware = _middleware(
        before_summarization=[captured.append],
        skill_file_read_tool_names=["custom_read"],
        preserve_recent_skill_count=5,
        preserve_recent_skill_tokens=10_000,
        preserve_recent_skill_tokens_per_skill=10_000,
    )
    middleware._skills_container_path = "/custom/skills"
    messages = [
        HumanMessage(content="u1"),
        AIMessage(content="", tool_calls=[{"name": "custom_read", "id": "t1", "args": {"path": "/custom/skills/demo/SKILL.md"}}]),
        ToolMessage(content="demo skill body", tool_call_id="t1"),
        HumanMessage(content="u2"),
        AIMessage(content="final"),
    ]
    request = _model_request(messages)
    handler, _ = _recording_handler()

    middleware.wrap_model_call(request, handler)

    preserved = captured[0].preserved_messages
    assert any(isinstance(m, ToolMessage) and m.content == "demo skill body" for m in preserved)


def test_wrap_model_call_skill_rescue_respects_per_skill_token_cap() -> None:
    captured: list[SummarizationEvent] = []
    middleware = _middleware(
        before_summarization=[captured.append],
        preserve_recent_skill_count=5,
        preserve_recent_skill_tokens=10_000,
        preserve_recent_skill_tokens_per_skill=0,
    )
    request = _model_request(_skill_conversation())
    handler, _ = _recording_handler()

    middleware.wrap_model_call(request, handler)

    preserved = captured[0].preserved_messages
    assert not any(isinstance(m, ToolMessage) and m.content in {"alpha skill body", "beta skill body"} for m in preserved)


def test_wrap_model_call_skill_rescue_disabled_when_count_zero() -> None:
    captured: list[SummarizationEvent] = []
    middleware = _middleware(
        before_summarization=[captured.append],
        preserve_recent_skill_count=0,
        preserve_recent_skill_tokens=10_000,
        preserve_recent_skill_tokens_per_skill=10_000,
    )
    request = _model_request(_skill_conversation())
    handler, _ = _recording_handler()

    middleware.wrap_model_call(request, handler)

    preserved = captured[0].preserved_messages
    assert not any(isinstance(m, ToolMessage) for m in preserved)


def test_wrap_model_call_skill_rescue_ignores_non_skill_tool_reads() -> None:
    captured: list[SummarizationEvent] = []
    middleware = _middleware(
        before_summarization=[captured.append],
        preserve_recent_skill_count=5,
        preserve_recent_skill_tokens=10_000,
        preserve_recent_skill_tokens_per_skill=10_000,
    )
    messages = [
        HumanMessage(content="u1"),
        AIMessage(
            content="",
            tool_calls=[{"name": "read_file", "id": "t1", "args": {"path": "/mnt/user-data/workspace/notes.md"}}],
        ),
        ToolMessage(content="user notes", tool_call_id="t1"),
        HumanMessage(content="u2"),
        AIMessage(content="done"),
    ]
    request = _model_request(messages)
    handler, _ = _recording_handler()

    middleware.wrap_model_call(request, handler)

    preserved = captured[0].preserved_messages
    assert not any(isinstance(m, ToolMessage) and m.content == "user notes" for m in preserved)


def test_wrap_model_call_skill_rescue_mixed_tool_calls() -> None:
    captured: list[SummarizationEvent] = []
    middleware = _middleware(
        before_summarization=[captured.append],
        preserve_recent_skill_count=5,
        preserve_recent_skill_tokens=10_000,
        preserve_recent_skill_tokens_per_skill=10_000,
    )
    messages = [
        HumanMessage(content="u1"),
        AIMessage(
            content="",
            tool_calls=[
                _skill_read_call("skill-1", "alpha"),
                {"name": "read_file", "id": "file-1", "args": {"path": "/mnt/user-data/workspace/notes.md"}},
            ],
        ),
        ToolMessage(content="alpha skill body", tool_call_id="skill-1"),
        ToolMessage(content="user notes", tool_call_id="file-1"),
        HumanMessage(content="u2"),
        AIMessage(content="done"),
    ]
    request = _model_request(messages)
    handler, _ = _recording_handler()

    middleware.wrap_model_call(request, handler)

    preserved = captured[0].preserved_messages
    summarized = captured[0].messages_to_summarize

    preserved_ai = next(m for m in preserved if isinstance(m, AIMessage) and m.tool_calls)
    summarized_ai = next(m for m in summarized if isinstance(m, AIMessage) and m.tool_calls)

    assert [tc["id"] for tc in preserved_ai.tool_calls] == ["skill-1"]
    assert [tc["id"] for tc in summarized_ai.tool_calls] == ["file-1"]
    assert any(isinstance(m, ToolMessage) and m.content == "alpha skill body" for m in preserved)
    assert not any(isinstance(m, ToolMessage) and m.content == "user notes" for m in preserved)
    assert any(isinstance(m, ToolMessage) and m.content == "user notes" for m in summarized)


def test_wrap_model_call_skill_rescue_clears_content_on_rescued_ai_clone() -> None:
    captured: list[SummarizationEvent] = []
    middleware = _middleware(
        before_summarization=[captured.append],
        preserve_recent_skill_count=5,
        preserve_recent_skill_tokens=10_000,
        preserve_recent_skill_tokens_per_skill=10_000,
    )
    messages = [
        HumanMessage(content="u1"),
        AIMessage(
            content="reading skill and notes",
            tool_calls=[
                _skill_read_call("skill-1", "alpha"),
                {"name": "read_file", "id": "file-1", "args": {"path": "/mnt/user-data/workspace/notes.md"}},
            ],
        ),
        ToolMessage(content="alpha skill body", tool_call_id="skill-1"),
        ToolMessage(content="user notes", tool_call_id="file-1"),
        HumanMessage(content="u2"),
        AIMessage(content="done"),
    ]
    request = _model_request(messages)
    handler, _ = _recording_handler()

    middleware.wrap_model_call(request, handler)

    preserved = captured[0].preserved_messages
    summarized = captured[0].messages_to_summarize

    preserved_ai = next(m for m in preserved if isinstance(m, AIMessage) and m.tool_calls)
    summarized_ai = next(m for m in summarized if isinstance(m, AIMessage) and m.tool_calls)

    assert preserved_ai.content == ""
    assert summarized_ai.content == "reading skill and notes"


def test_wrap_model_call_skill_rescue_only_preserves_matched_tool_results() -> None:
    captured: list[SummarizationEvent] = []
    middleware = _middleware(
        before_summarization=[captured.append],
        preserve_recent_skill_count=5,
        preserve_recent_skill_tokens=10_000,
        preserve_recent_skill_tokens_per_skill=10_000,
    )
    messages = [
        HumanMessage(content="u1"),
        AIMessage(
            content="",
            tool_calls=[
                _skill_read_call("skill-1", "alpha"),
                _skill_read_call("skill-2", "beta"),
            ],
        ),
        ToolMessage(content="alpha skill body", tool_call_id="skill-1"),
        HumanMessage(content="u2"),
        AIMessage(content="done"),
    ]
    request = _model_request(messages)
    handler, _ = _recording_handler()

    middleware.wrap_model_call(request, handler)

    preserved = captured[0].preserved_messages
    summarized = captured[0].messages_to_summarize

    preserved_ai = next(m for m in preserved if isinstance(m, AIMessage) and m.tool_calls)
    summarized_ai = next(m for m in summarized if isinstance(m, AIMessage) and m.tool_calls)

    assert [tc["id"] for tc in preserved_ai.tool_calls] == ["skill-1"]
    assert [tc["id"] for tc in summarized_ai.tool_calls] == ["skill-2"]
    assert any(isinstance(m, ToolMessage) and m.content == "alpha skill body" for m in preserved)
    assert not any(isinstance(m, ToolMessage) and getattr(m, "tool_call_id", None) == "skill-2" for m in preserved)


# ---------------------------------------------------------------------------
# Skill ledger: compact name+path pointers for compressed-away skill reads
# (plan 2026-09-30-skill-mechanism-optimization, Batch A §3 T1-T8)
# ---------------------------------------------------------------------------


def _ledger_of(window: list) -> HumanMessage | None:
    """Return the skill_ledger message from an inference window, if injected."""
    return next((m for m in window if getattr(m, "name", None) == "skill_ledger"), None)


def test_skill_ledger_injected_for_compressed_skill_reads() -> None:
    """T1: over-budget skill reads leave a name+path ledger; rescue set and turn structure unchanged."""
    middleware = _middleware(
        trigger=("messages", 4),
        keep=("messages", 2),
        preserve_recent_skill_count=1,  # only beta rescued; alpha compressed away
        preserve_recent_skill_tokens=10_000,
        preserve_recent_skill_tokens_per_skill=10_000,
    )
    messages = _skill_conversation()
    request = _model_request(messages)
    handler, received = _recording_handler()

    middleware.wrap_model_call(request, handler)

    window = received[0].messages
    # (a) ledger present, right after the summary block, with name + path
    assert window[0].name == "summary"
    ledger = _ledger_of(window)
    assert isinstance(ledger, HumanMessage)
    assert window.index(ledger) == 1
    assert "## Active skills" in ledger.content
    assert "alpha" in ledger.content
    assert "/mnt/skills/public/alpha/SKILL.md" in ledger.content
    # (b) rescue set unchanged: beta body rescued, alpha body compressed away
    assert any(isinstance(m, ToolMessage) and m.content == "beta skill body" for m in window)
    assert not any(isinstance(m, ToolMessage) and m.content == "alpha skill body" for m in window)
    # (c) the ledger carries pointers only — no SKILL.md body text
    assert "alpha skill body" not in ledger.content
    # (d) injection does not disturb the turn structure: last window message
    # is still the original final AIMessage (same object)
    assert window[-1] is messages[-1]
    assert window[-1].content == "final"


def test_skill_ledger_absent_when_all_skills_rescued() -> None:
    """T2: within rescue budgets nothing is compressed away -> no ledger."""
    middleware = _middleware(
        trigger=("messages", 4),
        keep=("messages", 2),
        preserve_recent_skill_count=5,
        preserve_recent_skill_tokens=10_000,
        preserve_recent_skill_tokens_per_skill=10_000,
    )
    request = _model_request(_skill_conversation())
    handler, received = _recording_handler()

    middleware.wrap_model_call(request, handler)

    assert _ledger_of(received[0].messages) is None


def test_skill_ledger_absent_without_skill_reads() -> None:
    """T3: summarization without skill reads injects no ledger."""
    middleware = _middleware(
        trigger=("messages", 4),
        keep=("messages", 2),
        preserve_recent_skill_count=5,
        preserve_recent_skill_tokens=10_000,
        preserve_recent_skill_tokens_per_skill=10_000,
    )
    request = _model_request(_messages())
    handler, received = _recording_handler()

    middleware.wrap_model_call(request, handler)

    assert _ledger_of(received[0].messages) is None


def test_skill_ledger_applies_on_reuse_path() -> None:
    """T4: checkpoint reuse rebuilds the ledger from the re-partitioned dropped span."""
    middleware = _middleware(
        trigger=("messages", 4),
        keep=("messages", 2),
        preserve_recent_skill_count=1,
        preserve_recent_skill_tokens=10_000,
        preserve_recent_skill_tokens_per_skill=10_000,
    )
    runtime = _runtime()
    state = {"messages": _skill_conversation()}
    _run_wrap_and_after(middleware, state, runtime)
    assert middleware._summary_model.invoke.call_count == 1

    state["messages"].append(HumanMessage(content="u4"))
    request = _model_request(state["messages"], runtime=runtime, state=state)
    handler, received = _recording_handler()
    middleware.wrap_model_call(request, handler)

    assert middleware._summary_model.invoke.call_count == 1  # reused
    window = received[0].messages
    assert window[0].name == "summary"
    ledger = _ledger_of(window)
    assert ledger is not None
    assert "/mnt/skills/public/alpha/SKILL.md" in ledger.content
    assert window.index(ledger) == 1
    assert window[-1] is state["messages"][-1]
    assert window[-1].content == "u4"


def test_skill_ledger_dedupes_and_caps_entries() -> None:
    """T5: same skill read twice -> one entry; more skills than the cap -> newest kept."""
    messages: list = [HumanMessage(content="u1")]
    for i in range(10):
        messages.append(AIMessage(content="", tool_calls=[_skill_read_call(f"t-{i}", f"skill-{i}")]))
        messages.append(ToolMessage(content=f"skill-{i} body", tool_call_id=f"t-{i}"))
    # re-read of skill-0: dedup must keep one entry, refreshed to newest
    messages.append(AIMessage(content="", tool_calls=[_skill_read_call("t-dup", "skill-0")]))
    messages.append(ToolMessage(content="skill-0 body again", tool_call_id="t-dup"))
    messages.append(HumanMessage(content="u2"))
    messages.append(AIMessage(content="final"))

    middleware = _middleware(
        trigger=("messages", 4),
        keep=("messages", 2),
        preserve_recent_skill_count=0,  # rescue off: every bundle is compressed away
        preserve_recent_skill_tokens=0,
        preserve_recent_skill_tokens_per_skill=0,
        skill_ledger_max_entries=3,
    )
    request = _model_request(messages)
    handler, received = _recording_handler()

    middleware.wrap_model_call(request, handler)

    ledger = _ledger_of(received[0].messages)
    assert ledger is not None
    lines = [ln for ln in ledger.content.splitlines() if ln.startswith("- ")]
    # 11 reads (10 distinct + 1 re-read) dedup to 10 paths, capped to the 3 newest
    assert len(lines) == 3
    assert lines[0].endswith("/mnt/skills/public/skill-8/SKILL.md")
    assert lines[1].endswith("/mnt/skills/public/skill-9/SKILL.md")
    assert lines[2].endswith("/mnt/skills/public/skill-0/SKILL.md")  # re-read refreshed recency
    assert ledger.content.count("/mnt/skills/public/skill-0/SKILL.md") == 1


def test_skill_ledger_escapes_untrusted_paths() -> None:
    """T6: paths come from model tool-call args — escaped output cannot forge tags."""
    messages = [
        HumanMessage(content="u1"),
        AIMessage(content="", tool_calls=[{"name": "read_file", "id": "t1", "args": {"path": "/mnt/skills/public/<script>alert(1)</script>/SKILL.md"}}]),
        ToolMessage(content="evil skill body", tool_call_id="t1"),
        HumanMessage(content="u2"),
        AIMessage(content="final"),
    ]
    middleware = _middleware(
        trigger=("messages", 4),
        keep=("messages", 2),
        preserve_recent_skill_count=0,
        preserve_recent_skill_tokens=0,
        preserve_recent_skill_tokens_per_skill=0,
    )
    request = _model_request(messages)
    handler, received = _recording_handler()

    middleware.wrap_model_call(request, handler)

    ledger = _ledger_of(received[0].messages)
    assert ledger is not None
    assert "<script>" not in ledger.content
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in ledger.content


def test_skill_ledger_disabled_when_max_entries_zero() -> None:
    """T7: skill_ledger_max_entries=0 disables ledger injection entirely."""
    middleware = _middleware(
        trigger=("messages", 4),
        keep=("messages", 2),
        preserve_recent_skill_count=0,
        preserve_recent_skill_tokens=0,
        preserve_recent_skill_tokens_per_skill=0,
        skill_ledger_max_entries=0,
    )
    request = _model_request(_skill_conversation())
    handler, received = _recording_handler()

    middleware.wrap_model_call(request, handler)

    assert _ledger_of(received[0].messages) is None
    assert received[0].messages[0].name == "summary"  # summarization itself unaffected


def test_skill_ledger_max_entries_injected_by_agent_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    """T8: the lead-agent factory passes skill_ledger_max_entries from config into the middleware."""
    from deerflow.agents.lead_agent import agent as lead_agent_module

    monkeypatch.setattr(lead_agent_module, "get_summarization_config", lambda: SummarizationConfig(enabled=True, skill_ledger_max_entries=3))
    monkeypatch.setattr(lead_agent_module, "get_memory_config", lambda: MemoryConfig(enabled=False))

    fake_model = MagicMock()
    fake_model.with_config.return_value = fake_model
    monkeypatch.setattr(lead_agent_module, "create_chat_model", lambda **kwargs: fake_model)
    monkeypatch.setattr(lead_agent_module, "DeerFlowSummarizationMiddleware", lambda **kwargs: kwargs)

    middleware = lead_agent_module._create_summarization_middleware(app_config=SimpleNamespace(skills=SimpleNamespace(container_path="/custom/skills")))

    assert middleware["skill_ledger_max_entries"] == 3


def test_state_intact_after_multiple_wrap_calls() -> None:
    """Multiple summarization passes must not reduce the original message list."""
    msgs = _messages()
    original_len = len(msgs)
    middleware = _middleware()
    handler, _ = _recording_handler()

    for _ in range(5):
        request = _model_request(msgs)
        middleware.wrap_model_call(request, handler)

    assert len(msgs) == original_len


# ---------------------------------------------------------------------------
# Memory flush hook tests (unchanged — test the hook directly)
# ---------------------------------------------------------------------------


def test_memory_flush_hook_skips_when_memory_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    queue = MagicMock()
    monkeypatch.setattr("deerflow.agents.memory.summarization_hook.get_memory_config", lambda: MemoryConfig(enabled=False))
    monkeypatch.setattr("deerflow.agents.memory.summarization_hook.get_memory_queue", lambda: queue)

    memory_flush_hook(
        SummarizationEvent(
            messages_to_summarize=tuple(_messages()[:2]),
            preserved_messages=(),
            thread_id="thread-1",
            agent_name=None,
            runtime=_runtime(),
        )
    )

    queue.add_nowait.assert_not_called()


def test_memory_flush_hook_skips_when_thread_id_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    queue = MagicMock()
    monkeypatch.setattr("deerflow.agents.memory.summarization_hook.get_memory_config", lambda: MemoryConfig(enabled=True))
    monkeypatch.setattr("deerflow.agents.memory.summarization_hook.get_memory_queue", lambda: queue)

    memory_flush_hook(
        SummarizationEvent(
            messages_to_summarize=tuple(_messages()[:2]),
            preserved_messages=(),
            thread_id=None,
            agent_name=None,
            runtime=_runtime(None),
        )
    )

    queue.add_nowait.assert_not_called()


def test_memory_flush_hook_enqueues_filtered_messages_and_flushes(monkeypatch: pytest.MonkeyPatch) -> None:
    queue = MagicMock()
    messages = [
        HumanMessage(content="Question"),
        AIMessage(content="Calling tool", tool_calls=[{"name": "search", "id": "tool-1", "args": {}}]),
        AIMessage(content="Final answer"),
    ]
    monkeypatch.setattr("deerflow.agents.memory.summarization_hook.get_memory_config", lambda: MemoryConfig(enabled=True))
    monkeypatch.setattr("deerflow.agents.memory.summarization_hook.get_memory_queue", lambda: queue)

    memory_flush_hook(
        SummarizationEvent(
            messages_to_summarize=tuple(messages),
            preserved_messages=(),
            thread_id="thread-1",
            agent_name=None,
            runtime=_runtime(),
        )
    )

    queue.add_nowait.assert_called_once()
    add_kwargs = queue.add_nowait.call_args.kwargs
    assert add_kwargs["thread_id"] == "thread-1"
    assert [message.content for message in add_kwargs["messages"]] == ["Question", "Final answer"]
    assert add_kwargs["correction_detected"] is False
    assert add_kwargs["reinforcement_detected"] is False


def test_memory_flush_hook_preserves_agent_scoped_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    queue = MagicMock()
    monkeypatch.setattr("deerflow.agents.memory.summarization_hook.get_memory_config", lambda: MemoryConfig(enabled=True))
    monkeypatch.setattr("deerflow.agents.memory.summarization_hook.get_memory_queue", lambda: queue)

    memory_flush_hook(
        SummarizationEvent(
            messages_to_summarize=tuple(_messages()[:2]),
            preserved_messages=(),
            thread_id="thread-1",
            agent_name="research-agent",
            runtime=_runtime(agent_name="research-agent"),
        )
    )

    queue.add_nowait.assert_called_once()
    assert queue.add_nowait.call_args.kwargs["agent_name"] == "research-agent"


# ---------------------------------------------------------------------------
# Regression tests: TAG_NOSTREAM on summary model
# ---------------------------------------------------------------------------


class _StaticChatModel(BaseChatModel):
    """Deterministic chat model for unit tests."""

    text: str = "ok"

    @property
    def _llm_type(self) -> str:
        return "static-test-chat-model"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=self.text))])

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        return self._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


def test_summary_model_is_tagged_nostream() -> None:
    """The dedicated _summary_model must carry TAG_NOSTREAM."""
    model = _StaticChatModel(text="compressed summary")
    middleware = DeerFlowSummarizationMiddleware(
        model=model,
        trigger=("messages", 4),
        keep=("messages", 2),
        token_counter=len,
    )

    assert TAG_NOSTREAM in (middleware._summary_model.config.get("tags") or [])
    assert TAG_NOSTREAM not in (getattr(middleware.model, "config", {}).get("tags") or [])


def test_summary_model_preserves_existing_tags_when_adding_nostream() -> None:
    """Adding TAG_NOSTREAM must not clobber existing tags like middleware:summarize."""
    tagged_model = _StaticChatModel(text="compressed summary").with_config(tags=["middleware:summarize"])
    middleware = DeerFlowSummarizationMiddleware(
        model=tagged_model,
        trigger=("messages", 4),
        keep=("messages", 2),
        token_counter=len,
    )

    summary_tags = middleware._summary_model.config.get("tags") or []
    assert "middleware:summarize" in summary_tags
    assert TAG_NOSTREAM in summary_tags
    assert summary_tags.count(TAG_NOSTREAM) == 1


def test_raw_model_is_preserved_for_parent_inspection() -> None:
    """self.model must stay the original model so attribute access does not drift."""
    model = _StaticChatModel(text="compressed summary")
    middleware = DeerFlowSummarizationMiddleware(
        model=model,
        trigger=("messages", 4),
        keep=("messages", 2),
        token_counter=len,
    )

    request = _model_request(_messages())
    handler, _ = _recording_handler()
    middleware.wrap_model_call(request, handler)

    assert middleware.model is model
    assert middleware._summary_model is not model


def test_summary_uses_nostream_model_not_shared_model() -> None:
    """Summary LLM call must go through _summary_model, not self.model."""
    invoke_targets: list[str] = []

    class _RecordingModel(_StaticChatModel):
        def invoke(self, input, config=None, **kwargs):
            invoke_targets.append("shared")
            return super().invoke(input, config=config, **kwargs)

    class _RecordingBinding:
        def __init__(self):
            self._inner = _StaticChatModel(text="compressed summary")

        def invoke(self, input, config=None, **kwargs):
            invoke_targets.append("summary_model")
            return self._inner.invoke(input, config=config, **kwargs)

        @property
        def config(self):
            return {"tags": [TAG_NOSTREAM]}

    model = _RecordingModel(text="compressed summary")
    middleware = DeerFlowSummarizationMiddleware(
        model=model,
        trigger=("messages", 4),
        keep=("messages", 2),
        token_counter=len,
    )
    middleware._summary_model = _RecordingBinding()

    request = _model_request(_messages())
    handler, _ = _recording_handler()
    middleware.wrap_model_call(request, handler)

    assert "summary_model" in invoke_targets
    assert "shared" not in invoke_targets


@pytest.mark.anyio
async def test_async_summary_uses_nostream_model() -> None:
    """Async summary LLM call must go through _summary_model."""
    invoke_targets: list[str] = []

    class _RecordingModel(_StaticChatModel):
        async def ainvoke(self, input, config=None, **kwargs):
            invoke_targets.append("shared_async")
            return SimpleNamespace(text="compressed summary")

    class _RecordingBinding:
        def __init__(self):
            self._inner = _StaticChatModel(text="compressed summary")

        async def ainvoke(self, input, config=None, **kwargs):
            invoke_targets.append("summary_model_async")
            return self._inner.ainvoke(input, config=config, **kwargs)

        @property
        def config(self):
            return {"tags": [TAG_NOSTREAM]}

    model = _RecordingModel(text="compressed summary")
    middleware = DeerFlowSummarizationMiddleware(
        model=model,
        trigger=("messages", 4),
        keep=("messages", 2),
        token_counter=len,
    )
    middleware._summary_model = _RecordingBinding()

    request = _model_request(_messages())

    async def _handler(request):
        return SimpleNamespace(result=[AIMessage(content="ok")])

    await middleware.awrap_model_call(request, _handler)

    assert "summary_model_async" in invoke_targets
    assert "shared_async" not in invoke_targets


# ---------------------------------------------------------------------------
# D-3 #3: dynamic-context reminders survive summarization
# ---------------------------------------------------------------------------

from langchain_core.messages import SystemMessage  # noqa: E402


def _reminder_triplet(base_id: str = "m1", user_text: str = "first question") -> list:
    from deerflow.agents.middlewares.dynamic_context_middleware import _DYNAMIC_CONTEXT_REMINDER_KEY, _REMINDER_DATE_KEY

    kw = {"hide_from_ui": True, _DYNAMIC_CONTEXT_REMINDER_KEY: True, _REMINDER_DATE_KEY: "2026-08-20, Thursday"}
    return [
        SystemMessage(content="<system-reminder>\n<current_date>2026-08-20, Thursday</current_date>\n</system-reminder>", id=base_id, additional_kwargs=kw),
        HumanMessage(content="<memory>\n偏好A\n</memory>", id=f"{base_id}__memory", additional_kwargs={"hide_from_ui": True, _DYNAMIC_CONTEXT_REMINDER_KEY: True}),
        HumanMessage(content=user_text, id=f"{base_id}__user"),
    ]


class TestPreserveDynamicContextReminders:
    def test_reminder_triplet_is_rescued_from_summary_compression(self):
        mw = _middleware()
        messages = [
            *_reminder_triplet(),
            AIMessage(content="answer-1"),
            HumanMessage(content="later question"),
            AIMessage(content="final"),
        ]

        to_summarize, preserved = mw._partition_with_skill_rescue(messages, cutoff_index=4)

        preserved_ids = {m.id for m in preserved}
        assert {"m1", "m1__memory", "m1__user"} <= preserved_ids  # whole triplet rescued
        summarize_ids = {m.id for m in to_summarize}
        assert "m1" not in summarize_ids and "m1__user" not in summarize_ids
        # The untagged __user peer is rescued too — its question must not be
        # compressed into prose while the tagged reminder survives.
        assert [str(m.content) for m in to_summarize] == ["answer-1"]

    def test_summary_window_still_contains_date_and_memory(self):
        """End-to-end through wrap_model_call: the modified request keeps the
        date + memory content visible to the model after summarization."""
        mw = _middleware(trigger=("messages", 6), keep=("messages", 2))
        messages = [
            *_reminder_triplet(),
            AIMessage(content="answer-1"),
            HumanMessage(content="q2"),
            AIMessage(content="answer-2"),
            HumanMessage(content="q3"),
            AIMessage(content="final"),
        ]
        handler, received = _recording_handler()
        request = _model_request(messages)
        mw.wrap_model_call(request, handler)

        assert handler.called
        final_messages = received[0].messages
        joined = "".join(str(getattr(m, "content", "")) for m in final_messages)
        assert "<current_date>2026-08-20, Thursday</current_date>" in joined
        assert "偏好A" in joined

    def test_no_reminders_leaves_partition_untouched(self):
        mw = _middleware()
        messages = [
            HumanMessage(content="u1"),
            AIMessage(content="a1"),
            HumanMessage(content="u2"),
            AIMessage(content="a2"),
        ]
        to_summarize, preserved = mw._partition_with_skill_rescue(messages, cutoff_index=2)
        assert [m.content for m in to_summarize] == ["u1", "a1"]
        assert [m.content for m in preserved] == ["u2", "a2"]


# ---------------------------------------------------------------------------
# Reuse guard: summarization_checkpoint persistence + summary reuse
# ---------------------------------------------------------------------------


def _run_wrap_and_after(middleware: DeerFlowSummarizationMiddleware, state: dict, runtime: SimpleNamespace | None = None) -> list:
    """Drive one wrap_model_call + after_model cycle, applying the state update.

    Mirrors the real graph: wrap_model_call runs inside the model node, then
    after_model persists any fresh summary checkpoint back into state.
    Returns the list of requests the handler received.
    """
    runtime = runtime or _runtime()
    request = _model_request(state["messages"], runtime=runtime, state=state)
    handler, received = _recording_handler()
    middleware.wrap_model_call(request, handler)
    update = middleware.after_model(state, runtime)
    if update:
        state.update(update)
    return received


def test_reuse_guard_skips_summary_model_below_growth_thresholds() -> None:
    """After a first summarization, small growth must reuse the stored summary."""
    middleware = _middleware()  # trigger=("messages", 4), knobs 20 messages / 30000 tokens
    runtime = _runtime()
    state = {"messages": _messages()}

    _run_wrap_and_after(middleware, state, runtime)
    assert middleware._summary_model.invoke.call_count == 1
    assert state["summarization_checkpoint"]["summary"] == "compressed summary"
    assert state["summarization_checkpoint"]["message_count"] == 4

    # One new message: trigger still fires (5 >= 4) but growth is below both thresholds.
    state["messages"].append(HumanMessage(content="user-3"))
    request = _model_request(state["messages"], runtime=runtime, state=state)
    handler, received = _recording_handler()
    middleware.wrap_model_call(request, handler)

    # No second summary-model call, yet the window still carries the summary.
    assert middleware._summary_model.invoke.call_count == 1
    window = received[0].messages
    assert window[0].name == "summary"
    assert "compressed summary" in window[0].content
    # Frozen cutoff C0=2 (summary covers messages[:2]): the window keeps
    # EVERYTHING since C0. "user-2" sits in the former blind zone [C0:fresh
    # cutoff=3) and must remain visible during reuse.
    assert [m.content for m in window[1:]] == ["user-2", "assistant-2", "user-3"]
    # Reuse path produces no fresh checkpoint.
    assert middleware.after_model(state, runtime) is None


def test_growth_past_message_threshold_triggers_fresh_summary() -> None:
    middleware = _middleware()
    runtime = _runtime()
    state = {"messages": _messages()}
    _run_wrap_and_after(middleware, state, runtime)
    assert middleware._summary_model.invoke.call_count == 1

    for i in range(20):
        state["messages"].append(HumanMessage(content=f"extra-{i}"))
    received = _run_wrap_and_after(middleware, state, runtime)

    assert middleware._summary_model.invoke.call_count == 2
    assert state["summarization_checkpoint"]["message_count"] == 24
    assert received[0].messages[0].name == "summary"


def test_growth_past_token_threshold_triggers_fresh_summary() -> None:
    """Token growth alone (message growth below its threshold) must re-summarize."""
    middleware = _middleware(resummarize_min_new_messages=1000, resummarize_min_new_tokens=3)
    runtime = _runtime()
    state = {"messages": _messages()}  # token_counter=len -> 4 tokens at checkpoint time
    _run_wrap_and_after(middleware, state, runtime)
    assert middleware._summary_model.invoke.call_count == 1

    # +3 tokens via 3 messages: under the message threshold, not under the token threshold.
    state["messages"].extend([HumanMessage(content="x"), AIMessage(content="y"), HumanMessage(content="z")])
    _run_wrap_and_after(middleware, state, runtime)

    assert middleware._summary_model.invoke.call_count == 2
    assert state["summarization_checkpoint"]["message_count"] == 7
    assert state["summarization_checkpoint"]["approx_tokens"] == 7


def test_zero_knobs_restore_legacy_always_resummarize() -> None:
    """Both knobs at 0 = legacy behavior: re-summarize on every triggered call."""
    middleware = _middleware(resummarize_min_new_messages=0, resummarize_min_new_tokens=0)
    runtime = _runtime()
    state = {"messages": _messages()}
    _run_wrap_and_after(middleware, state, runtime)
    assert state["summarization_checkpoint"]["message_count"] == 4

    state["messages"].append(HumanMessage(content="user-3"))
    received = _run_wrap_and_after(middleware, state, runtime)

    assert middleware._summary_model.invoke.call_count == 2
    assert received[0].messages[0].name == "summary"
    # Fresh summary re-persists a new checkpoint.
    assert state["summarization_checkpoint"]["message_count"] == 5


def test_missing_checkpoint_field_behaves_as_never_summarized() -> None:
    """Legacy checkpoints (no summarization_checkpoint key) take the fresh path."""
    middleware = _middleware()
    state = {"messages": _messages()}
    assert "summarization_checkpoint" not in state

    request = _model_request(state["messages"], state=state)
    handler, received = _recording_handler()
    middleware.wrap_model_call(request, handler)

    assert middleware._summary_model.invoke.call_count == 1
    assert received[0].messages[0].name == "summary"


def test_after_model_returns_none_without_fresh_summary() -> None:
    middleware = _middleware()
    assert middleware.after_model({"messages": _messages()}, _runtime()) is None


def _legacy_checkpoint(state: dict) -> None:
    """Install a checkpoint shaped like the pre-cutoff_index (2026-09-22) build wrote."""
    state["summarization_checkpoint"] = {
        "summary": "legacy stored summary",
        "message_count": 4,
        "approx_tokens": 4,
        "created_at": "2026-09-22T00:00:00+00:00",
    }


def test_checkpoint_without_cutoff_index_falls_back_to_fresh() -> None:
    """Backward compat: old checkpoints (no cutoff_index) take the fresh path."""
    middleware = _middleware()
    runtime = _runtime()
    state = {"messages": _messages()}
    _legacy_checkpoint(state)

    received = _run_wrap_and_after(middleware, state, runtime)

    assert middleware._summary_model.invoke.call_count == 1  # fresh, not reused
    assert received[0].messages[0].name == "summary"
    # The fresh pass upgrades the checkpoint to the new shape.
    assert state["summarization_checkpoint"]["cutoff_index"] == 2


@pytest.mark.parametrize("bad_cutoff", ["2", True, 0, -1, 100])
def test_invalid_cutoff_falls_back_to_fresh(bad_cutoff) -> None:
    """Non-int / bool / non-positive / beyond-history (rollback) cutoffs fall through to fresh."""
    middleware = _middleware()
    runtime = _runtime()
    state = {"messages": _messages()}
    _legacy_checkpoint(state)
    state["summarization_checkpoint"]["cutoff_index"] = bad_cutoff

    received = _run_wrap_and_after(middleware, state, runtime)

    assert middleware._summary_model.invoke.call_count == 1
    assert received[0].messages[0].name == "summary"


def test_failed_call_after_stash_persists_nothing() -> None:
    """A failure after stashing must pop the stash so after_model stays a no-op."""
    middleware = _middleware()
    runtime = _runtime()
    state = {"messages": _messages()}
    request = _model_request(state["messages"], runtime=runtime, state=state)
    request.override = MagicMock(side_effect=RuntimeError("override failed"))
    handler, received = _recording_handler()

    middleware.wrap_model_call(request, handler)

    assert len(received) == 1
    assert received[0] is request  # pass-through
    assert middleware.after_model(state, runtime) is None  # stash was popped


@pytest.mark.anyio
async def test_async_failed_call_after_stash_persists_nothing() -> None:
    middleware = _middleware()
    runtime = _runtime()
    state = {"messages": _messages()}
    request = _model_request(state["messages"], runtime=runtime, state=state)
    request.override = MagicMock(side_effect=RuntimeError("override failed"))
    received: list = []

    async def _handler(request):
        received.append(request)
        return SimpleNamespace(result=[AIMessage(content="ok")])

    await middleware.awrap_model_call(request, _handler)

    assert len(received) == 1
    assert received[0] is request
    assert await middleware.aafter_model(state, runtime) is None


def test_checkpoint_round_trip_through_state() -> None:
    """Checkpoint shape must round-trip: summary/message_count/approx_tokens/created_at/cutoff_index."""
    middleware = _middleware()
    runtime = _runtime()
    state = {"messages": _messages()}
    _run_wrap_and_after(middleware, state, runtime)

    checkpoint = state["summarization_checkpoint"]
    assert set(checkpoint) == {"summary", "message_count", "approx_tokens", "created_at", "cutoff_index"}
    assert checkpoint["summary"] == "compressed summary"
    assert checkpoint["message_count"] == 4
    assert checkpoint["approx_tokens"] == 4  # token_counter=len
    assert checkpoint["cutoff_index"] == 2  # keep=("messages", 2) over 4 messages
    datetime.fromisoformat(checkpoint["created_at"])  # must be an ISO timestamp

    # Same state with zero growth reuses immediately.
    request = _model_request(state["messages"], runtime=runtime, state=state)
    handler, _ = _recording_handler()
    middleware.wrap_model_call(request, handler)
    assert middleware._summary_model.invoke.call_count == 1


def test_skill_rescue_applies_on_reuse_path() -> None:
    """Reuse must still run skill rescue over the CURRENT state."""
    middleware = _middleware(
        preserve_recent_skill_count=5,
        preserve_recent_skill_tokens=10_000,
        preserve_recent_skill_tokens_per_skill=10_000,
    )
    runtime = _runtime()
    state = {"messages": _skill_conversation()}  # 8 messages
    _run_wrap_and_after(middleware, state, runtime)
    assert middleware._summary_model.invoke.call_count == 1

    state["messages"].append(HumanMessage(content="u4"))
    request = _model_request(state["messages"], runtime=runtime, state=state)
    handler, received = _recording_handler()
    middleware.wrap_model_call(request, handler)

    assert middleware._summary_model.invoke.call_count == 1  # reused
    window = received[0].messages
    assert window[0].name == "summary"
    assert "compressed summary" in window[0].content
    assert any(isinstance(m, ToolMessage) and m.content == "alpha skill body" for m in window)
    assert any(isinstance(m, ToolMessage) and m.content == "beta skill body" for m in window)


@pytest.mark.anyio
async def test_async_reuse_guard_matches_sync_behavior() -> None:
    middleware = _middleware()
    runtime = _runtime()
    state = {"messages": _messages()}
    received: list = []

    async def _handler(request):
        received.append(request)
        return SimpleNamespace(result=[AIMessage(content="ok")])

    await middleware.awrap_model_call(_model_request(state["messages"], runtime=runtime, state=state), _handler)
    update = await middleware.aafter_model(state, runtime)
    assert update is not None
    state.update(update)
    assert middleware._summary_model.ainvoke.call_count == 1
    assert state["summarization_checkpoint"]["message_count"] == 4

    state["messages"].append(HumanMessage(content="user-3"))
    await middleware.awrap_model_call(_model_request(state["messages"], runtime=runtime, state=state), _handler)

    assert middleware._summary_model.ainvoke.call_count == 1  # reused, no second call
    assert received[-1].messages[0].name == "summary"
    assert await middleware.aafter_model(state, runtime) is None


@pytest.mark.anyio
async def test_async_growth_past_threshold_resummarizes() -> None:
    middleware = _middleware()
    runtime = _runtime()
    state = {"messages": _messages()}

    async def _handler(request):
        return SimpleNamespace(result=[AIMessage(content="ok")])

    await middleware.awrap_model_call(_model_request(state["messages"], runtime=runtime, state=state), _handler)
    state.update(await middleware.aafter_model(state, runtime))

    for i in range(20):
        state["messages"].append(HumanMessage(content=f"extra-{i}"))
    await middleware.awrap_model_call(_model_request(state["messages"], runtime=runtime, state=state), _handler)

    assert middleware._summary_model.ainvoke.call_count == 2


def test_thread_state_carries_summarization_checkpoint_field() -> None:
    """ThreadState must expose summarization_checkpoint as a plain (reducer-free) field."""
    hints = get_type_hints(ThreadState, include_extras=True)
    assert "summarization_checkpoint" in hints


# ---------------------------------------------------------------------------
# DeerFlow default summary prompt
# ---------------------------------------------------------------------------


class TestDeerFlowDefaultSummaryPrompt:
    def test_keeps_base_sections_and_adds_working_state(self) -> None:
        for header in ("## SESSION INTENT", "## SUMMARY", "## ARTIFACTS", "## NEXT STEPS"):
            assert header in DEERFLOW_DEFAULT_SUMMARY_PROMPT
        assert "## 工作状态与产物清单 (WORKING STATE)" in DEERFLOW_DEFAULT_SUMMARY_PROMPT

    def test_working_state_sits_between_artifacts_and_next_steps(self) -> None:
        artifacts = DEERFLOW_DEFAULT_SUMMARY_PROMPT.index("## ARTIFACTS")
        working_state = DEERFLOW_DEFAULT_SUMMARY_PROMPT.index("## 工作状态与产物清单")
        next_steps = DEERFLOW_DEFAULT_SUMMARY_PROMPT.index("## NEXT STEPS")
        assert artifacts < working_state < next_steps

    def test_working_state_demands_paths_and_no_redo(self) -> None:
        assert "/mnt/user-data/workspace" in DEERFLOW_DEFAULT_SUMMARY_PROMPT
        assert "MUST NOT be redone" in DEERFLOW_DEFAULT_SUMMARY_PROMPT

    def test_formats_without_keyerror(self) -> None:
        rendered = DEERFLOW_DEFAULT_SUMMARY_PROMPT.format(messages="hello world")
        assert "hello world" in rendered

    def test_middleware_uses_deerflow_default_when_no_prompt_given(self) -> None:
        middleware = _middleware()
        assert middleware.summary_prompt is DEERFLOW_DEFAULT_SUMMARY_PROMPT

    def test_custom_prompt_still_overrides(self) -> None:
        middleware = _middleware(summary_prompt="custom {messages}")
        assert middleware.summary_prompt == "custom {messages}"


# ---------------------------------------------------------------------------
# Config knobs
# ---------------------------------------------------------------------------


class TestSummarizationConfigKnobs:
    def test_trim_default_is_30000(self) -> None:
        assert SummarizationConfig().trim_tokens_to_summarize == 30000

    def test_middleware_trim_default_is_30000(self) -> None:
        middleware = DeerFlowSummarizationMiddleware(
            model=MagicMock(),
            trigger=("messages", 4),
            keep=("messages", 2),
            token_counter=len,
        )
        assert middleware.trim_tokens_to_summarize == 30000

    def test_resummarize_knob_defaults(self) -> None:
        config = SummarizationConfig()
        assert config.resummarize_min_new_messages == 20
        assert config.resummarize_min_new_tokens == 30000

    def test_resummarize_knobs_reject_negative(self) -> None:
        with pytest.raises(ValidationError):
            SummarizationConfig(resummarize_min_new_messages=-1)
        with pytest.raises(ValidationError):
            SummarizationConfig(resummarize_min_new_tokens=-1)
