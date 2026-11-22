from __future__ import annotations

import pytest

from app.gateway.video_gen.orchestration.voices import CURATED_VOICES, LANG_LOCALE_MAP, resolve_default_voice
from app.gateway.video_gen.schemas import CreateVideoRequest


def test_lang_validation_rejects_invalid():
    with pytest.raises(Exception):
        CreateVideoRequest(video_type="ai_image", mode="fixed", script="test", lang="xx")


def test_lang_all_supported_for_legacy_payloads():
    for lang in ("zh", "en", "ja", "ko", "fr", "de", "es"):
        req = CreateVideoRequest(video_type="ai_image", mode="fixed", script="test", lang=lang)
        assert req.lang == lang


def test_only_chinese_has_a_default_voice():
    for lang in LANG_LOCALE_MAP:
        voice_id = resolve_default_voice(lang)
        if lang == "zh":
            assert voice_id == "doubao:zh_female_vv_uranus_bigtts"
        else:
            assert voice_id is None


def test_zh_default_voice_is_curated_doubao(monkeypatch):
    monkeypatch.delenv("DOUBAO_TTS_DEFAULT_SPEAKER", raising=False)
    voice = resolve_default_voice("zh")
    assert voice == "doubao:zh_female_vv_uranus_bigtts"
    assert voice in {entry["id"] for entry in CURATED_VOICES}


def test_zh_default_voice_uses_catalog_speaker(monkeypatch):
    monkeypatch.setenv("DOUBAO_TTS_DEFAULT_SPEAKER", "zh_male_m191_uranus_bigtts")
    assert resolve_default_voice("zh") == "doubao:zh_male_m191_uranus_bigtts"


def test_remotion_rejected_from_schema():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        CreateVideoRequest(video_type="remotion", mode="fixed", script="test script")
