"""Run event capture via LangChain callbacks.

RunJournal sits between LangChain's callback mechanism and the pluggable
RunEventStore. It standardizes callback data into RunEvent records and
handles token usage accumulation.

Key design decisions:
- on_llm_new_token is NOT implemented -- only complete messages via on_llm_end
- on_chat_model_start captures structured prompts as llm_request (OpenAI format) and
  extracts the first human message for run.input, because it is more reliable than
  on_chain_start (fires on every node) — messages here are fully structured.
- on_chain_start with parent_run_id=None emits a run.start trace marking root invocation.
- on_llm_end emits llm_response in OpenAI Chat Completions format
- Token usage accumulated in memory, written to RunRow on run completion
- Caller identification via tags injection (lead_agent / subagent:{name} / middleware:{name})
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage, AnyMessage, BaseMessage, HumanMessage, ToolMessage
from langgraph.types import Command

from deerflow.runtime.events.catalog import (
    LLM_AI_RESPONSE_EVENT,
    LLM_ERROR_EVENT,
    LLM_HUMAN_INPUT_EVENT,
    LLM_TOOL_RESULT_EVENT,
    RUN_END_EVENT,
    RUN_ERROR_EVENT,
    RUN_START_EVENT,
    SUBAGENT_LIFECYCLE_EVENTS,
)

if TYPE_CHECKING:
    from deerflow.runtime.events.store.base import RunEventStore

logger = logging.getLogger(__name__)


class RunJournal(BaseCallbackHandler):
    """LangChain callback handler that captures events to RunEventStore."""

    def __init__(
        self,
        run_id: str,
        thread_id: str,
        event_store: RunEventStore,
        *,
        track_token_usage: bool = True,
        flush_threshold: int = 20,
        progress_flush_every: int = 5,
    ):
        super().__init__()
        self.run_id = run_id
        self.thread_id = thread_id
        self._store = event_store
        self._track_tokens = track_token_usage
        self._flush_threshold = flush_threshold

        # In-flight metering cadence. Token accumulators below live only in
        # memory, and the sole writer used to be the worker's finally block, so
        # the console's token/cost columns read 0 for a run's whole duration and
        # a crash mid-run lost the usage outright.
        #
        # Cadence is per completed LLM call, not per token: on_llm_new_token is
        # deliberately not implemented here, and this deployment is single-worker
        # SQLite, so a write per token would serialize the run behind
        # busy_timeout for no operator benefit. One UPDATE every 5 LLM calls is
        # a handful of writes even on a long agentic run, while bounding what a
        # crash can lose to the last few calls. The first call flushes
        # immediately so a run's cost stops reading 0 as soon as it truly is not.
        self._progress_flush_every = max(1, progress_flush_every)
        self._progress_flushed_at_call = 0

        # Write buffer
        self._buffer: list[dict] = []
        self._pending_flush_tasks: set[asyncio.Task[None]] = set()

        # Token accumulators
        self._total_input_tokens = 0
        self._total_output_tokens = 0
        self._total_tokens = 0
        self._llm_call_count = 0

        # Caller-bucketed token accumulators
        self._lead_agent_tokens = 0
        self._subagent_tokens = 0
        self._middleware_tokens = 0

        # Per-model token accumulator, persisted to RunRow.token_usage_by_model.
        # The operations console prices runs off this map: a run that spans
        # several models (lead + subagents on different providers) cannot be
        # costed from the single run-level model_name.
        self._tokens_by_model: dict[str, dict[str, int]] = {}

        # Dedup: LangChain may fire on_llm_end multiple times for the same run_id
        self._counted_llm_run_ids: set[str] = set()
        self._counted_external_source_ids: set[str] = set()
        # Separate from _counted_llm_run_ids: token accumulation skips a repeat
        # callback only when it carried usage, while the message summary must
        # skip every repeat regardless of whether usage was reported.
        self._counted_message_llm_run_ids: set[str] = set()

        # Convenience fields
        self._last_ai_msg: str | None = None
        self._first_human_msg: str | None = None
        self._msg_count = 0

        # P1-9: set when the final assistant message carries the LLM-fallback
        # marker, i.e. the graph terminated cleanly only because the error
        # middleware substituted a message for a failed provider call. Such a run
        # must not be journaled as a success.
        self.had_llm_error_fallback = False
        self.llm_error_fallback_message: str | None = None

        # Latency tracking
        self._llm_start_times: dict[str, float] = {}  # langchain run_id -> start time

        # LLM request/response tracking
        self._llm_call_index = 0
        self._seen_llm_starts: set[str] = set()  # langchain run_ids that fired on_chat_model_start

    # -- Lifecycle callbacks --

    @staticmethod
    def _message_text(message: BaseMessage) -> str:
        """Extract displayable text from a message's mixed content shape.

        ``content`` is a plain string for most providers but a list of blocks
        for multimodal / thinking-enabled responses. ``str(content)`` on a list
        would store a Python repr in the console's answer preview.
        """
        content = getattr(message, "content", None)
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if isinstance(block, str):
                    parts.append(block)
                elif isinstance(block, Mapping):
                    text = block.get("text")
                    if isinstance(text, str):
                        parts.append(text)
            return "".join(parts)
        if isinstance(content, Mapping):
            for key in ("text", "content"):
                value = content.get(key)
                if isinstance(value, str):
                    return value
        return ""

    def _record_message_summary(self, message: BaseMessage, *, caller: str | None = None) -> None:
        """Update the run-level convenience fields persisted onto RunRow.

        ``message_count`` and ``last_ai_message`` back the operations console's
        message column and its answer preview. Both had no writer at all, so
        every run reported 0 messages and a null preview.

        ``last_ai_message`` must be the lead agent's user-facing answer:
        subagent and middleware (summarization / title) model calls, and
        tool-call-only AI messages with no text, must not overwrite it.
        """
        self._msg_count += 1

        is_ai_message = isinstance(message, AIMessage) or getattr(message, "type", None) == "ai"
        if is_ai_message and (caller is None or caller == "lead_agent"):
            text = self._message_text(message).strip()
            if text:
                self._last_ai_msg = text[:2000]

    def on_chain_start(
        self,
        serialized: dict[str, Any],
        inputs: dict[str, Any],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        caller = self._identify_caller(tags)
        if parent_run_id is None:
            # Root graph invocation — emit a single trace event for the run start.
            chain_name = (serialized or {}).get("name", "unknown")
            self._put(
                event_type=RUN_START_EVENT.event_type,
                category=RUN_START_EVENT.category,
                content={"chain": chain_name},
                metadata={"caller": caller, **(metadata or {})},
            )

    def on_chain_end(self, outputs: Any, *, run_id: UUID, **kwargs: Any) -> None:
        # P1-9: clean graph termination is not task success — the LLM-error
        # middleware may have substituted a fallback AIMessage for a failed
        # provider call. Detect the marker on the FINAL assistant message and
        # route the run onto the error path so it is not journaled as a success.
        status = "error" if self._detect_llm_error_fallback(outputs) else "success"
        self._put(event_type=RUN_END_EVENT.event_type, category=RUN_END_EVENT.category, content=outputs, metadata={"status": status})
        self._flush_sync()

    def _detect_llm_error_fallback(self, outputs: Any) -> bool:
        """True iff the TERMINAL message in ``outputs`` is the LLM-fallback marker.

        A synthesized fallback is the graph's final output, so it is the last
        message of the run. Only that terminal message is authoritative:

        - Scanning for "the last AI message" reaches into full thread history and
          would flag a PRIOR run's marker when the current run ended via
          interrupt/HITL with no new AI of its own (C4).
        - A marker followed by a real answer is a recovered run (retry succeeded),
          not a failure — the real answer is the terminal message, so it wins.
        - A trailing non-AI message (Tool/Human) means the run did not terminate
          on a fallback, regardless of what precedes it.

        ``on_llm_end`` never fires for a synthesized fallback (it never reached
        the model), so this is the only detection point (verified against a real
        create_agent graph before choosing the hook).
        """
        # Reset each detection so a True set by an earlier chain end in this run
        # cannot residue into the root chain end (C4 part 2: non-Mapping/empty
        # branches previously returned without clearing the flag).
        self.had_llm_error_fallback = False
        self.llm_error_fallback_message = None
        # Canonical: deerflow.agents.middlewares.llm_error_handling_middleware.FALLBACK_MARKER_KEY.
        # Duplicated here to keep runtime/ from depending up into agents/; the test
        # suite builds fallback state with that constant, so any drift fails loudly.
        marker_key = "deerflow_error_fallback"
        if not isinstance(outputs, Mapping):
            return False
        messages = outputs.get("messages") or []
        if not messages:
            return False
        last = messages[-1]
        if isinstance(last, AIMessage):
            additional = last.additional_kwargs
        elif isinstance(last, Mapping) and last.get("type") == "ai":
            additional = last.get("additional_kwargs") or {}
        else:
            return False
        if additional.get(marker_key) is True:
            self.had_llm_error_fallback = True
            self.llm_error_fallback_message = additional.get("error_detail") or additional.get("error_reason")
            return True
        return False

    def on_chain_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        self._put(
            event_type=RUN_ERROR_EVENT.event_type,
            category=RUN_ERROR_EVENT.category,
            content=str(error),
            metadata={"error_type": type(error).__name__},
        )
        self._flush_sync()

    # -- LLM callbacks --

    def on_chat_model_start(
        self,
        serialized: dict,
        messages: list[list[BaseMessage]],
        *,
        run_id: UUID,
        tags: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        """Capture structured prompt messages for llm_request event.

        This is also the canonical place to extract the first human message:
        messages are fully structured here, it fires only on real LLM calls,
        and the content is never compressed by checkpoint trimming.
        """
        rid = str(run_id)
        self._llm_start_times[rid] = time.monotonic()
        self._llm_call_index += 1
        self._seen_llm_starts.add(rid)

        logger.debug(
            "on_chat_model_start %s: tags=%s num_batches=%d message_counts=%s",
            run_id,
            tags,
            len(messages),
            [len(batch) for batch in messages],
        )

        # Capture the first human message sent to any LLM in this run.
        if not self._first_human_msg and messages:
            for batch in reversed(messages):
                for m in reversed(batch):
                    # Hidden human messages (D-3 reminders, todo reminders,
                    # view-image contexts) are not user input — the reversed
                    # scan would otherwise latch onto one of them and persist
                    # internal state as this run's first_human_message.
                    if isinstance(m, HumanMessage) and m.name != "summary" and not (m.additional_kwargs or {}).get("hide_from_ui"):
                        caller = self._identify_caller(tags)
                        self.set_first_human_message(m.text)
                        self._put(
                            event_type=LLM_HUMAN_INPUT_EVENT.event_type,
                            category=LLM_HUMAN_INPUT_EVENT.category,
                            content=m.model_dump(),
                            metadata={"caller": caller},
                        )
                        self._record_message_summary(m, caller=caller)
                        break
                if self._first_human_msg:
                    break

    def on_llm_start(self, serialized: dict, prompts: list[str], *, run_id: UUID, parent_run_id: UUID | None = None, tags: list[str] | None = None, metadata: dict[str, Any] | None = None, **kwargs: Any) -> None:
        # Fallback: on_chat_model_start is preferred. This just tracks latency.
        self._llm_start_times[str(run_id)] = time.monotonic()

    def on_llm_end(
        self,
        response: Any,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        messages: list[AnyMessage] = []
        logger.debug("on_llm_end %s: tags=%s", run_id, tags)
        for generation in response.generations:
            for gen in generation:
                if hasattr(gen, "message"):
                    messages.append(gen.message)
                else:
                    logger.warning(f"on_llm_end {run_id}: generation has no message attribute: {gen}")

        for message in messages:
            caller = self._identify_caller(tags)

            # Latency
            rid = str(run_id)
            start = self._llm_start_times.pop(rid, None)
            latency_ms = int((time.monotonic() - start) * 1000) if start else None

            # Token usage from message
            usage = getattr(message, "usage_metadata", None)
            usage_dict = dict(usage) if usage else {}

            # Resolve call index
            call_index = self._llm_call_index
            if rid not in self._seen_llm_starts:
                # Fallback: on_chat_model_start was not called
                self._llm_call_index += 1
                call_index = self._llm_call_index
                self._seen_llm_starts.add(rid)

            # Trace event: llm_response (OpenAI completion format)
            self._put(
                event_type=LLM_AI_RESPONSE_EVENT.event_type,
                category=LLM_AI_RESPONSE_EVENT.category,
                content=message.model_dump(),
                metadata={
                    "caller": caller,
                    "usage": usage_dict,
                    "latency_ms": latency_ms,
                    "llm_call_index": call_index,
                },
            )
            if rid not in self._counted_message_llm_run_ids:
                self._record_message_summary(message, caller=caller)

            # Token accumulation (dedup by langchain run_id to avoid double-counting
            # when the callback fires more than once for the same response)
            if self._track_tokens:
                input_tk = usage_dict.get("input_tokens", 0) or 0
                output_tk = usage_dict.get("output_tokens", 0) or 0
                total_tk = usage_dict.get("total_tokens", 0) or 0
                if total_tk == 0:
                    total_tk = input_tk + output_tk
                if total_tk > 0 and rid not in self._counted_llm_run_ids:
                    self._counted_llm_run_ids.add(rid)
                    self._total_input_tokens += input_tk
                    self._total_output_tokens += output_tk
                    self._total_tokens += total_tk
                    self._llm_call_count += 1

                    if caller.startswith("subagent:"):
                        self._subagent_tokens += total_tk
                    elif caller.startswith("middleware:"):
                        self._middleware_tokens += total_tk
                    else:
                        self._lead_agent_tokens += total_tk

                    # Per-model bucket — the provider-reported name, not the
                    # config name, because that is what actually served the call.
                    response_metadata = getattr(message, "response_metadata", None) or {}
                    per_call_model: str | None = None
                    if isinstance(response_metadata, Mapping):
                        per_call_model = response_metadata.get("model_name") or response_metadata.get("model")
                    # B1a: extract the gateway's real-bill amount (360 returns
                    # it in usage.amount as a string like "0.001174").  Two-level
                    # read: response_metadata["amount"] (B1b streaming patch)
                    # → response_metadata["token_usage"]["amount"] (non-streaming).
                    amount = self._extract_amount(response_metadata)
                    self._record_model_usage(per_call_model, input_tk, output_tk, total_tk, self._extract_cache_read(usage_dict), amount=amount)

        if messages:
            self._counted_message_llm_run_ids.add(str(run_id))

    def on_llm_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        self._llm_start_times.pop(str(run_id), None)
        self._put(event_type=LLM_ERROR_EVENT.event_type, category=LLM_ERROR_EVENT.category, content=str(error))

    def record_subagent_lifecycle(
        self,
        task_id: str,
        subagent_type: str,
        event: str,
        *,
        status: str | None = None,
        stop_reason: str | None = None,
        truncated: bool | None = None,
        error: str | None = None,
    ) -> None:
        """Persist a subagent ``task`` lifecycle point as a run_events row (P3-2, 2026-09-29).

        The SSE stream only carries these to the live client; rows written here
        keep the lifecycle (and its stop_reason / truncated honesty signals)
        queryable after the fact. ``event`` is the SSE event type
        ("task_started", "task_running", ...); unknown values are skipped with
        a warning rather than raising, so a journal call can never break the
        SSE main path.
        """
        definition = SUBAGENT_LIFECYCLE_EVENTS.get(event)
        if definition is None:
            logger.warning("record_subagent_lifecycle: unknown subagent event type %r (task_id=%s)", event, task_id)
            return
        content: dict = {"type": event, "task_id": task_id, "subagent_type": subagent_type}
        if status is not None:
            content["status"] = status
        if stop_reason is not None:
            content["stop_reason"] = stop_reason
        if truncated:
            content["truncated"] = True
        if error is not None:
            content["error"] = error
        self._put(
            event_type=definition.event_type,
            category=definition.category,
            content=content,
            metadata={"task_id": task_id, "subagent_type": subagent_type},
        )

    def on_tool_start(self, serialized, input_str, *, run_id, parent_run_id=None, tags=None, metadata=None, inputs=None, **kwargs):
        """Handle tool start event, cache tool call ID for later correlation"""
        tool_call_id = str(run_id)
        logger.debug("Tool start for node %s, tool_call_id=%s, tags=%s", run_id, tool_call_id, tags)

    def on_tool_end(self, output, *, run_id, parent_run_id=None, **kwargs):
        """Handle tool end event, append message and clear node data"""
        try:
            if isinstance(output, ToolMessage):
                msg = cast(ToolMessage, output)
                self._put(event_type=LLM_TOOL_RESULT_EVENT.event_type, category=LLM_TOOL_RESULT_EVENT.category, content=msg.model_dump())
                self._record_message_summary(msg)
            elif isinstance(output, Command):
                cmd = cast(Command, output)
                messages = cmd.update.get("messages", [])
                for message in messages:
                    if isinstance(message, BaseMessage):
                        self._put(event_type=LLM_TOOL_RESULT_EVENT.event_type, category=LLM_TOOL_RESULT_EVENT.category, content=message.model_dump())
                        self._record_message_summary(message)
                    else:
                        logger.warning(f"on_tool_end {run_id}: command update message is not BaseMessage: {type(message)}")
            else:
                logger.warning(f"on_tool_end {run_id}: output is not ToolMessage: {type(output)}")
        finally:
            logger.debug("Tool end for node %s", run_id)

    # -- Internal methods --

    def _put(self, *, event_type: str, category: str, content: str | dict = "", metadata: dict | None = None) -> None:
        # Defensive: coerce anything that is neither str, dict, nor list into a
        # JSON string so it can never poison a put_batch transaction. db.py now
        # serializes dict|list; this catches stray dataclasses/objects/tuples
        # that would otherwise bind as an unsupported type and roll back the batch.
        if content is not None and not isinstance(content, (str, dict, list)):
            content = json.dumps(content, default=str, ensure_ascii=False)
        self._buffer.append(
            {
                "thread_id": self.thread_id,
                "run_id": self.run_id,
                "event_type": event_type,
                "category": category,
                "content": content,
                "metadata": metadata or {},
                "created_at": datetime.now(UTC).isoformat(),
            }
        )
        if len(self._buffer) >= self._flush_threshold:
            self._flush_sync()

    def _flush_sync(self) -> None:
        """Best-effort flush of buffer to RunEventStore.

        BaseCallbackHandler methods are synchronous.  If an event loop is
        running we schedule an async ``put_batch``; otherwise the events
        stay in the buffer and are flushed later by the async ``flush()``
        call in the worker's ``finally`` block.
        """
        if not self._buffer:
            return
        # Skip if a flush is already in flight — avoids concurrent writes
        # to the same SQLite file from multiple fire-and-forget tasks.
        if self._pending_flush_tasks:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No event loop — keep events in buffer for later async flush.
            return
        batch = self._buffer.copy()
        self._buffer.clear()
        task = loop.create_task(self._flush_async(batch))
        self._pending_flush_tasks.add(task)
        task.add_done_callback(self._on_flush_done)

    async def _flush_async(self, batch: list[dict]) -> None:
        try:
            await self._store.put_batch(batch)
        except Exception:
            logger.warning(
                "Failed to flush %d events for run %s — returning to buffer",
                len(batch),
                self.run_id,
                exc_info=True,
            )
            # Return failed events to buffer for retry on next flush
            self._buffer = batch + self._buffer

    def _on_flush_done(self, task: asyncio.Task) -> None:
        self._pending_flush_tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc:
            logger.warning("Journal flush task failed: %s", exc)

    def _identify_caller(self, tags: list[str] | None) -> str:
        _tags = tags or []
        for tag in _tags:
            if isinstance(tag, str) and (tag.startswith("subagent:") or tag.startswith("middleware:") or tag == "lead_agent"):
                return tag
        # Default to lead_agent: the main agent graph does not inject
        # callback tags, while subagents and middleware explicitly tag
        # themselves.
        return "lead_agent"

    def _record_model_usage(
        self,
        model_name: str | None,
        input_tokens: int,
        output_tokens: int,
        total_tokens: int,
        cache_read_tokens: int = 0,
        amount: float | None = None,
    ) -> None:
        """Add one LLM call's token usage to the per-model accumulator.

        A missing / empty ``model_name`` collapses into a shared ``"unknown"``
        bucket so the breakdown stays usable when a provider does not surface
        ``response_metadata.model_name`` (the 360 gateway sometimes does not).

        ``cache_read_tokens`` is written as a sparse key — only when non-zero —
        so buckets from providers without cache reporting keep their historical
        shape and the console's cache-aware pricing can tell "no hits" from
        "provider does not report hits".

        ``amount`` (B1a) is the gateway's real-bill amount, written as a sparse
        key only when non-None.  Same sparse-key convention as cache_read_tokens.
        """
        if total_tokens <= 0:
            return
        bucket = self._tokens_by_model.setdefault(
            model_name or "unknown",
            {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
        )
        bucket["input_tokens"] += int(input_tokens or 0)
        bucket["output_tokens"] += int(output_tokens or 0)
        bucket["total_tokens"] += int(total_tokens)
        if cache_read_tokens > 0:
            bucket["cache_read_tokens"] = bucket.get("cache_read_tokens", 0) + int(cache_read_tokens)
        if amount is not None:
            bucket["amount"] = bucket.get("amount", 0.0) + float(amount)

    @staticmethod
    def _extract_amount(response_metadata: Any) -> float | None:
        """Two-level read for the gateway's real-bill amount (B1a/B1b).

        - First checks ``response_metadata["amount"]`` — the slot that
          ``PatchedChatOpenAI._convert_chunk_to_generation_chunk`` (B1b)
          writes for streaming responses.
        - Falls back to ``response_metadata["token_usage"]["amount"]`` —
          the non-streaming path where langchain-openai flattens the
          raw ``llm_output["token_usage"]`` dict into response_metadata.

        The 360 gateway returns amount as a **string** (e.g. ``"0.001174"``),
        so ``float()`` conversion is required.  Returns ``None`` on any
        failure so the sparse-key write is skipped.
        """
        try:
            val = response_metadata.get("amount") if isinstance(response_metadata, Mapping) else None
            if val is None:
                token_usage = response_metadata.get("token_usage") if isinstance(response_metadata, Mapping) else None
                if isinstance(token_usage, Mapping):
                    val = token_usage.get("amount")
            return float(val) if val is not None else None
        except (TypeError, ValueError):
            logger.debug("journal: failed to extract amount from response_metadata", exc_info=True)
            return None

    @staticmethod
    def _extract_cache_read(usage_dict: dict) -> int:
        """Prompt-cache-hit input tokens from LangChain's normalized usage."""
        details = usage_dict.get("input_token_details") or {}
        if not isinstance(details, Mapping):
            return 0
        try:
            return max(int(details.get("cache_read") or 0), 0)
        except (TypeError, ValueError):
            return 0

    # -- Public methods (called by worker) --

    def record_external_llm_usage_records(
        self,
        records: list[dict[str, int | str]],
    ) -> None:
        """Record token usage from external sources (e.g., subagents).

        Each record should contain:
            source_run_id: Unique identifier to prevent double-counting
            caller: Caller tag (e.g. "subagent:general-purpose")
            input_tokens: Input token count
            output_tokens: Output token count
            total_tokens: Total token count (computed from input+output if 0/missing)
            model_name: Optional provider-reported model that served the call.
                Absent for older collectors — those land in the "unknown"
                bucket rather than being dropped from the breakdown.
        """
        if not self._track_tokens:
            return
        for record in records:
            source_id = str(record.get("source_run_id", ""))
            if not source_id:
                continue
            if source_id in self._counted_external_source_ids:
                continue

            total_tk = record.get("total_tokens", 0) or 0
            if total_tk <= 0:
                input_tk = record.get("input_tokens", 0) or 0
                output_tk = record.get("output_tokens", 0) or 0
                total_tk = input_tk + output_tk
            if total_tk <= 0:
                continue

            self._counted_external_source_ids.add(source_id)
            self._total_input_tokens += record.get("input_tokens", 0) or 0
            self._total_output_tokens += record.get("output_tokens", 0) or 0
            self._total_tokens += total_tk

            caller = str(record.get("caller", ""))
            if caller.startswith("subagent:"):
                self._subagent_tokens += total_tk
            elif caller.startswith("middleware:"):
                self._middleware_tokens += total_tk
            else:
                self._lead_agent_tokens += total_tk

            raw_model = record.get("model_name")
            self._record_model_usage(
                str(raw_model) if raw_model else None,
                int(record.get("input_tokens", 0) or 0),
                int(record.get("output_tokens", 0) or 0),
                int(total_tk),
                int(record.get("cache_read_tokens", 0) or 0),
            )

    def set_first_human_message(self, content: str) -> None:
        """Record the first human message for convenience fields."""
        self._first_human_msg = content[:2000] if content else None

    async def flush(self) -> None:
        """Force flush remaining buffer. Called in worker's finally block."""
        if self._pending_flush_tasks:
            await asyncio.gather(*tuple(self._pending_flush_tasks), return_exceptions=True)

        while self._buffer:
            batch = self._buffer[: self._flush_threshold]
            del self._buffer[: self._flush_threshold]
            try:
                await self._store.put_batch(batch)
            except Exception:
                self._buffer = batch + self._buffer
                raise

    def primary_model_name(self) -> str | None:
        """The model that consumed the most tokens in this run, if known.

        ``RunRow.model_name`` had no writer at all before this — the three
        stores' ``update_model_name`` methods existed with zero callers, so
        every historical row has ``model_name IS NULL``. That left the
        operations console's model column blank and disabled its legacy
        run-level pricing fallback. The journal already sees the real
        provider-reported name on every call, so it is the natural writer.

        "Primary" = the largest bucket rather than the first call, so a run
        whose subagents did most of the work is attributed to the model that
        actually did it. The full split stays in ``token_usage_by_model``;
        this is only the single-value summary the row can hold.
        """
        if not self._tokens_by_model:
            return None
        known = {model: usage for model, usage in self._tokens_by_model.items() if model != "unknown"}
        candidates = known or self._tokens_by_model
        return max(candidates.items(), key=lambda kv: kv[1].get("total_tokens", 0))[0]

    def progress_flush_due(self) -> bool:
        """Whether enough LLM calls have landed to justify an in-flight write.

        Gated on ``_llm_call_count`` rather than elapsed time so a run that is
        waiting on a slow tool does not rewrite the same numbers, and so the
        cadence is deterministic in tests.
        """
        if not self._track_tokens or self._llm_call_count == 0:
            return False
        if self._progress_flushed_at_call == 0:
            # First priced call of the run: write straight away so the console
            # stops reading 0 the moment real spend exists.
            return True
        return self._llm_call_count - self._progress_flushed_at_call >= self._progress_flush_every

    def mark_progress_flushed(self) -> None:
        """Record that the current accumulator state has been persisted."""
        self._progress_flushed_at_call = self._llm_call_count

    def get_progress_data(self) -> dict:
        """Return an in-flight snapshot for ``update_run_progress``.

        Every value is the *accumulated* total, not a delta since the last
        flush, so repeated writes are idempotent and the final completion write
        cannot double-count what a progress write already stored. Deliberately
        omits ``last_ai_message`` / ``first_human_message``: a mid-run partial
        answer must not be persisted as the run's final answer preview.
        """
        return {
            "total_input_tokens": self._total_input_tokens,
            "total_output_tokens": self._total_output_tokens,
            "total_tokens": self._total_tokens,
            "llm_call_count": self._llm_call_count,
            "lead_agent_tokens": self._lead_agent_tokens,
            "subagent_tokens": self._subagent_tokens,
            "middleware_tokens": self._middleware_tokens,
            "token_usage_by_model": {model: dict(usage) for model, usage in self._tokens_by_model.items()},
            "message_count": self._msg_count,
        }

    def get_completion_data(self) -> dict:
        """Return accumulated token and message data for run completion."""
        return {
            "total_input_tokens": self._total_input_tokens,
            "total_output_tokens": self._total_output_tokens,
            "total_tokens": self._total_tokens,
            "llm_call_count": self._llm_call_count,
            "lead_agent_tokens": self._lead_agent_tokens,
            "subagent_tokens": self._subagent_tokens,
            "middleware_tokens": self._middleware_tokens,
            "token_usage_by_model": {model: dict(usage) for model, usage in self._tokens_by_model.items()},
            "message_count": self._msg_count,
            "last_ai_message": self._last_ai_msg,
            "first_human_message": self._first_human_msg,
            "llm_error_fallback": self.had_llm_error_fallback,
        }
