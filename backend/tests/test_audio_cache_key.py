"""CAS 音频键必须包含 narration+音色+速度——防跨代同名覆盖（2026-07-21 漂移根因环 1）。"""

from pathlib import Path

import pytest


@pytest.mark.asyncio
async def test_cache_key_varies_with_voice_and_speed(tmp_path, monkeypatch):
    """同旁白：不同 voice / 不同 speed → 不同文件；同参数 → 同文件（去重保留）。"""
    from app.gateway.video_gen import service as svc_mod
    from app.gateway.video_gen.orchestration import tts as tts_mod
    from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame

    written = []

    async def fake_synthesize(text, *, voice_id, speed, output_path, fallback_voice=None, **kwargs):
        written.append((voice_id, speed, Path(output_path).name))
        Path(output_path).write_bytes(b"mp3")
        return tts_mod.TTSResult(audio_path=output_path, srt="", duration=3.0)

    async def fake_run_ffmpeg(*a, **k):
        return None

    monkeypatch.setattr(tts_mod, "synthesize", fake_synthesize)
    monkeypatch.setattr(svc_mod, "run_ffmpeg", fake_run_ffmpeg)
    monkeypatch.setattr(svc_mod, "_write_storyboard_srt", lambda *a, **k: None)

    svc = svc_mod.VideoGenService.__new__(svc_mod.VideoGenService)
    svc._config = type("C", (), {"qihu_api_key": None, "tts_fallback_voice": "preset:146"})()

    def _sb():
        return Storyboard(
            title="t",
            config=StoryboardConfig(video_type="ai_image", width=1080, height=1920),
            frames=[StoryboardFrame(index=0, narration="同一句旁白")],
        )

    await svc._synthesize_audio(_sb(), tmp_path, "preset:146")  # 基准
    await svc._synthesize_audio(_sb(), tmp_path, "preset:147")  # 换音色
    sb_fast = _sb()
    sb_fast.config.tts_speed = 1.0
    await svc._synthesize_audio(sb_fast, tmp_path, "preset:146")  # 换速度(1.2→1.0)

    names = [n for _, _, n in written]
    assert len(set(names)) == 3, f"音色/速度不同必须生成不同文件（防跨代覆盖）: {names}"


def test_audio_cache_key_helper_stable_and_distinct():
    """纯函数：同参稳定；narration/voice/speed 任一变化 → 键变。"""
    from app.gateway.video_gen.service import _audio_cache_key

    base = _audio_cache_key("旁白", "preset:146", 1.2)
    assert base == _audio_cache_key("旁白", "preset:146", 1.2)  # 稳定（去重语义保留）
    assert base != _audio_cache_key("旁白", "preset:147", 1.2)  # 音色变
    assert base != _audio_cache_key("旁白", "preset:146", 1.0)  # 速度变
    assert base != _audio_cache_key("别的旁白", "preset:146", 1.2)  # 文本变
    assert len(base) == 12


@pytest.mark.asyncio
async def test_synthesize_audio_reuses_cas_hit(tmp_path, monkeypatch):
    """CAS 命中（同 narration|voice|speed）→ 不调 TTS，直接复用并 ffprobe 校时。"""
    from app.gateway.video_gen import service as svc_mod
    from app.gateway.video_gen.orchestration import tts as tts_mod
    from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame

    svc = svc_mod.VideoGenService.__new__(svc_mod.VideoGenService)
    svc._config = type("C", (), {"qihu_api_key": None, "tts_fallback_voice": "preset:146"})()
    task_dir = tmp_path / "task"
    (task_dir / "audio").mkdir(parents=True)
    frame = StoryboardFrame(index=0, narration="重复口号", duration=1.0)
    storyboard = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_image", width=1080, height=1920, voice_id="preset:146", tts_speed=1.0),
        frames=[frame],
    )
    key = svc_mod._audio_cache_key(frame.narration, "preset:146", 1.0)
    (task_dir / "audio" / f"{key}.mp3").write_bytes(b"cached")

    calls: list = []

    async def fake_synth(*a, **k):
        calls.append(a)
        raise AssertionError("CAS 命中不得调 TTS")

    async def fake_run_ffmpeg(*a, **k):
        return None

    monkeypatch.setattr(tts_mod, "synthesize", fake_synth)
    monkeypatch.setattr(svc_mod, "_get_audio_duration", lambda p: 2.5)
    monkeypatch.setattr(svc_mod, "run_ffmpeg", fake_run_ffmpeg)
    monkeypatch.setattr(svc_mod, "_write_storyboard_srt", lambda *a, **k: None)

    await svc._synthesize_audio(storyboard, task_dir, "preset:146")
    assert not calls
    assert frame.audio_path.endswith(f"{key}.mp3")
    assert frame.duration == 2.5
