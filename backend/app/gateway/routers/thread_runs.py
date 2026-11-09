"""Runs endpoints — create, stream, wait, cancel.

Implements the LangGraph Platform runs API on top of
:class:`deerflow.agents.runs.RunManager` and
:class:`deerflow.agents.stream_bridge.StreamBridge`.

SSE format is aligned with the LangGraph Platform protocol so that
the ``useStream`` React hook from ``@langchain/langgraph-sdk/react``
works without modification.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from copy import deepcopy
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse
from langchain_core.messages import BaseMessage
from pydantic import BaseModel, Field

from app.gateway.authz import require_permission
from app.gateway.checkpoint_lineage import (
    CheckpointLineageError,
    CheckpointParentMissingError,
    checkpoint_configurable,
    checkpoint_messages,
    find_checkpoint_before_message,
    find_checkpoint_before_message_chronologically,
    is_duration_only_checkpoint,
)
from app.gateway.deps import get_checkpointer, get_current_user, get_feedback_repo, get_run_event_store, get_run_manager, get_run_store, get_stream_bridge
from app.gateway.pagination import trim_run_message_page
from app.gateway.services import build_thread_checkpoint_state_accessor, sse_consumer, start_run
from app.gateway.utils import sanitize_log_param
from deerflow.agents.middlewares.dynamic_context_middleware import strip_injected_user_message_id_suffix
from deerflow.runtime import RunRecord, RunStatus, serialize_channel_values_for_api
from deerflow.runtime.checkpointer.prune import REGENERATE_HISTORY_RAW_SCAN_LIMIT, REGENERATE_HISTORY_SCAN_LIMIT
from deerflow.runtime.events.catalog import LLM_AI_RESPONSE_EVENT_TYPES
from deerflow.utils.messages import ORIGINAL_USER_CONTENT_KEY, get_original_user_content_text, message_to_text

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/threads", tags=["runs"])

# Regenerate history-scan window — imported above from
# deerflow.runtime.checkpointer.prune (canonical home) so the retention
# pruner's default stays aligned with this scan window from a single source
# (harness must not import app — tests/test_harness_boundary.py).
_MISSING_REGENERATE_BASE_DETAIL = "Could not find an addressable checkpoint before the target user message"
_UNSAFE_REGENERATE_LINEAGE_DETAIL = "Could not safely resolve the checkpoint before the target user message"


def _is_duration_only_checkpoint(checkpoint_tuple: Any) -> bool:
    return is_duration_only_checkpoint(checkpoint_tuple)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class RunCreateRequest(BaseModel):
    assistant_id: str | None = Field(default=None, description="Agent / assistant to use")
    input: dict[str, Any] | None = Field(default=None, description="Graph input (e.g. {messages: [...]})")
    command: dict[str, Any] | None = Field(default=None, description="LangGraph Command")
    metadata: dict[str, Any] | None = Field(default=None, description="Run metadata")
    config: dict[str, Any] | None = Field(default=None, description="RunnableConfig overrides")
    context: dict[str, Any] | None = Field(default=None, description="DeerFlow context overrides (model_name, thinking_enabled, etc.)")
    webhook: str | None = Field(default=None, description="Completion callback URL")
    checkpoint_id: str | None = Field(default=None, description="Resume from checkpoint")
    checkpoint: dict[str, Any] | None = Field(default=None, description="Full checkpoint object")
    interrupt_before: list[str] | Literal["*"] | None = Field(default=None, description="Nodes to interrupt before")
    interrupt_after: list[str] | Literal["*"] | None = Field(default=None, description="Nodes to interrupt after")
    stream_mode: list[str] | str | None = Field(default=None, description="Stream mode(s)")
    stream_subgraphs: bool = Field(default=False, description="Include subgraph events")
    stream_resumable: bool | None = Field(default=None, description="SSE resumable mode")
    on_disconnect: Literal["cancel", "continue"] = Field(default="cancel", description="Behaviour on SSE disconnect")
    on_completion: Literal["delete", "keep"] = Field(default="keep", description="Delete temp thread on completion")
    multitask_strategy: Literal["reject", "rollback", "interrupt", "enqueue"] = Field(default="reject", description="Concurrency strategy")
    after_seconds: float | None = Field(default=None, description="Delayed execution")
    if_not_exists: Literal["reject", "create"] = Field(default="create", description="Thread creation policy")
    feedback_keys: list[str] | None = Field(default=None, description="LangSmith feedback keys")


class RunResponse(BaseModel):
    run_id: str
    thread_id: str
    assistant_id: str | None = None
    status: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    kwargs: dict[str, Any] = Field(default_factory=dict)
    multitask_strategy: str = "reject"
    created_at: str = ""
    updated_at: str = ""
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_tokens: int = 0
    llm_call_count: int = 0
    lead_agent_tokens: int = 0
    subagent_tokens: int = 0
    middleware_tokens: int = 0
    message_count: int = 0


class RegeneratePrepareRequest(BaseModel):
    message_id: str = Field(..., min_length=1, description="Assistant message id to regenerate")


class RegeneratePrepareResponse(BaseModel):
    input: dict[str, Any]
    checkpoint: dict[str, Any]
    metadata: dict[str, Any]
    target_run_id: str


class EditRegeneratePrepareRequest(BaseModel):
    human_message_id: str = Field(..., min_length=1, description="Source human message id to edit and rerun")
    replacement_text: str = Field(..., min_length=1, description="Replacement user-visible text")


class EditRegeneratePrepareResponse(RegeneratePrepareResponse):
    replacement_human_message_id: str
    source_message_ids: list[str]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _record_to_response(record: RunRecord) -> RunResponse:
    return RunResponse(
        run_id=record.run_id,
        thread_id=record.thread_id,
        assistant_id=record.assistant_id,
        status=record.status.value,
        metadata=record.metadata,
        kwargs=record.kwargs,
        multitask_strategy=record.multitask_strategy,
        created_at=record.created_at,
        updated_at=record.updated_at,
        total_input_tokens=record.total_input_tokens,
        total_output_tokens=record.total_output_tokens,
        total_tokens=record.total_tokens,
        llm_call_count=record.llm_call_count,
        lead_agent_tokens=record.lead_agent_tokens,
        subagent_tokens=record.subagent_tokens,
        middleware_tokens=record.middleware_tokens,
        message_count=record.message_count,
    )


# ---------------------------------------------------------------------------
# Regenerate / edit-regenerate helpers
# ---------------------------------------------------------------------------


def _message_id(message: Any) -> str | None:
    value = getattr(message, "id", None)
    if value is None and isinstance(message, dict):
        value = message.get("id")
    return str(value) if value else None


def _message_type(message: Any) -> str | None:
    value = getattr(message, "type", None)
    if value is None and isinstance(message, dict):
        value = message.get("type") or message.get("role")
    if value == "assistant":
        return "ai"
    return str(value) if value else None


def _message_name(message: Any) -> str | None:
    value = getattr(message, "name", None)
    if value is None and isinstance(message, dict):
        value = message.get("name")
    return str(value) if value else None


def _message_content(message: Any) -> Any:
    if isinstance(message, dict):
        return message.get("content")
    return getattr(message, "content", None)


def _message_text(message: Any) -> str:
    return message_to_text(message)


def _message_additional_kwargs(message: Any) -> dict[str, Any]:
    value = getattr(message, "additional_kwargs", None)
    if value is None and isinstance(message, dict):
        value = message.get("additional_kwargs")
    return dict(value or {}) if isinstance(value, dict) else {}


def _message_tool_calls(message: Any) -> list[Any]:
    value = getattr(message, "tool_calls", None)
    if value is None and isinstance(message, dict):
        value = message.get("tool_calls")
    if value is None:
        value = _message_additional_kwargs(message).get("tool_calls")
    return list(value) if isinstance(value, list) else []


def _is_hidden_or_control_message(message: Any) -> bool:
    message_type = _message_type(message)
    additional_kwargs = _message_additional_kwargs(message)
    return message_type == "remove" or _message_name(message) == "summary" or additional_kwargs.get("hide_from_ui") is True


def _is_visible_human_message(message: Any) -> bool:
    return _message_type(message) == "human" and not _is_hidden_or_control_message(message)


def _is_visible_ai_message(message: Any) -> bool:
    return _message_type(message) == "ai" and not _is_hidden_or_control_message(message)


def _checkpoint_messages(snapshot: Any) -> list[Any]:
    return checkpoint_messages(snapshot)


def _checkpoint_values(snapshot: Any) -> dict[str, Any]:
    values = getattr(snapshot, "values", None)
    return dict(values) if isinstance(values, dict) else {}


def _checkpoint_configurable(checkpoint_tuple: Any) -> dict[str, Any]:
    return checkpoint_configurable(checkpoint_tuple)


def _checkpoint_response(checkpoint_tuple: Any) -> dict[str, Any]:
    configurable = _checkpoint_configurable(checkpoint_tuple)
    checkpoint_id = configurable.get("checkpoint_id")
    if not checkpoint_id:
        raise HTTPException(status_code=409, detail="Checkpoint is missing checkpoint_id")
    return {
        "checkpoint_ns": str(configurable.get("checkpoint_ns") or ""),
        "checkpoint_id": str(checkpoint_id),
        "checkpoint_map": configurable.get("checkpoint_map"),
    }


def _clean_human_message_for_regenerate(message: Any) -> dict[str, Any]:
    additional_kwargs = _message_additional_kwargs(message)
    content = get_original_user_content_text(_message_content(message), additional_kwargs)
    additional_kwargs.pop(ORIGINAL_USER_CONTENT_KEY, None)
    additional_kwargs.pop("hide_from_ui", None)

    clean_message: dict[str, Any] = {
        "type": "human",
        "content": [{"type": "text", "text": content}],
        "additional_kwargs": additional_kwargs,
    }
    # Replay the id the client originally sent. The dynamic-context reminder
    # re-keys the first user message of a thread to `{id}__user`, and replaying
    # that persisted id into a state that has no reminder yet makes the
    # middleware treat the turn as already injected, silently dropping the date
    # and memory block the original turn had.
    message_id = strip_injected_user_message_id_suffix(_message_id(message))
    if message_id:
        clean_message["id"] = message_id
    name = _message_name(message)
    if name:
        clean_message["name"] = name
    return clean_message


def _clean_human_message_for_edit(message: Any, *, replacement_id: str, replacement_text: str) -> dict[str, Any]:
    source_kwargs = _message_additional_kwargs(message)
    additional_kwargs: dict[str, Any] = {}
    for key in ("files", "referenced_message_contexts"):
        if key in source_kwargs:
            additional_kwargs[key] = deepcopy(source_kwargs[key])

    clean_message: dict[str, Any] = {
        "type": "human",
        "id": replacement_id,
        "content": [{"type": "text", "text": replacement_text}],
        "additional_kwargs": additional_kwargs,
    }
    name = _message_name(message)
    if name:
        clean_message["name"] = name
    return clean_message


def _is_terminal_assistant_text_message(message: Any) -> bool:
    return _is_visible_ai_message(message) and bool(_message_text(message).strip()) and not _message_tool_calls(message)


def _has_title(values: dict[str, Any]) -> bool:
    title = values.get("title")
    return isinstance(title, str) and bool(title)


def _has_active_goal(snapshot: Any) -> bool:
    goal = _checkpoint_values(snapshot).get("goal")
    return isinstance(goal, dict) and goal.get("status") == "active"


def _latest_editable_turn(messages: list[Any], human_message_id: str) -> tuple[int, Any, int, Any, list[str]]:
    latest_human_index = next((index for index in range(len(messages) - 1, -1, -1) if _is_visible_human_message(messages[index])), None)
    if latest_human_index is None or _message_id(messages[latest_human_index]) != human_message_id:
        raise HTTPException(status_code=409, detail="Only the latest completed user turn can be edited")

    source_human = messages[latest_human_index]
    last_ai_index: int | None = None
    for index, message in enumerate(messages[latest_human_index + 1 :], start=latest_human_index + 1):
        if _is_visible_human_message(message):
            break
        if _is_visible_ai_message(message):
            last_ai_index = index

    if last_ai_index is None or not _is_terminal_assistant_text_message(messages[last_ai_index]):
        raise HTTPException(status_code=409, detail="Only completed assistant text turns can be edited")

    source_message_ids = [message_id for message in messages[latest_human_index : last_ai_index + 1] if (message_id := _message_id(message))]
    return latest_human_index, source_human, last_ai_index, messages[last_ai_index], source_message_ids


def _event_message_id(row: dict[str, Any]) -> str | None:
    content = row.get("content")
    if isinstance(content, BaseMessage):
        return _message_id(content)
    if isinstance(content, dict):
        return _message_id(content)
    return None


def _run_last_ai_matches_message(record: RunRecord, message: Any) -> bool:
    last_ai_message = (record.last_ai_message or "").strip()
    if not last_ai_message:
        return False
    target_text = _message_text(message).strip()
    if not target_text:
        return False
    return last_ai_message == target_text[: len(last_ai_message)]


async def _find_target_run_id(
    thread_id: str,
    message_id: str,
    target_message: Any,
    source_human: Any,
    request: Request,
) -> str:
    event_store = get_run_event_store(request)
    rows = await event_store.list_messages(thread_id, limit=REGENERATE_HISTORY_SCAN_LIMIT)
    for row in reversed(rows):
        if row.get("event_type") not in set(LLM_AI_RESPONSE_EVENT_TYPES):
            continue
        if _event_message_id(row) == message_id:
            run_id = row.get("run_id")
            if isinstance(run_id, str) and run_id:
                return run_id

    source_run_id = _message_additional_kwargs(source_human).get("run_id")
    if isinstance(source_run_id, str) and source_run_id:
        return source_run_id

    run_mgr = get_run_manager(request)
    user_id = await get_current_user(request)
    records = await run_mgr.list_by_thread(thread_id, user_id=user_id, limit=10)
    fallback_record = next(
        (record for record in records if record.status == RunStatus.success and _run_last_ai_matches_message(record, target_message)),
        None,
    )
    if fallback_record is not None:
        return fallback_record.run_id
    if len(rows) >= REGENERATE_HISTORY_SCAN_LIMIT:
        logger.warning(
            "Could not find source run for regenerate message %s in recent run events for thread %s (limit=%s)",
            message_id,
            thread_id,
            REGENERATE_HISTORY_SCAN_LIMIT,
        )
    raise HTTPException(status_code=409, detail="Could not find source run for assistant message")


async def _find_base_checkpoint_before_human(
    thread_id: str,
    human_message_id: str,
    request: Request,
    *,
    head_checkpoint: Any | None = None,
) -> Any:
    accessor, base_config = await build_thread_checkpoint_state_accessor(request, thread_id=thread_id)
    if head_checkpoint is not None:
        try:
            return await find_checkpoint_before_message(
                accessor,
                head_checkpoint,
                human_message_id,
                max_depth=REGENERATE_HISTORY_RAW_SCAN_LIMIT,
            )
        except CheckpointParentMissingError:
            # Old checkpoints and imported histories may not have parent links.
            # Preserve the bounded chronological fallback for those records.
            logger.debug(
                "Could not resolve parent lineage for regenerate thread %s; falling back to history scan",
                sanitize_log_param(thread_id),
                exc_info=True,
            )
        except CheckpointLineageError as exc:
            logger.warning(
                "Rejected unsafe checkpoint lineage for regenerate thread %s",
                sanitize_log_param(thread_id),
                exc_info=True,
            )
            raise HTTPException(status_code=409, detail=_UNSAFE_REGENERATE_LINEAGE_DETAIL) from exc
    try:
        raw_checkpoints = await accessor.ahistory(base_config, limit=REGENERATE_HISTORY_RAW_SCAN_LIMIT)
    except Exception as exc:
        logger.exception("Failed to list checkpoints for regenerate thread %s", thread_id)
        raise HTTPException(status_code=500, detail="Failed to inspect checkpoint history") from exc

    previous_checkpoint, target_found = find_checkpoint_before_message_chronologically(raw_checkpoints, human_message_id)
    if target_found:
        if previous_checkpoint is None:
            raise HTTPException(
                status_code=409,
                detail=_MISSING_REGENERATE_BASE_DETAIL,
            )
        return previous_checkpoint

    if len(raw_checkpoints) >= REGENERATE_HISTORY_RAW_SCAN_LIMIT:
        logger.warning(
            "Could not locate target user message %s in recent checkpoint history for thread %s (limit=%s)",
            human_message_id,
            thread_id,
            REGENERATE_HISTORY_RAW_SCAN_LIMIT,
        )
    raise HTTPException(
        status_code=409,
        detail=(f"Could not locate target user message in recent checkpoint history (limit={REGENERATE_HISTORY_SCAN_LIMIT})"),
    )


def _run_status_value(record: Any) -> str | None:
    status = getattr(record, "status", None)
    if isinstance(status, RunStatus):
        return status.value
    return str(status) if status is not None else None


async def _require_successful_source_run(thread_id: str, run_id: str, request: Request) -> RunRecord:
    run_mgr = get_run_manager(request)
    user_id = await get_current_user(request)
    record = await run_mgr.get(run_id, user_id=user_id)
    if record is None:
        # The run-event journal is the authoritative lookup above. This fallback
        # only covers recent in-memory/store hydration gaps for the latest turn.
        records = await run_mgr.list_by_thread(thread_id, user_id=user_id, limit=20)
        record = next((candidate for candidate in records if getattr(candidate, "run_id", None) == run_id), None)
    if record is None:
        raise HTTPException(status_code=409, detail="Could not find source run for assistant message")
    record_thread_id = getattr(record, "thread_id", None)
    if isinstance(record_thread_id, str) and record_thread_id and record_thread_id != thread_id:
        raise HTTPException(status_code=409, detail="Could not find source run for assistant message")
    if _run_status_value(record) != RunStatus.success.value:
        raise HTTPException(status_code=409, detail="Only successful assistant runs can be edited and rerun")
    return record


async def _find_interrupted_target_run_id(
    thread_id: str,
    source_human: Any,
    request: Request,
) -> str | None:
    source_run_id = _message_additional_kwargs(source_human).get("run_id")
    if not isinstance(source_run_id, str) or not source_run_id:
        return None

    run_mgr = get_run_manager(request)
    user_id = await get_current_user(request)
    record = await run_mgr.get(source_run_id, user_id=user_id)
    if record is None:
        records = await run_mgr.list_by_thread(thread_id, user_id=user_id, limit=20)
        record = next(
            (candidate for candidate in records if getattr(candidate, "run_id", None) == source_run_id),
            None,
        )
    if record is None:
        return None
    if getattr(record, "thread_id", None) != thread_id:
        return None
    if _run_status_value(record) != RunStatus.interrupted.value:
        return None
    return source_run_id


async def _prepare_regenerate_payload(thread_id: str, message_id: str, request: Request) -> RegeneratePrepareResponse:
    accessor, latest_config = await build_thread_checkpoint_state_accessor(request, thread_id=thread_id)
    try:
        latest_checkpoint = await accessor.aget(latest_config)
    except Exception as exc:
        logger.exception("Failed to read latest checkpoint for regenerate thread %s", thread_id)
        raise HTTPException(status_code=500, detail="Failed to read latest checkpoint") from exc
    latest_checkpoint_id = _checkpoint_configurable(latest_checkpoint).get("checkpoint_id")
    if not latest_checkpoint_id:
        raise HTTPException(status_code=404, detail=f"Thread {thread_id} has no checkpoint")

    messages = _checkpoint_messages(latest_checkpoint)
    target_index = next((i for i, message in enumerate(messages) if _message_id(message) == message_id), None)
    if target_index is None:
        # A response interrupted during an LLM call can be visible in the live
        # stream without ever reaching a checkpoint. The server-stamped run ID
        # on the latest user message is the durable link to that partial turn.
        previous_human = next(
            (message for message in reversed(messages) if _is_visible_human_message(message)),
            None,
        )
        target_run_id = await _find_interrupted_target_run_id(thread_id, previous_human, request) if previous_human is not None else None
        if target_run_id is None:
            raise HTTPException(status_code=404, detail=f"Message {message_id} not found")
    else:
        target_message = messages[target_index]
        if not _is_visible_ai_message(target_message):
            raise HTTPException(status_code=409, detail="Only visible assistant messages can be regenerated")

        latest_visible_ai = next((message for message in reversed(messages) if _is_visible_ai_message(message)), None)
        if _message_id(latest_visible_ai) != message_id:
            raise HTTPException(status_code=409, detail="Only the latest assistant message can be regenerated")

        previous_human = next((message for message in reversed(messages[:target_index]) if _is_visible_human_message(message)), None)
        target_run_id = (
            await _find_target_run_id(
                thread_id,
                message_id,
                target_message,
                previous_human,
                request,
            )
            if previous_human is not None
            else None
        )
    if previous_human is None:
        raise HTTPException(status_code=409, detail="Could not find the user message for this assistant response")
    if target_run_id is None:
        raise HTTPException(status_code=409, detail="Could not find source run for assistant message")
    previous_human_id = _message_id(previous_human)
    if not previous_human_id:
        raise HTTPException(status_code=409, detail="The source user message is missing an id")

    base_checkpoint_tuple = await _find_base_checkpoint_before_human(
        thread_id,
        previous_human_id,
        request,
        head_checkpoint=latest_checkpoint,
    )
    checkpoint = _checkpoint_response(base_checkpoint_tuple)
    metadata = {
        "regenerate_from_message_id": message_id,
        "regenerate_from_run_id": target_run_id,
        "regenerate_checkpoint_id": checkpoint["checkpoint_id"],
    }
    regenerate_input: dict[str, Any] = {"messages": [_clean_human_message_for_regenerate(previous_human)]}
    latest_values = latest_checkpoint.values if isinstance(latest_checkpoint.values, dict) else {}
    latest_title = latest_values.get("title")
    if isinstance(latest_title, str) and latest_title:
        # Regenerate resumes from the checkpoint before the target human turn.
        # That checkpoint can predate a manual rename, so replay the current
        # title as graph input instead of letting checkpoint rollback restore
        # the older automatically generated title (#4457).
        regenerate_input["title"] = latest_title
    return RegeneratePrepareResponse(
        input=regenerate_input,
        checkpoint=checkpoint,
        metadata=metadata,
        target_run_id=target_run_id,
    )


async def _prepare_edit_regenerate_payload(
    thread_id: str,
    human_message_id: str,
    replacement_text: str,
    request: Request,
) -> EditRegeneratePrepareResponse:
    normalized_text = replacement_text.strip()
    if not normalized_text:
        raise HTTPException(status_code=409, detail="Edited message cannot be empty")

    accessor, latest_config = await build_thread_checkpoint_state_accessor(request, thread_id=thread_id)
    try:
        latest_checkpoint = await accessor.aget(latest_config)
    except Exception as exc:
        logger.exception("Failed to read latest checkpoint for edit replay thread %s", thread_id)
        raise HTTPException(status_code=500, detail="Failed to read latest checkpoint") from exc
    latest_checkpoint_id = _checkpoint_configurable(latest_checkpoint).get("checkpoint_id")
    if not latest_checkpoint_id:
        raise HTTPException(status_code=404, detail=f"Thread {thread_id} has no checkpoint")

    messages = _checkpoint_messages(latest_checkpoint)
    if _has_active_goal(latest_checkpoint):
        raise HTTPException(status_code=409, detail="Cannot edit while a goal is active")

    _, source_human, _, source_ai, source_message_ids = _latest_editable_turn(messages, human_message_id)
    source_text = get_original_user_content_text(_message_content(source_human), _message_additional_kwargs(source_human)).strip()
    if normalized_text == source_text:
        raise HTTPException(status_code=409, detail="Edited message is unchanged")

    source_human_id = _message_id(source_human)
    source_ai_id = _message_id(source_ai)
    if not source_human_id:
        raise HTTPException(status_code=409, detail="The source user message is missing an id")
    if not source_ai_id:
        raise HTTPException(status_code=409, detail="The source assistant message is missing an id")

    base_checkpoint_tuple = await _find_base_checkpoint_before_human(
        thread_id,
        source_human_id,
        request,
        head_checkpoint=latest_checkpoint,
    )
    target_run_id = await _find_target_run_id(thread_id, source_ai_id, source_ai, source_human, request)
    source_record = await _require_successful_source_run(thread_id, target_run_id, request)
    checkpoint = _checkpoint_response(base_checkpoint_tuple)
    replacement_human_message_id = str(uuid.uuid4())
    source_metadata = getattr(source_record, "metadata", None) or {}
    existing_group_id = source_metadata.get("edit_version_group_id") if isinstance(source_metadata, dict) else None
    # Reserved for future edit-chain grouping across repeated edits of the same
    # original prompt; current visibility still keys off regenerate_from_run_id.
    edit_version_group_id = existing_group_id if isinstance(existing_group_id, str) and existing_group_id else source_human_id
    metadata = {
        "replay_kind": "edit",
        "regenerate_from_message_id": source_ai_id,
        "regenerate_from_run_id": target_run_id,
        "regenerate_checkpoint_id": checkpoint["checkpoint_id"],
        "edit_from_message_id": source_human_id,
        "edit_message_id": replacement_human_message_id,
        "edit_version_group_id": edit_version_group_id,
    }
    edit_input: dict[str, Any] = {
        "messages": [
            _clean_human_message_for_edit(
                source_human,
                replacement_id=replacement_human_message_id,
                replacement_text=normalized_text,
            )
        ]
    }
    base_values = base_checkpoint_tuple.values if isinstance(getattr(base_checkpoint_tuple, "values", None), dict) else {}
    latest_values = latest_checkpoint.values if isinstance(latest_checkpoint.values, dict) else {}
    latest_title = latest_values.get("title")
    if _has_title(base_values) and isinstance(latest_title, str) and latest_title:
        # The replay base can predate a manual rename, so replay the current
        # title rather than letting checkpoint rollback restore the older one
        # (#4457, the same rollback regenerate already guards against). An
        # untitled base is deliberately left alone: it belongs to a thread the
        # title middleware has not named yet, and pinning the current title
        # there would keep a name generated from the prompt this edit replaced.
        edit_input["title"] = latest_title
    return EditRegeneratePrepareResponse(
        input=edit_input,
        checkpoint=checkpoint,
        metadata=metadata,
        target_run_id=target_run_id,
        replacement_human_message_id=replacement_human_message_id,
        source_message_ids=source_message_ids,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/{thread_id}/runs/regenerate/prepare", response_model=RegeneratePrepareResponse)
@require_permission("runs", "create", owner_check=True, require_existing=True)
async def prepare_regenerate_run(
    thread_id: str,
    body: RegeneratePrepareRequest,
    request: Request,
) -> RegeneratePrepareResponse:
    """Prepare input and checkpoint for regenerating the latest assistant turn."""
    return await _prepare_regenerate_payload(thread_id, body.message_id, request)


@router.post("/{thread_id}/runs/edit-regenerate/prepare", response_model=EditRegeneratePrepareResponse)
@require_permission("runs", "create", owner_check=True, require_existing=True)
async def prepare_edit_regenerate_run(
    thread_id: str,
    body: EditRegeneratePrepareRequest,
    request: Request,
) -> EditRegeneratePrepareResponse:
    """Prepare input and checkpoint for editing then rerunning the latest user turn."""
    return await _prepare_edit_regenerate_payload(thread_id, body.human_message_id, body.replacement_text, request)


@router.post("/{thread_id}/runs", response_model=RunResponse)
@require_permission("runs", "create", owner_check=True, require_existing=True)
async def create_run(thread_id: str, body: RunCreateRequest, request: Request) -> RunResponse:
    """Create a background run (returns immediately)."""
    record = await start_run(body, thread_id, request)
    return _record_to_response(record)


@router.post("/{thread_id}/runs/stream")
@require_permission("runs", "create", owner_check=True, require_existing=True)
async def stream_run(thread_id: str, body: RunCreateRequest, request: Request) -> StreamingResponse:
    """Create a run and stream events via SSE.

    The response includes a ``Content-Location`` header with the run's
    resource URL, matching the LangGraph Platform protocol.  The
    ``useStream`` React hook uses this to extract run metadata.
    """
    bridge = get_stream_bridge(request)
    run_mgr = get_run_manager(request)
    record = await start_run(body, thread_id, request)

    return StreamingResponse(
        sse_consumer(bridge, record, request, run_mgr),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            # LangGraph Platform includes run metadata in this header.
            # The SDK uses a greedy regex to extract the run id from this path,
            # so it must point at the canonical run resource without extra suffixes.
            "Content-Location": f"/api/threads/{thread_id}/runs/{record.run_id}",
        },
    )


@router.post("/{thread_id}/runs/wait", response_model=dict)
@require_permission("runs", "create", owner_check=True, require_existing=True)
async def wait_run(thread_id: str, body: RunCreateRequest, request: Request) -> dict:
    """Create a run and block until it completes, returning the final state."""
    record = await start_run(body, thread_id, request)

    if record.task is not None:
        try:
            await record.task
        except asyncio.CancelledError:
            pass

    checkpointer = get_checkpointer(request)
    config = {"configurable": {"thread_id": thread_id}}
    try:
        checkpoint_tuple = await checkpointer.aget_tuple(config)
        if checkpoint_tuple is not None:
            checkpoint = getattr(checkpoint_tuple, "checkpoint", {}) or {}
            channel_values = checkpoint.get("channel_values", {})
            return serialize_channel_values_for_api(channel_values)
    except Exception:
        logger.exception("Failed to fetch final state for run %s", record.run_id)

    return {"status": record.status.value, "error": record.error}


@router.get("/{thread_id}/runs", response_model=list[RunResponse])
@require_permission("runs", "read", owner_check=True)
async def list_runs(thread_id: str, request: Request) -> list[RunResponse]:
    """List all runs for a thread.

    Runs superseded by a successful regeneration are excluded so the frontend
    (``useThreadRuns`` -> ``useThreadHistory``) does not iterate over them and
    fetch their messages -- which would render "ghost rounds" alongside the
    replacement run.
    """
    run_mgr = get_run_manager(request)
    user_id = await get_current_user(request)
    records = await run_mgr.list_by_thread(thread_id)

    hidden_run_ids = await _default_history_hidden_run_ids(run_mgr, thread_id, user_id=user_id)
    if hidden_run_ids:
        records = [r for r in records if r.run_id not in hidden_run_ids]

    return [_record_to_response(r) for r in records]


@router.get("/{thread_id}/runs/{run_id}", response_model=RunResponse)
@require_permission("runs", "read", owner_check=True)
async def get_run(thread_id: str, run_id: str, request: Request) -> RunResponse:
    """Get details of a specific run."""
    run_mgr = get_run_manager(request)
    record = await run_mgr.get(run_id)
    if record is not None and record.thread_id == thread_id:
        return _record_to_response(record)

    # In-memory lookup failed (multi-worker / post-cleanup / restart).
    # Fall back to the persistent RunStore so completed runs remain queryable.
    run_store = get_run_store(request)
    stored = await run_store.get(run_id)
    if stored is not None and stored.get("thread_id") == thread_id:
        return RunResponse(
            run_id=stored["run_id"],
            thread_id=stored["thread_id"],
            assistant_id=stored.get("assistant_id"),
            status=stored.get("status", "unknown"),
            metadata=stored.get("metadata", {}),
            kwargs=stored.get("kwargs", {}),
            multitask_strategy=stored.get("multitask_strategy", "reject"),
            created_at=stored.get("created_at", ""),
            updated_at=stored.get("updated_at", ""),
        )

    raise HTTPException(status_code=404, detail=f"Run {run_id} not found")


@router.post("/{thread_id}/runs/{run_id}/cancel")
@require_permission("runs", "cancel", owner_check=True, require_existing=True)
async def cancel_run(
    thread_id: str,
    run_id: str,
    request: Request,
    wait: bool = Query(default=False, description="Block until run completes after cancel"),
    action: Literal["interrupt", "rollback"] = Query(default="interrupt", description="Cancel action"),
) -> Response:
    """Cancel a running or pending run.

    - action=interrupt: Stop execution, keep current checkpoint (can be resumed)
    - action=rollback: Stop execution, revert to pre-run checkpoint state
    - wait=true: Block until the run fully stops, return 204
    - wait=false: Return immediately with 202
    """
    run_mgr = get_run_manager(request)
    record = await run_mgr.get(run_id)
    if record is None or record.thread_id != thread_id:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

    cancelled = await run_mgr.cancel(run_id, action=action)
    if not cancelled:
        raise HTTPException(
            status_code=409,
            detail=f"Run {run_id} is not cancellable (status: {record.status.value})",
        )

    if wait and record.task is not None:
        try:
            await record.task
        except asyncio.CancelledError:
            pass
        return Response(status_code=204)

    return Response(status_code=202)


@router.get("/{thread_id}/runs/{run_id}/join")
@require_permission("runs", "read", owner_check=True)
async def join_run(thread_id: str, run_id: str, request: Request) -> StreamingResponse:
    """Join an existing run's SSE stream."""
    bridge = get_stream_bridge(request)
    run_mgr = get_run_manager(request)
    record = await run_mgr.get(run_id)
    if record is None or record.thread_id != thread_id:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

    return StreamingResponse(
        sse_consumer(bridge, record, request, run_mgr),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.api_route("/{thread_id}/runs/{run_id}/stream", methods=["GET", "POST"], response_model=None)
@require_permission("runs", "read", owner_check=True)
async def stream_existing_run(
    thread_id: str,
    run_id: str,
    request: Request,
    action: Literal["interrupt", "rollback"] | None = Query(default=None, description="Cancel action"),
    wait: int = Query(default=0, description="Block until cancelled (1) or return immediately (0)"),
):
    """Join an existing run's SSE stream (GET), or cancel-then-stream (POST).

    The LangGraph SDK's ``joinStream`` and ``useStream`` stop button both use
    ``POST`` to this endpoint.  When ``action=interrupt`` or ``action=rollback``
    is present the run is cancelled first; the response then streams any
    remaining buffered events so the client observes a clean shutdown.
    """
    run_mgr = get_run_manager(request)
    record = await run_mgr.get(run_id)
    if record is None or record.thread_id != thread_id:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

    # Cancel if an action was requested (stop-button / interrupt flow)
    if action is not None:
        cancelled = await run_mgr.cancel(run_id, action=action)
        if cancelled and wait and record.task is not None:
            try:
                await record.task
            except (asyncio.CancelledError, Exception):
                pass
            return Response(status_code=204)

    bridge = get_stream_bridge(request)
    return StreamingResponse(
        sse_consumer(bridge, record, request, run_mgr),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# Messages / Events / Token usage endpoints
# ---------------------------------------------------------------------------


async def _default_history_hidden_run_ids(run_mgr: Any, thread_id: str, *, user_id: str | None) -> set[str]:
    """Return run IDs whose messages should be hidden from thread history.

    A run is hidden when it was superseded by a successful regeneration
    (its ``regenerate_from_run_id`` metadata points to this run and the
    regeneration completed with ``status == success``).
    """
    return await run_mgr.list_successful_regenerate_sources(thread_id, user_id=user_id)


@router.get("/{thread_id}/messages")
@require_permission("runs", "read", owner_check=True)
async def list_thread_messages(
    thread_id: str,
    request: Request,
    limit: int = Query(default=50, le=200),
    before_seq: int | None = Query(default=None),
    after_seq: int | None = Query(default=None),
) -> list[dict]:
    """Return displayable messages for a thread (across all runs), with feedback attached."""
    event_store = get_run_event_store(request)
    user_id = await get_current_user(request)

    # Hide messages from runs superseded by successful regenerations so
    # they don't appear as "ghost rounds" alongside the replacement run.
    # Gracefully handle missing run_manager (e.g. in test apps that only
    # test feedback attachment).
    hidden_run_ids: set[str] = set()
    run_mgr = getattr(request.app.state, "run_manager", None)
    if run_mgr is not None:
        hidden_run_ids = await _default_history_hidden_run_ids(run_mgr, thread_id, user_id=user_id)

    messages = await event_store.list_messages(thread_id, limit=limit, before_seq=before_seq, after_seq=after_seq)
    if hidden_run_ids:
        messages = [m for m in messages if m.get("run_id") not in hidden_run_ids]

    # Attach feedback to the last AI message of each run
    feedback_repo = get_feedback_repo(request)
    feedback_map = await feedback_repo.list_by_thread_grouped(thread_id, user_id=user_id)

    # Find the last AI message per run_id. RunJournal canonically persists
    # these as "llm.ai.response"; "ai_message" only matches rows written
    # before that name became canonical. Matching the legacy string alone
    # (as this did) meant feedback was never attached at all.
    last_ai_per_run: dict[str, int] = {}  # run_id -> index in messages list
    for i, msg in enumerate(messages):
        if msg.get("event_type") in LLM_AI_RESPONSE_EVENT_TYPES:
            last_ai_per_run[msg["run_id"]] = i

    # Attach feedback field
    last_ai_indices = set(last_ai_per_run.values())
    for i, msg in enumerate(messages):
        if i in last_ai_indices:
            run_id = msg["run_id"]
            fb = feedback_map.get(run_id)
            msg["feedback"] = (
                {
                    "feedback_id": fb["feedback_id"],
                    "rating": fb["rating"],
                    "comment": fb.get("comment"),
                }
                if fb
                else None
            )
        else:
            msg["feedback"] = None

    return messages


@router.get("/{thread_id}/runs/{run_id}/messages")
@require_permission("runs", "read", owner_check=True)
async def list_run_messages(
    thread_id: str,
    run_id: str,
    request: Request,
    limit: int = Query(default=50, le=200, ge=1),
    before_seq: int | None = Query(default=None),
    after_seq: int | None = Query(default=None),
) -> dict:
    """Return paginated messages for a specific run.

    Response: { data: [...], has_more: bool }
    """
    event_store = get_run_event_store(request)
    rows = await event_store.list_messages_by_run(
        thread_id,
        run_id,
        limit=limit + 1,
        before_seq=before_seq,
        after_seq=after_seq,
    )
    data, has_more = trim_run_message_page(rows, limit=limit, after_seq=after_seq)
    return {"data": data, "has_more": has_more}


@router.get("/{thread_id}/runs/{run_id}/events")
@require_permission("runs", "read", owner_check=True)
async def list_run_events(
    thread_id: str,
    run_id: str,
    request: Request,
    event_types: str | None = Query(default=None),
    limit: int = Query(default=500, le=2000),
) -> list[dict]:
    """Return the full event stream for a run (debug/audit)."""
    event_store = get_run_event_store(request)
    types = event_types.split(",") if event_types else None
    return await event_store.list_events(thread_id, run_id, event_types=types, limit=limit)


@router.get("/{thread_id}/token-usage")
@require_permission("threads", "read", owner_check=True)
async def thread_token_usage(
    thread_id: str,
    request: Request,
    include_active: bool = Query(default=False, description="Include running run progress snapshots"),
) -> dict:
    """Thread-level token usage aggregation."""
    run_store = get_run_store(request)
    agg = await run_store.aggregate_tokens_by_thread(thread_id, include_active=include_active)
    return {"thread_id": thread_id, **agg}
