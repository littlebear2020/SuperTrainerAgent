"""Shared pagination helpers for gateway routers."""

from __future__ import annotations


def trim_run_message_page(rows: list[dict], *, limit: int, after_seq: int | None) -> tuple[list[dict], bool]:
    """Trim a ``limit + 1`` run-message page while preserving page boundaries.

    Forward pagination (no ``after_seq`` cursor) must keep the NEWEST ``limit``
    rows — the store returns them ascending, so plain ``rows[:limit]`` drops the
    latest message (R-04, 2026-08-13). Backward pagination with an explicit
    ``after_seq`` cursor keeps the first ``limit`` (oldest) rows instead.
    """
    has_more = len(rows) > limit
    if not has_more:
        return rows, False

    if after_seq is not None:
        return rows[:limit], True

    return rows[-limit:], True
