"""E12: MixedVisualSource 须传播子源失败（与 ai_image fail-loud 一致，不静默丢帧）。"""

import pytest

from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame


class _FlakySource:
    async def render(self, storyboard, *, only_frames=None, on_frame_done=None, task_dir=None, reference_image_paths=None):
        raise RuntimeError("sub-source boom")


class _OkSource:
    async def render(self, storyboard, *, only_frames=None, on_frame_done=None, task_dir=None, reference_image_paths=None):
        return None


def _storyboard():
    cfg = StoryboardConfig(video_type="ai_image", width=1080, height=1920)
    frames = [StoryboardFrame(index=0, narration="n")]
    return Storyboard(title="t", config=cfg, frames=frames)


@pytest.mark.asyncio
async def test_mixed_propagates_subsource_failure():
    """子源整组抛异常 → mixed 须重抛（修前仅 logger.error 静默吞）。"""
    from app.gateway.video_gen.visual_sources.mixed import MixedVisualSource

    src = MixedVisualSource({"ai_image": _FlakySource()})
    with pytest.raises(RuntimeError, match="sub-source boom"):
        await src.render(_storyboard())


@pytest.mark.asyncio
async def test_mixed_ok_when_all_succeed():
    """全部子源成功 → 不抛（不回归）。"""
    from app.gateway.video_gen.visual_sources.mixed import MixedVisualSource

    src = MixedVisualSource({"ai_image": _OkSource()})
    await src.render(_storyboard())  # 不抛即通过


@pytest.mark.asyncio
async def test_mixed_failure_attributes_correct_source_when_other_skipped():
    """某 source 未注册被跳过、另一注册 source 失败 → 错误须指向真正的失败源（不因索引错位误报）。

    修前 ``list(groups.keys())[i]`` 对齐 ``results``，但跳过的 source 不进 tasks/results，
    导致索引错位、错误消息误标为被跳过的 source_type。
    """
    from app.gateway.video_gen.visual_sources.mixed import MixedVisualSource

    # 两帧分别路由到不同 visual_source；"stock" 未注册会被跳过，"ai_image" 注册且失败
    cfg = StoryboardConfig(video_type="ai_image", width=1080, height=1920)
    frames = [
        StoryboardFrame(index=0, narration="n0", visual_source="stock"),
        StoryboardFrame(index=1, narration="n1", visual_source="ai_image"),
    ]
    sb = Storyboard(title="t", config=cfg, frames=frames)
    src = MixedVisualSource({"ai_image": _FlakySource()})  # stock 未注册

    with pytest.raises(RuntimeError) as exc:
        await src.render(sb)
    msg = str(exc.value)
    # 真正失败的是 ai_image，错误消息必须含 ai_image、不得误报被跳过的 stock
    assert "ai_image" in msg, f"错误须归因到失败源 ai_image：{msg}"
    assert "stock" not in msg, f"不得误报被跳过的 source stock：{msg}"
