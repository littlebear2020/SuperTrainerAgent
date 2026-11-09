"""Tests for SubagentLimitMiddleware."""

from unittest.mock import MagicMock

from langchain_core.messages import AIMessage, HumanMessage

from deerflow.agents.middlewares.subagent_limit_middleware import (
    MAX_CONCURRENT_SUBAGENTS,
    MAX_SUBAGENT_LIMIT,
    MAX_TOTAL_SUBAGENT_LIMIT,
    MIN_SUBAGENT_LIMIT,
    MIN_TOTAL_SUBAGENT_LIMIT,
    SubagentLimitMiddleware,
    _clamp_subagent_limit,
)


def _make_runtime():
    runtime = MagicMock()
    runtime.context = {"thread_id": "test-thread"}
    return runtime


def _task_call(task_id="call_1"):
    return {"name": "task", "id": task_id, "args": {"prompt": "do something"}}


def _other_call(name="bash", call_id="call_other"):
    return {"name": name, "id": call_id, "args": {}}


class TestClampSubagentLimit:
    def test_below_min_clamped_to_min(self):
        assert _clamp_subagent_limit(0) == MIN_SUBAGENT_LIMIT
        assert _clamp_subagent_limit(1) == MIN_SUBAGENT_LIMIT

    def test_above_max_clamped_to_max(self):
        assert _clamp_subagent_limit(10) == MAX_SUBAGENT_LIMIT
        assert _clamp_subagent_limit(100) == MAX_SUBAGENT_LIMIT

    def test_within_range_unchanged(self):
        assert _clamp_subagent_limit(2) == 2
        assert _clamp_subagent_limit(3) == 3
        assert _clamp_subagent_limit(4) == 4


class TestSubagentLimitMiddlewareInit:
    def test_default_max_concurrent(self):
        mw = SubagentLimitMiddleware()
        assert mw.max_concurrent == MAX_CONCURRENT_SUBAGENTS

    def test_custom_max_concurrent_clamped(self):
        mw = SubagentLimitMiddleware(max_concurrent=1)
        assert mw.max_concurrent == MIN_SUBAGENT_LIMIT

        mw = SubagentLimitMiddleware(max_concurrent=10)
        assert mw.max_concurrent == MAX_SUBAGENT_LIMIT


class TestTruncateTaskCalls:
    def test_no_messages_returns_none(self):
        mw = SubagentLimitMiddleware()
        assert mw._truncate_task_calls({"messages": []}) is None

    def test_missing_messages_returns_none(self):
        mw = SubagentLimitMiddleware()
        assert mw._truncate_task_calls({}) is None

    def test_last_message_not_ai_returns_none(self):
        mw = SubagentLimitMiddleware()
        state = {"messages": [HumanMessage(content="hello")]}
        assert mw._truncate_task_calls(state) is None

    def test_ai_no_tool_calls_returns_none(self):
        mw = SubagentLimitMiddleware()
        state = {"messages": [AIMessage(content="thinking...")]}
        assert mw._truncate_task_calls(state) is None

    def test_task_calls_within_limit_returns_none(self):
        mw = SubagentLimitMiddleware(max_concurrent=3)
        msg = AIMessage(
            content="",
            tool_calls=[_task_call("t1"), _task_call("t2"), _task_call("t3")],
        )
        assert mw._truncate_task_calls({"messages": [msg]}) is None

    def test_task_calls_exceeding_limit_truncated(self):
        mw = SubagentLimitMiddleware(max_concurrent=2)
        msg = AIMessage(
            content="",
            tool_calls=[_task_call("t1"), _task_call("t2"), _task_call("t3"), _task_call("t4")],
        )
        result = mw._truncate_task_calls({"messages": [msg]})
        assert result is not None
        updated_msg = result["messages"][0]
        task_calls = [tc for tc in updated_msg.tool_calls if tc["name"] == "task"]
        assert len(task_calls) == 2
        assert task_calls[0]["id"] == "t1"
        assert task_calls[1]["id"] == "t2"

    def test_non_task_calls_preserved(self):
        mw = SubagentLimitMiddleware(max_concurrent=2)
        msg = AIMessage(
            content="",
            tool_calls=[
                _other_call("bash", "b1"),
                _task_call("t1"),
                _task_call("t2"),
                _task_call("t3"),
                _other_call("read", "r1"),
            ],
        )
        result = mw._truncate_task_calls({"messages": [msg]})
        assert result is not None
        updated_msg = result["messages"][0]
        names = [tc["name"] for tc in updated_msg.tool_calls]
        assert "bash" in names
        assert "read" in names
        task_calls = [tc for tc in updated_msg.tool_calls if tc["name"] == "task"]
        assert len(task_calls) == 2

    def test_only_non_task_calls_returns_none(self):
        mw = SubagentLimitMiddleware()
        msg = AIMessage(
            content="",
            tool_calls=[_other_call("bash", "b1"), _other_call("read", "r1")],
        )
        assert mw._truncate_task_calls({"messages": [msg]}) is None


class TestAfterModel:
    def test_delegates_to_truncate(self):
        mw = SubagentLimitMiddleware(max_concurrent=2)
        runtime = _make_runtime()
        msg = AIMessage(
            content="",
            tool_calls=[_task_call("t1"), _task_call("t2"), _task_call("t3")],
        )
        result = mw.after_model({"messages": [msg]}, runtime)
        assert result is not None
        task_calls = [tc for tc in result["messages"][0].tool_calls if tc["name"] == "task"]
        assert len(task_calls) == 2


class TestTotalSubagentCap:
    """A per-conversation total cap closes the "N per round × unlimited rounds" hole.

    ``max_concurrent`` only bounds one model response. An agent that plans,
    delegates 3, reviews, delegates 3 more, and repeats stays legal on every
    single response while spending without bound. The total cap counts the
    ``task`` calls already in the message history — the checkpoint is the
    durable record of what was actually dispatched.
    """

    def test_default_max_total_is_set(self):
        mw = SubagentLimitMiddleware()
        assert mw.max_total >= mw.max_concurrent

    def test_max_total_clamped_to_range(self):
        assert SubagentLimitMiddleware(max_total=0).max_total == MIN_TOTAL_SUBAGENT_LIMIT
        assert SubagentLimitMiddleware(max_total=9999).max_total == MAX_TOTAL_SUBAGENT_LIMIT

    def test_prior_rounds_consume_the_total_budget(self):
        """Third round is truncated to the 1 call left of a 5-call budget."""
        mw = SubagentLimitMiddleware(max_concurrent=2, max_total=5)
        history = [
            AIMessage(content="", tool_calls=[_task_call("t1"), _task_call("t2")]),
            AIMessage(content="", tool_calls=[_task_call("t3"), _task_call("t4")]),
            AIMessage(content="", tool_calls=[_task_call("t5"), _task_call("t6")]),
        ]
        result = mw._truncate_task_calls({"messages": history})
        assert result is not None
        task_calls = [tc for tc in result["messages"][0].tool_calls if tc["name"] == "task"]
        assert len(task_calls) == 1
        assert task_calls[0]["id"] == "t5"

    def test_exhausted_budget_drops_all_task_calls(self):
        mw = SubagentLimitMiddleware(max_concurrent=3, max_total=2)
        history = [
            AIMessage(content="", tool_calls=[_task_call("t1"), _task_call("t2")]),
            AIMessage(content="", tool_calls=[_task_call("t3")]),
        ]
        result = mw._truncate_task_calls({"messages": history})
        assert result is not None
        task_calls = [tc for tc in result["messages"][0].tool_calls if tc["name"] == "task"]
        assert task_calls == []

    def test_exhausted_budget_preserves_non_task_calls(self):
        """Capping delegation must not break the agent's own tool use."""
        mw = SubagentLimitMiddleware(max_concurrent=3, max_total=1)
        history = [
            AIMessage(content="", tool_calls=[_task_call("t1")]),
            AIMessage(content="", tool_calls=[_other_call("bash", "b1"), _task_call("t2")]),
        ]
        result = mw._truncate_task_calls({"messages": history})
        assert result is not None
        names = [tc["name"] for tc in result["messages"][0].tool_calls]
        assert names == ["bash"]

    def test_within_total_budget_is_untouched(self):
        mw = SubagentLimitMiddleware(max_concurrent=3, max_total=10)
        history = [
            AIMessage(content="", tool_calls=[_task_call("t1"), _task_call("t2")]),
            AIMessage(content="", tool_calls=[_task_call("t3"), _task_call("t4")]),
        ]
        assert mw._truncate_task_calls({"messages": history}) is None

    def test_concurrent_limit_still_applies_below_total(self):
        """Both caps are live: the tighter one wins on any given round."""
        mw = SubagentLimitMiddleware(max_concurrent=2, max_total=50)
        msg = AIMessage(content="", tool_calls=[_task_call("t1"), _task_call("t2"), _task_call("t3")])
        result = mw._truncate_task_calls({"messages": [msg]})
        assert result is not None
        task_calls = [tc for tc in result["messages"][0].tool_calls if tc["name"] == "task"]
        assert len(task_calls) == 2

    def test_non_task_tool_calls_do_not_consume_the_budget(self):
        mw = SubagentLimitMiddleware(max_concurrent=3, max_total=3)
        history = [
            AIMessage(content="", tool_calls=[_other_call("bash", "b1"), _other_call("read", "r1")]),
            AIMessage(content="", tool_calls=[_task_call("t1"), _task_call("t2"), _task_call("t3")]),
        ]
        assert mw._truncate_task_calls({"messages": history}) is None

    def test_current_message_is_not_double_counted(self):
        """The pending calls must not be counted as prior usage."""
        mw = SubagentLimitMiddleware(max_concurrent=3, max_total=3)
        msg = AIMessage(content="", tool_calls=[_task_call("t1"), _task_call("t2"), _task_call("t3")])
        assert mw._truncate_task_calls({"messages": [msg]}) is None

    def test_after_model_enforces_the_total_cap(self):
        mw = SubagentLimitMiddleware(max_concurrent=3, max_total=2)
        history = [
            AIMessage(content="", tool_calls=[_task_call("t1"), _task_call("t2")]),
            AIMessage(content="", tool_calls=[_task_call("t3")]),
        ]
        result = mw.after_model({"messages": history}, _make_runtime())
        assert result is not None
        assert [tc for tc in result["messages"][0].tool_calls if tc["name"] == "task"] == []


class TestPerRunTotalCap:
    """D3 / R-02 (2026-08-13): the total cap counts the CURRENT run — messages
    since the last REAL user message — not the whole thread. N3: injected
    HumanMessages (loop warnings, image details) never reset the boundary."""

    def test_prior_thread_history_does_not_consume_budget(self):
        mw = SubagentLimitMiddleware(max_concurrent=3, max_total=4)
        history = [
            HumanMessage(content="first user request"),
            AIMessage(content="", tool_calls=[_task_call("t1"), _task_call("t2"), _task_call("t3")]),
            HumanMessage(content="second user request"),
            AIMessage(content="", tool_calls=[_task_call("t4"), _task_call("t5"), _task_call("t6")]),
        ]
        # 3 task calls in the CURRENT run, concurrent=3 → within limits.
        assert mw._truncate_task_calls({"messages": history}) is None

    def test_loop_warning_message_does_not_reset_boundary(self):
        # N3: a loop_warning HumanMessage between rounds must not read as a new
        # user turn (that would reset the per-run count and reopen the budget).
        mw = SubagentLimitMiddleware(max_concurrent=3, max_total=4)
        history = [
            HumanMessage(content="user request"),
            AIMessage(content="", tool_calls=[_task_call("t1"), _task_call("t2")]),
            HumanMessage(content="[LOOP DETECTED] stop looping", name="loop_warning"),
            AIMessage(content="", tool_calls=[_task_call("t3"), _task_call("t4"), _task_call("t5")]),
        ]
        result = mw._truncate_task_calls({"messages": history})
        assert result is not None
        task_calls = [tc for tc in result["messages"][0].tool_calls if tc["name"] == "task"]
        assert len(task_calls) == 2  # 4 - 2 prior = 2 allowed, not a fresh 3

    def test_image_details_message_does_not_reset_boundary(self):
        mw = SubagentLimitMiddleware(max_concurrent=3, max_total=3)
        history = [
            HumanMessage(content="user request"),
            AIMessage(content="", tool_calls=[_task_call("t1"), _task_call("t2"), _task_call("t3")]),
            HumanMessage(
                content=[{"type": "text", "text": "Here are the images you've viewed"}],
                additional_kwargs={"hide_from_ui": True},
            ),
            AIMessage(content="", tool_calls=[_task_call("t4")]),
        ]
        result = mw._truncate_task_calls({"messages": history})
        assert result is not None
        task_calls = [tc for tc in result["messages"][0].tool_calls if tc["name"] == "task"]
        assert len(task_calls) == 0  # budget fully consumed by the same run

    def test_total_exhaustion_appends_stop_notice_and_stamps_stop_reason(self):
        mw = SubagentLimitMiddleware(max_concurrent=3, max_total=2)
        runtime = _make_runtime()
        history = [
            HumanMessage(content="user request"),
            AIMessage(content="", tool_calls=[_task_call("t1"), _task_call("t2")]),
            AIMessage(content="", tool_calls=[_task_call("t3")]),
        ]
        result = mw._truncate_task_calls({"messages": history}, runtime)
        assert result is not None
        msg = result["messages"][0]
        assert "[SUBAGENT LIMIT REACHED]" in msg.content
        assert runtime.context["stop_reason"] == "subagent_limit_capped"

    def test_concurrent_only_truncation_skips_stop_notice(self):
        # When only the concurrent cap bites (budget remains), no stop notice
        # and no stop_reason stamp.
        mw = SubagentLimitMiddleware(max_concurrent=2, max_total=10)
        runtime = _make_runtime()
        msg = AIMessage(content="planning", tool_calls=[_task_call("t1"), _task_call("t2"), _task_call("t3")])
        result = mw._truncate_task_calls({"messages": [msg]}, runtime)
        assert "[SUBAGENT LIMIT REACHED]" not in result["messages"][0].content
        assert "stop_reason" not in runtime.context

    def test_raw_provider_tool_calls_synced_in_additional_kwargs(self):
        """Dropped calls must also be removed from additional_kwargs raw
        tool_calls, or DanglingToolCallMiddleware fabricates placeholders."""
        mw = SubagentLimitMiddleware(max_concurrent=2)
        msg = AIMessage(
            content="",
            tool_calls=[_task_call("t1"), _task_call("t2"), _task_call("t3")],
            additional_kwargs={
                "tool_calls": [
                    {"id": "t1", "type": "function", "function": {"name": "task", "arguments": "{}"}},
                    {"id": "t2", "type": "function", "function": {"name": "task", "arguments": "{}"}},
                    {"id": "t3", "type": "function", "function": {"name": "task", "arguments": "{}"}},
                ]
            },
        )
        result = mw._truncate_task_calls({"messages": [msg]})
        raw = result["messages"][0].additional_kwargs["tool_calls"]
        assert [rc["id"] for rc in raw] == ["t1", "t2"]


def test_subagents_config_max_total_loads_and_defaults():
    """D3: max_total comes from the subagents config section (default 12)."""
    from deerflow.config.subagents_config import SubagentsAppConfig, get_subagents_app_config, load_subagents_config_from_dict

    assert SubagentsAppConfig().max_total == 12
    load_subagents_config_from_dict({"max_total": 20})
    try:
        assert get_subagents_app_config().max_total == 20
    finally:
        load_subagents_config_from_dict({})
    assert get_subagents_app_config().max_total == 12
