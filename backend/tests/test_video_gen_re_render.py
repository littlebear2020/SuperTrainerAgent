"""Task 20c: 项目已更新 → 重新生成（reuse_blueprint=True 复用分镜重跑视觉 + force 刷新项目 refs）。

策略：改角色/风格仅影响之后新生成视频；已生成视频需手动「重新生成」。
重新生成时强制重跑视觉(reuse_blueprint 复用 storyboard 旁白/分镜 + 重新生图)，
force 刷新项目角色/风格覆盖 payload 过期快照，让新改动生效。
"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.gateway.video_gen.config import VideoGenConfig
from app.gateway.video_gen.providers.registry import ProviderRegistry
from app.gateway.video_gen.service import VideoGenService


def _cfg() -> VideoGenConfig:
    return VideoGenConfig(qihu_api_key="t")


def _make_service(tmp_path) -> VideoGenService:
    repo = MagicMock()
    repo.get = AsyncMock(
        return_value=MagicMock(
            status="ready",
            task_type="ai_image",
            payload=json.dumps({"course_project_id": "cp_dddddddddddd"}),
        )
    )
    return VideoGenService(
        repo=repo,
        config=_cfg(),
        providers=ProviderRegistry(_cfg()),
        storage_dir=str(tmp_path),
        sandbox_provider=AsyncMock(),
    )


def _write_project(tmp_path, front_url="/new.png") -> None:
    pdir = tmp_path / "course_projects" / "u1"
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "cp_dddddddddddd.json").write_text(
        json.dumps(
            {
                "id": "cp_dddddddddddd",
                "user_id": "u1",
                "title": "t",
                "character_sheets": [{"name": "小明", "front_url": front_url}],
            }
        ),
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_re_render_forces_visual_regen_and_project_refresh(tmp_path, monkeypatch):
    """ready 任务重新生成 → reuse_blueprint=True(复用分镜重跑视觉) + force 刷新项目 refs。

    不走 recompose_only(复用盘面视觉，不拾取新项目)，而是复用 storyboard 重新生图。
    """
    svc = _make_service(tmp_path)
    _write_project(tmp_path, front_url="/new.png")
    wrap = AsyncMock(return_value={"task_id": "t1", "status": "composing"})
    monkeypatch.setattr(svc, "_wrap_pipeline", wrap)

    await svc.re_render_task("t1", "u1")
    await svc._active_tasks["t1"]  # let fire-and-forget complete

    assert wrap.call_args is not None, "_wrap_pipeline 应被调度"
    payload = wrap.call_args.args[3]  # _wrap_pipeline(task_id, user_id, task_type, payload)
    assert payload.get("reuse_blueprint") is True, "应复用 storyboard(旁白/分镜可复用)"
    assert payload.get("auto_render") is True
    assert not payload.get("recompose_only"), "不应走 recompose(复用盘面视觉)"
    assert payload.get("frame_edits") == {}, "应触发 _render_from_draft 视觉重生分支"
    assert payload["character_sheets"][0]["front_url"] == "/new.png", "应 force 刷新项目 refs 覆盖过期快照"


@pytest.mark.asyncio
async def test_re_render_overwrites_stale_payload_sheets(tmp_path, monkeypatch):
    """Task 20c 核心：payload 里有过期 character_sheets → force 覆盖为项目最新值。"""
    svc = _make_service(tmp_path)
    _write_project(tmp_path, front_url="/fresh.png")
    # 模拟 payload 快照了旧角色（用户后来在项目改了角色）
    svc.repo.get = AsyncMock(
        return_value=MagicMock(
            status="ready",
            task_type="ai_image",
            payload=json.dumps({"course_project_id": "cp_dddddddddddd", "character_sheets": [{"front_url": "/stale.png"}]}),
        )
    )
    wrap = AsyncMock(return_value={"task_id": "t1", "status": "composing"})
    monkeypatch.setattr(svc, "_wrap_pipeline", wrap)

    await svc.re_render_task("t1", "u1")
    await svc._active_tasks["t1"]
    payload = wrap.call_args.args[3]
    assert payload["character_sheets"][0]["front_url"] == "/fresh.png", "force 应覆盖过期快照"


@pytest.mark.asyncio
async def test_re_render_rejects_non_ready(tmp_path):
    svc = _make_service(tmp_path)
    svc.repo.get = AsyncMock(return_value=MagicMock(status="draft", task_type="ai_image", payload="{}"))
    with pytest.raises(ValueError, match="ready|failed"):
        await svc.re_render_task("t1", "u1")


@pytest.mark.asyncio
async def test_re_render_rejects_vimax_remotion(tmp_path):
    svc = _make_service(tmp_path)
    svc.repo.get = AsyncMock(return_value=MagicMock(status="ready", task_type="vimax", payload="{}"))
    with pytest.raises(ValueError, match="segments|ai_image|unsupported"):
        await svc.re_render_task("t1", "u1")
