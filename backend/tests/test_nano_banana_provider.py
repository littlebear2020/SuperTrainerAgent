"""Tests for NanoBananaImageProvider — 360 google/gemini-3-pro-image-preview.

0.0 A/B spike verdict: Nano-Banana holds character identity across reference
editing (mild hairstyle drift); gpt-image-2 total identity failure (generated
different genders). Nano-Banana is ViMax's native backend → vimax image backend.

Wire format (confirmed by 0.0 real-API calls):
  - no reference → POST /v1/images/generations + extra_body.generation_config
  - with reference → POST /v1/images/edits + images:[url]
"""

import httpx
import pytest
import respx
import tenacity


@respx.mock
@pytest.mark.asyncio
async def test_generate_image_without_reference_calls_generations_with_generation_config():
    import json

    from app.gateway.video_gen.providers.nano_banana import NanoBananaImageProvider

    route = respx.post("https://api.360.cn/v1/images/generations").mock(return_value=httpx.Response(200, json={"data": [{"url": "https://cdn.360.cn/front.png"}]}))
    provider = NanoBananaImageProvider(api_key="test-key")
    ref = await provider.generate_image("a portrait")
    assert route.called
    body = json.loads(route.calls.last.request.content.decode())
    assert body["model"] == "google/gemini-3-pro-image-preview"
    # Nano-Banana requires generation_config (per 360 spec) for text-to-image
    assert body["extra_body"]["generation_config"]["imageConfig"]["personGeneration"] == "ALLOW_ALL"
    assert ref.url == "https://cdn.360.cn/front.png"
    assert ref.source == "nano-banana"


@respx.mock
@pytest.mark.asyncio
async def test_generate_image_with_reference_calls_edits_with_images():
    import json

    from app.gateway.video_gen.providers.nano_banana import NanoBananaImageProvider

    route = respx.post("https://api.360.cn/v1/images/edits").mock(return_value=httpx.Response(200, json={"data": [{"url": "https://cdn.360.cn/side.png"}]}))
    provider = NanoBananaImageProvider(api_key="test-key")
    ref = await provider.generate_image(
        "side-view of the same character",
        reference_image_paths=["https://cdn.360.cn/front.png"],
    )
    assert route.called
    body = json.loads(route.calls.last.request.content.decode())
    assert body["model"] == "google/gemini-3-pro-image-preview"
    assert body["images"] == ["https://cdn.360.cn/front.png"]
    assert ref.url == "https://cdn.360.cn/side.png"


@respx.mock
@pytest.mark.asyncio
async def test_generate_image_handles_b64_response():
    from app.gateway.video_gen.providers.nano_banana import NanoBananaImageProvider

    respx.post("https://api.360.cn/v1/images/generations").mock(return_value=httpx.Response(200, json={"data": [{"b64_json": "iVBORw0KGgoAAAANSUhEUg=="}]}))
    provider = NanoBananaImageProvider(api_key="test-key")
    ref = await provider.generate_image("test")
    assert ref.url.startswith("data:image/png;base64,")


@respx.mock
@pytest.mark.asyncio
async def test_generate_image_raises_on_rate_limit():
    from app.gateway.video_gen.providers.nano_banana import NanoBananaImageProvider
    from app.gateway.video_gen.providers.throttle import RateLimitError

    respx.post("https://api.360.cn/v1/images/edits").mock(return_value=httpx.Response(429, text="rate limited"))
    provider = NanoBananaImageProvider(api_key="test-key")
    with pytest.raises(RateLimitError):
        await provider.generate_image("test", reference_image_paths=["https://cdn.360.cn/front.png"])


@respx.mock
@pytest.mark.asyncio
async def test_generate_image_local_file_reference_converted_to_b64(tmp_path):
    import json

    from app.gateway.video_gen.providers.nano_banana import NanoBananaImageProvider

    ref_file = tmp_path / "portrait.png"
    ref_file.write_bytes(b"\x89PNG\r\n\x1a\nfake-png-bytes")

    route = respx.post("https://api.360.cn/v1/images/edits").mock(return_value=httpx.Response(200, json={"data": [{"url": "https://cdn.360.cn/out.png"}]}))
    provider = NanoBananaImageProvider(api_key="test-key")
    await provider.generate_image("portrait", reference_image_paths=[str(ref_file)])
    body = json.loads(route.calls.last.request.content.decode())
    assert body["images"][0].startswith("data:image/png;base64,")


# ── aspect ratio (regression for vimax real-run vg_8159cd6cc3fa) ────────────
# The run came back 768x768 (square) despite requesting 16:9. Root cause:
# NanoBananaImageProvider hardcoded aspectRatio="1:1" in extra_body. i2v (Hailuo)
# then inherited the square first frame. Fix: make aspectRatio configurable.


@respx.mock
@pytest.mark.asyncio
async def test_aspect_ratio_defaults_to_1_1_when_unset():
    import json

    from app.gateway.video_gen.providers.nano_banana import NanoBananaImageProvider

    route = respx.post("https://api.360.cn/v1/images/generations").mock(return_value=httpx.Response(200, json={"data": [{"url": "https://cdn.360.cn/x.png"}]}))
    provider = NanoBananaImageProvider(api_key="k")
    await provider.generate_image("test")
    body = json.loads(route.calls.last.request.content.decode())
    assert body["extra_body"]["generation_config"]["imageConfig"]["aspectRatio"] == "1:1"


@respx.mock
@pytest.mark.asyncio
async def test_aspect_ratio_16_9_passed_through_to_api():
    import json

    from app.gateway.video_gen.providers.nano_banana import NanoBananaImageProvider

    route = respx.post("https://api.360.cn/v1/images/generations").mock(return_value=httpx.Response(200, json={"data": [{"url": "https://cdn.360.cn/wide.png"}]}))
    provider = NanoBananaImageProvider(api_key="k", aspect_ratio="16:9")
    await provider.generate_image("wide shot")
    body = json.loads(route.calls.last.request.content.decode())
    # 16:9 must reach the API body, NOT the hardcoded 1:1
    assert body["extra_body"]["generation_config"]["imageConfig"]["aspectRatio"] == "16:9"


@respx.mock
@pytest.mark.asyncio
async def test_aspect_ratio_applies_to_i2i_edits_path_too():
    """First frames use the i2i /v1/images/edits path (with reference images). The
    aspect fix must reach that path too, otherwise first frames stay square (1024x1024)
    even with aspect_ratio set — rerun vg_0362585808e1 confirmed it: portrait
    (text-to-image) became 16:9 but shot first-frames (i2i) stayed square. The edits
    body must carry generation_config.imageConfig.aspectRatio."""
    import json

    from app.gateway.video_gen.providers.nano_banana import NanoBananaImageProvider

    route = respx.post("https://api.360.cn/v1/images/edits").mock(return_value=httpx.Response(200, json={"data": [{"url": "https://cdn.360.cn/ff.png"}]}))
    provider = NanoBananaImageProvider(api_key="k", aspect_ratio="16:9")
    await provider.generate_image("first frame", reference_image_paths=["https://cdn.360.cn/portrait.png"])
    body = json.loads(route.calls.last.request.content.decode())
    assert body["extra_body"]["generation_config"]["imageConfig"]["aspectRatio"] == "16:9", "i2i edits path dropped aspectRatio -> first frames stay square"


@respx.mock
@pytest.mark.asyncio
async def test_generate_image_retries_empty_response_then_succeeds(monkeypatch):
    """Empty responses (no data / no url+bytes) are transient — provider retries up to
    3 times (2s wait) and recovers. Regression for vg_00f494b2292a's
    'no url or b64_json in response' stalling ViMax portrait generation."""
    from app.gateway.video_gen.providers.nano_banana import NanoBananaImageProvider

    # wait_fixed(2) → 0：仍走满 3 次 attempt，只是不真睡（patch 装饰后方法绑定的 retry 状态）。
    monkeypatch.setattr(NanoBananaImageProvider.generate_image.retry, "wait", tenacity.wait_none())

    route = respx.post("https://api.360.cn/v1/images/generations").mock(
        side_effect=[
            httpx.Response(200, json={"data": []}),  # empty data — transient
            httpx.Response(200, json={"data": [{}]}),  # no url/b64 — transient
            httpx.Response(200, json={"data": [{"url": "https://cdn.360.cn/x.png"}]}),  # success
        ],
    )
    provider = NanoBananaImageProvider(api_key="test-key")
    ref = await provider.generate_image("a portrait")
    assert route.call_count == 3, "should retry twice then succeed on the 3rd attempt"
    assert ref.url == "https://cdn.360.cn/x.png"


@respx.mock
@pytest.mark.asyncio
async def test_generate_image_retries_transport_disconnect_then_succeeds(monkeypatch):
    """Server disconnected (httpx.RemoteProtocolError) — the 360 image API drops
    connections under ViMax's sustained load. Provider retries at the shot level
    (3 attempts, 2s wait) instead of escalating to a full-pipeline retry."""
    from app.gateway.video_gen.providers.nano_banana import NanoBananaImageProvider

    # wait_fixed(2) → 0：仍走满 3 次 attempt，只是不真睡。
    monkeypatch.setattr(NanoBananaImageProvider.generate_image.retry, "wait", tenacity.wait_none())

    route = respx.post("https://api.360.cn/v1/images/generations").mock(
        side_effect=[
            httpx.RemoteProtocolError("Server disconnected without sending a response"),
            httpx.RemoteProtocolError("Server disconnected without sending a response"),
            httpx.Response(200, json={"data": [{"url": "https://cdn.360.cn/x.png"}]}),
        ],
    )
    provider = NanoBananaImageProvider(api_key="test-key")
    ref = await provider.generate_image("a portrait")
    assert route.call_count == 3, "should retry twice then succeed on 3rd attempt"
    assert ref.url == "https://cdn.360.cn/x.png"
