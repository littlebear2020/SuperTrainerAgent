"""Middleware to enforce subagent tool-call limits per response and per run."""

import logging
from typing import Any, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langgraph.runtime import Runtime

from deerflow.agents.middlewares.tool_call_metadata import clone_ai_message_with_tool_calls
from deerflow.subagents.executor import MAX_CONCURRENT_SUBAGENTS

logger = logging.getLogger(__name__)

# Valid range for max_concurrent_subagents
MIN_SUBAGENT_LIMIT = 2
MAX_SUBAGENT_LIMIT = 4

# Valid range for the per-run total. The default is deliberately generous: the
# product keeps its "delegate whatever decomposes" character, so this cap only
# stops runaway spend and must not bite a legitimately broad task.
MIN_TOTAL_SUBAGENT_LIMIT = 1
MAX_TOTAL_SUBAGENT_LIMIT = 50
DEFAULT_MAX_TOTAL_SUBAGENTS = 12

# HumanMessage names that are framework injections, never a real user turn.
# A run boundary must not be (re)established on any of these (N3, 2026-08-13):
# loop_detection injects HumanMessage(name="loop_warning"), summarization uses
# name="summary", todo_middleware uses name="todo_reminder", and this
# middleware's own total-cap notice uses "subagent_limit_notice".
_INJECTED_HUMAN_MESSAGE_NAMES = frozenset(
    {
        "loop_warning",
        "summary",
        "todo_reminder",
        "subagent_limit_notice",
    }
)

_TOTAL_LIMIT_STOP_MSG = (
    "[SUBAGENT LIMIT REACHED] The subagent delegation limit for this run has been reached. "
    "Continue using the subagent results already collected, execute remaining simple work "
    "directly, or summarize the remaining work instead of launching more subagents."
)


def _clamp_subagent_limit(value: int) -> int:
    """Clamp subagent limit to valid range [2, 4]."""
    return max(MIN_SUBAGENT_LIMIT, min(MAX_SUBAGENT_LIMIT, value))


def _clamp_total_subagent_limit(value: int) -> int:
    """Clamp the per-run subagent total to [1, 50]."""
    return max(MIN_TOTAL_SUBAGENT_LIMIT, min(MAX_TOTAL_SUBAGENT_LIMIT, value))


def _is_real_user_message(msg: Any) -> bool:
    """True if *msg* is a genuine user turn, not a framework injection.

    N3 (2026-08-13): loop_detection injects ``HumanMessage(name="loop_warning")``
    and ViewImageMiddleware injects image-details HumanMessages (no name, but
    ``additional_kwargs.hide_from_ui=True``). Counting those as user turns would
    reset the per-run delegation count mid-run.
    """
    if getattr(msg, "type", None) != "human":
        return False
    if getattr(msg, "name", None) in _INJECTED_HUMAN_MESSAGE_NAMES:
        return False
    additional_kwargs = getattr(msg, "additional_kwargs", None) or {}
    if additional_kwargs.get("hide_from_ui"):
        return False
    return True


def _run_boundary_index(messages: list) -> int:
    """Index of the last real user message (the current run boundary).

    Returns -1 when no real user message exists — fall back to counting the
    whole history (lead chains always start with one; this path guards
    synthetic states in tests/subgraphs).
    """
    for i in range(len(messages) - 1, -1, -1):
        if _is_real_user_message(messages[i]):
            return i
    return -1


def _count_prior_task_calls(messages: list, boundary: int) -> int:
    """Count ``task`` tool calls dispatched in the current run.

    The message history is the durable record of what was actually launched: it
    travels with the checkpoint, and truncation below rewrites the AIMessage in
    place, so calls this middleware already dropped are never counted.

    Per-run scoping (D3 / R-02): only messages AFTER the last real user
    message count. History from earlier user turns in the same thread no
    longer eats the delegation budget, and interrupt/resume does not reset
    the count because the boundary message is unchanged.
    """
    total = 0
    for message in messages[boundary + 1 :]:
        if getattr(message, "type", None) != "ai":
            continue
        for tool_call in getattr(message, "tool_calls", None) or []:
            if tool_call.get("name") == "task":
                total += 1
    return total


def _append_text(content: Any, text: str) -> Any:
    if content is None:
        return text
    if isinstance(content, str):
        if content:
            return f"{content}\n\n{text}"
        return text
    if isinstance(content, list):
        return [*content, {"type": "text", "text": f"\n\n{text}"}]
    return f"{content}\n\n{text}"


class SubagentLimitMiddleware(AgentMiddleware[AgentState]):
    """Truncates 'task' tool calls exceeding the concurrent or per-run limit.

    Two independent caps; whichever is tighter wins on a given response:

    - ``max_concurrent`` bounds one model response, so a single planning step
      cannot fan out without bound.
    - ``max_total`` bounds the current RUN (2026-08-13, D3: previously the
      whole conversation). The run boundary is the last real user message —
      injected HumanMessages (loop warnings, image details, summaries) never
      reset it, and interrupt/resume keeps the same boundary. Without the
      cap, an agent that delegates a legal-sized batch, reviews, and delegates
      again stays within the concurrent limit on every single response while
      spending without bound — "N per round × unlimited rounds".

    When the total cap is exhausted the response gets a wrap-up notice appended
    and ``stop_reason="subagent_limit_capped"`` is stamped on the runtime
    context, so the worker can surface a capped completion instead of a silent
    truncation. Non-``task`` tool calls are never dropped: capping delegation
    must not disable the agent's own tools.

    Args:
        max_concurrent: Maximum concurrent subagent calls per response.
            Defaults to MAX_CONCURRENT_SUBAGENTS (3). Clamped to [2, 4].
        max_total: Maximum subagent calls per run.
            Defaults to DEFAULT_MAX_TOTAL_SUBAGENTS (12). Clamped to [1, 50].
    """

    def __init__(
        self,
        max_concurrent: int = MAX_CONCURRENT_SUBAGENTS,
        max_total: int = DEFAULT_MAX_TOTAL_SUBAGENTS,
    ):
        super().__init__()
        self.max_concurrent = _clamp_subagent_limit(max_concurrent)
        self.max_total = _clamp_total_subagent_limit(max_total)

    def _truncate_task_calls(self, state: AgentState, runtime: Runtime | None = None) -> dict | None:
        messages = state.get("messages", [])
        if not messages:
            return None

        last_msg = messages[-1]
        if getattr(last_msg, "type", None) != "ai":
            return None

        tool_calls = getattr(last_msg, "tool_calls", None)
        if not tool_calls:
            return None

        # Count task tool calls
        task_indices = [i for i, tc in enumerate(tool_calls) if tc.get("name") == "task"]
        if not task_indices:
            return None

        # Exclude the last message: its calls are the pending batch being
        # decided, not prior usage. Prior usage = task calls since the last
        # real user message (per-run, D3/N3).
        boundary = _run_boundary_index(messages[:-1])
        prior_task_calls = _count_prior_task_calls(messages[:-1], boundary)
        remaining_total = max(0, self.max_total - prior_task_calls)
        allowed = min(self.max_concurrent, remaining_total)

        if len(task_indices) <= allowed:
            return None

        # Build set of indices to drop (excess task calls beyond the limit)
        indices_to_drop = set(task_indices[allowed:])
        truncated_tool_calls = [tc for i, tc in enumerate(tool_calls) if i not in indices_to_drop]

        dropped_count = len(indices_to_drop)
        logger.warning(
            "Truncated %d excess task tool call(s) from model response (concurrent limit: %d, total limit: %d, prior delegations this run: %d)",
            dropped_count,
            self.max_concurrent,
            self.max_total,
            prior_task_calls,
        )

        # When the per-run total cap is exhausted, tell the model to wrap up
        # with direct tools and stamp the stop reason for the run worker.
        total_exhausted = remaining_total == 0
        if total_exhausted:
            if isinstance(getattr(runtime, "context", None), dict):
                runtime.context["stop_reason"] = "subagent_limit_capped"

        content = _append_text(last_msg.content, _TOTAL_LIMIT_STOP_MSG) if total_exhausted else None
        # Replace the AIMessage with truncated tool_calls (same id triggers
        # replacement). clone_ai_message_with_tool_calls also trims the raw
        # provider tool_calls in additional_kwargs so DanglingToolCallMiddleware
        # does not fabricate placeholders for the dropped calls.
        updated_msg = clone_ai_message_with_tool_calls(last_msg, truncated_tool_calls, content=content)
        return {"messages": [updated_msg]}

    @override
    def after_model(self, state: AgentState, runtime: Runtime) -> dict | None:
        return self._truncate_task_calls(state, runtime)

    @override
    async def aafter_model(self, state: AgentState, runtime: Runtime) -> dict | None:
        return self._truncate_task_calls(state, runtime)
