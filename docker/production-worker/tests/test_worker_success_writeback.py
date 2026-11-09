"""P0-1: 渲染成功后回写失败，不得清产物 + 标 failed。"""

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import worker  # noqa: E402


def _seed(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS unified_tasks (
            id TEXT PRIMARY KEY, user_id TEXT, domain TEXT, task_type TEXT,
            status TEXT, progress INTEGER DEFAULT 0, payload TEXT, result TEXT,
            error_message TEXT, claimed_by TEXT, claimed_at TEXT,
            lease_expires_at TEXT, retry_count INTEGER DEFAULT 0,
            dirty_frames TEXT DEFAULT '[]', priority INTEGER DEFAULT 0,
            created_at TEXT, updated_at TEXT)"""
    )
    conn.execute(
        "INSERT OR REPLACE INTO unified_tasks (id,user_id,domain,task_type,status,payload,claimed_by)"
        " VALUES ('t_ok','u1','video_gen','ai_image','composing','{}',?)",
        (worker.WORKER_ID,),
    )
    conn.commit()
    conn.close()


def _row(db_path: str) -> sqlite3.Row:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM unified_tasks WHERE id='t_ok'").fetchone()
    conn.close()
    return row


def test_success_writeback_retries_and_preserves_video(tmp_path, monkeypatch):
    db = str(tmp_path / "t.db")
    _seed(db)
    monkeypatch.setattr(worker, "DB_PATH", db)

    task_dir = tmp_path / "u1" / "tasks" / "t_ok"
    task_dir.mkdir(parents=True)
    monkeypatch.setattr(worker, "STORAGE_DIR", str(tmp_path))
    monkeypatch.setattr(worker, "_DOMAIN_STORAGE", {"video_gen": str(tmp_path)})

    out = task_dir / "output.mp4"
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=black:s=320x240:r=1",
             "-t", "1", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out)],
            capture_output=True, check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("需要 ffmpeg 造合法交付物")

    (task_dir / "audio").mkdir()
    (task_dir / "audio" / "a.mp3").write_bytes(b"x")

    def fake_pipeline(task_dir_, payload, options, progress_cb, **_kw):
        return out

    import pipelines.registry as reg
    monkeypatch.setattr(reg, "resolve_pipeline", lambda *a, **k: fake_pipeline)

    # 只让 status="ready" 的那次回写失败，其余放行
    real = worker.db_update_if_owned
    attempts = []

    def flaky(task_id, claimed_by=None, **kw):
        if kw.get("status") == "ready":
            attempts.append(1)
            raise sqlite3.OperationalError("database is locked")
        return real(task_id, claimed_by, **kw)

    monkeypatch.setattr(worker, "db_update_if_owned", flaky)
    monkeypatch.setattr(worker.time, "sleep", lambda _s: None)  # 免等退避

    worker._run_task({"id": "t_ok", "user_id": "u1", "domain": "video_gen",
                      "task_type": "ai_image", "payload": "{}"})

    assert len(attempts) == 3, f"成功回写必须重试 3 次，实际 {len(attempts)}"
    assert out.exists(), "成品视频不得被清理"
    assert (task_dir / "audio").exists(), "中间产物不得被清理--重渲要用"
    row = _row(db)
    assert "视频已生成" in (row["error_message"] or ""), row["error_message"]


def test_success_writeback_does_not_clobber_other_worker(tmp_path, monkeypatch):
    """租约被接管后，本 worker 的成功回写不得覆盖对方状态。"""
    db = str(tmp_path / "t2.db")
    _seed(db)
    monkeypatch.setattr(worker, "DB_PATH", db)

    conn = sqlite3.connect(db)
    conn.execute("UPDATE unified_tasks SET claimed_by='worker-OTHER' WHERE id='t_ok'")
    conn.commit()
    conn.close()

    n = worker.db_update_if_owned("t_ok", worker.WORKER_ID, status="ready", progress=100)
    assert n == 0, "非本 worker 拥有时不得写入"

    row = _row(db)
    assert row["claimed_by"] == "worker-OTHER", "不得抢走他人 claim"
    assert row["status"] != "ready", "不得覆盖他人状态"
