from __future__ import annotations

import pytest

from app.gateway.routers.video_gen import list_voices_endpoint
from app.gateway.video_gen.orchestration.voices import CURATED_VOICES

DOUBAO_IDS = {
    "doubao:zh_female_vv_uranus_bigtts",
    "doubao:zh_female_yingyujiaoxue_uranus_bigtts",
    "doubao:zh_male_m191_uranus_bigtts",
    "doubao:zh_male_liufei_uranus_bigtts",
}


def test_curated_voices_structure_and_unique_ids():
    assert len(CURATED_VOICES) == 4
    assert {voice["id"] for voice in CURATED_VOICES} == DOUBAO_IDS
    for voice in CURATED_VOICES:
        assert voice["name"]
        assert voice["locale"] == "zh-CN"
        assert voice["gender"] in {"Male", "Female"}
        assert voice["provider"] == "doubao"


@pytest.mark.asyncio
async def test_voices_include_all_doubao_when_access_key_set(monkeypatch):
    monkeypatch.setenv("VOLCENGINE_VOICE_ACCESS_KEY", "k")
    monkeypatch.delenv("DOUBAO_TTS_ENABLED_SPEAKERS", raising=False)
    result = await list_voices_endpoint()
    assert {voice["id"] for voice in result} == DOUBAO_IDS


@pytest.mark.asyncio
async def test_voices_exclude_doubao_when_access_key_unset(monkeypatch):
    monkeypatch.delenv("VOLCENGINE_VOICE_ACCESS_KEY", raising=False)
    assert await list_voices_endpoint() == []


@pytest.mark.asyncio
async def test_voices_use_optional_doubao_whitelist(monkeypatch):
    monkeypatch.setenv("VOLCENGINE_VOICE_ACCESS_KEY", "k")
    monkeypatch.setenv("DOUBAO_TTS_ENABLED_SPEAKERS", "zh_female_vv_uranus_bigtts,zh_male_m191_uranus_bigtts")
    result = await list_voices_endpoint()
    assert {voice["id"] for voice in result} == {"doubao:zh_female_vv_uranus_bigtts", "doubao:zh_male_m191_uranus_bigtts"}


@pytest.mark.asyncio
async def test_voices_non_chinese_has_no_retired_edge_entries(monkeypatch):
    monkeypatch.setenv("VOLCENGINE_VOICE_ACCESS_KEY", "k")
    assert await list_voices_endpoint(lang="en") == []
