"""C3: record_tts_degradation → degradations sidecar 接线测试。"""

import json

import pytest


def test_record_tts_degradation_writes_sidecar(tmp_path, monkeypatch):
    """有 task_dir 上下文时，record_tts_degradation 写入 degradations.json。"""
    from app.gateway.video_gen.degradations import DEGRADATION_SIDECAR_NAME
    from app.gateway.video_gen.orchestration import tts as tts_mod

    tts_mod.set_tts_degradation_context(tmp_path)
    try:
        tts_mod.record_tts_degradation(
            kind="tts_access_key_missing",
            requested_voice="doubao:spk1",
            actual_voice="preset:146",
            detail="VOLCENGINE_VOICE_ACCESS_KEY 未配置",
        )
    finally:
        tts_mod.set_tts_degradation_context(None)

    sidecar = tmp_path / DEGRADATION_SIDECAR_NAME
    assert sidecar.exists()
    data = json.loads(sidecar.read_text())
    assert len(data["items"]) == 1
    assert data["items"][0]["kind"] == "tts_access_key_missing"


def test_record_tts_degradation_no_context_just_logs(tmp_path, monkeypatch):
    """无 task_dir 上下文时，record_tts_degradation 不写文件只记日志。"""
    from app.gateway.video_gen.degradations import DEGRADATION_SIDECAR_NAME
    from app.gateway.video_gen.orchestration import tts as tts_mod

    tts_mod.set_tts_degradation_context(None)
    tts_mod.record_tts_degradation(kind="test", detail="no context")
    assert not (tmp_path / DEGRADATION_SIDECAR_NAME).exists()


def test_record_tts_degradation_dedup(tmp_path):
    """同 kind 不重复写（record_degradation 幂等）。"""
    from app.gateway.video_gen.degradations import DEGRADATION_SIDECAR_NAME
    from app.gateway.video_gen.orchestration import tts as tts_mod

    tts_mod.set_tts_degradation_context(tmp_path)
    try:
        tts_mod.record_tts_degradation(kind="voice_fallback", detail="degraded")
        tts_mod.record_tts_degradation(kind="voice_fallback", detail="degraded again")
    finally:
        tts_mod.set_tts_degradation_context(None)

    data = json.loads((tmp_path / DEGRADATION_SIDECAR_NAME).read_text())
    assert len(data["items"]) == 1


# ── C3 补接（2026-08-09 评审）：运行时降级路径留痕 ─────────────────────
# 评审发现 record_tts_degradation 此前只接了 access-key 缺失 1 个静态场景；
# doubao→360（3 条运行时路径）与 360→edge（2 条）零记录——P1-E 核心场景
# 「doubao→360 换声用户无感」实际未解决。以下测试锁住这些路径。


@pytest.mark.asyncio
async def test_doubao_transport_fallback_records_degradation(tmp_path, monkeypatch):
    """doubao 传输错误 → 360 降级时写 sidecar（voice_fallback）。"""
    from pathlib import Path

    import httpx

    from app.gateway.video_gen.degradations import DEGRADATION_SIDECAR_NAME
    from app.gateway.video_gen.orchestration import tts as tts_mod
    from app.gateway.video_gen.orchestration.tts import TTSResult

    async def boom(*a, **kw):
        raise httpx.ConnectError("no route")

    monkeypatch.setattr("app.gateway.voice_twin.doubao_tts_client.DoubaoTTSClient.synthesize_sse", boom)
    monkeypatch.setenv("VOLCENGINE_VOICE_ACCESS_KEY", "k")

    async def fake_qihu(text, voice_id, speed, output_path):
        Path(output_path).write_bytes(b"QIHUAUDIO")
        return TTSResult(audio_path=output_path, srt="", duration=2.0)

    monkeypatch.setattr(tts_mod, "_synthesize_qihu", fake_qihu)
    tts_mod.set_tts_degradation_context(tmp_path)
    try:
        await tts_mod._synthesize_doubao("hi", "doubao:spk1", 1.0, str(tmp_path / "f.mp3"))
    finally:
        tts_mod.set_tts_degradation_context(None)

    data = json.loads((tmp_path / DEGRADATION_SIDECAR_NAME).read_text())
    kinds = [i["kind"] for i in data["items"]]
    assert "voice_fallback" in kinds, "修前 doubao→360 运行时降级零留痕"


@pytest.mark.asyncio
async def test_qihu_to_edge_fallback_records_degradation(tmp_path, monkeypatch):
    """360 重试后仍失败 → edge 降级时写 sidecar（voice_fallback）。"""
    from pathlib import Path

    from app.gateway.video_gen.degradations import DEGRADATION_SIDECAR_NAME
    from app.gateway.video_gen.orchestration import tts as tts_mod
    from app.gateway.video_gen.orchestration.tts import TTSResult

    monkeypatch.setenv("QIHU_API_KEY", "k")
    monkeypatch.delenv("TTS_DISABLE_EDGE_FALLBACK", raising=False)

    import httpx

    async def boom(**kw):
        raise httpx.ConnectError("360 down")

    async def fake_edge(text, voice_id, speed, output_path):
        Path(output_path).write_bytes(b"EDGEAUDIO")
        return TTSResult(audio_path=output_path, srt="", duration=1.0)

    monkeypatch.setattr(tts_mod, "_call_360_with_retry", boom)
    monkeypatch.setattr(tts_mod, "_synthesize_edge", fake_edge)
    tts_mod.set_tts_degradation_context(tmp_path)
    try:
        await tts_mod._synthesize_qihu("hi", "preset:146", 1.0, str(tmp_path / "f.mp3"))
    finally:
        tts_mod.set_tts_degradation_context(None)

    data = json.loads((tmp_path / DEGRADATION_SIDECAR_NAME).read_text())
    kinds = [i["kind"] for i in data["items"]]
    assert "voice_fallback" in kinds, "修前 360→edge 降级零留痕（全片变声用户无感）"
