"""Tests for default-voice resolution (Stage 2a).

``resolve_default_voice`` makes zh config-driven + rollback-safe: when
``DOUBAO_TTS_DEFAULT_SPEAKER`` is set, zh → ``doubao:<speaker>`` (字节默认首选);
when unset, zh → ``preset:146`` (360, rollback). It reads the env lazily at call
time (after ``load_dotenv``), NOT at module import — so the speaker is picked up
correctly regardless of import order. See
backend/docs/BYTEDANCE_MODELS_INTEGRATION_PLAN.md §阶段 2a + §1.4 漏点1.
"""

from __future__ import annotations

from app.gateway.video_gen.orchestration.voices import resolve_default_voice


def test_resolve_zh_default_uses_doubao_when_speaker_set(monkeypatch) -> None:
    monkeypatch.setenv("DOUBAO_TTS_DEFAULT_SPEAKER", "zh_female_qingxin")
    assert resolve_default_voice("zh") == "doubao:zh_female_qingxin"


def test_resolve_zh_default_falls_back_to_preset_when_speaker_unset(monkeypatch) -> None:
    monkeypatch.delenv("DOUBAO_TTS_DEFAULT_SPEAKER", raising=False)
    assert resolve_default_voice("zh") == "preset:146"  # 360 rollback


def test_resolve_zh_default_empty_speaker_falls_back_to_preset(monkeypatch) -> None:
    monkeypatch.setenv("DOUBAO_TTS_DEFAULT_SPEAKER", "")
    assert resolve_default_voice("zh") == "preset:146"


def test_resolve_default_voice_returns_neural_for_other_langs() -> None:
    assert resolve_default_voice("en") == "en-US-ChristopherNeural"
    assert resolve_default_voice("ja") == "ja-JP-KeitaNeural"
    assert resolve_default_voice("ko") == "ko-KR-HyunsuNeural"


def test_resolve_default_voice_returns_none_for_unknown_lang() -> None:
    assert resolve_default_voice("xx") is None
