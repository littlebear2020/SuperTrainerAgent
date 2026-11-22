from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.gateway._shared.voice_catalog import CURATED_VOICES
from app.gateway.video_gen.orchestration.voices import resolve_default_voice
from app.gateway.voice_twin.production_service import ProductionService
from app.gateway.voice_twin.service import VoiceCloneService

DOUBAO_VOICE_IDS = [
    "doubao:zh_female_vv_uranus_bigtts",
    "doubao:zh_female_yingyujiaoxue_uranus_bigtts",
    "doubao:zh_male_m191_uranus_bigtts",
    "doubao:zh_male_liufei_uranus_bigtts",
]
RETIRED_VOICE_IDS = ["preset:146", "edge:zh-CN-XiaoxiaoNeural", "zh-CN-XiaoxiaoNeural"]
RETIRED_MESSAGE = "原音色已停用，请改选字节直连音色"


def test_shared_catalog_contains_only_four_chinese_doubao_voices():
    assert [voice.id for voice in CURATED_VOICES] == DOUBAO_VOICE_IDS
    assert {voice.provider for voice in CURATED_VOICES} == {"doubao"}
    assert {voice.locale for voice in CURATED_VOICES} == {"zh-CN"}


def test_voice_twin_catalog_api_contains_only_doubao_voices():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.gateway.routers.voice_twin import router

    app = FastAPI()
    app.include_router(router)
    service = MagicMock()
    service.doubao_client = MagicMock()
    service.qihu_client = MagicMock()
    service.edge_client = MagicMock()
    app.state.voice_clone_service = service
    with TestClient(app) as client:
        response = client.get("/api/voice-twins/preset-voices")
    assert response.status_code == 200
    assert [voice["id"] for voice in response.json()] == DOUBAO_VOICE_IDS


@pytest.mark.asyncio
async def test_video_catalog_api_contains_only_doubao_voices(monkeypatch):
    from app.gateway.routers.video_gen import list_voices_endpoint

    monkeypatch.setenv("VOLCENGINE_VOICE_ACCESS_KEY", "test-key")
    monkeypatch.delenv("DOUBAO_TTS_ENABLED_SPEAKERS", raising=False)
    monkeypatch.delenv("QIHU_API_KEY", raising=False)
    voices = await list_voices_endpoint()
    assert [voice["id"] for voice in voices] == DOUBAO_VOICE_IDS
    assert {voice["provider"] for voice in voices} == {"doubao"}


@pytest.mark.parametrize("voice_id", RETIRED_VOICE_IDS)
@pytest.mark.asyncio
async def test_clone_service_synthesize_rejects_retired_voices_before_io(voice_id, tmp_path):
    service = object.__new__(VoiceCloneService)
    service.repo = SimpleNamespace(get_by_id=AsyncMock(return_value=None))
    service.edge_client = SimpleNamespace(synthesize=AsyncMock(return_value=b"edge"))
    service._storage_base = tmp_path

    with pytest.raises(ValueError, match=RETIRED_MESSAGE):
        await service.synthesize(voice_id, "不得发起调用")

    service.repo.get_by_id.assert_not_awaited()
    service.edge_client.synthesize.assert_not_awaited()


@pytest.mark.parametrize("voice_id", RETIRED_VOICE_IDS)
@pytest.mark.asyncio
async def test_production_synthesize_rejects_retired_voices_before_retry_or_network(voice_id):
    service = object.__new__(ProductionService)
    service.voice_service = SimpleNamespace(
        doubao_client=MagicMock(),
        qihu_client=MagicMock(),
        edge_client=MagicMock(),
    )
    service._synthesize_with_retry = AsyncMock(return_value=b"audio")

    with pytest.raises(ValueError, match=RETIRED_MESSAGE):
        await service._do_tts_synthesis(voice_id, "不得发起调用")

    service._synthesize_with_retry.assert_not_awaited()


@pytest.mark.parametrize("lang", ["en", "ja", "ko", "fr", "de", "es"])
def test_non_chinese_edge_defaults_are_retired(lang):
    assert resolve_default_voice(lang) is None


def test_default_voice_must_be_catalog_doubao_voice(monkeypatch):
    monkeypatch.setenv("DOUBAO_TTS_DEFAULT_SPEAKER", "zh_male_m191_uranus_bigtts")
    assert resolve_default_voice("zh") == "doubao:zh_male_m191_uranus_bigtts"

    monkeypatch.setenv("DOUBAO_TTS_DEFAULT_SPEAKER", "retired-or-unlisted")
    assert resolve_default_voice("zh") == DOUBAO_VOICE_IDS[0]

    monkeypatch.delenv("DOUBAO_TTS_DEFAULT_SPEAKER")
    assert resolve_default_voice("zh") == DOUBAO_VOICE_IDS[0]


def test_unavailable_default_speaker_logs_visible_warning(monkeypatch, caplog):
    import logging

    monkeypatch.setenv("DOUBAO_TTS_DEFAULT_SPEAKER", "retired-or-unlisted")
    monkeypatch.delenv("DOUBAO_TTS_ENABLED_SPEAKERS", raising=False)
    with caplog.at_level(logging.WARNING):
        assert resolve_default_voice("zh") == DOUBAO_VOICE_IDS[0]
    assert "DOUBAO_TTS_DEFAULT_SPEAKER" in caplog.text
    assert "retired-or-unlisted" in caplog.text


@pytest.mark.parametrize("voice_id", [None, ""])
@pytest.mark.asyncio
async def test_orchestration_synthesize_rejects_empty_voice_before_io(voice_id, tmp_path, monkeypatch):
    from app.gateway.video_gen.orchestration import tts as tts_mod

    doubao = AsyncMock()
    monkeypatch.setattr(tts_mod, "_synthesize_doubao", doubao)
    with pytest.raises(ValueError, match="音色 ID 不能为空"):
        await tts_mod.synthesize("不得发起调用", voice_id=voice_id, output_path=str(tmp_path / "out.mp3"))
    doubao.assert_not_awaited()


@pytest.mark.parametrize("voice_id", ["doubao:not-in-catalog", "doubao:zh_male_m191_uranus_bigtts"])
@pytest.mark.asyncio
async def test_orchestration_rejects_doubao_outside_catalog_or_whitelist(voice_id, tmp_path, monkeypatch):
    from app.gateway.video_gen.orchestration import tts as tts_mod

    monkeypatch.setenv("DOUBAO_TTS_ENABLED_SPEAKERS", "zh_female_vv_uranus_bigtts")
    doubao = AsyncMock()
    monkeypatch.setattr(tts_mod, "_synthesize_doubao", doubao)
    with pytest.raises(ValueError, match="目录.*白名单"):
        await tts_mod.synthesize("不得发起调用", voice_id=voice_id, output_path=str(tmp_path / "out.mp3"))
    doubao.assert_not_awaited()


@pytest.mark.parametrize("voice_id", RETIRED_VOICE_IDS)
@pytest.mark.asyncio
async def test_orchestration_synthesize_rejects_retired_voices_before_io(voice_id, tmp_path, monkeypatch):
    from app.gateway.video_gen.orchestration import tts as tts_mod

    edge = AsyncMock()
    qihu = AsyncMock()
    doubao = AsyncMock()
    monkeypatch.setattr(tts_mod, "_synthesize_edge", edge, raising=False)
    monkeypatch.setattr(tts_mod, "_synthesize_qihu", qihu, raising=False)
    monkeypatch.setattr(tts_mod, "_synthesize_doubao", doubao)

    with pytest.raises(ValueError, match=RETIRED_MESSAGE):
        await tts_mod.synthesize("不得发起调用", voice_id=voice_id, output_path=str(tmp_path / "out.mp3"))

    assert edge.await_count == 0
    assert qihu.await_count == 0
    assert doubao.await_count == 0


@pytest.mark.parametrize(
    "error",
    [
        pytest.param("http-503", id="http-503"),
        pytest.param("transport", id="transport"),
        pytest.param("runtime", id="transient-runtime"),
    ],
)
@pytest.mark.asyncio
async def test_doubao_failure_never_changes_voice(error, tmp_path, monkeypatch):
    import httpx

    from app.gateway.video_gen.orchestration import tts as tts_mod
    from app.gateway.voice_twin.doubao_tts_client import DoubaoTTSHTTPError

    if error == "http-503":
        failure = DoubaoTTSHTTPError(503, "down")
    elif error == "transport":
        failure = httpx.ConnectError("no route")
    else:
        failure = RuntimeError("quota exceeded")

    monkeypatch.setenv("VOLCENGINE_VOICE_ACCESS_KEY", "test-key")
    monkeypatch.setattr(tts_mod, "_call_doubao_with_retry", AsyncMock(side_effect=failure))
    qihu = AsyncMock()
    edge = AsyncMock()
    monkeypatch.setattr(tts_mod, "_synthesize_qihu", qihu, raising=False)
    monkeypatch.setattr(tts_mod, "_synthesize_edge", edge, raising=False)

    with pytest.raises(type(failure)):
        await tts_mod._synthesize_doubao("失败即终止", DOUBAO_VOICE_IDS[0], 1.0, str(tmp_path / "out.mp3"))
    assert qihu.await_count == 0
    assert edge.await_count == 0


@pytest.mark.asyncio
async def test_doubao_missing_key_fails_without_fallback(tmp_path, monkeypatch):
    from app.gateway.video_gen.orchestration import tts as tts_mod

    monkeypatch.delenv("VOLCENGINE_VOICE_ACCESS_KEY", raising=False)
    qihu = AsyncMock()
    edge = AsyncMock()
    monkeypatch.setattr(tts_mod, "_synthesize_qihu", qihu, raising=False)
    monkeypatch.setattr(tts_mod, "_synthesize_edge", edge, raising=False)

    with pytest.raises(RuntimeError, match="VOLCENGINE_VOICE_ACCESS_KEY"):
        await tts_mod._synthesize_doubao("缺 key 即失败", DOUBAO_VOICE_IDS[0], 1.0, str(tmp_path / "out.mp3"))
    assert qihu.await_count == 0
    assert edge.await_count == 0


@pytest.mark.asyncio
async def test_production_doubao_failure_never_degrades():
    from app.gateway.voice_twin.doubao_tts_client import DoubaoTTSHTTPError

    service = object.__new__(ProductionService)
    service.voice_service = SimpleNamespace(qihu_client=None, edge_client=None)
    failure = DoubaoTTSHTTPError(503, "down")
    service._synthesize_with_retry = AsyncMock(side_effect=failure)

    with pytest.raises(DoubaoTTSHTTPError) as caught:
        await service._do_tts_synthesis(DOUBAO_VOICE_IDS[0], "失败即终止")
    assert caught.value is failure
    service._synthesize_with_retry.assert_awaited_once()


def test_vimax_default_never_falls_back_to_edge(monkeypatch):
    from app.gateway.video_gen.engines.vimax.voice_policy import resolve_vimax_default_voice

    monkeypatch.delenv("VOLCENGINE_VOICE_ACCESS_KEY", raising=False)
    monkeypatch.delenv("DOUBAO_TTS_DEFAULT_SPEAKER", raising=False)
    monkeypatch.delenv("DOUBAO_TTS_ENABLED_SPEAKERS", raising=False)
    assert resolve_vimax_default_voice("zh") == DOUBAO_VOICE_IDS[0]
    assert resolve_vimax_default_voice("en") is None


def test_vimax_non_chinese_without_default_fails_before_pipeline():
    from app.gateway.video_gen.service import VideoGenService

    service = object.__new__(VideoGenService)
    with pytest.raises(ValueError, match="非中文.*字节直连"):
        service._resolve_voice_with_vimax_gate("vimax", {"lang": "en"}, user_id="u1")


def test_non_chinese_task_does_not_fall_back_to_config_chinese_voice():
    from app.gateway.video_gen.service import VideoGenService

    service = object.__new__(VideoGenService)
    service._config = SimpleNamespace(tts_voice=DOUBAO_VOICE_IDS[0])
    with pytest.raises(ValueError, match="非中文.*字节直连"):
        service._resolve_voice_with_vimax_gate("ai_image", {"lang": "en"}, user_id="u1")


def test_chinese_task_does_not_bypass_empty_visible_voice_set(monkeypatch):
    from app.gateway.video_gen.service import VideoGenService

    monkeypatch.setenv("DOUBAO_TTS_ENABLED_SPEAKERS", "not-in-catalog")
    service = object.__new__(VideoGenService)
    service._config = SimpleNamespace(tts_voice=DOUBAO_VOICE_IDS[0])
    with pytest.raises(ValueError, match="白名单.*字节直连"):
        service._resolve_voice_with_vimax_gate("ai_image", {"lang": "zh"}, user_id="u1")


def test_vimax_retired_voice_uses_shared_readable_error():
    from app.gateway.video_gen.engines.vimax.voice_policy import ensure_vimax_voice_allowed

    with pytest.raises(ValueError, match=RETIRED_MESSAGE):
        ensure_vimax_voice_allowed("preset:146")


def test_retired_tts_config_is_removed_and_default_is_catalog_doubao():
    from app.gateway.video_gen.config import VideoGenConfig
    from app.gateway.voice_twin.config import VoiceCloneConfig

    clone_config = VoiceCloneConfig()
    assert not hasattr(clone_config, "qihu_tts")
    assert not hasattr(clone_config, "edge_tts")

    video_config = VideoGenConfig()
    assert video_config.tts_voice == DOUBAO_VOICE_IDS[0]
    assert not hasattr(video_config, "tts_fallback_voice")


@pytest.mark.asyncio
async def test_clone_service_routes_catalog_doubao_directly_before_repo(tmp_path):
    service = object.__new__(VoiceCloneService)
    service.doubao_client = SimpleNamespace(synthesize_sse=AsyncMock(return_value=b"mp3"))
    service.repo = SimpleNamespace(get_by_id=AsyncMock(return_value=None))
    service._write_synth_output = AsyncMock(return_value="output.mp3")

    result = await service.synthesize(DOUBAO_VOICE_IDS[0], "目录音色直连")

    assert result == "output.mp3"
    service.repo.get_by_id.assert_not_awaited()
    assert service.doubao_client.synthesize_sse.await_args.kwargs["speaker"] == "zh_female_vv_uranus_bigtts"


@pytest.mark.asyncio
async def test_voice_twin_catalog_uses_same_doubao_whitelist(monkeypatch):
    from app.gateway.routers.voice_twin import list_preset_voices

    monkeypatch.setenv("DOUBAO_TTS_ENABLED_SPEAKERS", "zh_female_vv_uranus_bigtts")
    service = SimpleNamespace(doubao_client=MagicMock())
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(voice_clone_service=service)))
    voices = await list_preset_voices(request)
    assert [voice["id"] for voice in voices] == [DOUBAO_VOICE_IDS[0]]
