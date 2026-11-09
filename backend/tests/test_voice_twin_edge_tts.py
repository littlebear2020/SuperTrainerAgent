"""Tests for A2: edge-tts integration in the voice-twin module (2026-08-09).

覆盖：EdgeTTSClient 合成/重试、_do_tts_synthesis edge 分支、
降级链（doubao→qihu→edge / preset→edge）、克隆音色不降级、
preview/_verify_voice 的 edge 识别。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.gateway._shared.task_framework.models import TaskRow
from app.gateway._shared.task_framework.repository import TaskRepository
from app.gateway.voice_twin.doubao_tts_client import DoubaoTTSHTTPError
from app.gateway.voice_twin.edge_tts_client import EdgeTTSClient, speed_to_rate
from app.gateway.voice_twin.production_service import (
    ProductionService,
    _edge_fallback_voice_for,
    _is_degradable_tts_error,
    _looks_like_mp3,
)
from app.gateway.voice_twin.qihu_tts_client import QihuTTSHTTPError
from app.gateway.voice_twin.volcengine_client import VolcengineTTSHTTPError


def _fake_communicate(chunks: list[bytes] | None = None, errors: list[Exception] | None = None):
    """Build a fake edge_tts.Communicate class.

    ``errors`` are raised (one per call, in order) before ``chunks`` stream
    succeeds — for tenacity retry tests.
    """
    state = {"calls": 0}
    errs = list(errors or [])
    payload = chunks if chunks is not None else [b"mp3-audio"]

    class _Fake:
        def __init__(self, text, voice, rate="+0%"):
            state["calls"] += 1
            self._err = errs.pop(0) if errs else None

        async def stream(self):
            if self._err is not None:
                raise self._err
            for c in payload:
                yield {"type": "audio", "data": c}

    return _Fake, state


def _install_fast_retry(monkeypatch) -> None:
    """把 _call_with_retry 的 tenacity wait 换成 0s——装饰器在 import 期定型，
    patch wait_fixed 无效；用 __wrapped__ 取原函数按同策略重包。"""
    import tenacity

    from app.gateway.voice_twin import edge_tts_client as etc

    raw = etc.EdgeTTSClient._call_with_retry.__wrapped__
    fast = tenacity.retry(
        retry=tenacity.retry_if_exception_type(etc._RETRYABLE_EDGE_ERRORS),
        wait=tenacity.wait_fixed(0),
        stop=tenacity.stop_after_attempt(3),
        reraise=True,
    )(raw)
    monkeypatch.setattr(etc.EdgeTTSClient, "_call_with_retry", fast)


class TestEdgeTTSClient:
    @pytest.mark.asyncio
    async def test_synthesize_returns_audio_bytes(self):
        fake, state = _fake_communicate([b"chunk1", b"chunk2"])
        with patch("app.gateway.voice_twin.edge_tts_client.edge_tts") as mock_edge:
            mock_edge.Communicate = fake
            client = EdgeTTSClient(timeout=5.0)
            audio = await client.synthesize(text="你好", voice="zh-CN-XiaoxiaoNeural")
        assert audio == b"chunk1chunk2"
        assert state["calls"] == 1

    @pytest.mark.asyncio
    async def test_retries_transport_error_then_succeeds(self, monkeypatch):
        _install_fast_retry(monkeypatch)
        fake, state = _fake_communicate(errors=[httpx.ConnectError("reset")])
        with patch("app.gateway.voice_twin.edge_tts_client.edge_tts") as mock_edge:
            mock_edge.Communicate = fake
            client = EdgeTTSClient(timeout=5.0)
            audio = await client.synthesize(text="hi", voice="en-US-AriaNeural")
        assert audio == b"mp3-audio"
        assert state["calls"] == 2

    @pytest.mark.asyncio
    async def test_exhausts_retries_on_persistent_transport_error(self, monkeypatch):
        _install_fast_retry(monkeypatch)
        errs = [httpx.ReadTimeout("t1"), httpx.ReadTimeout("t2"), httpx.ReadTimeout("t3")]
        fake, state = _fake_communicate(errors=errs)
        with patch("app.gateway.voice_twin.edge_tts_client.edge_tts") as mock_edge:
            mock_edge.Communicate = fake
            client = EdgeTTSClient(timeout=5.0)
            with pytest.raises(httpx.ReadTimeout):
                await client.synthesize(text="hi", voice="en-US-AriaNeural")
        assert state["calls"] == 3

    @pytest.mark.asyncio
    async def test_empty_audio_raises(self):
        fake, _ = _fake_communicate(chunks=[])
        with patch("app.gateway.voice_twin.edge_tts_client.edge_tts") as mock_edge:
            mock_edge.Communicate = fake
            client = EdgeTTSClient(timeout=5.0)
            with pytest.raises(RuntimeError, match="no audio"):
                await client.synthesize(text="hi", voice="en-US-AriaNeural")

    @pytest.mark.asyncio
    async def test_retries_aiohttp_error_then_succeeds(self, monkeypatch):
        """edge-tts 底层是 aiohttp（communicate.py:461）——aiohttp.ClientError 必须可重试（fix #2）。"""
        import aiohttp

        _install_fast_retry(monkeypatch)
        fake, state = _fake_communicate(errors=[aiohttp.ClientError("connection reset")])
        with patch("app.gateway.voice_twin.edge_tts_client.edge_tts") as mock_edge:
            mock_edge.Communicate = fake
            client = EdgeTTSClient(timeout=5.0)
            audio = await client.synthesize(text="hi", voice="en-US-AriaNeural")
        assert audio == b"mp3-audio"
        assert state["calls"] == 2  # 1 fail + 1 success

    @pytest.mark.asyncio
    async def test_exhausts_retries_on_persistent_aiohttp_error(self, monkeypatch):
        """aiohttp.ClientError 持续失败 → tenacity 3 次后放弃（fix #2）。"""
        import aiohttp

        _install_fast_retry(monkeypatch)
        errs = [aiohttp.ClientError("e1"), aiohttp.ClientError("e2"), aiohttp.ClientError("e3")]
        fake, state = _fake_communicate(errors=errs)
        with patch("app.gateway.voice_twin.edge_tts_client.edge_tts") as mock_edge:
            mock_edge.Communicate = fake
            client = EdgeTTSClient(timeout=5.0)
            with pytest.raises(aiohttp.ClientError):
                await client.synthesize(text="hi", voice="en-US-AriaNeural")
        assert state["calls"] == 3

    def test_speed_to_rate(self):
        assert speed_to_rate(1.0) == "+0%"
        assert speed_to_rate(1.2) == "+20%"
        assert speed_to_rate(0.8) == "-20%"


# ---------------------------------------------------------------------------
# ProductionService edge branch + degradation chain
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="function")
async def svc(tmp_path):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(TaskRow.metadata.create_all)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    repo = TaskRepository(session_factory=sf)
    voice_svc = MagicMock()
    voice_svc.repo = MagicMock()
    voice_svc.qihu_client = None
    voice_svc.doubao_client = None
    voice_svc.edge_client = None
    service = ProductionService(repo=repo, voice_service=voice_svc, storage_dir=str(tmp_path / "vt"))
    service._TTS_RETRY_BASE_DELAY = 0.001
    yield service
    await engine.dispose()


class TestEdgeBranch:
    @pytest.mark.asyncio
    async def test_edge_voice_routes_to_edge_client(self, svc: ProductionService):
        edge = MagicMock()
        edge.synthesize = AsyncMock(return_value=b"edge_mp3")
        svc.voice_service.edge_client = edge

        result = await svc._do_tts_synthesis("edge:zh-CN-XiaoxiaoNeural", "你好")
        assert result == b"edge_mp3"
        edge.synthesize.assert_awaited_once()
        kwargs = edge.synthesize.call_args.kwargs
        assert kwargs["voice"] == "zh-CN-XiaoxiaoNeural"  # 剥掉 edge: 前缀

    @pytest.mark.asyncio
    async def test_edge_voice_unconfigured_raises(self, svc: ProductionService):
        """edge: 音色但 edge_client 缺失 → 直接抛错（edge 是链末端，无更下级兜底）。"""
        with pytest.raises(RuntimeError, match="edge TTS service not configured"):
            await svc._do_tts_synthesis("edge:zh-CN-XiaoxiaoNeural", "你好")

    @pytest.mark.asyncio
    async def test_edge_voice_skips_outer_retry(self, svc: ProductionService):
        """edge voice 直接调 edge_client.synthesize，不经过 call_with_tts_retry（fix #4）。

        若走外层包装会 3×3=9 次重试（call_with_tts_retry × tenacity）。
        edge client 内部 tenacity 已足够——验证外层不被调用。
        """
        edge = MagicMock()
        edge.synthesize = AsyncMock(return_value=b"edge_mp3")
        svc.voice_service.edge_client = edge

        from app.gateway.voice_twin import _retry as retry_mod

        with patch.object(retry_mod, "call_with_tts_retry", wraps=retry_mod.call_with_tts_retry) as spy:
            result = await svc._synthesize_with_retry("edge:zh-CN-XiaoxiaoNeural", "你好")

        assert result == b"edge_mp3"
        spy.assert_not_called()  # edge path bypasses call_with_tts_retry
        edge.synthesize.assert_awaited_once()


class TestDegradationChain:
    @pytest.mark.asyncio
    async def test_preset_5xx_degrades_to_edge(self, svc: ProductionService):
        """付费引擎全挂 → edge 兜底出声（A2 核心场景）。"""
        qihu = MagicMock()
        qihu.synthesize_sse = AsyncMock(side_effect=QihuTTSHTTPError(500, "boom"))
        edge = MagicMock()
        edge.synthesize = AsyncMock(return_value=b"edge_fallback_mp3")
        svc.voice_service.qihu_client = qihu
        svc.voice_service.edge_client = edge

        result = await svc._do_tts_synthesis("preset:146", "你好")
        assert result == b"edge_fallback_mp3"
        # preset:146 是目录内女声 → edge 兜底选同性别中文女声
        assert edge.synthesize.call_args.kwargs["voice"] == "zh-CN-XiaoxiaoNeural"

    @pytest.mark.asyncio
    async def test_doubao_degrades_qihu_then_edge(self, svc: ProductionService):
        """doubao→qihu→edge 完整链：两者皆 5xx 时落 edge。"""
        doubao = MagicMock()
        doubao.synthesize_sse = AsyncMock(side_effect=DoubaoTTSHTTPError(503, "down"))
        qihu = MagicMock()
        qihu.synthesize_sse = AsyncMock(side_effect=QihuTTSHTTPError(500, "down"))
        edge = MagicMock()
        edge.synthesize = AsyncMock(return_value=b"edge_mp3")
        svc.voice_service.doubao_client = doubao
        svc.voice_service.qihu_client = qihu
        svc.voice_service.edge_client = edge

        result = await svc._do_tts_synthesis("doubao:zh_male_m191_uranus_bigtts", "你好")
        assert result == b"edge_mp3"
        # 链中 qihu 用 preset:146 兜底音色
        assert qihu.synthesize_sse.call_args.kwargs["voice"] == 146
        # 云舟2.0 是男声 → edge 男声兜底
        assert edge.synthesize.call_args.kwargs["voice"] == "zh-CN-YunjianNeural"

    @pytest.mark.asyncio
    async def test_doubao_degrades_to_qihu_success(self, svc: ProductionService):
        """doubao 挂、qihu 正常 → 落 qihu 不到 edge。"""
        doubao = MagicMock()
        doubao.synthesize_sse = AsyncMock(side_effect=DoubaoTTSHTTPError(502, "bad gateway"))
        qihu = MagicMock()
        qihu.synthesize_sse = AsyncMock(return_value=b"RIFFwav")
        svc.voice_service.doubao_client = doubao
        svc.voice_service.qihu_client = qihu

        result = await svc._do_tts_synthesis("doubao:spk_x", "你好")
        assert result == b"RIFFwav"

    @pytest.mark.asyncio
    async def test_4xx_not_degradable(self, svc: ProductionService):
        """401 鉴权错误不降级——掩盖配置问题且换引擎也救不了。"""
        qihu = MagicMock()
        qihu.synthesize_sse = AsyncMock(side_effect=QihuTTSHTTPError(401, "unauthorized"))
        edge = MagicMock()
        edge.synthesize = AsyncMock(return_value=b"edge_mp3")
        svc.voice_service.qihu_client = qihu
        svc.voice_service.edge_client = edge

        with pytest.raises(RuntimeError, match="401"):
            await svc._do_tts_synthesis("preset:146", "你好")
        edge.synthesize.assert_not_called()

    @pytest.mark.asyncio
    async def test_cloned_voice_never_degrades(self, svc: ProductionService):
        """克隆音色失败响亮报错，绝不静默换成别人的声音。"""
        client = MagicMock()
        client.synthesize_sse = AsyncMock(side_effect=VolcengineTTSHTTPError(500, "boom"))
        edge = MagicMock()
        edge.synthesize = AsyncMock(return_value=b"edge_mp3")
        svc.voice_service.client = client
        svc.voice_service.edge_client = edge
        row = SimpleNamespace(speaker_id="spk_1", model_type=5, language=0)

        with pytest.raises(RuntimeError, match="failed after 3 retries"):
            await svc._do_tts_synthesis("some-clone-id", "你好", row=row)
        edge.synthesize.assert_not_called()

    @pytest.mark.asyncio
    async def test_transport_exhaustion_degrades_via_cause(self, svc: ProductionService):
        """传输错误重试耗尽后，外层包装不含特征——按 __cause__ 分类进降级链。"""
        qihu = MagicMock()
        qihu.synthesize_sse = AsyncMock(side_effect=httpx.ConnectError("refused"))
        edge = MagicMock()
        edge.synthesize = AsyncMock(return_value=b"edge_mp3")
        svc.voice_service.qihu_client = qihu
        svc.voice_service.edge_client = edge

        result = await svc._do_tts_synthesis("preset:146", "你好")
        assert result == b"edge_mp3"


class TestHelpers:
    def test_is_degradable_transport(self):
        assert _is_degradable_tts_error(httpx.ConnectError("x")) is True

    def test_is_degradable_429_5xx(self):
        assert _is_degradable_tts_error(QihuTTSHTTPError(429, "too many")) is True
        assert _is_degradable_tts_error(VolcengineTTSHTTPError(503, "down")) is True
        # SSE 业务码错误（status None）不可降级
        assert _is_degradable_tts_error(QihuTTSHTTPError(None, "biz err", code=1001)) is False

    def test_is_degradable_not_configured(self):
        assert _is_degradable_tts_error(RuntimeError("360 TTS service not configured")) is True

    def test_not_degradable_4xx(self):
        assert _is_degradable_tts_error(QihuTTSHTTPError(401, "unauthorized")) is False
        assert _is_degradable_tts_error(ValueError("bad voice")) is False
        # 裸 RuntimeError 不再靠消息里的 "HTTP 5xx" 文本命中（正则分类已移除）
        assert _is_degradable_tts_error(RuntimeError("HTTP 500 fake")) is False

    def test_degradable_via_cause_chain(self):
        inner = httpx.ReadTimeout("t")
        outer = RuntimeError("TTS synthesis failed after 3 retries")
        outer.__cause__ = inner
        assert _is_degradable_tts_error(outer) is True

    def test_degradable_via_structured_cause(self):
        outer = RuntimeError("TTS synthesis failed after 3 retries")
        outer.__cause__ = QihuTTSHTTPError(500, "boom")
        assert _is_degradable_tts_error(outer) is True

    def test_edge_fallback_voice_gender_match(self):
        assert _edge_fallback_voice_for("preset:146") == "zh-CN-XiaoxiaoNeural"  # 女声
        assert _edge_fallback_voice_for("preset:295") == "zh-CN-YunjianNeural"  # 磁性男嗓

    def test_edge_fallback_voice_locale_match(self):
        """非中文音色降级到同 locale 的 edge 音色。"""
        assert _edge_fallback_voice_for("edge:en-US-AriaNeural") == "en-US-AriaNeural"

    def test_edge_fallback_unknown_voice(self):
        assert _edge_fallback_voice_for("preset:999") == "zh-CN-XiaoxiaoNeural"

    def test_looks_like_mp3(self):
        assert _looks_like_mp3(b"RIFF\x00\x00\x00\x00WAVE") is False
        assert _looks_like_mp3(b"ID3\x04\x00") is True
        assert _looks_like_mp3(b"\xff\xfb\x90\x00") is True


class TestPreviewAndVerify:
    @pytest.mark.asyncio
    async def test_preview_voice_edge_returns_mpeg(self, svc: ProductionService):
        edge = MagicMock()
        edge.synthesize = AsyncMock(return_value=b"edge_mp3")
        svc.voice_service.edge_client = edge

        audio, media = await svc.preview_voice("edge:zh-CN-XiaoxiaoNeural", "你好", "user_a")
        assert audio == b"edge_mp3"
        assert media == "audio/mpeg"

    @pytest.mark.asyncio
    async def test_verify_voice_edge_pseudo(self, svc: ProductionService):
        from app.gateway.routers.voice_twin_production import _verify_voice

        voice = await _verify_voice(svc, "edge:zh-CN-XiaoxiaoNeural", "user_a")
        assert voice.speaker_id == "edge:zh-CN-XiaoxiaoNeural"
        assert voice.status == "ready"
        assert "XiaoxiaoNeural" in voice.voice_name

    @pytest.mark.asyncio
    async def test_preview_doubao_degraded_returns_wav(self, svc: ProductionService):
        """doubao→qihu 降级产物是 WAV——preview media_type 必须是 audio/wav（fix #1）。

        按请求 voice_id 推格式会误标 audio/mpeg（doubao 正常产 mp3），导致
        播放器解码失败 + 7 天坏缓存（doubao 恢复后仍外发 WAV 顶着 mp3 头）。
        """
        doubao = MagicMock()
        doubao.synthesize_sse = AsyncMock(side_effect=DoubaoTTSHTTPError(503, "down"))
        qihu = MagicMock()
        qihu.synthesize_sse = AsyncMock(return_value=b"RIFF\x00\x00\x00\x00WAVEfmt ")
        svc.voice_service.doubao_client = doubao
        svc.voice_service.qihu_client = qihu

        audio, media = await svc.preview_voice("doubao:zh_male_m191_uranus_bigtts", "你好", "user_a")
        assert audio[:4] == b"RIFF"
        assert media == "audio/wav"  # WAV from qihu degradation, not falsely "audio/mpeg"

    @pytest.mark.asyncio
    async def test_preview_preset_returns_wav(self, svc: ProductionService):
        """preset 音色正常返回 WAV——preview media_type 是 audio/wav（fix #1 回归保障）。"""
        qihu = MagicMock()
        qihu.synthesize_sse = AsyncMock(return_value=b"RIFF\x00\x00\x00\x00WAVEfmt ")
        svc.voice_service.qihu_client = qihu

        audio, media = await svc.preview_voice("preset:146", "你好", "user_a")
        assert audio[:4] == b"RIFF"
        assert media == "audio/wav"

    @pytest.mark.asyncio
    async def test_preview_doubao_normal_returns_mpeg(self, svc: ProductionService):
        """doubao 正常返回 mp3——preview media_type 是 audio/mpeg（fix #1 回归保障）。"""
        doubao = MagicMock()
        doubao.synthesize_sse = AsyncMock(return_value=b"\xff\xfb\x90\x00mp3data")
        svc.voice_service.doubao_client = doubao

        audio, media = await svc.preview_voice("doubao:zh_male_m191_uranus_bigtts", "你好", "user_a")
        assert media == "audio/mpeg"
