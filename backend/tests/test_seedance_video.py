"""Tests for SeedanceVideoProvider (C1, 决策4).

seedance = same 360 submit+poll wire format as Hailuo (QihuVideoProvider), differing
in model (volcengine/doubao-seedance-2-0) and r2v reference support (multi image_url
content + extra_body.role="reference"). Endpoint/content shape confirmed in the
2026-06-22 multi-engine plan spec (§技术发现表). These mocked tests assert the
documented request shape; a live smoke test against real 360 remains the final
validation (flagged in the optimization plan, C0).
"""

import json

import httpx
import pytest
import respx

from app.gateway.video_gen.providers.base import VideoRef


@respx.mock
@pytest.mark.asyncio
async def test_seedance_r2v_submits_multi_reference_with_role():
    from app.gateway.video_gen.providers.seedance import SeedanceVideoProvider

    respx.post("https://api.360.cn/v1/videos/async_generations").mock(
        return_value=httpx.Response(200, json={"data": {"id": "s1", "status": "created"}}),
    )
    respx.get("https://api.360.cn/v1/videos/async_generations/s1").mock(
        return_value=httpx.Response(200, json={"data": {"status": "succeeded", "url": "https://cdn/seedance.mp4"}}),
    )
    provider = SeedanceVideoProvider(api_key="test-key", poll_interval=0.01)
    ref = await provider.generate_video(
        "a detective walking through neon streets",
        duration=8.0,
        image_urls=["https://img/a.png", "https://img/b.png"],
    )
    assert isinstance(ref, VideoRef)
    assert ref.url == "https://cdn/seedance.mp4"
    assert ref.source == "seedance"
    assert ref.duration == 8.0

    body = json.loads(respx.calls[0].request.content.decode())
    # E10（2026-08-21）：类默认模型 id 对齐 360 在册名（探针实测，旧值
    # volcengine/… 网关内部归一亦可用）。
    assert body["model"] == "bytedance/seedance-2-0"
    image_entries = [c for c in body["content"] if c.get("type") == "image_url"]
    assert len(image_entries) == 2  # multi-reference r2v
    # E1（2026-08-21）：extra_body 恒含 watermark/generate_audio:false（360 主力切换）；
    # role 语义不变（r2v 参考标识）。
    assert body["extra_body"]["role"] == "reference"
    assert body["extra_body"]["watermark"] is False
    assert body["extra_body"]["generate_audio"] is False


@respx.mock
@pytest.mark.asyncio
async def test_seedance_i2v_accepts_legacy_single_image_kwarg():
    """Bridge-compat: SeedanceVideoGeneratorForViMax forwards reference_image_paths[0]
    as image_url= (single first-frame). Must still work, routed as one reference."""
    from app.gateway.video_gen.providers.seedance import SeedanceVideoProvider

    respx.post("https://api.360.cn/v1/videos/async_generations").mock(
        return_value=httpx.Response(200, json={"data": {"id": "s2"}}),
    )
    respx.get("https://api.360.cn/v1/videos/async_generations/s2").mock(
        return_value=httpx.Response(200, json={"data": {"status": "succeeded", "url": "https://cdn/i2v.mp4"}}),
    )
    provider = SeedanceVideoProvider(api_key="k", poll_interval=0.01)
    ref = await provider.generate_video("prompt", duration=6.0, image_url="https://img/first.png")
    assert ref.url == "https://cdn/i2v.mp4"
    body = json.loads(respx.calls[0].request.content.decode())
    assert sum(1 for c in body["content"] if c.get("type") == "image_url") == 1
    assert body["extra_body"]["role"] == "reference"


@respx.mock
@pytest.mark.asyncio
async def test_seedance_text_to_video_has_no_role():
    """No reference images -> plain text-to-video; extra_body.role is reference-only
    （E1 后 extra_body 恒存在——watermark/generate_audio 默认值，但 role 仍只在 r2v 设）。"""
    from app.gateway.video_gen.providers.seedance import SeedanceVideoProvider

    respx.post("https://api.360.cn/v1/videos/async_generations").mock(
        return_value=httpx.Response(200, json={"data": {"id": "s3"}}),
    )
    respx.get("https://api.360.cn/v1/videos/async_generations/s3").mock(
        return_value=httpx.Response(200, json={"data": {"status": "succeeded", "url": "https://cdn/t2v.mp4"}}),
    )
    provider = SeedanceVideoProvider(api_key="k", poll_interval=0.01)
    await provider.generate_video("a serene landscape", duration=6.0)
    body = json.loads(respx.calls[0].request.content.decode())
    assert "role" not in body["extra_body"]
    assert body["content"] == [{"type": "text", "text": "a serene landscape"}]


@respx.mock
@pytest.mark.asyncio
async def test_seedance_polling_reuses_qihu_wire_format():
    """seedance shares Hailuo's GET poll shape (status success/failed/processing)."""
    from app.gateway.video_gen.providers.seedance import SeedanceVideoProvider

    respx.post("https://api.360.cn/v1/videos/async_generations").mock(
        return_value=httpx.Response(200, json={"data": {"id": "s4"}}),
    )
    respx.get("https://api.360.cn/v1/videos/async_generations/s4").mock(
        return_value=httpx.Response(200, json={"data": {"status": "succeeded", "url": "https://cdn/x.mp4"}}),
    )
    provider = SeedanceVideoProvider(api_key="k", poll_interval=0.01)
    ref = await provider.generate_video("p", duration=5.0)
    assert ref.url == "https://cdn/x.mp4"


def test_registry_builds_seedance_provider():
    """C2: ProviderRegistry builds SeedanceVideoProvider from the 'seedance' name
    (reuses qihu_api_key/qihu_base_url — same 360 gateway)."""
    from app.gateway.video_gen.config import VideoGenConfig
    from app.gateway.video_gen.providers.registry import ProviderRegistry

    cfg = VideoGenConfig(qihu_api_key="k")
    reg = ProviderRegistry(cfg)
    from app.gateway.video_gen.providers.seedance import SeedanceVideoProvider

    provider = reg._try_make_ai_video_provider(cfg, "seedance")
    assert isinstance(provider, SeedanceVideoProvider)


# ── T3（2026-08-18）：图生视频声明+接线（refs data-URI 化 + role 语义）──
# 官方依据 backend/docs/360-api/create-video-generation-seedance.md：
# - image_url.url「可以是图片 URL（确保可访问）或图片 Base64 编码」——本地路径须归一；
# - extra_body.role 是**全局生成类型标识**（原文：「在 extra_body 中设置生成类型
#   标识 role 的值为参考生视频 reference，会将所有的图像、视频和音频对象设置为
#   参考类型」）——无 per-image role；不设 role = 首帧 i2v（示例2），双图按序 =
#   首尾帧（示例3）。


def test_seedance_declares_image_urls_support():
    """T3：类声明（capabilities 表同步由 test_provider_capabilities 一致性锁看守）。"""
    from app.gateway.video_gen.providers.seedance import SeedanceVideoProvider

    assert SeedanceVideoProvider.supports_image_urls is True


@respx.mock
@pytest.mark.asyncio
async def test_seedance_refs_normalized_to_data_uri(tmp_path):
    """T3：refs 修前原样塞本地路径——远端 360 抓不到会被拒/静默丢图（官方
    「确保可访问」口径），逐张 _to_data_uri；http URL 原样透传。"""
    from app.gateway.video_gen.providers.seedance import SeedanceVideoProvider

    respx.post("https://api.360.cn/v1/videos/async_generations").mock(
        return_value=httpx.Response(200, json={"data": {"id": "s5"}}),
    )
    respx.get("https://api.360.cn/v1/videos/async_generations/s5").mock(
        return_value=httpx.Response(200, json={"data": {"status": "succeeded", "url": "https://cdn/r2v.mp4"}}),
    )
    img = tmp_path / "char_front.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\nfake-bytes")
    provider = SeedanceVideoProvider(api_key="k", poll_interval=0.01)
    await provider.generate_video("p", duration=6.0, image_urls=[str(img), "https://img/b.png"])
    body = json.loads(respx.calls[0].request.content.decode())
    urls = [c["image_url"]["url"] for c in body["content"] if c.get("type") == "image_url"]
    assert urls[0].startswith("data:image/"), f"本地路径须转 data-URI，实际 {urls[0]!r}"
    assert str(img) not in urls[0]
    assert urls[1] == "https://img/b.png", "远端 URL 原样透传"


@respx.mock
@pytest.mark.asyncio
async def test_seedance_role_none_is_first_frame_mode():
    """T3 role 语义缺口：role=None → 不设 extra_body.role = 首帧 i2v（官方示例2）。
    修前默认 role="reference" 且带图恒设——preview 首帧继承会被错标为 r2v 参考图。"""
    from app.gateway.video_gen.providers.seedance import SeedanceVideoProvider

    respx.post("https://api.360.cn/v1/videos/async_generations").mock(
        return_value=httpx.Response(200, json={"data": {"id": "s6"}}),
    )
    respx.get("https://api.360.cn/v1/videos/async_generations/s6").mock(
        return_value=httpx.Response(200, json={"data": {"status": "succeeded", "url": "https://cdn/i2v.mp4"}}),
    )
    provider = SeedanceVideoProvider(api_key="k", poll_interval=0.01)
    await provider.generate_video("p", duration=6.0, image_urls=["https://img/first.png"], role=None)
    body = json.loads(respx.calls[0].request.content.decode())
    # E1 后 extra_body 恒存在（watermark/generate_audio），但首帧模式**不得**设全局 role。
    assert "role" not in body["extra_body"], f"首帧模式不得设全局 role（会被当 r2v 参考图），实际 extra_body={body.get('extra_body')}"
