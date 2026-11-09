"""T3: 放开 preview/ready 与 regenerate/failed 状态门控。

- preview: DRAFT 与 ready 均可（非这两态→409）
- regenerate: ready 与 failed 均可（非这两态→409）
- recompose: 已允许 ready+failed（不动，回归保护）
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException


def _svc_with_task(status: str, task_type: str = "ai_image") -> MagicMock:
    svc = MagicMock()
    svc.get_task = AsyncMock(return_value=MagicMock(status=status, task_type=task_type))
    # 条目 35：预览/重生 POST 入口新增失效重置调用（await 依赖须为 AsyncMock）
    svc._expire_stale_running_state = AsyncMock()
    return svc


@pytest.mark.asyncio
async def test_preview_allowed_on_ready(monkeypatch, tmp_path):
    """ready 任务可预览单帧（不再 409）；异步化（2026-07-21）后返回 started。"""
    import json

    from app.gateway.routers import video_gen as router_mod

    svc = _svc_with_task("ready")
    svc._storage_dir = str(tmp_path)
    task_dir = tmp_path / "u1" / "tasks" / "t1"
    task_dir.mkdir(parents=True)
    sb = {
        "title": "t",
        "config": {"video_type": "ai_image", "width": 1080, "height": 1920},
        "frames": [{"index": 0, "narration": "n0", "image_prompt": "p0"}],
    }
    (task_dir / "storyboard.json").write_text(json.dumps(sb), encoding="utf-8")
    monkeypatch.setattr(router_mod, "_get_service", lambda req: svc)
    monkeypatch.setattr(router_mod, "get_effective_user_id", lambda: "u1")

    async def _no_bg(*a):
        return None

    monkeypatch.setattr(router_mod, "_preview_frame_bg", _no_bg)
    router_mod._preview_inflight.clear()
    resp = await router_mod.preview_frame_visual(MagicMock(), "t1", 0)
    assert resp == {"status": "started"}
    router_mod._preview_inflight.clear()


@pytest.mark.asyncio
async def test_preview_rejected_on_composing(monkeypatch):
    """composing 态预览→409（仍不允许）。"""
    from app.gateway.routers import video_gen as router_mod

    svc = _svc_with_task("composing")
    monkeypatch.setattr(router_mod, "_get_service", lambda req: svc)
    monkeypatch.setattr(router_mod, "get_effective_user_id", lambda: "u1")

    with pytest.raises(HTTPException) as exc:
        await router_mod.preview_frame_visual(MagicMock(), "t1", 0)
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_regenerate_allowed_on_failed(monkeypatch, tmp_path):
    """failed 任务可重生单帧（不再 409）；202 异步化（Task 4/H4）后返回 started。"""
    from app.gateway.routers import video_gen as router_mod

    svc = _svc_with_task("failed", task_type="ai_image")
    svc._storage_dir = str(tmp_path)
    # regenerate 端点查 _storage_dir/user_id/tasks/task_id/storyboard.json
    task_dir = tmp_path / "u1" / "tasks" / "t1"
    task_dir.mkdir(parents=True)
    # 202 契约：frame_index 范围校验前移为同步 400，需至少一帧才进入启动分支
    (task_dir / "storyboard.json").write_text('{"frames":[{"index":0,"narration":"x"}]}')
    monkeypatch.setattr(router_mod, "_get_service", lambda req: svc)
    monkeypatch.setattr(router_mod, "get_effective_user_id", lambda: "u1")

    async def _no_bg(*a):
        return None

    monkeypatch.setattr(router_mod, "_regenerate_frame_bg", _no_bg)
    router_mod._regen_inflight.clear()
    resp = await router_mod.regenerate_frame(MagicMock(), "t1", 0)
    # 2026-07-27 起响应回显 mode（all/audio_only/visual_only，默认 all）
    assert resp == {"status": "started", "mode": "all"}
    router_mod._regen_inflight.clear()


@pytest.mark.asyncio
async def test_regenerate_rejected_on_pending(monkeypatch, tmp_path):
    """pending 态重生→409（仍不允许）。"""
    from app.gateway.routers import video_gen as router_mod

    svc = _svc_with_task("pending")
    svc._storage_dir = str(tmp_path)
    monkeypatch.setattr(router_mod, "_get_service", lambda req: svc)
    monkeypatch.setattr(router_mod, "get_effective_user_id", lambda: "u1")

    with pytest.raises(HTTPException) as exc:
        await router_mod.regenerate_frame(MagicMock(), "t1", 0)
    assert exc.value.status_code == 409
