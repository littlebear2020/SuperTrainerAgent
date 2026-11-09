"""Subagent execution engine."""

import asyncio
import atexit
import logging
import threading
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from langchain.agents import create_agent
from langchain.tools import BaseTool
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.errors import GraphBubbleUp, GraphRecursionError

from deerflow.agents.thread_state import SandboxState, ThreadDataState, ThreadState
from deerflow.config.app_config import AppConfig
from deerflow.models import create_chat_model
from deerflow.subagents.config import SubagentConfig
from deerflow.subagents.turn_budget import JUMP_MARGIN_COVERED_HOOKS, find_jumping_hooks, resolve_recursion_limit

logger = logging.getLogger(__name__)


_previous_shutdown_isolated_subagent_loop = globals().get("_shutdown_isolated_subagent_loop")
if callable(_previous_shutdown_isolated_subagent_loop):
    atexit.unregister(_previous_shutdown_isolated_subagent_loop)
    _previous_shutdown_isolated_subagent_loop()


class SubagentStatus(Enum):
    """Status of a subagent execution."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


# Machine-readable "why did this run end" channel. Additive to ``SubagentStatus``
# on purpose: a new enum member would break consumers that switch exhaustively on
# COMPLETED/FAILED/CANCELLED/TIMED_OUT. The lead agent reads ``result.stop_reason``
# to tell a capped/killed finish from a clean one (P2-2 + P1-2 honesty signal — a
# turn-capped partial must not be dressed up as a plain success).
TURN_CAP_STOP_REASON = "turn_capped"
CANCELLED_STOP_REASON = "cancelled_by_user"
TIMEOUT_STOP_REASON = "timed_out"


@dataclass
class SubagentResult:
    """Result of a subagent execution.

    Attributes:
        task_id: Unique identifier for this execution.
        trace_id: Trace ID for distributed tracing (links parent and subagent logs).
        status: Current status of the execution.
        result: The final result message (if completed).
        error: Error message (if failed).
        started_at: When execution started.
        completed_at: When execution completed.
        ai_messages: List of complete AI messages (as dicts) generated during execution.
    """

    task_id: str
    trace_id: str
    status: SubagentStatus
    result: str | None = None
    error: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    ai_messages: list[dict[str, Any]] | None = None
    token_records: list[dict[str, int | str]] = field(default_factory=list)
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)
    # Additive honesty channel for runs that ended on a cap rather than on their
    # own terms. ``truncated`` marks a result as incomplete work; ``stop_reason``
    # says which cap bound it (currently only "turn_capped"). Kept out of
    # ``status`` on purpose: a new enum member would break existing consumers
    # that switch exhaustively on COMPLETED/FAILED.
    truncated: bool = False
    stop_reason: str | None = None
    # R1 (2026-09-24 §10.2.1): carrier for control-flow exceptions that escaped
    # the subagent graph (DiskAdmissionBlocked / SandboxCircuitBreakerError).
    # ``run_task`` stores the exception object here so the task tool can
    # re-raise it into the lead run's error terminal; without it the async
    # path's string conversion would hand the lead a "Task failed" message it
    # can retry past. None for ordinary failures.
    bubble_up: GraphBubbleUp | None = field(default=None, repr=False)

    def __post_init__(self):
        """Initialize mutable defaults."""
        if self.ai_messages is None:
            self.ai_messages = []


# Global storage for background task results
_background_tasks: dict[str, SubagentResult] = {}
_background_tasks_lock = threading.Lock()

# Thread pool for background task scheduling and orchestration
_scheduler_pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="subagent-scheduler-")

# Persistent event loop for isolated subagent executions triggered from an
# already-running parent loop. Reusing one long-lived loop avoids creating a
# fresh loop per execution and then closing async resources bound to it.
_isolated_subagent_loop: asyncio.AbstractEventLoop | None = None
_isolated_subagent_loop_thread: threading.Thread | None = None
_isolated_subagent_loop_started: threading.Event | None = None
_isolated_subagent_loop_lock = threading.Lock()


def _run_isolated_subagent_loop(
    loop: asyncio.AbstractEventLoop,
    started_event: threading.Event,
) -> None:
    """Run the persistent isolated subagent loop in a dedicated daemon thread."""
    asyncio.set_event_loop(loop)
    loop.call_soon(started_event.set)
    try:
        loop.run_forever()
    finally:
        started_event.clear()


def _shutdown_isolated_subagent_loop() -> None:
    """Stop and close the persistent isolated subagent loop."""
    global _isolated_subagent_loop, _isolated_subagent_loop_thread, _isolated_subagent_loop_started

    with _isolated_subagent_loop_lock:
        loop = _isolated_subagent_loop
        thread = _isolated_subagent_loop_thread
        _isolated_subagent_loop = None
        _isolated_subagent_loop_thread = None
        _isolated_subagent_loop_started = None

    if loop is None:
        return

    if loop.is_running():
        loop.call_soon_threadsafe(loop.stop)

    if thread is not None and thread.is_alive() and thread is not threading.current_thread():
        thread.join(timeout=1)

    thread_stopped = thread is None or not thread.is_alive()
    loop_stopped = not loop.is_running()

    if not loop.is_closed():
        if thread_stopped and loop_stopped:
            loop.close()
        else:
            logger.warning(
                "Skipping close of isolated subagent loop because shutdown did not complete within timeout (thread_alive=%s, loop_running=%s)",
                thread is not None and thread.is_alive(),
                loop.is_running(),
            )


atexit.register(_shutdown_isolated_subagent_loop)


def _get_isolated_subagent_loop() -> asyncio.AbstractEventLoop:
    """Return the persistent event loop used by isolated subagent executions."""
    global _isolated_subagent_loop, _isolated_subagent_loop_thread, _isolated_subagent_loop_started
    with _isolated_subagent_loop_lock:
        thread_is_alive = _isolated_subagent_loop_thread is not None and _isolated_subagent_loop_thread.is_alive()
        loop_is_usable = _isolated_subagent_loop is not None and not _isolated_subagent_loop.is_closed() and _isolated_subagent_loop.is_running() and thread_is_alive

        if not loop_is_usable:
            loop = asyncio.new_event_loop()
            started_event = threading.Event()
            thread = threading.Thread(
                target=_run_isolated_subagent_loop,
                args=(loop, started_event),
                name="subagent-persistent-loop",
                daemon=True,
            )
            thread.start()
            if not started_event.wait(timeout=5):
                loop.call_soon_threadsafe(loop.stop)
                thread.join(timeout=1)
                loop.close()
                raise RuntimeError("Timed out starting isolated subagent event loop")
            _isolated_subagent_loop = loop
            _isolated_subagent_loop_thread = thread
            _isolated_subagent_loop_started = started_event

        return _isolated_subagent_loop


def _filter_tools(
    all_tools: list[BaseTool],
    allowed: list[str] | None,
    disallowed: list[str] | None,
) -> list[BaseTool]:
    """Filter tools based on subagent configuration.

    Args:
        all_tools: List of all available tools.
        allowed: Optional allowlist of tool names. If provided, only these tools are included.
        disallowed: Optional denylist of tool names. These tools are always excluded.

    Returns:
        Filtered list of tools.
    """
    filtered = all_tools

    # Apply allowlist if specified
    if allowed is not None:
        allowed_set = set(allowed)
        filtered = [t for t in filtered if t.name in allowed_set]

    # Apply denylist
    if disallowed is not None:
        disallowed_set = set(disallowed)
        filtered = [t for t in filtered if t.name not in disallowed_set]

    return filtered


def _content_to_text(content: Any) -> str:
    """Flatten a message's mixed ``content`` shape into plain text.

    ``content`` is a string for most providers but a list of blocks for
    multimodal / thinking-enabled models. Raw string chunks are concatenated
    directly while full text blocks stay newline-separated, matching how the
    normal-completion path below renders its result.
    """
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content)

    text_parts: list[str] = []
    pending_str_parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            pending_str_parts.append(block)
        elif isinstance(block, dict):
            if pending_str_parts:
                text_parts.append("".join(pending_str_parts))
                pending_str_parts.clear()
            text_val = block.get("text")
            if isinstance(text_val, str):
                text_parts.append(text_val)
    if pending_str_parts:
        text_parts.append("".join(pending_str_parts))
    return "\n".join(text_parts)


def _find_last_ai_text(messages: list[Any]) -> str | None:
    """Scan backward for the last AIMessage that carries real text.

    Skips pure tool-call envelopes (``content=""`` with ``tool_calls``), which
    are the usual tail of a run aborted mid-step: the model had just requested
    another tool when the turn budget ran out. Returns None when no assistant
    message produced any text.
    """
    for message in reversed(messages):
        if not isinstance(message, AIMessage):
            continue
        text = _content_to_text(message.content).strip()
        if text:
            return text
    return None


def _get_model_name(config: SubagentConfig, parent_model: str | None) -> str | None:
    """Resolve the model name for a subagent.

    Args:
        config: Subagent configuration.
        parent_model: The parent agent's model name.

    Returns:
        Model name to use, or None to use default.
    """
    if config.model == "inherit":
        return parent_model
    return config.model


class SubagentExecutor:
    """Executor for running subagents."""

    def __init__(
        self,
        config: SubagentConfig,
        tools: list[BaseTool],
        app_config: AppConfig | None = None,
        parent_model: str | None = None,
        parent_thinking_enabled: bool | None = None,
        parent_reasoning_effort: str | None = None,
        sandbox_state: SandboxState | None = None,
        thread_data: ThreadDataState | None = None,
        thread_id: str | None = None,
        trace_id: str | None = None,
    ):
        """Initialize the executor.

        Args:
            config: Subagent configuration.
            tools: List of all available tools (will be filtered).
            app_config: Resolved AppConfig; threaded into middleware factories
                at agent-build time. When None, ``_create_agent`` falls back to
                ``get_app_config()`` (matches the lead-agent factory's pattern).
            parent_model: The parent agent's model name for inheritance.
            parent_thinking_enabled: Parent run's thinking mode, used only when
                ``config.thinking_enabled`` is None (inherit).
            parent_reasoning_effort: Parent run's reasoning effort, forwarded to
                the model when the subagent runs with thinking enabled.
            sandbox_state: Sandbox state from parent agent.
            thread_data: Thread data from parent agent.
            thread_id: Thread ID for sandbox operations.
            trace_id: Trace ID from parent for distributed tracing.
        """
        self.config = config
        self.app_config = app_config
        self.parent_model = parent_model
        self.parent_thinking_enabled = parent_thinking_enabled
        self.parent_reasoning_effort = parent_reasoning_effort
        self.sandbox_state = sandbox_state
        self.thread_data = thread_data
        self.thread_id = thread_id
        # Generate trace_id if not provided (for top-level calls)
        self.trace_id = trace_id or str(uuid.uuid4())[:8]

        # LangGraph super-step budget that buys ``config.max_turns`` turns,
        # resolved in ``_create_agent`` once the middleware chain — and with it
        # the compiled graph's per-turn node count — is known. Stays ``None``
        # until then; ``_aexecute`` falls back to the raw turn count so a test
        # double replacing ``_create_agent`` still produces a runnable config.
        self._recursion_limit: int | None = None

        # Filter tools based on config
        self.tools = _filter_tools(
            tools,
            config.tools,
            config.disallowed_tools,
        )

        logger.info(f"[trace={self.trace_id}] SubagentExecutor initialized: {config.name} with {len(self.tools)} tools")

    def _create_agent(self):
        """Create the agent instance."""
        # Mirror lead-agent factory pattern: prefer explicit app_config,
        # fall back to ambient lookup at agent-build time.
        from deerflow.config import get_app_config

        resolved_app_config = self.app_config or get_app_config()
        model_name = _get_model_name(self.config, self.parent_model)

        # P1-2 (2026-09-29): three-state thinking — the agent's own config wins,
        # else inherit the parent run's mode, else off (the former hard-coded
        # behavior, kept as the bottom fallback).
        thinking_enabled = self.config.thinking_enabled
        if thinking_enabled is None:
            thinking_enabled = bool(self.parent_thinking_enabled)

        try:
            model_config = resolved_app_config.get_model_config(model_name)
        except Exception:
            model_config = None

        # Degradation guard (mirrors make_lead_agent): keep subagents on
        # thinking-incapable models runnable instead of letting
        # create_chat_model raise on thinking_enabled=True.
        if thinking_enabled and model_config is not None and not model_config.supports_thinking:
            logger.warning(f"[trace={self.trace_id}] Subagent {self.config.name}: thinking requested but model '{model_name}' does not support it; falling back to non-thinking mode")
            thinking_enabled = False

        # Reasoning effort only means something with thinking on; forward the
        # parent's effort when the model declares support (factory would pop it
        # silently — the explicit check keeps the log line honest).
        model_kwargs: dict[str, Any] = {}
        if thinking_enabled and model_config is not None and model_config.supports_reasoning_effort and self.parent_reasoning_effort:
            logger.debug(
                "[trace=%s] Subagent %s running with thinking on, reasoning_effort=%s",
                self.trace_id,
                self.config.name,
                self.parent_reasoning_effort,
            )
            model_kwargs["reasoning_effort"] = self.parent_reasoning_effort

        model = create_chat_model(name=model_name, thinking_enabled=thinking_enabled, app_config=resolved_app_config, **model_kwargs)

        from deerflow.agents.middlewares.tool_error_handling_middleware import build_subagent_runtime_middlewares

        middlewares = build_subagent_runtime_middlewares(app_config=resolved_app_config, agent_name=self.config.name, lazy_init=True)
        self._recursion_limit = self._resolve_recursion_limit(middlewares)

        return create_agent(
            model=model,
            tools=self.tools,
            middleware=middlewares,
            system_prompt=self.config.system_prompt,
            state_schema=ThreadState,
            context_schema=dict[str, Any],  # R6(2026-09-24 W6): 开放 context 标注防序列化告警
        )

    def _resolve_recursion_limit(self, middlewares: list[Any]) -> int:
        """Translate ``max_turns`` into the super-step budget it actually means.

        ``max_turns`` is the operator-facing policy ("how many times may this
        agent think and act"), while LangGraph's ``recursion_limit`` counts
        graph nodes. ``create_agent`` compiles one node per middleware
        lifecycle hook, so the two differ by the depth of the assembled chain —
        see ``turn_budget.py`` for the arithmetic. Resolving it here, from the
        chain this subagent was actually built with, keeps the budget stable as
        middlewares are added and lets a per-agent chain differ.
        """
        recursion_limit = resolve_recursion_limit(self.config.max_turns, middlewares)
        logger.debug(
            "[trace=%s] Subagent %s turn budget: max_turns=%s -> recursion_limit=%s (%d middlewares)",
            self.trace_id,
            self.config.name,
            self.config.max_turns,
            recursion_limit,
            len(middlewares),
        )
        # P2-2 (2026-09-29): after_model->model jumps are budgeted with a
        # bounded retry margin in turn_budget.py (max_retries x 2 super-steps),
        # and this chain does carry one — TerminalResponseMiddleware's
        # empty-response retry — so those declarations are expected and only
        # rate a debug line. Any *other* jump shape (agent-level hooks,
        # unmodeled destinations) is unbudgeted and keeps the limit a lower
        # bound; say so loudly rather than letting runs quietly cap short.
        jumping_hooks = find_jumping_hooks(middlewares)
        covered = [(name, hook) for name, hook in jumping_hooks if hook in JUMP_MARGIN_COVERED_HOOKS]
        uncovered = [(name, hook) for name, hook in jumping_hooks if hook not in JUMP_MARGIN_COVERED_HOOKS]
        if covered:
            logger.debug(
                "[trace=%s] Subagent %s has margin-covered jump hooks (%s); recursion_limit=%s already includes the retry margin for max_turns=%s",
                self.trace_id,
                self.config.name,
                ", ".join(f"{name}.{hook}" for name, hook in covered),
                recursion_limit,
                self.config.max_turns,
            )
        if uncovered:
            logger.warning(
                "[trace=%s] Subagent %s has jump-declaring middleware hooks the budget does not model (%s); recursion_limit=%s is a lower bound for max_turns=%s, so the run may cap early",
                self.trace_id,
                self.config.name,
                ", ".join(f"{name}.{hook}" for name, hook in uncovered),
                recursion_limit,
                self.config.max_turns,
            )
        return recursion_limit

    async def _load_skill_messages(self) -> list[SystemMessage]:
        """Load the skills index as a single conversation item based on config.skills.

        Index injection, aligned with the lead agent (and Codex): skills are
        announced via a compact index (name/description/location) and loaded on
        demand with ``read_file`` from the shared sandbox, instead of injecting
        every enabled SKILL.md in full text. The config.skills whitelist
        controls which skills are indexed:
        - None: index all enabled skills
        - []: no skills
        - ["skill-a", "skill-b"]: only these skills

        Returns:
            List with at most one SystemMessage carrying the skills index.
        """
        if self.config.skills is not None and len(self.config.skills) == 0:
            logger.info(f"[trace={self.trace_id}] Subagent {self.config.name} skills=[] — skipping skill loading")
            return []

        # The index instructs the model to load a skill by calling ``read_file``
        # on the listed location; if the filtered tool set has no ``read_file``
        # (custom agents whose tools allowlist omits it) that guidance would
        # mislead — skip injection instead.
        if not any(getattr(tool, "name", None) == "read_file" for tool in self.tools):
            logger.warning(f"[trace={self.trace_id}] Subagent {self.config.name} has no read_file tool — skipping skills index injection")
            return []

        from deerflow.agents.lead_agent.prompt import get_skills_prompt_section
        from deerflow.config import get_app_config

        # Thread the executor's own AppConfig: skill discovery must not depend
        # on the lead process's cache-priming order (subagents run on their own
        # threads/loops), and get_skills_prompt_section(app_config=...) bypasses
        # the global enabled-skills cache by design.
        resolved_app_config = self.app_config or get_app_config()
        available_skills = set(self.config.skills) if self.config.skills is not None else None
        try:
            # Disk scan + rendering is synchronous; keep it off the event loop
            # (LangGraph ASGI requirement).
            section = await asyncio.to_thread(
                get_skills_prompt_section,
                available_skills,
                app_config=resolved_app_config,
            )
        except Exception:
            logger.warning(f"[trace={self.trace_id}] Failed to load skills for subagent {self.config.name}", exc_info=True)
            return []

        if not section:
            logger.info(f"[trace={self.trace_id}] Subagent {self.config.name} no skills matched its allowlist")
            return []

        scope = "all enabled" if available_skills is None else f"{len(available_skills)} allowlisted"
        logger.info(f"[trace={self.trace_id}] Subagent {self.config.name} injected skills index ({scope}, {len(section)} chars)")
        return [SystemMessage(content=section)]

    def _load_global_memory_context(self) -> str:
        """Load global user memory to inject as context into the subagent."""
        try:
            from deerflow.agents.memory.prompt import format_memory_for_injection
            from deerflow.agents.memory.storage import get_memory_storage
            from deerflow.runtime.user_context import get_effective_user_id

            user_id = get_effective_user_id()
            global_memory = get_memory_storage().load(agent_name=None, user_id=user_id)
            if global_memory:
                # Use a small token budget to avoid squeezing out the subagent's actual context
                formatted = format_memory_for_injection(global_memory, max_tokens=500)
                if formatted.strip():
                    return formatted
        except Exception:
            logger.debug(f"[trace={self.trace_id}] Failed to load global memory context for subagent {self.config.name}", exc_info=True)
        return ""

    def _load_subagent_workspace_memory(self) -> str:
        """Load this subagent's accumulated workspace memory (operational habits, corrections)."""
        try:
            from deerflow.agents.memory.prompt import format_memory_for_injection
            from deerflow.agents.memory.storage import get_memory_storage
            from deerflow.runtime.user_context import get_effective_user_id

            user_id = get_effective_user_id()
            workspace_memory = get_memory_storage().load(agent_name=self.config.name, user_id=user_id)
            if workspace_memory:
                formatted = format_memory_for_injection(workspace_memory, max_tokens=300)
                if formatted.strip():
                    logger.info(f"[trace={self.trace_id}] Subagent {self.config.name} loaded workspace memory")
                    return formatted
        except Exception:
            logger.debug(
                f"[trace={self.trace_id}] Failed to load workspace memory for {self.config.name}",
                exc_info=True,
            )
        return ""

    async def _build_initial_state(self, task: str) -> dict[str, Any]:
        """Build the initial state for agent execution.

        Args:
            task: The task description.

        Returns:
            Initial state dictionary.
        """
        # Load skills as conversation items (Codex pattern)
        skill_messages = await self._load_skill_messages()

        messages: list = []

        # 1. Inject Lead Agent's global memory as a context block first
        global_context = self._load_global_memory_context()
        if global_context:
            messages.append(SystemMessage(content=f"<global_user_context>\n{global_context}\n</global_user_context>"))

        # 2. Inject subagent's accumulated workspace memory (operational habits)
        workspace_memory = self._load_subagent_workspace_memory()
        if workspace_memory:
            messages.append(SystemMessage(content=f"<subagent_muscle_memory>\n{workspace_memory}\n</subagent_muscle_memory>"))

        # 3. Skill content injected as developer/system messages after context
        messages.extend(skill_messages)

        # Then the actual task
        messages.append(HumanMessage(content=task))

        state: dict[str, Any] = {
            "messages": messages,
        }

        # Pass through sandbox and thread data from parent
        if self.sandbox_state is not None:
            state["sandbox"] = self.sandbox_state
        if self.thread_data is not None:
            state["thread_data"] = self.thread_data

        return state

    async def _aexecute(self, task: str, result_holder: SubagentResult | None = None) -> SubagentResult:
        """Execute a task asynchronously.

        Args:
            task: The task description for the subagent.
            result_holder: Optional pre-created result object to update during execution.

        Returns:
            SubagentResult with the execution result.
        """
        if result_holder is not None:
            # Use the provided result holder (for async execution with real-time updates)
            result = result_holder
        else:
            # Create a new result for synchronous execution
            task_id = str(uuid.uuid4())[:8]
            result = SubagentResult(
                task_id=task_id,
                trace_id=self.trace_id,
                status=SubagentStatus.RUNNING,
                started_at=datetime.now(),
            )

        try:
            agent = self._create_agent()
            state = await self._build_initial_state(task)

            # Build config with thread_id for sandbox access and recursion limit
            run_config: RunnableConfig = {
                # Super-steps, not turns: ``_create_agent`` (just above) scaled
                # ``max_turns`` by the compiled chain's per-turn node count.
                # Unset only when that method was replaced by a test double.
                "recursion_limit": self._recursion_limit or self.config.max_turns,
            }
            context = {}
            if self.thread_id:
                run_config["configurable"] = {"thread_id": self.thread_id}
                context["thread_id"] = self.thread_id

            # Create SubagentTokenCollector when token usage is enabled
            collector = None
            try:
                resolved_config = self.app_config
                if resolved_config is None:
                    from deerflow.config import get_app_config

                    resolved_config = get_app_config()
                token_config = getattr(resolved_config, "token_usage", None)
                if token_config and getattr(token_config, "enabled", False):
                    from deerflow.subagents.token_collector import SubagentTokenCollector

                    collector = SubagentTokenCollector(caller=f"subagent:{self.config.name}")
            except Exception:
                logger.debug(f"[trace={self.trace_id}] Failed to init SubagentTokenCollector for {self.config.name}", exc_info=True)

            if collector is not None:
                run_config.setdefault("callbacks", []).append(collector)

            logger.info(f"[trace={self.trace_id}] Subagent {self.config.name} starting async execution with max_turns={self.config.max_turns}")

            # Use stream instead of invoke to get real-time updates
            # This allows us to collect AI messages as they are generated
            final_state = None

            # Pre-check: bail out immediately if already cancelled before streaming starts
            if result.cancel_event.is_set():
                logger.info(f"[trace={self.trace_id}] Subagent {self.config.name} cancelled before streaming")
                with _background_tasks_lock:
                    if result.status == SubagentStatus.RUNNING:
                        result.status = SubagentStatus.CANCELLED
                        result.stop_reason = CANCELLED_STOP_REASON
                        result.error = "Cancelled by user"
                        result.completed_at = datetime.now()
                return result

            async for chunk in agent.astream(state, config=run_config, context=context, stream_mode="values"):  # type: ignore[arg-type]
                # Cooperative cancellation: check if parent requested stop.
                # Note: cancellation is only detected at astream iteration boundaries,
                # so long-running tool calls within a single iteration will not be
                # interrupted until the next chunk is yielded.
                if result.cancel_event.is_set():
                    logger.info(f"[trace={self.trace_id}] Subagent {self.config.name} cancelled by parent")
                    with _background_tasks_lock:
                        if result.status == SubagentStatus.RUNNING:
                            result.status = SubagentStatus.CANCELLED
                            result.stop_reason = CANCELLED_STOP_REASON
                            result.error = "Cancelled by user"
                            result.completed_at = datetime.now()
                    return result

                final_state = chunk

                # Extract AI messages from the current state
                messages = chunk.get("messages", [])
                if messages:
                    last_message = messages[-1]
                    # Check if this is a new AI message
                    if isinstance(last_message, AIMessage):
                        # Convert message to dict for serialization
                        message_dict = last_message.model_dump()
                        # Only add if it's not already in the list (avoid duplicates)
                        # Check by comparing message IDs if available, otherwise compare full dict
                        message_id = message_dict.get("id")
                        is_duplicate = False
                        if message_id:
                            is_duplicate = any(msg.get("id") == message_id for msg in result.ai_messages)
                        else:
                            is_duplicate = message_dict in result.ai_messages

                        if not is_duplicate:
                            result.ai_messages.append(message_dict)
                            logger.info(f"[trace={self.trace_id}] Subagent {self.config.name} captured AI message #{len(result.ai_messages)}")

            logger.info(f"[trace={self.trace_id}] Subagent {self.config.name} completed async execution")

            # Transfer collected token records to result for parent journal consumption
            if collector is not None:
                result.token_records = collector.snapshot_records()
                if result.token_records:
                    logger.info(f"[trace={self.trace_id}] Subagent {self.config.name} collected {len(result.token_records)} token record(s)")

            if final_state is None:
                logger.warning(f"[trace={self.trace_id}] Subagent {self.config.name} no final state")
                result.result = "No response generated"
            else:
                # Extract the final message - find the last AIMessage
                messages = final_state.get("messages", [])
                logger.info(f"[trace={self.trace_id}] Subagent {self.config.name} final messages count: {len(messages)}")

                # Find the last AIMessage in the conversation
                last_ai_message = None
                for msg in reversed(messages):
                    if isinstance(msg, AIMessage):
                        last_ai_message = msg
                        break

                if last_ai_message is not None:
                    content = last_ai_message.content
                    # Handle both str and list content types for the final result
                    if isinstance(content, str):
                        result.result = content
                    elif isinstance(content, list):
                        # Extract text from list of content blocks for final result only.
                        # Concatenate raw string chunks directly, but preserve separation
                        # between full text blocks for readability.
                        text_parts = []
                        pending_str_parts = []
                        for block in content:
                            if isinstance(block, str):
                                pending_str_parts.append(block)
                            elif isinstance(block, dict):
                                if pending_str_parts:
                                    text_parts.append("".join(pending_str_parts))
                                    pending_str_parts.clear()
                                text_val = block.get("text")
                                if isinstance(text_val, str):
                                    text_parts.append(text_val)
                        if pending_str_parts:
                            text_parts.append("".join(pending_str_parts))
                        result.result = "\n".join(text_parts) if text_parts else "No text content in response"
                    else:
                        result.result = str(content)
                elif messages:
                    # Fallback: use the last message if no AIMessage found
                    last_message = messages[-1]
                    logger.warning(f"[trace={self.trace_id}] Subagent {self.config.name} no AIMessage found, using last message: {type(last_message)}")
                    raw_content = last_message.content if hasattr(last_message, "content") else str(last_message)
                    if isinstance(raw_content, str):
                        result.result = raw_content
                    elif isinstance(raw_content, list):
                        parts = []
                        pending_str_parts = []
                        for block in raw_content:
                            if isinstance(block, str):
                                pending_str_parts.append(block)
                            elif isinstance(block, dict):
                                if pending_str_parts:
                                    parts.append("".join(pending_str_parts))
                                    pending_str_parts.clear()
                                text_val = block.get("text")
                                if isinstance(text_val, str):
                                    parts.append(text_val)
                        if pending_str_parts:
                            parts.append("".join(pending_str_parts))
                        result.result = "\n".join(parts) if parts else "No text content in response"
                    else:
                        result.result = str(raw_content)
                else:
                    logger.warning(f"[trace={self.trace_id}] Subagent {self.config.name} no messages in final state")
                    result.result = "No response generated"

            result.status = SubagentStatus.COMPLETED
            result.completed_at = datetime.now()

        except GraphRecursionError:
            # ``recursion_limit`` on run_config 为换算后的超步预算（max_turns ×
            # 编译链每轮节点数，``_create_agent`` 中解析），命中它意味着子代理
            # 烧完了整轮次预算。Previously it fell through to the generic handler
            # below and every completed result was thrown away with only the raw
            # recursion sentence left.
            #
            # ``final_state`` still holds the last chunk streamed before the
            # limit fired, so the work is recoverable. Recover it — but mark it
            # ``truncated`` with ``stop_reason="turn_capped"`` and keep the
            # max_turns cause in ``error``, so a partial run is never handed to
            # the lead dressed up as a clean completion.
            max_turns = self.config.max_turns
            logger.warning(f"[trace={self.trace_id}] Subagent {self.config.name} reached max_turns={max_turns} (GraphRecursionError); recovering partial result")

            if collector is not None:
                result.token_records = collector.snapshot_records()

            partial = _find_last_ai_text((final_state or {}).get("messages", []))
            result.truncated = True
            result.stop_reason = TURN_CAP_STOP_REASON
            result.error = f"Reached max_turns={max_turns} before finishing"
            result.completed_at = datetime.now()

            if partial is not None:
                # Partial work exists: report COMPLETED so the lead receives the
                # content, with the truncation flags above telling it the work is
                # unfinished.
                result.result = partial
                result.status = SubagentStatus.COMPLETED
            else:
                # Nothing usable was produced — a genuine failure.
                result.status = SubagentStatus.FAILED

        except GraphBubbleUp:
            # Circuit-breaker / disk-admission signals raised inside the subagent
            # graph must reach the lead run's error terminal (worker.py), not be
            # swallowed into a "Task failed" string the lead would retry past.
            raise
        except Exception as e:
            logger.exception(f"[trace={self.trace_id}] Subagent {self.config.name} async execution failed")
            result.status = SubagentStatus.FAILED
            result.error = str(e)
            result.completed_at = datetime.now()

        return result

    def _execute_in_isolated_loop(self, task: str, result_holder: SubagentResult | None = None) -> SubagentResult:
        """Execute the subagent on the persistent isolated event loop.

        This method is used by the sync ``execute()`` path when the caller is
        already running inside an event loop. Because ``execute()`` is a sync
        API, this path blocks the caller while the actual coroutine runs on the
        long-lived isolated loop. Reusing that loop keeps shared async clients
        from being tied to a short-lived loop that gets closed per execution.
        """
        future: Future[SubagentResult] | None = None
        try:
            future = asyncio.run_coroutine_threadsafe(
                self._aexecute(task, result_holder),
                _get_isolated_subagent_loop(),
            )
            return future.result(timeout=self.config.timeout_seconds)
        except FuturesTimeoutError:
            if result_holder is not None:
                result_holder.cancel_event.set()
            if future is not None:
                future.cancel()
            raise
        except Exception:
            if future is None:
                logger.debug(
                    f"[trace={self.trace_id}] Failed to submit subagent {self.config.name} to the isolated event loop",
                    exc_info=True,
                )
            else:
                logger.debug(
                    f"[trace={self.trace_id}] Subagent {self.config.name} failed while executing on the isolated event loop",
                    exc_info=True,
                )
            raise

    def execute(self, task: str, result_holder: SubagentResult | None = None) -> SubagentResult:
        """Execute a task synchronously (wrapper around async execution).

        This method runs the async execution in a new event loop, allowing
        asynchronous tools (like MCP tools) to be used within the thread pool.

        When called from within an already-running event loop (e.g., when the
        parent agent is async), this method synchronously waits on the
        persistent isolated loop to avoid event loop conflicts with shared
        async primitives like httpx clients.

        Args:
            task: The task description for the subagent.
            result_holder: Optional pre-created result object to update during execution.

        Returns:
            SubagentResult with the execution result.
        """
        try:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            if loop is not None and loop.is_running():
                logger.debug(f"[trace={self.trace_id}] Subagent {self.config.name} detected running event loop, using isolated loop")
                return self._execute_in_isolated_loop(task, result_holder)

            # Standard path: no running event loop, use asyncio.run
            return asyncio.run(self._aexecute(task, result_holder))
        except FuturesTimeoutError:
            # Sync path: _execute_in_isolated_loop's future.result(timeout=...)
            # fired. The async background path (execute_async) stamps
            # TIMEOUT_STOP_REASON on its own TIMED_OUT branch; this mirrors it
            # so the lead can distinguish a wall-clock timeout from a crash
            # (C5 — previously fell into the generic Exception handler with no
            # stop_reason).
            if result_holder is not None:
                result = result_holder
            else:
                result = SubagentResult(
                    task_id=str(uuid.uuid4())[:8],
                    trace_id=self.trace_id,
                    status=SubagentStatus.FAILED,
                )
            result.status = SubagentStatus.FAILED
            result.stop_reason = TIMEOUT_STOP_REASON
            result.error = f"Execution timed out after {self.config.timeout_seconds} seconds"
            result.completed_at = datetime.now()
            return result
        except GraphBubbleUp:
            # R1 (2026-09-24 §10.2.1): control-flow exceptions must escape the
            # sync API too — same contract as the async background path.
            raise
        except Exception as e:
            logger.exception(f"[trace={self.trace_id}] Subagent {self.config.name} execution failed")
            # Create a result with error if we don't have one
            if result_holder is not None:
                result = result_holder
            else:
                result = SubagentResult(
                    task_id=str(uuid.uuid4())[:8],
                    trace_id=self.trace_id,
                    status=SubagentStatus.FAILED,
                )
            result.status = SubagentStatus.FAILED
            result.error = str(e)
            result.completed_at = datetime.now()
            return result

    def execute_async(self, task: str, task_id: str | None = None) -> str:
        """Start a task execution in the background.

        Args:
            task: The task description for the subagent.
            task_id: Optional task ID to use. If not provided, a random UUID will be generated.

        Returns:
            Task ID that can be used to check status later.
        """
        # Use provided task_id or generate a new one
        if task_id is None:
            task_id = str(uuid.uuid4())[:8]

        # Create initial pending result
        result = SubagentResult(
            task_id=task_id,
            trace_id=self.trace_id,
            status=SubagentStatus.PENDING,
        )

        logger.info(f"[trace={self.trace_id}] Subagent {self.config.name} starting async execution, task_id={task_id}, timeout={self.config.timeout_seconds}s")

        with _background_tasks_lock:
            _background_tasks[task_id] = result

        # Submit to scheduler pool
        def run_task():
            with _background_tasks_lock:
                _background_tasks[task_id].status = SubagentStatus.RUNNING
                _background_tasks[task_id].started_at = datetime.now()
                result_holder = _background_tasks[task_id]

            try:
                # Submit execution directly to the persistent isolated loop so the
                # background path does not create a temporary loop via execute().
                execution_future = asyncio.run_coroutine_threadsafe(
                    self._aexecute(task, result_holder),
                    _get_isolated_subagent_loop(),
                )
                try:
                    # Wait for execution with timeout
                    exec_result = execution_future.result(timeout=self.config.timeout_seconds)
                    with _background_tasks_lock:
                        _background_tasks[task_id].status = exec_result.status
                        _background_tasks[task_id].result = exec_result.result
                        _background_tasks[task_id].error = exec_result.error
                        _background_tasks[task_id].completed_at = datetime.now()
                        _background_tasks[task_id].ai_messages = exec_result.ai_messages
                except FuturesTimeoutError:
                    logger.error(f"[trace={self.trace_id}] Subagent {self.config.name} execution timed out after {self.config.timeout_seconds}s")
                    with _background_tasks_lock:
                        if _background_tasks[task_id].status == SubagentStatus.RUNNING:
                            _background_tasks[task_id].status = SubagentStatus.TIMED_OUT
                            _background_tasks[task_id].stop_reason = TIMEOUT_STOP_REASON
                            _background_tasks[task_id].error = f"Execution timed out after {self.config.timeout_seconds} seconds"
                            _background_tasks[task_id].completed_at = datetime.now()
                    # Signal cooperative cancellation and cancel the future
                    result_holder.cancel_event.set()
                    execution_future.cancel()
            except GraphBubbleUp as e:
                # R1 (2026-09-24 §10.2.1): keep the exception object alive on the
                # result — the task tool re-raises it so the signal reaches the
                # lead run's error terminal (worker.py), not a FAILED string.
                logger.exception(f"[trace={self.trace_id}] Subagent {self.config.name} raised control-flow exception")
                with _background_tasks_lock:
                    _background_tasks[task_id].status = SubagentStatus.FAILED
                    _background_tasks[task_id].error = str(e)
                    _background_tasks[task_id].bubble_up = e
                    _background_tasks[task_id].completed_at = datetime.now()
            except Exception as e:
                logger.exception(f"[trace={self.trace_id}] Subagent {self.config.name} async execution failed")
                with _background_tasks_lock:
                    _background_tasks[task_id].status = SubagentStatus.FAILED
                    _background_tasks[task_id].error = str(e)
                    _background_tasks[task_id].completed_at = datetime.now()

        _scheduler_pool.submit(run_task)
        return task_id


MAX_CONCURRENT_SUBAGENTS = 3


def request_cancel_background_task(task_id: str) -> None:
    """Signal a running background task to stop.

    Sets the cancel_event on the task, which is checked cooperatively
    by ``_aexecute`` during ``agent.astream()`` iteration.  This allows
    subagent threads — which cannot be force-killed via ``Future.cancel()``
    — to stop at the next iteration boundary.

    Args:
        task_id: The task ID to cancel.
    """
    with _background_tasks_lock:
        result = _background_tasks.get(task_id)
        if result is not None:
            result.cancel_event.set()
            logger.info("Requested cancellation for background task %s", task_id)


def get_background_task_result(task_id: str) -> SubagentResult | None:
    """Get the result of a background task.

    Args:
        task_id: The task ID returned by execute_async.

    Returns:
        SubagentResult if found, None otherwise.
    """
    with _background_tasks_lock:
        return _background_tasks.get(task_id)


def list_background_tasks() -> list[SubagentResult]:
    """List all background tasks.

    Returns:
        List of all SubagentResult instances.
    """
    with _background_tasks_lock:
        return list(_background_tasks.values())


def cleanup_background_task(task_id: str) -> None:
    """Remove a completed task from background tasks.

    Should be called by task_tool after it finishes polling and returns the result.
    This prevents memory leaks from accumulated completed tasks.

    Only removes tasks that are in a terminal state (COMPLETED/FAILED/TIMED_OUT)
    to avoid race conditions with the background executor still updating the task entry.

    Args:
        task_id: The task ID to remove.
    """
    with _background_tasks_lock:
        result = _background_tasks.get(task_id)
        if result is None:
            # Nothing to clean up; may have been removed already.
            logger.debug("Requested cleanup for unknown background task %s", task_id)
            return

        # Only clean up tasks that are in a terminal state to avoid races with
        # the background executor still updating the task entry.
        is_terminal_status = result.status in {
            SubagentStatus.COMPLETED,
            SubagentStatus.FAILED,
            SubagentStatus.CANCELLED,
            SubagentStatus.TIMED_OUT,
        }
        if is_terminal_status or result.completed_at is not None:
            del _background_tasks[task_id]
            logger.debug("Cleaned up background task: %s", task_id)
        else:
            logger.debug(
                "Skipping cleanup for non-terminal background task %s (status=%s)",
                task_id,
                result.status.value if hasattr(result.status, "value") else result.status,
            )
