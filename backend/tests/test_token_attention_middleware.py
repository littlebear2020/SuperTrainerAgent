"""Tests for TokenAttentionMiddleware (T20).

The load-bearing test here is ``TestZeroInterference``: it proves the
middleware records a threshold crossing **without touching what the model
sees**. That is the whole reason we did not port upstream's
``token_budget_middleware``, whose warning path injects

    "[TOKEN BUDGET WARNING] ... Wrap up your current work ... Avoid starting
     new tool calls unless absolutely necessary."

as a HumanMessage, and whose hard stop strips ``tool_calls`` off the AIMessage.
Either would silently change agent behavior — a video run told to wrap up
produces fewer shots and still reports success.

So the assertions are deliberately about *identity and mutation*, not just
about return values: same list object, same element objects, same tool_calls,
same content, and no ``wrap_model_call``-family hook that could rewrite a
request on the way to the provider.
"""

from __future__ import annotations

import copy
import logging
from pathlib import Path

import pytest
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from deerflow.agents.middlewares.token_attention_middleware import TokenAttentionMiddleware
from deerflow.config.token_usage_config import DEFAULT_ATTENTION_THRESHOLD_TOKENS, TokenUsageConfig


class _Runtime:
    """Minimal Runtime stand-in: the middleware only reads ``.context``."""

    def __init__(self, context: dict | None = None):
        self.context = context if context is not None else {}


def _ai(total: int, *, msg_id: str = "ai-1", tool_calls=None, model: str = "test-model") -> AIMessage:
    return AIMessage(
        content="hello",
        id=msg_id,
        tool_calls=tool_calls or [],
        usage_metadata={"input_tokens": total // 2, "output_tokens": total - total // 2, "total_tokens": total},
        response_metadata={"model_name": model},
    )


def _mw(threshold: int = 1000) -> TokenAttentionMiddleware:
    return TokenAttentionMiddleware(TokenUsageConfig(enabled=True, attention_threshold_tokens=threshold))


# ---------------------------------------------------------------------------
# The point of the whole task: zero model-side interference
# ---------------------------------------------------------------------------


class TestZeroInterference:
    @pytest.mark.anyio
    async def test_message_list_not_modified_when_threshold_crossed(self, caplog):
        """Over-threshold: the crossing is logged, the messages are untouched.

        Compares against a deep copy taken before the hooks run, and also
        asserts object identity so an in-place mutation (which a deep-equality
        check alone would catch, but an aliased rebuild would not) is caught.
        """
        tool_call = {"name": "generate_video", "args": {"n": 3}, "id": "call-1", "type": "tool_call"}
        messages = [
            HumanMessage(content="make a video", id="h-1"),
            AIMessage(content="working", id="ai-0", tool_calls=[tool_call], usage_metadata={"input_tokens": 5_000, "output_tokens": 5_000, "total_tokens": 10_000}),
            ToolMessage(content="ok", tool_call_id="call-1", id="tm-1"),
            _ai(10_000, msg_id="ai-1", tool_calls=[dict(tool_call, id="call-2")]),
        ]
        original_ids = [id(m) for m in messages]
        original_snapshot = copy.deepcopy(messages)
        state = {"messages": messages}

        mw = _mw(threshold=1_000)
        with caplog.at_level(logging.WARNING):
            sync_result = mw.after_model(state, _Runtime({"run_id": "run-1"}))
            async_result = await mw.aafter_model(state, _Runtime({"run_id": "run-2"}))

        # It really did notice — otherwise this test would pass trivially.
        assert "token attention threshold crossed" in caplog.text

        # No state update at all: nothing for LangGraph to merge into messages.
        assert sync_result is None
        assert async_result is None

        # Same list, same element objects, same order.
        assert state["messages"] is messages
        assert [id(m) for m in state["messages"]] == original_ids

        # Nothing rewritten in place.
        for before, after in zip(original_snapshot, messages, strict=True):
            assert type(before) is type(after)
            assert before.content == after.content
            assert before.id == after.id
            assert getattr(before, "tool_calls", None) == getattr(after, "tool_calls", None)
            assert getattr(before, "additional_kwargs", None) == getattr(after, "additional_kwargs", None)
            assert getattr(before, "response_metadata", None) == getattr(after, "response_metadata", None)

        # tool_calls specifically survive — upstream's hard stop strips these.
        assert messages[-1].tool_calls == [dict(tool_call, id="call-2")]
        assert messages[1].tool_calls == [tool_call]

    @pytest.mark.anyio
    async def test_no_budget_warning_text_reaches_the_messages(self):
        """No HumanMessage is appended, on this or any later call.

        Upstream defers injection: ``after_model`` queues the warning and
        ``wrap_model_call`` injects it at the *next* model call. Crossing the
        threshold and then driving several more hook cycles is what would
        surface that deferred write.
        """
        messages = [_ai(50_000, msg_id="ai-1")]
        state = {"messages": messages}
        mw = _mw(threshold=1_000)
        runtime = _Runtime({"run_id": "run-1"})

        for _ in range(3):
            assert mw.after_model(state, runtime) is None
            assert await mw.aafter_model(state, runtime) is None

        assert len(state["messages"]) == 1
        assert not any(isinstance(m, HumanMessage) for m in state["messages"])
        blob = " ".join(str(getattr(m, "content", "")) for m in state["messages"])
        assert "TOKEN BUDGET" not in blob
        assert "Wrap up" not in blob

    def test_declares_no_model_call_wrapping_hooks(self):
        """The injection vector is absent, not merely unused.

        Upstream injects from ``wrap_model_call`` / ``awrap_model_call``. If a
        future edit added either hook here, this middleware would regain the
        ability to alter the outgoing request; assert it defines neither
        beyond the inert base-class implementation.
        """
        for hook in ("wrap_model_call", "awrap_model_call", "before_model", "abefore_model"):
            assert getattr(TokenAttentionMiddleware, hook, None) is getattr(AgentMiddleware, hook, None), f"{hook} must not be overridden — it is a model-facing mutation point"

    def test_runtime_context_not_polluted(self):
        """No ``stop_reason`` stamp: this middleware does not end runs."""
        runtime = _Runtime({"run_id": "run-1"})
        _mw(threshold=1).after_model({"messages": [_ai(50_000)]}, runtime)
        assert set(runtime.context) == {"run_id"}


# ---------------------------------------------------------------------------
# Detection behavior
# ---------------------------------------------------------------------------


class TestDetection:
    def test_no_log_below_threshold(self, caplog):
        with caplog.at_level(logging.WARNING):
            _mw(threshold=1_000_000).after_model({"messages": [_ai(1_000)]}, _Runtime({"run_id": "r"}))
        assert "token attention threshold crossed" not in caplog.text

    def test_logs_once_per_run(self, caplog):
        mw = _mw(threshold=1_000)
        runtime = _Runtime({"run_id": "run-1"})
        state = {"messages": [_ai(50_000)]}
        with caplog.at_level(logging.WARNING):
            for _ in range(5):
                mw.after_model(state, runtime)
        assert caplog.text.count("token attention threshold crossed") == 1

    def test_separate_runs_each_log(self, caplog):
        mw = _mw(threshold=1_000)
        state = {"messages": [_ai(50_000)]}
        with caplog.at_level(logging.WARNING):
            mw.after_model(state, _Runtime({"run_id": "run-1"}))
            mw.after_model(state, _Runtime({"run_id": "run-2"}))
        assert caplog.text.count("token attention threshold crossed") == 2

    def test_log_payload_has_the_fields_needed_for_triage(self, caplog):
        """run_id / user_id / model / used / threshold, per the plan."""
        with caplog.at_level(logging.WARNING):
            _mw(threshold=1_000).after_model({"messages": [_ai(40_000, model="doubao-x")]}, _Runtime({"run_id": "run-xyz"}))
        record = next(r for r in caplog.records if "token attention" in r.getMessage())
        message = record.getMessage()
        assert "run_id=run-xyz" in message
        assert "model=doubao-x" in message
        assert "used=40000" in message
        assert "threshold=1000" in message
        assert "user_id=" in message

    def test_sums_history_so_subagent_tokens_count(self):
        """Subagent usage is folded into history retroactively; summing the
        whole history is what makes it visible."""
        mw = _mw(threshold=100)
        assert mw._sum_tokens([_ai(30, msg_id="a"), _ai(40, msg_id="b"), _ai(50, msg_id="c")]) == 120

    def test_counts_each_message_id_once(self):
        """Must agree with RunJournal, which dedups before writing the row.

        The console thresholds on ``RunRow.total_tokens`` (journal-deduped). If
        a replayed / re-emitted history let this middleware count the same
        AIMessage twice, it would flag runs the console does not — exactly the
        disagreement the class docstring rules out.
        """
        mw = _mw(threshold=1_000)
        duplicated = [_ai(600, msg_id="ai-1"), _ai(600, msg_id="ai-1")]
        assert mw._sum_tokens(duplicated) == 600

    def test_distinct_ids_still_accumulate(self):
        """Dedup must not collapse genuinely different calls."""
        mw = _mw()
        assert mw._sum_tokens([_ai(600, msg_id="ai-1"), _ai(400, msg_id="ai-2")]) == 1_000

    def test_duplicate_below_threshold_does_not_flag(self, caplog):
        """End-to-end of the same invariant: 600+600 must not cross 1000."""
        mw = _mw(threshold=1_000)
        state = {"messages": [_ai(600, msg_id="ai-1"), _ai(600, msg_id="ai-1")]}
        with caplog.at_level(logging.WARNING):
            mw.after_model(state, _Runtime({"run_id": "run-1"}))
        assert "token attention threshold crossed" not in caplog.text

    def test_messages_without_id_are_still_counted(self):
        """An unidentifiable message must not be silently dropped.

        Under-reporting a real crossing is worse than double-counting an
        anonymous one, so absent ids fall through to the sum.
        """
        mw = _mw()
        anonymous = [
            AIMessage(content="x", usage_metadata={"input_tokens": 5, "output_tokens": 5, "total_tokens": 10}),
            AIMessage(content="y", usage_metadata={"input_tokens": 5, "output_tokens": 5, "total_tokens": 10}),
        ]
        assert mw._sum_tokens(anonymous) == 20

    def test_non_ai_and_usageless_messages_ignored(self):
        mw = _mw()
        total = mw._sum_tokens([HumanMessage(content="hi"), ToolMessage(content="x", tool_call_id="c"), AIMessage(content="no usage", id="n")])
        assert total == 0

    def test_total_derived_from_input_plus_output_when_absent(self):
        """Defensive path: some providers omit total_tokens.

        ``AIMessage``'s pydantic schema requires ``total_tokens``, so this
        shape can only arrive from a provider adapter that writes the dict
        directly — hence the bypass here rather than a constructor call.
        """
        mw = _mw()
        msg = AIMessage(content="x", id="i")
        object.__setattr__(msg, "usage_metadata", {"input_tokens": 7, "output_tokens": 3})
        assert mw._sum_tokens([msg]) == 10

    def test_malformed_usage_does_not_raise(self):
        mw = _mw()
        msg = AIMessage(content="x", id="i")
        object.__setattr__(msg, "usage_metadata", {"total_tokens": "not-a-number"})
        assert mw._sum_tokens([msg]) == 0

    def test_threshold_zero_disables_everything(self, caplog):
        with caplog.at_level(logging.WARNING):
            result = _mw(threshold=0).after_model({"messages": [_ai(10**9)]}, _Runtime({"run_id": "r"}))
        assert result is None
        assert "token attention threshold crossed" not in caplog.text

    def test_empty_state_is_safe(self):
        mw = _mw()
        assert mw.after_model({}, _Runtime()) is None
        assert mw.after_model({"messages": []}, _Runtime()) is None

    def test_missing_run_id_falls_back_to_runtime_identity(self, caplog):
        """Two *live* contexts without run_id must not share a bucket.

        Both runtimes are held in locals for the duration: releasing the first
        would let CPython reuse its address for the second, making them
        collide by ``id()`` and hiding the property under test.
        """
        mw = _mw(threshold=10)
        state = {"messages": [_ai(1_000)]}
        runtime_a, runtime_b = _Runtime(), _Runtime()
        assert mw._run_id(runtime_a) != mw._run_id(runtime_b)
        with caplog.at_level(logging.WARNING):
            mw.after_model(state, runtime_a)
            mw.after_model(state, runtime_b)
        assert caplog.text.count("token attention threshold crossed") == 2

    def test_tracked_run_ids_are_bounded(self):
        """A long-lived process must not accumulate state for dead runs."""
        from deerflow.agents.middlewares.token_attention_middleware import _MAX_TRACKED_RUNS

        mw = _mw(threshold=10)
        state = {"messages": [_ai(1_000)]}
        for i in range(_MAX_TRACKED_RUNS + 50):
            mw.after_model(state, _Runtime({"run_id": f"run-{i}"}))
        assert len(mw._reported) <= _MAX_TRACKED_RUNS


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


class TestConfig:
    def test_default_threshold_is_the_measured_p95_not_upstream_default(self):
        """Upstream's 200k would flag 45% of this deployment's healthy runs."""
        assert DEFAULT_ATTENTION_THRESHOLD_TOKENS == 7_500_000
        assert TokenUsageConfig().attention_threshold_tokens == 7_500_000
        assert DEFAULT_ATTENTION_THRESHOLD_TOKENS != 200_000

    def test_negative_threshold_rejected(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            TokenUsageConfig(attention_threshold_tokens=-1)

    def test_from_config_constructor(self):
        mw = TokenAttentionMiddleware.from_config(TokenUsageConfig(attention_threshold_tokens=42))
        assert mw.threshold == 42


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


# CI 守卫（2026-09-23 收尾批）：标注用例经 AppConfig 读真实 config.yaml
# （gitignored，CI fresh checkout 不存在）——缺失时跳过，本地在位照常执行。
_REAL_CONFIG_EXISTS = any((Path(__file__).resolve().parents[i] / "config.yaml").exists() for i in (1, 2))
requires_real_config = pytest.mark.skipif(not _REAL_CONFIG_EXISTS, reason="依赖本地 gitignored config.yaml，CI 跳过")


@requires_real_config
class TestRegistration:
    @pytest.fixture(autouse=True)
    def _ensure_middlewares_package(self):
        """Drop MagicMock entries other test files leave in ``sys.modules``.

        ``test_subagent_executor.py`` (and ``conftest.py``) replace
        ``deerflow.agents`` / ``.middlewares`` with MagicMocks for the whole
        session to break a circular import. A later real submodule import then
        fails with "'deerflow.agents.middlewares' is not a package". Same
        workaround as ``test_tool_search.py::TestDeferredToolFilterMiddleware``.
        """
        import sys
        from unittest.mock import MagicMock

        for key in (
            "deerflow.agents",
            "deerflow.agents.middlewares",
            "deerflow.agents.middlewares.tool_error_handling_middleware",
        ):
            if isinstance(sys.modules.get(key), MagicMock):
                del sys.modules[key]

    def test_ported_budget_middleware_not_in_default_chains(self):
        """条目 10②（2026-09-12 缺陷批）：守卫口径更新——token_budget_middleware 由
        94773676e（2026-08-14 P1-T18）**刻意移植**，旧守卫钉「import 必须失败」相对
        那次移植已过时（守卫红 ≠ 产品缺陷）。新口径 = 行为断言（范本：
        test_subagent_middlewares_unchanged_by_this_batch）：模块存在可以，但
        TokenBudgetMiddleware **不得注册进默认中间件链**（lead 链与 subagent 链
        都不注册——预算拦截会掐断 agent 工具调用）。
        """
        import deerflow.agents.middlewares.token_budget_middleware  # noqa: F401, I001  (移植是刻意决策：import 成功是预期)
        from deerflow.agents.middlewares.tool_error_handling_middleware import (
            build_lead_runtime_middlewares,
            build_subagent_runtime_middlewares,
        )
        from deerflow.config import get_app_config

        cfg = get_app_config()
        lead_names = [type(m).__name__ for m in build_lead_runtime_middlewares(app_config=cfg)]
        assert "TokenBudgetMiddleware" not in lead_names
        sub_names = [type(m).__name__ for m in build_subagent_runtime_middlewares(app_config=cfg)]
        assert "TokenBudgetMiddleware" not in sub_names

    def test_subagent_middlewares_unchanged_by_this_batch(self):
        """Subagents get no token budget — this batch only records, so their
        behavior must be identical to before."""
        from deerflow.agents.middlewares.tool_error_handling_middleware import build_subagent_runtime_middlewares
        from deerflow.config import get_app_config

        names = [type(m).__name__ for m in build_subagent_runtime_middlewares(app_config=get_app_config())]
        assert "TokenBudgetMiddleware" not in names
        assert "TokenAttentionMiddleware" not in names
