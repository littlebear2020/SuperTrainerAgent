"""一.3 根因：空旁白帧（纯画面标记剥离后）不得让整批 TTS 崩。

日志证据：doubao TTS `45002001 No readable text!` → asyncio.gather 整批失败 →
_generate_draft_audio 写 audio_error → 前端"音频生成失败，请重试"。
修法：synth_one 对空旁白走静音占位，不送 TTS，且不让一帧拖垮整批。
"""

import pytest


@pytest.mark.asyncio
async def test_synthesize_audio_skips_empty_narration_frame(tmp_path, monkeypatch):
    from pathlib import Path

    from app.gateway.video_gen import service as svc_mod
    from app.gateway.video_gen.orchestration import tts as tts_mod
    from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame

    cfg = StoryboardConfig(video_type="ai_image", width=1080, height=1920)
    frames = [
        StoryboardFrame(index=0, narration=""),  # 空 → 修前 doubao "No readable text" → gather 崩
        StoryboardFrame(index=1, narration="你好世界"),  # 正常
    ]
    sb = Storyboard(title="t", config=cfg, frames=frames)

    # mock TTS：空文本抛错（模拟 doubao 45002001），正常文本写文件返回
    async def fake_synthesize(text, *, voice_id, speed, output_path, fallback_voice=None, **kwargs):
        if not text.strip():
            raise RuntimeError("No readable text")
        Path(output_path).write_bytes(b"mp3")
        return tts_mod.TTSResult(audio_path=output_path, srt="", duration=2.0)

    monkeypatch.setattr(tts_mod, "synthesize", fake_synthesize)

    # mock 静音占位（fix 引入的 helper；raising=False 让修前 attr 不存在也不报错）
    def fake_silent(path, seconds=1.0):
        Path(path).write_bytes(b"silent")

    monkeypatch.setattr(svc_mod, "_write_silent_audio", fake_silent, raising=False)

    # mock concat + srt（不依赖真 ffmpeg 的合成步骤）
    async def fake_run_ffmpeg(fn, args, out, **kw):
        return None

    monkeypatch.setattr(svc_mod, "run_ffmpeg", fake_run_ffmpeg)
    monkeypatch.setattr(svc_mod, "_write_storyboard_srt", lambda *a, **k: None)

    svc = svc_mod.VideoGenService.__new__(svc_mod.VideoGenService)
    svc._config = type("C", (), {"qihu_api_key": None, "tts_fallback_voice": "preset:146"})()

    # 关键断言：_synthesize_audio 不抛（修前会因空帧崩）
    await svc._synthesize_audio(sb, tmp_path, "preset:146")

    # 两帧都拿到 audio_path：空帧走静音占位，正常帧走 TTS
    assert frames[0].audio_path, "空旁白帧须有静音占位 audio_path"
    assert Path(frames[0].audio_path).exists()
    assert frames[1].audio_path and Path(frames[1].audio_path).exists()
