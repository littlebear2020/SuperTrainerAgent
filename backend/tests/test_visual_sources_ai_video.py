import pytest

from app.gateway.video_gen.providers.base import VideoRef
from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame


class _FakeVideoProvider:
    def __init__(self, results: list[VideoRef]):
        self._results = list(results)
        self.calls: list[tuple[str, float, dict]] = []

    async def generate_video(self, prompt: str, *, duration: float = 8.0, **kwargs):
        self.calls.append((prompt, duration, kwargs))
        if self._results:
            return self._results.pop(0)
        raise RuntimeError("no more results")


@pytest.mark.asyncio
async def test_ai_video_fills_visual_url_per_frame():
    from app.gateway.video_gen.visual_sources.ai_video import AIVideoVisualSource

    provider = _FakeVideoProvider(
        [
            VideoRef(url="https://cdn/a.mp4", duration=8.0, source="qihu"),
            VideoRef(url="https://cdn/b.mp4", duration=8.0, source="qihu"),
        ]
    )
    src = AIVideoVisualSource(provider)
    cfg = StoryboardConfig(video_type="ai_video", width=1080, height=1920)
    sb = Storyboard(
        title="t",
        config=cfg,
        frames=[
            StoryboardFrame(index=0, narration="猫咪", image_prompt="a cat running", duration=8.0),
            StoryboardFrame(index=1, narration="水流", image_prompt="water flowing", duration=8.0),
        ],
    )
    await src.render(sb)
    assert sb.frames[0].visual_url == "https://cdn/a.mp4"
    assert sb.frames[0].visual_type == "video"
    assert sb.frames[1].visual_url == "https://cdn/b.mp4"
    assert sb.frames[1].visual_type == "video"


@pytest.mark.asyncio
async def test_ai_video_caps_duration_at_8s():
    from app.gateway.video_gen.visual_sources.ai_video import AIVideoVisualSource

    provider = _FakeVideoProvider(
        [
            VideoRef(url="https://cdn/a.mp4", duration=8.0, source="qihu"),
        ]
    )
    src = AIVideoVisualSource(provider)
    cfg = StoryboardConfig(video_type="ai_video", width=1080, height=1920)
    sb = Storyboard(
        title="t",
        config=cfg,
        frames=[
            StoryboardFrame(index=0, narration="long", image_prompt="test", duration=15.0),
        ],
    )
    await src.render(sb)
    # Should cap at 8.0
    assert provider.calls[0][1] == 8.0


@pytest.mark.asyncio
async def test_ai_video_raises_on_all_frames_failure():
    from app.gateway.video_gen.visual_sources.ai_video import AIVideoVisualSource

    provider = _FakeVideoProvider([])
    src = AIVideoVisualSource(provider)
    cfg = StoryboardConfig(video_type="ai_video", width=1080, height=1920)
    sb = Storyboard(
        title="t",
        config=cfg,
        frames=[
            StoryboardFrame(index=0, narration="a", image_prompt="test", duration=8.0),
        ],
    )
    # Task 9/B2：判据由「全部失败」改为覆盖率，报错文案随之变化（意图不变：全失败必抛）
    with pytest.raises(RuntimeError, match="coverage too low"):
        await src.render(sb)
