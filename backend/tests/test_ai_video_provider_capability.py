"""R3: supports_image_urls 能力标记 + Throttled/Fallback 透传（修 Finding 3）。"""

from unittest.mock import MagicMock

from app.gateway.video_gen.providers.registry import FallbackAIVideoProvider
from app.gateway.video_gen.providers.seedance_ark import SeedanceArkVideoProvider
from app.gateway.video_gen.providers.throttle import ThrottledAIVideoProvider


def test_seedance_ark_supports_image_urls() -> None:
    assert SeedanceArkVideoProvider.supports_image_urls is True


def test_throttled_forwards_supports_image_urls() -> None:
    inner = SeedanceArkVideoProvider(api_key="x")
    throttled = ThrottledAIVideoProvider(inner, qps=1.0, max_retries=1)
    assert throttled.supports_image_urls is True


def test_throttled_defaults_false_for_unmarked_provider() -> None:
    """未声明 supports_image_urls 的 provider（如 qihu）→ False。"""
    inner = MagicMock()
    inner.supports_image_urls = False  # 显式模拟 qihu（未标记）
    throttled = ThrottledAIVideoProvider(inner, qps=1.0, max_retries=1)
    assert throttled.supports_image_urls is False


def test_fallback_forwards_primary_capability() -> None:
    primary = SeedanceArkVideoProvider(api_key="x")
    fallback = MagicMock()
    fallback.supports_image_urls = False
    wrapped = FallbackAIVideoProvider(primary, fallback)
    assert wrapped.supports_image_urls is True  # primary 的能力
