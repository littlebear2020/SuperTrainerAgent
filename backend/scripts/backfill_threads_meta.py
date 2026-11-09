"""One-time (re-runnable) backfill: create missing ``threads_meta`` rows.

SEC-M1 step ① (安全加固批次 2 项 2.15): ``check_access`` will later tighten to
"missing threads_meta row counts as a denial" (批次 3 项 3.3, window item).
Before that window, every thread that exists in ``checkpoints`` / ``runs`` but
lacks a ``threads_meta`` row must be backfilled so the tightening changes no
existing behavior. NULL owner stays readable under current semantics — this
script therefore **only INSERTs missing rows** (owner NULL) and touches nothing
else.

Data source: ``DISTINCT thread_id`` union of ``checkpoints`` ∪ ``runs``
(some sessions have runs but no checkpoint — 复核反馈补充, adopted).

Usage (from backend/):
    uv run python scripts/backfill_threads_meta.py --db .deer-flow/data/deerflow.db --dry-run
    uv run python scripts/backfill_threads_meta.py --db .deer-flow/data/deerflow.db

Safety:
- 只应在本机开发库运行；实跑前自动备份 DB 文件（sqlite3 backup API，
  WAL 一致）到同目录 ``backups/deerflow-backfill-<ts>.db``；
- 幂等：已存在的行一律不动（INSERT OR IGNORE 语义），可重复跑增量；
- 迁移清单 JSON 永久留存（回滚时按清单删除）；dry-run 不写库。
- ``migrate_user_isolation.py`` 只搬文件不建行，不可复用。
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)


def collect_source_thread_ids(conn: sqlite3.Connection) -> tuple[set[str], dict[str, int]]:
    """Union of DISTINCT thread_id from checkpoints ∪ runs (missing tables tolerated)."""
    union: set[str] = set()
    breakdown: dict[str, int] = {}
    for table in ("checkpoints", "runs"):
        try:
            rows = conn.execute(f"SELECT DISTINCT thread_id FROM {table}").fetchall()  # noqa: S608 — fixed table name
        except sqlite3.OperationalError:
            breakdown[table] = 0
            logger.warning("table %s missing in db — skipping source", table)
            continue
        ids = {r[0] for r in rows if r[0]}
        breakdown[table] = len(ids)
        union |= ids
    return union, breakdown


def backfill(
    db_path: Path,
    *,
    dry_run: bool = False,
    owner: str | None = None,
) -> dict:
    """Backfill missing threads_meta rows. Returns a report dict (also the manifest)."""
    conn = sqlite3.connect(str(db_path))
    try:
        existing = {r[0] for r in conn.execute("SELECT thread_id FROM threads_meta").fetchall()}
        source_ids, breakdown = collect_source_thread_ids(conn)
        missing = sorted(source_ids - existing)

        report: dict = {
            "db": str(db_path),
            "dry_run": dry_run,
            "owner": owner,
            "source_breakdown": breakdown,
            "source_union": len(source_ids),
            "existing_threads_meta": len(existing),
            "missing_to_backfill": len(missing),
            "inserted": 0,
            "thread_ids": missing,
        }
        if dry_run or not missing:
            return report

        # Backup via sqlite3 backup API (WAL-consistent) before any write.
        backup_dir = db_path.parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        backup_path = backup_dir / f"deerflow-backfill-{stamp}.db"
        with sqlite3.connect(str(backup_path)) as target:
            conn.backup(target)
        report["backup"] = str(backup_path)

        now = datetime.now(UTC).isoformat()  # 复核：带时区标注的 UTC 时间戳
        with conn:
            conn.executemany(
                "INSERT OR IGNORE INTO threads_meta (thread_id, assistant_id, user_id, display_name, status, metadata_json, created_at, updated_at) VALUES (?, NULL, ?, NULL, 'idle', '{}', ?, ?)",
                [(tid, owner, now, now) for tid in missing],
            )
        report["inserted"] = len(missing)

        # Manifest kept permanently (rollback: delete these thread_ids).
        manifest_path = backup_dir / f"threads-meta-backfill-{stamp}.json"
        manifest_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        report["manifest"] = str(manifest_path)
        return report
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="Path to the SQLite database (e.g. .deer-flow/data/deerflow.db)")
    parser.add_argument("--dry-run", action="store_true", help="Report only, write nothing.")
    parser.add_argument("--owner", default=None, help="user_id for backfilled rows (default: NULL — stays readable under current semantics)")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    db_path = Path(args.db).expanduser().resolve()
    if not db_path.exists():
        logger.error("database not found: %s", db_path)
        return 1

    report = backfill(db_path, dry_run=args.dry_run, owner=args.owner)
    print(json.dumps({k: v for k, v in report.items() if k != "thread_ids"}, ensure_ascii=False, indent=2))
    print(f"thread_ids ({len(report['thread_ids'])}): {report['thread_ids'][:20]}{' ...' if len(report['thread_ids']) > 20 else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
