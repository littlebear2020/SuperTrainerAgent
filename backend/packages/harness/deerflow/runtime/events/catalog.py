"""Canonical names and categories for persisted run events.

Producers import these definitions instead of repeating event-name/category
pairs as bare strings. That repetition has already cost us once: the
``feedback`` field on ``GET /api/threads/{id}/messages`` was permanently
``None`` because the read side matched ``"ai_message"`` while the write side
only ever emitted ``"llm.ai.response"`` — a mismatch no type checker or test
could see, since both sides were just strings.

Only the events this backend actually persists are defined here. The upstream
catalog additionally carries memory-context / workspace-changes definitions;
those producers do not exist locally, so importing them would advertise a
contract nothing writes. The subagent lifecycle definitions below are the
local counterpart of upstream's (P3-2, 2026-09-29): task_tool records every
`task` delegation lifecycle point that used to live only on the SSE stream.
"""

from __future__ import annotations

from dataclasses import dataclass

# Mirrors the column widths on ``RunEventRow`` (persistence/models/run_event.py:
# ``String(32)`` / ``String(16)``). Kept as constants so an over-long event
# name fails loudly at definition time rather than as a DB truncation/error.
RUN_EVENT_TYPE_MAX_LENGTH = 32
RUN_EVENT_CATEGORY_MAX_LENGTH = 16


def _validate_category(category: str) -> None:
    if not category:
        raise ValueError("Run event category must not be empty")
    if len(category) > RUN_EVENT_CATEGORY_MAX_LENGTH:
        raise ValueError(f"Run event category must not exceed {RUN_EVENT_CATEGORY_MAX_LENGTH} characters: {category!r}")


@dataclass(frozen=True, slots=True)
class RunEventDefinition:
    """A fixed (event_type, category) pair persisted by a producer."""

    event_type: str
    category: str

    def __post_init__(self) -> None:
        if not self.event_type:
            raise ValueError("Run event type must not be empty")
        if len(self.event_type) > RUN_EVENT_TYPE_MAX_LENGTH:
            raise ValueError(f"Run event type must not exceed {RUN_EVENT_TYPE_MAX_LENGTH} characters: {self.event_type!r}")
        _validate_category(self.category)


@dataclass(frozen=True, slots=True)
class RunEventPattern:
    """A family of event types sharing a prefix (e.g. ``middleware:{tag}``)."""

    pattern: str
    prefix: str
    category: str

    def __post_init__(self) -> None:
        _validate_category(self.category)

    def event_type(self, suffix: str) -> str:
        if not suffix:
            raise ValueError("Run event suffix must not be empty")
        max_suffix_length = RUN_EVENT_TYPE_MAX_LENGTH - len(self.prefix)
        if len(suffix) > max_suffix_length:
            raise ValueError(f"Run event suffix for {self.pattern!r} must not exceed {max_suffix_length} characters: {suffix!r}")
        return f"{self.prefix}{suffix}"


RUN_START_EVENT = RunEventDefinition("run.start", "trace")
RUN_END_EVENT = RunEventDefinition("run.end", "outputs")
RUN_ERROR_EVENT = RunEventDefinition("run.error", "error")
LLM_HUMAN_INPUT_EVENT = RunEventDefinition("llm.human.input", "message")
LLM_AI_RESPONSE_EVENT = RunEventDefinition("llm.ai.response", "message")
LLM_TOOL_RESULT_EVENT = RunEventDefinition("llm.tool.result", "message")
LLM_ERROR_EVENT = RunEventDefinition("llm.error", "trace")

# Legacy alias for AI-message rows. Never written by RunJournal; retained so
# readers can still match rows persisted before ``llm.ai.response`` became
# canonical. Read sides must accept BOTH (see LLM_AI_RESPONSE_EVENT_TYPES).
LEGACY_AI_MESSAGE_EVENT_TYPE = "ai_message"
LLM_AI_RESPONSE_EVENT_TYPES = (LLM_AI_RESPONSE_EVENT.event_type, LEGACY_AI_MESSAGE_EVENT_TYPE)

# P3-2 (2026-09-29): subagent `task` delegation lifecycle, persisted by
# RunJournal.record_subagent_lifecycle (producer: tools/builtins/task_tool.py).
# Mirrors the SSE event types one-to-one so the run_events rows are the durable
# counterpart of the stream the live client sees.
SUBAGENT_TASK_STARTED_EVENT = RunEventDefinition("subagent.task.started", "subagent")
SUBAGENT_TASK_RUNNING_EVENT = RunEventDefinition("subagent.task.running", "subagent")
SUBAGENT_TASK_COMPLETED_EVENT = RunEventDefinition("subagent.task.completed", "subagent")
SUBAGENT_TASK_FAILED_EVENT = RunEventDefinition("subagent.task.failed", "subagent")
SUBAGENT_TASK_CANCELLED_EVENT = RunEventDefinition("subagent.task.cancelled", "subagent")
SUBAGENT_TASK_TIMED_OUT_EVENT = RunEventDefinition("subagent.task.timed_out", "subagent")

# SSE event type (task_tool's "type" field) -> persisted definition.
SUBAGENT_LIFECYCLE_EVENTS = {
    "task_started": SUBAGENT_TASK_STARTED_EVENT,
    "task_running": SUBAGENT_TASK_RUNNING_EVENT,
    "task_completed": SUBAGENT_TASK_COMPLETED_EVENT,
    "task_failed": SUBAGENT_TASK_FAILED_EVENT,
    "task_cancelled": SUBAGENT_TASK_CANCELLED_EVENT,
    "task_timed_out": SUBAGENT_TASK_TIMED_OUT_EVENT,
}

MIDDLEWARE_EVENT_PATTERN = RunEventPattern(
    pattern="middleware:{tag}",
    prefix="middleware:",
    category="middleware",
)
MIDDLEWARE_EVENT_TAG_MAX_LENGTH = RUN_EVENT_TYPE_MAX_LENGTH - len(MIDDLEWARE_EVENT_PATTERN.prefix)

JOURNAL_RUN_EVENT_DEFINITIONS = (
    RUN_START_EVENT,
    RUN_END_EVENT,
    RUN_ERROR_EVENT,
    LLM_HUMAN_INPUT_EVENT,
    LLM_AI_RESPONSE_EVENT,
    LLM_TOOL_RESULT_EVENT,
    LLM_ERROR_EVENT,
)

FIXED_RUN_EVENT_DEFINITIONS = JOURNAL_RUN_EVENT_DEFINITIONS
