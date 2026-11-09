"""Tool error handling middleware and shared runtime middleware builders."""

import logging
from collections.abc import Awaitable, Callable
from typing import override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.errors import GraphBubbleUp
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from deerflow.config.app_config import AppConfig

logger = logging.getLogger(__name__)

_MISSING_TOOL_CALL_ID = "missing_tool_call_id"


class ToolErrorHandlingMiddleware(AgentMiddleware[AgentState]):
    """Convert tool exceptions into error ToolMessages so the run can continue."""

    def _build_error_message(self, request: ToolCallRequest, exc: Exception) -> ToolMessage:
        tool_name = str(request.tool_call.get("name") or "unknown_tool")
        tool_call_id = str(request.tool_call.get("id") or _MISSING_TOOL_CALL_ID)
        detail = str(exc).strip() or exc.__class__.__name__
        if len(detail) > 500:
            detail = detail[:497] + "..."

        content = f"Error: Tool '{tool_name}' failed with {exc.__class__.__name__}: {detail}. Continue with available context, or choose an alternative tool."
        return ToolMessage(
            content=content,
            tool_call_id=tool_call_id,
            name=tool_name,
            status="error",
        )

    @override
    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        try:
            return handler(request)
        except GraphBubbleUp:
            # Preserve LangGraph control-flow signals (interrupt/pause/resume).
            raise
        except Exception as exc:
            logger.exception("Tool execution failed (sync): name=%s id=%s", request.tool_call.get("name"), request.tool_call.get("id"))
            return self._build_error_message(request, exc)

    @override
    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        try:
            return await handler(request)
        except GraphBubbleUp:
            # Preserve LangGraph control-flow signals (interrupt/pause/resume).
            raise
        except Exception as exc:
            logger.exception("Tool execution failed (async): name=%s id=%s", request.tool_call.get("name"), request.tool_call.get("id"))
            return self._build_error_message(request, exc)


def _build_runtime_middlewares(
    *,
    app_config: AppConfig,
    include_uploads: bool,
    include_dangling_tool_call_patch: bool,
    lazy_init: bool = True,
    release_sandbox_on_exit: bool = True,
) -> list[AgentMiddleware]:
    """Build shared base middlewares for agent execution."""
    from deerflow.agents.middlewares.llm_error_handling_middleware import LLMErrorHandlingMiddleware
    from deerflow.agents.middlewares.thread_data_middleware import ThreadDataMiddleware
    from deerflow.sandbox.middleware import SandboxMiddleware

    middlewares: list[AgentMiddleware] = [
        ThreadDataMiddleware(lazy_init=lazy_init),
        # P3-1 (2026-09-29): subagent chains inherit the parent run's sandbox —
        # their SandboxMiddleware must not release it on exit.
        SandboxMiddleware(lazy_init=lazy_init, release_on_exit=release_sandbox_on_exit),
    ]

    if include_uploads:
        from deerflow.agents.middlewares.uploads_middleware import UploadsMiddleware
        from deerflow.agents.middlewares.workspace_inventory_middleware import WorkspaceInventoryMiddleware

        middlewares.insert(1, UploadsMiddleware())
        middlewares.insert(2, WorkspaceInventoryMiddleware())

    if include_dangling_tool_call_patch:
        from deerflow.agents.middlewares.dangling_tool_call_middleware import DanglingToolCallMiddleware

        middlewares.append(DanglingToolCallMiddleware())

    middlewares.append(LLMErrorHandlingMiddleware(app_config=app_config))

    # Guardrail middleware (if configured)
    guardrails_config = app_config.guardrails
    if guardrails_config.enabled and guardrails_config.provider:
        import inspect

        from deerflow.guardrails.middleware import GuardrailMiddleware
        from deerflow.reflection import resolve_variable

        provider_cls = resolve_variable(guardrails_config.provider.use)
        provider_kwargs = dict(guardrails_config.provider.config) if guardrails_config.provider.config else {}
        # Pass framework hint if the provider accepts it (e.g. for config discovery).
        # Built-in providers like AllowlistProvider don't need it, so only inject
        # when the constructor accepts 'framework' or '**kwargs'.
        if "framework" not in provider_kwargs:
            try:
                sig = inspect.signature(provider_cls.__init__)
                if "framework" in sig.parameters or any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()):
                    provider_kwargs["framework"] = "deerflow"
            except (ValueError, TypeError):
                pass
        provider = provider_cls(**provider_kwargs)
        middlewares.append(GuardrailMiddleware(provider, fail_closed=guardrails_config.fail_closed, passport=guardrails_config.passport))

    from deerflow.agents.middlewares.sandbox_audit_middleware import SandboxAuditMiddleware
    from deerflow.agents.middlewares.tool_result_sanitization_middleware import ToolResultSanitizationMiddleware

    middlewares.append(SandboxAuditMiddleware())
    # Ordering: for wrap_tool_call the *earlier* entry is the outer wrapper, so
    # this sits outside ToolErrorHandlingMiddleware and therefore observes the
    # settled result — a real tool result, or the error ToolMessage that error
    # handling substituted for a raised exception. Both are worth neutralizing:
    # a provider error string can echo fetched remote content back verbatim.
    middlewares.append(ToolResultSanitizationMiddleware())
    middlewares.append(ToolErrorHandlingMiddleware())
    # C1（DEF-04，2026-09-23 批 1）：沙箱死亡熔断。追加在 ToolErrorHandlingMiddleware
    # 之后 = wrap_tool_call 的内层：看得见错误兜底后的 ToolMessage，且其
    # GraphBubbleUp 上抛能借道外层的透传通道（:47-49）直达 worker error 路径。
    # 既有注册顺序不变，仅在链尾新增一员。
    from deerflow.agents.middlewares.sandbox_circuit_breaker_middleware import SandboxCircuitBreakerMiddleware

    middlewares.append(SandboxCircuitBreakerMiddleware.from_app_config(app_config))
    return middlewares


def build_lead_runtime_middlewares(*, app_config: AppConfig, lazy_init: bool = True) -> list[AgentMiddleware]:
    """Middlewares shared by lead agent runtime before lead-only middlewares."""
    return _build_runtime_middlewares(
        app_config=app_config,
        include_uploads=True,
        include_dangling_tool_call_patch=True,
        lazy_init=lazy_init,
    )


def build_subagent_runtime_middlewares(*, app_config: AppConfig, agent_name: str | None = None, lazy_init: bool = True) -> list[AgentMiddleware]:
    """Middlewares shared by subagent runtime before subagent-only middlewares."""
    middlewares = _build_runtime_middlewares(
        app_config=app_config,
        include_uploads=False,
        include_dangling_tool_call_patch=True,
        lazy_init=lazy_init,
        # P3-1 (2026-09-29): the subagent inherits the parent's sandbox_state;
        # releasing on exit tore down the shared container under the parent.
        release_sandbox_on_exit=False,
    )
    from deerflow.agents.middlewares.memory_middleware import MemoryMiddleware

    middlewares.append(MemoryMiddleware(agent_name=agent_name))

    # C-08 (2026-08-13): subagent chains previously had no loop detection —
    # a subagent stuck in a same-tool loop burned the whole delegation budget
    # silently. TerminalResponse ensures a tool-using subagent turn ends with
    # a visible response instead of an empty "success". (2026-09-23 批 1)
    # knobs come from config.yaml like the lead chain.
    from deerflow.agents.middlewares.loop_detection_middleware import build_loop_detection_from_config
    from deerflow.agents.middlewares.terminal_response_middleware import TerminalResponseMiddleware

    loop_mw = build_loop_detection_from_config()
    if loop_mw is not None:
        middlewares.append(loop_mw)
    middlewares.append(TerminalResponseMiddleware.from_app_config(app_config))
    return middlewares
