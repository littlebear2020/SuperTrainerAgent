"""F6（2026-09-29 长任务中断修复计划）：stop_reason 三条终止路径补全。

此前 stop_reason 仅来自 safety/model-length 中间件打点；循环硬停、LLM 兜底、
空响应兜底三条终止路径均不写该键，runs 表区分不了"正常收尾"与"被迫收尾"。
本文件钉死四处打点：
  * loop_detection 硬停（Layer 1 hash + Layer 2 freq）→ "loop_hard_limit"
  * llm_error_handling 兜底（重试耗尽 + 熔断快断）→ "llm_error_fallback"
  * terminal_response 兜底（空响应预算耗尽）→ "empty_response_fallback"
  * worker except 分支（GraphRecursionError → "recursion_limit"；generic → "run_error"）
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from deerflow.agents.middlewares.loop_detection_middleware import LoopDetectionMiddleware
from deerflow.agents.middlewares.terminal_response_middleware import TerminalResponseMiddleware


def _make_runtime(thread_id="t", context=None):
    from unittest.mock import MagicMock

    runtime = MagicMock()
    runtime.context = context if context is not None else {"thread_id": thread_id}
    return runtime


# ---------------------------------------------------------------------------
# loop_detection → "loop_hard_limit"
# ---------------------------------------------------------------------------


class TestLoopHardStopStampsStopReason:
    def _state(self, tool_calls):
        return {"messages": [AIMessage(content="", tool_calls=tool_calls)]}

    def test_hash_hard_stop_stamps(self):
        mw = LoopDetectionMiddleware(warn_threshold=2, hard_limit=4)
        runtime = _make_runtime()
        call = [{"name": "bash", "id": "c1", "args": {"command": "ls"}}]
        for _ in range(3):
            mw._apply(self._state(call), runtime)
        result = mw._apply(self._state(call), runtime)
        assert result is not None
        assert runtime.context["stop_reason"] == "loop_hard_limit"

    def test_freq_hard_stop_stamps(self):
        mw = LoopDetectionMiddleware(tool_freq_warn=3, tool_freq_hard_limit=5)
        runtime = _make_runtime()
        for i in range(5):
            mw._apply(self._state([{"name": "read_file", "id": f"c{i}", "args": {"path": f"/f{i}.py"}}]), runtime)
        assert runtime.context["stop_reason"] == "loop_hard_limit"

    def test_exempted_retry_does_not_stamp(self):
        """F4 豁免放行不算终止，不得打 stop_reason。"""
        mw = LoopDetectionMiddleware(warn_threshold=2, hard_limit=4)
        runtime = _make_runtime()
        call = [{"name": "bash", "id": "c", "args": {"command": "render"}}]

        def _history_state(n_prior):
            msgs = []
            for i in range(n_prior):
                msgs.append(AIMessage(content="", tool_calls=[dict(call[0], id=f"c{i}")]))
                msgs.append(ToolMessage(content="err", tool_call_id=f"c{i}", name="bash", status="error"))
            msgs.append(AIMessage(content="", tool_calls=[dict(call[0], id="c_now")]))
            return {"messages": msgs}

        for attempt in range(1, 5):
            mw._apply(_history_state(attempt - 1), runtime)
        assert "stop_reason" not in runtime.context

    def test_normal_rounds_do_not_stamp(self):
        mw = LoopDetectionMiddleware()
        runtime = _make_runtime()
        mw._apply(self._state([{"name": "bash", "id": "c1", "args": {"command": "ls"}}]), runtime)
        assert "stop_reason" not in runtime.context


# ---------------------------------------------------------------------------
# llm_error_handling → "llm_error_fallback"
# ---------------------------------------------------------------------------


def _llm_middleware(**overrides):
    from deerflow.agents.middlewares.llm_error_handling_middleware import LLMErrorHandlingMiddleware

    app_config = SimpleNamespace(
        circuit_breaker=SimpleNamespace(failure_threshold=overrides.pop("failure_threshold", 5), recovery_timeout_sec=60),
    )
    mw = LLMErrorHandlingMiddleware(app_config=app_config)
    for key, value in overrides.items():
        setattr(mw, key, value)
    return mw


def _llm_request(context=None):
    runtime = SimpleNamespace(context=context if context is not None else {})
    return SimpleNamespace(model=SimpleNamespace(model_name="fake"), runtime=runtime)


class _NonRetriableError(Exception):
    pass


class TestLLMErrorFallbackStampsStopReason:
    def test_exhausted_retries_stamp_sync(self, monkeypatch):
        monkeypatch.setattr("time.sleep", lambda _s: None)
        mw = _llm_middleware(retry_max_attempts=1, retry_base_delay_ms=1)

        def handler(_request):
            raise _NonRetriableError("boom")

        request = _llm_request()
        result = mw.wrap_model_call(request, handler)
        assert result.additional_kwargs.get("deerflow_error_fallback")
        assert request.runtime.context.get("stop_reason") == "llm_error_fallback"

    def test_exhausted_retries_stamp_async(self):
        mw = _llm_middleware(retry_max_attempts=1, retry_base_delay_ms=1)

        async def run():
            async def handler(_request):
                raise _NonRetriableError("boom")

            request = _llm_request()
            result = await mw.awrap_model_call(request, handler)
            return request, result

        request, result = asyncio.run(run())
        assert result.additional_kwargs.get("deerflow_error_fallback")
        assert request.runtime.context.get("stop_reason") == "llm_error_fallback"

    def test_circuit_open_stamp_sync(self):
        mw = _llm_middleware()
        # 强制熔断开启：直接走 _record_failure 触发阈值
        mw._circuit_failure_count = 5
        mw._record_failure()
        assert mw._check_circuit() is True

        request = _llm_request()
        result = mw.wrap_model_call(request, lambda r: AIMessage(content="never"))
        assert result.additional_kwargs.get("deerflow_error_fallback")
        assert request.runtime.context.get("stop_reason") == "llm_error_fallback"

    def test_prestamped_stop_reason_not_overwritten(self):
        """setdefault 语义：更早的终止原因（如 loop_hard_limit）不得被覆盖。"""
        mw = _llm_middleware(retry_max_attempts=1)
        request = _llm_request(context={"stop_reason": "loop_hard_limit"})
        result = mw.wrap_model_call(request, lambda r: (_ for _ in ()).throw(_NonRetriableError("boom")))
        assert result.additional_kwargs.get("deerflow_error_fallback")
        assert request.runtime.context["stop_reason"] == "loop_hard_limit"

    def test_successful_call_does_not_stamp(self):
        mw = _llm_middleware()
        request = _llm_request()
        mw.wrap_model_call(request, lambda r: AIMessage(content="ok"))
        assert "stop_reason" not in request.runtime.context


# ---------------------------------------------------------------------------
# terminal_response → "empty_response_fallback"
# ---------------------------------------------------------------------------


def _post_tool_state():
    return {
        "messages": [
            HumanMessage(content="hi"),
            ToolMessage(content="result", tool_call_id="c0"),
            AIMessage(content=""),
        ]
    }


class TestEmptyResponseFallbackStampsStopReason:
    def test_fallback_stamps_stop_reason(self):
        mw = TerminalResponseMiddleware(max_retries=1)
        state = _post_tool_state()
        runtime = _make_runtime()
        mw.after_model(state, runtime)  # retry 1
        mw.after_model(state, runtime)  # exhausted → fallback
        assert runtime.context["empty_fallback_count"] == 1
        assert runtime.context["stop_reason"] == "empty_response_fallback"

    def test_retry_only_does_not_stamp(self):
        mw = TerminalResponseMiddleware(max_retries=3)
        state = _post_tool_state()
        runtime = _make_runtime()
        mw.after_model(state, runtime)  # retry 1 only
        assert "stop_reason" not in runtime.context


# ---------------------------------------------------------------------------
# tool_call_protocol → "tool_protocol_error"（2026-10-14 加固批 Task D）
# ---------------------------------------------------------------------------


_PSEUDO_BODY = '<tool_call>{"name":"bash","arguments":{"command":"pwd"}}</tool_call>'


class TestToolProtocolErrorStampsStopReason:
    def test_second_offense_stamps_before_raising(self):
        """第二次正文伪调用：after_model 先打 tool_protocol_error 并提交移除，
        下一次 wrap_model_call 抛 ToolProtocolError——worker 的
        setdefault("run_error") 不覆盖更早原因。"""
        from deerflow.agents.middlewares.tool_call_protocol_middleware import ToolCallProtocolMiddleware, ToolProtocolError

        mw = ToolCallProtocolMiddleware()
        runtime = _make_runtime(context={"thread_id": "t", "run_id": "r"})

        state = {"messages": [AIMessage(content=_PSEUDO_BODY, id="p1")]}
        mw.after_model(state, runtime)
        mw.after_model({"messages": [AIMessage(content=_PSEUDO_BODY, id="p2")]}, runtime)

        request = SimpleNamespace(runtime=runtime)
        with pytest.raises(ToolProtocolError):
            mw.wrap_model_call(request, lambda req: AIMessage(content="never"))

        assert runtime.context["stop_reason"] == "tool_protocol_error"

    def test_first_offense_does_not_stamp(self):
        from deerflow.agents.middlewares.tool_call_protocol_middleware import ToolCallProtocolMiddleware

        mw = ToolCallProtocolMiddleware()
        runtime = _make_runtime(context={"thread_id": "t", "run_id": "r"})

        state = {"messages": [AIMessage(content=_PSEUDO_BODY, id="p1")]}
        mw.after_model(state, runtime)
        assert "stop_reason" not in runtime.context


# ---------------------------------------------------------------------------
# worker except 分支 → "recursion_limit" / "run_error"
# ---------------------------------------------------------------------------


class _Bridge:
    def __init__(self):
        self.published: list[tuple[str, str, object]] = []

    async def publish(self, run_id, event, data):
        self.published.append((run_id, event, data))

    async def publish_end(self, run_id):
        self.published.append((run_id, "end", None))

    async def cleanup(self, run_id, *, delay=60):
        pass


class _EventStore:
    def __init__(self):
        self.batches: list[list[dict]] = []

    async def put_batch(self, batch):
        self.batches.append(list(batch))


def _ctx_with_journal(app_config=None):
    return SimpleNamespace(
        checkpointer=None,
        store=None,
        event_store=_EventStore(),
        run_events_config=None,
        thread_store=None,
        app_config=app_config,
    )


def _failing_agent_factory(exc: BaseException):
    class _Agent:
        checkpointer = None
        store = None

        async def astream(self, *a, **kw):
            raise exc
            yield  # pragma: no cover

    def _factory(config=None):
        return _Agent()

    return _factory


_APP_CONFIG = SimpleNamespace(
    sandbox=None,
    disk_min_free_bytes_block=0,
    disk_warn_free_ratio=0.0,
    token_budget_warn_tokens={"default": 0},
)


@pytest.fixture()
def _fast_run_cleanup(monkeypatch):
    from deerflow.runtime.runs.manager import RunManager

    def fake_cleanup(self, run_id, *, delay=300):
        return asyncio.sleep(0)

    monkeypatch.setattr(RunManager, "cleanup", fake_cleanup)


class TestWorkerStopReason:
    @pytest.mark.asyncio()
    async def test_recursion_error_stamps_recursion_limit(self, _fast_run_cleanup):
        from langgraph.errors import GraphRecursionError

        from deerflow.runtime.runs.manager import RunManager
        from deerflow.runtime.runs.schemas import RunStatus
        from deerflow.runtime.runs.worker import run_agent

        mgr = RunManager(store=None)
        record = await mgr.create("t-stop-reason")

        await run_agent(
            _Bridge(),
            mgr,
            record,
            ctx=_ctx_with_journal(app_config=_APP_CONFIG),
            agent_factory=_failing_agent_factory(GraphRecursionError("Recursion limit of 2500 reached")),
            graph_input={},
            config={"recursion_limit": 2500},
        )

        assert record.status == RunStatus.error
        assert record.stop_reason == "recursion_limit"

    @pytest.mark.asyncio()
    async def test_generic_error_stamps_run_error(self, _fast_run_cleanup):
        from deerflow.runtime.runs.manager import RunManager
        from deerflow.runtime.runs.schemas import RunStatus
        from deerflow.runtime.runs.worker import run_agent

        mgr = RunManager(store=None)
        record = await mgr.create("t-stop-reason-2")

        await run_agent(
            _Bridge(),
            mgr,
            record,
            ctx=_ctx_with_journal(app_config=_APP_CONFIG),
            agent_factory=_failing_agent_factory(RuntimeError("provider exploded")),
            graph_input={},
            config={},
        )

        assert record.status == RunStatus.error
        assert record.stop_reason == "run_error"

    @pytest.mark.asyncio()
    async def test_success_run_has_no_stop_reason(self, _fast_run_cleanup):
        from deerflow.runtime.runs.manager import RunManager
        from deerflow.runtime.runs.schemas import RunStatus
        from deerflow.runtime.runs.worker import run_agent

        def _ok_factory(config=None, app_config=None):
            class _Agent:
                checkpointer = None
                store = None

                async def astream(self, *a, **kw):
                    yield {"messages": []}

            return _Agent()

        mgr = RunManager(store=None)
        record = await mgr.create("t-stop-reason-3")

        await run_agent(
            _Bridge(),
            mgr,
            record,
            ctx=_ctx_with_journal(app_config=_APP_CONFIG),
            agent_factory=_ok_factory,
            graph_input={},
            config={},
        )

        assert record.status == RunStatus.success
        assert record.stop_reason is None

    @pytest.mark.asyncio()
    async def test_protocol_error_enters_worker_error_path_with_its_own_stop_reason(self, _fast_run_cleanup):
        """真实图 + ToolCallProtocolMiddleware：第二次正文伪调用抛 ToolProtocolError
        → worker 通用 except 落 status=error，且中间件先打的
        tool_protocol_error 不被 setdefault("run_error") 覆盖。"""
        from langchain.agents import create_agent
        from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
        from langchain_core.messages import AIMessage
        from langchain_core.tools import tool

        from deerflow.agents.middlewares.llm_error_handling_middleware import LLMErrorHandlingMiddleware
        from deerflow.agents.middlewares.tool_call_protocol_middleware import ToolCallProtocolMiddleware
        from deerflow.runtime.runs.manager import RunManager
        from deerflow.runtime.runs.schemas import RunStatus
        from deerflow.runtime.runs.worker import run_agent

        @tool
        def bash(command: str) -> str:
            """Run a fake shell command."""
            return f"ran: {command}"

        class _Cap(FakeMessagesListChatModel):
            def bind_tools(self, tools, *, tool_choice=None, **kwargs):
                return self

        def _factory(config=None, app_config=None):
            model = _Cap(
                responses=[
                    AIMessage(content=_PSEUDO_BODY, id="pseudo_1"),
                    AIMessage(content=_PSEUDO_BODY, id="pseudo_2"),
                ],
            )
            # 2026-10-15 评审二轮：worker 级测试必须用真实 lead 链形态——
            # 外层 LLMErrorHandling 兜底会吞协议终断异常（转 fallback → success），
            # 只挂协议中间件的图测不出该缺陷。
            # 评审三轮：升级为生产注册序链形态（LoopDetection/TerminalResponse 在
            # 协议守卫 after_model 反序方向上排在前面）——两件套最小链掩蔽过
            # jump_to EphemeralValue 失效与中间层吞错两类拓扑缺陷。
            from deerflow.agents.middlewares.loop_detection_middleware import LoopDetectionMiddleware
            from deerflow.agents.middlewares.terminal_response_middleware import TerminalResponseMiddleware

            llm_guard = LLMErrorHandlingMiddleware(app_config=SimpleNamespace(circuit_breaker=SimpleNamespace(failure_threshold=5, recovery_timeout_sec=60)))
            graph = create_agent(
                model=model,
                tools=[bash],
                middleware=[
                    llm_guard,
                    LoopDetectionMiddleware(warn_threshold=99, hard_limit=99),
                    TerminalResponseMiddleware(max_retries=1),
                    ToolCallProtocolMiddleware(),
                ],
            )

            class _GraphAgent:
                checkpointer = None
                store = None

                async def astream(self, *a, **kw):
                    async for chunk in graph.astream(*a, **kw):
                        yield chunk

            return _GraphAgent()

        mgr = RunManager(store=None)
        record = await mgr.create("t-stop-reason-protocol")

        await run_agent(
            _Bridge(),
            mgr,
            record,
            ctx=_ctx_with_journal(app_config=_APP_CONFIG),
            agent_factory=_factory,
            graph_input={"messages": [("user", "run pwd")]},
            config={},
        )

        assert record.status == RunStatus.error
        assert record.stop_reason == "tool_protocol_error"
