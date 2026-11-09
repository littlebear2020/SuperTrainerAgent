"""Task 15: /voices/default 返回与成品一致的解析默认音色（修前试听用 edge-tts，
成品用 doubao/preset:146——试听满意成品换声）。"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.gateway.routers.video_gen import router


@pytest.mark.asyncio
async def test_default_voice_zh_with_doubao_speaker(monkeypatch):
    monkeypatch.setenv("DOUBAO_TTS_DEFAULT_SPEAKER", "speaker_x")
    app = FastAPI()
    app.include_router(router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/api/video/voices/default", params={"lang": "zh"})
    assert resp.status_code == 200
    assert resp.json()["voice_id"] == "doubao:speaker_x"


@pytest.mark.asyncio
async def test_default_voice_zh_fallback_preset(monkeypatch):
    monkeypatch.delenv("DOUBAO_TTS_DEFAULT_SPEAKER", raising=False)
    app = FastAPI()
    app.include_router(router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/api/video/voices/default", params={"lang": "zh"})
    assert resp.json()["voice_id"] == "preset:146"
