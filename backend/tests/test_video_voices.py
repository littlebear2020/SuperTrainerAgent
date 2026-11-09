"""Tests for voice list caching and curated voices."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.gateway.routers.video_gen import list_voices_endpoint
from app.gateway.video_gen.orchestration.voices import (
    CURATED_VOICES,
    list_voices_cached,
)

# doubao curated voices 依赖本地 gitignored config.yaml 里的 access_key（CI fresh
# checkout 无此文件，端点返回为空）——缺失时跳过，本地在位照常执行。同
# test_skills_custom_router.py 的 CI 守卫惯例。
_REAL_CONFIG_EXISTS = any((Path(__file__).resolve().parents[i] / "config.yaml").exists() for i in (1, 2))
requires_real_config = pytest.mark.skipif(not _REAL_CONFIG_EXISTS, reason="依赖本地 gitignored config.yaml，CI 跳过")


def test_curated_voices_structure():
    """All curated voices have required fields."""
    assert len(CURATED_VOICES) >= 5
    for v in CURATED_VOICES:
        assert v["id"]
        assert v["name"]
        assert v["locale"]
        assert v["gender"] in ("Male", "Female")


def test_curated_voices_unique_ids():
    """All curated voice IDs are unique."""
    ids = [v["id"] for v in CURATED_VOICES]
    assert len(ids) == len(set(ids))


def test_curated_voices_includes_chinese():
    """Curated list includes Chinese voices."""
    zh = [v for v in CURATED_VOICES if v["locale"].startswith("zh")]
    assert len(zh) >= 3


def test_curated_voices_includes_english():
    """Curated list includes English voices."""
    en = [v for v in CURATED_VOICES if v["locale"].startswith("en")]
    assert len(en) >= 2


@pytest.mark.asyncio
async def test_list_voices_cached_no_filter():
    """list_voices_cached without lang returns all voices."""
    mock_voices = [
        {"ShortName": "zh-CN-Test1", "Locale": "zh-CN", "Gender": "Male"},
        {"ShortName": "en-US-Test1", "Locale": "en-US", "Gender": "Female"},
    ]
    with patch("app.gateway.video_gen.orchestration.voices.edge_tts") as mock_tts:
        mock_tts.list_voices = AsyncMock(return_value=mock_voices)
        # Reset cache
        import app.gateway.video_gen.orchestration.voices as mod

        mod._cache = None
        result = await list_voices_cached()
    assert len(result) == 2


@pytest.mark.asyncio
async def test_list_voices_cached_with_filter():
    """list_voices_cached filters by locale prefix."""
    mock_voices = [
        {"ShortName": "zh-CN-Test1", "Locale": "zh-CN", "Gender": "Male"},
        {"ShortName": "zh-TW-Test1", "Locale": "zh-TW", "Gender": "Female"},
        {"ShortName": "en-US-Test1", "Locale": "en-US", "Gender": "Female"},
    ]
    with patch("app.gateway.video_gen.orchestration.voices.edge_tts") as mock_tts:
        mock_tts.list_voices = AsyncMock(return_value=mock_voices)
        import app.gateway.video_gen.orchestration.voices as mod

        mod._cache = None
        result = await list_voices_cached("zh")
    assert len(result) == 2
    assert all(v["Locale"].startswith("zh") for v in result)


@pytest.mark.asyncio
async def test_list_voices_cached_uses_cache():
    """Second call uses cached result without calling edge_tts again."""
    mock_voices = [{"ShortName": "test", "Locale": "en", "Gender": "Female"}]
    import app.gateway.video_gen.orchestration.voices as mod

    mod._cache = None

    with patch("app.gateway.video_gen.orchestration.voices.edge_tts") as mock_tts:
        mock_tts.list_voices = AsyncMock(return_value=mock_voices)
        await list_voices_cached()
        assert mock_tts.list_voices.call_count == 1

        # Second call should use cache
        await list_voices_cached()
        assert mock_tts.list_voices.call_count == 1


# ---------------------------------------------------------------------------
# Stage 2a breadth: doubao voices in /voices endpoint
# ---------------------------------------------------------------------------


@requires_real_config
class TestDoubaoVoicesInEndpoint:
    """GET /voices includes the doubao seed-tts-2.0 voices from CURATED_VOICES.

    The 4 doubao voices (Vivi/Tina/云舟/刘飞) are curated like the qihu presets —
    always present (the access_key is configured), NOT env-conditional. See
    backend/docs/BYTEDANCE_MODELS_INTEGRATION_PLAN.md §阶段 2a.
    """

    @pytest.mark.asyncio
    async def test_no_lang_includes_doubao_curated_voices(self):
        """No-lang response includes the 4 doubao curated voices (regardless of env)."""
        result = await list_voices_endpoint(lang=None)
        doubao_entries = [v for v in result if v.get("provider") == "doubao"]
        assert len(doubao_entries) == 4
        ids = {v["id"] for v in doubao_entries}
        assert "doubao:zh_female_vv_uranus_bigtts" in ids
        assert "doubao:zh_male_m191_uranus_bigtts" in ids
        assert all(v["locale"] == "zh-CN" for v in doubao_entries)

    @pytest.mark.asyncio
    async def test_lang_zh_includes_doubao_curated_voices(self):
        """lang=zh includes the doubao curated voices (locale zh-CN matches)."""
        mock_voices = [
            {"ShortName": "zh-CN-Test1", "Locale": "zh-CN", "Gender": "Male"},
        ]
        with patch("app.gateway.video_gen.orchestration.voices.edge_tts") as mock_tts:
            mock_tts.list_voices = AsyncMock(return_value=mock_voices)
            import app.gateway.video_gen.orchestration.voices as mod

            mod._cache = None
            result = await list_voices_endpoint(lang="zh")
        doubao_entries = [v for v in result if v.get("provider") == "doubao"]
        assert len(doubao_entries) == 4

    @pytest.mark.asyncio
    async def test_lang_en_excludes_doubao(self):
        """lang=en excludes doubao (zh-CN only)."""
        mock_voices = [
            {"ShortName": "en-US-Test1", "Locale": "en-US", "Gender": "Female"},
        ]
        with patch("app.gateway.video_gen.orchestration.voices.edge_tts") as mock_tts:
            mock_tts.list_voices = AsyncMock(return_value=mock_voices)
            import app.gateway.video_gen.orchestration.voices as mod

            mod._cache = None
            result = await list_voices_endpoint(lang="en")
        doubao_entries = [v for v in result if v.get("provider") == "doubao"]
        assert len(doubao_entries) == 0


# ---------------------------------------------------------------------------
# Usability (review 痛点1): doubao voices gated by access_key + optional whitelist.
# /voices must NOT list doubao voices when VOLCENGINE_VOICE_ACCESS_KEY is unset (they'd all
# fail with 55000000 "resource ID mismatched with speaker" — no fallback per requirement #4).
# Optional DOUBAO_TTS_ENABLED_SPEAKERS (comma list) filters to only provisioned speakers.
# See backend/docs/BYTEDANCE_MODELS_INTEGRATION_PLAN.md §阶段 2a.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_voices_excludes_doubao_when_access_key_unset(monkeypatch):
    """No VOLCENGINE_VOICE_ACCESS_KEY → no doubao voices listed (they'd all fail)."""
    monkeypatch.delenv("VOLCENGINE_VOICE_ACCESS_KEY", raising=False)
    result = await list_voices_endpoint(lang=None)
    doubao = [v for v in result if v.get("provider") == "doubao"]
    assert len(doubao) == 0


@pytest.mark.asyncio
async def test_voices_includes_doubao_when_access_key_set(monkeypatch):
    """access_key set → doubao voices listed (they work)."""
    monkeypatch.setenv("VOLCENGINE_VOICE_ACCESS_KEY", "k")
    result = await list_voices_endpoint(lang=None)
    doubao = [v for v in result if v.get("provider") == "doubao"]
    assert len(doubao) == 4


@pytest.mark.asyncio
async def test_voices_doubao_whitelist_filters_speakers(monkeypatch):
    """Optional DOUBAO_TTS_ENABLED_SPEAKERS whitelist → only provisioned doubao speakers listed."""
    monkeypatch.setenv("VOLCENGINE_VOICE_ACCESS_KEY", "k")
    monkeypatch.setenv("DOUBAO_TTS_ENABLED_SPEAKERS", "zh_female_vv_uranus_bigtts,zh_male_m191_uranus_bigtts")
    result = await list_voices_endpoint(lang=None)
    doubao = [v for v in result if v.get("provider") == "doubao"]
    assert len(doubao) == 2
    ids = {v["id"] for v in doubao}
    assert "doubao:zh_female_vv_uranus_bigtts" in ids
    assert "doubao:zh_male_m191_uranus_bigtts" in ids


@pytest.mark.asyncio
async def test_voices_hides_qihu_when_no_api_key(monkeypatch):
    """无 QIHU_API_KEY → /voices 不列 360 音色（必失败，链末端 edge 国内不可达）；doubao 可见作对照。"""
    monkeypatch.delenv("QIHU_API_KEY", raising=False)
    monkeypatch.setenv("VOLCENGINE_VOICE_ACCESS_KEY", "k")  # doubao 可见作对照
    result = await list_voices_endpoint(lang=None)
    providers = {v.get("provider") for v in result}
    assert "qihu" not in providers
    assert "doubao" in providers
