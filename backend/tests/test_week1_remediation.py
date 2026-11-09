"""Tests for Week 1 remediation: SceneGroup visual_theme, enhanced plan_course, batch, chapter_script."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.gateway.video_gen.storyboard import SceneGroup, Storyboard, StoryboardConfig, StoryboardFrame


def test_scene_group_has_visual_theme():
    """SceneGroup should have visual_theme field (plan section 4.2)."""
    g = SceneGroup(index=0, title="核心概念", frame_indices=[0, 1])
    assert hasattr(g, "visual_theme")
    assert g.visual_theme is None  # default


def test_scene_group_visual_theme_roundtrip():
    """visual_theme should survive to_dict/from_dict."""
    sb = Storyboard(
        title="test",
        config=StoryboardConfig(video_type="ai_image", width=1080, height=1920),
        frames=[StoryboardFrame(index=0, narration="a")],
        scene_groups=[SceneGroup(index=0, title="段1", frame_indices=[0], visual_theme="概念图示")],
    )
    data = sb.to_dict()
    assert data["scene_groups"][0]["visual_theme"] == "概念图示"
    sb2 = Storyboard.from_dict(data)
    assert sb2.scene_groups[0].visual_theme == "概念图示"


@pytest.mark.asyncio
async def test_plan_course_sets_visual_source_per_frame():
    """plan_course should set visual_source on frames when using enhanced prompt."""
    from app.gateway.video_gen.orchestration import storyboard_planner as planner

    sb = Storyboard(
        title="测试课程",
        config=StoryboardConfig(video_type="ai_image", width=1920, height=1080),
        frames=[
            StoryboardFrame(index=0, narration="概念定义"),
            StoryboardFrame(index=1, narration="举例说明"),
        ],
        scene_groups=[
            SceneGroup(index=0, title="概念", frame_indices=[0]),
            SceneGroup(index=1, title="案例", frame_indices=[1]),
        ],
    )

    mock_llm = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.content = """```json
{"frames": [
    {"image_prompt": "diagram", "visual_source": "remotion"},
    {"image_prompt": "scene", "visual_source": "ai_image"}
]}
```"""
    mock_llm.ainvoke = AsyncMock(return_value=mock_resp)

    await planner.plan_course(mock_llm, storyboard=sb, style_context={}, chunk_size=8)  # E8: bulk 路径

    # P0-5 → content-match T5/D2（2026-08-14）：路由默认 on——推荐写回后由护栏收敛。
    # LLM 推荐 "remotion"（存量值）→ D8 枚举规则回退主引擎 "ai_image"。
    # off 态行为（写回被丢弃、保持 None）由 test_planner_content_routing.py 覆盖。
    assert sb.frames[0].visual_source == "ai_image"
    assert sb.frames[1].visual_source == "ai_image"
    # 3k: image_prompt 追加质量词后缀（"diagram" → "diagram, high quality, detailed"）
    assert sb.frames[0].image_prompt.startswith("diagram")
    assert sb.frames[1].image_prompt.startswith("scene")


@pytest.mark.asyncio
async def test_plan_course_batched_maps_indices_correctly():
    """_plan_course_batched should correctly map 0-based mini indices back to original frame indices."""
    from app.gateway.video_gen.orchestration import storyboard_planner as planner

    # Create 51 frames across 2 groups to trigger batch path
    frames = [StoryboardFrame(index=i, narration=f"旁白{i}") for i in range(51)]
    groups = [
        SceneGroup(index=0, title="第一部分", frame_indices=list(range(0, 30))),
        SceneGroup(index=1, title="第二部分", frame_indices=list(range(30, 51))),
    ]
    sb = Storyboard(
        title="大课程",
        config=StoryboardConfig(video_type="ai_image", width=1920, height=1080),
        frames=frames,
        scene_groups=groups,
    )

    # Mock LLM that returns enhanced prompts per group
    call_count = 0

    async def mock_ainvoke(messages):
        nonlocal call_count
        resp = MagicMock()
        if call_count == 0:
            # T5（2026-08-18）：第 1 次调用是 plan_course 分块前的全片锚定调用
            resp.content = '{"anchor_card": "锚定卡", "style_string": "风格串"}'
            call_count += 1
            return resp
        call_count += 1
        # Second call: 30 frames, third call: 21 frames（组序 = call_count - 1，锚定不占组号）
        group_idx = call_count - 1
        n = 30 if group_idx == 1 else 21
        frame_json = ", ".join(f'{{"image_prompt": "prompt_{group_idx}_{i}", "visual_source": "ai_image"}}' for i in range(n))
        resp.content = f'{{"frames": [{frame_json}]}}'
        return resp

    mock_llm = AsyncMock()
    mock_llm.ainvoke = mock_ainvoke

    await planner.plan_course(mock_llm, storyboard=sb, style_context={})

    # Verify mapping: frame 0 from group 0 → prompt_1_0（3k 追加质量词后缀，用 startswith）
    assert sb.frames[0].image_prompt.startswith("prompt_1_0")
    # Frame 29 (last in group 0) → prompt_1_29
    assert sb.frames[29].image_prompt.startswith("prompt_1_29")
    # Frame 30 (first in group 1) → prompt_2_0
    assert sb.frames[30].image_prompt.startswith("prompt_2_0")
    # Frame 50 (last in group 1) → prompt_2_20
    assert sb.frames[50].image_prompt.startswith("prompt_2_20")
    # All should have visual_source set
    # P0-5 → content-match T5/D2（2026-08-14）：路由默认 on——推荐写回（值==主引擎，护栏不动）
    assert all(f.visual_source == "ai_image" for f in sb.frames)


@pytest.mark.asyncio
async def test_plan_course_batched_missing_frames_topic_fallback():
    """QP1-6 新语义 + BUG 1-A：_plan_course_batched LLM 返回条数不一致（10/51）
    -> 整组不可信，全部帧降级单帧重试。单帧重试也解析不出 image_prompts 键
    （返回的是 frames 键）-> 主题意象兜底。"""
    from app.gateway.video_gen.orchestration import storyboard_planner as planner

    frames = [StoryboardFrame(index=i, narration=f"旁白{i}") for i in range(51)]
    groups = [
        SceneGroup(index=0, title="第一部分", frame_indices=list(range(0, 51))),
    ]
    sb = Storyboard(
        title="大课程",
        config=StoryboardConfig(video_type="ai_image", width=1920, height=1080),
        frames=frames,
        scene_groups=groups,
    )

    # Mock LLM that returns only 10 prompts (fewer than 51 frames) -> BUG 1-A 判定整组未对齐
    resp = MagicMock()
    frame_json = ", ".join(f'{{"image_prompt": "p{i}", "visual_source": "ai_image"}}' for i in range(10))
    resp.content = f'{{"frames": [{frame_json}]}}'

    mock_llm = AsyncMock()
    mock_llm.ainvoke = AsyncMock(return_value=resp)

    await planner.plan_course(mock_llm, storyboard=sb, style_context={})

    # BUG 1-A：10 != 51 -> 整组降级单帧重试。单帧重试也失败（mock 返 frames 键非 image_prompts）
    # -> 全部帧由 QP1-6 主题意象兜底（含课程标题），绝不回退 narration
    for i in range(51):
        assert sb.frames[i].image_prompt, f"帧 {i} 不允许留空"
        assert sb.frames[i].image_prompt != sb.frames[i].narration, f"帧 {i} 回退了 narration"
        assert "大课程" in sb.frames[i].image_prompt, f"帧 {i} 应由主题意象兜底"


@pytest.mark.asyncio
async def test_generate_chapter_script_on_pipeline():
    """CoursePipelineService should have generate_chapter_script method."""
    from app.gateway.video_gen.course_models import OutlineChapter
    from app.gateway.video_gen.course_pipeline import CoursePipelineService

    svc = CoursePipelineService()
    mock_llm = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.content = "这是第一段旁白。\n\n这是第二段旁白。"
    mock_llm.ainvoke = AsyncMock(return_value=mock_resp)

    chapter = OutlineChapter(title="领导力基础", key_points=["定义", "要素"], estimated_minutes=3)
    script = await svc.generate_chapter_script(mock_llm, chapter=chapter)

    assert isinstance(script, str)
    assert len(script) > 0
    assert "\n\n" in script
