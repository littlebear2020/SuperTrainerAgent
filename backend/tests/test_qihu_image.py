import httpx
import pytest
import respx

from app.gateway.video_gen.providers.base import ImageRef


@respx.mock
@pytest.mark.asyncio
async def test_generate_image_returns_image_ref():
    from app.gateway.video_gen.providers.qihu_image import QihuImageProvider

    respx.post("https://api.360.cn/v1/images/generations").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [{"url": "https://cdn.360.cn/img001.png"}],
            },
        ),
    )
    provider = QihuImageProvider(api_key="test-key")
    ref = await provider.generate_image("a sunset over mountains")
    assert isinstance(ref, ImageRef)
    assert ref.url == "https://cdn.360.cn/img001.png"
    assert ref.source == "qihu"
    assert ref.width > 0
    assert ref.height > 0


@respx.mock
@pytest.mark.asyncio
async def test_generate_image_uses_configured_model_and_size():
    import json

    from app.gateway.video_gen.providers.qihu_image import QihuImageProvider

    route = respx.post("https://api.360.cn/v1/images/generations").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [{"url": "https://cdn.360.cn/img001.png"}],
            },
        ),
    )
    provider = QihuImageProvider(
        api_key="test-key",
        model="google/gemini-3.1-flash-image-preview",
    )
    await provider.generate_image("test", size="1152x2048")
    assert route.called
    body = route.calls.last.request.content.decode()
    parsed = json.loads(body)
    assert parsed["model"] == "google/gemini-3.1-flash-image-preview"
    # E4（2026-08-21）：size 改走 extra_body.size（顶层实测被静默改派，T4）。
    # 1152x2048 本身即三档之一，映射后原值不变。
    assert parsed["extra_body"]["size"] == "1152x2048"
    assert "size" not in parsed


@respx.mock
@pytest.mark.asyncio
async def test_generate_image_handles_b64_response():
    from app.gateway.video_gen.providers.qihu_image import QihuImageProvider

    respx.post("https://api.360.cn/v1/images/generations").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [{"b64_json": "iVBORw0KGgoAAAANSUhEUg=="}],
            },
        ),
    )
    provider = QihuImageProvider(api_key="test-key")
    ref = await provider.generate_image("test")
    assert ref.url.startswith("data:image/png;base64,")
    assert ref.source == "qihu"


@respx.mock
@pytest.mark.asyncio
async def test_generate_image_raises_on_rate_limit():
    from app.gateway.video_gen.providers.qihu_image import QihuImageProvider
    from app.gateway.video_gen.providers.throttle import RateLimitError

    respx.post("https://api.360.cn/v1/images/generations").mock(
        return_value=httpx.Response(429, text="rate limited"),
    )
    provider = QihuImageProvider(api_key="test-key")
    with pytest.raises(RateLimitError):
        await provider.generate_image("test")


@respx.mock
@pytest.mark.asyncio
async def test_generate_image_5xx_raises_provider_unavailable():
    """P1-1（2026-08-08）：qihu 5xx 必须映射为 ProviderUnavailableError（∈ _FALLBACK_EXC），
    修前抛裸 RuntimeError → Throttle 零重试、registry 不降级，一次瞬时 500 即废帧。"""
    from app.gateway.video_gen.providers.qihu_image import QihuImageProvider
    from app.gateway.video_gen.providers.registry import _FALLBACK_EXC
    from app.gateway.video_gen.providers.throttle import ProviderUnavailableError

    respx.post("https://api.360.cn/v1/images/generations").mock(
        return_value=httpx.Response(500, text="internal server error"),
    )
    provider = QihuImageProvider(api_key="test-key")
    with pytest.raises(ProviderUnavailableError) as exc_info:
        await provider.generate_image("test")
    assert isinstance(exc_info.value, _FALLBACK_EXC), "必须落入降级/重试异常元组"


@respx.mock
@pytest.mark.asyncio
async def test_generate_image_4xx_stays_permanent_runtime_error():
    """4xx（非 429）仍是永久错误：裸 RuntimeError，不进重试/降级元组。"""
    from app.gateway.video_gen.providers.qihu_image import QihuImageProvider
    from app.gateway.video_gen.providers.registry import _FALLBACK_EXC

    respx.post("https://api.360.cn/v1/images/generations").mock(
        return_value=httpx.Response(400, text="bad request"),
    )
    provider = QihuImageProvider(api_key="test-key")
    with pytest.raises(RuntimeError) as exc_info:
        await provider.generate_image("test")
    assert not isinstance(exc_info.value, _FALLBACK_EXC)


@respx.mock
@pytest.mark.asyncio
async def test_throttled_qihu_retries_5xx():
    """Throttle 包裹链对 qihu 5xx（ProviderUnavailableError）必须执行重试。"""
    from app.gateway.video_gen.providers.qihu_image import QihuImageProvider
    from app.gateway.video_gen.providers.throttle import ThrottledAIImageProvider

    route = respx.post("https://api.360.cn/v1/images/generations").mock(
        side_effect=[
            httpx.Response(500, text="boom"),
            httpx.Response(200, json={"data": [{"url": "https://cdn.360.cn/ok.png"}]}),
        ],
    )
    inner = QihuImageProvider(api_key="test-key")
    provider = ThrottledAIImageProvider(inner, qps=1000.0)
    ref = await provider.generate_image("test")
    assert ref.url == "https://cdn.360.cn/ok.png"
    assert route.call_count == 2, "首次 500 后必须重试"


@respx.mock
@pytest.mark.asyncio
async def test_generate_image_custom_base_url():
    from app.gateway.video_gen.providers.qihu_image import QihuImageProvider

    route = respx.post("https://custom.api.cn/v1/images/generations").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [{"url": "https://example.com/img.png"}],
            },
        ),
    )
    provider = QihuImageProvider(api_key="test-key", base_url="https://custom.api.cn")
    await provider.generate_image("test")
    assert route.called
