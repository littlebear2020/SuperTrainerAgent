"""E5（360 通道切换，2026-08-21）：providers 列表接口的能力/风险标注元数据。

口径（决策 D5）：**实测过的标实测结论，未实测的一律标「未检测」，不做推断性标注**。
依据 docs/plans/2026-08-21-video-gen-360-switch-impl-plan.md E5 + 姊妹篇实测记录
（T1~T8，temp/v1_probe）。kimi-k2.7 类不适用项不出现在可选名单（KNOWN 拓扑既有约束）。
"""

from __future__ import annotations

from app.gateway.video_gen.config import VideoGenConfig
from app.gateway.video_gen.providers.registry import ProviderRegistry

_ANNOTATION_KEYS = {"reference_images", "tested", "notes"}


def _all_items():
    reg = ProviderRegistry(VideoGenConfig(qihu_api_key="k", ark_api_key="a"))
    return reg.available_video_providers() + reg.available_image_providers()


def test_every_provider_item_has_annotations():
    """接口返回含元数据字段（reference_images / tested / notes）。"""
    items = _all_items()
    assert items
    for item in items:
        ann = item.get("annotations")
        assert isinstance(ann, dict), f"{item['name']} 缺 annotations"
        assert _ANNOTATION_KEYS <= set(ann.keys()), f"{item['name']} annotations 键不全: {ann}"


def test_annotations_cover_known_providers_consistently():
    """与注册表一致：KNOWN 名单里每个 provider 都有标注（缺失=默认「未检测」回填，
    不允许 KeyError/静默丢项）。"""
    items = {i["name"]: i for i in _all_items()}
    for name in ProviderRegistry.KNOWN_VIDEO_PROVIDERS + ProviderRegistry.KNOWN_IMAGE_PROVIDERS:
        assert name in items, f"KNOWN 名单里的 {name} 未出现在接口"
        assert items[name]["annotations"]["tested"], f"{name} tested 不得为空串"


def test_untested_providers_marked_as_such_no_inference():
    """未实测的（fast 未跑探针）标「未检测」，不得推断成已验证。"""
    items = {i["name"]: i["annotations"] for i in _all_items()}
    assert items["seedance-fast"]["tested"] == "未检测"
    # fast 提示文案带能力事实（官方文档口径，非实测推断）
    assert "720p" in items["seedance-fast"]["notes"]


def test_tested_providers_carry_evidence_text():
    """实测过的标实测结论（seedance=主力探针对象；seedream-360=T3 参考图）。"""
    items = {i["name"]: i["annotations"] for i in _all_items()}
    assert "实测" in items["seedance"]["tested"]
    assert items["seedance"]["tested"] != "未检测"
    assert items["seedream-360"]["tested"] != "未检测"
    assert items["seedream-360"]["reference_images"] is True


def test_kimi_not_in_selectable_lists():
    """kimi-k2.7 类不适用项不出现在可选名单。"""
    names = {i["name"] for i in _all_items()}
    assert not any("kimi" in n for n in names)
