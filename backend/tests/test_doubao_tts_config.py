"""Tests for DoubaoTTSConfig (Stage 2a breadth).

``DoubaoTTSConfig`` holds the seed-tts-2.0 settings for voice_twin's production
TTS path. The API key is NOT a separate field — it reuses
``VoiceCloneConfig.access_key`` (single openspeech account, same ``X-Api-Key``
header). The speaker comes from the ``doubao:<speaker>`` voice id; the openspeech
resource id is fixed inside DoubaoTTSClient (the old ``default_speaker`` /
``resource_id`` config fields were dead — zero code reads — and were deleted
2026-07-28). See backend/docs/BYTEDANCE_MODELS_INTEGRATION_PLAN.md §阶段 2a + §0.2.
"""

from __future__ import annotations

from app.gateway.voice_twin.config import DoubaoTTSConfig, VoiceCloneConfig


def test_doubao_tts_config_defaults():
    cfg = DoubaoTTSConfig()
    assert cfg.enabled is True


def test_doubao_tts_config_override():
    cfg = DoubaoTTSConfig(enabled=False)
    assert cfg.enabled is False


def test_doubao_tts_config_has_no_api_key_field():
    """api_key reuses VoiceCloneConfig.access_key — DoubaoTTSConfig must NOT
    declare its own api_key (single openspeech account)."""
    field_names = set(DoubaoTTSConfig.model_fields.keys())
    assert "api_key" not in field_names
    assert "access_key" not in field_names


def test_voice_clone_config_includes_doubao_tts():
    """VoiceCloneConfig should embed DoubaoTTSConfig so the production path can
    read the enabled flag from the same config tree."""
    vc = VoiceCloneConfig()
    assert hasattr(vc, "doubao_tts")
    assert isinstance(vc.doubao_tts, DoubaoTTSConfig)
    assert vc.doubao_tts.enabled is True
