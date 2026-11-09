"""Tests for checkpointer retention pruning (storage plan 2026-09-02, batch A1).

Covers ``deerflow.runtime.checkpointer.prune``:

- keep-per-stream retention aligned with the regenerate design window
  (``REGENERATE_HISTORY_RAW_SCAN_LIMIT``);
- ``writes`` rows cascade with their checkpoint rows;
- batched DELETE transactions never exceed the configured row budget;
- dry-run never mutates the database;
- ``(thread_id, checkpoint_ns)`` grouping (subgraph namespaces prune
  independently);
- CLI ``main()`` dry-run reporting.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path

from deerflow.runtime.checkpointer.prune import (
    REGENERATE_HISTORY_RAW_SCAN_LIMIT,
    prune_checkpoints,
)
from deerflow.runtime.checkpointer.prune import (
    main as prune_main,
)

# ---------------------------------------------------------------------------
# Test-database helpers — mirror the AsyncSqliteSaver.setup() schema exactly
# (langgraph_checkpoint_sqlite 3.0.3: two tables, blobs inline).
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS checkpoints (
    thread_id TEXT NOT NULL,
    checkpoint_ns TEXT NOT NULL DEFAULT '',
    checkpoint_id TEXT NOT NULL,
    parent_checkpoint_id TEXT,
    type TEXT,
    checkpoint BLOB,
    metadata BLOB,
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
);
CREATE TABLE IF NOT EXISTS writes (
    thread_id TEXT NOT NULL,
    checkpoint_ns TEXT NOT NULL DEFAULT '',
    checkpoint_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    idx INTEGER NOT NULL,
    channel TEXT NOT NULL,
    type TEXT,
    value BLOB,
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
);
"""


def _ckpt_id(seq: int) -> str:
    """Sortable langgraph-style checkpoint id (``1-<hex ts>-<uuid>`` layout)."""
    return f"1-{seq:016x}-{uuid.uuid4().hex}"


def _make_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.executescript(_SCHEMA)
    return conn


def _seed_stream(
    conn: sqlite3.Connection,
    thread_id: str,
    count: int,
    *,
    ns: str = "",
    writes_per_checkpoint: int = 0,
    blob_bytes: int = 1024,
) -> list[str]:
    """Insert ``count`` checkpoints (ascending ids) + optional writes rows."""
    ids: list[str] = []
    for seq in range(count):
        cid = _ckpt_id(seq)
        ids.append(cid)
        conn.execute(
            "INSERT INTO checkpoints (thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id, type, checkpoint, metadata) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (thread_id, ns, cid, ids[seq - 1] if seq else None, "json", b"x" * blob_bytes, b"{}"),
        )
        for w in range(writes_per_checkpoint):
            conn.execute(
                "INSERT INTO writes (thread_id, checkpoint_ns, checkpoint_id, task_id, idx, channel, type, value) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (thread_id, ns, cid, f"task-{w}", w, "messages", "json", b"y" * 64),
            )
    conn.commit()
    return ids


def _count(conn: sqlite3.Connection, table: str, thread_id: str, ns: str = "") -> int:
    return conn.execute(f"SELECT COUNT(*) FROM {table} WHERE thread_id=? AND checkpoint_ns=?", (thread_id, ns)).fetchone()[0]


# ---------------------------------------------------------------------------
# Retention aligned with the regenerate window
# ---------------------------------------------------------------------------


def test_prune_keeps_latest_n_per_thread_and_preserves_regenerate_window(tmp_path: Path):
    """2 threads x 500 checkpoints, keep=400: exactly the newest 400 survive.

    The regenerate base scan reads up to REGENERATE_HISTORY_RAW_SCAN_LIMIT
    raw checkpoints per thread; after prune the full remaining history must
    still be visible to that scan (no truncation at the window edge).
    """
    conn = _make_db(tmp_path / "prune.db")
    ids_a = _seed_stream(conn, "thread-a", 500, writes_per_checkpoint=1)
    ids_b = _seed_stream(conn, "thread-b", 500, writes_per_checkpoint=1)

    result = prune_checkpoints(tmp_path / "prune.db", 400, dry_run=False)

    # Exactly the oldest 100 per thread were dropped
    assert result.checkpoints_deleted == 200
    assert result.writes_deleted == 200
    assert _count(conn, "checkpoints", "thread-a") == 400
    assert _count(conn, "checkpoints", "thread-b") == 400

    # Newest 400 kept, oldest 100 gone — per stream, ordered by checkpoint_id
    for ids in (ids_a, ids_b):
        kept = conn.execute(
            "SELECT checkpoint_id FROM checkpoints WHERE thread_id=? ORDER BY checkpoint_id",
            ("thread-a" if ids is ids_a else "thread-b",),
        ).fetchall()
        assert [r[0] for r in kept] == ids[100:]

    # Regenerate window intact: a history scan with the raw limit (ORDER BY
    # checkpoint_id DESC LIMIT 400 — the saver's own ordering) sees every
    # remaining checkpoint, i.e. the oldest kept one is still inside the window.
    oldest_kept = conn.execute("SELECT MIN(checkpoint_id) FROM checkpoints WHERE thread_id='thread-a'").fetchone()[0]
    visible = conn.execute(
        "SELECT COUNT(*) FROM (SELECT checkpoint_id FROM checkpoints WHERE thread_id='thread-a' ORDER BY checkpoint_id DESC LIMIT ?)",
        (REGENERATE_HISTORY_RAW_SCAN_LIMIT,),
    ).fetchone()[0]
    assert visible == 400
    assert oldest_kept == ids_a[100]

    conn.close()


def test_default_keep_is_regenerate_window_constant(tmp_path: Path):
    """Omitting keep_per_thread keeps the regenerate raw-scan limit (400)."""
    conn = _make_db(tmp_path / "prune.db")
    _seed_stream(conn, "thread-a", 401)

    result = prune_checkpoints(tmp_path / "prune.db", dry_run=False)  # keep not specified

    assert result.keep_per_thread == REGENERATE_HISTORY_RAW_SCAN_LIMIT
    assert REGENERATE_HISTORY_RAW_SCAN_LIMIT == 400
    assert _count(conn, "checkpoints", "thread-a") == 400
    conn.close()


def test_thread_runs_reexports_same_constants():
    """app.gateway.routers.thread_runs must share this module's constants (single source)."""
    from app.gateway.routers import thread_runs

    assert thread_runs.REGENERATE_HISTORY_RAW_SCAN_LIMIT is REGENERATE_HISTORY_RAW_SCAN_LIMIT
    assert thread_runs.REGENERATE_HISTORY_SCAN_LIMIT == 200


# ---------------------------------------------------------------------------
# writes cascade + batching + dry-run
# ---------------------------------------------------------------------------


def test_prune_deletes_writes_rows_with_their_checkpoints(tmp_path: Path):
    conn = _make_db(tmp_path / "prune.db")
    ids = _seed_stream(conn, "thread-a", 10, writes_per_checkpoint=2)

    prune_checkpoints(tmp_path / "prune.db", 4, dry_run=False)

    # Victims (oldest 6) lost both checkpoints and writes; survivors keep writes
    assert _count(conn, "checkpoints", "thread-a") == 4
    assert _count(conn, "writes", "thread-a") == 8
    for cid in ids[:6]:
        assert conn.execute("SELECT COUNT(*) FROM writes WHERE checkpoint_id=?", (cid,)).fetchone()[0] == 0
    for cid in ids[6:]:
        assert conn.execute("SELECT COUNT(*) FROM writes WHERE checkpoint_id=?", (cid,)).fetchone()[0] == 2
    conn.close()


def test_prune_batches_deletes_within_row_budget(tmp_path: Path):
    """No transaction deletes more rows than the configured batch budget."""
    conn = _make_db(tmp_path / "prune.db")
    _seed_stream(conn, "thread-a", 50, writes_per_checkpoint=1)  # 40 victims x2 rows = 80 rows

    statements: list[str] = []

    import deerflow.runtime.checkpointer.prune as prune_mod

    original_connect = prune_mod._connect

    def traced_connect(db_path):
        c = original_connect(db_path)
        c.set_trace_callback(statements.append)
        return c

    # Route through our traced connect by monkey-patching the module attribute
    prune_mod._connect = traced_connect
    try:
        result = prune_checkpoints(tmp_path / "prune.db", 10, dry_run=False, batch_rows=7)
    finally:
        prune_mod._connect = original_connect

    assert result.checkpoints_deleted == 40
    assert result.writes_deleted == 40

    # Parse trace: count DELETE statements between BEGIN/COMMIT boundaries
    txn_deletes: list[int] = []
    current = 0
    started = False
    for stmt in statements:
        head = stmt.split("(")[0].strip().upper()
        if head.startswith("BEGIN"):
            current, started = 0, True
        elif head.startswith("COMMIT"):
            if started:
                txn_deletes.append(current)
            current, started = 0, False
        elif head.startswith("DELETE"):
            current += 1
    assert txn_deletes, "expected at least one delete transaction in the trace"
    assert all(n <= 7 for n in txn_deletes), f"transaction budgets exceeded: {txn_deletes}"
    assert len(txn_deletes) > 1, "batching ineffective: everything deleted in one transaction"
    assert sum(txn_deletes) == 80
    conn.close()


def test_prune_dry_run_mutates_nothing(tmp_path: Path):
    conn = _make_db(tmp_path / "prune.db")
    _seed_stream(conn, "thread-a", 20, writes_per_checkpoint=1)

    result = prune_checkpoints(tmp_path / "prune.db", 5, dry_run=True)

    assert result.dry_run is True
    assert result.checkpoints_deleted == 15
    assert result.writes_deleted == 15
    assert _count(conn, "checkpoints", "thread-a") == 20
    assert _count(conn, "writes", "thread-a") == 20
    conn.close()


def test_prune_reports_reclaimable_bytes(tmp_path: Path):
    conn = _make_db(tmp_path / "prune.db")
    # 12 checkpoints x (1024-byte blob + 2-byte metadata); victims = 8
    _seed_stream(conn, "thread-a", 12, writes_per_checkpoint=1, blob_bytes=1024)

    result = prune_checkpoints(tmp_path / "prune.db", 4, dry_run=True)

    # 8 victim checkpoints x (1024 + 2 bytes) + 8 victim writes rows x 64 bytes
    assert result.reclaimable_bytes == 8 * (1024 + 2) + 8 * 64
    conn.close()


# ---------------------------------------------------------------------------
# Grouping + skip set
# ---------------------------------------------------------------------------


def test_prune_groups_by_thread_and_namespace(tmp_path: Path):
    """Subgraph namespaces (checkpoint_ns != '') keep their own newest N."""
    conn = _make_db(tmp_path / "prune.db")
    _seed_stream(conn, "thread-a", 10, ns="")
    _seed_stream(conn, "thread-a", 10, ns="subgraph:1")

    result = prune_checkpoints(tmp_path / "prune.db", 4, dry_run=False)

    assert result.checkpoints_deleted == 12
    assert _count(conn, "checkpoints", "thread-a", ns="") == 4
    assert _count(conn, "checkpoints", "thread-a", ns="subgraph:1") == 4
    conn.close()


def test_prune_skips_requested_threads(tmp_path: Path):
    conn = _make_db(tmp_path / "prune.db")
    _seed_stream(conn, "thread-a", 10)
    _seed_stream(conn, "thread-b", 10)

    result = prune_checkpoints(tmp_path / "prune.db", 4, dry_run=False, skip_thread_ids={"thread-b"})

    assert result.threads_skipped == 1
    assert _count(conn, "checkpoints", "thread-a") == 4
    assert _count(conn, "checkpoints", "thread-b") == 10
    conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_dry_run_reports_and_json(tmp_path: Path, capsys):
    conn = _make_db(tmp_path / "prune.db")
    _seed_stream(conn, "thread-a", 30, writes_per_checkpoint=1)
    conn.close()

    rc = prune_main(["--db", str(tmp_path / "prune.db"), "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["dry_run"] is True
    # keep defaults to the regenerate window; 30 < 400 → nothing to delete
    assert payload["checkpoints_deleted"] == 0
    assert payload["keep_per_thread"] == REGENERATE_HISTORY_RAW_SCAN_LIMIT


def test_cli_apply_deletes(tmp_path: Path, capsys):
    conn = _make_db(tmp_path / "prune.db")
    _seed_stream(conn, "thread-a", 6)
    conn.close()

    rc = prune_main(["--db", str(tmp_path / "prune.db"), "--keep", "2", "--apply"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "4" in out
    conn = sqlite3.connect(str(tmp_path / "prune.db"))
    assert conn.execute("SELECT COUNT(*) FROM checkpoints").fetchone()[0] == 2
    conn.close()


def test_cli_missing_db_fails_cleanly(tmp_path: Path, capsys):
    rc = prune_main(["--db", str(tmp_path / "nope.db")])
    assert rc == 2
    assert "nope.db" in capsys.readouterr().err


def test_cli_rejects_invalid_keep_cleanly(tmp_path: Path, capsys):
    """--keep 0 exits 2 with a one-line error, not a ValueError traceback (复核修复)."""
    db = tmp_path / "prune.db"
    db.touch()
    rc = prune_main(["--db", str(db), "--keep", "0"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "--keep must be >= 1" in err
    assert "Traceback" not in err


def test_cli_rejects_invalid_batch_rows_cleanly(tmp_path: Path, capsys):
    db = tmp_path / "prune.db"
    db.touch()
    rc = prune_main(["--db", str(db), "--apply", "--batch-rows", "0"])
    assert rc == 2
    assert "--batch-rows must be >= 1" in capsys.readouterr().err
