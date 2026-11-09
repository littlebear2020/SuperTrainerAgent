"""Tests for GeminiFlashEditProvider — 备选 image provider (gemini-3.1-flash-image-preview-edit).

Subclasses NanoBananaImageProvider (same /v1/images/edits + top-level ``images``
format); only the default model id differs. Registered as backup only —
unvalidated combination (spec §3.3 ⚠️).
"""

import httpx
import pytest
import respx


@respx.mock
@pytest.mark.asyncio
async def test_default_model_is_flash_edit():
    import json

    from app.gateway.video_gen.providers.gemini_flash_edit import GeminiFlashEditProvider

    route = respx.post("https://api.360.cn/v1/images/edits").mock(return_value=httpx.Response(200, json={"data": [{"url": "https://cdn.360.cn/x.png"}]}))
    provider = GeminiFlashEditProvider(api_key="test-key")
    await provider.generate_image("a portrait", reference_image_paths=["https://cdn.360.cn/ref.png"])
    body = json.loads(route.calls.last.request.content.decode())
    assert body["model"] == "google/gemini-3.1-flash-image-preview-edit"
    assert body["images"] == ["https://cdn.360.cn/ref.png"]
