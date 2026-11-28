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
    task.delivery = None  # 响应层派生字段：MagicMock auto-attr 不能作为 DTO 输入
    task.cost = None  # not set on TaskRow (批次 C：端点事后赋值)
    task.currency = None
    task.payload = None  # _task_with_thumbnail does json.loads(task.payload or "{}")
    task.result = json.dumps({"thumbnail_path": "/tmp/fake_thumb.jpg", "output_path": "/tmp/fake_out.mp4"})

    # 文件在 → 缩略图 URL 下发；ready + output 在 → artifact_missing False
    with (
        patch("pathlib.Path.exists", return_value=True),
        patch(
            "pathlib.Path.stat",
            return_value=MagicMock(st_size=4, st_mtime=1_700_000_000),
        ),
    ):
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


# ── Batch B（2026-11-28）：共享抽帧 helper 加固 ──────────────────────────────────────
# 真实 ffmpeg，进 CI：失败不留旧图、不留临时文件；<1s 视频回退首帧；签名兼容既有调用。


def _load_ffmpeg_helpers():
    """按路径加载 worker 共享 helper（同 test_ffmpeg_extract_thumbnail_helper 的写法）。"""
    import importlib.util

    helper_path = Path(__file__).resolve().parents[2] / "docker" / "production-worker" / "pipelines" / "_ffmpeg_helpers.py"
    spec = importlib.util.spec_from_file_location("_ffmpeg_helpers", helper_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_helper_failure_removes_stale_thumbnail_and_leaves_no_temp(tmp_path):
    """目标位置已有旧图、本次抽帧失败（输入不是视频）：返回 False 且旧图被删，无临时文件残留。"""
    mod = _load_ffmpeg_helpers()
    stale = tmp_path / "thumbnail.jpg"
    stale.write_bytes(b"old frame")
    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"not a video")

    assert mod._ffmpeg_extract_thumbnail(broken, stale) is False
    assert not stale.exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["broken.mp4"]


def test_helper_falls_back_to_first_frame_for_sub_second_video(tmp_path):
    """时长 <1s 的视频在代表时点（seek 1s）拿不到帧，t=0 回退成功。"""
    mod = _load_ffmpeg_helpers()
    video = tmp_path / "output.mp4"
    _make_mini_mp4(video, duration=0.5)
    thumb = tmp_path / "thumbnail.jpg"

    assert mod._ffmpeg_extract_thumbnail(video, thumb) is True
    assert thumb.read_bytes()[:3] == b"\xff\xd8\xff"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["output.mp4", "thumbnail.jpg"]


def test_helper_replaces_stale_thumbnail_without_temp_leftovers(video_dir):
    """成功时目标文件是真实 JPEG（非旧的 3 字节），且没有残留的临时文件。"""
    mod = _load_ffmpeg_helpers()
    thumb = video_dir / "thumbnail.jpg"
    thumb.write_bytes(b"old")

    assert mod._ffmpeg_extract_thumbnail(video_dir / "output.mp4", thumb) is True
    assert thumb.read_bytes()[:3] == b"\xff\xd8\xff"
    assert thumb.stat().st_size > 100
    assert sorted(p.name for p in video_dir.iterdir()) == ["output.mp4", "thumbnail.jpg"]


def test_helper_seek_ratio_is_keyword_only_and_keeps_positional_signature(video_dir):
    """位置参数与返回值不变（多处测试按位置参数桩掉它）；seek_ratio 仅关键字。"""
    import inspect

    mod = _load_ffmpeg_helpers()
    parameters = inspect.signature(mod._ffmpeg_extract_thumbnail).parameters
    assert list(parameters) == ["video_path", "output_path", "timeout", "seek_ratio"]
    assert parameters["seek_ratio"].kind is inspect.Parameter.KEYWORD_ONLY
    assert parameters["seek_ratio"].default == 0.1

    thumb = video_dir / "half.jpg"
    assert mod._ffmpeg_extract_thumbnail(video_dir / "output.mp4", thumb, 30, seek_ratio=0.5) is True
    assert thumb.exists()


# ── Batch B Task 7 Step 1：旧图路径的 API 级行为验收 ────────────────────────────────


def _mock_task():
    """与 test_task_with_thumbnail_router_logic 同形态的最小任务行。"""
    task = MagicMock()
    task.id = "vg_test123"
    task.user_id = "u1"
    task.domain = "video_gen"
    task.task_type = "ai_image"
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
    task.result = None
    return task


def test_stale_thumbnail_is_not_served_after_failed_extraction(tmp_path):
    """任务目录有旧 thumbnail.jpg、result 也指向它；对损坏的成片调用加固后的 helper 失败后，
    旧图被删，`_task_with_thumbnail` 不再下发 thumbnail_url（列表不再下发旧图）。"""
    mod = _load_ffmpeg_helpers()
    from app.gateway.routers.video_gen import _task_with_thumbnail

    broken = tmp_path / "output.mp4"
    broken.write_bytes(b"broken render")
    stale = tmp_path / "thumbnail.jpg"
    stale.write_bytes(b"old frame")
    task = _mock_task()
    task.result = json.dumps({"thumbnail_path": str(stale), "output_path": str(broken)})

    assert _task_with_thumbnail(task).thumbnail_url == "/api/video/tasks/vg_test123/thumbnail"

    assert mod._ffmpeg_extract_thumbnail(broken, stale) is False
    assert not stale.exists()
    brief = _task_with_thumbnail(task)
    assert brief.thumbnail_url is None
    assert brief.artifact_missing is False


def test_helper_falls_back_to_first_frame_when_representative_seek_times_out(tmp_path, monkeypatch):
    """代表时点（-ss 非 0）超时不得整体放弃：单次尝试的 TimeoutExpired 就地吞掉，仍走 -ss 0 回退。"""
    import subprocess as _subprocess

    mod = _load_ffmpeg_helpers()
    video = tmp_path / "output.mp4"
    _make_mini_mp4(video, duration=2.0)
    thumb = tmp_path / "thumbnail.jpg"
    real_run = _subprocess.run

    def fake_run(cmd, *args, **kwargs):
        if cmd[0] == "ffmpeg" and cmd[cmd.index("-ss") + 1] != "0.0":
            raise _subprocess.TimeoutExpired(cmd, kwargs.get("timeout", 30))
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(mod.subprocess, "run", fake_run)

    assert mod._ffmpeg_extract_thumbnail(video, thumb) is True
    assert thumb.read_bytes()[:3] == b"\xff\xd8\xff"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["output.mp4", "thumbnail.jpg"]
