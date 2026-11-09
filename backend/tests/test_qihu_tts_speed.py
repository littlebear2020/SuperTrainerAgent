"""360 TTS 语速透传链路（2026-07-27 P1：_synthesize_qihu 收 speed 但从不使用）。

两个分支：
1. 默认——speed 透传到 QihuTTSClient.synthesize_sse 的 payload（360 API 若支持则服务端变速）。
2. QIHU_TTS_SPEED_ATEMPO=1——speed 不下发 API，改走 ffmpeg atempo 后处理（保底路径）。
"""

from pathlib import Path

import pytest


def _minimal_wav_bytes() -> bytes:
    """1 帧 8kHz 单声道 16bit 静音 WAV。"""
    import io
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\x00\x00")
    return buf.getvalue()


@pytest.mark.asyncio
async def test_synthesize_qihu_passes_speed_to_client(monkeypatch, tmp_path):
    """_synthesize_qihu 必须把 speed 透传到 QihuTTSClient.synthesize_sse。"""
    from app.gateway.video_gen.orchestration import tts as tts_mod

    captured: dict = {}

    class _FakeClient:
        def __init__(self, api_key: str) -> None:
            pass

        async def synthesize_sse(self, text, voice, response_format="base64", speed=None):
            captured["speed"] = speed
            return _minimal_wav_bytes()

    async def _fake_run_ffmpeg(fn, *args):
        # 不真跑 ffmpeg：_wav_to_mp3 直接产出目标 mp3；_wav_atempo 不动；
        # 其余（如 _get_audio_duration 的 monkeypatch lambda）直接调用
        if fn is tts_mod._wav_to_mp3:
            Path(args[-1]).write_bytes(b"ID3-fake-mp3")
            return None
        if fn is tts_mod._wav_atempo:
            return None
        return fn(*args)

    monkeypatch.setenv("QIHU_API_KEY", "test-key")
    monkeypatch.delenv("QIHU_TTS_SPEED_ATEMPO", raising=False)
    monkeypatch.setattr("app.gateway.voice_twin.qihu_tts_client.QihuTTSClient", _FakeClient)
    monkeypatch.setattr(tts_mod, "run_ffmpeg", _fake_run_ffmpeg)
    monkeypatch.setattr(tts_mod, "_get_audio_duration", lambda path: 1.0)

    await tts_mod._synthesize_qihu("测试文本", "preset:146", 1.8, str(tmp_path / "out.mp3"))
    assert captured["speed"] == 1.8


@pytest.mark.asyncio
async def test_synthesize_qihu_atempo_fallback_env(monkeypatch, tmp_path):
    """QIHU_TTS_SPEED_ATEMPO=1：speed 不下发 API（None），改走 ffmpeg atempo 后处理。"""
    from app.gateway.video_gen.orchestration import tts as tts_mod

    captured: dict = {}
    atempo_calls: list = []

    class _FakeClient:
        def __init__(self, api_key: str) -> None:
            pass

        async def synthesize_sse(self, text, voice, response_format="base64", speed=None):
            captured["speed"] = speed
            return _minimal_wav_bytes()

    async def _fake_run_ffmpeg(fn, *args):
        if fn is tts_mod._wav_to_mp3:
            Path(args[-1]).write_bytes(b"ID3-fake-mp3")
            return None
        if fn is tts_mod._wav_atempo:
            atempo_calls.append(args)
            return None
        return fn(*args)

    monkeypatch.setenv("QIHU_API_KEY", "test-key")
    monkeypatch.setenv("QIHU_TTS_SPEED_ATEMPO", "1")
    monkeypatch.setattr("app.gateway.voice_twin.qihu_tts_client.QihuTTSClient", _FakeClient)
    monkeypatch.setattr(tts_mod, "run_ffmpeg", _fake_run_ffmpeg)
    monkeypatch.setattr(tts_mod, "_get_audio_duration", lambda path: 1.0)

    await tts_mod._synthesize_qihu("测试文本", "preset:146", 1.8, str(tmp_path / "out.mp3"))
    assert captured["speed"] is None, "atempo 模式下 speed 不得下发 360 API"
    assert len(atempo_calls) == 1, "atempo 模式必须走一次 ffmpeg atempo 后处理"
    assert atempo_calls[0][1] == 1.8
