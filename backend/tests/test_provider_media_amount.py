"""B1: 媒体 amount 全链路捕获——ImageRef/VideoRef 带 cost_cny + raw_usage。"""

import httpx
import pytest
import respx


@respx.mock
@pytest.mark.asyncio
async def test_qihu_image_extracts_cost_cny():
    from app.gateway.video_gen.providers.qihu_image import QihuImageProvider

    respx.post("https://api.360.cn/v1/images/generations").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [{"url": "http://cdn/img.png"}],
                "usage": {"amount": "0.020396"},
            },
        ),
    )
    provider = QihuImageProvider(api_key="test-key")
    ref = await provider.generate_image("a cat")
    assert ref.cost_cny == pytest.approx(0.020396)
    assert ref.raw_usage == {"amount": "0.020396"}


@respx.mock
@pytest.mark.asyncio
async def test_qihu_image_no_usage_field():
    """无 usage 字段 → cost_cny=None, raw_usage=None（不炸）。"""
    from app.gateway.video_gen.providers.qihu_image import QihuImageProvider

    respx.post("https://api.360.cn/v1/images/generations").mock(
        return_value=httpx.Response(200, json={"data": [{"url": "http://cdn/img.png"}]}),
    )
    provider = QihuImageProvider(api_key="test-key")
    ref = await provider.generate_image("a cat")
    assert ref.cost_cny is None
    assert ref.raw_usage is None


@respx.mock
@pytest.mark.asyncio
async def test_seedream_extracts_cost_cny():
    from app.gateway.video_gen.providers.seedream import SeedreamImageProvider

    respx.post("https://ark.cn-beijing.volces.com/api/v3/images/generations").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [{"url": "http://cdn/sd.png"}],
                "usage": {"amount": "0.031"},
            },
        ),
    )
    provider = SeedreamImageProvider(api_key="test-key")
    ref = await provider.generate_image("a cat")
    assert ref.cost_cny == pytest.approx(0.031)
    assert ref.raw_usage["amount"] == "0.031"


@respx.mock
@pytest.mark.asyncio
async def test_qihu_video_extracts_cost_cny_from_poll():
    from app.gateway.video_gen.providers.qihu_video import QihuVideoProvider

    respx.post("https://api.360.cn/v1/videos/async_generations").mock(
        return_value=httpx.Response(200, json={"data": {"id": "v1", "status": "created"}}),
    )
    respx.get("https://api.360.cn/v1/videos/async_generations/v1").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {"status": "success", "url": "http://cdn/done.mp4"},
                "usage": {"amount": "0.15"},
            },
        ),
    )
    provider = QihuVideoProvider(api_key="test-key", poll_interval=0.01)
    ref = await provider.generate_video("test", duration=8.0)
    assert ref.cost_cny == pytest.approx(0.15)
    assert ref.raw_usage["amount"] == "0.15"


@respx.mock
@pytest.mark.asyncio
async def test_seedance_ark_extracts_cost_cny():
    from app.gateway.video_gen.providers.seedance_ark import SeedanceArkVideoProvider

    respx.post("https://ark.cn-beijing.volces.com/api/v3/contents/generations/tasks").mock(
        return_value=httpx.Response(200, json={"id": "ark-1"}),
    )
    respx.get("https://ark.cn-beijing.volces.com/api/v3/contents/generations/tasks/ark-1").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "succeeded",
                "content": {"video_url": "http://cdn/ark.mp4"},
                "usage": {"amount": "0.22"},
            },
        ),
    )
    provider = SeedanceArkVideoProvider(api_key="test-key", poll_interval=0.01)
    ref = await provider.generate_video("test", duration=5.0)
    assert ref.cost_cny == pytest.approx(0.22)


@respx.mock
@pytest.mark.asyncio
async def test_gpt_image2_extracts_cost_cny():
    from app.gateway.video_gen.providers.gpt_image2 import GptImage2Provider

    respx.post("https://api.360.cn/v1/images/generations").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [{"url": "http://cdn/gpt.png"}],
                "usage": {"amount": "0.0"},
            },
        ),
    )
    provider = GptImage2Provider(api_key="test-key")
    ref = await provider.generate_image("a cat")
    assert ref.cost_cny == pytest.approx(0.0)
    assert ref.raw_usage["amount"] == "0.0"
