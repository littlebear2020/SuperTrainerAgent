"""2026-09-23 决策 3：gpt-image-2.5-flare / sunburst 注册为生图 provider 选项。

计划 docs/plans/2026-09-23-360-new-models-integration-plan.md §3（v6 终稿）。
spike 结论（仓库根 temp/360_spike_0923*.py 四轮探针，2026-09-23）：
- 文生图可用，但 ``quality`` 为**必填**字段——缺省即 400 "invalid field: quality"；
  实测 low/high 通过、standard 被拒。D3 修复批定稿：flare/sunburst 均配 high（low 实测反而更慢且为最低质量档）。
- size 支持任意 WxH（1024x1024 / 1280x720 / 1536x1024 实测通过；5:4 的 800x640
  被拒——remotion_course 槽位尺寸需就近映射，见 annotations notes）。
- /v1/images/edits 参考图**全变体 400**（data URL / 公网 URL / image 单数 / multipart）
  → 参考图编辑不可用，**不进 I2I_CAPABLE_IMAGE_PROVIDERS**（计划 §3.2.3 的
  「2.5 支持参考图编辑」前提被证伪，按 §4 探针结论覆盖，附录留档）。
"""

from __future__ import annotations

import httpx
import pytest
import respx

from app.gateway.video_gen.config import VideoGenConfig
from app.gateway.video_gen.providers import registry as reg
from app.gateway.video_gen.providers.gpt_image2 import GptImage2Provider

NEW_25_PROVIDERS = ("gpt-image-2.5-flare", "gpt-image-2.5-sunburst")


def _registry(qihu_key: str = "k") -> reg.ProviderRegistry:
    return reg.ProviderRegistry(VideoGenConfig(qihu_api_key=qihu_key))


# ── 五件套注册 ────────────────────────────────────────────────────────────


def test_known_image_providers_contains_both() -> None:
    for name in NEW_25_PROVIDERS:
        assert name in reg.ProviderRegistry.KNOWN_IMAGE_PROVIDERS


def test_key_map_uses_qihu_key() -> None:
    for name in NEW_25_PROVIDERS:
        assert reg.ProviderRegistry._IMAGE_PROVIDER_KEY[name] == "qihu_api_key"


def test_labels_present() -> None:
    for name in NEW_25_PROVIDERS:
        assert reg._IMAGE_PROVIDER_LABELS.get(name), f"{name} 缺 label"


def test_annotations_registered_explicitly() -> None:
    """D5 口径显式登记（漏填只会静默回落默认标注、测试不红，故显式 pin）。"""
    for name in NEW_25_PROVIDERS:
        ann = reg._PROVIDER_ANNOTATIONS.get(name)
        assert isinstance(ann, dict), f"{name} 未在 _PROVIDER_ANNOTATIONS 显式登记"
        assert {"reference_images", "tested", "notes"} <= set(ann)
        assert ann["reference_images"] is False, "edits 不可用（spike 全变体 400），不得宣称参考图能力"
        assert ann["tested"] != "未检测"


def test_not_in_i2i_whitelist() -> None:
    """spike：/v1/images/edits 对 2.5 全变体 400——进白名单会让 remotion_course
    参考图链路选中即失败。"""
    for name in NEW_25_PROVIDERS:
        assert name not in reg.I2I_CAPABLE_IMAGE_PROVIDERS


# ── 构造分支 ──────────────────────────────────────────────────────────────


def test_construction_pins_model_and_quality() -> None:
    r = _registry()
    flare = r._try_make_ai_image_provider(r._config, "gpt-image-2.5-flare")
    sun = r._try_make_ai_image_provider(r._config, "gpt-image-2.5-sunburst")
    assert isinstance(flare, GptImage2Provider) and flare._model == "openai/gpt-image-2.5-flare" and flare._quality == "high"
    assert isinstance(sun, GptImage2Provider) and sun._model == "openai/gpt-image-2.5-sunburst" and sun._quality == "high"


def test_construction_gated_on_qihu_key() -> None:
    r = reg.ProviderRegistry(VideoGenConfig())
    assert r._try_make_ai_image_provider(r._config, "gpt-image-2.5-flare") is None
    assert r._try_make_ai_image_provider(r._config, "gpt-image-2.5-sunburst") is None


# ── wire format：quality 必填 ────────────────────────────────────────────


@respx.mock
@pytest.mark.asyncio
async def test_wire_body_carries_quality() -> None:
    """quality 缺省即 400（spike）——构造分支配置的档位必须落到请求体。"""
    import json

    route = respx.post("https://api.360.cn/v1/images/generations").mock(return_value=httpx.Response(200, json={"data": [{"url": "https://cdn.360.cn/out.png"}]}))
    provider = GptImage2Provider(api_key="k", model="openai/gpt-image-2.5-flare", quality="high")
    await provider.generate_image("a red circle icon", size="1024x1024")
    body = json.loads(route.calls.last.request.content.decode())
    assert body["model"] == "openai/gpt-image-2.5-flare"
    assert body["quality"] == "high"


@respx.mock
@pytest.mark.asyncio
async def test_legacy_gpt_image_2_omits_quality() -> None:
    """存量 gpt-image-2（v1）不强制 quality——缺省不得改变既有 wire format。"""
    import json

    route = respx.post("https://api.360.cn/v1/images/generations").mock(return_value=httpx.Response(200, json={"data": [{"url": "https://cdn.360.cn/out.png"}]}))
    provider = GptImage2Provider(api_key="k")
    await provider.generate_image("a sunset")
    body = json.loads(route.calls.last.request.content.decode())
    assert "quality" not in body


# ── D2 修复批：2.5 系尺寸贴档映射（白名单外尺寸 → 就近档 + 留痕字段） ─────
# 白名单 = spike 实测（temp/360_spike_0923_r2_sizes.py）：1024x1024 / 1280x720 /
# 1536x1024 通过；800x640 / 540x675 / 520x640 / 1920x1080 均 400。
# 映射规则：先按长宽比最近（对数距离，几何对称）、再按面积最近。
# REMOTION_SLOT_SIZES 贴档预期：800x640→1536x1024（|ln(1.25/1.5)|=0.182 <
# |ln(1.25/1.0)|=0.223）、540x675/520x640→1024x1024、1920x1080→1280x720、
# 1280x720 直通。


def test_slot_size_mappings_pinned() -> None:
    """remotion_course 六槽位尺寸的贴档结果 pin（白名单漂移即红）。"""
    from app.gateway.video_gen.providers.gpt_image2 import _map_size_to_whitelist

    assert _map_size_to_whitelist("800x640") == "1536x1024"  # ImageTextLeft/Right
    assert _map_size_to_whitelist("540x675") == "1024x1024"  # QuotePage
    assert _map_size_to_whitelist("520x640") == "1024x1024"  # EndingPage
    assert _map_size_to_whitelist("1920x1080") == "1280x720"  # FullVisualPage
    assert _map_size_to_whitelist("1280x720") == "1280x720"  # VideoClipPage 直通
    assert _map_size_to_whitelist("1024x1024") == "1024x1024"


@respx.mock
@pytest.mark.asyncio
async def test_size_outside_whitelist_mapped_and_flagged() -> None:
    """800x640 不在白名单 → wire 发映射后尺寸；ImageRef 携带 size_mapped_from。"""
    import json

    route = respx.post("https://api.360.cn/v1/images/generations").mock(return_value=httpx.Response(200, json={"data": [{"url": "https://cdn.360.cn/out.png"}]}))
    provider = GptImage2Provider(api_key="k", model="openai/gpt-image-2.5-flare", quality="high")
    ref = await provider.generate_image("an icon", size="800x640")
    body = json.loads(route.calls.last.request.content.decode())
    assert body["size"] == "1536x1024", "wire 必须发白名单内尺寸（发 800x640 即 400）"
    assert ref.size_mapped_from == "800x640"
    assert (ref.width, ref.height) == (1536, 1024)


@respx.mock
@pytest.mark.asyncio
async def test_size_inside_whitelist_not_mapped() -> None:
    import json

    route = respx.post("https://api.360.cn/v1/images/generations").mock(return_value=httpx.Response(200, json={"data": [{"url": "https://cdn.360.cn/out.png"}]}))
    provider = GptImage2Provider(api_key="k", model="openai/gpt-image-2.5-sunburst", quality="high")
    ref = await provider.generate_image("an icon", size="1280x720")
    body = json.loads(route.calls.last.request.content.decode())
    assert body["size"] == "1280x720"
    assert ref.size_mapped_from is None


@respx.mock
@pytest.mark.asyncio
async def test_v1_size_passthrough_no_mapping() -> None:
    """v1 gpt-image-2：size 原样透传、无映射无留痕字段（wire format 零变化）。"""
    import json

    route = respx.post("https://api.360.cn/v1/images/generations").mock(return_value=httpx.Response(200, json={"data": [{"url": "https://cdn.360.cn/out.png"}]}))
    provider = GptImage2Provider(api_key="k")
    ref = await provider.generate_image("an icon", size="800x640")
    body = json.loads(route.calls.last.request.content.decode())
    assert body["size"] == "800x640"
    assert ref.size_mapped_from is None
