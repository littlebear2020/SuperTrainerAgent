"""P0-2 / P0-3（2026-08-08）：接管场景下 worker 迟到写入不得碰他人任务行。

P0-2：渲染成功但成功回写 n==0（已被接管）——修前 TaskCancelled 被重试循环的
except Exception 吞掉，空转 3 次后抛 _SuccessWritebackFailed → 误标 failed。
修后走 _OwnershipLost：不碰任务行、保留产物。

P0-3：失败回写（_run_task）与取消回写（TaskCancelled 分支）换成
db_update_if_owned——修前无守卫 db_update 会覆盖新持有者的行/清掉其 claim。
"""

import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import worker  # noqa: E402

OTHER = "worker-OTHER"


def _seed(db_path: str, *, claimed_by) -> None:
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
        " VALUES ('t_own','u1','video_gen','ai_image','composing','{}',?)",
        (claimed_by,),
    )
    conn.commit()
    conn.close()


def _row(db_path: str) -> sqlite3.Row:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM unified_tasks WHERE id='t_own'").fetchone()
    conn.close()
    return row


def _mk_output(task_dir: Path) -> Path:
    out = task_dir / "output.mp4"
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=black:s=320x240:r=1",
             "-t", "1", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out)],
            capture_output=True, check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("需要 ffmpeg 造合法交付物")
    return out


def _common_patches(tmp_path, monkeypatch, db):
    monkeypatch.setattr(worker, "DB_PATH", db)
    monkeypatch.setattr(worker, "_DOMAIN_STORAGE", {"video_gen": str(tmp_path)})
    monkeypatch.setattr(worker, "_metrics_inc", lambda *a, **k: None)
    monkeypatch.setattr(worker, "_metrics_observe", lambda *a, **k: None)
    monkeypatch.setattr(worker.time, "sleep", lambda _s: None)  # 免等退避


def test_success_writeback_ownership_lost_leaves_row_and_artifacts(tmp_path, monkeypatch):
    """P0-2：渲染成功但任务已被他人接管 → 不碰任务行、不写 failed、保留产物。"""
    db = str(tmp_path / "t.db")
    _seed(db, claimed_by=OTHER)  # 渲染期间已被接管
    _common_patches(tmp_path, monkeypatch, db)

    task_dir = tmp_path / "u1" / "tasks" / "t_own"
    task_dir.mkdir(parents=True)
    (task_dir / "audio").mkdir()
    (task_dir / "audio" / "a.mp3").write_bytes(b"x")
    out = _mk_output(task_dir)

    def fake_pipeline(task_dir_, payload, options, progress_cb, **_kw):
        return out

    import pipelines.registry as reg
    monkeypatch.setattr(reg, "resolve_pipeline", lambda *a, **k: fake_pipeline)

    # 观测成功回写次数：正确实现 n==0 立即抛 _OwnershipLost 透传（恰好 1 次）；
    # 若 _OwnershipLost 被 except Exception 吞掉（修前形态）会空转 3 次。
    real = worker.db_update_if_owned
    ready_attempts = []

    def spy(task_id, claimed_by=None, **kw):
        if kw.get("status") == "ready":
            ready_attempts.append(1)
        return real(task_id, claimed_by, **kw)

    monkeypatch.setattr(worker, "db_update_if_owned", spy)

    worker._run_task({"id": "t_own", "user_id": "u1", "domain": "video_gen",
                      "task_type": "ai_image", "payload": "{}"})

    row = _row(db)
    assert row["claimed_by"] == OTHER, "不得碰他人任务行的 claim"
    assert row["status"] == "composing", "不得写 ready、更不得写 failed"
    assert row["error_message"] is None, "不得写 error_message"
    assert out.exists(), "产物必须保留"
    assert (task_dir / "audio").exists(), "中间产物必须保留"
    assert len(ready_attempts) == 1, (
        f"n==0 应立即终止回写（_OwnershipLost 透传），实际尝试 {len(ready_attempts)} 次——"
        "若 == 3 说明异常又被 except Exception 吞了"
    )


def test_failure_writeback_does_not_clobber_taken_over_task(tmp_path, monkeypatch):
    """P0-3a：渲染失败但任务已被接管 → _run_task 不得把他人任务标 failed。"""
    db = str(tmp_path / "t.db")
    _seed(db, claimed_by=OTHER)
    _common_patches(tmp_path, monkeypatch, db)

    task_dir = tmp_path / "u1" / "tasks" / "t_own"
    task_dir.mkdir(parents=True)

    def fake_pipeline(task_dir_, payload, options, progress_cb, **_kw):
        raise RuntimeError("render exploded")

    import pipelines.registry as reg
    monkeypatch.setattr(reg, "resolve_pipeline", lambda *a, **k: fake_pipeline)

    worker._run_task({"id": "t_own", "user_id": "u1", "domain": "video_gen",
                      "task_type": "ai_image", "payload": "{}"})

    row = _row(db)
    assert row["claimed_by"] == OTHER, "不得清掉新持有者的 claim"
    assert row["status"] == "composing", "不得把他人任务标 failed"
    assert row["error_message"] is None, "不得覆盖他人 error_message"


def test_cancel_writeback_does_not_clear_other_workers_claim(tmp_path, monkeypatch):
    """P0-3b：TaskCancelled 分支的释放回写不得清掉新持有者的 claim。"""
    db = str(tmp_path / "t.db")
    _seed(db, claimed_by=worker.WORKER_ID)  # 起步时归本 worker
    _common_patches(tmp_path, monkeypatch, db)

    task_dir = tmp_path / "u1" / "tasks" / "t_own"
    task_dir.mkdir(parents=True)

    def fake_pipeline(task_dir_, payload, options, progress_cb, **_kw):
        # 渲染途中任务被他人接管，随后本 worker 收到取消信号
        conn = sqlite3.connect(db)
        conn.execute("UPDATE unified_tasks SET claimed_by=? WHERE id='t_own'", (OTHER,))
        conn.commit()
        conn.close()
        raise worker.TaskCancelled("t_own")

    import pipelines.registry as reg
    monkeypatch.setattr(reg, "resolve_pipeline", lambda *a, **k: fake_pipeline)

    worker._run_task({"id": "t_own", "user_id": "u1", "domain": "video_gen",
                      "task_type": "ai_image", "payload": "{}"})

    row = _row(db)
    assert row["claimed_by"] == OTHER, "取消回写不得清掉新持有者的 claim（对方会 renew 0 行自杀）"
    assert row["status"] == "composing", "不得改他人任务状态"
