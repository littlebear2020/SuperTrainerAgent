"""Task tool for delegating work to subagents."""

import asyncio
import logging
import uuid
from dataclasses import replace
from typing import Annotated

from langchain.tools import InjectedToolCallId, ToolRuntime, tool
from langgraph.config import get_stream_writer
from langgraph.typing import ContextT

from deerflow.agents.thread_state import ThreadState
from deerflow.sandbox.security import LOCAL_BASH_SUBAGENT_DISABLED_MESSAGE, is_host_bash_allowed
from deerflow.subagents import SubagentExecutor, get_available_subagent_names, get_subagent_config
from deerflow.subagents.executor import SubagentStatus, cleanup_background_task, get_background_task_result, request_cancel_background_task

logger = logging.getLogger(__name__)


def _transfer_subagent_tokens(runtime, token_records: list[dict]) -> None:
    """Transfer subagent token records to the parent run's RunJournal."""
    if not token_records:
        return
    if runtime is None or not hasattr(runtime, "context") or runtime.context is None:
        return
    journal = runtime.context.get("__run_journal")
    if journal is None:
        return
    try:
        journal.record_external_llm_usage_records(token_records)
    except Exception:
        logger.debug("Failed to transfer subagent token records", exc_info=True)


def _record_subagent_lifecycle(
    runtime,
    task_id: str,
    subagent_type: str,
    event: str,
    *,
    status: str | None = None,
    stop_reason: str | None = None,
    truncated: bool | None = None,
    error: str | None = None,
) -> None:
    """Persist a ``task`` lifecycle point to the parent run's RunJournal (P3-2, 2026-09-29).

    The SSE stream only reaches the live client; run_events rows written here
    keep the lifecycle (with stop_reason / truncated honesty signals) queryable
    after the fact. Best-effort by design: the journal comes from
    ``runtime.context["__run_journal"]`` (injected by the run worker) and a
    missing journal — tests, runs outside the standard worker — skips silently
    so the SSE main path is never affected.
    """
    if runtime is None or not hasattr(runtime, "context") or runtime.context is None:
        return
    journal = runtime.context.get("__run_journal")
    if journal is None:
        return
    try:
        journal.record_subagent_lifecycle(
            task_id,
            subagent_type,
            event,
            status=status,
            stop_reason=stop_reason,
            truncated=truncated,
            error=error,
        )
    except Exception:
        logger.debug("Failed to record subagent lifecycle event %s (task_id=%s)", event, task_id, exc_info=True)


# P2-1 (2026-09-29): production runs always carry a thread_id (the run worker
# injects it unconditionally), so a missing one means this call bypassed the
# standard gateway runtime — ThreadDataMiddleware would raise on the subagent
# chain. Fail fast with an actionable message; do NOT mint a throwaway
# thread_id (it would orphan threads/ dirs and break sandbox reuse).
NO_THREAD_ID_DELEGATION_MESSAGE = "Delegation unavailable: no thread_id in runtime context or config; this run was started outside the standard gateway runtime."


def _merge_skill_allowlists(parent: list[str] | None, child: list[str] | None) -> list[str] | None:
    """Return the effective subagent skill allowlist under the parent policy."""
    if parent is None:
        return child
    if child is None:
        return list(parent)

    parent_set = set(parent)
    return [skill for skill in child if skill in parent_set]


@tool("task", parse_docstring=True)
async def task_tool(
    runtime: ToolRuntime[ContextT, ThreadState],
    description: str,
    prompt: str,
    subagent_type: str,
    tool_call_id: Annotated[str, InjectedToolCallId],
    max_turns: int | None = None,
) -> str:
    """Delegate a task to a specialized subagent that runs in its own context.

    Subagents help you:
    - Preserve context by keeping exploration and implementation separate
    - Handle complex multi-step tasks autonomously
    - Execute commands or operations in isolated contexts

    Built-in subagent types:
    - **general-purpose**: A capable agent for complex, multi-step tasks that require
      both exploration and action. Use when the task requires complex reasoning,
      multiple dependent steps, or would benefit from isolated context.
    - **bash**: Command execution specialist for running bash commands. This is only
      available when host bash is explicitly allowed or when using an isolated shell
      sandbox such as `AioSandboxProvider`.

    Additional custom subagent types may be defined in config.yaml under
    `subagents.custom_agents`. Each custom type can have its own system prompt,
    tools, skills, model, and timeout configuration. If an unknown subagent_type
    is provided, the error message will list all available types.

    When to use this tool:
    - Complex tasks requiring multiple steps or tools
    - Tasks that produce verbose output
    - When you want to isolate context from the main conversation
    - Parallel research or exploration tasks

    When NOT to use this tool:
    - Simple, single-step operations (use tools directly)
    - Tasks requiring user interaction or clarification

    Args:
        description: A short (3-5 word) description of the task for logging/display. ALWAYS PROVIDE THIS PARAMETER FIRST.
        prompt: The task description for the subagent. Be specific and clear about what needs to be done. ALWAYS PROVIDE THIS PARAMETER SECOND.
        subagent_type: The type of subagent to use. ALWAYS PROVIDE THIS PARAMETER THIRD.
        max_turns: Optional maximum number of agent turns. Defaults to subagent's configured max.
    """
    available_subagent_names = get_available_subagent_names()

    # Get subagent configuration
    config = get_subagent_config(subagent_type)
    if config is None:
        available = ", ".join(available_subagent_names)
        return f"Error: Unknown subagent type '{subagent_type}'. Available: {available}"
    if subagent_type == "bash" and not is_host_bash_allowed():
        return f"Error: {LOCAL_BASH_SUBAGENT_DISABLED_MESSAGE}"

    # Build config overrides
    overrides: dict = {}

    # Skills are loaded by SubagentExecutor per-session (aligned with Codex's pattern:
    # each subagent loads its own skills based on config, injected as conversation items).
    # No longer appended to system_prompt here.

    if max_turns is not None:
        overrides["max_turns"] = max_turns

    # Extract parent context from runtime
    sandbox_state = None
    thread_data = None
    thread_id = None
    parent_model = None
    parent_thinking_enabled = None
    parent_reasoning_effort = None
    trace_id = None
    metadata: dict = {}

    if runtime is not None:
        sandbox_state = runtime.state.get("sandbox")
        thread_data = runtime.state.get("thread_data")
        thread_id = runtime.context.get("thread_id") if runtime.context else None
        if thread_id is None:
            thread_id = runtime.config.get("configurable", {}).get("thread_id")

        # Try to get parent model from configurable
        metadata = runtime.config.get("metadata", {})
        parent_model = metadata.get("model_name")

        # Parent run's thinking mode (P1-2, 2026-09-29): make_lead_agent writes
        # thinking_enabled/reasoning_effort into run metadata; the executor
        # resolves them only when the subagent's own config leaves thinking
        # unset (None = inherit).
        parent_thinking_enabled = metadata.get("thinking_enabled")
        parent_reasoning_effort = metadata.get("reasoning_effort")

        # Get or generate trace_id for distributed tracing
        trace_id = metadata.get("trace_id") or str(uuid.uuid4())[:8]

    if thread_id is None:
        logger.warning(
            "[trace=%s] task tool invoked without a thread_id (runtime=%s); refusing to start a subagent run",
            trace_id,
            "present" if runtime is not None else "None",
        )
        return NO_THREAD_ID_DELEGATION_MESSAGE

    parent_available_skills = metadata.get("available_skills")
    if parent_available_skills is not None:
        overrides["skills"] = _merge_skill_allowlists(list(parent_available_skills), config.skills)

    if overrides:
        config = replace(config, **overrides)

    # Get available tools (excluding task tool to prevent nesting)
    # Lazy import to avoid circular dependency
    from deerflow.tools import get_available_tools

    # Inherit parent agent's tool_groups so subagents respect the same restrictions
    parent_tool_groups = metadata.get("tool_groups")

    # Subagents should not have subagent tools enabled (prevent recursive nesting)
    tools = get_available_tools(model_name=parent_model, groups=parent_tool_groups, subagent_enabled=False)

    # Create executor
    executor = SubagentExecutor(
        config=config,
        tools=tools,
        parent_model=parent_model,
        parent_thinking_enabled=parent_thinking_enabled,
        parent_reasoning_effort=parent_reasoning_effort,
        sandbox_state=sandbox_state,
        thread_data=thread_data,
        thread_id=thread_id,
        trace_id=trace_id,
    )

    # Start background execution (always async to prevent blocking)
    # Use tool_call_id as task_id for better traceability
    task_id = executor.execute_async(prompt, task_id=tool_call_id)

    # Poll for task completion in backend (removes need for LLM to poll)
    poll_count = 0
    last_status = None
    last_message_count = 0  # Track how many AI messages we've already sent
    # Polling timeout: execution timeout + 60s buffer, checked every 5s
    max_poll_count = (config.timeout_seconds + 60) // 5

    logger.info(f"[trace={trace_id}] Started background task {task_id} (subagent={subagent_type}, timeout={config.timeout_seconds}s, polling_limit={max_poll_count} polls)")

    writer = get_stream_writer()
    # Send Task Started message'
    writer({"type": "task_started", "task_id": task_id, "description": description})
    _record_subagent_lifecycle(runtime, task_id, subagent_type, "task_started")

    try:
        while True:
            result = get_background_task_result(task_id)

            if result is None:
                logger.error(f"[trace={trace_id}] Task {task_id} not found in background tasks")
                writer({"type": "task_failed", "task_id": task_id, "error": "Task disappeared from background tasks"})
                _record_subagent_lifecycle(runtime, task_id, subagent_type, "task_failed", error="Task disappeared from background tasks")
                cleanup_background_task(task_id)
                return f"Error: Task {task_id} disappeared from background tasks"

            # Log status changes for debugging
            if result.status != last_status:
                logger.info(f"[trace={trace_id}] Task {task_id} status: {result.status.value}")
                last_status = result.status

            # Check for new AI messages and send task_running events
            current_message_count = len(result.ai_messages)
            if current_message_count > last_message_count:
                # Send task_running event for each new message
                for i in range(last_message_count, current_message_count):
                    message = result.ai_messages[i]
                    writer(
                        {
                            "type": "task_running",
                            "task_id": task_id,
                            "message": message,
                            "message_index": i + 1,  # 1-based index for display
                            "total_messages": current_message_count,
                        }
                    )
                    _record_subagent_lifecycle(runtime, task_id, subagent_type, "task_running", status="running")
                    logger.info(f"[trace={trace_id}] Task {task_id} sent message #{i + 1}/{current_message_count}")
                last_message_count = current_message_count

            # Check if task completed, failed, or timed out.
            # stop_reason (P2-2) is surfaced both in the SSE event and in the return
            # text so the lead agent can tell a capped/killed finish from a clean one
            # — a turn-capped partial must not look like a plain "Task Succeeded".
            # getattr back-compat: results predating the field have no attribute.
            stop_reason = getattr(result, "stop_reason", None)
            stop_reason_suffix = f" (stop_reason: {stop_reason})" if stop_reason else ""
            if result.status == SubagentStatus.COMPLETED:
                _transfer_subagent_tokens(runtime, result.token_records)
                writer({"type": "task_completed", "task_id": task_id, "result": result.result, "stop_reason": stop_reason})
                _record_subagent_lifecycle(runtime, task_id, subagent_type, "task_completed", status="completed", stop_reason=stop_reason, truncated=getattr(result, "truncated", False))
                logger.info(f"[trace={trace_id}] Task {task_id} completed after {poll_count} polls")
                cleanup_background_task(task_id)
                return f"Task Succeeded. Result: {result.result}{stop_reason_suffix}"
            elif result.status == SubagentStatus.FAILED:
                _transfer_subagent_tokens(runtime, result.token_records)
                writer({"type": "task_failed", "task_id": task_id, "error": result.error, "stop_reason": stop_reason})
                _record_subagent_lifecycle(runtime, task_id, subagent_type, "task_failed", status="failed", stop_reason=stop_reason, truncated=getattr(result, "truncated", False), error=result.error)
                logger.error(f"[trace={trace_id}] Task {task_id} failed: {result.error}")
                cleanup_background_task(task_id)
                # getattr back-compat：同上 stop_reason 惯例——生产恒为
                # SubagentResult（字段必有），合成 SimpleNamespace 的测试桩免挂。
                bubble_up = getattr(result, "bubble_up", None)
                if bubble_up is not None:
                    # R1 (2026-09-24 §10.2.1): the subagent graph escaped a
                    # control-flow exception (disk admission / sandbox breaker).
                    # Re-raise so it reaches the lead run's error terminal via the
                    # tool-error passthrough — returning a "Task failed" string
                    # here would let the lead retry past the breaker.
                    raise bubble_up
                return f"Task failed. Error: {result.error}{stop_reason_suffix}"
            elif result.status == SubagentStatus.CANCELLED:
                # Tokens already spent are billable however the task ended — a
                # user stopping a long delegation, or a subagent burning the
                # full timeout, must not make that cost vanish from the run.
                _transfer_subagent_tokens(runtime, result.token_records)
                writer({"type": "task_cancelled", "task_id": task_id, "error": result.error, "stop_reason": stop_reason})
                _record_subagent_lifecycle(runtime, task_id, subagent_type, "task_cancelled", status="cancelled", stop_reason=stop_reason, truncated=getattr(result, "truncated", False), error=result.error)
                logger.info(f"[trace={trace_id}] Task {task_id} cancelled: {result.error}")
                cleanup_background_task(task_id)
                return f"Task cancelled by user.{stop_reason_suffix}"
            elif result.status == SubagentStatus.TIMED_OUT:
                _transfer_subagent_tokens(runtime, result.token_records)
                writer({"type": "task_timed_out", "task_id": task_id, "error": result.error, "stop_reason": stop_reason})
                _record_subagent_lifecycle(runtime, task_id, subagent_type, "task_timed_out", status="timed_out", stop_reason=stop_reason, truncated=getattr(result, "truncated", False), error=result.error)
                logger.warning(f"[trace={trace_id}] Task {task_id} timed out: {result.error}")
                cleanup_background_task(task_id)
                return f"Task timed out. Error: {result.error}{stop_reason_suffix}"

            # Still running, wait before next poll
            await asyncio.sleep(5)
            poll_count += 1

            # Polling timeout as a safety net (in case thread pool timeout doesn't work)
            # Set to execution timeout + 60s buffer, in 5s poll intervals
            # This catches edge cases where the background task gets stuck
            # Note: We don't call cleanup_background_task here because the task may
            # still be running in the background. The cleanup will happen when the
            # executor completes and sets a terminal status.
            if poll_count > max_poll_count:
                timeout_minutes = config.timeout_seconds // 60
                logger.error(f"[trace={trace_id}] Task {task_id} polling timed out after {poll_count} polls (should have been caught by thread pool timeout)")
                writer({"type": "task_timed_out", "task_id": task_id})
                _record_subagent_lifecycle(runtime, task_id, subagent_type, "task_timed_out", status=result.status.value)
                return f"Task polling timed out after {timeout_minutes} minutes. This may indicate the background task is stuck. Status: {result.status.value}"
    except asyncio.CancelledError:
        # Signal the background subagent thread to stop cooperatively.
        # Without this, the thread (running in ThreadPoolExecutor with its
        # own event loop via asyncio.run) would continue executing even
        # after the parent task is cancelled.
        request_cancel_background_task(task_id)

        async def cleanup_when_done() -> None:
            max_cleanup_polls = max_poll_count
            cleanup_poll_count = 0

            while True:
                result = get_background_task_result(task_id)
                if result is None:
                    return

                if result.status in {SubagentStatus.COMPLETED, SubagentStatus.FAILED, SubagentStatus.CANCELLED, SubagentStatus.TIMED_OUT} or getattr(result, "completed_at", None) is not None:
                    cleanup_background_task(task_id)
                    return

                if cleanup_poll_count > max_cleanup_polls:
                    logger.warning(f"[trace={trace_id}] Deferred cleanup for task {task_id} timed out after {cleanup_poll_count} polls")
                    return

                await asyncio.sleep(5)
                cleanup_poll_count += 1

        def log_cleanup_failure(cleanup_task: asyncio.Task[None]) -> None:
            if cleanup_task.cancelled():
                return

            exc = cleanup_task.exception()
            if exc is not None:
                logger.error(f"[trace={trace_id}] Deferred cleanup failed for task {task_id}: {exc}")

        logger.debug(f"[trace={trace_id}] Scheduling deferred cleanup for cancelled task {task_id}")
        asyncio.create_task(cleanup_when_done()).add_done_callback(log_cleanup_failure)
        raise
