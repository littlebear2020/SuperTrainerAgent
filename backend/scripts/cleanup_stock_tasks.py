"""Cleanup script: remove legacy stock/stock_footage/stock_clip tasks.

Run BEFORE deploying the Pexels-removal change to the company server.
Idempotent — safe to run multiple times.

Usage:
    cd backend && PYTHONPATH=. python scripts/cleanup_stock_tasks.py [--dry-run] [--db-path PATH]

Removes:
  1. DB rows in unified_tasks where task_type in (stock, stock_footage, stock_clip).
  2. On-disk task directories under .deer-flow/video-gen/<user_id>/tasks/<task_id>/.

DB path auto-detection mirrors backend/scripts/cleanup_checkpoints.py (verified
real path is .deer-flow/data/deerflow.db, NOT .deer-flow/checkpointer.db).
"""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
from pathlib import Path

# Match video_gen/service.py storage layout.
STOCK_TYPES = ("stock", "stock_footage", "stock_clip")

# DB auto-detect candidates — mirrors cleanup_checkpoints.py. Real path confirmed
# via app_config.py:38 sqlite_dir=".deer-flow/data" + test_e2e_recompose.py:33.
# Run from backend/ or repo root; script_dir relative path covers backend/scripts/.
_SCRIPT_DIR = Path(__file__).resolve().parent


def find_db(explicit: str | None = None) -> Path:
    """Locate the deerflow.db SQLite DB. explicit overrides auto-detect."""
    if explicit:
        p = Path(explicit).expanduser()
        if not p.exists():
            raise FileNotFoundError(f"--db-path {p} does not exist")
        return p
    candidates = [
        Path(os.getcwd(), ".deer-flow", "data", "deerflow.db"),
        Path(os.getcwd(), "backend", ".deer-flow", "data", "deerflow.db"),
        Path(os.path.dirname(os.getcwd()), "backend", ".deer-flow", "data", "deerflow.db"),
        Path(_SCRIPT_DIR, "..", ".deer-flow", "data", "deerflow.db"),  # backend/scripts/../.deer-flow
    ]
    for c in candidates:
        if c.exists():
            return c.resolve()
    raise FileNotFoundError("Could not find deerflow.db. Expected one of: " + ", ".join(str(c) for c in candidates) + ". Use --db-path to specify.")


def find_storage_root() -> Path:
    """Locate the video-gen storage dir. Mirrors find_db candidate logic."""
    candidates = [
        Path(os.getcwd(), ".deer-flow", "video-gen"),
        Path(os.getcwd(), "backend", ".deer-flow", "video-gen"),
        Path(os.path.dirname(os.getcwd()), "backend", ".deer-flow", "video-gen"),
        Path(_SCRIPT_DIR, "..", ".deer-flow", "video-gen"),
    ]
    for c in candidates:
        if c.exists():
            return c.resolve()
    return candidates[0]  # return default even if not exists (for dry-run reporting)


def list_stock_tasks(db_path: Path) -> list[tuple[str, str, str]]:
    """Return [(task_id, user_id, task_type), ...] for stock-type tasks."""
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            f"SELECT id, user_id, task_type FROM unified_tasks WHERE task_type IN ({','.join('?' * len(STOCK_TYPES))})",
            STOCK_TYPES,
        ).fetchall()
    finally:
        conn.close()
    return [(r[0], r[1], r[2]) for r in rows]


def delete_task_rows(db_path: Path, task_ids: list[str]) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executemany("DELETE FROM unified_tasks WHERE id = ?", [(t,) for t in task_ids])
        conn.commit()
    finally:
        conn.close()


def delete_task_dirs(tasks: list[tuple[str, str, str]]) -> list[str]:
    """Delete on-disk task dirs. Returns list of deleted paths."""
    storage_root = find_storage_root()
    deleted: list[str] = []
    for task_id, user_id, _ in tasks:
        task_dir = storage_root / user_id / "tasks" / task_id
        if task_dir.exists():
            shutil.rmtree(task_dir, ignore_errors=True)
            deleted.append(str(task_dir))
    return deleted


def main() -> None:
    parser = argparse.ArgumentParser(description="Remove legacy stock video tasks.")
    parser.add_argument("--dry-run", action="store_true", help="List what would be deleted, don't delete.")
    parser.add_argument("--db-path", default=None, help="Path to deerflow.db (default: auto-detect)")
    args = parser.parse_args()

    db_path = find_db(args.db_path)
    storage_root = find_storage_root()
    tasks = list_stock_tasks(db_path)
    print(f"DB: {db_path}")
    print(f"Storage root: {storage_root}")
    print(f"Found {len(tasks)} stock-type task(s):")
    for tid, uid, ttype in tasks:
        print(f"  {ttype:15s}  user={uid}  task={tid}")

    if not tasks:
        print("Nothing to clean. Exiting.")
        return

    if args.dry_run:
        print("\n[dry-run] No rows/dirs deleted. Re-run without --dry-run to delete.")
        return

    delete_task_rows(db_path, [t[0] for t in tasks])
    print(f"\nDeleted {len(tasks)} DB row(s).")

    deleted_dirs = delete_task_dirs(tasks)
    print(f"Deleted {len(deleted_dirs)} task dir(s):")
    for d in deleted_dirs:
        print(f"  {d}")


if __name__ == "__main__":
    main()
