"""Tests for the ByteDance Ark seedream image provider (Stage 4.1).

SeedreamImageProvider is a copy of QihuImageProvider's shape but pointed at the Ark
direct endpoint (ark.cn-beijing.volces.com/api/v3/images/generations) with Bearer
$ARK_API_KEY + the Ark-native model id doubao-seedream-5-0-pro-260628. Error
classification is the key difference from qihu: 429→RateLimitError, 5xx→
ProviderUnavailableError (so FallbackAIImageProvider degrades to qihu on infra
errors per requirement #4); 4xx stays RuntimeError (auth/content — no fallback).
See backend/docs/BYTEDANCE_MODELS_INTEGRATION_PLAN.md §阶段 4.1.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from app.gateway.video_gen.providers.base import ImageRef

_ARK_URL = "https://ark.cn-beijing.volces.com/api/v3/images/generations"


@respx.mock
@pytest.mark.asyncio
async def test_seedream_generate_image_returns_image_ref() -> None:
    from app.gateway.video_gen.providers.seedream import SeedreamImageProvider

    respx.post(_ARK_URL).mock(
        return_value=httpx.Response(200, json={"data": [{"url": "https://tos-seedream.example/img.png"}]}),
    )
    provider = SeedreamImageProvider(api_key="ark-key")
    ref = await provider.generate_image("a cat sitting on a windowsill")
    assert isinstance(ref, ImageRef)
    assert ref.url == "https://tos-seedream.example/img.png"
    assert ref.source == "seedream"


@respx.mock
@pytest.mark.asyncio
async def test_seedream_uses_ark_endpoint_bearer_and_native_model() -> None:
    from app.gateway.video_gen.providers.seedream import SeedreamImageProvider

    route = respx.post(_ARK_URL).mock(
        return_value=httpx.Response(200, json={"data": [{"url": "https://x.example/img.png"}]}),
    )
    provider = SeedreamImageProvider(api_key="ark-key-xxx")
    await provider.generate_image("a sunrise", size="1024x1024")
    assert route.called
    req = route.calls.last.request
    assert req.headers["authorization"] == "Bearer ark-key-xxx"
    body = json.loads(req.content.decode())
    assert body["model"] == "doubao-seedream-5-0-pro-260628"  # Ark-native id (no bytedance/ prefix)
    assert body["prompt"] == "a sunrise"
    assert body["size"] == "1024x1024"


@respx.mock
@pytest.mark.asyncio
async def test_seedream_handles_b64_response() -> None:
    from app.gateway.video_gen.providers.seedream import SeedreamImageProvider

    respx.post(_ARK_URL).mock(
        return_value=httpx.Response(200, json={"data": [{"b64_json": "iVBORw0KGgoAAAANSUhEUg=="}]}),
    )
    provider = SeedreamImageProvider(api_key="ark-key")
    ref = await provider.generate_image("test")
    assert ref.url.startswith("data:image/png;base64,")
    assert ref.source == "seedream"


@respx.mock
@pytest.mark.asyncio
async def test_seedream_429_raises_rate_limit_for_fallback() -> None:
    from app.gateway.video_gen.providers.seedream import SeedreamImageProvider
    from app.gateway.video_gen.providers.throttle import RateLimitError

    respx.post(_ARK_URL).mock(return_value=httpx.Response(429, text="rate limited"))
    provider = SeedreamImageProvider(api_key="ark-key")
    with pytest.raises(RateLimitError):
        await provider.generate_image("test")


@respx.mock
@pytest.mark.asyncio
async def test_seedream_5xx_raises_provider_unavailable_for_fallback() -> None:
    """5xx is infrastructure → ProviderUnavailableError, which is in _FALLBACK_EXC →
    FallbackAIImageProvider degrades to qihu (requirement #4)."""
    from app.gateway.video_gen.providers.seedream import SeedreamImageProvider
    from app.gateway.video_gen.providers.throttle import ProviderUnavailableError

    respx.post(_ARK_URL).mock(return_value=httpx.Response(503, text="unavailable"))
    provider = SeedreamImageProvider(api_key="ark-key")
    with pytest.raises(ProviderUnavailableError):
        await provider.generate_image("test")


@respx.mock
@pytest.mark.asyncio
async def test_seedream_4xx_raises_runtime_error_no_fallback() -> None:
    """4xx (auth/content) is NOT infrastructure → RuntimeError, NOT in _FALLBACK_EXC →
    no fallback (requirement #4: content review doesn't degrade)."""
    from app.gateway.video_gen.providers.seedream import SeedreamImageProvider

    respx.post(_ARK_URL).mock(return_value=httpx.Response(400, text="bad request"))
    provider = SeedreamImageProvider(api_key="ark-key")
    with pytest.raises(RuntimeError, match="400"):
        await provider.generate_image("test")


@respx.mock
@pytest.mark.asyncio
async def test_seedream_absorbs_reference_image_paths_kwarg() -> None:
    """General seedream path is text-only; reference_image_paths is absorbed + ignored
    (§4.1). ViMax passes it via the bridge generator (§4.2), not here."""
    from app.gateway.video_gen.providers.seedream import SeedreamImageProvider

    respx.post(_ARK_URL).mock(
        return_value=httpx.Response(200, json={"data": [{"url": "https://x.example/img.png"}]}),
    )
    provider = SeedreamImageProvider(api_key="ark-key")
    ref = await provider.generate_image("test", reference_image_paths=["/tmp/ref.png"])
    assert ref.source == "seedream"


def test_registry_builds_seedream_with_gpt_image_2_fallback() -> None:
    """Stage 4.1: ai_image_provider=seedream → FallbackAIImageProvider (primary seedream,
    fallback gpt-image-2). Previously _build_ai_image never assembled the fallback (dead code)."""
    from app.gateway.video_gen.config import VideoGenConfig
    from app.gateway.video_gen.providers.registry import FallbackAIImageProvider, ProviderRegistry

    cfg = VideoGenConfig(
        qihu_api_key="qihu-key",
        ark_api_key="ark-key",
        ai_image_provider="seedream",
    )
    reg = ProviderRegistry(cfg)
    assert isinstance(reg.ai_image, FallbackAIImageProvider)


def test_registry_seedream_no_ark_key_degrades_to_gpt_image_2_not_stub() -> None:
    """Usability (review 痛点2): ai_image_provider=seedream but ark_api_key empty → degrade to
    gpt-image-2 (360 降级备用), NOT _StubAIImageProvider/NotImplementedError. Consistent with TTS
    (key empty → 360) + the '360 降级备用' principle. Avoids the 'enabled seedream but forgot
    the key → all image tasks crash' trap."""
    from app.gateway.video_gen.config import VideoGenConfig
    from app.gateway.video_gen.providers.registry import ProviderRegistry

    cfg = VideoGenConfig(qihu_api_key="qihu-key", ark_api_key="", ai_image_provider="seedream")
    reg = ProviderRegistry(cfg)
    # Degrades to qihu (ThrottledAIImageProvider), NOT _StubAIImageProvider.
    assert type(reg.ai_image).__name__ == "ThrottledAIImageProvider"
