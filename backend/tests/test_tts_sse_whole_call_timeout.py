"""T1（2026-08-14 计划）：doubao/qihu TTS SSE 整调用超时。

修前两客户端只靠 httpx 标量 timeout——其 read 语义是**字节间间隔**而非整调用上限：
SSE 停滞（连接存活但零数据）或慢速滴字节时永不触发，`async for ... aiter_lines()`
永不退出 → TTS 阶段（progress=10）永挂 running、零错误日志（服务器实测 20+ 分钟）。
修复对齐 edge C4 同款：``asyncio.wait_for`` 包整个 SSE 消费循环，env ``TTS_SSE_TIMEOUT``
默认 120s；超时抛 ``asyncio.TimeoutError``，落入既有 tenacity 重试 + 360/edge 降级链。
"""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.gateway.video_gen.orchestration import tts as tts_mod
from app.gateway.video_gen.orchestration.tts import TTSResult


def _ts_adapter(fn):
    """T10 桩适配：把既有 synthesize_sse 桩包成 with_timestamps 形态 (bytes, [])。"""

    async def _wrapped(self, *a, **kw):
        return await fn(self, *a, **kw), []

    return _wrapped


class _HangingResponse:
    """SSE 流停滞：aiter_lines 永不 yield（hang 远长于测试窗口 0.01s）。"""

    is_error = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def aiter_lines(self):
        await asyncio.sleep(10)  # 修前：客户端永不超时，测试 10s 后拿不到 TimeoutError → 红
        if False:
            yield ""  # pragma: no cover


class _FakeAsyncClient:
    def __init__(self, timeout=None):
        self.timeout = timeout

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def stream(self, *a, **kw):
        return _HangingResponse()


# ---------------------------------------------------------------------------
# 客户端层：假 SSE 流永不 yield → wait_for 窗口内抛 TimeoutError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_doubao_hanging_sse_raises_timeout(monkeypatch):
    from app.gateway.voice_twin import doubao_tts_client as mod

    monkeypatch.setattr(mod, "httpx", SimpleNamespace(AsyncClient=_FakeAsyncClient))
    monkeypatch.setenv("TTS_SSE_TIMEOUT", "0.01")
    client = mod.DoubaoTTSClient(access_key="k")
    with pytest.raises(asyncio.TimeoutError):
        await client.synthesize_sse(speaker="spk", text="你好")


@pytest.mark.asyncio
async def test_qihu_hanging_sse_raises_timeout(monkeypatch):
    from app.gateway.voice_twin import qihu_tts_client as mod

    monkeypatch.setattr(mod, "httpx", SimpleNamespace(AsyncClient=_FakeAsyncClient))
    monkeypatch.setenv("TTS_SSE_TIMEOUT", "0.01")
    client = mod.QihuTTSClient(api_key="k")
    with pytest.raises(asyncio.TimeoutError):
        await client.synthesize_sse(text="你好", voice=146)


# ---------------------------------------------------------------------------
# 回归保护：正常快速流不受整调用超时影响
# ---------------------------------------------------------------------------


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


class _QihuOkResponse:
    is_error = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def aiter_lines(self):
        audio = base64.b64encode(b"\x04\x05\x06").decode()
        yield f'data: {{"code": "0", "data": {{"content": "{audio}"}}}}'
        yield ""
        yield "data: [DONE]"


@pytest.mark.asyncio
async def test_doubao_normal_sse_unaffected_by_timeout(monkeypatch):
    from app.gateway.voice_twin import doubao_tts_client as mod

    class _OkClient(_FakeAsyncClient):
        def stream(self, *a, **kw):
            return _DoubaoOkResponse()

    monkeypatch.setattr(mod, "httpx", SimpleNamespace(AsyncClient=_OkClient))
    monkeypatch.setenv("TTS_SSE_TIMEOUT", "0.5")
    client = mod.DoubaoTTSClient(access_key="k")
    out = await client.synthesize_sse(speaker="spk", text="你好")
    assert out == b"\x01\x02\x03"


@pytest.mark.asyncio
async def test_qihu_normal_sse_unaffected_by_timeout(monkeypatch):
    from app.gateway.voice_twin import qihu_tts_client as mod

    class _OkClient(_FakeAsyncClient):
        def stream(self, *a, **kw):
            return _QihuOkResponse()

    monkeypatch.setattr(mod, "httpx", SimpleNamespace(AsyncClient=_OkClient))
    monkeypatch.setenv("TTS_SSE_TIMEOUT", "0.5")
    client = mod.QihuTTSClient(api_key="k")
    out = await client.synthesize_sse(text="你好", voice=146)
    assert out == b"\x04\x05\x06"


def test_tts_sse_timeout_default_and_env(monkeypatch):
    """env TTS_SSE_TIMEOUT 可调，默认 120s（决策 D2：doubao 是主力，宁可多等不误降级全片换音色）。"""
    from app.gateway.voice_twin._retry import tts_sse_timeout

    monkeypatch.delenv("TTS_SSE_TIMEOUT", raising=False)
    assert tts_sse_timeout() == 120.0
    monkeypatch.setenv("TTS_SSE_TIMEOUT", "45")
    assert tts_sse_timeout() == 45.0


# ---------------------------------------------------------------------------
# 重试与降级链：TimeoutError 必须落入 tenacity 重试 → 仍败则降级 + 留痕
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_call_with_tts_retry_retries_timeout():
    """voice_twin production 路径核实点：TimeoutError 必须在可重试组——修前该路径超时后不重试。"""
    from app.gateway.voice_twin._retry import call_with_tts_retry

    calls = {"n": 0}

    async def flaky():
        calls["n"] += 1
        raise TimeoutError()

    with pytest.raises(RuntimeError, match="failed after 3 retries"):
        await call_with_tts_retry(flaky, base_delay=0)
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_doubao_timeout_retries_3x_then_degrades_360(tmp_path, monkeypatch):
    """T1 链路确认：doubao 客户端超时 → tenacity 3 次重试 → 360 降级 + record_tts_degradation。"""
    import tenacity

    from app.gateway.voice_twin.doubao_tts_client import DoubaoTTSClient

    attempts = {"n": 0}

    async def boom(*a, **kw):
        attempts["n"] += 1
        raise TimeoutError()

    monkeypatch.setattr(DoubaoTTSClient, "synthesize_sse", boom)
    monkeypatch.setattr(DoubaoTTSClient, "synthesize_sse_with_timestamps", _ts_adapter(boom))
    monkeypatch.setenv("VOLCENGINE_VOICE_ACCESS_KEY", "k")
    monkeypatch.setattr(tts_mod._call_doubao_with_retry.retry, "wait", tenacity.wait_none())
    degradations: list = []
    monkeypatch.setattr(tts_mod, "record_tts_degradation", lambda **kw: degradations.append(kw))

    async def fake_qihu(text, voice_id, speed, output_path):
        Path(output_path).write_bytes(b"QIHUAUDIO")
        return TTSResult(audio_path=output_path, srt="", duration=2.0)

    monkeypatch.setattr(tts_mod, "_synthesize_qihu", fake_qihu)
    out = tmp_path / "f.mp3"
    r = await tts_mod._synthesize_doubao("hi", "doubao:spk1", 1.0, str(out))
    assert attempts["n"] == 3
    assert out.read_bytes() == b"QIHUAUDIO"
    assert r.duration == 2.0
    assert degradations, "降级必须留痕（record_tts_degradation）"


@pytest.mark.asyncio
async def test_qihu_timeout_retries_3x_then_degrades_edge(tmp_path, monkeypatch):
    """T1 链路确认（360 路径）：qihu 超时 → 3 次重试 → edge 降级 + 留痕。"""
    import tenacity

    from app.gateway.voice_twin.qihu_tts_client import QihuTTSClient

    attempts = {"n": 0}

    async def boom(*a, **kw):
        attempts["n"] += 1
        raise TimeoutError()

    monkeypatch.setattr(QihuTTSClient, "synthesize_sse", boom)
    monkeypatch.setenv("QIHU_API_KEY", "k")
    monkeypatch.setattr(tts_mod._call_360_with_retry.retry, "wait", tenacity.wait_none())
    degradations: list = []
    monkeypatch.setattr(tts_mod, "record_tts_degradation", lambda **kw: degradations.append(kw))

    async def fake_edge(text, voice_id, speed, output_path):
        Path(output_path).write_bytes(b"EDGEAUDIO")
        return TTSResult(audio_path=output_path, srt="", duration=1.0)

    monkeypatch.setattr(tts_mod, "_synthesize_edge", fake_edge)
    out = tmp_path / "f.mp3"
    await tts_mod._synthesize_qihu("hi", "preset:146", 1.0, str(out))
    assert attempts["n"] == 3
    assert out.read_bytes() == b"EDGEAUDIO"
    assert degradations, "降级必须留痕（record_tts_degradation）"
