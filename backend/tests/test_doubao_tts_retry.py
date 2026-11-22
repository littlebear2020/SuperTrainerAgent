import httpx
import pytest

from app.gateway.video_gen.orchestration import tts as tts_mod


def _ts_adapter(fn):
    async def _wrapped(self, *args, **kwargs):
        return await fn(self, *args, **kwargs), []

    return _wrapped


@pytest.mark.asyncio
async def test_doubao_retries_transport_error_without_changing_voice(tmp_path, monkeypatch):
    from app.gateway.voice_twin.doubao_tts_client import DoubaoTTSClient

    attempts = 0

    async def flaky(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise httpx.ReadTimeout("transient")
        return b"ID3_audio_bytes"

    monkeypatch.setattr(DoubaoTTSClient, "synthesize_sse", flaky)
    monkeypatch.setattr(DoubaoTTSClient, "synthesize_sse_with_timestamps", _ts_adapter(flaky))
    monkeypatch.setenv("VOLCENGINE_VOICE_ACCESS_KEY", "k")
    monkeypatch.setattr(tts_mod._call_doubao_with_retry.retry, "wait", tts_mod.tenacity.wait_none())

    async def fake_duration(*args, **kwargs):
        return 2.0

    monkeypatch.setattr(tts_mod, "run_ffmpeg", fake_duration)
    result = await tts_mod._synthesize_doubao("你好", "doubao:spk1", 1.0, str(tmp_path / "f.mp3"))
    assert attempts == 3
    assert result.actual_voice_id == "doubao:spk1"


@pytest.mark.asyncio
async def test_doubao_retry_exhaustion_fails_without_degrading(tmp_path, monkeypatch):
    from app.gateway.voice_twin.doubao_tts_client import DoubaoTTSClient

    attempts = 0

    async def always_fail(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        raise httpx.ReadTimeout("down")

    monkeypatch.setattr(DoubaoTTSClient, "synthesize_sse", always_fail)
    monkeypatch.setattr(DoubaoTTSClient, "synthesize_sse_with_timestamps", _ts_adapter(always_fail))
    monkeypatch.setenv("VOLCENGINE_VOICE_ACCESS_KEY", "k")
    monkeypatch.setattr(tts_mod._call_doubao_with_retry.retry, "wait", tts_mod.tenacity.wait_none())

    with pytest.raises(httpx.ReadTimeout, match="down"):
        await tts_mod._synthesize_doubao("你好", "doubao:spk1", 1.0, str(tmp_path / "f.mp3"))
    assert attempts == 3


@pytest.mark.asyncio
async def test_doubao_retries_5xx_without_degrading(tmp_path, monkeypatch):
    from app.gateway.voice_twin.doubao_tts_client import DoubaoTTSClient, DoubaoTTSHTTPError

    attempts = 0

    async def flaky(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts < 2:
            raise DoubaoTTSHTTPError(500, "server error")
        return b"audio"

    monkeypatch.setattr(DoubaoTTSClient, "synthesize_sse", flaky)
    monkeypatch.setattr(DoubaoTTSClient, "synthesize_sse_with_timestamps", _ts_adapter(flaky))
    monkeypatch.setenv("VOLCENGINE_VOICE_ACCESS_KEY", "k")
    monkeypatch.setattr(tts_mod._call_doubao_with_retry.retry, "wait", tts_mod.tenacity.wait_none())

    async def fake_duration(*args, **kwargs):
        return 2.0

    monkeypatch.setattr(tts_mod, "run_ffmpeg", fake_duration)
    result = await tts_mod._synthesize_doubao("你好", "doubao:spk1", 1.0, str(tmp_path / "f.mp3"))
    assert attempts == 2
    assert result.actual_voice_id == "doubao:spk1"


@pytest.mark.asyncio
async def test_doubao_4xx_raises_without_retry(tmp_path, monkeypatch):
    from app.gateway.voice_twin.doubao_tts_client import DoubaoTTSClient, DoubaoTTSHTTPError

    attempts = 0

    async def bad_auth(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        raise DoubaoTTSHTTPError(401, "unauthorized")

    monkeypatch.setattr(DoubaoTTSClient, "synthesize_sse", bad_auth)
    monkeypatch.setattr(DoubaoTTSClient, "synthesize_sse_with_timestamps", _ts_adapter(bad_auth))
    monkeypatch.setenv("VOLCENGINE_VOICE_ACCESS_KEY", "k")
    with pytest.raises(DoubaoTTSHTTPError):
        await tts_mod._synthesize_doubao("你好", "doubao:spk1", 1.0, str(tmp_path / "f.mp3"))
    assert attempts == 1


@pytest.mark.asyncio
async def test_missing_access_key_fails_loudly(tmp_path, monkeypatch):
    monkeypatch.delenv("VOLCENGINE_VOICE_ACCESS_KEY", raising=False)
    with pytest.raises(RuntimeError, match="VOLCENGINE_VOICE_ACCESS_KEY"):
        await tts_mod._synthesize_doubao("你好", "doubao:spk1", 1.0, str(tmp_path / "f.mp3"))
