"""Task 10: 旧容器（不同 HOSTNAME）留下的过期 lease composing 任务被回收置 failed。

修前：recover_stale_tasks 只认 claimed_by=WORKER_ID——docker recreate 换 HOSTNAME 后
旧 claimed 行永无人认领（claim 要求 claimed_by IS NULL）→ 任务永久卡 95%。
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def _seed_task(db_path, *, claimed_by, lease_offset_sec, status="composing"):
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS unified_tasks (
                id TEXT PRIMARY KEY, domain TEXT, task_type TEXT, user_id TEXT,
                status TEXT, progress INTEGER, payload TEXT, result TEXT,
                error_message TEXT, retry_count INTEGER, priority INTEGER,
                claimed_by TEXT, claimed_at TEXT, lease_expires_at TEXT,
                created_at TEXT, updated_at TEXT
            )
            """
        )
        now = datetime.now(UTC)
        lease = (now + timedelta(seconds=lease_offset_sec)).isoformat()
        conn.execute(
            """
            INSERT OR REPLACE INTO unified_tasks
            (id, domain, task_type, user_id, status, progress, payload, retry_count,
             priority, claimed_by, claimed_at, lease_expires_at, created_at, updated_at)
            VALUES (?, 'video_gen', 'ai_video', 'default', ?, 95, '{}', 0, 0, ?, ?, ?, ?, ?)
            """,
            ("vg_orphan1", status, claimed_by, now.isoformat(), lease, now.isoformat(), now.isoformat()),
        )
        conn.commit()
    finally:
        conn.close()


def test_expired_lease_from_other_worker_recovered(tmp_path, monkeypatch):
    db = str(tmp_path / "tasks.db")
    _seed_task(db, claimed_by="old-container-host", lease_offset_sec=-300)  # 过期 5 分钟

    import worker  # docker/production-worker/worker.py

    monkeypatch.setattr(worker, "DB_PATH", db)
    worker.recover_stale_tasks()

    conn = sqlite3.connect(db)
    row = conn.execute("SELECT status, claimed_by, error_message, retry_count FROM unified_tasks WHERE id='vg_orphan1'").fetchone()
    conn.close()
    # Task 25 起：过期租约且未超重试上限 → **重排队**而非直接判 failed。
    # 本测试原本要锁的 P0-4 语义（不认 claimed_by，任何 replica 的孤儿都能收）
    # 由 claimed_by 归 NULL 保证——那才是「能被重新认领」的充要条件。
    # 直接 failed 的旧行为是缺陷：租约过期也可能只是任务跑得久（渲染期从不续租），
    # 健康长任务会被兄弟 replica 打成 failed。详见 test_worker_lease_lifecycle.py。
    assert row[0] == "composing", "未超重试上限应重排队"
    assert row[1] is None, "必须放开 claimed_by，孤儿才能被任何 replica 重新认领"
    assert row[3] == 1, "重排队必须计次，否则无限重排"


def test_unexpired_lease_untouched(tmp_path, monkeypatch):
    db = str(tmp_path / "tasks.db")
    _seed_task(db, claimed_by="other-live-worker", lease_offset_sec=3600)  # 未过期

    import worker

    monkeypatch.setattr(worker, "DB_PATH", db)
    worker.recover_stale_tasks()

    conn = sqlite3.connect(db)
    row = conn.execute("SELECT status, claimed_by FROM unified_tasks WHERE id='vg_orphan1'").fetchone()
    conn.close()
    assert row[0] == "composing" and row[1] == "other-live-worker"
