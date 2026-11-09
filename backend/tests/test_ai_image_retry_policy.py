"""P0-4: 永久 4xx 不重试（成功率=0，重试只多扣一次费）；瞬态错误保留重试。"""

import pytest

from app.gateway.video_gen.providers.throttle import RateLimitError
from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame
from app.gateway.video_gen.visual_sources.ai_image import AIImageVisualSource


class _AlwaysFails:
    def __init__(self, exc):
        self._exc = exc
        self.calls = 0

    async def generate_image(self, prompt, **kwargs):
        self.calls += 1
        raise self._exc


class _CountingBudget:
    def __init__(self):
        self.charges = 0

    def charge(self):
        self.charges += 1


def _sb() -> Storyboard:
    cfg = StoryboardConfig(video_type="ai_image", width=1080, height=1920)
    return Storyboard(title="t", config=cfg, frames=[StoryboardFrame(index=0, narration="n", duration=3.0, image_prompt="p")])


@pytest.mark.asyncio
async def test_permanent_4xx_is_not_retried():
    prov = _AlwaysFails(RuntimeError("360 image API error (400): bad param"))
    budget = _CountingBudget()
    src = AIImageVisualSource(prov, concurrency=1, budget=budget)
    with pytest.raises(RuntimeError, match="coverage too low"):
        await src.render(_sb(), task_dir=None)
    assert prov.calls == 1, f"永久错误不得重试，实际调用 {prov.calls} 次"
    assert budget.charges == 1, f"只应扣 1 次费，实际 {budget.charges}"


@pytest.mark.asyncio
async def test_transient_429_is_retried():
    prov = _AlwaysFails(RateLimitError("360 image API rate limited"))
    budget = _CountingBudget()
    src = AIImageVisualSource(prov, concurrency=1, budget=budget)
    with pytest.raises(RuntimeError, match="coverage too low"):
        await src.render(_sb(), task_dir=None)
    assert prov.calls == 2, f"瞬态错误应重试一次，实际 {prov.calls}"
    assert budget.charges == 2
