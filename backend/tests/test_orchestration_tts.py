import os
from pathlib import Path

import pytest

from app.gateway.video_gen.orchestration import tts as tts_mod
from app.gateway.video_gen.orchestration.tts import TTSResult


def _ts_adapter(fn):
    """T10 桩适配：把既有 synthesize_sse 桩包成 with_timestamps 形态 (bytes, [])。"""

    async def _wrapped(self, *a, **kw):
        return await fn(self, *a, **kw), []

    return _wrapped


class _FakeCommunicate:
    def __init__(self, text, voice, rate="+0%"):
        self.text = text

    async def stream(self):
        yield {"type": "audio", "data": b"ID3FAKEAUDIO"}
        yield {"type": "WordBoundary", "offset": 0, "duration": 30_000_000, "text": "你好"}


@pytest.mark.asyncio
async def test_synthesize_writes_audio_and_srt(tmp_path, monkeypatch):
    monkeypatch.setattr(tts_mod.edge_tts, "Communicate", _FakeCommunicate)
    out = tmp_path / "frame0.mp3"
    result = await tts_mod.synthesize("你好", voice_id="zh-CN-YunjianNeural", speed=1.0, output_path=str(out))
    assert out.exists()
    assert out.read_bytes() == b"ID3FAKEAUDIO"
    assert result.duration == pytest.approx(3.0, abs=0.1)
    assert "你好" in result.srt
    assert result.audio_path == str(out)


def test_speed_to_rate():
    assert tts_mod.speed_to_rate(1.0) == "+0%"
    assert tts_mod.speed_to_rate(1.5) == "+50%"
    assert tts_mod.speed_to_rate(0.8) == "-20%"


@pytest.mark.skipif(os.getenv("RUN_TTS_LIVE") != "1", reason="needs network")
@pytest.mark.asyncio
async def test_synthesize_live(tmp_path):
    out = tmp_path / "live.mp3"
    r = await tts_mod.synthesize("你好，这是一个测试。", output_path=str(out))
    assert out.stat().st_size > 1000
    assert r.duration > 0.5


# ---------------------------------------------------------------------------
# Stage 2a: doubao seed-tts-2.0 routing + fallback chain.
# - "doubao:X" voice_id → _synthesize_doubao (not edge/qihu).
# - Infra errors (5xx/transport) → fallback to qihu(360) → edge.
# - Config/auth errors (speaker-mismatch 55000000, 4xx) → user-visible, NO fallback
#   (requirement #4: only 429/5xx/timeout/conn degrade; 360 has different speakers anyway).
# See backend/docs/BYTEDANCE_MODELS_INTEGRATION_PLAN.md §阶段 2a.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_synthesize_routes_doubao_prefix_to_doubao(tmp_path, monkeypatch):
    """voice_id 'doubao:X' routes to _synthesize_doubao (not edge/qihu)."""
    called = {}

    async def fake_doubao(text, voice_id, speed, output_path, **kw):
        called["voice_id"] = voice_id
        return TTSResult(audio_path=output_path, srt="", duration=1.0)

    monkeypatch.setattr(tts_mod, "_synthesize_doubao", fake_doubao)
    out = tmp_path / "f.mp3"
    await tts_mod.synthesize("hi", voice_id="doubao:spk1", speed=1.0, output_path=str(out))
    assert called["voice_id"] == "doubao:spk1"


@pytest.mark.asyncio
async def test_synthesize_doubao_falls_back_to_qihu_on_5xx(tmp_path, monkeypatch):
    """doubao HTTP 5xx (infra) → fallback to qihu(360)."""
    from app.gateway.voice_twin.doubao_tts_client import DoubaoTTSHTTPError

    async def boom(*a, **kw):
        raise DoubaoTTSHTTPError(503, "down")

    monkeypatch.setattr("app.gateway.voice_twin.doubao_tts_client.DoubaoTTSClient.synthesize_sse", boom)
    monkeypatch.setattr("app.gateway.voice_twin.doubao_tts_client.DoubaoTTSClient.synthesize_sse_with_timestamps", _ts_adapter(boom))
    monkeypatch.setenv("VOLCENGINE_VOICE_ACCESS_KEY", "k")
    # wait_fixed(2) → 0，避免 3 次重试真睡 4s（patch 装饰后函数绑定的 retry 状态）。
    import tenacity

    monkeypatch.setattr(tts_mod._call_doubao_with_retry.retry, "wait", tenacity.wait_none())

    async def fake_qihu(text, voice_id, speed, output_path):
        Path(output_path).write_bytes(b"QIHUAUDIO")
        return TTSResult(audio_path=output_path, srt="", duration=2.0)

    monkeypatch.setattr(tts_mod, "_synthesize_qihu", fake_qihu)
    out = tmp_path / "f.mp3"
    r = await tts_mod._synthesize_doubao("hi", "doubao:spk1", 1.0, str(out))
    assert out.read_bytes() == b"QIHUAUDIO"  # fell back to qihu
    assert r.duration == 2.0


@pytest.mark.asyncio
async def test_synthesize_doubao_falls_back_to_qihu_on_transport_error(tmp_path, monkeypatch):
    """doubao transport error (ConnectError) → fallback to qihu(360)."""
    import httpx

    async def boom(*a, **kw):
        raise httpx.ConnectError("no route")

    monkeypatch.setattr("app.gateway.voice_twin.doubao_tts_client.DoubaoTTSClient.synthesize_sse", boom)
    monkeypatch.setattr("app.gateway.voice_twin.doubao_tts_client.DoubaoTTSClient.synthesize_sse_with_timestamps", _ts_adapter(boom))
    monkeypatch.setenv("VOLCENGINE_VOICE_ACCESS_KEY", "k")
    # wait_fixed(2) → 0，避免 3 次重试真睡 4s（patch 装饰后函数绑定的 retry 状态）。
    import tenacity

    monkeypatch.setattr(tts_mod._call_doubao_with_retry.retry, "wait", tenacity.wait_none())

    async def fake_qihu(text, voice_id, speed, output_path):
        Path(output_path).write_bytes(b"QIHUAUDIO")
        return TTSResult(audio_path=output_path, srt="", duration=2.0)

    monkeypatch.setattr(tts_mod, "_synthesize_qihu", fake_qihu)
    out = tmp_path / "f.mp3"
    await tts_mod._synthesize_doubao("hi", "doubao:spk1", 1.0, str(out))
    assert out.read_bytes() == b"QIHUAUDIO"


@pytest.mark.asyncio
async def test_synthesize_doubao_surfaces_speaker_mismatch_no_fallback(tmp_path, monkeypatch):
    """doubao speaker-mismatch (code 55000000 RuntimeError) → user-visible error, NO fallback."""

    async def boom(*a, **kw):
        raise RuntimeError("doubao TTS error (code=55000000): resource ID mismatched with speaker")

    monkeypatch.setattr("app.gateway.voice_twin.doubao_tts_client.DoubaoTTSClient.synthesize_sse", boom)
    monkeypatch.setattr("app.gateway.voice_twin.doubao_tts_client.DoubaoTTSClient.synthesize_sse_with_timestamps", _ts_adapter(boom))
    monkeypatch.setenv("VOLCENGINE_VOICE_ACCESS_KEY", "k")

    qihu_called = []

    async def fake_qihu(*a, **kw):
        qihu_called.append(True)
        return TTSResult(audio_path="", srt="", duration=0)

    monkeypatch.setattr(tts_mod, "_synthesize_qihu", fake_qihu)
    out = tmp_path / "f.mp3"
    with pytest.raises(RuntimeError, match="55000000"):
        await tts_mod._synthesize_doubao("hi", "doubao:spk1", 1.0, str(out))
    assert qihu_called == []  # NO fallback — config error, not infra


@pytest.mark.asyncio
async def test_synthesize_doubao_writes_audio_and_duration(tmp_path, monkeypatch):
    """doubao success → writes mp3 bytes + duration via ffprobe (srt empty)."""
    monkeypatch.setenv("VOLCENGINE_VOICE_ACCESS_KEY", "k")

    async def fake_synth(*a, **kw):
        return b"DOUBAOAUDIO"

    monkeypatch.setattr("app.gateway.voice_twin.doubao_tts_client.DoubaoTTSClient.synthesize_sse", fake_synth)
    monkeypatch.setattr("app.gateway.voice_twin.doubao_tts_client.DoubaoTTSClient.synthesize_sse_with_timestamps", _ts_adapter(fake_synth))

    async def fake_dur(path):
        return 3.5

    monkeypatch.setattr(tts_mod, "_get_audio_duration", lambda p: 3.5)
    out = tmp_path / "f.mp3"
    r = await tts_mod._synthesize_doubao("hi", "doubao:spk1", 1.0, str(out))
    assert out.read_bytes() == b"DOUBAOAUDIO"
    assert r.srt == ""
    assert r.duration == 3.5


@pytest.mark.asyncio
async def test_synthesize_doubao_falls_back_on_transient_runtime_error(tmp_path, monkeypatch):
    """A1: doubao 瞬时/配额 RuntimeError（非 55000000 音色不匹配）→ 降级 360，不搞挂整段音频。

    日志实证 doubao `45000292 quota exceeded` / `45002001 No readable text` 等会以
    RuntimeError 抛出；修前一律 raise → asyncio.gather 整批崩 → audio_error。
    """
    from app.gateway.voice_twin.doubao_tts_client import DoubaoTTSClient

    async def boom(*a, **kw):
        raise RuntimeError("doubao TTS session failed (code=45000292): quota exceeded")

    monkeypatch.setattr(DoubaoTTSClient, "synthesize_sse", boom)
    monkeypatch.setattr(DoubaoTTSClient, "synthesize_sse_with_timestamps", _ts_adapter(boom))
    monkeypatch.setenv("VOLCENGINE_VOICE_ACCESS_KEY", "k")

    qihu_called = []

    async def fake_qihu(text, vid, speed, out):
        qihu_called.append((text, vid))
        Path(out).write_bytes(b"mp3")
        return TTSResult(audio_path=str(out), srt="", duration=2.0)

    monkeypatch.setattr(tts_mod, "_synthesize_qihu", fake_qihu)
    out = tmp_path / "f.mp3"
    r = await tts_mod._synthesize_doubao("hi", "doubao:spk1", 1.0, str(out))
    assert qihu_called, "非音色不匹配的 RuntimeError 应降级 360，而非 raise"
    assert qihu_called[0][1] == "preset:146"
    assert str(out) == r.audio_path


@pytest.mark.asyncio
async def test_doubao_failure_falls_back_to_configured_voice(monkeypatch, tmp_path):
    """doubao 瞬时失败 → 降级到调用方指定的 fallback_voice（曾硬编码 preset:146 无视配置）。"""
    from app.gateway.video_gen.orchestration import tts as tts_mod

    monkeypatch.setenv("VOLCENGINE_VOICE_ACCESS_KEY", "k")
    calls: list[str] = []

    async def fake_qihu(text, voice_id, speed, output_path):
        calls.append(voice_id)
        return tts_mod.TTSResult(audio_path=output_path, srt="", duration=1.0)

    monkeypatch.setattr(tts_mod, "_synthesize_qihu", fake_qihu)

    from app.gateway.voice_twin.doubao_tts_client import DoubaoTTSClient

    async def boom(*a, **k):
        raise RuntimeError("45000292 quota exceeded")

    monkeypatch.setattr(DoubaoTTSClient, "synthesize_sse", boom)
    monkeypatch.setattr(DoubaoTTSClient, "synthesize_sse_with_timestamps", _ts_adapter(boom))

    await tts_mod._synthesize_doubao("你好", "doubao:x", 1.0, str(tmp_path / "o.mp3"), fallback_voice="preset:158")
    assert calls == ["preset:158"]


def test_wav_to_mp3_raises_when_output_missing(monkeypatch, tmp_path):
    """returncode=0 但产物缺失（异常 ffmpeg/磁盘满）同样 raise（07-27 缩减：rc≠0 raise 已由 4b915c180 落地）。"""
    import subprocess as sp

    from app.gateway.video_gen.orchestration import tts as tts_mod

    wav = tmp_path / "a.wav"
    wav.write_bytes(b"RIFFfake")
    monkeypatch.setattr(
        sp,
        "run",
        lambda *a, **k: sp.CompletedProcess(args=a[0], returncode=0, stderr=b""),
    )
    with pytest.raises(RuntimeError, match="output missing"):
        tts_mod._wav_to_mp3(str(wav), str(tmp_path / "a.mp3"))


# ---------------------------------------------------------------------------
# Task 5 (P1#8/#9): edge 回退性别感知 + 可禁用开关。
# 360 失败回退曾硬编码 edge 男声 YunjianNeural（国内死链 + 女声 preset 静默变男声）；
# 国内仅可达部署应能设 TTS_DISABLE_EDGE_FALLBACK=1 直接 fail-loud。
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_qihu_fallback_edge_voice_matches_gender(monkeypatch, tmp_path):
    """女声 preset 360 失败 → edge 回退必须仍是女声（曾硬编码 Yunjian 男声）。"""
    from app.gateway.video_gen.orchestration import tts as tts_mod

    called: dict = {}

    async def fake_edge(text, voice_id, speed, output_path):
        called["voice_id"] = voice_id
        return tts_mod.TTSResult(audio_path=output_path, srt="", duration=1.0)

    monkeypatch.setattr(tts_mod, "_synthesize_edge", fake_edge)
    monkeypatch.setenv("QIHU_API_KEY", "")  # 无 key → 直接走回退分支
    await tts_mod._synthesize_qihu("你好", "preset:146", 1.0, str(tmp_path / "o.mp3"))
    assert called["voice_id"] == "zh-CN-XiaoxiaoNeural"


@pytest.mark.asyncio
async def test_edge_fallback_disabled_raises(monkeypatch, tmp_path):
    """TTS_DISABLE_EDGE_FALLBACK=1（国内服务器推荐）→ 360 无 key 直接 raise，不死链回退。"""
    from app.gateway.video_gen.orchestration import tts as tts_mod

    monkeypatch.setenv("QIHU_API_KEY", "")
    monkeypatch.setenv("TTS_DISABLE_EDGE_FALLBACK", "1")
    with pytest.raises(RuntimeError, match="edge fallback disabled"):
        await tts_mod._synthesize_qihu("你好", "preset:146", 1.0, str(tmp_path / "o.mp3"))


# ---------------------------------------------------------------------------
# Task 17: preset:abc 校验 + edge 传输错误重试。
# ---------------------------------------------------------------------------


def test_preset_voice_id_validation():
    """preset:abc / preset: → ValueError 带清晰消息（曾 ValueError 裸抛 500）。"""
    with pytest.raises(ValueError, match="invalid preset voice_id"):
        tts_mod._parse_preset_id("preset:abc")
    with pytest.raises(ValueError, match="invalid preset voice_id"):
        tts_mod._parse_preset_id("preset:")
    assert tts_mod._parse_preset_id("preset:146") == 146


@pytest.mark.asyncio
async def test_edge_synthesis_retries_transport_errors(tmp_path, monkeypatch):
    """edge-tts 传输错误按 _RETRYABLE_TTS_ERRORS 策略重试（与 360 同款 tenacity），最终成功。"""
    import httpx
    import tenacity

    attempts = {"n": 0}

    class _FlakyCommunicate:
        def __init__(self, text, voice, rate="+0%"):
            attempts["n"] += 1

        async def stream(self):
            if attempts["n"] < 3:
                raise httpx.ConnectError("flaky")
            yield {"type": "audio", "data": b"EDGEAUDIO"}
            yield {"type": "WordBoundary", "offset": 0, "duration": 20_000_000, "text": "你好"}

    monkeypatch.setattr(tts_mod.edge_tts, "Communicate", _FlakyCommunicate)
    # wait_fixed(2) → 0，避免测试真睡 4s。
    monkeypatch.setattr(tts_mod._call_edge_with_retry.retry, "wait", tenacity.wait_none())
    out = tmp_path / "edge.mp3"
    result = await tts_mod._synthesize_edge("你好", "zh-CN-YunjianNeural", 1.0, str(out))
    assert attempts["n"] == 3
    assert out.read_bytes() == b"EDGEAUDIO"
    assert result.duration == pytest.approx(2.0, abs=0.1)


# ---------------------------------------------------------------------------
# C4: edge-tts 显式超时 + asyncio.TimeoutError 纳入可重试。
# 修前 _call_edge_with_retry 裸调 communicate.stream()，无显式超时——edge 服务端
# 半挂（TCP 活但零数据）会无限阻塞，tenacity 无法捕获（不是 transport error）。
# ---------------------------------------------------------------------------


def test_timeout_error_is_retryable():
    """C4: asyncio.TimeoutError 必须在 _RETRYABLE_TTS_ERRORS 中，否则超时不会被重试。"""
    import asyncio

    assert asyncio.TimeoutError in tts_mod._RETRYABLE_TTS_ERRORS


@pytest.mark.asyncio
async def test_edge_hanging_stream_times_out(tmp_path, monkeypatch):
    """C4: edge stream 挂起 → asyncio.TimeoutError 在超时后触发，被 tenacity 重试。

    用 TTS_EDGE_TIMEOUT=0.01 把超时压到 10ms，避免真等 60s。第一次挂起、第二次正常。
    """
    import tenacity

    monkeypatch.setenv("TTS_EDGE_TIMEOUT", "0.01")
    monkeypatch.setattr(tts_mod._call_edge_with_retry.retry, "wait", tenacity.wait_none())

    attempts = {"n": 0}

    class _HangingThenOk:
        def __init__(self, text, voice, rate="+0%"):
            attempts["n"] += 1

        async def stream(self):
            if attempts["n"] < 2:
                # Simulate a hanging stream that never yields data
                await asyncio.sleep(10)
            yield {"type": "audio", "data": b"OK"}
            yield {"type": "WordBoundary", "offset": 0, "duration": 10_000_000, "text": "hi"}

    import asyncio

    monkeypatch.setattr(tts_mod.edge_tts, "Communicate", _HangingThenOk)
    out = tmp_path / "edge_timeout.mp3"
    await tts_mod._synthesize_edge("你好", "zh-CN-YunjianNeural", 1.0, str(out))
    assert attempts["n"] == 2, "超时后应被 tenacity 重试"
    assert out.read_bytes() == b"OK"


@pytest.mark.asyncio
async def test_edge_normal_stream_unaffected_by_timeout(tmp_path, monkeypatch):
    """C4: 正常快速流不受超时影响（回归保护）。"""
    monkeypatch.setattr(tts_mod.edge_tts, "Communicate", _FakeCommunicate)
    monkeypatch.setenv("TTS_EDGE_TIMEOUT", "60")
    out = tmp_path / "edge_normal.mp3"
    result = await tts_mod._synthesize_edge("你好", "zh-CN-YunjianNeural", 1.0, str(out))
    assert out.read_bytes() == b"ID3FAKEAUDIO"
    assert result.duration == pytest.approx(3.0, abs=0.1)
