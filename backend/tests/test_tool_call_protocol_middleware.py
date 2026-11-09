"""Tests for ToolCallProtocolMiddleware（2026-10-14 加固批 Task D）。

背景事故（run ``0c8fd42c-e80a-49bf-81d8-c82311c35cc2``）：模型把工具调用写成
正文 ``<tool_call>{"name":"bash", ...}</tool_call>``，同时 finish_reason="stop"、
tool_calls=[]、invalid_tool_calls=[]、无对应 ToolMessage——旧链路把它当作合法
最终答案，run 以 status=success 结束，命令从未执行却"看起来完成了"。

守卫语义（用户已拍板）：
  * 只识别完整、JSON 可解析、无原生工具调用的 <tool_call>...</tool_call> 正文；
  * 首次命中：移除伪消息 + jump_to="model"，经 wrap_model_call 注入
    ``tool_protocol_recovery`` 恢复提示（要求改用原生工具调用），每 run 一次；
  * 第二次同一 run 命中：stop_reason="tool_protocol_error" + 抛 ToolProtocolError，
    经 worker 通用异常路径落 status=error；
  * 任何情况下都不解析执行正文中的命令。
"""

from unittest.mock import MagicMock

import pytest
from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, ToolMessage
from langchain_core.runnables import Runnable
from langchain_core.tools import tool as as_tool
from pydantic import PrivateAttr

from deerflow.agents.middlewares.tool_call_protocol_middleware import (
    ToolCallProtocolMiddleware,
    ToolProtocolError,
)

# ---------------------------------------------------------------------------
# 事故最小夹具（溯源标识 run_id=0c8fd42c；只保留必要字段，不复制业务内容）
# ---------------------------------------------------------------------------

_PSEUDO_BODY = '<tool_call>{"name":"bash","arguments":{"command":"pwd"}}</tool_call>'
_INCIDENT_RUN_ID = "0c8fd42c"


def _pseudo_message(body=_PSEUDO_BODY, msg_id="pseudo_1"):
    """The incident response shape: plain-text tool call, finish stop, no native calls."""
    return AIMessage(content=body, id=msg_id, response_metadata={"finish_reason": "stop"})


def _make_runtime(thread_id="test-thread", run_id="test-run"):
    runtime = MagicMock()
    runtime.context = {"thread_id": thread_id, "run_id": run_id}
    runtime.stream_writer = object()
    return runtime


def _make_state(last_message):
    return {"messages": [HumanMessage(content="do it"), last_message]}


def _make_request(messages, runtime):
    request = MagicMock()
    request.messages = list(messages)
    request.runtime = runtime
    request.override = lambda **updates: _override_request(request, updates)
    return request


def _override_request(request, updates):
    new = MagicMock()
    new.messages = updates.get("messages", request.messages)
    new.runtime = updates.get("runtime", request.runtime)
    new.override = lambda **u: _override_request(new, u)
    return new


def _capture_handler():
    captured: list = []

    def handler(req):
        captured.append(req)
        return MagicMock()

    return captured, handler


class _CapturingFakeMessagesListChatModel(FakeMessagesListChatModel):
    """Fake chat model that records each model request's messages."""

    _seen_messages: list = PrivateAttr(default_factory=list)

    @property
    def seen_messages(self) -> list:
        return self._seen_messages

    def bind_tools(
        self,
        tools,
        *,
        tool_choice=None,
        **kwargs,
    ) -> Runnable:
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self._seen_messages.append(list(messages))
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


# ---------------------------------------------------------------------------
# 检测边界
# ---------------------------------------------------------------------------


class TestDetectionBoundary:
    def test_complete_parseable_pseudo_call_is_detected(self):
        mw = ToolCallProtocolMiddleware()
        assert mw._is_pseudo_tool_call(_pseudo_message()) is True

    def test_surrounding_whitespace_still_detected(self):
        mw = ToolCallProtocolMiddleware()
        assert mw._is_pseudo_tool_call(_pseudo_message(body=f"\n\n  {_PSEUDO_BODY}  \n")) is True

    @pytest.mark.parametrize(
        "body",
        [
            # 代码块示例——不是完整匹配的伪调用
            f"```xml\n{_PSEUDO_BODY}\n```",
            f"Here is the call I would make:\n{_PSEUDO_BODY}",
            # 残缺 XML
            '<tool_call>{"name":"bash","arguments":{"command":"pwd"}}',
            '<tool_call>{"name":"bash"}</tool_call> extra trailing text',
            # JSON 不可解析
            "<tool_call>{not json}</tool_call>",
            # name 非空字符串约束
            '<tool_call>{"name":"","arguments":{}}</tool_call>',
            '<tool_call>{"name":123,"arguments":{}}</tool_call>',
            # arguments 必须是对象
            '<tool_call>{"name":"bash","arguments":"pwd"}</tool_call>',
            '<tool_call>{"name":"bash"}</tool_call>',
            # <invoke> 变体不在本批拦截范围
            '<invoke name="bash"><parameter name="command">pwd</parameter></invoke>',
            # 普通叙述
            "I would run pwd to answer your question.",
        ],
    )
    def test_non_matching_bodies_not_detected(self, body):
        mw = ToolCallProtocolMiddleware()
        assert mw._is_pseudo_tool_call(_pseudo_message(body=body)) is False

    def test_native_tool_call_content_not_detected(self):
        """响应已带原生 tool_calls 时正文里的 <tool_call> 文本不算伪调用。"""
        mw = ToolCallProtocolMiddleware()
        msg = AIMessage(
            content=_PSEUDO_BODY,
            tool_calls=[{"name": "bash", "id": "c1", "args": {"command": "pwd"}}],
            response_metadata={"finish_reason": "tool_calls"},
        )
        assert mw._is_pseudo_tool_call(msg) is False

    def test_invalid_tool_calls_present_not_detected(self):
        mw = ToolCallProtocolMiddleware()
        msg = AIMessage(
            content=_PSEUDO_BODY,
            invalid_tool_calls=[{"name": "bash", "id": "c1", "args": "{}", "error": "malformed"}],
        )
        assert mw._is_pseudo_tool_call(msg) is False

    def test_list_content_not_detected(self):
        """首版只拦截字符串正文；content block 列表保守放行。"""
        mw = ToolCallProtocolMiddleware()
        msg = AIMessage(content=[{"type": "text", "text": _PSEUDO_BODY}])
        assert mw._is_pseudo_tool_call(msg) is False

    def test_non_ai_last_message_ignored(self):
        mw = ToolCallProtocolMiddleware()
        runtime = _make_runtime()
        assert mw.after_model({"messages": [HumanMessage(content=_PSEUDO_BODY)]}, runtime) is None


# ---------------------------------------------------------------------------
# 一次恢复 + 二次终止
# ---------------------------------------------------------------------------


class TestRecoveryAndTermination:
    def test_first_pseudo_call_jumps_to_model_without_tool_message(self):
        """首次命中：移除伪消息、jump_to="model"、不创建 ToolMessage、不执行任何工具。"""
        mw = ToolCallProtocolMiddleware()
        runtime = _make_runtime(run_id=_INCIDENT_RUN_ID)
        pseudo = _pseudo_message()

        result = mw.after_model(_make_state(pseudo), runtime)

        assert isinstance(result, dict)
        assert result.get("jump_to") == "model"
        updates = result.get("messages", [])
        assert updates, "must schedule the pseudo-message removal"
        for update in updates:
            assert isinstance(update, RemoveMessage)
        assert updates[0].id == pseudo.id
        # 不产生任何 ToolMessage（执行结果只能来自真实工具节点）
        assert not any(isinstance(update, ToolMessage) for update in updates)

    def test_recovery_prompt_injected_once_then_drained(self):
        mw = ToolCallProtocolMiddleware()
        runtime = _make_runtime()
        mw.after_model(_make_state(_pseudo_message()), runtime)

        captured, handler = _capture_handler()
        mw.wrap_model_call(_make_request([HumanMessage(content="hi")], runtime), handler)
        first = captured[0].messages
        recovery = [m for m in first if isinstance(m, HumanMessage) and m.name == "tool_protocol_recovery"]
        assert len(recovery) == 1
        assert "native tool call" in recovery[0].content
        assert (recovery[0].additional_kwargs or {}).get("hide_from_ui") is True

        mw.wrap_model_call(_make_request([HumanMessage(content="hi")], runtime), handler)
        second = captured[1].messages
        assert not any(isinstance(m, HumanMessage) and m.name == "tool_protocol_recovery" for m in second)

    def test_second_pseudo_call_terminates_at_next_model_call(self):
        """第二次命中：after_model 提交移除 + jump；wrap_model_call 抛
        ToolProtocolError（模型不再被调用）并已打 stop_reason。"""
        mw = ToolCallProtocolMiddleware()
        runtime = _make_runtime(run_id=_INCIDENT_RUN_ID)

        mw.after_model(_make_state(_pseudo_message(msg_id="pseudo_1")), runtime)
        result = mw.after_model(_make_state(_pseudo_message(msg_id="pseudo_2")), runtime)
        assert result.get("jump_to") == "model"
        assert isinstance(result["messages"][0], RemoveMessage)
        assert result["messages"][0].id == "pseudo_2"

        with pytest.raises(ToolProtocolError):
            mw.wrap_model_call(_make_request([HumanMessage(content="hi")], runtime), lambda req: MagicMock())

        assert runtime.context["stop_reason"] == "tool_protocol_error"

    def test_prestamped_stop_reason_not_overwritten(self):
        mw = ToolCallProtocolMiddleware()
        runtime = _make_runtime()
        runtime.context["stop_reason"] = "loop_hard_limit"

        mw.after_model(_make_state(_pseudo_message(msg_id="p1")), runtime)
        mw.after_model(_make_state(_pseudo_message(msg_id="p2")), runtime)
        with pytest.raises(ToolProtocolError):
            mw.wrap_model_call(_make_request([HumanMessage(content="hi")], runtime), lambda req: MagicMock())

        assert runtime.context["stop_reason"] == "loop_hard_limit"

    def test_native_tool_call_after_first_pseudo_passes_through(self):
        """首次伪调用后模型改用原生工具调用 → 直接放行（不再拦截）。"""
        mw = ToolCallProtocolMiddleware()
        runtime = _make_runtime()

        mw.after_model(_make_state(_pseudo_message()), runtime)

        native = AIMessage(content="", tool_calls=[{"name": "bash", "id": "c1", "args": {"command": "pwd"}}])
        assert mw.after_model(_make_state(native), runtime) is None

        # 之后再次出现正文伪调用（已消耗唯一恢复机会）→ 终止
        mw.after_model(_make_state(_pseudo_message(msg_id="p2")), runtime)
        with pytest.raises(ToolProtocolError):
            mw.wrap_model_call(_make_request([HumanMessage(content="hi")], runtime), lambda req: MagicMock())

    def test_new_run_gets_fresh_recovery_budget(self):
        """恢复计数按 (thread_id, run_id)：新 run 的首次伪调用重新获得一次恢复。"""
        mw = ToolCallProtocolMiddleware()
        runtime_a = _make_runtime(run_id="run-A")
        runtime_b = _make_runtime(run_id="run-B")

        mw.after_model(_make_state(_pseudo_message(msg_id="a1")), runtime_a)
        mw.after_model(_make_state(_pseudo_message(msg_id="a2")), runtime_a)
        with pytest.raises(ToolProtocolError):
            mw.wrap_model_call(_make_request([HumanMessage(content="hi")], runtime_a), lambda req: MagicMock())

        # run B 未被 run A 的失败牵连——首次伪调用仍走恢复路径
        result = mw.after_model(_make_state(_pseudo_message(msg_id="b1")), runtime_b)
        assert result.get("jump_to") == "model"
        captured, handler = _capture_handler()
        mw.wrap_model_call(_make_request([HumanMessage(content="hi")], runtime_b), handler)
        recovery = [m for m in captured[0].messages if isinstance(m, HumanMessage) and m.name == "tool_protocol_recovery"]
        assert len(recovery) == 1

    def test_after_agent_clears_unconsumed_pending_flags(self):
        """评审修复（PR#21 #3）回归：run 结束时未被消费的终止/恢复标志
        必须强制失效，防悬挂泄漏到后续流程。"""
        mw = ToolCallProtocolMiddleware()
        runtime = _make_runtime()
        mw._pending_terminations[mw._key(runtime)] = True
        mw._pending_prompts[mw._key(runtime)] = True
        mw._recoveries[mw._key(runtime)] = 1

        mw.after_agent({"messages": []}, runtime)

        assert not mw._pending_terminations
        assert not mw._pending_prompts
        # 恢复预算（每 run 一次）不随 after_agent 清理——跨 run 由 before_agent 清 stale。
        assert mw._recoveries.get(mw._key(runtime)) == 1

    def test_before_agent_clears_stale_runs(self):
        mw = ToolCallProtocolMiddleware()
        runtime_a = _make_runtime(run_id="run-A")
        runtime_b = _make_runtime(run_id="run-B")

        mw.after_model(_make_state(_pseudo_message()), runtime_a)
        assert mw._recoveries

        mw.before_agent({"messages": []}, runtime_b)
        assert not mw._recoveries


# ---------------------------------------------------------------------------
# 真实 agent 图：不执行伪命令 + 恢复 + 终止
# ---------------------------------------------------------------------------


class TestRealAgentGraph:
    @staticmethod
    def _bash_tool(executed):
        @as_tool
        def bash(command: str) -> str:
            """Run a fake shell command."""
            executed.append(command)
            return f"ran: {command}"

        return bash

    def test_second_offense_errors_and_never_executes_pseudo_command(self):
        """重放事故形态：两次伪调用 → ToolProtocolError、bash 从未执行、无伪调用 ToolMessage。"""
        executed: list[str] = []
        mw = ToolCallProtocolMiddleware()
        model = _CapturingFakeMessagesListChatModel(
            responses=[
                AIMessage(content=_PSEUDO_BODY, id="pseudo_1", response_metadata={"finish_reason": "stop"}),
                AIMessage(content=_PSEUDO_BODY, id="pseudo_2", response_metadata={"finish_reason": "stop"}),
            ],
        )
        graph = create_agent(model=model, tools=[self._bash_tool(executed)], middleware=[mw])

        with pytest.raises(ToolProtocolError):
            graph.invoke(
                {"messages": [("user", "run pwd")]},
                context={"thread_id": "proto-thread", "run_id": _INCIDENT_RUN_ID},
                config={"recursion_limit": 10},
            )

        # 正文中的 pwd 从未被解析执行
        assert executed == []
        # 第一次伪调用后请求里出现过一次恢复提示
        recovery_requests = [messages for messages in model.seen_messages if any(isinstance(m, HumanMessage) and m.name == "tool_protocol_recovery" for m in messages)]
        assert len(recovery_requests) == 1

    def test_first_offense_recovers_to_native_call(self):
        """首次伪调用 → 恢复 → 模型改用原生调用 → 工具真实执行、run 正常收尾。"""
        executed: list[str] = []
        mw = ToolCallProtocolMiddleware()
        model = _CapturingFakeMessagesListChatModel(
            responses=[
                AIMessage(content=_PSEUDO_BODY, id="pseudo_1", response_metadata={"finish_reason": "stop"}),
                AIMessage(content="", tool_calls=[{"name": "bash", "id": "native_1", "args": {"command": "pwd"}}]),
                AIMessage(content="done: ran pwd"),
            ],
        )
        graph = create_agent(model=model, tools=[self._bash_tool(executed)], middleware=[mw])

        result = graph.invoke(
            {"messages": [("user", "run pwd")]},
            context={"thread_id": "proto-thread-2", "run_id": "recovery-run"},
            config={"recursion_limit": 10},
        )

        assert executed == ["pwd"]
        assert result["messages"][-1].content == "done: ran pwd"
        # 伪消息已被移除：最终消息里没有正文伪调用
        assert not any(isinstance(m, AIMessage) and "<tool_call>" in str(m.content) for m in result["messages"])
        # 恢复提示不落 checkpoint
        assert not any(isinstance(m, HumanMessage) and m.name == "tool_protocol_recovery" for m in result["messages"])

    def test_errored_run_does_not_persist_pseudo_message(self, tmp_path):
        """第二次命中走异常路径：抛出发生在节点提交前，伪消息不进 checkpoint。"""
        from langgraph.checkpoint.memory import InMemorySaver

        executed: list[str] = []
        mw = ToolCallProtocolMiddleware()
        model = _CapturingFakeMessagesListChatModel(
            responses=[
                AIMessage(content=_PSEUDO_BODY, id="pseudo_1", response_metadata={"finish_reason": "stop"}),
                AIMessage(content=_PSEUDO_BODY, id="pseudo_2", response_metadata={"finish_reason": "stop"}),
            ],
        )
        graph = create_agent(model=model, tools=[self._bash_tool(executed)], middleware=[mw], checkpointer=InMemorySaver())

        with pytest.raises(ToolProtocolError):
            graph.invoke(
                {"messages": [("user", "run pwd")]},
                config={"configurable": {"thread_id": "ckpt-thread"}, "recursion_limit": 10},
                context={"thread_id": "ckpt-thread", "run_id": "ckpt-run"},
            )

        final_state = graph.get_state({"configurable": {"thread_id": "ckpt-thread"}})
        persisted = list(final_state.values.get("messages", [])) if final_state.values else []
        assert not any(isinstance(m, AIMessage) and "<tool_call>" in str(m.content) for m in persisted)


# ---------------------------------------------------------------------------
# 全链路（外层 LLMErrorHandling 不得吞协议终断）+ 瞬时失败恢复（2026-10-15 评审二轮）
# ---------------------------------------------------------------------------


def _llm_error_middleware(**overrides):
    from types import SimpleNamespace

    from deerflow.agents.middlewares.llm_error_handling_middleware import LLMErrorHandlingMiddleware

    app_config = SimpleNamespace(circuit_breaker=SimpleNamespace(failure_threshold=overrides.pop("failure_threshold", 5), recovery_timeout_sec=60))
    mw = LLMErrorHandlingMiddleware(app_config=app_config)
    for key, value in overrides.items():
        setattr(mw, key, value)
    return mw


class TestFullChainWithLLMErrorHandling:
    def test_second_offense_propagates_through_llm_error_handling(self):
        """真实 lead 链形态：LLMErrorHandling 在外层、协议守卫在内层——二次违规的
        ToolProtocolError 必须穿透外层兜底直达 worker（不得被转成 fallback
        AIMessage 伪装 success，2026-10-15 评审二轮关键缺陷）。"""
        executed: list[str] = []
        protocol = ToolCallProtocolMiddleware()
        llm_guard = _llm_error_middleware(retry_max_attempts=1, retry_base_delay_ms=1)
        model = _CapturingFakeMessagesListChatModel(
            responses=[
                AIMessage(content=_PSEUDO_BODY, id="pseudo_1", response_metadata={"finish_reason": "stop"}),
                AIMessage(content=_PSEUDO_BODY, id="pseudo_2", response_metadata={"finish_reason": "stop"}),
            ],
        )
        graph = create_agent(model=model, tools=[TestRealAgentGraph._bash_tool(executed)], middleware=[llm_guard, protocol])

        with pytest.raises(ToolProtocolError):
            graph.invoke(
                {"messages": [("user", "run pwd")]},
                context={"thread_id": "fullchain-thread", "run_id": "fullchain-run"},
                config={"recursion_limit": 10},
            )

        # 不产生兜底 AIMessage（deerflow_error_fallback 是 LLMErrorHandling 的
        # 兜底标记——协议终断不得走它）
        assert executed == []
        assert model.seen_messages, "model was invoked at least once"

    def test_recovery_prompt_restored_after_failed_model_call(self):
        """恢复提示在模型调用瞬时失败后必须回队——外层 LLMErrorHandling 重试时
        wrap 再次执行，重试请求必须仍携带 tool_protocol_recovery（#5433 同型）。"""
        mw = ToolCallProtocolMiddleware()
        runtime = _make_runtime()
        mw.after_model(_make_state(_pseudo_message()), runtime)

        def failing_handler(_req):
            raise RuntimeError("transient provider 500")

        captured, ok_handler = _capture_handler()
        with pytest.raises(RuntimeError):
            mw.wrap_model_call(_make_request([HumanMessage(content="hi")], runtime), failing_handler)
        # 失败后提示已回队
        assert mw._pending_prompts.get(mw._key(runtime))

        mw.wrap_model_call(_make_request([HumanMessage(content="hi")], runtime), ok_handler)
        recovery = [m for m in captured[0].messages if isinstance(m, HumanMessage) and m.name == "tool_protocol_recovery"]
        assert len(recovery) == 1

    @pytest.mark.asyncio
    async def test_recovery_prompt_restored_after_failed_async_model_call(self):
        mw = ToolCallProtocolMiddleware()
        runtime = _make_runtime()
        mw.after_model(_make_state(_pseudo_message()), runtime)

        async def failing_handler(_req):
            raise RuntimeError("transient provider 500")

        request = _make_request([HumanMessage(content="hi")], runtime)
        with pytest.raises(RuntimeError):
            await mw.awrap_model_call(request, failing_handler)
        assert mw._pending_prompts.get(mw._key(runtime))


class TestProductionOrderChain:
    """2026-10-15 评审三轮（对抗工作流实证）：jump_to 是 langgraph EphemeralValue，
    after_model 反序链中排在其后的每个常驻钩子节点都会耗一步——缺
    @hook_config(can_jump_to=["model"]) 时跳转值在到达路由边前被清除，生产链上
    首次违规静默空成功（无恢复、无二次调用）。本组测试用生产注册序链形态钉死：
    恢复可达、二次违规 ToolProtocolError 穿透全部中间层。"""

    @staticmethod
    def _production_chain(executed):
        from deerflow.agents.middlewares.loop_detection_middleware import LoopDetectionMiddleware
        from deerflow.agents.middlewares.terminal_response_middleware import TerminalResponseMiddleware

        llm_guard = _llm_error_middleware(retry_max_attempts=1, retry_base_delay_ms=1)
        # 生产注册序：基座段（LLMErrorHandling 在 build_lead_runtime_middlewares）→
        # lead 段 Summarization→…→LoopDetection→…→TerminalResponse→ToolCallProtocol
        return [
            llm_guard,
            LoopDetectionMiddleware(warn_threshold=99, hard_limit=99),
            TerminalResponseMiddleware(max_retries=1),
            ToolCallProtocolMiddleware(),
        ]

    def test_production_order_chain_recovers_then_terminates(self):
        """生产序链（4 个 after_model 中间件）：首次违规恢复提示可达模型、二次违规
        ToolProtocolError 穿透 LoopDetection/TerminalResponse/LLMErrorHandling 直达图外。"""
        executed: list[str] = []
        model = _CapturingFakeMessagesListChatModel(
            responses=[
                AIMessage(content=_PSEUDO_BODY, id="p1", response_metadata={"finish_reason": "stop"}),
                AIMessage(content=_PSEUDO_BODY, id="p2", response_metadata={"finish_reason": "stop"}),
            ],
        )
        graph = create_agent(model=model, tools=[TestRealAgentGraph._bash_tool(executed)], middleware=self._production_chain(executed))

        with pytest.raises(ToolProtocolError):
            graph.invoke(
                {"messages": [("user", "run pwd")]},
                context={"thread_id": "prodorder-thread", "run_id": "prodorder-run"},
                config={"recursion_limit": 25},
            )

        # 首次违规的恢复提示真实到达过模型（jump 生效，模型被二次调用）
        assert len(model.seen_messages) == 2
        recovery = [m for m in model.seen_messages[1] if isinstance(m, HumanMessage) and m.name == "tool_protocol_recovery"]
        assert len(recovery) == 1
        assert executed == []

    def test_tool_protocol_error_passes_through_real_summarization(self):
        """Summarization 的 except Exception→handler(request) 回落（enabled=true 的
        生产形态，摘要路径触发时）不得吞掉协议终断——终止标志已被首次调用弹出，
        回落重调等于带原始请求再打一次模型（评审三轮 critical）。"""
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        from deerflow.agents.middlewares.summarization_middleware import DeerFlowSummarizationMiddleware

        model_mock = MagicMock()
        summary_model = model_mock.with_config.return_value
        summary_model.invoke.return_value = SimpleNamespace(text="compressed summary")
        summarization = DeerFlowSummarizationMiddleware(
            model=model_mock,
            trigger=("messages", 2),
            keep=("messages", 1),
            token_counter=len,
            skill_file_read_tool_names=None,
            preserve_recent_skill_count=0,
            preserve_recent_skill_tokens=0,
            preserve_recent_skill_tokens_per_skill=0,
        )

        executed: list[str] = []
        model = _CapturingFakeMessagesListChatModel(
            responses=[
                AIMessage(content=_PSEUDO_BODY, id="p1", response_metadata={"finish_reason": "stop"}),
                AIMessage(content=_PSEUDO_BODY, id="p2", response_metadata={"finish_reason": "stop"}),
            ],
        )
        graph = create_agent(model=model, tools=[TestRealAgentGraph._bash_tool(executed)], middleware=[_llm_error_middleware(retry_max_attempts=1), summarization, ToolCallProtocolMiddleware()])

        with pytest.raises(ToolProtocolError):
            graph.invoke(
                {"messages": [("user", "run pwd")]},
                context={"thread_id": "summ-thread", "run_id": "summ-run"},
                config={"recursion_limit": 25},
            )

        # 摘要回落不得重调模型：模型总调用数 = 首次违规 + 恢复后的第二次违规
        assert len(model.seen_messages) == 2
        assert executed == []
        # OCR（PR#22）：协议终断穿透时，本次调用已 stash 的摘要 checkpoint 必须
        # 同步丢弃——残留会被同 thread 后续成功 run 的 after_model 持久化成
        # "从未被模型消费过的窗口"。
        assert not getattr(summarization, "_pending_checkpoints", None)


class TestSummarizationStashDroppedOnProtocolTermination:
    """PR#22 OCR：协议终断穿透 Summarization 时，本次调用已 stash 的摘要
    checkpoint 必须丢弃——残留会被同 thread 后续成功 run 的 after_model
    持久化成"从未被模型消费过的窗口"（确定性单测，图级场景可能走
    frozen-reuse 路径而空转）。"""

    def test_stash_dropped_when_protocol_error_passes_through(self):
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        from deerflow.agents.middlewares.summarization_middleware import DeerFlowSummarizationMiddleware

        model_mock = MagicMock()
        summary_model = model_mock.with_config.return_value
        summary_model.invoke.return_value = SimpleNamespace(text="compressed summary")
        summarization = DeerFlowSummarizationMiddleware(
            model=model_mock,
            trigger=("messages", 4),
            keep=("messages", 2),
            token_counter=len,
            skill_file_read_tool_names=None,
            preserve_recent_skill_count=0,
            preserve_recent_skill_tokens=0,
            preserve_recent_skill_tokens_per_skill=0,
        )
        msgs = [HumanMessage(content=f"m{i}", id=f"h{i}") for i in range(6)]
        runtime = _make_runtime(thread_id="stash-t", run_id="stash-r")
        request = _make_request(msgs, runtime)

        def raising_handler(_req):
            raise ToolProtocolError("terminate")

        with pytest.raises(ToolProtocolError):
            summarization.wrap_model_call(request, raising_handler)

        assert not summarization._pending_checkpoints


# ---------------------------------------------------------------------------
# 装配顺序（lead 链）
# ---------------------------------------------------------------------------


class TestChainAssembly:
    def test_protocol_guard_registered_after_terminal_response_before_clarification(self, monkeypatch, tmp_path):
        """Guard 必须注册在 TerminalResponse 之后（after_model 反序 → 先于最终答案
        判定生效），且 ClarificationMiddleware 保持最后。"""
        import re
        from pathlib import Path

        # CI/干净 worktree 没有 gitignored 的 config.yaml——从 example 构造：
        # $ENV 引用以哑值填充、models 名册（example 中全注释）注入最小条目。
        # 本测试只关心中间件顺序，不消费任何密钥。
        import yaml

        from deerflow.agents.lead_agent.agent import _build_middlewares
        from deerflow.agents.middlewares.clarification_middleware import ClarificationMiddleware
        from deerflow.config.app_config import AppConfig

        repo_root = Path(__file__).resolve().parents[2]
        example_text = (repo_root / "config.example.yaml").read_text(encoding="utf-8")
        for var in sorted(set(re.findall(r"\$\{?([A-Z_][A-Z0-9_]*)\}?", example_text))):
            monkeypatch.setenv(var, "test-dummy")
        data = yaml.safe_load(example_text)
        data["models"] = [{"name": "dummy", "model": "dummy-model", "use": "deerflow.models.patched_openai:PatchedChatOpenAI"}]
        config_copy = tmp_path / "config.yaml"
        config_copy.write_text(yaml.safe_dump(data), encoding="utf-8")

        middlewares = _build_middlewares(config={}, model_name=None, app_config=AppConfig.from_file(str(config_copy)))

        names = [type(m).__name__ for m in middlewares]
        assert "ToolCallProtocolMiddleware" in names
        assert isinstance(middlewares[-1], ClarificationMiddleware)
        terminal_idx = names.index("TerminalResponseMiddleware")
        protocol_idx = names.index("ToolCallProtocolMiddleware")
        assert protocol_idx > terminal_idx
