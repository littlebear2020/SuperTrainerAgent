"""P0-3: mixed 任务里 ai_image 覆盖率只算自己的帧，且不越界复用。"""

import pytest

from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame
from app.gateway.video_gen.visual_sources.ai_image import AIImageVisualSource


class _OkProvider:
    """手写 stub（非 MagicMock）--避免包裹链属性穿透陷阱。"""

    async def generate_image(self, prompt, **kwargs):
        from app.gateway.video_gen.providers.base import ImageRef

        return ImageRef(url="http://img/ok.png")


def _mixed_sb(n_image: int, n_video: int) -> Storyboard:
    cfg = StoryboardConfig(video_type="ai_image", width=1080, height=1920)
    frames = [StoryboardFrame(index=i, narration=f"n{i}", duration=3.0, image_prompt=f"p{i}", visual_source="ai_image") for i in range(n_image)] + [
        StoryboardFrame(index=n_image + i, narration=f"v{i}", duration=3.0, image_prompt=f"vp{i}", visual_source="ai_video") for i in range(n_video)
    ]
    return Storyboard(title="t", config=cfg, frames=frames)


@pytest.mark.asyncio
async def test_mixed_coverage_ignores_ai_video_frames():
    """5 ai_image 全成功 + 5 ai_video 未渲染 -> 不得抛（修前 50% < 60% 必抛）。"""
    sb = _mixed_sb(5, 5)
    src = AIImageVisualSource(_OkProvider(), concurrency=2)
    await src.render(sb, only_frames=[0, 1, 2, 3, 4], task_dir=None)
    assert all(f.visual_url for f in sb.frames[:5])


@pytest.mark.asyncio
async def test_image_reuse_does_not_touch_ai_video_frames():
    """覆盖率通过时，ai_image 不得把自己的图复用到 ai_video 帧上。"""
    sb = _mixed_sb(8, 2)
    src = AIImageVisualSource(_OkProvider(), concurrency=2)
    await src.render(sb, only_frames=list(range(8)), task_dir=None)
    for f in sb.frames[8:]:
        assert f.visual_url is None, f"ai_video 帧被 ai_image 越界写入：{f.visual_type}"
