"""Stateless runs endpoints -- stream and wait without a pre-existing thread.

These endpoints auto-create a temporary thread when no ``thread_id`` is
supplied in the request body.  When a ``thread_id`` **is** provided, it
is reused so that conversation history is preserved across calls.
"""

from __future__ import annotations

import asyncio
import logging
import uuid

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.gateway.authz import _authenticate, require_permission
from app.gateway.deps import get_checkpointer, get_feedback_repo, get_run_event_store, get_run_manager, get_run_store, get_stream_bridge
from app.gateway.pagination import trim_run_message_page
from app.gateway.routers.thread_runs import RunCreateRequest
from app.gateway.services import sse_consumer, start_run
from app.gateway.utils import sanitize_log_param
from deerflow.runtime import RunStatus, serialize_channel_values_for_api

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/runs", tags=["runs"])


def _supplied_thread_id(body: RunCreateRequest) -> str | None:
    """Return the thread_id explicitly supplied in the body, if any."""
    thread_id = (body.config or {}).get("configurable", {}).get("thread_id")
    return str(thread_id) if thread_id else None


async def _require_thread_access(thread_id: str, request: Request) -> None:
    """SEC-C1 (security plan 2026-09-02 B1): ownership gate for body-supplied thread_ids.

    Same semantics as ``@require_permission(..., owner_check=True)`` on the
    thread-scoped routers: an existing ``threads_meta`` row owned by someone
    else → 404 (no existence leak). A missing row or a NULL owner passes —
    that is the documented auto-create semantics of stateless runs
    (``ThreadMetaStore.check_access(require_existing=False)``). Anonymous
    requests (no-auth deployments, where AuthMiddleware is disabled) have no
    identity to check against and pass through unchanged.
    """
    auth = getattr(request.state, "auth", None)
    if auth is None:
        auth = await _authenticate(request)
        request.state.auth = auth
    user = getattr(auth, "user", None)
    if user is None:
        return

    from app.gateway.deps import get_thread_store

    allowed = await get_thread_store(request).check_access(thread_id, str(user.id), require_existing=False)
    if not allowed:
        logger.warning("Denied stateless run on thread %s not owned by user %s", sanitize_log_param(thread_id), user.id)
        raise HTTPException(status_code=404, detail=f"Thread {thread_id} not found")


@router.post("/stream")
async def stateless_stream(body: RunCreateRequest, request: Request) -> StreamingResponse:
    """Create a run and stream events via SSE.

    If ``config.configurable.thread_id`` is provided, the run is created
    on the given thread so that conversation history is preserved.
    Otherwise a new temporary thread is created.
    """
    supplied = _supplied_thread_id(body)
    if supplied is not None:
        await _require_thread_access(supplied, request)
    thread_id = supplied or str(uuid.uuid4())
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
            "Content-Location": f"/api/threads/{thread_id}/runs/{record.run_id}",
        },
    )


@router.post("/wait", response_model=dict)
async def stateless_wait(body: RunCreateRequest, request: Request) -> dict:
    """Create a run and block until completion.

    If ``config.configurable.thread_id`` is provided, the run is created
    on the given thread so that conversation history is preserved.
    Otherwise a new temporary thread is created.
    """
    supplied = _supplied_thread_id(body)
    if supplied is not None:
        await _require_thread_access(supplied, request)
    thread_id = supplied or str(uuid.uuid4())
    record = await start_run(body, thread_id, request)

    if record.task is not None:
        try:
            await record.task
        except asyncio.CancelledError:
            # R-04 (2026-08-13): do NOT swallow this and then serialize the
            # checkpoint as if the run completed. A cancelled wait (client
            # disconnect or an explicit cancel — which sets record.status to
            # "interrupted" synchronously) must fall through to the
            # status-gated serialization below.
            logger.info("Wait for run %s was cancelled", record.run_id)
        except Exception:
            logger.exception("Run %s task failed", record.run_id)

    checkpointer = get_checkpointer(request)
    config = {"configurable": {"thread_id": thread_id}}
    if record.status == RunStatus.success:
        try:
            checkpoint_tuple = await checkpointer.aget_tuple(config)
            if checkpoint_tuple is not None:
                checkpoint = getattr(checkpoint_tuple, "checkpoint", {}) or {}
                channel_values = checkpoint.get("channel_values", {})
                return serialize_channel_values_for_api(channel_values)
        except Exception:
            logger.exception("Failed to fetch final state for run %s", record.run_id)

    return {"status": record.status.value, "error": record.error}


# ---------------------------------------------------------------------------
# Run-scoped read endpoints
# ---------------------------------------------------------------------------


async def _resolve_run(run_id: str, request: Request) -> dict:
    """Fetch run by run_id with user ownership check. Raises 404 if not found."""
    run_store = get_run_store(request)
    record = await run_store.get(run_id)  # user_id=AUTO filters by contextvar
    if record is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    return record


@router.get("/{run_id}/messages")
@require_permission("runs", "read")
async def run_messages(
    run_id: str,
    request: Request,
    limit: int = Query(default=50, le=200, ge=1),
    before_seq: int | None = Query(default=None),
    after_seq: int | None = Query(default=None),
) -> dict:
    """Return paginated messages for a run (cursor-based).

    Pagination:
    - after_seq: messages with seq > after_seq (forward)
    - before_seq: messages with seq < before_seq (backward)
    - neither: latest messages

    Response: { data: [...], has_more: bool }
    """
    run = await _resolve_run(run_id, request)
    event_store = get_run_event_store(request)
    rows = await event_store.list_messages_by_run(
        run["thread_id"],
        run_id,
        limit=limit + 1,
        before_seq=before_seq,
        after_seq=after_seq,
    )
    data, has_more = trim_run_message_page(rows, limit=limit, after_seq=after_seq)
    return {"data": data, "has_more": has_more}


@router.get("/{run_id}/feedback")
@require_permission("runs", "read")
async def run_feedback(run_id: str, request: Request) -> list[dict]:
    """Return all feedback for a run."""
    run = await _resolve_run(run_id, request)
    feedback_repo = get_feedback_repo(request)
    return await feedback_repo.list_by_run(run["thread_id"], run_id)
