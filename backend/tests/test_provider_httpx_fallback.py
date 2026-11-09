"""P1-4: httpx 传输异常必须触发降级与重试（须穿透真实包裹链）。"""

import httpx
import pytest

from app.gateway.video_gen.providers.base import ImageRef
from app.gateway.video_gen.providers.registry import FallbackAIImageProvider
from app.gateway.video_gen.providers.throttle import ThrottledAIImageProvider


class _Boom:
    supports_image_urls = True

    def __init__(self, exc):
        self._exc = exc
        self.calls = 0

    async def generate_image(self, prompt, **kwargs):
        self.calls += 1
        raise self._exc


class _Ok:
    supports_image_urls = True

    def __init__(self):
        self.called = False

    async def generate_image(self, prompt, **kwargs):
        self.called = True
        return ImageRef(url="http://fb/ok.png")


@pytest.mark.parametrize("exc", [httpx.ConnectError("refused"), httpx.ReadTimeout("slow")])
@pytest.mark.asyncio
async def test_httpx_error_triggers_fallback_through_wrappers(exc):
    """必须走真实包裹链 Fallback(Throttled, Throttled)--只修 registry 会假绿。"""
    primary, fb = _Boom(exc), _Ok()
    p = FallbackAIImageProvider(
        ThrottledAIImageProvider(primary, qps=100.0, max_retries=1),
        ThrottledAIImageProvider(fb, qps=100.0, max_retries=1),
    )
    ref = await p.generate_image("x")
    assert ref.url == "http://fb/ok.png"
    assert fb.called, "httpx 异常未触发降级"
    assert primary.calls == 2, f"httpx 异常未被重试，实际调用 {primary.calls} 次"
