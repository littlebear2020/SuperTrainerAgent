from __future__ import annotations

import asyncio
import base64
from types import SimpleNamespace

import pytest

from app.gateway.video_gen.orchestration import tts as tts_mod


def _ts_adapter(fn):
    async def _wrapped(self, *args, **kwargs):
        return await fn(self, *args, **kwargs), []

    return _wrapped


class _HangingResponse:
    is_error = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def aiter_lines(self):
        await asyncio.sleep(10)
        if False:
            yield ""


class _FakeAsyncClient:
    def __init__(self, timeout=None):
        self.timeout = timeout

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def stream(self, *args, **kwargs):
        return _HangingResponse()


@pytest.mark.asyncio
async def test_doubao_hanging_sse_raises_timeout(monkeypatch):
    from app.gateway.voice_twin import doubao_tts_client as mod

    monkeypatch.setattr(mod, "httpx", SimpleNamespace(AsyncClient=_FakeAsyncClient))
    monkeypatch.setenv("TTS_SSE_TIMEOUT", "0.01")
    client = mod.DoubaoTTSClient(access_key="k")
    with pytest.raises(asyncio.TimeoutError):
        await client.synthesize_sse(speaker="spk", text="你好")


class _DoubaoOkResponse:
    is_error = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def aiter_lines(self):
        audio = base64.b64encode(b"\x01\x02\x03").decode()
        yield "event: 352"
        yield f'data: {{"code": 0, "data": "{audio}"}}'
        yield ""
        yield "event: 152"
        yield 'data: {"code": 20000000}'
        yield ""


@pytest.mark.asyncio
async def test_doubao_normal_sse_unaffected_by_timeout(monkeypatch):
    from app.gateway.voice_twin import doubao_tts_client as mod

    class _OkClient(_FakeAsyncClient):
        def stream(self, *args, **kwargs):
            return _DoubaoOkResponse()

    monkeypatch.setattr(mod, "httpx", SimpleNamespace(AsyncClient=_OkClient))
    monkeypatch.setenv("TTS_SSE_TIMEOUT", "0.5")
    client = mod.DoubaoTTSClient(access_key="k")
    assert await client.synthesize_sse(speaker="spk", text="你好") == b"\x01\x02\x03"


def test_tts_sse_timeout_default_and_env(monkeypatch):
    from app.gateway.voice_twin._retry import tts_sse_timeout

    monkeypatch.delenv("TTS_SSE_TIMEOUT", raising=False)
    assert tts_sse_timeout() == 120.0
    monkeypatch.setenv("TTS_SSE_TIMEOUT", "45")
    assert tts_sse_timeout() == 45.0


@pytest.mark.asyncio
async def test_call_with_tts_retry_retries_timeout():
    from app.gateway.voice_twin._retry import call_with_tts_retry

    calls = 0

    async def flaky():
        nonlocal calls
        calls += 1
        raise TimeoutError()

    with pytest.raises(RuntimeError, match="failed after 3 retries"):
        await call_with_tts_retry(flaky, base_delay=0)
    assert calls == 3


@pytest.mark.asyncio
async def test_doubao_timeout_retries_3x_then_fails_without_degrading(tmp_path, monkeypatch):
    import tenacity

    from app.gateway.voice_twin.doubao_tts_client import DoubaoTTSClient

    attempts = 0

    async def boom(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        raise TimeoutError()

    monkeypatch.setattr(DoubaoTTSClient, "synthesize_sse", boom)
    monkeypatch.setattr(DoubaoTTSClient, "synthesize_sse_with_timestamps", _ts_adapter(boom))
    monkeypatch.setenv("VOLCENGINE_VOICE_ACCESS_KEY", "k")
    monkeypatch.setattr(tts_mod._call_doubao_with_retry.retry, "wait", tenacity.wait_none())

    with pytest.raises(TimeoutError):
        await tts_mod._synthesize_doubao("hi", "doubao:spk1", 1.0, str(tmp_path / "f.mp3"))
    assert attempts == 3
