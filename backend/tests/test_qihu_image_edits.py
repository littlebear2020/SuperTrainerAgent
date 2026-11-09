"""E4（360 通道切换，2026-08-21）：seedream-360 参考图能力（edits 路径）+ 白名单。

依据 docs/plans/2026-08-21-video-gen-360-switch-impl-plan.md E4 + 实测（T3/T4，
temp/v1_probe/results_image / results_a7supp）：
- 有 ``reference_image_paths`` 时调 ``POST /v1/images/edits``，参考图经
  ``extra_body.image``（单图字符串 / 多图数组，官方《图片编辑》口径）；
- 尺寸一律走 ``extra_body.size`` 三档精确映射（顶层 size 实测被静默改派）；
- ``watermark: false``；**禁发** ``sequential_image_generation``（对 5.0-pro 实测 400）；
- 纯文生图仍走 generations（尺寸同样改走 extra_body.size）；
- ``registry.py`` i2i 白名单纳入 ``seedream-360``（角色参考图不再告警丢图）。
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from app.gateway.video_gen.providers.qihu_image import QihuImageProvider

EDITS_URL = "https://api.360.cn/v1/images/edits"
GEN_URL = "https://api.360.cn/v1/images/generations"


def _mock_ok() -> None:
    ok = httpx.Response(200, json={"data": [{"url": "https://cdn/img.png"}], "usage": {"amount": "0.1"}})
    respx.post(EDITS_URL).mock(return_value=ok)
    respx.post(GEN_URL).mock(return_value=ok)


def _last_body(url: str) -> dict:
    calls = [c for c in respx.calls if str(c.request.url) == url and c.request.method == "POST"]
    assert calls, f"no POST hit {url}"
    return json.loads(calls[-1].request.content.decode())


@pytest.mark.asyncio
@respx.mock
async def test_single_reference_goes_edits_with_string_image():
    """带 1 张参考图 → edits 端点；extra_body.image 为字符串。"""
    _mock_ok()
    p = QihuImageProvider(api_key="k", model="bytedance/doubao-seedream-5.0-pro")
    ref = await p.generate_image("prompt", size="1920x1080", reference_image_paths=["https://img/front.png"])
    assert ref.url == "https://cdn/img.png"
    body = _last_body(EDITS_URL)
    assert body["extra_body"]["image"] == "https://img/front.png"
    assert isinstance(body["extra_body"]["image"], str)


@pytest.mark.asyncio
@respx.mock
async def test_multiple_references_go_edits_with_array_image():
    """带 3 张参考图 → edits 端点；extra_body.image 为数组。"""
    _mock_ok()
    p = QihuImageProvider(api_key="k", model="bytedance/doubao-seedream-5.0-pro")
    await p.generate_image("prompt", size="1920x1080", reference_image_paths=["https://img/a.png", "https://img/b.png", "https://img/c.png"])
    body = _last_body(EDITS_URL)
    assert body["extra_body"]["image"] == ["https://img/a.png", "https://img/b.png", "https://img/c.png"]


@pytest.mark.asyncio
@respx.mock
async def test_edits_body_shape_no_toplevel_size_no_sequential():
    """请求体不含顶层 size、不含 sequential_image_generation、含 watermark:false。"""
    _mock_ok()
    p = QihuImageProvider(api_key="k", model="bytedance/doubao-seedream-5.0-pro")
    await p.generate_image("prompt", size="1920x1080", reference_image_paths=["https://img/a.png"])
    body = _last_body(EDITS_URL)
    assert "size" not in body, "顶层 size 实测被静默改派（T4），禁发"
    assert "sequential_image_generation" not in body
    assert "sequential_image_generation" not in body.get("extra_body", {}), "对 5.0-pro 实测 400，禁发"
    assert body["extra_body"]["watermark"] is False


@pytest.mark.parametrize(
    ("task_size", "expected"),
    [
        ("1920x1080", "2048x1152"),  # 16:9
        ("1024x1024", "2048x2048"),  # 1:1
        ("1080x1920", "1152x2048"),  # 9:16
    ],
)
@pytest.mark.asyncio
@respx.mock
async def test_size_maps_to_seedream_tiers(task_size: str, expected: str):
    """尺寸映射：任务比例 → extra_body.size 三档精确（T4 实测三档，顶层被改派）。"""
    _mock_ok()
    p = QihuImageProvider(api_key="k", model="bytedance/doubao-seedream-5.0-pro")
    await p.generate_image("prompt", size=task_size, reference_image_paths=["https://img/a.png"])
    assert _last_body(EDITS_URL)["extra_body"]["size"] == expected


@pytest.mark.asyncio
@respx.mock
async def test_generations_still_used_without_refs_and_size_in_extra_body():
    """纯文生图仍走 generations；尺寸同样改走 extra_body.size（非顶层）。"""
    _mock_ok()
    p = QihuImageProvider(api_key="k", model="bytedance/doubao-seedream-5.0-pro")
    await p.generate_image("prompt", size="1920x1080")
    body = _last_body(GEN_URL)
    assert "size" not in body, "generations 顶层 size 同样实测被静默改派"
    assert body["extra_body"]["size"] == "2048x1152"
    assert body["extra_body"]["watermark"] is False


@pytest.mark.asyncio
@respx.mock
async def test_local_reference_file_normalized_to_data_uri(tmp_path):
    """本地参考图（角色三视图）须转 data-URI——远端 360 抓不到本地路径。"""
    _mock_ok()
    img = tmp_path / "front.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    p = QihuImageProvider(api_key="k", model="bytedance/doubao-seedream-5.0-pro")
    await p.generate_image("prompt", size="1024x1024", reference_image_paths=[str(img)])
    image_field = _last_body(EDITS_URL)["extra_body"]["image"]
    assert image_field.startswith("data:image/"), f"本地路径须转 data-URI，实际: {image_field!r}"


def test_seedream_360_in_i2i_whitelist_no_warning_sidecar(tmp_path):
    """白名单纳入后：ai_image 渲染链带角色参考图不再写 dropped 告警 sidecar。"""
    from app.gateway.video_gen.config import VideoGenConfig
    from app.gateway.video_gen.providers.registry import I2I_CAPABLE_IMAGE_PROVIDERS
    from app.gateway.video_gen.service import _warn_if_image_provider_not_i2i

    assert "seedream-360" in I2I_CAPABLE_IMAGE_PROVIDERS
    cfg = VideoGenConfig(ai_image_provider="seedream-360")
    payload = {"character_reference": "/abs/front.png"}
    _warn_if_image_provider_not_i2i(cfg, payload, tmp_path)
    assert not (tmp_path / "ai_image_i2i_warning.json").exists(), "seedream-360 支持参考图，不应再告警"
