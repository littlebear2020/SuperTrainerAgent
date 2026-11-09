"""Tests for Week 2: TTS parallelization and draft audio background generation."""

import asyncio
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.gateway.video_gen.config import VideoGenConfig
from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame


def test_effective_frame_concurrency():
    cfg = VideoGenConfig()
    assert cfg.effective_frame_concurrency(10) == 3
    assert cfg.effective_frame_concurrency(20) == 3
    assert cfg.effective_frame_concurrency(30) == 2
    # W5/P8（2026-08-24 活性重构）：>50 帧档从硬编码 1 放开到 2（360 通道
    # 10,000 RPM 配额下不再需要串行压制），分档阈值/档位值均配置化。
    assert cfg.effective_frame_concurrency(100) == 2


@pytest.mark.asyncio
async def test_synthesize_audio_parallel():
    """_synthesize_audio should process frames in parallel (not serial)."""
    from app.gateway.video_gen.service import VideoGenService

    repo = MagicMock()
    config = VideoGenConfig()
    providers = MagicMock()
    svc = VideoGenService(repo=repo, config=config, providers=providers)

    cfg = StoryboardConfig(video_type="ai_image", width=1080, height=1920, voice_id="zh-CN-YunjianNeural")
    sb = Storyboard(
        title="test",
        config=cfg,
        frames=[StoryboardFrame(index=i, narration=f"测试{i}") for i in range(5)],
    )

    call_times = []
    per_call = 0.08  # s——模拟单次 TTS 耗时：串行为 n×per_call，并行 ≈ per_call

    async def mock_synthesize(text, *, voice_id, speed, output_path, fallback_voice=None, **kwargs):
        call_times.append(time.monotonic())
        await asyncio.sleep(per_call)
        Path(output_path).write_bytes(b"fake_mp3")
        from app.gateway.video_gen.orchestration.tts import TTSResult

        return TTSResult(audio_path=output_path, srt="", duration=2.0)

    with patch("app.gateway.video_gen.orchestration.tts.synthesize", side_effect=mock_synthesize):
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("app.gateway.video_gen.service.run_ffmpeg", new=AsyncMock()):
                # 字幕 LLM 预切与 SRT 写出均非本用例断言对象——桩掉以保持离线
                # （真实 config 的 subtitle_llm_enabled=True 曾让每帧串行调真 LLM，~14s）。
                with patch("app.gateway.video_gen.service._write_storyboard_srt"):
                    with patch("app.gateway.video_gen.service._precompute_llm_chunks", new=AsyncMock(return_value={})):
                        started = time.monotonic()
                        await svc._synthesize_audio(sb, Path(tmpdir), "zh-CN-YunjianNeural")
                        wall = time.monotonic() - started

    # All frames should have audio and duration
    for f in sb.frames:
        assert f.audio_path is not None
        assert f.duration == 2.0

    # 相对并行断言（不钉绝对时延，抗机器负载）：并行墙钟须远小于串行总耗时，
    # 且各帧启动时刻集中（串行回归会把 spread 拉到 ~n×per_call）。
    n = len(sb.frames)
    assert wall < n * per_call * 0.6, f"Frames not parallel: wall={wall:.3f}s ≈ 串行下界 {n * per_call:.2f}s"
    if len(call_times) >= 3:
        spread = max(call_times) - min(call_times)
        assert spread < n * per_call * 0.5, f"Frames not parallel: start spread={spread:.3f}s"


@pytest.mark.asyncio
async def test_generate_draft_audio_updates_storyboard():
    """_generate_draft_audio should update storyboard.json and mark audio_ready."""
    from app.gateway.video_gen.service import VideoGenService

    repo = AsyncMock()
    mock_task = MagicMock()
    mock_task.payload = '{"topic": "test"}'
    repo.get = AsyncMock(return_value=mock_task)
    config = VideoGenConfig()
    providers = MagicMock()
    svc = VideoGenService(repo=repo, config=config, providers=providers)

    cfg = StoryboardConfig(video_type="ai_image", width=1080, height=1920, voice_id="zh-CN-YunjianNeural")
    sb = Storyboard(
        title="test",
        config=cfg,
        frames=[StoryboardFrame(index=0, narration="测试旁白")],
    )

    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        task_dir = Path(tmpdir)
        # Write initial storyboard.json (no audio)
        (task_dir / "storyboard.json").write_text(json.dumps(sb.to_dict(), ensure_ascii=False), encoding="utf-8")

        async def mock_synth(*args, **kwargs):
            pass

        with patch.object(svc, "_synthesize_audio", side_effect=mock_synth):
            await svc._generate_draft_audio("test_task", task_dir)

        # Check storyboard.json was updated
        updated = json.loads((task_dir / "storyboard.json").read_text(encoding="utf-8"))
        assert updated["title"] == "test"

        # Check repo.update was called with audio_ready=True (read-merge-write, not payload_patch)
        repo.update.assert_called_once()
        call_kwargs = repo.update.call_args
        assert "payload_patch" not in call_kwargs.kwargs
        payload_str = call_kwargs.kwargs.get("payload", "")
        assert json.loads(payload_str).get("audio_ready") is True
