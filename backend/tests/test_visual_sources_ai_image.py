import pytest

from app.gateway.video_gen.providers.base import ImageRef
from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame


class _FakeImageProvider:
    def __init__(self, results: list[ImageRef]):
        self._results = list(results)
        self.calls: list[str] = []
        self.kwargs_calls: list[dict] = []

    async def generate_image(self, prompt: str, **kwargs):
        self.calls.append(prompt)
        self.kwargs_calls.append(kwargs)
        if self._results:
            return self._results.pop(0)
        raise RuntimeError("no more results")


@pytest.mark.asyncio
async def test_ai_image_fills_visual_url_per_frame():
    from app.gateway.video_gen.visual_sources.ai_image import AIImageVisualSource

    provider = _FakeImageProvider(
        [
            ImageRef(url="https://cdn/a.png", width=1152, height=2048, source="qihu"),
            ImageRef(url="https://cdn/b.png", width=1152, height=2048, source="qihu"),
        ]
    )
    src = AIImageVisualSource(provider)
    cfg = StoryboardConfig(video_type="ai_image", width=1080, height=1920)
    sb = Storyboard(
        title="t",
        config=cfg,
        frames=[
            StoryboardFrame(index=0, narration="日出", image_prompt="a golden sunrise", duration=5.0),
            StoryboardFrame(index=1, narration="攀登", image_prompt="a person climbing", duration=4.0),
        ],
    )
    await src.render(sb)
    assert sb.frames[0].visual_url == "https://cdn/a.png"
    assert sb.frames[0].visual_type == "image"
    assert sb.frames[1].visual_url == "https://cdn/b.png"
    assert sb.frames[1].visual_type == "image"
    assert provider.calls == ["a golden sunrise", "a person climbing"]


@pytest.mark.asyncio
async def test_ai_image_raises_on_all_frames_failure():
    from app.gateway.video_gen.visual_sources.ai_image import AIImageVisualSource

    provider = _FakeImageProvider([])
    src = AIImageVisualSource(provider)
    cfg = StoryboardConfig(video_type="ai_image", width=1080, height=1920)
    sb = Storyboard(
        title="t",
        config=cfg,
        frames=[
            StoryboardFrame(index=0, narration="a", image_prompt="test", duration=5.0),
        ],
    )
    # All frames failing → 0% coverage → coverage guard raises.
    with pytest.raises(RuntimeError, match="coverage too low"):
        await src.render(sb)


@pytest.mark.asyncio
async def test_ai_image_passes_size_from_storyboard():
    """AIImageVisualSource should pass width x height as size kwarg to provider."""
    from app.gateway.video_gen.visual_sources.ai_image import AIImageVisualSource

    provider = _FakeImageProvider([ImageRef(url="https://cdn/a.png", width=1920, height=1080, source="qihu")])
    src = AIImageVisualSource(provider)
    cfg = StoryboardConfig(video_type="ai_image", width=1920, height=1080)
    sb = Storyboard(
        title="t",
        config=cfg,
        frames=[StoryboardFrame(index=0, narration="a", image_prompt="test", duration=5.0)],
    )
    await src.render(sb)
    assert provider.kwargs_calls[0].get("size") == "1920x1080"


@pytest.mark.asyncio
async def test_ai_image_records_size_mapped_degradation(tmp_path):
    """D2 修复批（2026-09-23）：gpt-image-2.5 尺寸贴档映射 → degradations.json 留痕。

    provider 返回 ImageRef.size_mapped_from（白名单外尺寸被就近贴档）时，
    AIImageVisualSource 在 task_dir 写 image_size_mapped 条目——用户在任务详情
    可见「该帧尺寸被映射」，不是静默出图。
    """
    import json

    from app.gateway.video_gen.visual_sources.ai_image import AIImageVisualSource

    provider = _FakeImageProvider(
        [
            ImageRef(
                url="https://cdn/a.png",
                width=1536,
                height=1024,
                source="gpt-image-2.5-flare",
                size_mapped_from="800x640",
            )
        ]
    )
    src = AIImageVisualSource(provider)
    cfg = StoryboardConfig(video_type="ai_image", width=800, height=640)
    sb = Storyboard(
        title="t",
        config=cfg,
        frames=[StoryboardFrame(index=0, narration="一段", image_prompt="a poster", duration=4.0)],
    )
    await src.render(sb, task_dir=tmp_path)
    assert sb.frames[0].visual_url == "https://cdn/a.png"
    sidecar = json.loads((tmp_path / "degradations.json").read_text(encoding="utf-8"))
    entries = [i for i in sidecar["items"] if i.get("kind") == "image_size_mapped"]
    assert len(entries) == 1
    assert "800x640" in entries[0]["detail"] and "1536x1024" in entries[0]["detail"]
    assert entries[0]["frame_index"] == 0
