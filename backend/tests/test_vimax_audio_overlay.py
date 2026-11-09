"""Tests for ViMax audio overlay ffmpeg command (Phase 1.3 apad fix).

The key fix: apad pads audio to infinite → -shortest caps to video length.
Without apad, -shortest truncated the video to the (much shorter) audio length.
"""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.gateway.video_gen.engines.vimax.audio_overlay import build_overlay_cmd, overlay_audio_track


def test_build_overlay_cmd_no_bgm_uses_apad_to_preserve_full_video():
    """Without BGM: audio is apad'd so -shortest picks VIDEO length, not audio."""
    cmd = build_overlay_cmd(Path("/v/in.mp4"), Path("/a/audio.mp3"), None, Path("/o/out.mp4"))
    cmd_str = " ".join(cmd)
    assert "apad" in cmd_str, "apad must be present to pad audio to video length"
    assert "-shortest" in cmd_str, "-shortest must cap output to the finite stream (video)"
    assert "-filter_complex" in cmd_str
    assert "[1:a]apad[a]" in cmd_str
    assert "-map" in cmd_str and "[a]" in cmd_str


def test_build_overlay_cmd_with_bgm_uses_apad_on_voice():
    """With BGM: apad is on the voice chain, amix duration=first (voice=infinite)."""
    cmd = build_overlay_cmd(Path("/v/in.mp4"), Path("/a/audio.mp3"), Path("/b/bgm.mp3"), Path("/o/out.mp4"))
    cmd_str = " ".join(cmd)
    assert "apad" in cmd_str
    assert "duration=first" in cmd_str
    assert "-shortest" in cmd_str
    assert "volume=1.0,apad" in cmd_str, "apad must be chained after volume on voice"
    assert cmd.count("-i") == 3  # video + audio + bgm


def test_build_overlay_cmd_no_bgm_has_two_inputs():
    """No BGM: 2 inputs (video + audio)."""
    cmd = build_overlay_cmd(Path("/v/in.mp4"), Path("/a/audio.mp3"), None, Path("/o/out.mp4"))
    assert cmd.count("-i") == 2


@pytest.mark.asyncio
async def test_overlay_runs_ffmpeg_and_returns_output(tmp_path):
    (tmp_path / "audio.mp3").write_bytes(b"TTS")
    video = tmp_path / "silent.mp4"
    video.write_bytes(b"VID")

    with patch("app.gateway.video_gen.engines.vimax.audio_overlay.run_ffmpeg", new=AsyncMock()) as mock_ff:
        out = await overlay_audio_track(video, tmp_path)

    assert out == tmp_path / "vimax_final.mp4"
    # P2-13 起两趟：混音 + loudnorm（mock 不产文件 → loudnorm 产物缺失 → 回落原产物）
    assert mock_ff.await_count == 2
    sent_cmd = mock_ff.call_args_list[0].args[1]
    assert str(video) in sent_cmd and str(tmp_path / "audio.mp3") in sent_cmd


@pytest.mark.asyncio
async def test_overlay_noop_when_no_audio(tmp_path):
    """If prep produced no audio.mp3 (e.g. draft-only path), return the video unchanged."""
    video = tmp_path / "silent.mp4"
    video.write_bytes(b"VID")

    with patch("app.gateway.video_gen.engines.vimax.audio_overlay.run_ffmpeg", new=AsyncMock()) as mock_ff:
        out = await overlay_audio_track(video, tmp_path)

    assert out == video  # unchanged
    mock_ff.assert_not_awaited()


def test_run_cmd_error_tail_keeps_2000_bytes(monkeypatch):
    """ffmpeg stderr 很长时，错误信息保留末 2000 字节（500 会切掉 libass 决定性行）。"""
    import subprocess as sp

    from app.gateway.video_gen.engines.vimax import audio_overlay as ao

    marker = b"Unable to open /app/x/subtitle.srt"
    long_tail = marker + b"\n" + (b"x" * 900)

    def fake_run(cmd, **kwargs):
        return sp.CompletedProcess(cmd, 1, b"", long_tail)

    monkeypatch.setattr(ao.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError) as exc_info:
        ao._run_cmd(["ffmpeg", "-y"])
    assert marker.decode() in str(exc_info.value), "500 字节截断会丢失该行；2000 应保留"


# ---------------------------------------------------------------------------
# 2026-07-27 Task13④：BGM -stream_loop 循环 + volume 吃 storyboard 配置
# ---------------------------------------------------------------------------


def test_overlay_cmd_bgm_loops_and_uses_config_volume(tmp_path):
    """BGM 必须 -stream_loop -1（短 BGM 放一遍即停）且 volume 用参数值（曾硬编码 0.15）。"""
    bgm = tmp_path / "bgm.mp3"
    bgm.write_bytes(b"x")
    cmd = build_overlay_cmd(tmp_path / "v.mp4", tmp_path / "a.mp3", bgm, tmp_path / "o.mp4", bgm_volume=0.3)
    assert "-stream_loop" in cmd
    assert "volume=0.3" in " ".join(cmd)


def test_overlay_cmd_bgm_default_volume_unchanged(tmp_path):
    """缺省 bgm_volume=0.15，保持既有混音行为不变。"""
    bgm = tmp_path / "bgm.mp3"
    bgm.write_bytes(b"x")
    cmd = build_overlay_cmd(tmp_path / "v.mp4", tmp_path / "a.mp3", bgm, tmp_path / "o.mp4")
    assert "volume=0.15" in " ".join(cmd)


@pytest.mark.asyncio
async def test_overlay_reads_bgm_volume_from_storyboard(tmp_path):
    """overlay_audio_track 从 task_dir/storyboard.json 读 config.bgm_volume 并传给 ffmpeg 命令。"""
    import json

    (tmp_path / "audio.mp3").write_bytes(b"TTS")
    (tmp_path / "bgm.mp3").write_bytes(b"BGM")
    (tmp_path / "storyboard.json").write_text(json.dumps({"config": {"bgm_volume": 0.42}}), encoding="utf-8")
    video = tmp_path / "silent.mp4"
    video.write_bytes(b"VID")

    with patch("app.gateway.video_gen.engines.vimax.audio_overlay.run_ffmpeg", new=AsyncMock()) as mock_ff:
        out = await overlay_audio_track(video, tmp_path)

    assert out == tmp_path / "vimax_final.mp4"
    sent_cmd = mock_ff.call_args_list[0].args[1]  # 第一趟=混音（第二趟是 loudnorm）
    assert "-stream_loop" in sent_cmd
    assert "volume=0.42" in " ".join(sent_cmd)


# ── P2-13：ViMax 成品响度归一（与 worker 三管线同标准 I=-16/TP=-1.5/LRA=11）──


@pytest.mark.asyncio
async def test_overlay_applies_loudness_normalization(tmp_path):
    """混音完成后必须再跑一趟 loudnorm；交付归一化后的产物。"""
    (tmp_path / "audio.mp3").write_bytes(b"TTS")
    video = tmp_path / "silent.mp4"
    video.write_bytes(b"VID")

    async def fake_ffmpeg(fn, cmd):
        out = Path(cmd[-1])
        out.write_bytes(b"OUT")  # 模拟 ffmpeg 产出

    with patch("app.gateway.video_gen.engines.vimax.audio_overlay.run_ffmpeg", new=fake_ffmpeg):
        out = await overlay_audio_track(video, tmp_path)

    assert out.name == "vimax_final_loudnorm.mp4", f"应交付响度归一化产物，实得 {out.name}"


@pytest.mark.asyncio
async def test_overlay_loudnorm_cmd_matches_house_standard(tmp_path):
    """loudnorm 参数与 worker _normalize_loudness 一致：I=-16/TP=-1.5/LRA=11，-c:v copy。"""
    (tmp_path / "audio.mp3").write_bytes(b"TTS")
    video = tmp_path / "silent.mp4"
    video.write_bytes(b"VID")
    seen_cmds: list[list[str]] = []

    async def fake_ffmpeg(fn, cmd):
        seen_cmds.append(cmd)
        Path(cmd[-1]).write_bytes(b"OUT")

    with patch("app.gateway.video_gen.engines.vimax.audio_overlay.run_ffmpeg", new=fake_ffmpeg):
        await overlay_audio_track(video, tmp_path)

    assert len(seen_cmds) == 2, f"应跑两趟 ffmpeg（混音+loudnorm），实跑 {len(seen_cmds)}"
    loud = " ".join(seen_cmds[1])
    assert "loudnorm=I=-16.0:TP=-1.5:LRA=11" in loud
    assert "-c:v copy" in loud, "视频流必须 copy（秒级），只重编码音频"


@pytest.mark.asyncio
async def test_overlay_loudnorm_failure_keeps_unnormalized(tmp_path):
    """loudnorm 失败不得阻断出片——回落交付未归一化的混音产物。"""
    (tmp_path / "audio.mp3").write_bytes(b"TTS")
    video = tmp_path / "silent.mp4"
    video.write_bytes(b"VID")
    calls: list[list[str]] = []

    async def fake_ffmpeg(fn, cmd):
        calls.append(cmd)
        if any(a.startswith("loudnorm=") for a in cmd):
            raise RuntimeError("loudnorm exploded")
        Path(cmd[-1]).write_bytes(b"OUT")

    with patch("app.gateway.video_gen.engines.vimax.audio_overlay.run_ffmpeg", new=fake_ffmpeg):
        out = await overlay_audio_track(video, tmp_path)

    assert out == tmp_path / "vimax_final.mp4", f"loudnorm 失败应回落原混音产物，实得 {out}"
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_overlay_no_audio_skips_loudnorm(tmp_path):
    """无配音早退路径（静默视频）不得跑 loudnorm——无音轨归一无意义。"""
    video = tmp_path / "silent.mp4"
    video.write_bytes(b"VID")
    seen: list[list[str]] = []

    async def fake_ffmpeg(fn, cmd):
        seen.append(cmd)

    with patch("app.gateway.video_gen.engines.vimax.audio_overlay.run_ffmpeg", new=fake_ffmpeg):
        out = await overlay_audio_track(video, tmp_path)

    assert out == video
    assert seen == [], f"无音轨不应调 ffmpeg，实调 {len(seen)} 次"
