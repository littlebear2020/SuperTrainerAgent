"""Risk D: _synthesize_qihu must retry transport errors before edge-tts fallback.

Mirrors NanoBanana's _RETRYABLE_TRANSPORT_ERRORS pattern. Without retry, a
single 360 transient disconnect degrades the shot to edge-tts (different
voice), producing inconsistent narration across shots.

NOTE: QihuTTSClient is imported locally inside _synthesize_qihu
(``from app.gateway.voice_twin.qihu_tts_client import QihuTTSClient``), so we
patch it on its defining module. run_ffmpeg and _synthesize_edge are
module-level names in tts.py, so patching tts_mod.* works for those.
"""

import httpx
import pytest
import tenacity

from app.gateway.video_gen.orchestration import tts as tts_mod
from app.gateway.video_gen.orchestration.tts import TTSResult
from app.gateway.voice_twin import qihu_tts_client as qihu_client_mod


@pytest.mark.asyncio
async def test_synthesize_qihu_retries_transport_error_then_succeeds(tmp_path, monkeypatch):
    """360 raises RemoteProtocolError once, then succeeds → no edge-tts fallback."""
    monkeypatch.setenv("QIHU_API_KEY", "fake-key")
    # wait_fixed(2) → 0：重试行为不变，只是不再真睡。
    monkeypatch.setattr(tts_mod._call_360_with_retry.retry, "wait", tenacity.wait_none())
    out = tmp_path / "out.mp3"

    call_count = {"n": 0}

    class _FakeClient:
        def __init__(self, *, api_key=None):
            pass

        async def synthesize_sse(self, *, text, voice, speed=None):
            call_count["n"] += 1
            if call_count["n"] < 2:
                raise httpx.RemoteProtocolError("Server disconnected")
            return b"RIFF\x24\x00\x00\x00WAVEfmt "

    monkeypatch.setattr(qihu_client_mod, "QihuTTSClient", _FakeClient)

    # Stub ffmpeg so no real binary needed; just return a float for duration probes.
    async def _fake_run_ffmpeg(fn, *args, **kwargs):
        return 0.0

    monkeypatch.setattr(tts_mod, "run_ffmpeg", _fake_run_ffmpeg)

    result = await tts_mod._synthesize_qihu("hello", "preset:146", 1.0, str(out))

    assert call_count["n"] == 2, f"expected 2 attempts (1 retry + 1 success), got {call_count['n']}"
    assert result.audio_path == str(out)  # got the 360 result, not edge-tts


@pytest.mark.asyncio
async def test_synthesize_qihu_falls_back_after_retries_exhausted(tmp_path, monkeypatch):
    """360 always raises transport error → after 3 attempts, fall back to edge-tts."""
    monkeypatch.setenv("QIHU_API_KEY", "fake-key")
    # wait_fixed(2) → 0：重试 3 次的行为不变，只是不再真睡 4s。
    monkeypatch.setattr(tts_mod._call_360_with_retry.retry, "wait", tenacity.wait_none())
    out = tmp_path / "out.mp3"

    class _FakeClient:
        def __init__(self, *, api_key=None):
            pass

        async def synthesize_sse(self, *, text, voice, speed=None):
            raise httpx.RemoteProtocolError("Server disconnected")

    monkeypatch.setattr(qihu_client_mod, "QihuTTSClient", _FakeClient)

    async def _fake_run_ffmpeg(fn, *args, **kwargs):
        return 0.0

    monkeypatch.setattr(tts_mod, "run_ffmpeg", _fake_run_ffmpeg)
    # Stub edge-tts fallback so the test doesn't hit the network.
    monkeypatch.setattr(
        tts_mod,
        "_synthesize_edge",
        lambda *a, **k: None,
    )

    async def _fake_edge(*args, **kwargs):
        return TTSResult(audio_path=str(out), srt="", duration=0.0)

    monkeypatch.setattr(tts_mod, "_synthesize_edge", _fake_edge)

    # Should not raise — falls back to edge-tts after retries exhausted.
    result = await tts_mod._synthesize_qihu("hello", "preset:146", 1.0, str(out))
    assert result.audio_path == str(out)
