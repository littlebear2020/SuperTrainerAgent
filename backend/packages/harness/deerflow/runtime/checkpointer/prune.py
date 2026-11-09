"""Checkpoint retention pruning for the SQLite checkpointer.

Storage-bleeding plan 2026-09-02, batch A1 (ST-1/ST-2): the checkpointer
database grows unboundedly because nothing ever deletes old checkpoints.
This module implements a keep-the-newest-N-per-stream retention policy.

Schema facts (langgraph_checkpoint_sqlite 3.0.3): exactly two tables —
``checkpoints`` (PK thread_id, checkpoint_ns, checkpoint_id; state blobs
inline) and ``writes`` (PK thread_id, checkpoint_ns, checkpoint_id, task_id,
idx). There are no shared-blob tables, so deleting a checkpoint row together
with its ``writes`` rows is safe — no cross-checkpoint cascade hazard.

Concurrency (WAL single-writer): deletes run in small transactions
(``batch_rows`` budget, default 1000) so an online gateway never contends
with a long-running prune transaction. Callers should additionally skip
threads that currently have a running run (the gateway scheduler does).

CLI::

    cd backend && uv run python -m deerflow.runtime.checkpointer.prune \
        --db .deer-flow/data/deerflow.db            # dry-run report
    ... --apply                                     # actually delete

``--apply`` never reclaims file size — DELETE leaves freelist pages. Space
reclamation is a separate ``VACUUM INTO`` step in a maintenance window
(plan batch A2); this module only reports the reclaimable estimate.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "REGENERATE_HISTORY_SCAN_LIMIT",
    "REGENERATE_HISTORY_RAW_SCAN_LIMIT",
    "DEFAULT_BATCH_ROWS",
    "PruneResult",
    "prune_checkpoints",
    "main",
]

# ---------------------------------------------------------------------------
# Retention window — single source of truth
# ---------------------------------------------------------------------------
# The gateway's regenerate / edit-regenerate flow (app.gateway.routers.
# thread_runs) locates the replay base by scanning up to
# REGENERATE_HISTORY_RAW_SCAN_LIMIT physical checkpoints per thread; a thread
# with fewer than that can fail the scan with 409
# ``_MISSING_REGENERATE_BASE_DETAIL``. The prune default keeps exactly this
# many per stream, so pruning never shrinks the regenerate window.
#
# These constants live in the harness layer (not their historical home in
# thread_runs.py) because the prune CLI belongs to this package and the
# harness → app import is forbidden (tests/test_harness_boundary.py);
# thread_runs.py re-imports them from here.
REGENERATE_HISTORY_SCAN_LIMIT = 200
# Doubled to keep ~200 effective checkpoints when duration-only checkpoints
# (one per successful run in steady state) consume roughly half of history.
REGENERATE_HISTORY_RAW_SCAN_LIMIT = REGENERATE_HISTORY_SCAN_LIMIT * 2

# Row budget (checkpoints + writes) per DELETE transaction. WAL allows one
# writer at a time; small transactions keep online run writes winning the
# lock quickly instead of queueing behind a giant prune transaction.
# A victim is always deleted atomically (its writes + checkpoint row in one
# transaction) so a crash can never orphan writes rows behind a surviving
# checkpoint — which means a pathological single checkpoint carrying MORE
# writes rows than this budget still lands in one oversized transaction
# (复核边界, 2026-09-02). Real-world writes-per-checkpoint is single digits.
DEFAULT_BATCH_ROWS = 1000

# Match the engine/checkpointer busy_timeout (30s) so a prune hitting a busy
# database waits like every other writer instead of failing fast.
BUSY_TIMEOUT_MS = 30_000


@dataclass
class PruneResult:
    """Outcome summary for one prune pass (dry-run or applied)."""

    dry_run: bool
    keep_per_thread: int
    streams_total: int = 0  # distinct (thread_id, checkpoint_ns) streams seen
    streams_pruned: int = 0
    threads_skipped: int = 0
    checkpoints_deleted: int = 0
    writes_deleted: int = 0
    reclaimable_bytes: int = 0  # estimated bytes freelist would reclaim on VACUUM
    transactions: int = 0
    skipped_thread_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "dry_run": self.dry_run,
            "keep_per_thread": self.keep_per_thread,
            "streams_total": self.streams_total,
            "streams_pruned": self.streams_pruned,
            "threads_skipped": self.threads_skipped,
            "checkpoints_deleted": self.checkpoints_deleted,
            "writes_deleted": self.writes_deleted,
            "rows_deleted": self.checkpoints_deleted + self.writes_deleted,
            "reclaimable_bytes": self.reclaimable_bytes,
            "reclaimable_mib": round(self.reclaimable_bytes / (1024 * 1024), 1),
            "transactions": self.transactions,
            "skipped_thread_ids": self.skipped_thread_ids,
        }


@dataclass
class _Stream:
    """One checkpoint stream (thread_id, checkpoint_ns) and its victims."""

    thread_id: str
    checkpoint_ns: str
    drop_ids: list[str]


def _connect(db_path: str | Path) -> sqlite3.Connection:
    """Open the checkpointer database in explicit-transaction mode."""
    conn = sqlite3.connect(str(db_path), timeout=BUSY_TIMEOUT_MS / 1000, isolation_level=None)
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS};")
    return conn


def _plan(
    conn: sqlite3.Connection,
    keep_per_thread: int,
    skip_thread_ids: frozenset[str],
) -> tuple[list[_Stream], int, int]:
    """Compute per-stream victims. Returns (streams, streams_total, threads_skipped)."""
    if keep_per_thread < 1:
        raise ValueError(f"keep_per_thread must be >= 1, got {keep_per_thread}")

    streams: list[_Stream] = []
    current_key: tuple[str, str] | None = None
    drop_ids: list[str] = []
    threads_seen: set[str] = set()
    threads_skipped = 0
    skipped: set[str] = set()

    # ORDER BY groups streams together and sorts ids ascending, so the
    # "newest N" are simply the last N of each run — matching the saver's
    # own checkpoint_id ordering (lexicographic ts-prefixed ids).
    cursor = conn.execute("SELECT thread_id, checkpoint_ns, checkpoint_id FROM checkpoints ORDER BY thread_id, checkpoint_ns, checkpoint_id")
    for thread_id, ns, checkpoint_id in cursor:
        threads_seen.add(thread_id)
        if thread_id in skip_thread_ids:
            skipped.add(thread_id)
            continue
        key = (thread_id, ns)
        if current_key is not None and key != current_key:
            streams.append(_Stream(current_key[0], current_key[1], drop_ids))
            drop_ids = []
        current_key = key
        drop_ids.append(checkpoint_id)

    if current_key is not None:
        streams.append(_Stream(current_key[0], current_key[1], drop_ids))

    # Retain the newest keep_per_thread of each stream
    for stream in streams:
        if len(stream.drop_ids) > keep_per_thread:
            stream.drop_ids = stream.drop_ids[:-keep_per_thread]
        else:
            stream.drop_ids = []

    threads_skipped = len(skipped & threads_seen)
    streams_total = len(streams)
    victims = [s for s in streams if s.drop_ids]
    return victims, streams_total, threads_skipped


def _measure_victims(conn: sqlite3.Connection, streams: list[_Stream], chunk: int = 400) -> tuple[int, int, int]:
    """Count victim checkpoint/writes rows and estimate reclaimable bytes."""
    cp_rows = 0
    write_rows = 0
    reclaim = 0
    for stream in streams:
        ids = stream.drop_ids
        cp_rows += len(ids)
        for i in range(0, len(ids), chunk):
            part = ids[i : i + chunk]
            qmarks = ",".join("?" * len(part))
            base = (stream.thread_id, stream.checkpoint_ns)
            reclaim += conn.execute(
                f"SELECT COALESCE(SUM(LENGTH(checkpoint) + LENGTH(metadata)), 0) FROM checkpoints WHERE thread_id=? AND checkpoint_ns=? AND checkpoint_id IN ({qmarks})",
                (*base, *part),
            ).fetchone()[0]
            w = conn.execute(
                f"SELECT COUNT(*), COALESCE(SUM(LENGTH(value)), 0) FROM writes WHERE thread_id=? AND checkpoint_ns=? AND checkpoint_id IN ({qmarks})",
                (*base, *part),
            ).fetchone()
            write_rows += w[0]
            reclaim += w[1]
    return cp_rows, write_rows, reclaim


def _apply(
    conn: sqlite3.Connection,
    streams: list[_Stream],
    batch_rows: int,
) -> tuple[int, int, int]:
    """Delete victims in row-budgeted transactions. Returns (cp_deleted, w_deleted, txns)."""
    if batch_rows < 1:
        raise ValueError(f"batch_rows must be >= 1, got {batch_rows}")

    # Flatten victims with per-victim write counts for greedy packing
    victims: list[tuple[str, str, str, int]] = []
    for stream in streams:
        base = (stream.thread_id, stream.checkpoint_ns)
        cursor = conn.execute(
            "SELECT checkpoint_id, COUNT(*) FROM writes WHERE thread_id=? AND checkpoint_ns=? GROUP BY checkpoint_id",
            base,
        )
        writes_by_id = dict(cursor.fetchall())
        for cid in stream.drop_ids:
            victims.append((*base, cid, writes_by_id.get(cid, 0)))

    cp_deleted = 0
    w_deleted = 0
    txns = 0

    def _flush(batch: list[tuple[str, str, str, int]]) -> None:
        nonlocal cp_deleted, w_deleted, txns
        if not batch:
            return
        conn.execute("BEGIN")
        try:
            # Writes first (child rows), then checkpoints — per row, so the
            # transaction's row count is observable and the budget exact.
            conn.executemany(
                "DELETE FROM writes WHERE thread_id=? AND checkpoint_ns=? AND checkpoint_id=?",
                [(t, ns, cid) for t, ns, cid, _ in batch],
            )
            conn.executemany(
                "DELETE FROM checkpoints WHERE thread_id=? AND checkpoint_ns=? AND checkpoint_id=?",
                [(t, ns, cid) for t, ns, cid, _ in batch],
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        cp_deleted += len(batch)
        w_deleted += sum(w for *_x, w in batch)
        txns += 1

    budget = 0
    batch: list[tuple[str, str, str, int]] = []
    for victim in victims:
        cost = 1 + victim[3]  # 1 checkpoint row + its writes rows
        if batch and budget + cost > batch_rows:
            _flush(batch)
            batch, budget = [], 0
        batch.append(victim)
        budget += cost
    _flush(batch)
    return cp_deleted, w_deleted, txns


def prune_checkpoints(
    db_path: str | Path,
    keep_per_thread: int = REGENERATE_HISTORY_RAW_SCAN_LIMIT,
    *,
    dry_run: bool = True,
    batch_rows: int = DEFAULT_BATCH_ROWS,
    skip_thread_ids: frozenset[str] | set[str] = frozenset(),
) -> PruneResult:
    """Prune each checkpoint stream to its newest ``keep_per_thread`` rows.

    Deletes the older checkpoints together with their ``writes`` rows, in
    transactions bounded by ``batch_rows`` deleted rows. With ``dry_run=True``
    (default) nothing is written — the result reports what *would* happen.

    ⚠️ Lowering ``keep_per_thread`` below the default is a behavior change:
    regenerate/edit-regenerate only stays semantically valid within the newest
    N checkpoints per thread (see REGENERATE_HISTORY_RAW_SCAN_LIMIT).
    """
    conn = _connect(db_path)
    try:
        streams, streams_total, threads_skipped = _plan(conn, keep_per_thread, frozenset(skip_thread_ids))
        cp_rows, write_rows, reclaim = _measure_victims(conn, streams)

        result = PruneResult(
            dry_run=dry_run,
            keep_per_thread=keep_per_thread,
            streams_total=streams_total,
            streams_pruned=len(streams),
            threads_skipped=threads_skipped,
            checkpoints_deleted=cp_rows,
            writes_deleted=write_rows,
            reclaimable_bytes=reclaim,
            skipped_thread_ids=sorted(skip_thread_ids),
        )
        if dry_run or not streams:
            return result

        cp_deleted, w_deleted, txns = _apply(conn, streams, batch_rows)
        result.checkpoints_deleted = cp_deleted
        result.writes_deleted = w_deleted
        result.transactions = txns
        return result
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _format_bytes(n: int) -> str:
    gib = n / (1024**3)
    if gib >= 1:
        return f"{gib:.2f} GiB"
    return f"{n / (1024**2):.1f} MiB"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m deerflow.runtime.checkpointer.prune",
        description="Prune SQLite checkpointer history to the newest N checkpoints per stream (dry-run by default; --apply deletes).",
    )
    parser.add_argument("--db", required=True, help="Path to the checkpointer SQLite database (e.g. .deer-flow/data/deerflow.db)")
    parser.add_argument(
        "--keep",
        type=int,
        default=REGENERATE_HISTORY_RAW_SCAN_LIMIT,
        help="Checkpoints to keep per (thread, ns) stream. Default = regenerate window (400). LOWERING THIS BREAKS regenerate for older turns — behavior change.",
    )
    parser.add_argument("--batch-rows", type=int, default=DEFAULT_BATCH_ROWS, help="Max rows deleted per transaction (WAL-friendly)")
    parser.add_argument("--skip-thread", action="append", default=[], metavar="THREAD_ID", help="Thread to leave untouched (repeatable), e.g. one with a running run")
    parser.add_argument("--apply", action="store_true", help="Actually delete (default: dry-run report only)")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON instead of the human summary")
    args = parser.parse_args(argv)

    if args.keep < 1:
        print(f"error: --keep must be >= 1, got {args.keep}", file=sys.stderr)
        return 2
    if args.batch_rows < 1:
        print(f"error: --batch-rows must be >= 1, got {args.batch_rows}", file=sys.stderr)
        return 2

    if not Path(args.db).exists():
        print(f"error: database not found: {args.db}", file=sys.stderr)
        return 2

    result = prune_checkpoints(
        args.db,
        args.keep,
        dry_run=not args.apply,
        batch_rows=args.batch_rows,
        skip_thread_ids=frozenset(args.skip_thread),
    )

    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False))
        return 0

    verb = "DRY-RUN would delete" if result.dry_run else "deleted"
    print(
        f"{verb} {result.checkpoints_deleted} checkpoint(s) + {result.writes_deleted} writes row(s) "
        f"({result.checkpoints_deleted + result.writes_deleted} rows) across {result.streams_pruned} stream(s); "
        f"estimated reclaim on VACUUM: {_format_bytes(result.reclaimable_bytes)}"
    )
    print(f"keep_per_thread={result.keep_per_thread} batch_rows={args.batch_rows} transactions={result.transactions}")
    if result.skipped_thread_ids:
        print(f"skipped threads: {', '.join(result.skipped_thread_ids)}")
    if result.dry_run:
        print("dry-run only — re-run with --apply to delete; file size shrinks only after VACUUM INTO (plan A2)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
