#!/usr/bin/env python3
"""Clean up old LangGraph checkpoints to reduce database size.

LangGraph creates a checkpoint for every graph step. Over time, this
accumulates massive amounts of data (the biggest thread in this database
has 1869 checkpoints totaling 11 GB). Only the latest checkpoint (and its
parent for rollback support) is needed for normal operations.

This script:
1. Backs up the database before any changes
2. Keeps the latest + parent checkpoint per thread
3. Deletes all older checkpoints and their associated writes
4. VACUUMs the database to reclaim disk space

User-visible impact:
  - Current conversations: NO IMPACT (latest checkpoint preserved)
  - Rollback (cancel action): NO IMPACT (parent checkpoint preserved)
  - Time-travel / history browsing: LOSES old history entries
  - Interrupted thread resume: NO IMPACT (latest checkpoint preserved)

Usage:
  # MUST stop gateway first!
  make stop   # or: pkill -f uvicorn
  python3 scripts/cleanup_checkpoints.py [--dry-run] [--db-path PATH]
  # Then restart gateway
  make dev

IMPORTANT: This script MUST be run with the gateway STOPPED.
SQLite does not handle concurrent writes well — running this while
the gateway is active risks database corruption.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import subprocess
import sys
import time


def main() -> None:
    parser = argparse.ArgumentParser(description="Clean up old LangGraph checkpoints")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be deleted without making changes")
    parser.add_argument("--db-path", default=None, help="Path to deerflow.db (default: auto-detect)")
    args = parser.parse_args()

    # Find database
    if args.db_path:
        db_path = args.db_path
    else:
        # Search common locations (works from both project root and backend/)
        script_dir = os.path.dirname(os.path.abspath(__file__))
        candidates = [
            os.path.join(os.getcwd(), ".deer-flow/data/deerflow.db"),
            os.path.join(os.getcwd(), "backend/.deer-flow/data/deerflow.db"),
            os.path.join(os.path.dirname(os.getcwd()), "backend/.deer-flow/data/deerflow.db"),
            os.path.join(script_dir, "..", ".deer-flow/data/deerflow.db"),  # relative to backend/scripts/
        ]
        db_path = None
        for c in candidates:
            if os.path.exists(c):
                db_path = c
                break
        if db_path is None:
            print("ERROR: Cannot find deerflow.db. Use --db-path to specify.")
            sys.exit(1)

    if not os.path.exists(db_path):
        print(f"ERROR: Database not found at {db_path}")
        sys.exit(1)

    # Check if gateway is running
    try:
        result = subprocess.run(["pgrep", "-f", "uvicorn.*gateway"], capture_output=True, text=True)
        if result.returncode == 0:
            print("ERROR: Gateway appears to be running!")
            print("  This script modifies the SQLite database directly and MUST NOT")
            print("  run while the gateway is active (risk of database corruption).")
            print("  Stop the gateway first:  make stop")
            sys.exit(1)
    except FileNotFoundError:
        # pgrep not available, skip check
        pass

    db_size_mb = os.path.getsize(db_path) / 1024 / 1024
    print(f"Database: {db_path}")
    print(f"Current size: {db_size_mb:.0f} MB")

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # ---- Step 1: Analyze ----
    print("\n=== ANALYSIS ===")

    cur.execute("SELECT COUNT(*) FROM checkpoints WHERE checkpoint_ns = ''")
    total_checkpoints = cur.fetchone()[0]
    cur.execute("SELECT COUNT(DISTINCT thread_id) FROM checkpoints")
    total_threads = cur.fetchone()[0]
    cur.execute("SELECT SUM(LENGTH(checkpoint)) FROM checkpoints WHERE checkpoint_ns = ''")
    total_checkpoint_bytes = cur.fetchone()[0] or 0
    cur.execute("SELECT SUM(LENGTH(value)) FROM writes")
    total_writes_bytes = cur.fetchone()[0] or 0

    print(f"Threads: {total_threads}")
    print(f"Total checkpoints: {total_checkpoints}")
    print(f"Total checkpoint data: {total_checkpoint_bytes / 1024 / 1024:.0f} MB")
    print(f"Total writes data: {total_writes_bytes / 1024 / 1024:.0f} MB")

    # Find checkpoint IDs to KEEP (latest + parent per thread)
    cur.execute("""
        WITH ranked AS (
            SELECT thread_id, checkpoint_ns, checkpoint_id,
                   ROW_NUMBER() OVER (
                       PARTITION BY thread_id, checkpoint_ns
                       ORDER BY checkpoint_id DESC
                   ) as rn
            FROM checkpoints
        )
        SELECT thread_id, checkpoint_ns, checkpoint_id
        FROM ranked
        WHERE rn <= 2
    """)
    keep_ids = {(row[0], row[1], row[2]) for row in cur.fetchall()}
    keep_checkpoint_ids = {row[2] for row in keep_ids}

    # Calculate sizes
    cur.execute(
        f"""
        SELECT SUM(LENGTH(checkpoint)) FROM checkpoints
        WHERE checkpoint_id IN ({",".join("?" * len(keep_checkpoint_ids))})
    """,
        list(keep_checkpoint_ids),
    )
    keep_checkpoint_bytes = cur.fetchone()[0] or 0

    cur.execute(
        f"""
        SELECT COUNT(*) FROM checkpoints
        WHERE checkpoint_id NOT IN ({",".join("?" * len(keep_checkpoint_ids))})
    """,
        list(keep_checkpoint_ids),
    )
    delete_checkpoint_count = cur.fetchone()[0]

    cur.execute(
        f"""
        SELECT SUM(LENGTH(checkpoint)) FROM checkpoints
        WHERE checkpoint_id NOT IN ({",".join("?" * len(keep_checkpoint_ids))})
    """,
        list(keep_checkpoint_ids),
    )
    delete_checkpoint_bytes = cur.fetchone()[0] or 0

    # Writes to delete
    cur.execute(
        """
        SELECT COUNT(*) FROM writes w
        WHERE NOT EXISTS (
            SELECT 1 FROM checkpoints c
            WHERE c.thread_id = w.thread_id
            AND c.checkpoint_ns = w.checkpoint_ns
            AND c.checkpoint_id = w.checkpoint_id
            AND c.checkpoint_id IN ({})
        )
    """.format(",".join("?" * len(keep_checkpoint_ids))),
        list(keep_checkpoint_ids),
    )
    delete_writes_count = cur.fetchone()[0]

    cur.execute(
        """
        SELECT SUM(LENGTH(value)) FROM writes w
        WHERE NOT EXISTS (
            SELECT 1 FROM checkpoints c
            WHERE c.thread_id = w.thread_id
            AND c.checkpoint_ns = w.checkpoint_ns
            AND c.checkpoint_id = w.checkpoint_id
            AND c.checkpoint_id IN ({})
        )
    """.format(",".join("?" * len(keep_checkpoint_ids))),
        list(keep_checkpoint_ids),
    )
    delete_writes_bytes = cur.fetchone()[0] or 0

    print(f"\nKeep: {len(keep_checkpoint_ids)} checkpoints ({keep_checkpoint_bytes / 1024 / 1024:.0f} MB)")
    print(f"Delete: {delete_checkpoint_count} checkpoints ({delete_checkpoint_bytes / 1024 / 1024:.0f} MB)")
    print(f"Delete: {delete_writes_count} writes ({delete_writes_bytes / 1024 / 1024:.0f} MB)")
    print(f"\nEstimated space freed: {(delete_checkpoint_bytes + delete_writes_bytes) / 1024 / 1024:.0f} MB")
    print(f"Estimated remaining size: ~{(keep_checkpoint_bytes + total_writes_bytes - delete_writes_bytes) / 1024 / 1024:.0f} MB (before VACUUM)")

    if args.dry_run:
        print("\n=== DRY RUN - No changes made ===")
        conn.close()
        return

    # ---- Step 2: Backup ----
    print("\n=== BACKUP ===")
    backup_path = db_path + f".backup-{time.strftime('%Y%m%d-%H%M%S')}"
    print(f"Creating backup: {backup_path}")
    shutil.copy2(db_path, backup_path)
    backup_size_mb = os.path.getsize(backup_path) / 1024 / 1024
    print(f"Backup created: {backup_size_mb:.0f} MB")

    # ---- Step 3: Delete old checkpoints ----
    print("\n=== DELETING OLD CHECKPOINTS ===")

    # Delete old writes first (they reference checkpoints)
    cur.execute(
        """
        DELETE FROM writes w
        WHERE NOT EXISTS (
            SELECT 1 FROM checkpoints c
            WHERE c.thread_id = w.thread_id
            AND c.checkpoint_ns = w.checkpoint_ns
            AND c.checkpoint_id = w.checkpoint_id
            AND c.checkpoint_id IN ({})
        )
    """.format(",".join("?" * len(keep_checkpoint_ids))),
        list(keep_checkpoint_ids),
    )
    deleted_writes = cur.rowcount
    print(f"Deleted {deleted_writes} old writes")

    # Delete old checkpoints
    cur.execute(
        f"""
        DELETE FROM checkpoints
        WHERE checkpoint_id NOT IN ({",".join("?" * len(keep_checkpoint_ids))})
    """,
        list(keep_checkpoint_ids),
    )
    deleted_checkpoints = cur.rowcount
    print(f"Deleted {deleted_checkpoints} old checkpoints")

    conn.commit()

    # ---- Step 4: VACUUM ----
    print("\n=== VACUUM ===")
    print("Reclaiming disk space... (this may take a few minutes)")
    cur.execute("VACUUM")
    conn.commit()

    new_size_mb = os.path.getsize(db_path) / 1024 / 1024
    print(f"Database size after VACUUM: {new_size_mb:.0f} MB")
    print(f"Space freed: {db_size_mb - new_size_mb:.0f} MB")
    print(f"Size reduction: {(db_size_mb - new_size_mb) / db_size_mb * 100:.0f}%")

    # ---- Step 5: Verify ----
    print("\n=== VERIFICATION ===")
    cur.execute("SELECT COUNT(*) FROM checkpoints WHERE checkpoint_ns = ''")
    remaining = cur.fetchone()[0]
    print(f"Remaining checkpoints: {remaining} (expected: ~{len(keep_checkpoint_ids)})")

    cur.execute("SELECT COUNT(DISTINCT thread_id) FROM checkpoints")
    remaining_threads = cur.fetchone()[0]
    print(f"Remaining threads with checkpoints: {remaining_threads} (expected: {total_threads})")

    # Verify every thread still has at least one checkpoint
    cur.execute("""
        SELECT tm.thread_id
        FROM threads_meta tm
        WHERE tm.thread_id NOT IN (
            SELECT DISTINCT thread_id FROM checkpoints WHERE checkpoint_ns = ''
        )
    """)
    missing = cur.fetchall()
    if missing:
        print(f"WARNING: {len(missing)} threads lost their last checkpoint!")
        for row in missing:
            print(f"  {row[0]}")
    else:
        print("All threads still have their latest checkpoint: OK")

    conn.close()
    print("\n=== DONE ===")
    print(f"Backup at: {backup_path}")
    print("If anything goes wrong, restore with:")
    print(f"  cp {backup_path} {db_path}")
    print("Then restart gateway.")


if __name__ == "__main__":
    main()
