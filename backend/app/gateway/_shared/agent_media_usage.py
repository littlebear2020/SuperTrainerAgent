"""Drain a thread's sandbox media-usage ledger into ``external_api_calls``.

The run worker calls :func:`drain_agent_media_usage` after ``publish_end`` — but
only when ``DEER_FLOW_MEDIA_USAGE_LEDGER=1`` made ``deps.get_run_context`` inject
it (default is ``None``: the feature sleeps and nothing is collected).

Only the four core Agent threads are accepted; the ledger files are written by
other Agents too (they share the same ``_doubao_tts.py`` mirrors), but the sink
does not vouch for them. Rows are bound to the *current* ``thread_id`` /
``user_id`` of the run that just ended: ``task_id = thread_id``,
``user_id = run's effective user``.

Idempotence: ``call_id = media_call_id(thread_id, event_id)`` is stable across
drains, so re-reading the ledger never double-bills — already-stored events are
counted as ``duplicate``. Rows are committed one by one in their own session
(same shape as ``record_external_api_call``); an ``IntegrityError`` (lost race
with a concurrent drain) is a duplicate, any other failure is an ``error`` and
the loop moves on. Nothing here ever raises: metering must not break a run.
Log lines never carry ledger line content.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.gateway._shared.personal_usage.types import canonical_agent_capability
from deerflow.persistence.engine import get_session_factory
from deerflow.persistence.external_api_calls.model import ExternalApiCallRow
from deerflow.runtime.media_usage import MAX_EVENTS_PER_DRAIN, ledger_path, map_event_to_call, media_call_id, read_ledger_events

logger = logging.getLogger(__name__)

#: Threads whose ledgers this sink accepts (resolved through
#: ``canonical_agent_capability`` so retired aliases still match).
CORE_MEDIA_AGENTS = frozenset({"course-video", "creative-studio", "ppt-master", "podcast-studio"})

#: Existing-call_id lookups are batched to keep the IN clause bounded.
_EXISTING_LOOKUP_BATCH = 500


@dataclass(frozen=True)
class MediaDrainStats:
    written: int = 0
    duplicate: int = 0
    error: int = 0


#: Upper bound on rollback/close once a statement is already blocked (e.g. SQLite
#: busy lock): without it the cleanup queues behind the blocked statement and
#: ``wait_for``'s cancellation takes ``busy_timeout`` (30 s) instead of ~2 s.
_CLEANUP_TIMEOUT = 1.0


async def _close_quietly(session, *, timeout: float = _CLEANUP_TIMEOUT) -> None:
    """Roll back + close, each bounded; a lock-blocked connection must not stall the caller."""
    for operation in (session.rollback, session.close):
        try:
            await asyncio.wait_for(operation(), timeout=timeout)
        except Exception:
            pass


async def _commit_row(session_factory, row: ExternalApiCallRow) -> None:
    """Insert one row in its own session; roll back before the exception escapes.

    ``except BaseException`` on purpose: a cancellation (``asyncio.wait_for`` in the
    worker) must run the *bounded* cleanup and then propagate unchanged — never sit
    in an unbounded ``session.close()`` behind a blocked INSERT, and never be logged
    as a metering ``error``.
    """
    session = session_factory()
    try:
        session.add(row)
        await session.commit()
    except BaseException:
        await _close_quietly(session)
        raise
    await _close_quietly(session)


async def _existing_call_ids(session_factory, call_ids: list[str]) -> set[str]:
    existing: set[str] = set()
    session = session_factory()
    try:
        for start in range(0, len(call_ids), _EXISTING_LOOKUP_BATCH):
            rows = await session.execute(select(ExternalApiCallRow.call_id).where(ExternalApiCallRow.call_id.in_(call_ids[start : start + _EXISTING_LOOKUP_BATCH])))
            existing.update(r[0] for r in rows)
    finally:
        await _close_quietly(session)
    return existing


async def drain_agent_media_usage(*, thread_id: str, user_id: str, agent_name: str | None) -> MediaDrainStats:
    """Read the thread ledger tail and store its new events under this user."""
    if canonical_agent_capability(agent_name) not in CORE_MEDIA_AGENTS:
        return MediaDrainStats()

    read = await asyncio.to_thread(read_ledger_events, ledger_path(thread_id, user_id))
    error = read.errors
    if error:
        logger.warning("media usage ledger: %d unreadable or invalid line(s) for thread %s (agent=%s)", error, thread_id, agent_name)
    if not read.events:
        return MediaDrainStats(0, 0, error)

    call_ids = [media_call_id(thread_id, event.event_id) for event in read.events]
    session_factory = get_session_factory()
    if session_factory is None:
        logger.warning("media usage ledger: no SQL session factory; %d event(s) left in ledger for thread %s", len(read.events), thread_id)
        return MediaDrainStats(0, 0, error + 1)
    try:
        existing = await _existing_call_ids(session_factory, call_ids)
    except Exception:
        logger.warning("media usage ledger: existing-row lookup failed for thread %s; events left for the next drain", thread_id, exc_info=True)
        return MediaDrainStats(0, 0, error + 1)

    if read.truncated and call_ids[0] not in existing:
        # The oldest event still inside the window was never stored, so whatever
        # fell out of the window before it was lost too.
        logger.warning("ledger window overflow: thread %s ledger exceeds the read window and its oldest in-window event is not recorded; events outside the window may be lost", thread_id)

    written = 0
    duplicate = 0
    for event, call_id in zip(read.events, call_ids, strict=True):
        if written >= MAX_EVENTS_PER_DRAIN:
            break  # the rest stay in the window for the next drain
        if asyncio.current_task().cancelling():
            # The worker's time bound fired. A CancelledError raised inside a blocked
            # DBAPI call can be absorbed by the driver/ORM and resurface as another
            # exception (counted above as ``error``); never let the loop move on to
            # the next row afterwards.
            raise asyncio.CancelledError
        if call_id in existing:
            duplicate += 1
            continue
        mapped = map_event_to_call(event)
        row = ExternalApiCallRow(
            call_id=call_id,
            user_id=user_id,
            task_id=thread_id,
            provider=mapped.provider,
            method=mapped.method,
            status=mapped.status,
            elapsed_ms=mapped.elapsed_ms,
            metadata_json=dict(mapped.metadata),
        )
        try:
            await _commit_row(session_factory, row)
            written += 1
        except IntegrityError:
            duplicate += 1
        except asyncio.CancelledError:
            raise  # the worker's time bound: not a metering error, never swallowed
        except Exception:
            error += 1
            logger.warning("media usage ledger: write failed for one event in thread %s (agent=%s)", thread_id, agent_name, exc_info=True)

    logger.info("media usage drain: thread=%s agent=%s written=%d duplicate=%d error=%d", thread_id, agent_name, written, duplicate, error)
    return MediaDrainStats(written=written, duplicate=duplicate, error=error)
