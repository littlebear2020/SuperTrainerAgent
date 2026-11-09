"""Tests for the daily checkpoint-prune scheduler (storage plan 2026-09-02, A1 step 2).

Covers:

- ``CheckpointPruneConfig`` parsing and its regenerate-aligned keep default;
- ``RunManager.inflight_thread_ids`` (the prune skip set);
- ``_seconds_until_next_run`` daily scheduling math;
- ``run_prune_once`` wiring: sqlite path, skip set, apply semantics;
- start/stop lifecycle and disabled / non-sqlite no-ops.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

from app.gateway import checkpoint_prune as cp
from deerflow.config.app_config import AppConfig
from deerflow.config.checkpoint_prune_config import CheckpointPruneConfig
from deerflow.runtime.checkpointer.prune import REGENERATE_HISTORY_RAW_SCAN_LIMIT
from deerflow.runtime.runs.manager import RunManager
from deerflow.runtime.runs.schemas import RunStatus

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def test_prune_config_defaults():
    cfg = CheckpointPruneConfig()
    assert cfg.enabled is True
    # keep_per_thread=None defers to the regenerate raw-scan window (single
    # source in deerflow.runtime.checkpointer.prune — no config-side magic number)
    assert cfg.keep_per_thread is None
    assert cfg.daily_hour == 3
    assert cfg.batch_rows == 1000


def test_prune_config_parsed_from_app_config_section():
    app_cfg = AppConfig.model_validate(
        {
            "sandbox": {"use": "deerflow.sandbox.local.provider:LocalSandboxProvider"},
            "checkpoint_prune": {"enabled": False, "daily_hour": 4, "keep_per_thread": 500, "batch_rows": 250},
        }
    )
    assert app_cfg.checkpoint_prune.enabled is False
    assert app_cfg.checkpoint_prune.daily_hour == 4
    assert app_cfg.checkpoint_prune.keep_per_thread == 500
    assert app_cfg.checkpoint_prune.batch_rows == 250


def test_prune_config_rejects_bad_hour():
    with pytest.raises(Exception):
        CheckpointPruneConfig(daily_hour=24)


# ---------------------------------------------------------------------------
# RunManager.inflight_thread_ids
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inflight_thread_ids_reports_pending_and_running_only():
    mgr = RunManager()
    await mgr.create("thread-a")
    b = await mgr.create("thread-b")
    c = await mgr.create("thread-c")
    await mgr.set_status(b.run_id, RunStatus.running)
    await mgr.set_status(c.run_id, RunStatus.success)

    assert await mgr.inflight_thread_ids() == {"thread-a", "thread-b"}


# ---------------------------------------------------------------------------
# Scheduling math
# ---------------------------------------------------------------------------


def test_next_run_before_hour_is_today():
    now = datetime(2026, 9, 2, 1, 0, 0)
    assert cp._seconds_until_next_run(now, 3) == pytest.approx(2 * 3600)


def test_next_run_after_hour_is_tomorrow():
    now = datetime(2026, 9, 2, 5, 0, 0)
    assert cp._seconds_until_next_run(now, 3) == pytest.approx(22 * 3600)


def test_next_run_at_exact_hour_is_tomorrow():
    now = datetime(2026, 9, 2, 3, 0, 0)
    assert cp._seconds_until_next_run(now, 3) == pytest.approx(24 * 3600)


# ---------------------------------------------------------------------------
# run_prune_once wiring
# ---------------------------------------------------------------------------


def _fake_app(tmp_path, *, enabled=True, keep=None, backend="sqlite") -> SimpleNamespace:
    db_path = tmp_path / "deerflow.db"
    db_path.touch()
    database = SimpleNamespace(backend=backend, checkpointer_sqlite_path=str(db_path))
    config = SimpleNamespace(database=database, checkpoint_prune=CheckpointPruneConfig(enabled=enabled, keep_per_thread=keep))
    run_manager = RunManager()
    return SimpleNamespace(state=SimpleNamespace(config=config, run_manager=run_manager))


@pytest.mark.asyncio
async def test_run_prune_once_applies_with_inflight_skip(tmp_path, monkeypatch):
    app = _fake_app(tmp_path)
    inflight_id = (await app.state.run_manager.create("thread-inflight")).run_id
    _ = inflight_id

    captured: dict = {}
    real_result = SimpleNamespace(checkpoints_deleted=3, writes_deleted=1, reclaimable_bytes=10, streams_pruned=2, transactions=1)

    async def fake_to_thread(fn, *args, **kwargs):
        captured["fn"] = fn
        captured["args"] = args
        captured["kwargs"] = kwargs
        return real_result

    monkeypatch.setattr(cp.asyncio, "to_thread", fake_to_thread)

    result = await cp.run_prune_once(app)

    assert result is real_result
    assert captured["fn"].__name__ == "prune_checkpoints"
    assert captured["args"][0] == app.state.config.database.checkpointer_sqlite_path
    kwargs = captured["kwargs"]
    assert kwargs["dry_run"] is False
    assert kwargs["skip_thread_ids"] == {"thread-inflight"}
    assert kwargs["keep_per_thread"] == REGENERATE_HISTORY_RAW_SCAN_LIMIT  # None → regenerate window


@pytest.mark.asyncio
async def test_run_prune_once_honors_configured_keep(tmp_path, monkeypatch):
    app = _fake_app(tmp_path, keep=500)

    captured: dict = {}

    async def fake_to_thread(fn, *args, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(checkpoints_deleted=0, writes_deleted=0, streams_pruned=0, transactions=0)

    monkeypatch.setattr(cp.asyncio, "to_thread", fake_to_thread)
    await cp.run_prune_once(app)
    assert captured["keep_per_thread"] == 500


# ---------------------------------------------------------------------------
# start/stop lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_returns_none_when_disabled(tmp_path):
    app = _fake_app(tmp_path, enabled=False)
    assert cp.start_checkpoint_prune_task(app) is None


@pytest.mark.asyncio
async def test_start_returns_none_for_non_sqlite(tmp_path):
    app = _fake_app(tmp_path, backend="postgres")
    assert cp.start_checkpoint_prune_task(app) is None


@pytest.mark.asyncio
async def test_start_creates_cancellable_task(tmp_path, monkeypatch):
    app = _fake_app(tmp_path)
    monkeypatch.setattr(cp, "_seconds_until_next_run", lambda now, hour: 3600.0)

    task = cp.start_checkpoint_prune_task(app)
    try:
        assert task is not None and not task.done()
    finally:
        await cp.stop_checkpoint_prune_task(app)
    assert task.cancelled() or task.done()
