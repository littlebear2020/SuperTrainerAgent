import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.gateway.routers import video_gen as video_router
from app.gateway.video_gen.engines.vimax.dialogue import _zh_voice_pool
from app.gateway.video_gen.engines.vimax.voice_policy import ensure_vimax_voice_allowed, resolve_vimax_default_voice
from app.gateway.video_gen.orchestration import tts as tts_mod

VIVI = "doubao:zh_female_vv_uranus_bigtts"
YUNZHOU = "doubao:zh_male_m191_uranus_bigtts"


def test_default_uses_catalog_env_speaker(monkeypatch):
    monkeypatch.setenv("DOUBAO_TTS_DEFAULT_SPEAKER", "zh_male_m191_uranus_bigtts")
    monkeypatch.delenv("DOUBAO_TTS_ENABLED_SPEAKERS", raising=False)
    assert resolve_vimax_default_voice("zh") == YUNZHOU


def test_default_uses_catalog_whitelist_first(monkeypatch):
    monkeypatch.setenv("DOUBAO_TTS_DEFAULT_SPEAKER", "unlisted")
    monkeypatch.setenv("DOUBAO_TTS_ENABLED_SPEAKERS", "zh_male_m191_uranus_bigtts,zh_female_vv_uranus_bigtts")
    assert resolve_vimax_default_voice("zh") == YUNZHOU


def test_default_returns_none_when_whitelist_has_no_catalog_speaker(monkeypatch):
    from app.gateway.video_gen.orchestration.voices import resolve_default_voice

    monkeypatch.delenv("DOUBAO_TTS_DEFAULT_SPEAKER", raising=False)
    monkeypatch.setenv("DOUBAO_TTS_ENABLED_SPEAKERS", "not-in-catalog")
    assert resolve_vimax_default_voice("zh") is None
    assert resolve_default_voice("zh") is None


def test_default_never_uses_edge(monkeypatch):
    monkeypatch.delenv("VOLCENGINE_VOICE_ACCESS_KEY", raising=False)
    monkeypatch.delenv("DOUBAO_TTS_DEFAULT_SPEAKER", raising=False)
    monkeypatch.delenv("DOUBAO_TTS_ENABLED_SPEAKERS", raising=False)
    assert resolve_vimax_default_voice("zh") == VIVI
    assert resolve_vimax_default_voice("en") is None


def test_retired_voice_rejected_with_shared_message():
    with pytest.raises(ValueError, match="原音色已停用，请改选字节直连音色"):
        ensure_vimax_voice_allowed("preset:332")
    with pytest.raises(ValueError, match="原音色已停用，请改选字节直连音色"):
        ensure_vimax_voice_allowed("zh-CN-YunjianNeural")
    ensure_vimax_voice_allowed(YUNZHOU)
    ensure_vimax_voice_allowed(None)


def test_voice_pool_contains_only_doubao():
    assert _zh_voice_pool("Male") == [YUNZHOU, "doubao:zh_male_liufei_uranus_bigtts"]


def test_voice_pool_respects_doubao_whitelist(monkeypatch):
    monkeypatch.setenv("DOUBAO_TTS_ENABLED_SPEAKERS", "zh_female_vv_uranus_bigtts")
    assert _zh_voice_pool("Female") == [VIVI]
    assert _zh_voice_pool("Male") == []


def test_speaker_map_warns_when_single_gender_whitelist_uses_default(monkeypatch, caplog):
    import logging

    from app.gateway.video_gen.engines.vimax.dialogue import build_speaker_voice_map

    monkeypatch.setenv("DOUBAO_TTS_ENABLED_SPEAKERS", "zh_female_vv_uranus_bigtts")
    with caplog.at_level(logging.WARNING):
        result = build_speaker_voice_map(
            [{"identifier_in_scene": "男角色", "static_features": "男性"}],
            default_voice=VIVI,
            narrator_voice=VIVI,
        )
    assert result.by_speaker == {"男角色": VIVI}
    assert "男声角色" in caplog.text
    assert "默认音色" in caplog.text


def test_speaker_map_warns_after_removing_narrator(monkeypatch, caplog):
    import logging

    from app.gateway.video_gen.engines.vimax.dialogue import build_speaker_voice_map

    monkeypatch.setenv("DOUBAO_TTS_ENABLED_SPEAKERS", "zh_male_m191_uranus_bigtts")
    with caplog.at_level(logging.WARNING):
        result = build_speaker_voice_map(
            [{"identifier_in_scene": "男角色", "static_features": "男性"}],
            default_voice=VIVI,
            narrator_voice=YUNZHOU,
        )
    assert result.by_speaker == {"男角色": VIVI}
    assert "男声角色" in caplog.text
    assert "默认音色" in caplog.text


def _preview_app(monkeypatch, tmp_path):
    monkeypatch.setattr(video_router, "get_paths", lambda: SimpleNamespace(base_dir=tmp_path))
    app = FastAPI()
    app.include_router(video_router.router)
    app.state.video_gen_service = None
    return app, tmp_path / "video-gen" / "voice_previews"


@pytest.mark.asyncio
async def test_preview_cache_isolated_by_task_type(monkeypatch, tmp_path):
    calls = []

    async def fake_synthesize(text, *, voice_id, speed, output_path, **kwargs):
        calls.append((voice_id, text))
        Path(output_path).write_bytes(b"mp3")
        return tts_mod.TTSResult(audio_path=output_path, srt="", duration=1.0)

    monkeypatch.setattr(tts_mod, "synthesize", fake_synthesize)
    app, previews = _preview_app(monkeypatch, tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        for task_type in (None, "vimax"):
            params = {"text": "same-preview"}
            if task_type:
                params["task_type"] = task_type
            response = await client.get(f"/api/video/voices/{VIVI}/preview", params=params)
            assert response.status_code == 200
    assert len(calls) == 2
    assert len(list(previews.iterdir())) == 2


@pytest.mark.parametrize("task_type", ["a/b", "x" * 300])
@pytest.mark.asyncio
async def test_preview_task_type_produces_flat_bounded_cache(monkeypatch, tmp_path, task_type):
    async def fake_synthesize(text, *, voice_id, speed, output_path, **kwargs):
        Path(output_path).write_bytes(b"mp3")
        return tts_mod.TTSResult(audio_path=output_path, srt="", duration=1.0)

    monkeypatch.setattr(tts_mod, "synthesize", fake_synthesize)
    app, previews = _preview_app(monkeypatch, tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        response = await client.get(f"/api/video/voices/{VIVI}/preview", params={"task_type": task_type, "text": "bounded"})
    assert response.status_code == 200
    entries = list(previews.iterdir())
    assert len(entries) == 1
    assert entries[0].is_file()
    assert len(entries[0].name.encode()) <= 255


@pytest.mark.asyncio
async def test_preview_retired_preset_gate_precedes_cache(monkeypatch, tmp_path):
    from app.gateway.routers.voice_twin_production import _safe_cache_voice_id

    app, previews = _preview_app(monkeypatch, tmp_path)
    previews.mkdir(parents=True)
    probe = "gate-order"
    key = f"{_safe_cache_voice_id('preset:332')}_vimax_{hashlib.md5(probe.encode()).hexdigest()[:8]}"
    (previews / f"{key}.mp3").write_bytes(b"retired-audio")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        response = await client.get("/api/video/voices/preset%3A332/preview", params={"task_type": "vimax", "text": probe})
    assert response.status_code == 400
    assert response.json()["detail"] == "原音色已停用，请改选字节直连音色"
