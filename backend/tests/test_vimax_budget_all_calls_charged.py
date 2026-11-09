"""P1-3: ViMax 视频调用逐次计费（堵 t2v 回退与 tenacity 重试的预算绕过）。"""

from unittest.mock import AsyncMock

import pytest
import tenacity

from app.gateway.video_gen.engines.vimax.provider_bridge import (
    SeedanceVideoGeneratorForViMax,
    VimaxBudgetExceeded,
)
from app.gateway.video_gen.providers.base import VideoRef


def _make_bridge(max_calls=None, max_cost_cny=None):
    """Create a SeedanceVideoGeneratorForViMax with mocked provider."""
    bridge = SeedanceVideoGeneratorForViMax.__new__(SeedanceVideoGeneratorForViMax)
    bridge._backend = "seedance-ark"
    bridge._provider = AsyncMock()
    bridge._call_count = 0
    bridge.cost_cny = 0.0
    bridge._max_calls = max_calls
    bridge._max_cost_cny = max_cost_cny
    bridge._aspect_ratio = None
    return bridge


@pytest.mark.asyncio
async def test_content_review_fallback_charges_twice():
    """i2v 拒绝 -> t2v 回退：应计 2 次费。"""
    bridge = _make_bridge()
    # First call (i2v) raises content review, second (t2v) succeeds
    bridge._provider.generate_video = AsyncMock(
        side_effect=[
            RuntimeError("green net check failed"),
            VideoRef(url="http://v/ok.mp4"),
        ]
    )

    await bridge.generate_single_video("prompt", reference_image_paths=["/tmp/x.png"])
    assert bridge._call_count == 2, f"t2v 回退应计 2 次，实际 {bridge._call_count}"
    assert bridge.cost_cny == 3.0, f"应扣 ¥3.0，实际 ¥{bridge.cost_cny}"


@pytest.mark.asyncio
async def test_max_calls_blocks_before_t2v_fallback():
    """max_calls=1 + 审核拒绝 -> t2v 回退前应抛 VimaxBudgetExceeded。"""
    bridge = _make_bridge(max_calls=1)
    bridge._provider.generate_video = AsyncMock(side_effect=[RuntimeError("green net check failed")])

    with pytest.raises(VimaxBudgetExceeded, match="call"):
        await bridge.generate_single_video("prompt", reference_image_paths=["/tmp/x.png"])
    assert bridge._provider.generate_video.await_count == 1, "t2v 回退不应执行"


@pytest.mark.asyncio
async def test_tenacity_retry_charges_each_attempt(monkeypatch):
    """provider 连抛 2 次 TimeoutError 后成功：应计 3 次。"""
    # wait_exponential(2, max=30) → 0：仍走满 3 次 attempt，只是不真睡 2s+4s。
    # 须 patch 装饰后方法上绑定的 retry 状态（import 时已定型，patch wait 工厂无效）。
    monkeypatch.setattr(SeedanceVideoGeneratorForViMax._generate_video_with_retry.retry, "wait", tenacity.wait_none())
    bridge = _make_bridge()
    bridge._provider.generate_video = AsyncMock(
        side_effect=[
            TimeoutError("slow"),
            TimeoutError("slow again"),
            VideoRef(url="http://v/ok.mp4"),
        ]
    )

    # Mock the semaphore to avoid actual async sem
    import app.gateway.video_gen.engines.vimax.provider_bridge as pb

    bridge._aspect_ratio = None

    # Need to mock _api_semaphore
    import contextlib

    @contextlib.asynccontextmanager
    async def fake_sem():
        yield

    original_sem = pb._api_semaphore
    pb._api_semaphore = fake_sem
    try:
        await bridge.generate_single_video("prompt")
    finally:
        pb._api_semaphore = original_sem

    assert bridge._call_count == 3, f"重试应各计 1 次，共 3 次，实际 {bridge._call_count}"
