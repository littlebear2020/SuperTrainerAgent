"""E2（360 通道切换，2026-08-21）：registry 透传模型名 + fast/mini 型号选项。

依据 docs/plans/2026-08-21-video-gen-360-switch-impl-plan.md E2：
- registry 构建 seedance 时传入 ``config.seedance_video_model``（修前用类默认，
  config 字段是死旋钮）+ ``seedance_video_resolution``（E1 接线）；
- fast/mini 型号作为用户可选项（模型 id 为 2026-08-21 GET /v1/models 探针实测：
  ``bytedance/seedance-2-0-fast`` / ``bytedance/doubao-seedance-2-0-mini``）；
- 未知型号名仍抛错（既有行为不回归）。
"""

from __future__ import annotations

import pytest

from app.gateway.video_gen.config import VideoGenConfig
from app.gateway.video_gen.providers.registry import ProviderRegistry


def _inner(provider):
    """剥 Throttle/Fallback 包裹，取最内层 provider（测试专用）。"""
    cur = provider
    while hasattr(cur, "_inner"):
        cur = cur._inner
    while hasattr(cur, "_primary"):
        cur = cur._primary
    return cur


def test_registry_builds_seedance_with_config_model():
    """registry 构建的 seedance provider 模型名 == config.seedance_video_model。"""
    cfg = VideoGenConfig(qihu_api_key="k", seedance_video_model="bytedance/seedance-2-0")
    reg = ProviderRegistry(cfg)
    inner = _inner(reg.ai_video)
    from app.gateway.video_gen.providers.seedance import SeedanceVideoProvider

    assert isinstance(inner, SeedanceVideoProvider)
    assert inner._model == "bytedance/seedance-2-0"


def test_registry_passes_resolution_config():
    """E1 的 resolution 配置经 registry 接线到 provider。"""
    cfg = VideoGenConfig(qihu_api_key="k", seedance_video_model="bytedance/seedance-2-0", seedance_video_resolution="720p")
    reg = ProviderRegistry(cfg)
    assert _inner(reg.ai_video)._resolution == "720p"


@pytest.mark.parametrize(
    ("name", "expected_model"),
    [
        ("seedance-fast", "bytedance/seedance-2-0-fast"),
    ],
)
def test_fast_options_buildable_with_probe_confirmed_ids(name: str, expected_model: str):
    """fast 作为用户可选项可构建且模型名正确（探针实测 id，非猜测）。
    2026-08-28：seedance-mini 随拍板从下拉摘除（factory 分支保留但不可具名调用）。"""
    reg = ProviderRegistry(VideoGenConfig(qihu_api_key="k"))
    p = reg.make_ai_video(name)
    assert _inner(p)._model == expected_model


def test_fast_in_known_list_and_availability():
    """fast 在 KNOWN_VIDEO_PROVIDERS；配了 qihu key 即 available。
    2026-08-28：seedance-mini/happyhorse 已摘除（实现保留），断言其不在名单。"""
    assert "seedance-fast" in ProviderRegistry.KNOWN_VIDEO_PROVIDERS
    assert "seedance-mini" not in ProviderRegistry.KNOWN_VIDEO_PROVIDERS
    assert "happyhorse" not in ProviderRegistry.KNOWN_VIDEO_PROVIDERS
    reg = ProviderRegistry(VideoGenConfig(qihu_api_key="k"))
    names = {i["name"]: i["available"] for i in reg.available_video_providers()}
    assert names.get("seedance-fast") is True
    assert "seedance-mini" not in names
    assert "happyhorse" not in names


def test_unknown_video_model_still_raises():
    """未知型号名仍抛错（既有行为不回归）。"""
    reg = ProviderRegistry(VideoGenConfig(qihu_api_key="k"))
    with pytest.raises(ValueError, match="未知的生视频模型"):
        reg.make_ai_video("seedance-ultra")


def test_fast_capability_registered():
    """新型号进能力表（test_every_known_video_provider_is_registered 同款契约）。
    2026-08-28：mini 已摘除，fast 保留。"""
    from app.gateway.video_gen.providers.capabilities import video_capability

    for name in ("seedance-fast",):
        cap = video_capability(name)
        assert cap.max_duration == 15.0
        assert cap.supports_image_urls is True
