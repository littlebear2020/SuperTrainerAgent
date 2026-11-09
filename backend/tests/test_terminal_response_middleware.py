"""Tests for TerminalResponseMiddleware (C-08, 2026-08-13; 2026-09-23 plan A1-A4)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, ToolMessage

from deerflow.agents.middlewares.terminal_response_middleware import (
    TerminalResponseMiddleware,
    _trim_middle_tool_outputs,
)


def _runtime(thread_id="t1", run_id="r1"):
    return SimpleNamespace(context={"thread_id": thread_id, "run_id": run_id})


def _empty_ai():
    return AIMessage(content="", id="empty-terminal-1")


def _post_tool_state():
    return {"messages": [HumanMessage(content="hi"), ToolMessage(content="ok", tool_call_id="c1"), _empty_ai()]}


def _first_call_state():
    return {"messages": [HumanMessage(content="hi"), _empty_ai()]}


class TestAfterModelGuard:
    def test_visible_content_passes_through(self):
        mw = TerminalResponseMiddleware()
        state = {"messages": [HumanMessage(content="hi"), ToolMessage(content="ok", tool_call_id="c1"), AIMessage(content="done")]}
        assert mw.after_model(state, _runtime()) is None

    def test_tool_call_intent_passes_through(self):
        mw = TerminalResponseMiddleware()
        ai = AIMessage(content="", tool_calls=[{"name": "bash", "id": "c2", "args": {}}])
        state = {"messages": [HumanMessage(content="hi"), ToolMessage(content="ok", tool_call_id="c1"), ai]}
        assert mw.after_model(state, _runtime()) is None

    # ── A1 七路场景（2026-09-23 批 1）────────────────────────────────────
    # 路一：首次调用即空（无工具结果）——扩围后介入重试。
    def test_first_call_empty_gets_retry(self):
        mw = TerminalResponseMiddleware()
        result = mw.after_model(_first_call_state(), _runtime())
        assert result is not None
        assert isinstance(result["messages"][0], RemoveMessage)
        assert result["jump_to"] == "model"

    # 路二：post-tool 空——原覆盖面保持。
    def test_empty_after_tool_gets_retry(self):
        mw = TerminalResponseMiddleware()
        ai = _empty_ai()
        state = {"messages": [HumanMessage(content="hi"), ToolMessage(content="ok", tool_call_id="c1"), ai]}
        result = mw.after_model(state, _runtime())
        assert result is not None
        # Empty terminal message removed (no stale empty turn in checkpoints)
        # and the model is retried.
        assert len(result["messages"]) == 1
        assert isinstance(result["messages"][0], RemoveMessage)
        assert result["jump_to"] == "model"

    # 路三：重试成功——预算内的重试不落兜底。
    def test_retry_then_success_stops_guard(self):
        mw = TerminalResponseMiddleware()
        mw.after_model(_post_tool_state(), _runtime())  # consume retry 1
        recovered = {"messages": [HumanMessage(content="hi"), ToolMessage(content="ok", tool_call_id="c1"), AIMessage(content="recovered")]}
        assert mw.after_model(recovered, _runtime()) is None

    # 路四：3 次耗尽 → 可见兜底（中文文案 + deerflow_error_fallback 标记）。
    def test_exhausted_budget_falls_back_in_chinese(self):
        mw = TerminalResponseMiddleware(max_retries=3)
        state = _post_tool_state()
        for _ in range(3):
            result = mw.after_model(state, _runtime())
            assert result is not None
            assert result["jump_to"] == "model"
        result = mw.after_model(state, _runtime())  # 4th empty: exhausted
        assert result is not None
        fallback = result["messages"][0]
        assert "继续" in fallback.content
        assert fallback.additional_kwargs.get("deerflow_error_fallback") is True
        assert fallback.additional_kwargs.get("error_reason")

    # 路五：豁免——有可见内容 / 工具调用意图 / 错误标记绝不介入。
    def test_exemptions_never_intervene(self):
        mw = TerminalResponseMiddleware()
        error_ai = AIMessage(content="", additional_kwargs={"deerflow_error_fallback": True})
        assert mw.after_model({"messages": [HumanMessage(content="hi"), error_ai]}, _runtime()) is None

    # 路六：无真实用户消息的纯内部调用不介入（#4027 invariant）。
    def test_internal_invocation_without_real_user_message_is_left_alone(self):
        mw = TerminalResponseMiddleware()
        hidden = HumanMessage(content="internal payload", additional_kwargs={"hide_from_ui": True})
        state = {"messages": [hidden, ToolMessage(content="ok", tool_call_id="c1"), _empty_ai()]}
        assert mw.after_model(state, _runtime()) is None

    # 路七：IM 渠道来源（真实用户消息，无 hide_from_ui）正常介入。
    def test_channel_originated_run_is_covered(self):
        mw = TerminalResponseMiddleware()
        channel_user = HumanMessage(content="来自飞书的消息", additional_kwargs={"channel": "feishu"})
        state = {"messages": [channel_user, _empty_ai()]}
        result = mw.after_model(state, _runtime())
        assert result is not None
        assert result["jump_to"] == "model"

    def test_retry_budget_is_per_run(self):
        """A new run gets a fresh retry budget."""
        mw = TerminalResponseMiddleware()
        state = _post_tool_state()
        mw.after_model(state, _runtime("t1", "r1"))
        mw.after_model(state, _runtime("t1", "r1"))
        mw.after_model(state, _runtime("t1", "r1"))
        # Same thread, new run: budget reset → retry (RemoveMessage), not fallback.
        result = mw.after_model(state, _runtime("t1", "r2"))
        assert result is not None
        assert isinstance(result["messages"][0], RemoveMessage)


class TestRecoveryPromptInjection:
    def test_recovery_prompt_appended_on_retry(self):
        mw = TerminalResponseMiddleware()
        mw.after_model(_post_tool_state(), _runtime())

        overridden = []

        request = SimpleNamespace()
        request.messages = [HumanMessage(content="hi")]
        request.runtime = _runtime()
        request.override = lambda messages: overridden.append(messages) or SimpleNamespace(messages=messages)

        augmented = mw.wrap_model_call(request, lambda r: "model-result")
        assert augmented == "model-result"
        assert len(overridden[0]) == 2
        assert overridden[0][1].name == "terminal_response_recovery"
        assert "empty" in overridden[0][1].content

    def test_no_prompt_without_pending_retry(self):
        mw = TerminalResponseMiddleware()
        request = SimpleNamespace()
        request.messages = [HumanMessage(content="hi")]
        request.runtime = _runtime()
        called = []
        request.override = lambda messages: called.append(messages) or SimpleNamespace(messages=messages)
        mw.wrap_model_call(request, lambda r: "model-result")
        assert called == []  # request passed through untouched


class TestCooldown:
    @pytest.mark.asyncio
    async def test_async_path_sleeps_exponential_capped(self):
        delays: list[float] = []

        async def recorder(seconds: float) -> None:
            delays.append(seconds)

        mw = TerminalResponseMiddleware(max_retries=4, cooldown_base_seconds=2.0, cooldown_max_seconds=8.0)
        mw._sleep = recorder
        state = _post_tool_state()
        for _ in range(4):
            await mw.aafter_model(state, _runtime())
        assert delays == [2.0, 4.0, 8.0, 8.0]  # base*2^(n-1), capped at max; no sleep on fallback

    @pytest.mark.asyncio
    async def test_sync_path_has_no_cooldown(self):
        mw = TerminalResponseMiddleware(cooldown_base_seconds=5.0)
        original_sleep = asyncio.sleep

        async def exploding_sleep(seconds: float) -> None:  # pragma: no cover - guard assertion
            raise AssertionError("sync after_model must not cool down")

        mw._sleep = exploding_sleep
        # after_model (sync) drives _apply directly; the injected sleep must never run.
        mw.after_model(_post_tool_state(), _runtime())
        mw._sleep = original_sleep


class TestTrimOnRetry:
    def test_trim_only_from_second_retry(self):
        mw = TerminalResponseMiddleware(trim_on_retry=True)
        mw.after_model(_post_tool_state(), _runtime())  # retry 1

        big = "x" * 5000
        # 13 messages: the older half carries oversized tool outputs.
        messages = [HumanMessage(content="hi")]
        for i in range(6):
            messages.append(AIMessage(content="", tool_calls=[{"name": "bash", "id": f"c{i}", "args": {}}]))
            messages.append(ToolMessage(content=big, tool_call_id=f"c{i}"))

        overridden = []
        request = SimpleNamespace()
        request.messages = messages
        request.runtime = _runtime()
        request.override = lambda messages: overridden.append(messages) or SimpleNamespace(messages=messages)

        mw.after_model(_post_tool_state(), _runtime())  # retry 2 → attempt=2 ≥ threshold
        mw.wrap_model_call(request, lambda r: "model-result")

        window = overridden[-1]
        trimmed = [m for m in window if isinstance(m, ToolMessage) and "middle truncated" in str(m.content)]
        # Recent _TRIM_KEEP_TAIL_MESSAGES messages stay verbatim; older oversized ones get trimmed.
        assert 0 < len(trimmed) < 6
        # Zero state writes: the original state message objects are untouched.
        assert all(m.content == big for m in messages if isinstance(m, ToolMessage))

    def test_trim_disabled_by_config(self):
        mw = TerminalResponseMiddleware(trim_on_retry=False)
        mw.after_model(_post_tool_state(), _runtime())
        mw.after_model(_post_tool_state(), _runtime())  # retry 2

        big = "y" * 5000
        messages = [HumanMessage(content="hi")] + [ToolMessage(content=big, tool_call_id=f"c{i}") for i in range(6)] + [AIMessage(content="")]
        overridden = []
        request = SimpleNamespace()
        request.messages = messages
        request.runtime = _runtime()
        request.override = lambda messages: overridden.append(messages) or SimpleNamespace(messages=messages)
        mw.wrap_model_call(request, lambda r: "model-result")
        window = overridden[-1]
        assert all("middle truncated" not in str(m.content) for m in window)

    def test_trim_middle_tool_outputs_pure_function(self):
        big = "z" * 4000
        msgs = [HumanMessage(content="hi"), ToolMessage(content=big, tool_call_id="c0"), ToolMessage(content=big, tool_call_id="c1"), AIMessage(content="tail")]
        out = _trim_middle_tool_outputs(msgs)
        # Small window (< keep-tail window) → nothing trimmed.
        assert [m.content for m in out if isinstance(m, ToolMessage)] == [big, big]


class TestObservability:
    def test_fallback_stamps_empty_fallback_count(self):
        mw = TerminalResponseMiddleware(max_retries=1)
        state = _post_tool_state()
        runtime = _runtime()
        mw.after_model(state, runtime)  # retry 1 (budget=1)
        mw.after_model(state, runtime)  # exhausted → fallback
        assert runtime.context["empty_fallback_count"] == 1
        mw.after_model(state, runtime)  # another empty terminal → fallback again
        assert runtime.context["empty_fallback_count"] == 2

    def test_fallback_emits_stream_event(self, monkeypatch):
        emitted = []

        class _Writer:
            def __call__(self, payload):
                emitted.append(payload)

        import langgraph.config as lg_config

        # 直接补丁 langgraph.config 模块属性——中间件在 _emit_event 内
        # `from langgraph.config import get_stream_writer` 按调用时取值。
        monkeypatch.setattr(lg_config, "get_stream_writer", lambda: _Writer(), raising=False)

        mw = TerminalResponseMiddleware(max_retries=2)
        state = _post_tool_state()
        runtime = _runtime()
        mw.after_model(state, runtime)
        mw.after_model(state, runtime)
        assert emitted[0]["type"] == "llm_empty_retry"
        assert emitted[0]["attempt"] == 1
        assert emitted[0]["max_retries"] == 2  # M 取配置值，不写死
        assert "第 1/2 次" in emitted[0]["message"]
        assert emitted[1] == {"type": "llm_empty_retry", "attempt": 2, "max_retries": 2, "message": emitted[1]["message"]}
        mw.after_model(state, runtime)  # exhausted
        assert emitted[-1]["type"] == "llm_empty_fallback"
        assert "继续" in emitted[-1]["message"]


# CI 守卫（2026-09-23 收尾批）：标注用例经 AppConfig 读真实 config.yaml
# （gitignored，CI fresh checkout 不存在）——缺失时跳过，本地在位照常执行。
_REAL_CONFIG_EXISTS = any((Path(__file__).resolve().parents[i] / "config.yaml").exists() for i in (1, 2))
requires_real_config = pytest.mark.skipif(not _REAL_CONFIG_EXISTS, reason="依赖本地 gitignored config.yaml，CI 跳过")


@requires_real_config
class TestSubagentChainBreakers:
    def test_subagent_runtime_chain_includes_loop_detection_and_terminal_response(self):
        """C-08: subagent chains must carry the loop breaker and the terminal
        response guard — previously neither was wired (a subagent stuck in a
        same-tool loop burned the whole delegation budget silently)."""
        from deerflow.agents.middlewares.loop_detection_middleware import LoopDetectionMiddleware
        from deerflow.agents.middlewares.tool_error_handling_middleware import build_subagent_runtime_middlewares
        from deerflow.config import get_app_config

        mws = build_subagent_runtime_middlewares(app_config=get_app_config(), agent_name=None)
        types = [type(mw) for mw in mws]
        assert LoopDetectionMiddleware in types, f"subagent chain lacks loop detection: {types}"
        assert TerminalResponseMiddleware in types, f"subagent chain lacks terminal response guard: {types}"
