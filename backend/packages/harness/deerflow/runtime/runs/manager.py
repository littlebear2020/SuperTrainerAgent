"""In-memory run registry with optional persistent RunStore backing."""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from .schemas import DisconnectMode, RunStatus

if TYPE_CHECKING:
    from deerflow.runtime.runs.store.base import RunStore

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class RunRecord:
    """Mutable record for a single run."""

    run_id: str
    thread_id: str
    assistant_id: str | None
    status: RunStatus
    on_disconnect: DisconnectMode
    # Discriminates real agent runs from bookkeeping rows. Only "run" is ever
    # produced today; the operations console filters on it so a future
    # non-run row cannot silently inflate run counts or reported spend.
    operation_kind: str = "run"
    multitask_strategy: str = "reject"
    metadata: dict = field(default_factory=dict)
    kwargs: dict = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""
    task: asyncio.Task | None = field(default=None, repr=False)
    abort_event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    abort_action: str = "interrupt"
    error: str | None = None
    store_only: bool = False
    model_name: str | None = None
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_tokens: int = 0
    llm_call_count: int = 0
    lead_agent_tokens: int = 0
    subagent_tokens: int = 0
    middleware_tokens: int = 0
    token_usage_by_model: dict[str, dict[str, int]] = field(default_factory=dict)
    message_count: int = 0
    last_ai_message: str | None = None
    first_human_message: str | None = None
    stop_reason: str | None = None
    slow_llm_call_count: int = 0
    # R9（2026-09-24 批 2）：empty_fallback_count 镜像（slow 链同款）——列已由
    # migration p9c0d1e2f3a4 建好、worker 已透传，此前仅缺 RunRecord 字段与水合。
    empty_fallback_count: int = 0


class RunManager:
    """In-memory run registry with optional persistent RunStore backing.

    All mutations are protected by an asyncio lock. When a ``store`` is
    provided, serializable metadata is also persisted to the store so
    that run history survives process restarts.
    """

    def __init__(self, store: RunStore | None = None) -> None:
        self._runs: dict[str, RunRecord] = {}
        self._lock = asyncio.Lock()
        self._store = store

    async def _persist_to_store(self, record: RunRecord) -> None:
        """Best-effort persist run record to backing store."""
        if self._store is None:
            return
        try:
            await self._store.put(
                record.run_id,
                thread_id=record.thread_id,
                assistant_id=record.assistant_id,
                status=record.status.value,
                operation_kind=record.operation_kind,
                multitask_strategy=record.multitask_strategy,
                metadata=record.metadata or {},
                kwargs=record.kwargs or {},
                created_at=record.created_at,
            )
        except Exception:
            logger.warning("Failed to persist run %s to store", record.run_id, exc_info=True)

    async def _persist_status(self, run_id: str, status: RunStatus, *, error: str | None = None) -> None:
        """Best-effort persist a status transition to the backing store."""
        if self._store is None:
            return
        try:
            await self._store.update_status(run_id, status.value, error=error)
        except Exception:
            logger.warning("Failed to persist status update for run %s", run_id, exc_info=True)

    async def update_run_completion(self, run_id: str, **kwargs) -> None:
        """Persist token usage and completion data to the backing store."""
        async with self._lock:
            record = self._runs.get(run_id)
            if record is not None:
                for key, value in kwargs.items():
                    if key == "status":
                        continue
                    if hasattr(record, key) and value is not None:
                        setattr(record, key, value)
                record.updated_at = _now_iso()
        if self._store is not None:
            try:
                await self._store.update_run_completion(run_id, **kwargs)
            except Exception:
                logger.warning("Failed to persist run completion for %s", run_id, exc_info=True)

    async def aggregate_thread_tokens(self, thread_id: str, *, include_active: bool = False) -> dict | None:
        """Thread-cumulative token aggregate (D3, 2026-09-23), or None without a store.

        Best-effort like the other helpers: the budget warning is observability
        and must never fail a finished run.
        """
        if self._store is None:
            return None
        try:
            return await self._store.aggregate_tokens_by_thread(thread_id, include_active=include_active)
        except Exception:
            logger.warning("Failed to aggregate thread tokens for %s (non-fatal)", thread_id, exc_info=True)
            return None

    async def update_model_name(self, run_id: str, model_name: str | None) -> None:
        """Record which model served a run, in memory and in the store.

        The stores have had ``update_model_name`` since the beginning but
        nothing ever called it, so ``runs.model_name`` was NULL on every row
        and the operations console had a permanently blank model column. The
        worker now calls this once, on completion, with the model the journal
        observed. Best-effort like the other persist helpers: reporting
        metadata must never fail a finished run.
        """
        async with self._lock:
            record = self._runs.get(run_id)
            if record is not None:
                record.model_name = model_name
                record.updated_at = _now_iso()
        if self._store is None:
            return
        try:
            await self._store.update_model_name(run_id, model_name)
        except Exception:
            logger.warning("Failed to persist model_name for run %s", run_id, exc_info=True)

    async def update_run_progress(self, run_id: str, **kwargs) -> None:
        """Persist a running token/message snapshot without changing status."""
        should_persist = True
        async with self._lock:
            record = self._runs.get(run_id)
            if record is not None:
                should_persist = record.status == RunStatus.running
            if record is not None and should_persist:
                for key, value in kwargs.items():
                    if hasattr(record, key) and value is not None:
                        setattr(record, key, value)
                record.updated_at = _now_iso()
        if should_persist and self._store is not None:
            try:
                await self._store.update_run_progress(run_id, **kwargs)
            except Exception:
                logger.warning("Failed to persist run progress for %s", run_id, exc_info=True)

    async def create(
        self,
        thread_id: str,
        assistant_id: str | None = None,
        *,
        on_disconnect: DisconnectMode = DisconnectMode.cancel,
        metadata: dict | None = None,
        kwargs: dict | None = None,
        multitask_strategy: str = "reject",
    ) -> RunRecord:
        """Create a new pending run and register it."""
        run_id = str(uuid.uuid4())
        now = _now_iso()
        record = RunRecord(
            run_id=run_id,
            thread_id=thread_id,
            assistant_id=assistant_id,
            status=RunStatus.pending,
            on_disconnect=on_disconnect,
            multitask_strategy=multitask_strategy,
            metadata=metadata or {},
            kwargs=kwargs or {},
            created_at=now,
            updated_at=now,
        )
        async with self._lock:
            self._runs[run_id] = record
            persisted = False
            try:
                if self._store is not None:
                    await self._store.put(
                        record.run_id,
                        thread_id=record.thread_id,
                        assistant_id=record.assistant_id,
                        status=record.status.value,
                        operation_kind=record.operation_kind,
                        multitask_strategy=record.multitask_strategy,
                        metadata=record.metadata or {},
                        kwargs=record.kwargs or {},
                        created_at=record.created_at,
                    )
                persisted = True
            except Exception:
                logger.warning("Failed to persist run %s; rolled back in-memory record", run_id, exc_info=True)
                raise
            finally:
                if not persisted:
                    self._runs.pop(run_id, None)
        logger.info("Run created: run_id=%s thread_id=%s", run_id, thread_id)
        return record

    async def get(self, run_id: str, *, user_id: str | None = None) -> RunRecord | None:
        """Return a run record by ID, hydrating from store if not in memory."""
        async with self._lock:
            record = self._runs.get(run_id)
        if record is not None:
            return record
        if self._store is None:
            return None
        try:
            row = await self._store.get(run_id, user_id=user_id)
        except Exception:
            logger.warning("Failed to hydrate run %s from store", run_id, exc_info=True)
            return None
        # Re-check after store await
        async with self._lock:
            record = self._runs.get(run_id)
        if record is not None:
            return record
        if row is None:
            return None
        try:
            return self._record_from_store(row)
        except Exception:
            logger.warning("Failed to map store row for run %s", run_id, exc_info=True)
            return None

    async def list_by_thread(self, thread_id: str, *, user_id: str | None = None, limit: int = 100) -> list[RunRecord]:
        """Return runs for a given thread, newest first, at most ``limit`` records.

        In-memory runs take precedence over store-only records for the same run_id.
        """
        async with self._lock:
            memory_records = [r for r in self._runs.values() if r.thread_id == thread_id]
        if self._store is None:
            return sorted(memory_records, key=lambda r: r.created_at, reverse=True)[:limit]
        records_by_id = {r.run_id: r for r in memory_records}
        # R-14 (2026-08-13): fetch limit + |memory| from the store — memory and
        # store overlap for the same run_id, and dedup below drops the store
        # copies. The old `limit - len(memory)` cap returned short pages
        # whenever overlap existed (and hid runs entirely when the memory was
        # older than the store and |memory| >= limit).
        store_limit = limit + len(memory_records)
        try:
            rows = await self._store.list_by_thread(thread_id, user_id=user_id, limit=store_limit)
        except Exception:
            logger.warning("Failed to hydrate runs for thread %s from store", thread_id, exc_info=True)
            return sorted(memory_records, key=lambda r: r.created_at, reverse=True)[:limit]
        for row in rows:
            rid = row.get("run_id")
            if rid and rid not in records_by_id:
                try:
                    records_by_id[rid] = self._record_from_store(row)
                except Exception:
                    logger.warning("Failed to map store row for run %s", rid, exc_info=True)
        return sorted(records_by_id.values(), key=lambda r: r.created_at, reverse=True)[:limit]

    async def list_successful_regenerate_sources(
        self,
        thread_id: str,
        *,
        user_id: str | None = None,
    ) -> set[str]:
        """Return source run IDs superseded by successful regenerations.

        Combines persisted store records with in-memory records. A latest
        in-memory failure discards a previously successful store snapshot
        for the same source (the regeneration was retried and failed).
        """
        async with self._lock:
            # Local RunRecord doesn't carry user_id; in single-worker mode the
            # in-memory dict only holds the current process's runs, so we skip
            # user filtering here and let the store handle it for persisted rows.
            memory_records = [r for r in self._runs.values() if r.thread_id == thread_id and r.operation_kind == "run"]

        sources: set[str] = set()
        if self._store is not None:
            try:
                sources = set(await self._store.list_successful_regenerate_sources(thread_id, user_id=user_id))
            except Exception:
                logger.warning("Failed to list regenerate sources for thread %s", thread_id, exc_info=True)

        # Sort by created_at so the latest attempt for a given source is
        # processed last -- its status (success/error) is authoritative.
        # created_at is an ISO-8601 string; lexicographic sort is correct.
        for record in sorted(memory_records, key=lambda r: r.created_at):
            source = record.metadata.get("regenerate_from_run_id")
            if not isinstance(source, str) or not source:
                continue
            sources.discard(source)
            if record.status == RunStatus.success:
                sources.add(source)
        return sources

    async def set_status(self, run_id: str, status: RunStatus, *, error: str | None = None) -> None:
        """Transition a run to a new status."""
        async with self._lock:
            record = self._runs.get(run_id)
            if record is None:
                logger.warning("set_status called for unknown run %s", run_id)
                return
            record.status = status
            record.updated_at = _now_iso()
            if error is not None:
                record.error = error
        await self._persist_status(run_id, status, error=error)
        logger.info("Run %s -> %s", run_id, status.value)

    async def cancel(self, run_id: str, *, action: str = "interrupt") -> bool:
        """Request cancellation of a run.

        Args:
            run_id: The run ID to cancel.
            action: "interrupt" keeps checkpoint, "rollback" reverts to pre-run state.

        Sets the abort event with the action reason and cancels the asyncio task.
        Returns ``True`` if the run was in-flight and cancellation was initiated,
        or if it had already been cancelled — cancelling an interrupted run is
        idempotent, because the caller's intent (this run is stopped) already
        holds. Without that, a double stop-button click turned into a 409 from
        the cancel route and an unhandled rejection in the browser.
        """
        async with self._lock:
            record = self._runs.get(run_id)
            if record is None:
                return False
            if record.status == RunStatus.interrupted:
                # Already cancelled — report success without re-arming the abort
                # or overwriting the action the first cancel chose.
                return True
            if record.status not in (RunStatus.pending, RunStatus.running):
                return False
            record.abort_action = action
            record.abort_event.set()
            if record.task is not None and not record.task.done():
                record.task.cancel()
            record.status = RunStatus.interrupted
            record.updated_at = _now_iso()
        await self._persist_status(run_id, RunStatus.interrupted)
        logger.info("Run %s cancelled (action=%s)", run_id, action)
        return True

    async def create_or_reject(
        self,
        thread_id: str,
        assistant_id: str | None = None,
        *,
        on_disconnect: DisconnectMode = DisconnectMode.cancel,
        metadata: dict | None = None,
        kwargs: dict | None = None,
        multitask_strategy: str = "reject",
    ) -> RunRecord:
        """Atomically check for inflight runs and create a new one.

        For ``reject`` strategy, raises ``ConflictError`` if thread
        already has a pending/running run.  For ``interrupt``/``rollback``,
        cancels inflight runs before creating.

        This method holds the lock across both the check and the insert,
        eliminating the TOCTOU race in separate ``has_inflight`` + ``create``.
        """
        run_id = str(uuid.uuid4())
        now = _now_iso()

        _supported_strategies = ("reject", "interrupt", "rollback")

        async with self._lock:
            if multitask_strategy not in _supported_strategies:
                raise UnsupportedStrategyError(f"Multitask strategy '{multitask_strategy}' is not yet supported. Supported strategies: {', '.join(_supported_strategies)}")

            inflight = [r for r in self._runs.values() if r.thread_id == thread_id and r.status in (RunStatus.pending, RunStatus.running)]

            if multitask_strategy == "reject" and inflight:
                raise ConflictError(f"Thread {thread_id} already has an active run")

            if multitask_strategy in ("interrupt", "rollback") and inflight:
                logger.info(
                    "Preparing to cancel %d inflight run(s) on thread %s (strategy=%s)",
                    len(inflight),
                    thread_id,
                    multitask_strategy,
                )

            record = RunRecord(
                run_id=run_id,
                thread_id=thread_id,
                assistant_id=assistant_id,
                status=RunStatus.pending,
                on_disconnect=on_disconnect,
                multitask_strategy=multitask_strategy,
                metadata=metadata or {},
                kwargs=kwargs or {},
                created_at=now,
                updated_at=now,
            )
            self._runs[run_id] = record
            persisted = False
            try:
                if self._store is not None:
                    await self._store.put(
                        record.run_id,
                        thread_id=record.thread_id,
                        assistant_id=record.assistant_id,
                        status=record.status.value,
                        operation_kind=record.operation_kind,
                        multitask_strategy=record.multitask_strategy,
                        metadata=record.metadata or {},
                        kwargs=record.kwargs or {},
                        created_at=record.created_at,
                    )
                persisted = True
            except Exception:
                logger.warning("Failed to persist run %s; rolled back in-memory record", run_id, exc_info=True)
                raise
            finally:
                if not persisted:
                    self._runs.pop(run_id, None)

            if multitask_strategy in ("interrupt", "rollback") and inflight:
                for r in inflight:
                    r.abort_action = multitask_strategy
                    r.abort_event.set()
                    if r.task is not None and not r.task.done():
                        r.task.cancel()
                    r.status = RunStatus.interrupted
                    r.updated_at = now

        for r in inflight if multitask_strategy in ("interrupt", "rollback") else []:
            await self._persist_to_store(r)
        logger.info("Run created: run_id=%s thread_id=%s", run_id, thread_id)
        return record

    @staticmethod
    def _record_from_store(row: dict[str, Any]) -> RunRecord:
        """Reconstruct a RunRecord from a store row dict."""
        return RunRecord(
            run_id=row["run_id"],
            thread_id=row["thread_id"],
            assistant_id=row.get("assistant_id"),
            status=RunStatus(row.get("status", "pending")),
            on_disconnect=DisconnectMode(row.get("on_disconnect", "cancel")),
            operation_kind=row.get("operation_kind") or "run",
            multitask_strategy=row.get("multitask_strategy", "reject"),
            metadata=row.get("metadata") or {},
            kwargs=row.get("kwargs") or {},
            created_at=row.get("created_at", ""),
            updated_at=row.get("updated_at", ""),
            error=row.get("error"),
            model_name=row.get("model_name"),
            store_only=True,
            total_input_tokens=row.get("total_input_tokens") or 0,
            total_output_tokens=row.get("total_output_tokens") or 0,
            total_tokens=row.get("total_tokens") or 0,
            llm_call_count=row.get("llm_call_count") or 0,
            lead_agent_tokens=row.get("lead_agent_tokens") or 0,
            subagent_tokens=row.get("subagent_tokens") or 0,
            middleware_tokens=row.get("middleware_tokens") or 0,
            token_usage_by_model=row.get("token_usage_by_model") or {},
            message_count=row.get("message_count") or 0,
            last_ai_message=row.get("last_ai_message"),
            first_human_message=row.get("first_human_message"),
            stop_reason=row.get("stop_reason"),
            slow_llm_call_count=row.get("slow_llm_call_count") or 0,
            empty_fallback_count=row.get("empty_fallback_count") or 0,
        )

    async def has_inflight(self, thread_id: str) -> bool:
        """Return ``True`` if *thread_id* has a pending or running run."""
        async with self._lock:
            return any(r.thread_id == thread_id and r.status in (RunStatus.pending, RunStatus.running) for r in self._runs.values())

    async def inflight_thread_ids(self) -> set[str]:
        """Thread ids that currently have a pending or running run.

        The checkpoint-prune scheduler passes this set as its skip list so a
        live run's history is never pruned mid-flight (storage plan 2026-09-02 A1).
        """
        async with self._lock:
            return {r.thread_id for r in self._runs.values() if r.status in (RunStatus.pending, RunStatus.running)}

    async def reconcile_orphaned_inflight_runs(
        self,
        *,
        error: str,
        before: str | None = None,
    ) -> list[RunRecord]:
        """Mark persisted active runs as failed when no local task owns them.

        Gateway runs are process-local: the asyncio task and abort event live in
        memory, while the run row is durable.  After a SQLite-backed gateway
        restart, any persisted ``pending`` or ``running`` row created before
        startup cannot still have a local worker.  This recovery step turns that
        ambiguous state into an explicit error.
        """
        if self._store is None:
            return []
        try:
            rows = await self._store.list_inflight(before=before)
        except Exception:
            logger.warning("Failed to query inflight runs for recovery", exc_info=True)
            return []
        if not rows:
            return []

        recovered: list[RunRecord] = []
        now = _now_iso()
        for row in rows:
            run_id = row["run_id"]
            async with self._lock:
                if run_id in self._runs:
                    continue
                record = self._record_from_store(row)
                self._runs[run_id] = record
                record.status = RunStatus.error
                record.error = error
                record.updated_at = now
            if self._store is not None:
                try:
                    await self._store.update_status(run_id, RunStatus.error.value, error=error)
                except Exception:
                    logger.warning("Failed to persist recovery status for run %s", run_id, exc_info=True)
                    continue
            recovered.append(record)

        if recovered:
            logger.warning("Recovered %d orphaned inflight run(s) as error", len(recovered))
        return recovered

    async def cleanup(self, run_id: str, *, delay: float = 300) -> None:
        """Remove a run record after an optional delay."""
        if delay > 0:
            await asyncio.sleep(delay)
        async with self._lock:
            self._runs.pop(run_id, None)
        logger.debug("Run record %s cleaned up", run_id)


class ConflictError(Exception):
    """Raised when multitask_strategy=reject and thread has inflight runs."""


class UnsupportedStrategyError(Exception):
    """Raised when a multitask_strategy value is not yet implemented."""
