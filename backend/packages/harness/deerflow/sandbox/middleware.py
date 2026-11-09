import logging
from typing import NotRequired, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langgraph.runtime import Runtime

from deerflow.agents.thread_state import SandboxState, ThreadDataState
from deerflow.sandbox import get_sandbox_provider

logger = logging.getLogger(__name__)


class SandboxMiddlewareState(AgentState):
    """Compatible with the `ThreadState` schema."""

    sandbox: NotRequired[SandboxState | None]
    thread_data: NotRequired[ThreadDataState | None]


class SandboxMiddleware(AgentMiddleware[SandboxMiddlewareState]):
    """Create a sandbox environment and assign it to an agent.

    Lifecycle Management:
    - With lazy_init=True (default): Sandbox is acquired on first tool call
    - With lazy_init=False: Sandbox is acquired on first agent invocation (before_agent)
    - Sandbox is reused across multiple turns within the same thread
    - With release_on_exit=True (default) the sandbox is released in
      after_agent (P3-1, 2026-09-29: this class used to claim the sandbox was
      "NOT released after each agent call", which was never true — after_agent
      released unconditionally)
    - With release_on_exit=False the inherited sandbox is left alone: subagent
      chains share the parent run's sandbox (sandbox_state is passed through
      task_tool), so releasing here would tear down the shared container — and
      its warm-pool bookkeeping — underneath the still-running parent. The
      parent's own after_agent does the final release.
    - Cleanup at application shutdown happens via SandboxProvider.shutdown()
    """

    state_schema = SandboxMiddlewareState

    def __init__(self, lazy_init: bool = True, release_on_exit: bool = True):
        """Initialize sandbox middleware.

        Args:
            lazy_init: If True, defer sandbox acquisition until first tool call.
                      If False, acquire sandbox eagerly in before_agent().
                      Default is True for optimal performance.
            release_on_exit: If True (default), release the sandbox in
                      after_agent. Subagent chains set False to avoid releasing
                      the parent run's sandbox they inherited.
        """
        super().__init__()
        self._lazy_init = lazy_init
        self._release_on_exit = release_on_exit

    def _acquire_sandbox(self, thread_id: str) -> str:
        provider = get_sandbox_provider()
        sandbox_id = provider.acquire(thread_id)
        logger.info(f"Acquiring sandbox {sandbox_id}")
        return sandbox_id

    @override
    def before_agent(self, state: SandboxMiddlewareState, runtime: Runtime) -> dict | None:
        # Skip acquisition if lazy_init is enabled
        if self._lazy_init:
            return super().before_agent(state, runtime)

        # Eager initialization (original behavior)
        if "sandbox" not in state or state["sandbox"] is None:
            thread_id = (runtime.context or {}).get("thread_id")
            if thread_id is None:
                return super().before_agent(state, runtime)
            sandbox_id = self._acquire_sandbox(thread_id)
            logger.info(f"Assigned sandbox {sandbox_id} to thread {thread_id}")
            return {"sandbox": {"sandbox_id": sandbox_id}}
        return super().before_agent(state, runtime)

    @override
    def after_agent(self, state: SandboxMiddlewareState, runtime: Runtime) -> dict | None:
        # P3-1 (2026-09-29): subagent chains inherit the parent run's sandbox —
        # skip the release and let the parent's own after_agent close it out.
        if not self._release_on_exit:
            sandbox = state.get("sandbox")
            inherited = sandbox["sandbox_id"] if sandbox is not None else (runtime.context or {}).get("sandbox_id")
            if inherited is not None:
                logger.debug(f"Keeping inherited sandbox {inherited} acquired (release_on_exit=False)")
            return None

        sandbox = state.get("sandbox")
        if sandbox is not None:
            sandbox_id = sandbox["sandbox_id"]
            logger.info(f"Releasing sandbox {sandbox_id}")
            get_sandbox_provider().release(sandbox_id)
            return None

        if (runtime.context or {}).get("sandbox_id") is not None:
            sandbox_id = runtime.context.get("sandbox_id")
            logger.info(f"Releasing sandbox {sandbox_id} from context")
            get_sandbox_provider().release(sandbox_id)
            return None

        # No sandbox to release
        return super().after_agent(state, runtime)
