"""Tests for thumbnail extraction logic."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def _make_mini_mp4(path: Path, duration: float = 2.0) -> None:
    """Create a minimal mp4 file for testing using ffmpeg."""
    cmd = [
        "ffmpeg",
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
    ]
    subprocess.run(cmd, capture_output=True, timeout=30, check=True)


@pytest.fixture
def video_dir(tmp_path):
    """Create a temp dir with a test video."""
    video = tmp_path / "output.mp4"
    _make_mini_mp4(video, duration=5.0)
    return tmp_path


def test_extract_thumbnail_success(video_dir):
    from app.gateway.video_gen.service import _extract_thumbnail

    video_path = str(video_dir / "output.mp4")
    result = _extract_thumbnail(video_path, video_dir)

    assert result is not None
    thumb = Path(result)
    assert thumb.exists()
    assert thumb.name == "thumbnail.jpg"
    assert thumb.stat().st_size > 0


def test_extract_thumbnail_short_video(tmp_path):
    """Videos shorter than 10s should use 1s seek position."""
    from app.gateway.video_gen.service import _extract_thumbnail

    video = tmp_path / "output.mp4"
    _make_mini_mp4(video, duration=1.5)
    result = _extract_thumbnail(str(video), tmp_path)

    assert result is not None
    assert Path(result).exists()


def test_extract_thumbnail_missing_video(tmp_path):
    from app.gateway.video_gen.service import _extract_thumbnail

    result = _extract_thumbnail(str(tmp_path / "nonexistent.mp4"), tmp_path)
    assert result is None


def test_ffmpeg_extract_thumbnail_helper(video_dir):
    import importlib.util

    # 仓内相对定位（曾硬编码开发者本机绝对路径，CI 必炸）
    helper_path = Path(__file__).resolve().parents[2] / "docker" / "production-worker" / "pipelines" / "_ffmpeg_helpers.py"
    spec = importlib.util.spec_from_file_location("_ffmpeg_helpers", helper_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    thumb = video_dir / "thumb.jpg"
    ok = mod._ffmpeg_extract_thumbnail(video_dir / "output.mp4", thumb)
    assert ok
    assert thumb.exists()


def test_task_with_thumbnail_router_logic():
    """Test _task_with_thumbnail helper logic.

    2026-09-13 死链修复回归：
    - DB 陈旧 thumbnail_url 列值在文件失/字段缺时不得下发（两种脏值形态都归 None）；
    - artifact_missing 仅 ready 态按 output_path 存在性计算。
    另修复既有红：amount_source/queue_position 加字段后 mock 未跟上，
    MagicMock auto-attr 过不了 str/int 校验（同 title 注释先例）。
    """
    from app.gateway.routers.video_gen import _task_with_thumbnail

    # Mock task with thumbnail in result
    task = MagicMock()
    task.id = "vg_test123"
    task.user_id = "u1"
    task.domain = "video_gen"
    task.task_type = "ai_image"
    task.status = "ready"
    task.progress = 100
    task.error_message = None
    task.retry_count = 0
    task.title = None  # explicit: MagicMock auto-attr would fail str validation
    task.created_at = None
    task.updated_at = None
    task.thumbnail_url = None  # DB 列（P2-4 完成路径落库）；下方死链用例改为脏值
    task.amount_source = None  # 2026-08-27 字段：mock 未跟上过不了 str 校验（既有红修复）
    task.queue_position = None  # 2026-09-08 字段：同上（int 校验）
    task.artifact_missing = False  # 响应层派生字段（2026-09-13）：函数内覆写
    task.cost = None  # not set on TaskRow (批次 C：端点事后赋值)
    task.currency = None
    task.payload = None  # _task_with_thumbnail does json.loads(task.payload or "{}")
    task.result = json.dumps({"thumbnail_path": "/tmp/fake_thumb.jpg", "output_path": "/tmp/fake_out.mp4"})

    # 文件在 → 缩略图 URL 下发；ready + output 在 → artifact_missing False
    with patch("pathlib.Path.exists", return_value=True):
        brief = _task_with_thumbnail(task)
        assert brief.thumbnail_url == "/api/video/tasks/vg_test123/thumbnail"
        assert brief.artifact_missing is False

    # 死链形态①（计划 T3）：result 含 thumbnail_path + 文件失 + DB 列残留脏值 → 不下发
    task.thumbnail_url = "/api/video/tasks/vg_test123/thumbnail"  # 模拟 DB 脏列
    with patch("pathlib.Path.exists", return_value=False):
        brief = _task_with_thumbnail(task)
        assert brief.thumbnail_url is None

    # 死链形态②（审计补充）：result 无 thumbnail_path（thumb==""）+ DB 列残留 → 同样归 None
    task.result = "{}"
    with patch("pathlib.Path.exists", return_value=False):
        brief = _task_with_thumbnail(task)
        assert brief.thumbnail_url is None

    # 假就绪：ready + output 缺失 → artifact_missing True（缩略图/成片双失即「假就绪」）
    task.result = json.dumps({"thumbnail_path": "/tmp/fake_thumb.jpg", "output_path": "/tmp/fake_out.mp4"})
    with patch("pathlib.Path.exists", return_value=False):
        brief = _task_with_thumbnail(task)
        assert brief.artifact_missing is True

    # 非 ready 状态 → artifact_missing 恒 False（即便无 output_path）
    task.status = "failed"
    task.result = "{}"
    with patch("pathlib.Path.exists", return_value=False):
        brief = _task_with_thumbnail(task)
        assert brief.artifact_missing is False
