#!/usr/bin/env python3
"""Prompt Cache hit-rate report (D-2).

Queries the runs table for ``token_usage_by_model`` JSON and produces a
per-day / per-model breakdown of:

- ``cache_read_tokens`` total
- cache-hit run count (runs with any ``cache_read_tokens > 0``)
- hit rate = hit runs / effective runs (``total_tokens > 0``)
- estimated savings (requires pricing data — shows raw tokens if unpriced)

Usage::

    cd backend && PYTHONPATH=. uv run python scripts/cache_hit_report.py
    cd backend && PYTHONPATH=. uv run python scripts/cache_hit_report.py --days 7
    cd backend && PYTHONPATH=. uv run python scripts/cache_hit_report.py --model gemini-3.6-flash

The DB path is auto-detected from config.yaml (``database.sqlite_dir``).
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

# ── helpers ───────────────────────────────────────────────────────────────────


def _find_db() -> Path:
    """Locate deerflow.db relative to the backend or project root."""
    candidates = [
        Path(".deer-flow/data/deerflow.db"),
        Path("../.deer-flow/data/deerflow.db"),
        Path("backend/.deer-flow/data/deerflow.db"),
    ]
    for c in candidates:
        if c.exists():
            return c.resolve()
    raise FileNotFoundError("deerflow.db not found. Run from backend/ or project root.")


def _parse_model_usage(raw: str | None) -> dict[str, dict]:
    """Parse token_usage_by_model JSON → {model: {input_tokens, output_tokens, total_tokens, cache_read_tokens}}."""
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def _date_of(dt_str: str) -> str:
    """Extract YYYY-MM-DD from a datetime string."""
    return dt_str[:10] if dt_str else "unknown"


# ── main report ───────────────────────────────────────────────────────────────


def run_report(db_path: Path, days: int | None = None, model_filter: str | None = None) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    where_parts = ["total_tokens > 0"]  # effective runs only
    params: list = []

    if days is not None:
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        where_parts.append("created_at >= ?")
        params.append(cutoff)

    where_clause = " AND ".join(where_parts)

    rows = conn.execute(
        f"""
        SELECT run_id, model_name, created_at, total_tokens, total_input_tokens,
               total_output_tokens, token_usage_by_model
        FROM runs
        WHERE {where_clause}
        ORDER BY created_at DESC
        """,
        params,
    ).fetchall()

    conn.close()

    if not rows:
        print("No runs found matching criteria.")
        return

    # Aggregate
    total_runs = len(rows)
    total_input = sum(r["total_input_tokens"] or 0 for r in rows)
    total_cache_read = 0
    hit_runs = 0

    by_day: dict[str, dict] = defaultdict(lambda: {"runs": 0, "hit_runs": 0, "cache_read_tokens": 0, "input_tokens": 0})
    by_model: dict[str, dict] = defaultdict(lambda: {"runs": 0, "hit_runs": 0, "cache_read_tokens": 0, "input_tokens": 0})

    for row in rows:
        date_key = _date_of(row["created_at"])
        model_name = row["model_name"] or "unknown"
        run_input = row["total_input_tokens"] or 0

        # Extract cache_read_tokens from per-model JSON
        model_usage = _parse_model_usage(row["token_usage_by_model"])
        run_cache_read = 0
        for m, bucket in model_usage.items():
            if model_filter and model_filter not in m:
                continue
            cr = int(bucket.get("cache_read_tokens", 0) or 0)
            run_cache_read += cr

        total_cache_read += run_cache_read
        if run_cache_read > 0:
            hit_runs += 1

        # By day
        by_day[date_key]["runs"] += 1
        by_day[date_key]["input_tokens"] += run_input
        by_day[date_key]["cache_read_tokens"] += run_cache_read
        if run_cache_read > 0:
            by_day[date_key]["hit_runs"] += 1

        # By model
        by_model[model_name]["runs"] += 1
        by_model[model_name]["input_tokens"] += run_input
        by_model[model_name]["cache_read_tokens"] += run_cache_read
        if run_cache_read > 0:
            by_model[model_name]["hit_runs"] += 1

    # Print summary
    print("=" * 80)
    print("Prompt Cache Hit-Rate Report")
    print(f"Database: {db_path}")
    if days:
        print(f"Window: last {days} days")
    else:
        print("Window: all time")
    if model_filter:
        print(f"Model filter: {model_filter}")
    print("=" * 80)

    hit_rate = (hit_runs / total_runs * 100) if total_runs else 0
    cache_ratio = (total_cache_read / total_input * 100) if total_input else 0

    print("\n## Overall\n")
    print(f"  Effective runs (total_tokens > 0):  {total_runs:>10,}")
    print(f"  Cache-hit runs (cache_read > 0):    {hit_runs:>10,}  ({hit_rate:.2f}%)")
    print(f"  Total input tokens:                 {total_input:>10,}")
    print(f"  Total cache_read tokens:            {total_cache_read:>10,}  ({cache_ratio:.2f}% of input)")

    # By day
    print("\n## By Day\n")
    print(f"  {'Date':<12} {'Runs':>8} {'Hits':>6} {'Hit%':>7} {'Cache Read':>14} {'Input':>14}")
    print(f"  {'-' * 12} {'-' * 8} {'-' * 6} {'-' * 7} {'-' * 14} {'-' * 14}")
    for date_key in sorted(by_day.keys()):
        d = by_day[date_key]
        hr = (d["hit_runs"] / d["runs"] * 100) if d["runs"] else 0
        print(f"  {date_key:<12} {d['runs']:>8,} {d['hit_runs']:>6,} {hr:>6.2f}% {d['cache_read_tokens']:>14,} {d['input_tokens']:>14,}")

    # By model
    print("\n## By Model\n")
    print(f"  {'Model':<30} {'Runs':>8} {'Hits':>6} {'Hit%':>7} {'Cache Read':>14} {'Input':>14}")
    print(f"  {'-' * 30} {'-' * 8} {'-' * 6} {'-' * 7} {'-' * 14} {'-' * 14}")
    for model_name in sorted(by_model.keys()):
        m = by_model[model_name]
        hr = (m["hit_runs"] / m["runs"] * 100) if m["runs"] else 0
        print(f"  {model_name:<30} {m['runs']:>8,} {m['hit_runs']:>6,} {hr:>6.2f}% {m['cache_read_tokens']:>14,} {m['input_tokens']:>14,}")

    print()


# ── CLI ───────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="Prompt Cache hit-rate report")
    parser.add_argument("--days", type=int, default=None, help="Only show last N days")
    parser.add_argument("--model", type=str, default=None, help="Filter by model name (substring match)")
    parser.add_argument("--db", type=str, default=None, help="Path to deerflow.db (auto-detected if omitted)")
    args = parser.parse_args()

    db_path = Path(args.db) if args.db else _find_db()
    if not db_path.exists():
        print(f"Error: database not found at {db_path}", file=sys.stderr)
        sys.exit(1)

    run_report(db_path, days=args.days, model_filter=args.model)


if __name__ == "__main__":
    main()
