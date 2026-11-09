"""Task 6 (C1 根因): 豆包 TTS 加重试——不再一抖动就换声。

审计的 C1「同一视频多个声音」是**表象**，根因在此：``_synthesize_doubao`` 里的
``client.synthesize_sse(...)`` 是**裸调，无 tenacity**；而同文件的 360
（``_call_360_with_retry``）和 edge（``_call_edge_with_retry``）**都有**
``@tenacity.retry(stop=stop_after_attempt(3), wait=wait_fixed(2))``。

后果：豆包**首次** 429/5xx/超时 → 立刻 ``return await _synthesize_qihu(...)``
降级 360 → **该帧音色与全片不同** → 用户听到视频中段突然换人。

修法：给豆包补一层与 360/edge 同策略的重试（3 次、固定 2s），重试全败再降级。

关联（同文件同类问题）：access key 缺失时的降级此前只有 warning，无用户可见
信号 —— 补 sidecar 告警通道（degradation 记录）。

注：配额错误（45000292）**保持降级语义不变**。既有测试
``test_synthesize_doubao_falls_back_on_transient_runtime_error`` 与
``test_doubao_failure_falls_back_to_configured_voice`` 明确锁定「配额/并发
RuntimeError → 降级 360」，因为整批 draft audio 走 asyncio.gather，单帧 raise
会拖垮全批。计划提出的「配额不当瞬时错误降级」与该既有契约冲突，故只补重试、
不改配额分类（详见交付说明）。
"""

from pathlib import Path

import httpx
import pytest

from app.gateway.video_gen.orchestration import tts as tts_mod
from app.gateway.video_gen.orchestration.tts import TTSResult


def _ts_adapter(fn):
    """T10 桩适配：把既有 synthesize_sse 桩包成 with_timestamps 形态 (bytes, [])。"""

    async def _wrapped(self, *a, **kw):
        return await fn(self, *a, **kw), []

    return _wrapped


@pytest.mark.asyncio
async def test_doubao_retries_transport_error_before_degrading(tmp_path, monkeypatch):
    """前两次传输错误、第三次成功 → 不降级，且 actual_voice_id 仍是豆包。"""
    from app.gateway.voice_twin.doubao_tts_client import DoubaoTTSClient

    attempts = {"n": 0}

    async def flaky(*a, **kw):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise httpx.ReadTimeout("transient")
        return b"ID3_audio_bytes"

    monkeypatch.setattr(DoubaoTTSClient, "synthesize_sse", flaky)
    monkeypatch.setattr(DoubaoTTSClient, "synthesize_sse_with_timestamps", _ts_adapter(flaky))
    monkeypatch.setenv("VOLCENGINE_VOICE_ACCESS_KEY", "k")
    # wait_fixed(2) 在模块 import 时已绑进 Retrying 对象——patch wait_fixed 工厂无效，
    # 必须直接改装饰后函数的 retry 状态（同 test_orchestration_tts.py 的 edge 用例）。
    monkeypatch.setattr(tts_mod._call_doubao_with_retry.retry, "wait", tts_mod.tenacity.wait_none())

    degraded = []

    async def fake_qihu(text, vid, speed, out):
        degraded.append(vid)
        return TTSResult(audio_path=str(out), srt="", duration=1.0)

    monkeypatch.setattr(tts_mod, "_synthesize_qihu", fake_qihu)

    async def fake_dur(*a, **k):
        return 2.0

    monkeypatch.setattr(tts_mod, "run_ffmpeg", fake_dur)

    out = tmp_path / "f.mp3"
    result = await tts_mod._synthesize_doubao("你好", "doubao:spk1", 1.0, str(out))

    assert attempts["n"] == 3, f"应重试到第 3 次才成功，实际调用 {attempts['n']} 次"
    assert not degraded, "重试成功后不得降级 360（否则该帧音色与全片不同）"
    assert result.actual_voice_id == "doubao:spk1", "音色应仍是豆包"


@pytest.mark.asyncio
async def test_doubao_degrades_only_after_retries_exhausted(tmp_path, monkeypatch):
    """持续传输错误 → 重试 3 次后才降级 360。"""
    from app.gateway.voice_twin.doubao_tts_client import DoubaoTTSClient

    attempts = {"n": 0}

    async def always_fail(*a, **kw):
        attempts["n"] += 1
        raise httpx.ReadTimeout("down")

    monkeypatch.setattr(DoubaoTTSClient, "synthesize_sse", always_fail)
    monkeypatch.setattr(DoubaoTTSClient, "synthesize_sse_with_timestamps", _ts_adapter(always_fail))
    monkeypatch.setenv("VOLCENGINE_VOICE_ACCESS_KEY", "k")
    # wait_fixed(2) 在模块 import 时已绑进 Retrying 对象——patch wait_fixed 工厂无效，
    # 必须直接改装饰后函数的 retry 状态（同 test_orchestration_tts.py 的 edge 用例）。
    monkeypatch.setattr(tts_mod._call_doubao_with_retry.retry, "wait", tts_mod.tenacity.wait_none())

    degraded = []

    async def fake_qihu(text, vid, speed, out):
        degraded.append(vid)
        Path(out).write_bytes(b"mp3")
        return TTSResult(audio_path=str(out), srt="", duration=1.0)

    monkeypatch.setattr(tts_mod, "_synthesize_qihu", fake_qihu)

    out = tmp_path / "f.mp3"
    await tts_mod._synthesize_doubao("你好", "doubao:spk1", 1.0, str(out))

    assert attempts["n"] == 3, f"应重试满 3 次，实际 {attempts['n']} 次"
    assert degraded, "重试耗尽后应降级 360"


@pytest.mark.asyncio
async def test_doubao_retries_5xx_before_degrading(tmp_path, monkeypatch):
    """5xx 同样先重试（供应商瞬时 500 不该立刻换声）。"""
    from app.gateway.voice_twin.doubao_tts_client import (
        DoubaoTTSClient,
        DoubaoTTSHTTPError,
    )

    attempts = {"n": 0}

    async def flaky(*a, **kw):
        attempts["n"] += 1
        if attempts["n"] < 2:
            raise DoubaoTTSHTTPError(500, "server error")
        return b"audio"

    monkeypatch.setattr(DoubaoTTSClient, "synthesize_sse", flaky)
    monkeypatch.setattr(DoubaoTTSClient, "synthesize_sse_with_timestamps", _ts_adapter(flaky))
    monkeypatch.setenv("VOLCENGINE_VOICE_ACCESS_KEY", "k")
    # wait_fixed(2) 在模块 import 时已绑进 Retrying 对象——patch wait_fixed 工厂无效，
    # 必须直接改装饰后函数的 retry 状态（同 test_orchestration_tts.py 的 edge 用例）。
    monkeypatch.setattr(tts_mod._call_doubao_with_retry.retry, "wait", tts_mod.tenacity.wait_none())

    degraded = []

    async def fake_qihu(text, vid, speed, out):
        degraded.append(vid)
        return TTSResult(audio_path=str(out), srt="", duration=1.0)

    monkeypatch.setattr(tts_mod, "_synthesize_qihu", fake_qihu)

    async def fake_dur(*a, **k):
        return 2.0

    monkeypatch.setattr(tts_mod, "run_ffmpeg", fake_dur)

    out = tmp_path / "f.mp3"
    result = await tts_mod._synthesize_doubao("你好", "doubao:spk1", 1.0, str(out))

    assert attempts["n"] == 2, f"5xx 应重试，实际调用 {attempts['n']} 次"
    assert not degraded, "重试成功后不得降级"
    assert result.actual_voice_id == "doubao:spk1"


@pytest.mark.asyncio
async def test_doubao_4xx_still_raises_without_retry(tmp_path, monkeypatch):
    """4xx 鉴权/配置错不可重试也不降级（既有契约，不得破坏）。"""
    from app.gateway.voice_twin.doubao_tts_client import (
        DoubaoTTSClient,
        DoubaoTTSHTTPError,
    )

    attempts = {"n": 0}

    async def bad_auth(*a, **kw):
        attempts["n"] += 1
        raise DoubaoTTSHTTPError(401, "unauthorized")

    monkeypatch.setattr(DoubaoTTSClient, "synthesize_sse", bad_auth)
    monkeypatch.setattr(DoubaoTTSClient, "synthesize_sse_with_timestamps", _ts_adapter(bad_auth))
    monkeypatch.setenv("VOLCENGINE_VOICE_ACCESS_KEY", "k")

    with pytest.raises(DoubaoTTSHTTPError):
        await tts_mod._synthesize_doubao("你好", "doubao:spk1", 1.0, str(tmp_path / "f.mp3"))

    assert attempts["n"] == 1, "4xx 不该重试"


@pytest.mark.asyncio
async def test_missing_access_key_records_degradation(tmp_path, monkeypatch):
    """access key 缺失导致的全帧降级必须留痕（不能只有一条 warning）。"""
    monkeypatch.delenv("VOLCENGINE_VOICE_ACCESS_KEY", raising=False)

    async def fake_qihu(text, vid, speed, out):
        return TTSResult(audio_path=str(out), srt="", duration=1.0)

    monkeypatch.setattr(tts_mod, "_synthesize_qihu", fake_qihu)

    recorded = []
    monkeypatch.setattr(
        tts_mod,
        "record_tts_degradation",
        lambda **kw: recorded.append(kw),
        raising=False,
    )

    await tts_mod._synthesize_doubao("你好", "doubao:spk1", 1.0, str(tmp_path / "f.mp3"))

    assert recorded, "access key 缺失→全片换声，必须写降级记录供前端告警"
    assert recorded[0].get("kind") == "tts_access_key_missing"
