"""Task 1（2026-10-20）：resume_draft 确认渲染固化全局镜头预算。

事故正路径：UI 创建 → 草稿（payload 只有 total_duration=238.06）→ 确认渲染。
修前 payload 无 target_duration / vimax_global_shot_budget → engine 派生不到预算
→ 单批 300s 反复超时。本测试钉死：resume_draft 在派发前读盘面 total_duration，
写回 vimax_global_shot_budget + vimax_budget_source；显式预算不被覆盖；
idea2video 不写（行为零变化）。
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.gateway._shared.task_framework.schemas import DRAFT
from app.gateway.video_gen.config import VideoGenConfig
from app.gateway.video_gen.providers.registry import ProviderRegistry
from app.gateway.video_gen.service import VideoGenService

USER_ID = "u1"
TASK_ID = "vg_budget"


def _make_service(tmp_path) -> VideoGenService:
    cfg = VideoGenConfig(qihu_api_key="t", script_model="m")
    return VideoGenService(
        repo=MagicMock(),
        config=cfg,
        providers=ProviderRegistry(cfg),
        storage_dir=str(tmp_path),
        sandbox_provider=AsyncMock(),
    )


def _seed_task(tmp_path, svc: VideoGenService, payload: dict, total_duration: float = 238.06) -> None:
    task = MagicMock()
    task.task_type = "vimax"
    task.status = DRAFT
    task.payload = json.dumps(payload, ensure_ascii=False)
    svc.repo.get = AsyncMock(return_value=task)
    svc.repo.update = AsyncMock()
    task_dir = tmp_path / USER_ID / "tasks" / TASK_ID
    task_dir.mkdir(parents=True)
    (task_dir / "storyboard.json").write_text(
        json.dumps(
            {
                "title": "t",
                "config": {"video_type": "vimax", "width": 1080, "height": 1920},
                "frames": [
                    {"index": 0, "narration": "第一句", "duration": 2.0},
                    {"index": 1, "narration": "第二句", "duration": 2.0},
                ],
                "total_duration": total_duration,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


async def _resume(svc: VideoGenService) -> dict:
    with patch.object(svc, "_wrap_pipeline", new=AsyncMock()), patch.object(svc, "_register_active_task", return_value=True):
        await svc.resume_draft(TASK_ID, USER_ID)
    assert svc.repo.update.await_count == 1
    return json.loads(svc.repo.update.call_args.kwargs["payload"])


@pytest.mark.asyncio
async def test_resume_draft_persists_storyboard_duration_budget(tmp_path):
    svc = _make_service(tmp_path)
    _seed_task(tmp_path, svc, {"mode": "fixed", "script": "原始脚本", "reuse_blueprint": True})

    payload = await _resume(svc)

    assert payload["vimax_global_shot_budget"] == 30
    assert payload["vimax_budget_source"] == "storyboard_duration"
    assert payload["reuse_blueprint"] is True


@pytest.mark.asyncio
async def test_resume_draft_keeps_explicit_budget(tmp_path):
    svc = _make_service(tmp_path)
    _seed_task(tmp_path, svc, {"mode": "fixed", "script": "原始脚本", "vimax_global_shot_budget": 19})

    payload = await _resume(svc)

    assert payload["vimax_global_shot_budget"] == 19
    assert payload["vimax_budget_source"] == "explicit"


@pytest.mark.asyncio
async def test_resume_draft_target_duration_beats_storyboard_duration(tmp_path):
    svc = _make_service(tmp_path)
    _seed_task(tmp_path, svc, {"mode": "fixed", "script": "原始脚本", "target_duration": 180})

    payload = await _resume(svc)

    assert payload["vimax_global_shot_budget"] == 24
    assert payload["vimax_budget_source"] == "target_duration"


@pytest.mark.asyncio
async def test_resume_draft_idea2video_does_not_write_budget(tmp_path):
    svc = _make_service(tmp_path)
    _seed_task(tmp_path, svc, {"mode": "generate", "idea": "一个想法", "pipeline": "idea2video"})

    payload = await _resume(svc)

    assert "vimax_global_shot_budget" not in payload
    assert "vimax_budget_source" not in payload
