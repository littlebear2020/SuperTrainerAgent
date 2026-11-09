"""Task 7: 画面词任何失败路径都不回退中文 narration（narration 直发 seedance = 画面词退化）。"""

from __future__ import annotations

import pytest

from app.gateway.video_gen.orchestration.storyboard_planner import (
    SceneGroup,
    Storyboard,
    StoryboardConfig,
    StoryboardFrame,
    _plan_course_batched,
    _topic_fallback_prompt,
)


class _ShortLLM:
    """模拟 LLM 早停：3 帧只回 1 个 prompt。"""

    async def ainvoke(self, messages):
        import types

        return types.SimpleNamespace(content='{"image_prompts": ["only one prompt"]}')


@pytest.mark.asyncio
async def test_batched_path_never_falls_back_to_narration():
    sb = Storyboard(
        title="人工智能导论",
        config=StoryboardConfig(video_type="ai_video", width=1920, height=1080),
        frames=[
            StoryboardFrame(index=0, narration="第一段旁白"),
            StoryboardFrame(index=1, narration="第二段旁白"),
            StoryboardFrame(index=2, narration="第三段旁白"),
        ],
        scene_groups=[SceneGroup(index=0, title="g", frame_indices=[0, 1, 2])],
    )
    await _plan_course_batched(_ShortLLM(), sb, "")
    for f in sb.frames:
        assert f.image_prompt, "漏帧不允许留空"
        assert f.image_prompt != f.narration, f"帧 {f.index} 回退了 narration"


@pytest.mark.asyncio
async def test_robust_fill_never_leaves_empty_or_narration(monkeypatch):
    """路径 (b)：_fill_image_prompts_robust bulk + 单帧兜底全失败 → 主题意象兜底，绝不留空
    （留空会被下游 `or narration` 吞成 narration）。"""
    from app.gateway.video_gen.orchestration import storyboard_planner as sp

    async def empty_bulk(llm, storyboard, style_prefix, **kw):
        return [], []

    async def empty_single(llm, narrations, **kw):
        return []

    monkeypatch.setattr(sp, "_generate_course_prompts_enhanced", empty_bulk)
    monkeypatch.setattr(sp, "_generate_image_prompts", empty_single)

    sb = Storyboard(
        title="人工智能导论",
        config=StoryboardConfig(video_type="ai_image", width=1920, height=1080),
        frames=[
            StoryboardFrame(index=0, narration="旁白甲"),
            StoryboardFrame(index=1, narration="旁白乙"),
        ],
    )
    await sp._fill_image_prompts_robust(None, sb, "")
    for f in sb.frames:
        assert f.image_prompt, "漏帧不允许留空"
        assert f.image_prompt != f.narration, f"帧 {f.index} 回退了 narration"
        assert "人工智能导论" in f.image_prompt


def test_topic_fallback_prompt_is_visual_not_narration():
    sb = Storyboard(title="人工智能导论", config=StoryboardConfig(video_type="ai_video", width=1920, height=1080), frames=[])
    p = _topic_fallback_prompt(sb)
    assert "人工智能导论" in p
    assert len(p) > 20


@pytest.mark.asyncio
@pytest.mark.parametrize("empty_prompt", ["", None])
async def test_visual_source_prompt_guard(tmp_path, monkeypatch, empty_prompt):
    from app.gateway.video_gen.providers.base import VideoRef
    from app.gateway.video_gen.visual_sources import ai_video as ai_video_mod

    monkeypatch.setattr(ai_video_mod, "_LOCALIZE_RETRY_DELAYS", [0])  # 加速：本地化必败（stub URL）

    received: list[str] = []

    class Cap:
        supports_image_urls = True

        async def generate_video(self, prompt, **kwargs):
            received.append(prompt)
            return VideoRef(url="http://x/v.mp4", duration=5.0, source="stub")

    sb = Storyboard(
        title="人工智能导论",
        config=StoryboardConfig(video_type="ai_video", width=1920, height=1080),
        frames=[StoryboardFrame(index=0, narration="这段中文旁白绝不能当画面词", image_prompt=empty_prompt)],
    )
    src = ai_video_mod.AIVideoVisualSource(Cap())
    await src.render(sb, task_dir=tmp_path)
    assert received and received[0] != "这段中文旁白绝不能当画面词"
    assert "人工智能导论" in received[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("empty_prompt", ["", None])
async def test_ai_image_source_prompt_guard(tmp_path, monkeypatch, empty_prompt):
    """ai_image 消费端同一最后防线：image_prompt 空/None 也绝不把 narration 发给 provider。"""
    from app.gateway.video_gen.providers.base import ImageRef
    from app.gateway.video_gen.visual_sources import ai_image as ai_image_mod

    monkeypatch.setattr(ai_image_mod, "_LOCALIZE_RETRY_DELAYS", [0])

    received: list[str] = []

    class Cap:
        async def generate_image(self, prompt, **kwargs):
            received.append(prompt)
            return ImageRef(url="http://x/i.png", source="stub")

    sb = Storyboard(
        title="人工智能导论",
        config=StoryboardConfig(video_type="ai_image", width=1920, height=1080),
        frames=[StoryboardFrame(index=0, narration="这段中文旁白绝不能当画面词", image_prompt=empty_prompt)],
    )
    src = ai_image_mod.AIImageVisualSource(Cap())
    await src.render(sb, task_dir=tmp_path)
    assert received and received[0] != "这段中文旁白绝不能当画面词"
    assert "人工智能导论" in received[0]
