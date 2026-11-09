"""Sandbox-death circuit breaker middleware (DEF-04 C1, 2026-09-23 批 1).

The probe loop: with no sandbox-level breaker, a dead container made the agent
retry ``bash`` forever inside the only hard ceiling left (recursion_limit=2500)
— each call returned the same transport error string and the run burned its
step budget on a dead environment.

Source tagging (this middleware's contract): ``AioSandbox.execute_command``
prefixes sandbox-death fingerprints (container gone / connection refused /
daemon unreachable) with the machine-readable ``[SANDBOX_DEAD]`` marker, and
the acquire path's readiness-timeout RuntimeError carries the same marker.
This middleware counts ONLY that marker — it never parses natural-language
error wording, so daemon message drift cannot silently disable the breaker.
Normal non-zero exits (compile errors, empty grep) never carry the marker.

When N consecutive tool results carry the marker, the middleware raises
``SandboxCircuitBreakerError`` (a ``GraphBubbleUp`` subclass). The existing
GraphBubbleUp passthrough in ``ToolErrorHandlingMiddleware`` lets it escape
to the run worker's error path: the run is marked error with a Chinese,
user-actionable message while the thread checkpoint stays intact — the same
graceful-finish trio as GraphRecursionError. Any marker-free tool result
resets the consecutive counter.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Awaitable, Callable
from typing import Any, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.errors import GraphBubbleUp
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.runtime import Runtime

from deerflow.agents.middlewares._bounded_dict import BoundedDict
from deerflow.community.aio_sandbox.aio_sandbox import SANDBOX_DEAD_MARKER

logger = logging.getLogger(__name__)

DEFAULT_FAILURE_THRESHOLD = 5


def _breaker_message(consecutive: int, threshold: int) -> str:
    return f"执行环境异常，任务已暂停，现场已保留，环境恢复后发送『继续』即可续作。\n（沙箱连续 {consecutive} 次死亡指纹命中，达到熔断阈值 {threshold}，本次运行已终止；环境恢复后从断点续作即可。）"


class SandboxCircuitBreakerError(GraphBubbleUp):
    """Tripped sandbox-death breaker — escapes to the run worker's error path.

    ``str()`` is the user-facing Chinese message; the worker's generic
    ``except Exception`` branch stamps it onto the run row and the SSE
    ``error`` event (checkpoint preserved — resume with 「继续」).
    """

    def __init__(self, consecutive: int, threshold: int) -> None:
        self.consecutive = consecutive
        self.threshold = threshold
        self.message = _breaker_message(consecutive, threshold)
        super().__init__(self.message)

    def __str__(self) -> str:
        return self.message


def _tool_message_text(result: Any) -> str:
    """Extract plain text from a ToolMessage-shaped result for marker matching."""
    if not isinstance(result, ToolMessage):
        return ""
    content = result.content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "\n".join(parts)
    return ""


def _is_dead_tool_result(result: Any) -> bool:
    """R7（评审 Q5，2026-09-24）：死亡判定改首行前缀匹配，消除误报.

    两条真实死亡路径的工具文本首行都是携带暗号的 "Error:" 行：
    ① ``AioSandbox.execute_command`` 源头打标 → ``"Error: [SANDBOX_DEAD] …"``；
    ② acquire 就绪超时 RuntimeError 被工具层兜成字符串 →
       ``"Error: Unexpected error …: [SANDBOX_DEAD] …"``。
    agent 读文件读出的暗号在内容中部/非 Error 行，天然排除——修前子串匹配
    会把「连读 5 个含暗号的日志文件」误判成环境死亡而熔断。
    """
    text = _tool_message_text(result)
    if not text:
        return False
    first_line = text.split("\n", 1)[0].strip()
    return first_line.startswith("Error:") and SANDBOX_DEAD_MARKER in first_line


class SandboxCircuitBreakerMiddleware(AgentMiddleware[AgentState]):
    """Trip after N consecutive ``[SANDBOX_DEAD]``-tagged tool results.

    Observation-only on the happy path: a first-line prefix check per tool
    result and zero bookkeeping when the marker is absent — no sandbox errors,
    no counts.
    """

    def __init__(self, *, failure_threshold: int = DEFAULT_FAILURE_THRESHOLD) -> None:
        super().__init__()
        self.failure_threshold = max(1, int(failure_threshold))
        self._lock = threading.Lock()
        # Consecutive-marker counts keyed by (thread_id, run_id); cleared per
        # run via before_agent like TerminalResponseMiddleware's budget.
        self._consecutive: BoundedDict[tuple[str, str], int] = BoundedDict(1000)

    @classmethod
    def from_app_config(cls, app_config: Any) -> SandboxCircuitBreakerMiddleware:
        return cls(failure_threshold=getattr(app_config, "sandbox_failure_circuit_breaker_threshold", DEFAULT_FAILURE_THRESHOLD))

    @staticmethod
    def _key(runtime: Runtime | None) -> tuple[str, str]:
        context = getattr(runtime, "context", None)
        if isinstance(context, dict):
            thread_id = str(context.get("thread_id") or "unknown-thread")
            run_id = str(context.get("run_id") or id(runtime))
            return thread_id, run_id
        return "unknown-thread", str(id(runtime))

    def _clear(self, runtime: Runtime | None) -> None:
        key = self._key(runtime)
        with self._lock:
            self._consecutive.pop(key, None)

    def _record(self, request: ToolCallRequest, *, dead: bool) -> None:
        """Update the consecutive counter; raise when the threshold trips."""
        key = self._key(getattr(request, "runtime", None))
        if not dead:
            # Any healthy tool result breaks the streak — the breaker targets
            # the "environment is gone and every retry dies" loop, not flaky
            # individual failures.
            with self._lock:
                self._consecutive.pop(key, None)
            return
        with self._lock:
            count = self._consecutive.get(key, 0) + 1
            self._consecutive[key] = count
        logger.warning(
            "Sandbox death fingerprint hit (%d/%d consecutive): tool=%s",
            count,
            self.failure_threshold,
            request.tool_call.get("name"),
        )
        if count >= self.failure_threshold:
            raise SandboxCircuitBreakerError(count, self.failure_threshold)

    @override
    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Any],
    ) -> ToolMessage | Any:
        result = handler(request)
        self._record(request, dead=_is_dead_tool_result(result))
        return result

    @override
    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Any]],
    ) -> ToolMessage | Any:
        result = await handler(request)
        self._record(request, dead=_is_dead_tool_result(result))
        return result

    @override
    def before_agent(self, state: AgentState, runtime: Runtime) -> dict | None:
        self._clear(runtime)
        return None

    @override
    async def abefore_agent(self, state: AgentState, runtime: Runtime) -> dict | None:
        self._clear(runtime)
        return None
