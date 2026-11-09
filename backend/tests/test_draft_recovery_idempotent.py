"""Task 2: gateway 重启重调度 DRAFT 任务时，磁盘 storyboard（含用户编辑）不被覆盖。"""

from __future__ import annotations

import json

import pytest

from app.gateway.video_gen.pipeline_result import PipelineDraft
from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame


def _sb_dict() -> dict:
    sb = Storyboard(
        title="用户改过的标题",
        config=StoryboardConfig(video_type="ai_image", width=1920, height=1080),
        frames=[StoryboardFrame(index=0, narration="用户编辑后的旁白")],
    )
    return sb.to_dict()


@pytest.mark.asyncio
async def test_draft_branch_reuses_disk_storyboard(tmp_path, monkeypatch):
    from app.gateway.video_gen.service import VideoGenService

    svc = VideoGenService.__new__(VideoGenService)  # 绕过 __init__ 重依赖
    svc._storage_dir = str(tmp_path)  # _run_pipeline 入口 _inject_project_character_context 读取

    # 若 _generate_draft 被调用即失败——恢复路径绝不允许重生成
    async def _boom(*a, **k):
        raise AssertionError("_generate_draft must NOT be called when storyboard.json exists")

    async def _noop_audio(*a, **k):
        return None

    monkeypatch.setattr(svc, "_generate_draft", _boom)
    monkeypatch.setattr(svc, "_generate_draft_audio", _noop_audio)
    (tmp_path / "storyboard.json").write_text(json.dumps(_sb_dict(), ensure_ascii=False), encoding="utf-8")

    async def progress(**kwargs):
        return None

    result = await svc._run_pipeline(
        "vg_test123",
        "default",
        "ai_image",
        {"auto_render": False, "mode": "fixed", "script": "原始脚本"},
        tmp_path,
        progress,
    )
    assert isinstance(result, PipelineDraft)
    assert result.storyboard["title"] == "用户改过的标题"
    assert result.storyboard["frames"][0]["narration"] == "用户编辑后的旁白"


@pytest.mark.asyncio
async def test_idea2video_story_txt_reused(tmp_path, monkeypatch):
    from app.gateway.video_gen.service import VideoGenService

    svc = VideoGenService.__new__(VideoGenService)
    svc._storage_dir = str(tmp_path)

    async def _boom(*a, **k):
        raise AssertionError("_generate_draft must NOT be called when story.txt exists")

    monkeypatch.setattr(svc, "_generate_draft", _boom)
    (tmp_path / "story.txt").write_text("确认过的故事", encoding="utf-8")

    async def progress(**kwargs):
        return None

    result = await svc._run_pipeline(
        "vg_test456",
        "default",
        "vimax",
        {"auto_render": False, "pipeline": "idea2video", "topic": "原始 idea"},
        tmp_path,
        progress,
    )
    assert isinstance(result, PipelineDraft)
    assert result.story == "确认过的故事"
