"""Tests for _tts_concurrency helper (Stage 2a breadth).

doubao TTS is a paid cloud service like 360 preset — both get concurrency 3
(not 5 which is for free edge-tts). See
backend/docs/BYTEDANCE_MODELS_INTEGRATION_PLAN.md §阶段 2a.
"""

from __future__ import annotations

from app.gateway.video_gen.service import _tts_concurrency


def test_concurrency_3_for_preset():
    assert _tts_concurrency("preset:146") == 3


def test_concurrency_3_for_doubao():
    assert _tts_concurrency("doubao:zh_female_qingxin") == 3


def test_concurrency_5_for_edge_tts():
    assert _tts_concurrency("zh-CN-YunjianNeural") == 5
    assert _tts_concurrency("en-US-ChristopherNeural") == 5


def test_concurrency_5_for_clone_voice():
    """Cloned voice IDs (hex strings) get concurrency 5 (Volcengine path)."""
    assert _tts_concurrency("vt_abc123") == 5
