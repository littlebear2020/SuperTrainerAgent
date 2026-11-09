"""Ensure tool-using lead-agent turns end with a visible assistant response."""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Awaitable, Callable
from typing import Any, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelCallResult, ModelRequest, ModelResponse, hook_config
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, ToolMessage
from langgraph.runtime import Runtime

from deerflow.agents.middlewares._bounded_dict import BoundedDict

logger = logging.getLogger(__name__)

_RECOVERY_PROMPT = (
    "<system_reminder>\n"
    "Your previous response after the tool execution was empty. Review the tool results "
    "already present in the conversation and provide a concise, user-visible final response. "
    "Do not call another tool unless it is strictly necessary.\n"
    "</system_reminder>"
)

# B1（2026-09-23 批 1）：兜底文案中文化 + 「继续」续作指引。前端 B2 据本消息的
# ``deerflow_error_fallback`` 标记渲染错误卡片（含「重试」= 发送「继续」按钮）。
_FALLBACK_CONTENT = "模型在本次执行后未返回最终回复（自动重试后仍为空）。您可以发送「继续」从断点续作，或稍后重试。"

_TOOL_CALL_FINISH_REASONS = {"tool_calls", "function_call"}

# A2 defaults（OBS-02，2026-09-23 批 1）：生产值来自 config.yaml
# （empty_response_max_retries / empty_response_cooldown_base_seconds /
#   empty_response_cooldown_max_seconds / empty_response_trim_on_retry）。
DEFAULT_MAX_RETRIES = 3
DEFAULT_COOLDOWN_BASE_SECONDS = 2.0
DEFAULT_COOLDOWN_MAX_SECONDS = 8.0
DEFAULT_TRIM_ON_RETRY = True

# A3 trim knobs（推理窗口级，零 state 写）：
#   - 只裁“中段”——最近 _TRIM_KEEP_TAIL_MESSAGES 条消息保持原文（当前回合的
#     工具结果与用户诉求是模型恢复响应的最小依据）；
#   - 单条工具输出超过 _TRIM_MIN_CHARS 才动，保留头尾各一段并留机器可读标记。
_TRIM_KEEP_TAIL_MESSAGES = 8
_TRIM_MIN_CHARS = 2000
_TRIM_KEEP_HEAD_CHARS = 600
_TRIM_KEEP_TAIL_CHARS = 600


def _has_visible_content(message: AIMessage) -> bool:
    """Return whether an AI message contains user-visible text."""
    content = message.content
    if isinstance(content, str):
        return bool(content.strip())
    if isinstance(content, list):
        for block in content:
            if isinstance(block, str) and block.strip():
                return True
            if isinstance(block, dict) and block.get("type") in {"text", "output_text"}:
                text = block.get("text")
                if isinstance(text, str) and text.strip():
                    return True
    return False


def _has_tool_call_intent_or_error(message: AIMessage) -> bool:
    """Keep tool routing, malformed tool-call handling, and error fallbacks out of this guard."""
    if message.tool_calls or getattr(message, "invalid_tool_calls", None):
        return True
    additional_kwargs = message.additional_kwargs or {}
    if additional_kwargs.get("tool_calls") or additional_kwargs.get("function_call"):
        return True
    # Error-marker exemption: an AIMessage the LLM-error middleware already
    # substituted (deerflow_error_fallback, see llm_error_handling_middleware)
    # is settled output — retrying it here would loop the two guards.
    if additional_kwargs.get("deerflow_error_fallback"):
        return True
    response_metadata = message.response_metadata or {}
    return response_metadata.get("finish_reason") in _TOOL_CALL_FINISH_REASONS


def _has_real_user_message(messages: list[Any]) -> bool:
    """Return whether the thread contains at least one real (UI-visible) user message.

    A1 expansion boundary (OBS-02, 2026-09-23 plan): the guard now covers every
    empty terminal AIMessage — including a first-call empty — but ONLY for runs
    rooted in a real user message. Web and IM-channel runs both start from a
    HumanMessage without ``hide_from_ui``; pure internal invocations (scheduled
    jobs, embeddings) do not, and their terminal-success invariant must not be
    inferred from arbitrary history (#4027 scope note below).
    """
    for message in messages:
        if not isinstance(message, HumanMessage):
            continue
        if (message.additional_kwargs or {}).get("hide_from_ui"):
            continue
        return True
    return False


def _trim_middle_tool_outputs(messages: list[Any]) -> list[Any]:
    """Middle-truncate older oversized ToolMessages — inference window only.

    A3 (2026-09-23 plan): the Input>=125K window is the observed trigger of
    empty terminal responses. From the 2nd retry onward this replaces the
    middle-truncated contents in the *model call window only* — copies are
    returned, state/checkpoint messages are never mutated (the same
    inference-window-only contract as ViewImageMiddleware), so regenerate
    semantics and the summarization reuse guard are unaffected.
    """
    cutoff = len(messages) - _TRIM_KEEP_TAIL_MESSAGES
    trimmed: list[Any] = []
    trimmed_count = 0
    for index, message in enumerate(messages):
        if index < cutoff and isinstance(message, ToolMessage):
            content = message.content
            if isinstance(content, str) and len(content) > _TRIM_MIN_CHARS:
                omitted = len(content) - _TRIM_KEEP_HEAD_CHARS - _TRIM_KEEP_TAIL_CHARS
                replacement = content[:_TRIM_KEEP_HEAD_CHARS] + f"\n[tool output middle truncated: {omitted} chars omitted — empty-response retry context relief]\n" + content[-_TRIM_KEEP_TAIL_CHARS:]
                trimmed.append(message.model_copy(update={"content": replacement}))
                trimmed_count += 1
                continue
        trimmed.append(message)
    if trimmed_count:
        logger.info("Empty-response retry: middle-truncated %d tool outputs in the inference window (state untouched)", trimmed_count)
    return trimmed


class TerminalResponseMiddleware(AgentMiddleware[AgentState]):
    """Retry empty terminal responses (budgeted, cooled, trimmed), then persist a visible error fallback.

    Behavior (2026-09-23 plan A1-A4):
      * trigger: any empty terminal AIMessage in a run rooted in a real user
        message (visible content / tool-call intent / error markers stay exempt);
      * budget: ``max_retries`` per run — a retry that calls another tool does
        not refresh the budget, so empty -> retry -> tool loops stay bounded;
      * cooldown: exponential (base * 2^(n-1), capped) via ``asyncio.sleep`` on
        the async path only — production streams through ``agent.astream()``.
        The sync ``after_model`` path (tests / custom embeddings) retries
        without sleeping;
      * trim: from the 2nd retry, middle-truncate oversized mid-history tool
        outputs in the inference window (zero state writes);
      * observability: WARNING log + ``llm_empty_retry`` / ``llm_empty_fallback``
        stream events + ``empty_fallback_count`` stamped into runtime.context
        (persisted on the run row by the run worker).
    """

    def __init__(
        self,
        *,
        max_retries: int = DEFAULT_MAX_RETRIES,
        cooldown_base_seconds: float = DEFAULT_COOLDOWN_BASE_SECONDS,
        cooldown_max_seconds: float = DEFAULT_COOLDOWN_MAX_SECONDS,
        trim_on_retry: bool = DEFAULT_TRIM_ON_RETRY,
    ) -> None:
        super().__init__()
        self.max_retries = max(1, int(max_retries))
        self.cooldown_base_seconds = max(0.0, float(cooldown_base_seconds))
        self.cooldown_max_seconds = max(self.cooldown_base_seconds, float(cooldown_max_seconds))
        self.trim_on_retry = bool(trim_on_retry)
        # Injectable so tests can observe the cooldown rhythm without real waits.
        self._sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
        self._lock = threading.Lock()
        self._retry_counts: BoundedDict[tuple[str, str], int] = BoundedDict(1000)
        self._pending_prompts: BoundedDict[tuple[str, str], bool] = BoundedDict(1000)

    @classmethod
    def from_app_config(cls, app_config: Any) -> TerminalResponseMiddleware:
        """Build from AppConfig (config.yaml driven; values are hot-reloaded per run)."""
        return cls(
            max_retries=getattr(app_config, "empty_response_max_retries", DEFAULT_MAX_RETRIES),
            cooldown_base_seconds=getattr(app_config, "empty_response_cooldown_base_seconds", DEFAULT_COOLDOWN_BASE_SECONDS),
            cooldown_max_seconds=getattr(app_config, "empty_response_cooldown_max_seconds", DEFAULT_COOLDOWN_MAX_SECONDS),
            trim_on_retry=getattr(app_config, "empty_response_trim_on_retry", DEFAULT_TRIM_ON_RETRY),
        )

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

    def _clear(self, runtime: Runtime) -> None:
        key = self._key(runtime)
        with self._lock:
            self._retry_counts.pop(key, None)
            self._pending_prompts.pop(key, None)

    def _clear_other_runs(self, runtime: Runtime) -> None:
        thread_id, run_id = self._key(runtime)
        with self._lock:
            stale = [key for key in self._retry_counts if key[0] == thread_id and key[1] != run_id]
            for key in stale:
                self._retry_counts.pop(key, None)
                self._pending_prompts.pop(key, None)

    # ── A4 observability ─────────────────────────────────────────────────

    def _emit_event(self, payload: dict[str, Any]) -> None:
        try:
            from langgraph.config import get_stream_writer

            writer = get_stream_writer()
            writer(payload)
        except Exception:
            logger.debug("Failed to emit empty-response stream event", exc_info=True)

    def _stamp_empty_fallback_count(self, runtime: Runtime) -> None:
        try:
            context = getattr(runtime, "context", None)
            if isinstance(context, dict):
                context["empty_fallback_count"] = int(context.get("empty_fallback_count") or 0) + 1
        except Exception:
            logger.debug("Failed to stamp empty_fallback_count", exc_info=True)

    @staticmethod
    def _stamp_stop_reason(runtime: Runtime) -> None:
        """F6（2026-09-29）：空响应兜底落库时打 stop_reason；setdefault 语义——
        更早的终止原因（如 llm_error_fallback）不覆盖。never raises。"""
        try:
            context = getattr(runtime, "context", None)
            if isinstance(context, dict):
                context.setdefault("stop_reason", "empty_response_fallback")
        except Exception:
            logger.debug("Failed to stamp stop_reason", exc_info=True)

    def _note_retry(self, attempt: int, runtime: Runtime) -> None:
        thread_id, run_id = self._key(runtime)
        # A4 + review #8（2026-09-23）：WARNING 携带 thread/run 双 id——多并发
        # run 下生产排障需要把日志行关联回具体任务。
        logger.warning(
            "Empty terminal model response — retrying (attempt %d/%d, cooldown will apply on the async path; thread=%s run=%s)",
            attempt,
            self.max_retries,
            thread_id,
            run_id,
        )
        # A4: 文案次数上限取配置值，不写死（第四轮核查修订 4）。
        self._emit_event(
            {
                "type": "llm_empty_retry",
                "attempt": attempt,
                "max_retries": self.max_retries,
                "message": f"模型空响应，自动重试中（第 {attempt}/{self.max_retries} 次）…",
            }
        )

    def _note_fallback(self, runtime: Runtime) -> None:
        thread_id, run_id = self._key(runtime)
        logger.warning(
            "Empty terminal model response persisted after exhausting %d retries (thread=%s run=%s)",
            self.max_retries,
            thread_id,
            run_id,
        )
        self._emit_event({"type": "llm_empty_fallback", "message": _FALLBACK_CONTENT})

    # ── guard ────────────────────────────────────────────────────────────

    def _empty_terminal(self, state: AgentState) -> AIMessage | None:
        """Return the trailing AIMessage when it is an empty terminal response this guard owns."""
        messages = list(state.get("messages") or [])
        if not messages or not isinstance(messages[-1], AIMessage):
            return None
        last = messages[-1]
        if _has_visible_content(last) or _has_tool_call_intent_or_error(last):
            return None
        # A1: covers first-call empties too — the boundary is "run rooted in a
        # real user message", not "tool result in current turn". #4027 scope:
        # scheduled/internal invocations without a real HumanMessage need a
        # separate terminal-success invariant rather than being inferred from
        # arbitrary historical tools.
        if not _has_real_user_message(messages):
            return None
        return last

    def _retry(self, last: AIMessage, attempt: int) -> dict[str, Any]:
        # The next model call gets a new message id. Remove this empty
        # terminal message now so a successful recovery does not leave it
        # in checkpoint history or future model context.
        message_updates = [RemoveMessage(id=last.id)] if last.id else []
        return {"messages": message_updates, "jump_to": "model"}

    def _fallback(self, last: AIMessage, runtime: Runtime) -> dict[str, Any]:
        self._note_fallback(runtime)
        self._stamp_empty_fallback_count(runtime)
        self._stamp_stop_reason(runtime)
        additional_kwargs = dict(last.additional_kwargs or {})
        additional_kwargs.update(
            {
                "deerflow_error_fallback": True,
                "error_reason": f"Model returned an empty terminal response after {self.max_retries} retries",
            }
        )
        fallback = last.model_copy(
            update={
                "content": _FALLBACK_CONTENT,
                "additional_kwargs": additional_kwargs,
            }
        )
        return {"messages": [fallback]}

    def _apply(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        """Sync path: retry without cooldown (tests / custom embeddings only)."""
        last = self._empty_terminal(state)
        if last is None:
            return None
        key = self._key(runtime)
        with self._lock:
            # The recovery budget is per run, not per empty message. A retry
            # that calls another tool must not refresh the budget and create
            # an unbounded empty -> retry -> tool loop.
            retry_count = self._retry_counts.get(key, 0)
            if retry_count < self.max_retries:
                self._retry_counts[key] = retry_count + 1
                self._pending_prompts[key] = True
        if retry_count < self.max_retries:
            attempt = retry_count + 1
            self._note_retry(attempt, runtime)
            return self._retry(last, attempt)
        return self._fallback(last, runtime)

    async def _aapply(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        """Async path (production ``agent.astream()``): retry under exponential cooldown."""
        last = self._empty_terminal(state)
        if last is None:
            return None
        key = self._key(runtime)
        with self._lock:
            retry_count = self._retry_counts.get(key, 0)
            if retry_count < self.max_retries:
                self._retry_counts[key] = retry_count + 1
                self._pending_prompts[key] = True
        if retry_count < self.max_retries:
            attempt = retry_count + 1
            self._note_retry(attempt, runtime)
            # 2s -> 4s -> 8s: base * 2^(attempt-1), capped. asyncio.sleep keeps
            # the event loop free while the run cools down.
            delay = min(self.cooldown_base_seconds * (2 ** (attempt - 1)), self.cooldown_max_seconds)
            if delay > 0:
                await self._sleep(delay)
            return self._retry(last, attempt)
        return self._fallback(last, runtime)

    def _augment_request(self, request: ModelRequest) -> ModelRequest:
        key = self._key(request.runtime)
        with self._lock:
            pending = key in self._pending_prompts
            self._pending_prompts.pop(key, None)
            attempt = self._retry_counts.get(key, 0)
        if not pending:
            return request
        messages = list(request.messages)
        if self.trim_on_retry and attempt >= 2:
            # A3: from the 2nd retry the window itself is the suspect — trim the
            # middle of oversized older tool outputs for THIS call only. The
            # state and checkpoints keep the full originals.
            messages = _trim_middle_tool_outputs(messages)
        reminder = HumanMessage(
            content=_RECOVERY_PROMPT,
            name="terminal_response_recovery",
            additional_kwargs={"hide_from_ui": True},
        )
        return request.override(messages=[*messages, reminder])

    @override
    def before_agent(self, state: AgentState, runtime: Runtime) -> dict | None:
        self._clear_other_runs(runtime)
        # A prior invocation can bypass after_agent via Command(goto=END).
        # Reset the same run id here so resume starts with a fresh retry
        # budget; internal jump_to=model loops do not re-run before_agent.
        self._clear(runtime)
        return None

    @override
    async def abefore_agent(self, state: AgentState, runtime: Runtime) -> dict | None:
        self._clear_other_runs(runtime)
        self._clear(runtime)
        return None

    @hook_config(can_jump_to=["model"])
    @override
    def after_model(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        return self._apply(state, runtime)

    @hook_config(can_jump_to=["model"])
    @override
    async def aafter_model(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        return await self._aapply(state, runtime)

    @override
    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelCallResult:
        return handler(self._augment_request(request))

    @override
    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelCallResult:
        return await handler(self._augment_request(request))

    @override
    def after_agent(self, state: AgentState, runtime: Runtime) -> dict | None:
        self._clear(runtime)
        return None

    @override
    async def aafter_agent(self, state: AgentState, runtime: Runtime) -> dict | None:
        self._clear(runtime)
        return None
