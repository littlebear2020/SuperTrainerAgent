"""AIImageVisualSource must fail loudly when too few frames succeed (coverage guard)."""

import pytest

from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame
from app.gateway.video_gen.visual_sources.ai_image import AIImageVisualSource


class _FlakyProvider:
    """Fails deterministically for even-indexed frames (retry won't recover)."""

    async def generate_image(self, prompt: str, **kwargs):
        # prompt = "frame N"（见 _storyboard）；按帧索引确定性失败，使 P0-4 layer 1
        # 重试不掩盖低 coverage——重试同一帧 N 仍失败（旧实现用 stateful self.calls，
        # 重试时 parity 翻转→偶发恢复→coverage 虚高不 raise）。
        n = int(prompt.rsplit(" ", 1)[-1])
        if n % 2 == 0:
            raise TimeoutError("Timeout on reading data from socket")
        from app.gateway.video_gen.providers.base import ImageRef

        return ImageRef(url=f"data:image/png;base64,{n}", width=10, height=10, source="qihu")


def _storyboard(n: int) -> Storyboard:
    cfg = StoryboardConfig(video_type="ai_image", width=720, height=1280)
    frames = [StoryboardFrame(index=i, narration=f"frame {i}", duration=3.0) for i in range(n)]
    return Storyboard(title="t", config=cfg, frames=frames)


@pytest.mark.asyncio
async def test_raises_when_coverage_below_threshold() -> None:
    # 10 frames, 5 succeed → 50% < 60% threshold → must raise
    src = AIImageVisualSource(_FlakyProvider(), concurrency=2)
    with pytest.raises(RuntimeError, match="coverage too low"):
        await src.render(_storyboard(10))


@pytest.mark.asyncio
async def test_passes_when_coverage_at_or_above_threshold() -> None:
    # Provider that always succeeds → 100% coverage → no raise
    class _Ok:
        async def generate_image(self, prompt: str, **kwargs):
            from app.gateway.video_gen.providers.base import ImageRef

            return ImageRef(url="data:image/png;base64,1", width=10, height=10, source="qihu")

    src = AIImageVisualSource(_Ok(), concurrency=2)
    sb = _storyboard(4)
    await src.render(sb)
    assert all(f.visual_url for f in sb.frames)
