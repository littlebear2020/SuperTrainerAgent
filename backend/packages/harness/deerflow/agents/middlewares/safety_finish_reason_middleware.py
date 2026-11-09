"""Repair AIMessages the provider safety-terminated so they are neither
executed nor persisted empty.

Some providers (doubao/360 gateway ``finish_reason='content_filter'``,
Anthropic ``stop_reason='refusal'``, Gemini ``finish_reason='SAFETY'`` ...) stop
generation mid-stream while still returning partially-formed ``tool_calls``.
LangChain's tool router treats any AIMessage with non-empty ``tool_calls`` as
"go execute these", so half-truncated arguments — e.g. a ``write_file`` whose
content stops mid-sentence — get dispatched as if complete. The agent then sees
the truncated file, tries to fix it, gets filtered again, and loops.

This middleware sits at ``after_model`` and gates that behaviour: when a
detector fires it either

* strips the AIMessage's tool calls (structured *and* raw provider payloads)
  when it carries any — the truncated-tool-call case; or
* backfills a user-facing explanation when the message is otherwise blank,
  where an empty assistant message would be persisted and then rejected by
  strict OpenAI-compatible providers on every following request ("message ...
  with role 'assistant' must not be empty"), stranding the thread.

A safety-terminated message with visible text but no tool calls is left
untouched so its partial answer still reaches the user.

Local adaptation notes (differs from upstream):

* Upstream calls ``tool_call_metadata.clone_ai_message_with_tool_calls``, which
  does not exist here. Local ``LoopDetectionMiddleware._build_hard_stop_update``
  already implements the same three-surface clearing (structured ``tool_calls``,
  ``additional_kwargs.tool_calls``/``function_call``, and the
  ``finish_reason == "tool_calls"`` rewrite), so that mechanic is reused —
  keeping one tool-call-suppression behaviour in the codebase rather than two.
* Upstream emits a custom stream event and a ``RunJournal`` audit record via
  ``utils.custom_events`` / ``runtime.events.catalog``; neither module exists
  locally. Those paths are dropped in favour of a ``logger.warning`` plus the
  ``additional_kwargs.safety_termination`` record, which local SSE consumers can
  already read off the message.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage
from langgraph.runtime import Runtime

from deerflow.agents.middlewares.safety_termination_detectors import (
    SafetyTermination,
    SafetyTerminationDetector,
    default_detectors,
)

logger = logging.getLogger(__name__)

SAFETY_CAPPED_STOP_REASON = "safety_capped"

_USER_FACING_MESSAGE = (
    "The model provider stopped this response with a safety-related signal "
    "({reason_field}={reason_value!r}, detector={detector!r}). Any tool calls "
    "produced in this turn were suppressed because their arguments may be "
    "truncated and unsafe to execute. Please rephrase the request or ask for "
    "a narrower output."
)

# Used when the safety termination produced no tool calls *and* no content: the
# message is rewritten only so it is not persisted empty, so it must not claim
# any tool calls were suppressed.
_USER_FACING_EMPTY_MESSAGE = "The model provider stopped this response with a safety-related signal ({reason_field}={reason_value!r}, detector={detector!r}) and returned no content. Please rephrase your request or start a new conversation."


def _content_to_text(content: object) -> str:
    """Flatten AIMessage content to plain text for blank-detection.

    Local stand-in for upstream's ``utils.messages.message_content_to_text``
    (absent here). Only used to decide "is there visible content", so
    non-text blocks contribute nothing.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") in {"text", "output_text"}:
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return ""


@dataclass(frozen=True)
class _SafetyIntervention:
    update: dict
    termination: SafetyTermination
    suppressed_names: list[str]


class SafetyFinishReasonMiddleware(AgentMiddleware[AgentState]):
    """Repair AIMessages flagged by a SafetyTerminationDetector: strip tool
    calls, or backfill an explanation when the message is otherwise empty."""

    def __init__(self, detectors: list[SafetyTerminationDetector] | None = None) -> None:
        super().__init__()
        # Copy so caller mutations after construction don't leak into us.
        self._detectors: list[SafetyTerminationDetector] = list(detectors) if detectors else default_detectors()

    def _detect(self, message: AIMessage) -> SafetyTermination | None:
        for detector in self._detectors:
            try:
                hit = detector.detect(message)
            except Exception:
                logger.exception("SafetyTerminationDetector %r raised; treating as no-match", getattr(detector, "name", type(detector).__name__))
                continue
            if hit is not None:
                return hit
        return None

    def _build_suppressed_message(self, message: AIMessage, termination: SafetyTermination) -> AIMessage:
        from deerflow.agents.middlewares.loop_detection_middleware import LoopDetectionMiddleware

        tool_calls = message.tool_calls or []
        suppressed_names = [tc.get("name") or "unknown" for tc in tool_calls]
        template = _USER_FACING_MESSAGE if tool_calls else _USER_FACING_EMPTY_MESSAGE
        explanation = template.format(
            reason_field=termination.reason_field,
            reason_value=termination.reason_value,
            detector=termination.detector,
        )
        new_content = LoopDetectionMiddleware._append_text(message.content, explanation)

        # Reuse the local hard-stop mechanic: clears structured tool_calls, raw
        # additional_kwargs tool_calls/function_call, and normalizes a
        # finish_reason of "tool_calls". Our provider reason (content_filter /
        # refusal / SAFETY) is not "tool_calls", so it is preserved for
        # downstream SSE/converters.
        update = LoopDetectionMiddleware._build_hard_stop_update(message, new_content)

        kwargs = dict(update.get("additional_kwargs") or {})
        kwargs["safety_termination"] = {
            "detector": termination.detector,
            "reason_field": termination.reason_field,
            "reason_value": termination.reason_value,
            "suppressed_tool_call_count": len(suppressed_names),
            "suppressed_tool_call_names": suppressed_names,
            "extras": dict(termination.extras) if termination.extras else {},
        }
        update["additional_kwargs"] = kwargs
        return message.model_copy(update=update)

    def _prepare_intervention(self, state: AgentState, runtime: Runtime) -> _SafetyIntervention | None:
        messages = state.get("messages", [])
        if not messages:
            return None

        last = messages[-1]
        if not isinstance(last, AIMessage):
            return None

        tool_calls = list(last.tool_calls or [])
        # ``or ""`` normalizes every "no visible content" shape to blank: None,
        # "", [] and whitespace all count. None is reachable via
        # model_copy(update={"content": None}), which skips validation.
        content_is_blank = not _content_to_text(last.content or "").strip()
        if not tool_calls and not content_is_blank:
            return None

        termination = self._detect(last)
        if termination is None:
            return None

        backfilled_empty = content_is_blank and not tool_calls

        ctx = getattr(runtime, "context", None)
        thread_id = None
        if isinstance(ctx, dict):
            # Stamp so the run's stop reason reflects the cap, alongside
            # loop_capped / token_capped.
            ctx["stop_reason"] = SAFETY_CAPPED_STOP_REASON
            thread_id = ctx.get("thread_id")

        patched = self._build_suppressed_message(last, termination)

        logger.warning(
            "Provider safety termination detected — suppressed %d tool call(s), backfilled_empty_content=%s",
            len(tool_calls),
            backfilled_empty,
            extra={
                "thread_id": thread_id,
                "detector": termination.detector,
                "reason_field": termination.reason_field,
                "reason_value": termination.reason_value,
                "suppressed_tool_call_names": [tc.get("name") for tc in tool_calls],
                "backfilled_empty_content": backfilled_empty,
            },
        )

        return _SafetyIntervention(
            update={"messages": [patched]},
            termination=termination,
            suppressed_names=[tc.get("name") or "unknown" for tc in tool_calls],
        )

    @override
    def after_model(self, state: AgentState, runtime: Runtime) -> dict | None:
        intervention = self._prepare_intervention(state, runtime)
        return intervention.update if intervention else None

    @override
    async def aafter_model(self, state: AgentState, runtime: Runtime) -> dict | None:
        intervention = self._prepare_intervention(state, runtime)
        return intervention.update if intervention else None
