"""POST /api/ai/assist must disable nginx proxy buffering for SSE (P1-3).

Without ``X-Accel-Buffering: no`` the nginx /api/ catch-all buffers the whole
stream — the typewriter effect dies and >300s generations 504. runs.py and
thread_runs.py already set this header; ai_assist was the missing SSE endpoint.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest
from fastapi import FastAPI

from app.gateway.routers.ai_assist import router


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)

    async def _fake_stream(req, *, user_id: str, request=None):
        yield 'data: {"text": "x", "type": "text", "content": "x"}\n\n'
        yield "data: [DONE]\n\n"

    svc = MagicMock()
    svc.stream = _fake_stream
    app.state.assist_service = svc
    return app


@pytest.mark.asyncio
async def test_assist_sse_sets_x_accel_buffering_no():
    transport = httpx.ASGITransport(app=_make_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        async with client.stream(
            "POST",
            "/api/ai/assist",
            json={"op": "polish", "text": "你好"},
        ) as resp:
            assert resp.status_code == 200
            assert resp.headers.get("x-accel-buffering") == "no"
