"""Tests for audio ducking functionality."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch


def test_ducking_fallback_on_failure(tmp_path):
    """When ducking fails, falls back to simple mix."""
    # Hermetic load: do NOT import via `pipelines._ffmpeg_helpers` — other test
    # modules (test_worker_ai_visual_payload, test_worker_incremental_manifest)
    # register do-nothing stub modules under that sys.modules key at import
    # time, so `from pipelines._ffmpeg_helpers import ...` may resolve to a
    # stub whose _ffmpeg_mix_bgm_with_ducking never calls subprocess.run.
    helpers_path = Path(__file__).resolve().parents[2] / "docker" / "production-worker" / "pipelines" / "_ffmpeg_helpers.py"
    spec = importlib.util.spec_from_file_location("_ffmpeg_helpers_ducking_test", helpers_path)
    helpers = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helpers)

    video = tmp_path / "video.mp4"
    bgm = tmp_path / "bgm.mp3"
    output = tmp_path / "output.mp4"
    video.write_bytes(b"fake_video")
    bgm.write_bytes(b"fake_bgm")

    # Mock _ffprobe_duration to return a valid duration
    # _ffprobe_stream_duration 也要 mock：条件 -shortest（2026-07-24 混音去 tpad 批次）
    # 在 ducking 之前会探 v/a 两路流时长，不 mock 会吃掉 side_effect 配额（StopIteration）
    with patch.object(helpers, "_ffprobe_duration", return_value=10.0), patch.object(helpers, "_ffprobe_stream_duration", return_value=10.0), patch("subprocess.run") as mock_run:
        # First call (ducking) fails, second call (fallback) succeeds
        fail_proc = MagicMock()
        fail_proc.returncode = 1
        fail_proc.stderr = b"ducking error"
        success_proc = MagicMock()
        success_proc.returncode = 0
        mock_run.side_effect = [fail_proc, success_proc]

        helpers._ffmpeg_mix_bgm_with_ducking(video, bgm, output, bgm_volume=0.15)

        # Should have been called twice: once for ducking, once for fallback
        assert mock_run.call_count == 2


def test_ducking_schema_default():
    """bgm_ducking defaults to True in CreateVideoRequest."""
    from app.gateway.video_gen.schemas import CreateVideoRequest

    req = CreateVideoRequest(video_type="ai_image", mode="fixed", script="test")
    assert req.bgm_ducking is True


def test_ducking_disabled():
    """bgm_ducking can be set to False."""
    from app.gateway.video_gen.schemas import CreateVideoRequest

    req = CreateVideoRequest(video_type="ai_image", mode="fixed", script="test", bgm_ducking=False)
    assert req.bgm_ducking is False


def test_storyboard_config_ducking_default():
    """StoryboardConfig has bgm_ducking default True."""
    from app.gateway.video_gen.storyboard import StoryboardConfig

    cfg = StoryboardConfig(video_type="ai_image", width=1080, height=1920)
    assert cfg.bgm_ducking is True
