"""G1: seconds_per_frame 驱动每帧时长——TTS 短于 spf 时 frame.duration=max(TTS,spf) + 音频补静音到 spf。

修前 frame.duration=r.duration（TTS 时长），用户设 spf=8 但每帧只显示 TTS 时长(~3s)→画面太快。
"""

import pytest


@pytest.mark.asyncio
async def test_synthesize_audio_spf_extends_frame_duration_and_pads(tmp_path, monkeypatch):
    """spf=8、TTS 产 3s → frame.duration=8 且音频补静音到 8s。"""
    from pathlib import Path

    from app.gateway.video_gen import service as svc_mod
    from app.gateway.video_gen.orchestration import tts as tts_mod
    from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame

    cfg = StoryboardConfig(video_type="ai_image", width=1080, height=1920)
    frames = [StoryboardFrame(index=0, narration="短旁白")]
    sb = Storyboard(title="t", config=cfg, frames=frames)

    async def fake_synthesize(text, *, voice_id, speed, output_path, fallback_voice=None, **kwargs):
        Path(output_path).write_bytes(b"mp3")
        return tts_mod.TTSResult(audio_path=output_path, srt="", duration=3.0)  # TTS 3s

    monkeypatch.setattr(tts_mod, "synthesize", fake_synthesize)

    pad_calls = []

    def _fake_pad(p, t):
        # C9/Task 7：_pad_audio_to_duration 现在**返回**应使用的音频路径（补静音
        # 产物另存，不再原地覆写 CAS 原件）。stub 必须遵守该契约，否则
        # frame.audio_path 会被置空而测试仍绿。
        pad_calls.append((str(p), t))
        return str(p)

    monkeypatch.setattr(svc_mod, "_pad_audio_to_duration", _fake_pad, raising=False)

    async def fake_run_ffmpeg(fn, args, out, **kw):
        return None

    monkeypatch.setattr(svc_mod, "run_ffmpeg", fake_run_ffmpeg)
    monkeypatch.setattr(svc_mod, "_write_storyboard_srt", lambda *a, **k: None)

    svc = svc_mod.VideoGenService.__new__(svc_mod.VideoGenService)
    svc._config = type("C", (), {"qihu_api_key": None, "tts_fallback_voice": "preset:146"})()

    await svc._synthesize_audio(sb, tmp_path, "preset:146", seconds_per_frame=8)

    assert frames[0].duration == 8.0, f"spf=8 时 frame.duration 须 8（修前=TTS 3s）：{frames[0].duration}"
    assert pad_calls, "spf>TTS 时须补静音到 spf"
    assert pad_calls[0][1] == 8.0
    # C9/Task 7：调用方必须采用 _pad_audio_to_duration 的返回值，否则
    # frame.audio_path 会指向未补静音的 CAS 原件（音画不等长）。
    assert frames[0].audio_path, "frame.audio_path 不得被补静音流程置空"


@pytest.mark.asyncio
async def test_synthesize_audio_no_spf_keeps_tts_duration(tmp_path, monkeypatch):
    """无 spf → frame.duration=TTS 时长（不回归）。"""
    from pathlib import Path

    from app.gateway.video_gen import service as svc_mod
    from app.gateway.video_gen.orchestration import tts as tts_mod
    from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame

    sb = Storyboard(title="t", config=StoryboardConfig(video_type="ai_image", width=1080, height=1920), frames=[StoryboardFrame(index=0, narration="短旁白")])

    async def fake_synthesize(text, *, voice_id, speed, output_path, fallback_voice=None, **kwargs):
        Path(output_path).write_bytes(b"mp3")
        return tts_mod.TTSResult(audio_path=output_path, srt="", duration=3.0)

    monkeypatch.setattr(tts_mod, "synthesize", fake_synthesize)
    monkeypatch.setattr(svc_mod, "_pad_audio_to_duration", lambda p, t: str(p), raising=False)

    async def fake_run_ffmpeg_noop(*a, **k):
        return None

    monkeypatch.setattr(svc_mod, "run_ffmpeg", fake_run_ffmpeg_noop)
    monkeypatch.setattr(svc_mod, "_write_storyboard_srt", lambda *a, **k: None)

    svc = svc_mod.VideoGenService.__new__(svc_mod.VideoGenService)
    svc._config = type("C", (), {"qihu_api_key": None, "tts_fallback_voice": "preset:146"})()

    await svc._synthesize_audio(sb, tmp_path, "preset:146")  # 无 seconds_per_frame
    assert sb.frames[0].duration == 3.0, "无 spf 须保持 TTS 时长"


@pytest.mark.asyncio
async def test_synthesize_audio_records_real_speech_duration(tmp_path, monkeypatch):
    """spf=8、TTS 3s → speech_duration=3（真实语音，字幕配比基准），duration=8（含静音）。"""
    from pathlib import Path

    from app.gateway.video_gen import service as svc_mod
    from app.gateway.video_gen.orchestration import tts as tts_mod
    from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame

    cfg = StoryboardConfig(video_type="ai_image", width=1080, height=1920)
    frames = [StoryboardFrame(index=0, narration="短旁白")]
    sb = Storyboard(title="t", config=cfg, frames=frames)

    async def fake_synthesize(text, *, voice_id, speed, output_path, fallback_voice=None, **kwargs):
        Path(output_path).write_bytes(b"mp3")
        return tts_mod.TTSResult(audio_path=output_path, srt="", duration=3.0)

    monkeypatch.setattr(tts_mod, "synthesize", fake_synthesize)
    monkeypatch.setattr(svc_mod, "_pad_audio_to_duration", lambda p, t: str(p), raising=False)

    async def fake_run_ffmpeg(*a, **k):
        return None

    monkeypatch.setattr(svc_mod, "run_ffmpeg", fake_run_ffmpeg)
    monkeypatch.setattr(svc_mod, "_write_storyboard_srt", lambda *a, **k: None)

    svc = svc_mod.VideoGenService.__new__(svc_mod.VideoGenService)
    svc._config = type("C", (), {"qihu_api_key": None, "tts_fallback_voice": "preset:146"})()

    await svc._synthesize_audio(sb, tmp_path, "preset:146", seconds_per_frame=8)
    assert frames[0].speech_duration == 3.0, "speech_duration 须为真实 TTS 时长（字幕配比基准）"
    assert frames[0].duration == 8.0, "duration 仍为 spf（含静音 padding）"


@pytest.mark.asyncio
async def test_synthesize_audio_empty_narration_speech_duration_zero(tmp_path, monkeypatch):
    """空旁白静音帧 → speech_duration=0.0（无语音，字幕不写条目）。"""

    from app.gateway.video_gen import service as svc_mod
    from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame

    cfg = StoryboardConfig(video_type="ai_image", width=1080, height=1920)
    frames = [StoryboardFrame(index=0, narration="   ")]
    sb = Storyboard(title="t", config=cfg, frames=frames)

    async def fake_run_ffmpeg(*a, **k):
        return None

    monkeypatch.setattr(svc_mod, "run_ffmpeg", fake_run_ffmpeg)
    monkeypatch.setattr(svc_mod, "_write_storyboard_srt", lambda *a, **k: None)

    svc = svc_mod.VideoGenService.__new__(svc_mod.VideoGenService)
    svc._config = type("C", (), {"qihu_api_key": None, "tts_fallback_voice": "preset:146"})()

    await svc._synthesize_audio(sb, tmp_path, "preset:146")
    assert frames[0].speech_duration == 0.0
    assert frames[0].duration == 1.0
