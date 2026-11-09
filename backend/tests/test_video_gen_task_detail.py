"""GET /tasks/{id} 必须返回 payload+result，否则前端脚本/ViMax阶段不渲染。"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest


def _fake_task(*, payload: dict, result: dict) -> MagicMock:
    """Build a task object with payload/result as JSON strings (as stored in DB)."""
    t = MagicMock()
    t.id = "t1"
    t.user_id = "u1"
    t.domain = "video"
    t.task_type = "ai_image"
    t.status = "ready"
    t.progress = 100
    t.title = None
    t.error_message = None
    t.retry_count = 0
    t.dirty_frames = "[]"
    t.created_at = None
    t.updated_at = None
    t.thumbnail_url = None
    t.amount_source = None  # 2026-08-27 字段：mock 未跟上过不了 str 校验（2026-09-14 基线债修复）
    t.cost = None
    t.currency = None
    t.payload = json.dumps(payload, ensure_ascii=False)
    t.result = json.dumps(result, ensure_ascii=False)
    return t


@pytest.mark.asyncio
async def test_get_task_returns_payload_and_result(monkeypatch):
    """detail 端点返回 payload+result（非 TaskBrief 的空 {}）。"""
    from app.gateway.routers import video_gen as router_mod

    fake_task = _fake_task(
        payload={"script": "测试脚本", "video_type": "ai_image"},
        result={"output_path": "/out/output.mp4"},
    )
    svc = MagicMock()
    svc.get_task = AsyncMock(return_value=fake_task)
    # T13 队位查询为 async（MagicMock 不可 await——2026-09-14 基线债修复）
    svc.get_task_with_queue_position = AsyncMock(return_value=fake_task)
    request = MagicMock()
    monkeypatch.setattr(router_mod, "_get_service", lambda req: svc)
    monkeypatch.setattr(router_mod, "get_effective_user_id", lambda: "u1")

    resp = await router_mod.get_video_task(request, "t1")
    data = resp if isinstance(resp, dict) else resp.model_dump()
    assert data.get("payload") == {"script": "测试脚本", "video_type": "ai_image"}, "payload 被剥，前端脚本不渲染"
    # Task 18-20: detail 端点会给 result 附着 subtitle_status/duration_warning，按关键字段取值而非全等
    assert data.get("result", {}).get("output_path") == "/out/output.mp4", "result 被剥，ViMax阶段指示器不渲染"
