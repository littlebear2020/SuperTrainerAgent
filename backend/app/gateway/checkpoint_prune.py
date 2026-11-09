"""Daily checkpoint retention-prune scheduler (storage plan 2026-09-02, batch A1).

Background asyncio task started from the gateway lifespan (same pattern as
the voice-twin training reaper): once a day at the configured low-traffic
hour it prunes every checkpoint stream to its newest ``keep_per_thread``
rows via :func:`deerflow.runtime.checkpointer.prune.prune_checkpoints`.

Safety properties:

- skips threads that currently have a pending/running run (regenerate and
  live graph writes must not race the pruner);
- deletes in small transactions (``batch_rows``) — WAL single-writer friendly;
- any failure logs a warning and waits for the next day; the gateway keeps
  serving (the prune is an optimization, never a dependency);
- non-sqlite backends (memory/postgres) never start the task.

Note: prune DELETEs never shrink the database file — freelist pages are only
reclaimed by the separate ``VACUUM INTO`` maintenance step (plan batch A2).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from fastapi import FastAPI

from deerflow.config.checkpoint_prune_config import CheckpointPruneConfig
from deerflow.runtime.checkpointer.prune import REGENERATE_HISTORY_RAW_SCAN_LIMIT, prune_checkpoints

logger = logging.getLogger(__name__)

_TASK_ATTR = "checkpoint_prune_task"


def _cfg(app: FastAPI) -> CheckpointPruneConfig | None:
    config = getattr(app.state, "config", None)
    return getattr(config, "checkpoint_prune", None)


def _seconds_until_next_run(now: datetime, hour: int) -> float:
    """Seconds from *now* until the next local-time ``hour`` (strictly future)."""
    candidate = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return (candidate - now).total_seconds()


async def run_prune_once(app: FastAPI):
    """One prune pass against the sqlite checkpointer, skipping inflight threads."""
    config = getattr(app.state, "config", None)
    database = getattr(config, "database", None)
    prune_cfg = _cfg(app) or CheckpointPruneConfig()
    keep = prune_cfg.keep_per_thread if prune_cfg.keep_per_thread is not None else REGENERATE_HISTORY_RAW_SCAN_LIMIT

    run_manager = getattr(app.state, "run_manager", None)
    if run_manager is None:
        logger.warning("Checkpoint prune skipped: run manager not available")
        return None

    skip = await run_manager.inflight_thread_ids()
    db_path = database.checkpointer_sqlite_path
    logger.info(
        "Checkpoint prune starting: db=%s keep=%d batch_rows=%d skip_threads=%d",
        db_path,
        keep,
        prune_cfg.batch_rows,
        len(skip),
    )
    result = await asyncio.to_thread(
        prune_checkpoints,
        db_path,
        keep_per_thread=keep,
        dry_run=False,
        batch_rows=prune_cfg.batch_rows,
        skip_thread_ids=skip,
    )
    logger.info(
        "Checkpoint prune done: deleted %d checkpoint(s) + %d writes row(s) across %d stream(s), %d transaction(s)",
        result.checkpoints_deleted,
        result.writes_deleted,
        result.streams_pruned,
        result.transactions,
    )
    return result


async def _prune_loop(app: FastAPI) -> None:
    prune_cfg = _cfg(app) or CheckpointPruneConfig()
    while True:
        delay = _seconds_until_next_run(datetime.now(), prune_cfg.daily_hour)
        logger.info("Checkpoint prune scheduled in %.0f minute(s)", delay / 60)
        await asyncio.sleep(delay)
        try:
            await run_prune_once(app)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Warn-only: the prune is an optimization; a failed pass retries next day.
            logger.exception("Checkpoint prune pass failed; retrying at the next scheduled window")


def start_checkpoint_prune_task(app: FastAPI) -> asyncio.Task | None:
    """Start the daily prune task. Returns ``None`` (with a log line) when disabled or non-sqlite."""
    prune_cfg = _cfg(app)
    if prune_cfg is None or not prune_cfg.enabled:
        logger.info("Checkpoint prune scheduler disabled (checkpoint_prune.enabled=false)")
        return None

    database = getattr(getattr(app.state, "config", None), "database", None)
    if database is None or getattr(database, "backend", None) != "sqlite":
        logger.info("Checkpoint prune scheduler idle: database backend is not sqlite")
        return None

    task = asyncio.create_task(_prune_loop(app), name="checkpoint-prune")
    app.state.checkpoint_prune_task = task
    logger.info(
        "Checkpoint prune scheduler started (daily at %02d:00, keep=%s)",
        prune_cfg.daily_hour,
        prune_cfg.keep_per_thread if prune_cfg.keep_per_thread is not None else f"regenerate window ({REGENERATE_HISTORY_RAW_SCAN_LIMIT})",
    )
    return task


async def stop_checkpoint_prune_task(app: FastAPI) -> None:
    """Cancel the prune task if running (lifespan shutdown)."""
    task = getattr(app.state, _TASK_ATTR, None)
    if task is None:
        return
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):  # noqa: BLE001 — shutdown must proceed
        pass
    app.state.checkpoint_prune_task = None
    logger.info("Checkpoint prune scheduler stopped")
