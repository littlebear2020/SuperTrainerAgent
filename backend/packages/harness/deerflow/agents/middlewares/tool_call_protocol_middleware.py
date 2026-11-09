"""Reject textual pseudo tool calls in assistant message bodies.

2026-10-14 加固批（事故 run ``0c8fd42c-e80a-49bf-81d8-c82311c35cc2``）：模型把
工具调用写成正文 ``<tool_call>{"name":"bash", ...}</tool_call>``——同时
finish_reason="stop"、无原生/无效 tool_calls、无对应 ToolMessage——旧链路把
它当作合法最终答案，run 以 status=success 结束，命令从未执行却"看起来完成了"。

守卫语义（用户已拍板，见 docs/plans/2026-10-14-loop-warning-and-tool-call-protocol-hardening-plan.md）：

  * 只拦截**完整、可解析的**伪调用：正文 trim 后整段匹配
    ``<tool_call>...</tool_call>``、内部 JSON 可解析、``name`` 非空字符串、
    ``arguments`` 为对象、且响应不带任何原生/无效 tool_calls。普通叙述、代码块
    示例、残缺 XML、``<invoke>`` 等变体不在拦截范围内——但**任何情况下都不
    解析执行正文中的命令**；
  * 首次命中：移除伪消息并 ``jump_to="model"``，在下一次模型请求经
    ``wrap_model_call`` 注入名为 ``tool_protocol_recovery`` 的恢复提示（要求
    改用原生工具调用），每个 (thread_id, run_id) 仅一次恢复；
  * 第二次同一 run 命中：``after_model`` 先提交伪消息移除（RemoveMessage +
    ``jump_to="model"``）并打 ``stop_reason="tool_protocol_error"``，随后
    ``wrap_model_call`` 在再次调用模型前抛 ``ToolProtocolError``——两段式是
    因为 after_model 在模型节点提交**之后**的独立图步执行，直接抛会把伪消息
    留进 checkpoint（实证 2026-10-15）。worker 通用异常路径使 run 落
    ``status=error``（setdefault 语义保证此处打的 stop_reason 不被
    ``run_error`` 覆盖）；
  * 恢复计数按 run 重置（新 run 重新获得一次恢复），不自动无限重试。

装配位置：lead 链 TerminalResponse **之后**、Clarification 之前——after_model
按注册反序执行，后注册者先看到模型输出，因此本守卫先于 TerminalResponse 的
最终答案判定生效（伪消息不会被判成合法终态）。
"""

from __future__ import annotations

import json
import logging
import re
import threading
from collections.abc import Awaitable, Callable
from typing import Any, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelCallResult, ModelRequest, ModelResponse, hook_config
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage
from langgraph.runtime import Runtime

from deerflow.agents.middlewares._bounded_dict import BoundedDict

logger = logging.getLogger(__name__)

_TOOL_CALL_WRAPPER_RE = re.compile(r"<tool_call>(?P<payload>.*)</tool_call>", re.DOTALL)

_RECOVERY_PROMPT = (
    "<system_reminder>\n"
    "Your previous response put a tool call in plain text inside <tool_call>...</tool_call> tags. "
    "Textual tool calls are never executed by this system. Re-issue the intended action as a "
    "native tool call (function calling). Do not wrap tool calls in tags or code fences, and do "
    "not describe them as text.\n"
    "</system_reminder>"
)


class ToolProtocolError(Exception):
    """Raised when the model re-emits a textual pseudo tool call after one recovery."""


class ToolCallProtocolMiddleware(AgentMiddleware[AgentState]):
    """Detect textual ``<tool_call>`` pseudo calls; recover once, then fail the run.

    The middleware never parses or executes the embedded command. Execution can
    only ever come from the real tools node processing a native tool call.
    """

    def __init__(self) -> None:
        super().__init__()
        self._lock = threading.Lock()
        # (thread_id, run_id) -> recoveries consumed. One recovery per run:
        # a run that keeps emitting pseudo calls after its single second
        # chance is a broken protocol loop, not something to retry forever.
        self._recoveries: BoundedDict[tuple[str, str], int] = BoundedDict(1000)
        self._pending_prompts: BoundedDict[tuple[str, str], bool] = BoundedDict(1000)
        # Second-offense terminations: after_model commits the pseudo-message
        # removal + jump, and the NEXT wrap_model_call raises before invoking
        # the model. langchain runs after_model hooks in a separate graph step
        # after the model node has already committed its AIMessage — raising
        # directly in after_model would leave the pseudo message checkpointed
        # (empirical, 2026-10-15). The two-phase shape keeps the removal
        # committed while still failing the run with ToolProtocolError.
        self._pending_terminations: BoundedDict[tuple[str, str], bool] = BoundedDict(1000)

    # ── keying / lifecycle（同 TerminalResponseMiddleware 的 per-run 记账）──

    @staticmethod
    def _key(runtime: Runtime) -> tuple[str, str]:
        context = getattr(runtime, "context", None)
        if isinstance(context, dict):
            thread_id = str(context.get("thread_id") or "unknown-thread")
            run_id = str(context.get("run_id") or context.get("run_attempt_id") or id(runtime))
            return thread_id, run_id
        # Defensive fallback for tests/custom embeddings. Production Gateway
        # runs always provide thread_id and run_id in Runtime.context.
        return "unknown-thread", str(id(runtime))

    def _clear_other_runs(self, runtime: Runtime) -> None:
        thread_id, run_id = self._key(runtime)
        with self._lock:
            stale = [key for key in self._recoveries if key[0] == thread_id and key[1] != run_id]
            for key in stale:
                self._recoveries.pop(key, None)
                self._pending_prompts.pop(key, None)
                self._pending_terminations.pop(key, None)

    @override
    def before_agent(self, state: AgentState, runtime: Runtime) -> dict | None:
        self._clear_other_runs(runtime)
        return None

    @override
    async def abefore_agent(self, state: AgentState, runtime: Runtime) -> dict | None:
        self._clear_other_runs(runtime)
        return None

    # ── detection ─────────────────────────────────────────────────────────

    @staticmethod
    def _is_pseudo_tool_call(message: Any) -> bool:
        """Whether *message* is a complete, parseable, native-call-free pseudo call.

        Conditions (all must hold):
          * an AIMessage whose content is a plain string;
          * the stripped body full-matches ``<tool_call>...</tool_call>``;
          * the inner JSON parses to an object with a non-empty string
            ``name`` and a dict ``arguments``;
          * the response carries no native ``tool_calls`` and no
            ``invalid_tool_calls``.

        Anything looser (code fences, surrounding prose, broken XML,
        ``<invoke>`` variants, list-content blocks) stays out of scope for
        this first version — and is of course never executed either.
        """
        if not isinstance(message, AIMessage):
            return False
        if getattr(message, "tool_calls", None) or getattr(message, "invalid_tool_calls", None):
            return False
        content = message.content
        if not isinstance(content, str):
            return False
        match = _TOOL_CALL_WRAPPER_RE.fullmatch(content.strip())
        if match is None:
            return False
        try:
            payload = json.loads(match.group("payload"))
        except (TypeError, ValueError, json.JSONDecodeError):
            return False
        if not isinstance(payload, dict):
            return False
        name = payload.get("name")
        if not isinstance(name, str) or not name:
            return False
        return isinstance(payload.get("arguments"), dict)

    # ── verdict ───────────────────────────────────────────────────────────

    def _apply(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        messages = state.get("messages") or []
        if not messages:
            return None
        last = messages[-1]
        if not self._is_pseudo_tool_call(last):
            return None

        key = self._key(runtime)
        # 评审修复（PR#21 #2）：读-判-写合并为单次加锁——拆成两段会开一个
        # check-then-act 窗口，与 _clear_other_runs 交错时可能重复注入恢复
        # 提示、或两次命中都读到 recoveries=0 而均走恢复路径。
        with self._lock:
            recoveries = self._recoveries.get(key, 0)
            first_offense = recoveries < 1
            if first_offense:
                self._recoveries[key] = recoveries + 1
                self._pending_prompts[key] = True

        if first_offense:
            logger.warning(
                "Textual pseudo tool call rejected — removing message and asking the model to use a native tool call (recovery 1/1)",
                extra={"thread_id": key[0], "run_id": key[1]},
            )
            # The pseudo message must not survive into checkpoint or future
            # model context; the retry gets a fresh model call. No ToolMessage
            # is fabricated — execution only ever comes from the tools node.
            message_updates = [RemoveMessage(id=last.id)] if last.id else []
            return {"messages": message_updates, "jump_to": "model"}

        # Second offense in the same run: commit the pseudo-message removal
        # (raise-in-after_model would leave it checkpointed — see
        # ``_pending_terminations``), stamp the stop reason (worker's
        # setdefault("run_error") will not overwrite it), and terminate the
        # run from the next ``wrap_model_call`` with ToolProtocolError —
        # the model is never invoked again.
        with self._lock:
            self._pending_terminations[key] = True
        self._stamp_stop_reason(runtime, "tool_protocol_error")
        logger.error(
            "Textual pseudo tool call rejected twice in one run — failing the run with tool_protocol_error",
            extra={"thread_id": key[0], "run_id": key[1]},
        )
        message_updates = [RemoveMessage(id=last.id)] if last.id else []
        return {"messages": message_updates, "jump_to": "model"}

    @staticmethod
    def _stamp_stop_reason(runtime: Runtime, value: str) -> None:
        """Stamp *value* into runtime.context["stop_reason"]; never raises."""
        try:
            ctx = getattr(runtime, "context", None)
            if isinstance(ctx, dict):
                ctx.setdefault("stop_reason", value)
        except Exception:
            logger.debug("Failed to stamp stop_reason", exc_info=True)

    # 评审三轮（2026-10-15 对抗工作流实证）：jump_to 是 EphemeralValue，after_model
    # 反序链中排在其后的每个常驻钩子节点都耗一步；不声明 can_jump_to 时跳转值在
    # 到达路由边前被清除——生产链首次违规会静默空成功（TerminalResponse/Todo 同款
    # 声明先例）。
    @hook_config(can_jump_to=["model"])
    @override
    def after_model(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        return self._apply(state, runtime)

    @hook_config(can_jump_to=["model"])
    @override
    async def aafter_model(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        return self._apply(state, runtime)

    @override
    def after_agent(self, state: AgentState, runtime: Runtime) -> dict | None:
        # 评审修复（PR#21 #3）：终止/恢复标志若在本 run 内未被消费（如链上
        # 其他中间件短路了模型调用、或 run 在到达模型前被中断），随 run 结束
        # 强制失效——防悬挂标志被误读，也兜住 before_agent 清理之外的路径。
        key = self._key(runtime)
        with self._lock:
            self._pending_terminations.pop(key, None)
            self._pending_prompts.pop(key, None)
        return None

    @override
    async def aafter_agent(self, state: AgentState, runtime: Runtime) -> dict | None:
        key = self._key(runtime)
        with self._lock:
            self._pending_terminations.pop(key, None)
            self._pending_prompts.pop(key, None)
        return None

    # ── request-only recovery prompt（同 TerminalResponse 的注入形态）─────

    def _take_termination_or_prompt(self, request: ModelRequest) -> tuple[bool, bool]:
        """Atomically pop (termination, prompt) flags for this run's next model call."""
        key = self._key(request.runtime)
        with self._lock:
            terminate = self._pending_terminations.pop(key, False)
            pending = key in self._pending_prompts
            self._pending_prompts.pop(key, None)
        return terminate, pending

    def _inject_prompt(self, request: ModelRequest, pending: bool) -> ModelRequest:
        if not pending:
            return request
        reminder = HumanMessage(
            content=_RECOVERY_PROMPT,
            name="tool_protocol_recovery",
            additional_kwargs={"hide_from_ui": True},
        )
        return request.override(messages=[*request.messages, reminder])

    def _restore_prompt(self, runtime: Runtime, pending: bool) -> None:
        """恢复提示在模型调用失败后回队（#5433 同型，2026-10-15 评审二轮）——
        外层 LLMErrorHandling 重试会再次执行本 wrap，重试请求仍须携带提示；
        丢失提示会让首次违规被误判为"已恢复"，下一次伪调用直接终止。"""
        if not pending:
            return
        key = self._key(runtime)
        with self._lock:
            self._pending_prompts[key] = True

    @override
    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelCallResult:
        terminate, pending = self._take_termination_or_prompt(request)
        if terminate:
            # Second-offense path: the pseudo message was already removed by
            # the after_model update that queued this termination. Fail the
            # run before the model is invoked again — no retry, no fallback
            # answer, no execution of the textual command. LLMErrorHandling
            # passes ToolProtocolError through (2026-10-15 评审二轮修复)，
            # worker 落 status=error。
            self._stamp_stop_reason(request.runtime, "tool_protocol_error")
            raise ToolProtocolError("模型在恢复提示后仍以正文 <tool_call> 伪调用代替原生工具调用，本轮以 tool_protocol_error 终止；可换模型后继续、开新会话续作，或同会话手动再试一次。")
        try:
            return handler(self._inject_prompt(request, pending))
        # CancelledError（BaseException）不入此分支：取消即丢、与上游 #5433 对齐——
        # run 已死，后续新 run 由 before_agent 重置该 run 的全部一次性状态。
        except Exception:
            self._restore_prompt(request.runtime, pending)
            raise

    @override
    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelCallResult:
        terminate, pending = self._take_termination_or_prompt(request)
        if terminate:
            self._stamp_stop_reason(request.runtime, "tool_protocol_error")
            raise ToolProtocolError("模型在恢复提示后仍以正文 <tool_call> 伪调用代替原生工具调用，本轮以 tool_protocol_error 终止；可换模型后继续、开新会话续作，或同会话手动再试一次。")
        try:
            return await handler(self._inject_prompt(request, pending))
        # CancelledError（BaseException）不入此分支：取消即丢、与上游 #5433 对齐——
        # run 已死，后续新 run 由 before_agent 重置该 run 的全部一次性状态。
        except Exception:
            self._restore_prompt(request.runtime, pending)
            raise
