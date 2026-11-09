"""Tests for GptImage2Provider — 360 openai/gpt-image-2 with reference-image support.

0.4 task: a reference-image-capable image provider (qihu_image.py is text-only).
Wire format per plan §决策确认「关键技术发现」: /v1/images/edits + JSON body
with images array. Exact real-API shape confirmed by 0.0 spike; these tests
pin the provider's behavior.
"""

import httpx
import pytest
import respx

from app.gateway.video_gen.providers.base import ImageRef


@respx.mock
@pytest.mark.asyncio
async def test_generate_image_with_reference_calls_edits_endpoint():
    from app.gateway.video_gen.providers.gpt_image2 import GptImage2Provider

    route = respx.post("https://api.360.cn/v1/images/edits").mock(return_value=httpx.Response(200, json={"data": [{"url": "https://cdn.360.cn/out.png"}]}))
    provider = GptImage2Provider(api_key="test-key")
    ref = await provider.generate_image(
        "a portrait matching the reference",
        reference_image_paths=["https://cdn.360.cn/ref1.png"],
    )
    assert route.called
    assert isinstance(ref, ImageRef)
    assert ref.url == "https://cdn.360.cn/out.png"
    assert ref.source == "gpt-image-2"


@respx.mock
@pytest.mark.asyncio
async def test_generate_image_with_reference_includes_images_in_body():
    import json

    from app.gateway.video_gen.providers.gpt_image2 import GptImage2Provider

    route = respx.post("https://api.360.cn/v1/images/edits").mock(return_value=httpx.Response(200, json={"data": [{"url": "https://cdn.360.cn/out.png"}]}))
    provider = GptImage2Provider(api_key="test-key")
    await provider.generate_image(
        "portrait",
        reference_image_paths=["https://cdn.360.cn/ref1.png", "https://cdn.360.cn/ref2.png"],
    )
    body = json.loads(route.calls.last.request.content.decode())
    assert body["model"] == "openai/gpt-image-2"
    assert body["prompt"] == "portrait"
    assert body["images"] == ["https://cdn.360.cn/ref1.png", "https://cdn.360.cn/ref2.png"]


@respx.mock
@pytest.mark.asyncio
async def test_generate_image_without_reference_calls_generations_endpoint():
    # D2 (2026-08-09 实测 3/3 成功)：纯文生图走 /v1/images/generations
    # （修前强制 edits 源于 0.0 spike「generations is broken」，已被本轮探针证伪）。
    from app.gateway.video_gen.providers.gpt_image2 import GptImage2Provider

    route = respx.post("https://api.360.cn/v1/images/generations").mock(return_value=httpx.Response(200, json={"data": [{"url": "https://cdn.360.cn/out.png"}]}))
    provider = GptImage2Provider(api_key="test-key")
    ref = await provider.generate_image("a sunset")
    assert route.called
    assert ref.url == "https://cdn.360.cn/out.png"


@respx.mock
@pytest.mark.asyncio
async def test_generate_image_without_reference_omits_images_field():
    import json

    from app.gateway.video_gen.providers.gpt_image2 import GptImage2Provider

    route = respx.post("https://api.360.cn/v1/images/generations").mock(return_value=httpx.Response(200, json={"data": [{"url": "https://cdn.360.cn/out.png"}]}))
    provider = GptImage2Provider(api_key="test-key")
    await provider.generate_image("a sunset")
    body = json.loads(route.calls.last.request.content.decode())
    assert "images" not in body  # no reference → no images key


@respx.mock
@pytest.mark.asyncio
async def test_generate_image_handles_b64_response():
    from app.gateway.video_gen.providers.gpt_image2 import GptImage2Provider

    respx.post("https://api.360.cn/v1/images/generations").mock(return_value=httpx.Response(200, json={"data": [{"b64_json": "iVBORw0KGgoAAAANSUhEUg=="}]}))
    provider = GptImage2Provider(api_key="test-key")
    ref = await provider.generate_image("test")
    assert ref.url.startswith("data:image/png;base64,")


@respx.mock
@pytest.mark.asyncio
async def test_generate_image_raises_on_rate_limit():
    from app.gateway.video_gen.providers.gpt_image2 import GptImage2Provider
    from app.gateway.video_gen.providers.throttle import RateLimitError

    respx.post("https://api.360.cn/v1/images/edits").mock(return_value=httpx.Response(429, text="rate limited"))
    provider = GptImage2Provider(api_key="test-key")
    with pytest.raises(RateLimitError):
        await provider.generate_image("test", reference_image_paths=["https://cdn.360.cn/ref.png"])


@respx.mock
@pytest.mark.asyncio
async def test_generate_image_local_file_reference_converted_to_b64(tmp_path):
    import json

    from app.gateway.video_gen.providers.gpt_image2 import GptImage2Provider

    ref_file = tmp_path / "portrait.png"
    ref_file.write_bytes(b"\x89PNG\r\n\x1a\nfake-png-bytes")

    route = respx.post("https://api.360.cn/v1/images/edits").mock(return_value=httpx.Response(200, json={"data": [{"url": "https://cdn.360.cn/out.png"}]}))
    provider = GptImage2Provider(api_key="test-key")
    await provider.generate_image("portrait", reference_image_paths=[str(ref_file)])
    body = json.loads(route.calls.last.request.content.decode())
    assert body["images"][0].startswith("data:image/png;base64,")


def test_registry_builds_gpt_image2_provider_from_qihu_credentials():
    """gpt-image-2 lives on the same 360 gateway as qihu → reuse qihu creds."""
    from app.gateway.video_gen.config import VideoGenConfig
    from app.gateway.video_gen.providers.registry import ProviderRegistry

    cfg = VideoGenConfig(
        qihu_api_key="test-key",
        ai_image_provider="gpt-image-2",
    )
    reg = ProviderRegistry(cfg)
    assert type(reg.ai_image).__name__ == "ThrottledAIImageProvider"
    assert type(reg.ai_image._inner).__name__ == "GptImage2Provider"
