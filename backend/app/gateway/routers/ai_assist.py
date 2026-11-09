"""AI Assist API router - lightweight LLM rewrite endpoint."""

from __future__ import annotations

import random
import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.gateway.content.assist_service import AssistRequest, AssistService
from deerflow.runtime.user_context import get_effective_user_id

router = APIRouter(prefix="/api/ai", tags=["AI Assist"])

# P2-5: assist 无任何限流，concurrency=50 且单次最多 16384 输出 token，
# 可被脚本无限刷。形状对齐 assets_router._check_user_rate，但**独立计数桶**
# --与生图共用会把交互式改写卡死在 5/min。
_ASSIST_RATE: dict[str, list[float]] = {}
_ASSIST_MAX_REQUESTS = 30
_ASSIST_WINDOW = 60


def _check_assist_rate(user_id: str) -> bool:
    now = time.time()
    ts = [t for t in _ASSIST_RATE.get(user_id, []) if now - t < _ASSIST_WINDOW]
    if len(ts) >= _ASSIST_MAX_REQUESTS:
        _ASSIST_RATE[user_id] = ts
        return False
    ts.append(now)
    _ASSIST_RATE[user_id] = ts
    return True


def _cleanup_assist_rate() -> None:
    now = time.time()
    for uid in [u for u, ts in _ASSIST_RATE.items() if not ts or all(now - t >= _ASSIST_WINDOW for t in ts)]:
        del _ASSIST_RATE[uid]


def _get_service(request: Request) -> AssistService:
    svc = getattr(request.app.state, "assist_service", None)
    if svc is None:
        raise HTTPException(status_code=503, detail="Assist service not available")
    return svc


@router.post("/assist")
async def assist(request: Request, body: AssistRequest):
    svc = _get_service(request)
    user_id = get_effective_user_id()
    if not _check_assist_rate(user_id):
        raise HTTPException(status_code=429, detail="AI 改写请求过于频繁（每分钟 30 次），请稍后再试")
    if random.random() < 0.01:
        _cleanup_assist_rate()
    return StreamingResponse(
        svc.stream(body, user_id=user_id, request=request),
        media_type="text/event-stream",
        # SSE must not be buffered by nginx (same as runs.py / thread_runs.py) (P1-3)
        headers={"X-Accel-Buffering": "no"},
    )
