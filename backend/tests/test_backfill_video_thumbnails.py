"""Batch B（2026-11-28）：历史视频缩略图离线回填工具（``scripts/backfill_video_thumbnails.py``）。

契约（plan §5.5 回填脚本）：默认 dry-run 且只读连接、零写入；默认扫描与 ``--apply`` 只处理
remotion_course/remotion/ai_image/ai_visual/ai_video 五种类型；只处理 ready、output_path 存在、
且 result.thumbnail_path 缺失或其文件不存在的任务；只写 thumbnail_path 一个键；重复执行幂等；
单任务失败不中断批次；输出 scanned/generated/skipped/failed 计数；真实小 MP4 生成有效 JPEG。

全部用临时目录 + 临时 SQLite，不碰开发库。
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

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
