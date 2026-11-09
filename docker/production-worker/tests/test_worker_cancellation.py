"""Task 22: worker 侧任务可取消。

核实：`DELETE /tasks/{id}` 确实会 `async_task.cancel()`（gateway 侧），但
ai_image/ai_video 交给独立 worker 进程后，**worker 主循环从不回查任务是否已被
取消/删除** —— 删除只清 DB 与目录，worker 里的 ffmpeg / 供应商调用照跑到底，
配额照烧，最后还可能把 status 写回 ready，把已删任务"复活"。

修法：worker 在每个进度检查点（progress_cb 本就每帧读一次 DB）回查取消标志，
命中则抛 TaskCancelled，process_task 走取消分支——不写 ready、不写 failed，
清 claim/lease 后安静退出。
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import worker  # noqa: E402


def _seed(db_path: str, *, status: str = "composing", task_id: str = "vg_cancel1") -> None:
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
        conn.execute(
            """
            INSERT OR REPLACE INTO unified_tasks
            (id, domain, task_type, user_id, status, progress, payload, result,
             retry_count, priority, claimed_by, claimed_at, lease_expires_at,
             created_at, updated_at)
            VALUES (?, 'video_gen', 'ai_image', 'u1', ?, 10, '{}', '{}', 0, 0,
                    ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                status,
                # 审查发现 4：必须是本 worker 的 ID —— 归属校验会把「他人持有」
                # 判成该停（真实场景里在跑的任务一定由本 worker 持有）。
                worker.WORKER_ID,
                now.isoformat(),
                (now + timedelta(seconds=3600)).isoformat(),
                now.isoformat(),
                now.isoformat(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _row(db_path: str, task_id: str = "vg_cancel1") -> sqlite3.Row:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            "SELECT * FROM unified_tasks WHERE id = ?", (task_id,)
        ).fetchone()
    finally:
        conn.close()


def _set_status(db_path: str, status: str, task_id: str = "vg_cancel1") -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "UPDATE unified_tasks SET status = ? WHERE id = ?", (status, task_id)
        )
        conn.commit()
    finally:
        conn.close()


def _delete_row(db_path: str, task_id: str = "vg_cancel1") -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("DELETE FROM unified_tasks WHERE id = ?", (task_id,))
        conn.commit()
    finally:
        conn.close()


def test_cancel_predicate_detects_cancelled_status(tmp_path, monkeypatch):
    """状态被置 cancelled → is_task_cancelled 为真。"""
    import worker

    db = str(tmp_path / "t.db")
    _seed(db)
    monkeypatch.setattr(worker, "DB_PATH", db)

    assert worker.is_task_cancelled("vg_cancel1") is False
    _set_status(db, "cancelled")
    assert worker.is_task_cancelled("vg_cancel1") is True


def test_cancel_predicate_treats_deleted_row_as_cancelled(tmp_path, monkeypatch):
    """行被删除（DELETE /tasks/{id}）→ 同样视为取消，别继续烧配额。"""
    import worker

    db = str(tmp_path / "t.db")
    _seed(db)
    monkeypatch.setattr(worker, "DB_PATH", db)

    _delete_row(db)
    assert worker.is_task_cancelled("vg_cancel1") is True


def test_progress_cb_raises_when_cancelled(tmp_path, monkeypatch):
    """渲染中置 cancelled → 下一个 progress 检查点抛 TaskCancelled。

    progress_cb 是 worker 唯一贯穿渲染全程的回调（每帧调一次），用它当检查点
    等于逐帧可中断，无需管线各自改造。
    """
    import worker

    db = str(tmp_path / "t.db")
    _seed(db)
    monkeypatch.setattr(worker, "DB_PATH", db)

    task_dir = tmp_path / "u1" / "tasks" / "vg_cancel1"
    task_dir.mkdir(parents=True)
    monkeypatch.setattr(worker, "STORAGE_DIR", str(tmp_path))
    monkeypatch.setattr(worker, "_DOMAIN_STORAGE", {"video_gen": str(tmp_path)})

    seen: list[int] = []

    def fake_pipeline(task_dir_, payload, options, progress_cb, **_kw):
        progress_cb(20)
        seen.append(20)
        _set_status(db, "cancelled")  # 用户此刻点了取消
        progress_cb(40)  # 应在此抛出
        seen.append(40)
        return task_dir_ / "output.mp4"

    monkeypatch.setattr(
        worker, "resolve_pipeline", lambda *a, **k: fake_pipeline, raising=False
    )
    import pipelines.registry as reg

    monkeypatch.setattr(reg, "resolve_pipeline", lambda *a, **k: fake_pipeline)

    task = {
        "id": "vg_cancel1",
        "user_id": "u1",
        "domain": "video_gen",
        "task_type": "ai_image",
        "payload": "{}",
    }
    worker.process_task(task)

    assert seen == [20], "取消后仍继续执行了后续阶段"
    row = _row(db)
    assert row["status"] == "cancelled", f"不得改写终态，实际 {row['status']}"
    assert row["claimed_by"] is None, "取消后应释放 claim"


def test_cancelled_task_never_written_ready(tmp_path, monkeypatch):
    """取消的任务绝不能被写成 ready（否则前端显示"已完成"、可下载空文件）。"""
    import worker

    db = str(tmp_path / "t.db")
    _seed(db)
    monkeypatch.setattr(worker, "DB_PATH", db)

    task_dir = tmp_path / "u1" / "tasks" / "vg_cancel1"
    task_dir.mkdir(parents=True)
    monkeypatch.setattr(worker, "STORAGE_DIR", str(tmp_path))
    monkeypatch.setattr(worker, "_DOMAIN_STORAGE", {"video_gen": str(tmp_path)})

    out = task_dir / "output.mp4"

    def fake_pipeline(task_dir_, payload, options, progress_cb, **_kw):
        _set_status(db, "cancelled")
        progress_cb(50)  # 抛 TaskCancelled
        out.write_bytes(b"should-not-matter")
        return out

    import pipelines.registry as reg

    monkeypatch.setattr(reg, "resolve_pipeline", lambda *a, **k: fake_pipeline)

    worker.process_task(
        {
            "id": "vg_cancel1",
            "user_id": "u1",
            "domain": "video_gen",
            "task_type": "ai_image",
            "payload": "{}",
        }
    )

    row = _row(db)
    assert row["status"] != "ready"
    assert row["status"] == "cancelled"


def test_normal_task_unaffected(tmp_path, monkeypatch):
    """未取消的任务照常跑完并写 ready（不得因本 Task 误伤正常路径）。"""
    import subprocess

    import worker

    db = str(tmp_path / "t.db")
    _seed(db)
    monkeypatch.setattr(worker, "DB_PATH", db)

    task_dir = tmp_path / "u1" / "tasks" / "vg_cancel1"
    task_dir.mkdir(parents=True)
    monkeypatch.setattr(worker, "STORAGE_DIR", str(tmp_path))
    monkeypatch.setattr(worker, "_DOMAIN_STORAGE", {"video_gen": str(tmp_path)})

    out = task_dir / "output.mp4"
    try:
        subprocess.run(
            [
                "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=black:s=320x240:r=1",
                "-t", "1", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out),
            ],
            capture_output=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("需要 ffmpeg 造合法交付物")

    def fake_pipeline(task_dir_, payload, options, progress_cb, **_kw):
        progress_cb(50)
        return out

    import pipelines.registry as reg

    monkeypatch.setattr(reg, "resolve_pipeline", lambda *a, **k: fake_pipeline)

    worker.process_task(
        {
            "id": "vg_cancel1",
            "user_id": "u1",
            "domain": "video_gen",
            "task_type": "ai_image",
            "payload": "{}",
        }
    )

    row = _row(db)
    assert row["status"] == "ready", f"正常任务被误判取消：{row['status']}"
    assert json.loads(row["result"])["output_path"] == str(out)


# ── 审查发现 7：取消分支删中间产物，违反「保留产物」契约 ────────────────────
#
# cancel_task 的文档明确承诺「保留任务行与产物目录」（这正是它区别于
# delete_task 的地方——用户要的是「先停下别烧配额」，不是「连记录一起抹掉」）。
# 但 worker 的 TaskCancelled 分支也调了 _cleanup_task_dir，把 audio/segments/
# slides 全删了，用户无法检查已生成的部分、也无法续做。
# 失败路径（except Exception）保留清理是对的——那是真故障，中间产物无价值。


def test_cancel_preserves_intermediate_artifacts(tmp_path, monkeypatch):
    """取消后 audio/segments 等中间产物必须还在。"""
    import worker

    db = str(tmp_path / "t.db")
    _seed(db)
    monkeypatch.setattr(worker, "DB_PATH", db)

    task_dir = tmp_path / "u1" / "tasks" / "vg_cancel1"
    (task_dir / "audio").mkdir(parents=True)
    (task_dir / "segments").mkdir(parents=True)
    (task_dir / "audio" / "frame_000.mp3").write_bytes(b"generated audio")
    (task_dir / "segments" / "seg_000.mp4").write_bytes(b"generated segment")
    monkeypatch.setattr(worker, "STORAGE_DIR", str(tmp_path))
    monkeypatch.setattr(worker, "_DOMAIN_STORAGE", {"video_gen": str(tmp_path)})

    def fake_pipeline(task_dir_, payload, options, progress_cb, **_kw):
        _set_status(db, "cancelled")
        progress_cb(50)  # 抛 TaskCancelled
        return task_dir_ / "output.mp4"

    import pipelines.registry as reg

    monkeypatch.setattr(reg, "resolve_pipeline", lambda *a, **k: fake_pipeline)

    worker.process_task(
        {
            "id": "vg_cancel1",
            "user_id": "u1",
            "domain": "video_gen",
            "task_type": "ai_image",
            "payload": "{}",
        }
    )

    assert (task_dir / "audio" / "frame_000.mp3").exists(), (
        "取消不得删已生成的配音——cancel_task 承诺保留产物，用户要能检查/续做"
    )
    assert (task_dir / "segments" / "seg_000.mp4").exists(), "取消不得删已生成的分镜段"


def test_failure_still_cleans_up(tmp_path, monkeypatch):
    """真失败路径仍然清理中间产物（不得因本项改动而泄漏磁盘）。"""
    import pytest as _pytest

    import worker

    db = str(tmp_path / "t.db")
    _seed(db)
    monkeypatch.setattr(worker, "DB_PATH", db)

    task_dir = tmp_path / "u1" / "tasks" / "vg_cancel1"
    (task_dir / "audio").mkdir(parents=True)
    (task_dir / "audio" / "x.mp3").write_bytes(b"a")
    monkeypatch.setattr(worker, "STORAGE_DIR", str(tmp_path))
    monkeypatch.setattr(worker, "_DOMAIN_STORAGE", {"video_gen": str(tmp_path)})

    def boom(task_dir_, payload, options, progress_cb, **_kw):
        raise RuntimeError("render exploded")

    import pipelines.registry as reg

    monkeypatch.setattr(reg, "resolve_pipeline", lambda *a, **k: boom)

    with _pytest.raises(RuntimeError):
        worker.process_task(
            {
                "id": "vg_cancel1",
                "user_id": "u1",
                "domain": "video_gen",
                "task_type": "ai_image",
                "payload": "{}",
            }
        )

    assert not (task_dir / "audio").exists(), "失败路径应继续清理中间产物"
