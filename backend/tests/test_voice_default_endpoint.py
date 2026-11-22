from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.gateway.routers.video_gen import router


@pytest.mark.asyncio
async def test_default_voice_zh_with_catalog_doubao_speaker(monkeypatch):
    monkeypatch.setenv("DOUBAO_TTS_DEFAULT_SPEAKER", "zh_male_m191_uranus_bigtts")
    app = FastAPI()
    app.include_router(router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        response = await client.get("/api/video/voices/default", params={"lang": "zh"})
    assert response.status_code == 200
    assert response.json()["voice_id"] == "doubao:zh_male_m191_uranus_bigtts"


@pytest.mark.asyncio
async def test_default_voice_zh_uses_vivi_without_env(monkeypatch):
    monkeypatch.delenv("DOUBAO_TTS_DEFAULT_SPEAKER", raising=False)
    app = FastAPI()
    app.include_router(router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        response = await client.get("/api/video/voices/default", params={"lang": "zh"})
    assert response.json()["voice_id"] == "doubao:zh_female_vv_uranus_bigtts"


@pytest.mark.parametrize("task_type", [None, "vimax"])
@pytest.mark.asyncio
async def test_default_voice_non_chinese_has_no_edge_fallback(task_type):
    app = FastAPI()
    app.include_router(router)
    params = {"lang": "en"}
    if task_type:
        params["task_type"] = task_type
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        response = await client.get("/api/video/voices/default", params=params)
    assert response.status_code == 404
