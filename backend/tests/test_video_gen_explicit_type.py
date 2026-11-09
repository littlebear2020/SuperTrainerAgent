"""P0-5: 用户显式选 ai_image/ai_video 时，LLM 逐帧 visual_source 推荐不应写回 frame。

根因：storyboard_planner.plan_course 把 LLM 的逐帧 visual_source 推荐无条件写回
frame.visual_source，导致 _has_mixed_sources 判混合 → 掺入 AI 视频帧。
修复：显式选定 ai_image/ai_video 时不写回（用户手动改的保留）。
"""

from unittest.mock import MagicMock

import pytest

from app.gateway.video_gen.orchestration import storyboard_planner as planner
from app.gateway.video_gen.storyboard import (
    SceneGroup,
    Storyboard,
    StoryboardConfig,
    StoryboardFrame,
)


def _make_sb(video_type="ai_image", n=3):
    cfg = StoryboardConfig(video_type=video_type, width=720, height=1280)
    frames = [StoryboardFrame(index=i, narration=f"旁白{i}", duration=5.0, image_prompt=f"p{i}") for i in range(n)]
    return Storyboard(title="t", config=cfg, frames=frames)


@pytest.mark.asyncio
async def test_explicit_ai_image_not_overridden_by_llm(monkeypatch):
    """显式选 ai_image → LLM 即使推荐 ai_video 也不写回 frame.visual_source。"""
    sb = _make_sb("ai_image")

    async def fake_enhanced(llm, s, style_prefix):
        return ["p0", "p1", "p2"], ["ai_image", "ai_video", "ai_image"]

    monkeypatch.setattr(planner, "_generate_course_prompts_enhanced", fake_enhanced)

    await planner.plan_course(MagicMock(), storyboard=sb, style_context={})

    # 关键：所有 frame.visual_source 不应被 LLM 推荐污染为 ai_video
    for f in sb.frames:
        assert f.visual_source in (None, "ai_image"), f"frame{f.index} 被污染为 {f.visual_source}"


@pytest.mark.asyncio
async def test_explicit_ai_video_not_overridden_by_llm(monkeypatch):
    """显式选 ai_video → LLM 推荐的 ai_image 不写回。"""
    sb = _make_sb("ai_video", n=2)

    async def fake_enhanced(llm, s, style_prefix):
        return ["p0", "p1"], ["ai_image", "ai_video"]

    monkeypatch.setattr(planner, "_generate_course_prompts_enhanced", fake_enhanced)

    await planner.plan_course(MagicMock(), storyboard=sb, style_context={})

    for f in sb.frames:
        assert f.visual_source in (None, "ai_video"), f"frame{f.index} 被污染为 {f.visual_source}"


@pytest.mark.asyncio
async def test_manual_per_frame_source_preserved(monkeypatch):
    """用户在分镜页手动改过的 frame.visual_source 不应被清掉。"""
    sb = _make_sb("ai_image")
    sb.frames[1].visual_source = "ai_video"  # 用户手动改

    async def fake_enhanced(llm, s, style_prefix):
        return ["p0", "p1", "p2"], ["ai_image", "ai_image", "ai_image"]

    monkeypatch.setattr(planner, "_generate_course_prompts_enhanced", fake_enhanced)

    await planner.plan_course(MagicMock(), storyboard=sb, style_context={})

    assert sb.frames[1].visual_source == "ai_video"  # 手动改的保留


@pytest.mark.asyncio
async def test_batched_path_also_respects_explicit_type(monkeypatch):
    """批量路径（>50 帧 + scene_groups）同样不写回显式类型的 visual_source。"""
    n = 55
    sb = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_image", width=720, height=1280),
        frames=[StoryboardFrame(index=i, narration=f"n{i}", duration=5.0, image_prompt=f"p{i}") for i in range(n)],
        scene_groups=[
            SceneGroup(index=0, title="A", frame_indices=list(range(0, 30))),
            SceneGroup(index=1, title="B", frame_indices=list(range(30, 55))),
        ],
    )

    async def fake_enhanced(llm, s, style_prefix):
        count = len(s.frames)
        # LLM 给部分帧推荐 ai_video（应被忽略）
        sources = ["ai_video" if i % 5 == 0 else "ai_image" for i in range(count)]
        prompts = [f"p{i}" for i in range(count)]
        return prompts, sources

    monkeypatch.setattr(planner, "_generate_course_prompts_enhanced", fake_enhanced)

    await planner.plan_course(MagicMock(), storyboard=sb, style_context={})

    for f in sb.frames:
        assert f.visual_source in (None, "ai_image"), f"frame{f.index} 被污染为 {f.visual_source}"
