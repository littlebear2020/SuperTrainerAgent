"""SEC-M1 step ① (批次 2 项 2.15): threads_meta 回填脚本单元测试.

脚本契约：checkpoints ∪ runs 的 thread_id 并集为数据源；只为缺失者建行
（owner NULL，现行语义仍可读 = 回填不改变任何现有行为）；幂等（可重复
跑增量）；dry-run 不写库；实跑前自动备份 + 永久清单。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from scripts.backfill_threads_meta import backfill


def _make_db(path: Path, *, checkpoints=("t-cp1", "t-cp2"), runs=("t-run1",), meta=("t-cp1",)) -> None:
    conn = sqlite3.connect(str(path))
    with conn:
        conn.execute("CREATE TABLE checkpoints (thread_id TEXT)")
        conn.execute("CREATE TABLE runs (thread_id TEXT)")
        conn.execute("CREATE TABLE threads_meta (thread_id TEXT PRIMARY KEY, assistant_id TEXT, user_id TEXT, display_name TEXT, status TEXT NOT NULL, metadata_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
        conn.executemany("INSERT INTO checkpoints VALUES (?)", [(t,) for t in checkpoints])
        conn.executemany("INSERT INTO runs VALUES (?)", [(t,) for t in runs])
        conn.executemany("INSERT INTO threads_meta (thread_id, status, metadata_json, created_at, updated_at) VALUES (?, 'idle', '{}', '2026-01-01', '2026-01-01')", [(t,) for t in meta])
    conn.close()


def test_dry_run_reports_missing_without_writing(tmp_path):
    db = tmp_path / "deerflow.db"
    _make_db(db)
    report = backfill(db, dry_run=True)
    assert report["missing_to_backfill"] == 2  # t-cp2 (cp) + t-run1 (runs-only)
    assert set(report["thread_ids"]) == {"t-cp2", "t-run1"}
    assert report["source_breakdown"] == {"checkpoints": 2, "runs": 1}
    assert report["source_union"] == 3
    conn = sqlite3.connect(str(db))
    assert conn.execute("SELECT COUNT(*) FROM threads_meta").fetchone()[0] == 1
    conn.close()
    assert not (tmp_path / "backups").exists()  # dry-run 不留备份


def test_apply_inserts_missing_rows_owner_null(tmp_path):
    db = tmp_path / "deerflow.db"
    _make_db(db)
    report = backfill(db)
    assert report["inserted"] == 2
    conn = sqlite3.connect(str(db))
    rows = dict(conn.execute("SELECT thread_id, user_id FROM threads_meta").fetchall())
    conn.close()
    assert rows == {"t-cp1": None, "t-cp2": None, "t-run1": None}  # 既有行不动，新行 owner NULL
    # 备份 + 永久清单落盘
    backups = tmp_path / "backups"
    assert any(b.suffix == ".db" for b in backups.iterdir())
    manifests = [b for b in backups.iterdir() if b.suffix == ".json"]
    assert len(manifests) == 1
    manifest = json.loads(manifests[0].read_text(encoding="utf-8"))
    assert set(manifest["thread_ids"]) == {"t-cp2", "t-run1"}


def test_idempotent_rerun_is_noop(tmp_path):
    db = tmp_path / "deerflow.db"
    _make_db(db)
    backfill(db)
    report = backfill(db)  # 再跑：无缺失 → 不写、不再留备份
    assert report["missing_to_backfill"] == 0
    assert "backup" not in report


def test_runs_only_threads_are_covered(tmp_path):
    """有 run 无 checkpoint 的会话也须回填（复核反馈补充已采纳）。"""
    db = tmp_path / "deerflow.db"
    _make_db(db, checkpoints=("t-cp1",), runs=("t-run-a", "t-run-b"), meta=("t-cp1",))
    report = backfill(db, dry_run=True)
    assert set(report["thread_ids"]) == {"t-run-a", "t-run-b"}
