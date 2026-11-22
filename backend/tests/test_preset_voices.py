from __future__ import annotations

from pathlib import Path

import pytest

from app.gateway._shared.voice_catalog import CURATED_VOICES
from app.gateway.voice_twin.preset_voices import is_doubao_voice_id, parse_doubao_voice_id


def test_doubao_voice_id_helpers():
    assert is_doubao_voice_id("doubao:zh_female_vv_uranus_bigtts") is True
    assert is_doubao_voice_id("preset:146") is False
    assert is_doubao_voice_id("doubao:") is False
    assert parse_doubao_voice_id("doubao:spk_x") == "spk_x"


@pytest.mark.parametrize("voice_id", ["doubao:../../etc/passwd", "doubao:a\\b", "doubao:a..b"])
def test_doubao_voice_id_rejects_path_traversal(voice_id):
    with pytest.raises(ValueError):
        parse_doubao_voice_id(voice_id)


def test_shared_catalog_has_four_complete_chinese_doubao_entries():
    assert len(CURATED_VOICES) == 4
    assert len({voice.id for voice in CURATED_VOICES}) == 4
    for voice in CURATED_VOICES:
        assert voice.id.startswith("doubao:")
        assert voice.provider == "doubao"
        assert voice.locale == "zh-CN"
        assert voice.name and voice.description
        assert voice.scenario and voice.tone


def test_video_legacy_reexport_keeps_shape_and_doubao_ids():
    from app.gateway.video_gen.orchestration.voices import CURATED_VOICES as LEGACY

    assert len(LEGACY) == 4
    for voice in LEGACY:
        assert set(voice) == {"id", "name", "locale", "gender", "provider"}
        assert voice["id"].startswith("doubao:")
        assert voice["gender"] in {"Male", "Female"}


def test_preview_cache_key_sanitize_cannot_escape_directory():
    from app.gateway.routers.voice_twin_production import _safe_cache_voice_id

    safe = _safe_cache_voice_id("doubao:../../etc/passwd")
    assert "/" not in safe and "\\" not in safe
    assert (Path("/cache/root") / f"{safe}_abc12345.mp3").resolve().parent == Path("/cache/root")
