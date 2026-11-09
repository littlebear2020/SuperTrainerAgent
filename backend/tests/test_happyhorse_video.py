"""Tests for HappyhorseVideoProvider (写实人物后端, alibaba/happyhorse-1.0).

happyhorse = same 360 submit+poll endpoint as Hailuo/seedance, but a DIFFERENT
request body (extra_body.input/parameters, no top-level content/duration — verified
live). Status returns UPPERCASE 'SUCCEEDED'; the shared _poll lowercases it and the
'succeeded' acceptance (qihu_video._poll) covers it. These mocked tests assert the
documented request shape; a live smoke test (写实侦探肖像) already confirmed happyhorse
accepts realistic persons and produces video.
"""

import json

import httpx
import pytest
import respx

from app.gateway.video_gen.providers.base import VideoRef


@respx.mock
@pytest.mark.asyncio
async def test_happyhorse_i2v_submits_first_frame_shape():
    from app.gateway.video_gen.providers.happyhorse import HappyhorseVideoProvider

    respx.post("https://api.360.cn/v1/videos/async_generations").mock(
        return_value=httpx.Response(200, json={"data": {"id": "h1", "status": "PENDING"}}),
    )
    respx.get("https://api.360.cn/v1/videos/async_generations/h1").mock(
        return_value=httpx.Response(200, json={"data": {"status": "SUCCEEDED", "url": "https://cdn/h.mp4"}}),
    )
    provider = HappyhorseVideoProvider(api_key="k", poll_interval=0.01)
    ref = await provider.generate_video("the detective walks slowly", duration=5, image_url="https://img/p.png")

    assert isinstance(ref, VideoRef)
    assert ref.url == "https://cdn/h.mp4"
    assert ref.source == "happyhorse"

    body = json.loads(respx.calls[0].request.content.decode())
    assert body["model"] == "alibaba/happyhorse-1.0-i2v"
    assert body["extra_body"]["input"]["prompt"] == "the detective walks slowly"
    assert body["extra_body"]["input"]["media"] == [{"type": "first_frame", "url": "https://img/p.png"}]
    assert body["extra_body"]["parameters"]["duration"] == 5
    # happyhorse puts everything in extra_body — NO top-level content/duration
    assert "content" not in body


@respx.mock
@pytest.mark.asyncio
async def test_happyhorse_t2v_when_no_first_frame():
    """No image → auto-switch to the t2v model (no media)."""
    from app.gateway.video_gen.providers.happyhorse import HappyhorseVideoProvider

    respx.post("https://api.360.cn/v1/videos/async_generations").mock(
        return_value=httpx.Response(200, json={"data": {"id": "h2", "status": "PENDING"}}),
    )
    respx.get("https://api.360.cn/v1/videos/async_generations/h2").mock(
        return_value=httpx.Response(200, json={"data": {"status": "SUCCEEDED", "url": "https://cdn/t.mp4"}}),
    )
    provider = HappyhorseVideoProvider(api_key="k", poll_interval=0.01)
    ref = await provider.generate_video("a serene landscape", duration=5)

    assert ref.url == "https://cdn/t.mp4"
    body = json.loads(respx.calls[0].request.content.decode())
    assert body["model"] == "alibaba/happyhorse-1.0-t2v"
    assert "media" not in body["extra_body"]["input"]


@respx.mock
@pytest.mark.asyncio
async def test_happyhorse_poll_recognizes_uppercase_succeeded():
    """happyhorse returns UPPERCASE 'SUCCEEDED'/'RUNNING'. The shared _poll lowercases
    and accepts 'succeeded' — must not time out."""
    from app.gateway.video_gen.providers.happyhorse import HappyhorseVideoProvider

    respx.post("https://api.360.cn/v1/videos/async_generations").mock(
        return_value=httpx.Response(200, json={"data": {"id": "h3", "status": "PENDING"}}),
    )
    respx.get("https://api.360.cn/v1/videos/async_generations/h3").mock(
        return_value=httpx.Response(200, json={"data": {"status": "RUNNING"}}),
    )
    # second poll mock via side_effect would be cleaner; respx .mock covers all calls
    # so override with side_effect
    respx.get("https://api.360.cn/v1/videos/async_generations/h3").mock(
        side_effect=[
            httpx.Response(200, json={"data": {"status": "RUNNING"}}),
            httpx.Response(200, json={"data": {"status": "SUCCEEDED", "url": "https://cdn/x.mp4"}}),
        ],
    )
    provider = HappyhorseVideoProvider(api_key="k", poll_interval=0.01)
    ref = await provider.generate_video("p", duration=5)
    assert ref.url == "https://cdn/x.mp4"


def test_registry_builds_happyhorse_provider():
    """ProviderRegistry builds HappyhorseVideoProvider from the 'happyhorse' name
    (reuses qihu_api_key/qihu_base_url — same 360 gateway)."""
    from app.gateway.video_gen.config import VideoGenConfig
    from app.gateway.video_gen.providers.registry import ProviderRegistry

    cfg = VideoGenConfig(qihu_api_key="k")
    reg = ProviderRegistry(cfg)
    from app.gateway.video_gen.providers.happyhorse import HappyhorseVideoProvider

    provider = reg._try_make_ai_video_provider(cfg, "happyhorse")
    assert isinstance(provider, HappyhorseVideoProvider)


# ── T3（2026-08-18）：图生视频声明+接线（复数 image_urls 适配 + data-URI 归一）──


def test_happyhorse_declares_image_urls_support():
    """T3：类声明（capabilities 表同步由 test_provider_capabilities 一致性锁看守）。"""
    from app.gateway.video_gen.providers.happyhorse import HappyhorseVideoProvider

    assert HappyhorseVideoProvider.supports_image_urls is True


@respx.mock
@pytest.mark.asyncio
async def test_happyhorse_plural_image_urls_first_image_as_first_frame(tmp_path):
    """T3：调用方传复数 image_urls（ai_video.py 口径）→ 取 [0] 进
    media:[{type:first_frame,url}]；修前签名是单数具名参数，复数被 **kwargs 吞。
    本地路径须 data-URI 化（是否接受 data-URI 按探针 3 结论回填计划）。"""
    from app.gateway.video_gen.providers.happyhorse import HappyhorseVideoProvider

    respx.post("https://api.360.cn/v1/videos/async_generations").mock(
        return_value=httpx.Response(200, json={"data": {"id": "h4", "status": "PENDING"}}),
    )
    respx.get("https://api.360.cn/v1/videos/async_generations/h4").mock(
        return_value=httpx.Response(200, json={"data": {"status": "SUCCEEDED", "url": "https://cdn/h.mp4"}}),
    )
    img = tmp_path / "preview.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\nfake-bytes")
    provider = HappyhorseVideoProvider(api_key="k", poll_interval=0.01)
    ref = await provider.generate_video("p", duration=5, image_urls=[str(img), "https://img/second.png"])
    assert ref.url == "https://cdn/h.mp4"
    body = json.loads(respx.calls[0].request.content.decode())
    media = body["extra_body"]["input"]["media"]
    assert len(media) == 1, f"只取首图作 first_frame，实际 {media}"
    assert media[0]["type"] == "first_frame"
    assert media[0]["url"].startswith("data:image/"), f"本地路径须转 data-URI，实际 {media[0]['url']!r}"
