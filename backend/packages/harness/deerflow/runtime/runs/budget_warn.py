"""Thread-cumulative token budget warning (OBS-03 D3, 2026-09-23 批 2).

Warn-only observability: when a thread's cumulative tokens (the same
口径 as the UI Tokens badge — the sum across all runs in the thread)
exceed the task-type threshold (``token_budget_warn_tokens`` in
config.yaml, e.g. ``{"default": 12_000_000, "ppt-master": 12_000_000}``),
the run worker logs a WARNING and emits a ``token_budget_warn`` run
event; the frontend badge turns red. Nothing is ever blocked — a red
badge must mean "this task is genuinely heavy", not "this task died"
(12M default: a normal 5-page PPT ≈ 8M stays green; a 33-page deck ≈ 15M
is the ~80th percentile of the measured distribution).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

DEFAULT_TASK_TYPE = "default"


def resolve_warn_threshold(task_type: str | None, thresholds: Mapping[str, Any] | None) -> int | None:
    """Exact task-type match first, then ``default``; ``None`` = warn nowhere.

    A non-positive or non-numeric value disables the warning for that key —
    operators can turn a single task type off without touching the rest.
    """
    if not isinstance(thresholds, Mapping):
        return None
    for key in (task_type, DEFAULT_TASK_TYPE):
        if not key or key not in thresholds:
            continue
        value = thresholds[key]
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)) and value > 0:
            return int(value)
    return None


def should_warn_token_budget(total_tokens: int | None, task_type: str | None, thresholds: Mapping[str, Any] | None) -> bool:
    """True only when a positive thread total strictly exceeds the resolved threshold."""
    threshold = resolve_warn_threshold(task_type, thresholds)
    if not threshold or not total_tokens:
        return False
    return int(total_tokens) > threshold


def thread_total_tokens(aggregate: Mapping[str, Any] | None) -> int:
    """Pull the thread-cumulative total out of ``aggregate_tokens_by_thread`` output."""
    if not isinstance(aggregate, Mapping):
        return 0
    return int(aggregate.get("total_tokens") or 0)
