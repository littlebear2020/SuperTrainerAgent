from __future__ import annotations

from app.gateway.video_gen.orchestration.voices import resolve_default_voice


def test_resolve_zh_default_uses_catalog_doubao_when_speaker_set(monkeypatch) -> None:
    monkeypatch.setenv("DOUBAO_TTS_DEFAULT_SPEAKER", "zh_female_vv_uranus_bigtts")
    assert resolve_default_voice("zh") == "doubao:zh_female_vv_uranus_bigtts"


def test_resolve_zh_default_uses_vivi_when_speaker_unset(monkeypatch) -> None:
    monkeypatch.delenv("DOUBAO_TTS_DEFAULT_SPEAKER", raising=False)
    assert resolve_default_voice("zh") == "doubao:zh_female_vv_uranus_bigtts"


def test_resolve_zh_default_rejects_unlisted_speaker(monkeypatch) -> None:
    monkeypatch.setenv("DOUBAO_TTS_DEFAULT_SPEAKER", "unlisted")
    assert resolve_default_voice("zh") == "doubao:zh_female_vv_uranus_bigtts"


def test_resolve_default_voice_returns_none_for_retired_edge_languages() -> None:
    for lang in ("en", "ja", "ko", "fr", "de", "es"):
        assert resolve_default_voice(lang) is None


def test_resolve_default_voice_returns_none_for_unknown_lang() -> None:
    assert resolve_default_voice("xx") is None
