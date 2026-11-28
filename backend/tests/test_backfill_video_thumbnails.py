"""Batch B（2026-11-28）：历史视频缩略图离线回填工具（``scripts/backfill_video_thumbnails.py``）。

契约（plan §5.5 回填脚本）：默认 dry-run 且只读连接、零写入；默认扫描与 ``--apply`` 只处理
remotion_course/remotion/ai_image/ai_visual/ai_video 五种类型；只处理 ready、output_path 存在、
且 result.thumbnail_path 缺失或其文件不存在的任务；只写 thumbnail_path 一个键；重复执行幂等；
单任务失败不中断批次；输出 scanned/generated/skipped/failed 计数；真实小 MP4 生成有效 JPEG。

Task 7 Step 1 的 API 级行为验收也在这里：回填 ``--apply`` 后，``_task_with_thumbnail`` 下发
``thumbnail_url``，缩略图 GET 端点返回可解码的 JPEG。全部用临时目录 + 临时 SQLite，不碰开发库。
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sqlite3
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.responses import FileResponse

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "backfill_video_thumbnails.py"
FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")

pytestmark = pytest.mark.skipif(
    not FFMPEG or not FFPROBE,
    reason="ffmpeg/ffprobe not installed — backfill tests need real extraction",
)


def _load_module():
    spec = importlib.util.spec_from_file_location("backfill_video_thumbnails_t", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_db(tmp_path: Path):
    db = tmp_path / "deerflow.db"
    conn = sqlite3.connect(db)
    conn.execute(
        """CREATE TABLE unified_tasks (
            id TEXT PRIMARY KEY, user_id TEXT, task_type TEXT, status TEXT,
            created_at TEXT, thumbnail_url TEXT, result TEXT)"""
    )
    return conn, db


def _make_mp4(path: Path, duration: float = 2.0) -> None:
    subprocess.run(
        [
            FFMPEG,
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=blue:s=320x240:d={duration}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        capture_output=True,
        timeout=30,
        check=True,
    )


def _result_of(output: Path, **extra) -> str:
    return json.dumps({"output_path": str(output), "frame_count": 7, "title": "保留字段", **extra})


def _add_task(conn, tid, *, task_type="ai_video", status="ready", result=None, created_at="2026-11-01 00:00:00"):
    conn.execute(
        "INSERT INTO unified_tasks (id, user_id, task_type, status, created_at, thumbnail_url, result) VALUES (?,?,?,?,?,?,?)",
        (tid, "u1", task_type, status, created_at, None, result),
    )
    conn.commit()


def _result(conn, tid):
    return conn.execute("SELECT result FROM unified_tasks WHERE id = ?", (tid,)).fetchone()[0]


@pytest.fixture
def seeded(tmp_path: Path):
    """五档：需补图 / 成片损坏 / 已有缩略图 / 缩略图引用失效 / 成片缺失；外加 draft 与范围外类型各一。"""
    conn, db = _make_db(tmp_path)
    store = tmp_path / "store"
    store.mkdir()
    good = store / "good.mp4"
    _make_mp4(good)
    broken = store / "broken.mp4"
    broken.write_bytes(b"not a video")
    existing_thumb = store / "thumb_ok.jpg"
    existing_thumb.write_bytes(b"\xff\xd8\xffold")

    _add_task(conn, "vg_needs", result=_result_of(good))
    _add_task(conn, "vg_broken", result=_result_of(broken), created_at="2026-11-01 00:00:01")
    _add_task(conn, "vg_has", task_type="remotion_course", result=_result_of(good, thumbnail_path=str(existing_thumb)), created_at="2026-11-01 00:00:02")
    _add_task(conn, "vg_stale", task_type="ai_visual", result=_result_of(good, thumbnail_path=str(store / "gone.jpg")), created_at="2026-11-01 00:00:03")
    _add_task(conn, "vg_nooutput", task_type="ai_image", result=_result_of(store / "missing.mp4"), created_at="2026-11-01 00:00:04")
    _add_task(conn, "vg_draft", status="draft", result=_result_of(good))
    _add_task(conn, "vg_other", task_type="ppt", result=_result_of(good))
    yield conn, db, store, good
    conn.close()


def test_dry_run_writes_nothing(seeded, capsys):
    mod = _load_module()
    conn, db, store, good = seeded
    before = {tid: _result(conn, tid) for tid in ("vg_needs", "vg_stale", "vg_broken")}

    code = mod.main(["--db-path", str(db)])

    assert code == 1  # 范围内有一个成片损坏的任务（dry-run 也报告 failed）
    assert {tid: _result(conn, tid) for tid in before} == before
    assert sorted(p.name for p in store.iterdir()) == ["broken.mp4", "good.mp4", "thumb_ok.jpg"]
    out = capsys.readouterr().out
    assert "scanned=5 generated=2 skipped=2 failed=1" in out
    assert "[dry-run]" in out


def test_apply_backfills_only_eligible_tasks_and_single_key(seeded, capsys):
    mod = _load_module()
    conn, db, store, good = seeded

    code = mod.main(["--db-path", str(db), "--apply"])

    assert code == 1
    out = capsys.readouterr().out
    assert "scanned=5 generated=2 skipped=2 failed=1" in out
    # 只写 thumbnail_path 一个键，其余字段原样
    patched = json.loads(_result(conn, "vg_needs"))
    assert patched["thumbnail_path"] == str(store / "thumbnail.jpg")
    assert patched["frame_count"] == 7 and patched["title"] == "保留字段"
    assert json.loads(_result(conn, "vg_stale"))["thumbnail_path"] == str(store / "thumbnail.jpg")
    # 已有缩略图 / 成片缺失 / 草稿 / 范围外类型：一律不动
    assert json.loads(_result(conn, "vg_has"))["thumbnail_path"] == str(store / "thumb_ok.jpg")
    assert "thumbnail_path" not in json.loads(_result(conn, "vg_nooutput"))
    assert "thumbnail_path" not in json.loads(_result(conn, "vg_draft"))
    assert "thumbnail_path" not in json.loads(_result(conn, "vg_other"))
    assert "thumbnail_path" not in json.loads(_result(conn, "vg_broken"))


def test_apply_is_idempotent(seeded, capsys):
    mod = _load_module()
    conn, db, store, good = seeded
    mod.main(["--db-path", str(db), "--apply"])
    first = _result(conn, "vg_needs")

    code = mod.main(["--db-path", str(db), "--apply"])

    assert code == 1
    assert _result(conn, "vg_needs") == first
    assert "scanned=5 generated=0 skipped=4 failed=1" in capsys.readouterr().out


def test_task_type_filter_and_rejection(seeded, capsys):
    mod = _load_module()
    conn, db, store, good = seeded
    with pytest.raises(SystemExit):
        mod.main(["--db-path", str(db), "--task-type", "ppt"])

    code = mod.main(["--db-path", str(db), "--apply", "--task-type", "ai_video", "--task-id", "vg_needs"])
    assert code == 0
    assert json.loads(_result(conn, "vg_needs"))["thumbnail_path"] == str(store / "thumbnail.jpg")
    # 过滤运行不碰其他类型的任务：vg_stale 的失效引用原样保留
    assert json.loads(_result(conn, "vg_stale"))["thumbnail_path"] == str(store / "gone.jpg")


def test_real_mp4_yields_valid_jpeg(seeded):
    mod = _load_module()
    conn, db, store, good = seeded
    mod.main(["--db-path", str(db), "--apply", "--task-id", "vg_needs"])
    thumb = store / "thumbnail.jpg"
    assert thumb.exists() and thumb.stat().st_size > 0
    assert thumb.read_bytes()[:3] == b"\xff\xd8\xff"
    probe = subprocess.run(
        [FFPROBE, "-v", "error", "-show_entries", "stream=codec_name", "-of", "csv=p=0", str(thumb)],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    assert probe.stdout.strip() == "mjpeg"


def test_single_failure_does_not_abort_batch(seeded):
    mod = _load_module()
    conn, db, store, good = seeded
    mod.main(["--db-path", str(db), "--apply", "--task-id", "vg_broken"])
    # 成片损坏 → failed，不抛、不写；批次其他任务照常（上一个用例）
    assert "thumbnail_path" not in json.loads(_result(conn, "vg_broken"))


def _mock_task(result: str):
    """与 test_thumbnail.py 同形态的最小任务行。"""
    task = MagicMock()
    task.id = "vg_needs"
    task.user_id = "u1"
    task.domain = "video_gen"
    task.task_type = "ai_video"
    task.status = "ready"
    task.progress = 100
    task.error_message = None
    task.retry_count = 0
    task.title = None
    task.created_at = None
    task.updated_at = None
    task.thumbnail_url = None
    task.amount_source = None
    task.queue_position = None
    task.artifact_missing = False
    task.delivery = None
    task.cost = None
    task.currency = None
    task.payload = None
    task.result = result
    return task


def test_backfill_is_visible_through_thumbnail_api(seeded):
    """Task 7 Step 1 回填路径：临时库里 ready ai_video 任务没有缩略图，回填 --apply 后，
    `_task_with_thumbnail` 下发 thumbnail_url，缩略图 GET 返回可解码 JPEG。"""
    mod = _load_module()
    conn, db, store, good = seeded
    from app.gateway.routers.video_gen import _task_with_thumbnail, get_video_thumbnail

    row = _mock_task(_result(conn, "vg_needs"))
    assert _task_with_thumbnail(row).thumbnail_url is None

    mod.main(["--db-path", str(db), "--apply", "--task-id", "vg_needs"])
    row.result = _result(conn, "vg_needs")

    assert _task_with_thumbnail(row).thumbnail_url == "/api/video/tasks/vg_needs/thumbnail"

    svc = SimpleNamespace(get_task=AsyncMock(return_value=row))
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(video_gen_service=svc)))
    with patch("app.gateway.routers.video_gen.get_effective_user_id", return_value="u1"):
        import asyncio

    response = asyncio.run(get_video_thumbnail(request, "vg_needs"))
    assert isinstance(response, FileResponse) and Path(response.path).read_bytes()[:3] == b"\xff\xd8\xff"


def test_multiple_task_types_filter_before_limit(seeded, capsys):
    """`--task-type` 在 SQL 层过滤：LIMIT 作用于目标类型，不被其他类型的更早行占掉。"""
    mod = _load_module()
    conn, db, store, good = seeded

    code = mod.main(["--db-path", str(db), "--apply", "--task-type", "ai_visual", "--task-type", "remotion_course", "--limit", "1"])

    assert code == 0
    # LIMIT 1 落在目标类型里（跳过 vg_has 后取 vg_stale），而不是被其他类型（ai_video）更早的行占掉
    assert "scanned=2 generated=1 skipped=1 failed=0" in capsys.readouterr().out
    assert json.loads(_result(conn, "vg_stale"))["thumbnail_path"] == str(store / "thumbnail.jpg")
    assert "thumbnail_path" not in json.loads(_result(conn, "vg_needs"))


def test_write_back_failure_removes_fresh_thumbnail(seeded, monkeypatch, capsys):
    """写库失败时不留孤儿缩略图：下次运行仍能识别为候选（幂等）。"""
    mod = _load_module()
    conn, db, store, good = seeded

    def boom(*a, **k):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(mod, "_write_thumbnail_path", boom)
    code = mod.main(["--db-path", str(db), "--apply", "--task-id", "vg_needs"])

    assert code == 1
    assert not (store / "thumbnail.jpg").exists()
    assert "thumbnail_path" not in json.loads(_result(conn, "vg_needs"))
    assert "write-back failed" in capsys.readouterr().out


def test_limit_reports_truncation(seeded, capsys):
    """LIMIT 作用于扫描行数而非候选数：达到上限时在报告里提示。"""
    mod = _load_module()
    conn, db, store, good = seeded

    mod.main(["--db-path", str(db), "--limit", "1"])

    out = capsys.readouterr().out
    assert "scanned=1 " in out
    assert "[limit]" in out


def test_limit_counts_candidates_and_batch_advances(seeded, capsys):
    """`--limit N` = 最多处理 N 个候选：已补图的任务不再占额度，分批重跑能推进到后面的任务。"""
    mod = _load_module()
    conn, db, store, good = seeded

    code = mod.main(["--db-path", str(db), "--apply", "--limit", "1"])
    assert code == 0
    first = capsys.readouterr().out
    assert "scanned=1 generated=1 skipped=0 failed=0" in first
    assert "[limit]" in first  # 后面还有候选

    code = mod.main(["--db-path", str(db), "--apply", "--limit", "1"])
    assert code == 1
    # 第二批：vg_needs 已有图被跳过（不计额度），候选是下一个 vg_broken（成片损坏）
    assert "scanned=2 generated=0 skipped=1 failed=1" in capsys.readouterr().out


def test_limit_rejects_non_positive_values(seeded):
    mod = _load_module()
    conn, db, store, good = seeded
    with pytest.raises(SystemExit):
        mod.main(["--db-path", str(db), "--limit", "0"])


def test_write_back_affecting_zero_rows_is_skipped_and_reclaimed(seeded, monkeypatch, capsys):
    """UPDATE 命中 0 行（状态已被并发改掉）按 skipped 处理，并回收本次新建的缩略图。"""
    mod = _load_module()
    conn, db, store, good = seeded
    monkeypatch.setattr(mod, "_write_thumbnail_path", lambda *a, **k: 0)

    code = mod.main(["--db-path", str(db), "--apply", "--task-id", "vg_needs"])

    assert code == 0
    assert not (store / "thumbnail.jpg").exists()
    assert "thumbnail_path" not in json.loads(_result(conn, "vg_needs"))
    assert "scanned=1 generated=0 skipped=1 failed=0" in capsys.readouterr().out
