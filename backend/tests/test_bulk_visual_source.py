"""graphic-fix W2（2026-08-17）：任务级一键关闭图文卡（bulk 端点）。

- 全部 graphic 帧 visual_source→None；graphic_spec 保留（切回免重提取）
- ai_image/ai_video 帧不动；无 graphic 帧幂等返回 0
- 状态守卫 draft/failed/ready（composing 409）；未知 action 400；不标 pending
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.gateway.routers.video_gen import BulkVisualSourceRequest, bulk_visual_source


def _make_request(svc):
    class _Req:
        class app:
            class state:
                video_gen_service = svc

    return _Req()


def _make_service(tmp_path):
    from app.gateway.video_gen.config import VideoGenConfig
    from app.gateway.video_gen.providers.registry import ProviderRegistry
    from app.gateway.video_gen.service import VideoGenService

    cfg = VideoGenConfig(qihu_api_key="t")
    repo = MagicMock()
    repo.update = AsyncMock(return_value=None)
    return VideoGenService(
        repo=repo,
        config=cfg,
        providers=ProviderRegistry(cfg),
        storage_dir=str(tmp_path),
        sandbox_provider=AsyncMock(),
    )


def _seed(tmp_path, task_id="vg_w2", status="draft", sources=("graphic", "ai_image", "graphic")):
    task_dir = tmp_path / "test-user-autouse" / "tasks" / task_id
    task_dir.mkdir(parents=True)
    frames = []
    for i, s in enumerate(sources):
        f = {"index": i, "narration": f"n{i}", "image_prompt": f"p{i}", "visual_source": s}
        if s == "graphic":
            f["graphic_spec"] = {"type": "text_card", "title": "t", "points": ["甲", "乙"]}
        frames.append(f)
    (task_dir / "storyboard.json").write_text(json.dumps({"title": "t", "config": {}, "frames": frames}, ensure_ascii=False), encoding="utf-8")
    task = MagicMock()
    task.status = status
    task.task_type = "ai_image"
    task.payload = json.dumps({})
    task.dirty_frames = []
    return task_dir, task


@pytest.mark.asyncio
async def test_disable_graphic_flips_all_keeps_spec(tmp_path):
    svc = _make_service(tmp_path)
    task_dir, task = _seed(tmp_path)
    svc.get_task = AsyncMock(return_value=task)
    svc.touch_task = AsyncMock()

    resp = await bulk_visual_source(_make_request(svc), "vg_w2", BulkVisualSourceRequest(action="disable_graphic"))

    assert resp == {"disabled": 2}
    data = json.loads((task_dir / "storyboard.json").read_text(encoding="utf-8"))
    assert data["frames"][0]["visual_source"] is None and data["frames"][2]["visual_source"] is None, "graphic 帧全部回主引擎"
    assert data["frames"][1]["visual_source"] == "ai_image", "非 graphic 帧不动"
    specs = [f.get("graphic_spec") for f in data["frames"]]
    assert specs[0] is not None and specs[2] is not None, "graphic_spec 保留（切回免重提取）"
    assert "pending_regen_frames" not in data, "不标 pending（recompose 不误伤 409）"


@pytest.mark.asyncio
async def test_no_graphic_frames_idempotent_zero(tmp_path):
    svc = _make_service(tmp_path)
    _seed(tmp_path, sources=("ai_image", "ai_video"))
    task = MagicMock()
    task.status = "draft"
    svc.get_task = AsyncMock(return_value=task)
    svc.touch_task = AsyncMock()
    resp = await bulk_visual_source(_make_request(svc), "vg_w2", BulkVisualSourceRequest())
    assert resp == {"disabled": 0}


@pytest.mark.asyncio
async def test_composing_rejected_409(tmp_path):
    from fastapi import HTTPException

    svc = _make_service(tmp_path)
    _, task = _seed(tmp_path, status="composing")
    svc.get_task = AsyncMock(return_value=task)
    with pytest.raises(HTTPException) as ei:
        await bulk_visual_source(_make_request(svc), "vg_w2", BulkVisualSourceRequest())
    assert ei.value.status_code == 409


@pytest.mark.asyncio
async def test_unknown_action_400(tmp_path):
    from fastapi import HTTPException

    svc = _make_service(tmp_path)
    with pytest.raises(HTTPException) as ei:
        await bulk_visual_source(_make_request(svc), "vg_w2", BulkVisualSourceRequest(action="enable_graphic"))
    assert ei.value.status_code == 400
