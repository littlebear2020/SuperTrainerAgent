"""Tests for multi-language TTS support."""

from __future__ import annotations

import pytest

from app.gateway.video_gen.orchestration.voices import (
    CURATED_VOICES,
    DEFAULT_VOICES,
    LANG_LOCALE_MAP,
    resolve_default_voice,
)
from app.gateway.video_gen.schemas import CreateVideoRequest


def test_lang_validation_rejects_invalid():
    """Unsupported lang code raises validation error."""
    with pytest.raises(Exception):
        CreateVideoRequest(video_type="ai_image", mode="fixed", script="test", lang="xx")


def test_lang_all_supported():
    """All declared langs are accepted."""
    for lang in ("zh", "en", "ja", "ko", "fr", "de", "es"):
        req = CreateVideoRequest(video_type="ai_image", mode="fixed", script="test", lang=lang)
        assert req.lang == lang


def test_default_voice_per_lang():
    """Each supported lang has a default voice mapping.

    zh is resolved lazily via resolve_default_voice() (not in DEFAULT_VOICES
    dict) so the doubao speaker is read from env at call time. Other langs
    use the static DEFAULT_VOICES dict.
    """
    for lang in LANG_LOCALE_MAP:
        voice_id = resolve_default_voice(lang)
        assert voice_id is not None, f"Missing default voice for lang '{lang}'"
        assert isinstance(voice_id, str) and len(voice_id) > 0


def test_default_voices_in_curated():
    """Non-preset default voices appear in the curated edge-tts list. Preset voices
    (preset:XXX → 360 TTS) are intentionally excluded — zh uses a 360 preset voice
    because edge-tts/bing is blocked in restricted networks."""
    curated_ids = {v["id"] for v in CURATED_VOICES}
    for lang, voice_id in DEFAULT_VOICES.items():
        if voice_id.startswith("preset:"):
            continue
        assert voice_id in curated_ids, f"Default voice '{voice_id}' for lang '{lang}' not in CURATED_VOICES"


def test_zh_default_voice_is_360_preset_when_no_doubao(monkeypatch):
    """zh must default to a 360 preset voice (preset:XXX → 360 TTS) when
    DOUBAO_TTS_DEFAULT_SPEAKER is not set, NOT edge-tts. edge-tts needs
    speech.platform.bing.com which is blocked in restricted/sandbox networks."""
    monkeypatch.delenv("DOUBAO_TTS_DEFAULT_SPEAKER", raising=False)
    voice = resolve_default_voice("zh")
    assert voice is not None
    assert voice.startswith("preset:"), f"zh default must be 360 preset (no doubao), got {voice!r}"


def test_zh_default_voice_is_doubao_when_speaker_set(monkeypatch):
    """zh defaults to doubao:<speaker> when DOUBAO_TTS_DEFAULT_SPEAKER is set."""
    monkeypatch.setenv("DOUBAO_TTS_DEFAULT_SPEAKER", "zh_female_qingxin")
    voice = resolve_default_voice("zh")
    assert voice == "doubao:zh_female_qingxin"


def test_lang_locale_map_coverage():
    """LANG_LOCALE_MAP covers all supported langs — zh is resolved via
    resolve_default_voice(), others via DEFAULT_VOICES."""
    for lang in LANG_LOCALE_MAP:
        assert lang in DEFAULT_VOICES or lang == "zh", f"lang '{lang}' has no default voice path"


def test_remotion_rejected_from_schema():
    """Remotion removed (2026-07-15 split): video_type="remotion" is rejected."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        CreateVideoRequest(video_type="remotion", mode="fixed", script="test script")
