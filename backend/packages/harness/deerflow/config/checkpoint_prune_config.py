"""Checkpoint retention-prune configuration (storage plan 2026-09-02, batch A1).

The daily gateway scheduler prunes every checkpoint stream to its newest
``keep_per_thread`` rows. The default (``None``) resolves at prune time to
``REGENERATE_HISTORY_RAW_SCAN_LIMIT`` (400) from
``deerflow.runtime.checkpointer.prune`` — deliberately NOT redefined here so
the retention window and the regenerate scan window can never drift apart
(the config module also cannot import the runtime package: app_config loads
before runtime and would create an import cycle).
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class CheckpointPruneConfig(BaseModel):
    """Daily checkpoint-prune scheduler knobs (``checkpoint_prune:`` in config.yaml)."""

    enabled: bool = Field(
        default=True,
        description="Run the daily checkpoint prune task. Failures only log a warning — the gateway itself is never impacted.",
    )
    keep_per_thread: int | None = Field(
        default=None,
        ge=1,
        description=(
            "Checkpoints kept per (thread, namespace) stream. "
            "None (default) = the regenerate design window (REGENERATE_HISTORY_RAW_SCAN_LIMIT, 400). "
            "⚠️ LOWERING THIS IS A BEHAVIOR CHANGE: regenerate/edit-regenerate stays semantically valid "
            "only within the newest N checkpoints per thread."
        ),
    )
    daily_hour: int = Field(
        default=3,
        ge=0,
        le=23,
        description="Local hour of day for the daily prune pass (low-traffic window).",
    )
    batch_rows: int = Field(
        default=1000,
        ge=1,
        description="Max rows deleted per transaction — WAL single-writer friendly; keeps online runs winning the write lock.",
    )
