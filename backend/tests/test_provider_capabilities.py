"""Task 13: 声明式 provider 能力表。"""

import pytest

from app.gateway.video_gen.providers.capabilities import VideoCapability, video_capability


def _make_provider(name: str):  # noqa: ANN202
    """按注册名构造裸 provider（能力声明只需类/实例属性，不发请求）。"""
    if name == "seedance-ark":
        from app.gateway.video_gen.providers.seedance_ark import SeedanceArkVideoProvider

        return SeedanceArkVideoProvider(api_key="x")
    if name == "minimax-h3":
        from app.gateway.video_gen.providers.qihu_video import QihuVideoProvider

        return QihuVideoProvider(api_key="x", model="minimax/MiniMax-H3")
    if name == "seedance":
        from app.gateway.video_gen.providers.seedance import SeedanceVideoProvider

        return SeedanceVideoProvider(api_key="x")
    if name == "happyhorse":
        from app.gateway.video_gen.providers.happyhorse import HappyhorseVideoProvider

        return HappyhorseVideoProvider(api_key="x")
    raise ValueError(f"unknown provider: {name}")


def test_seedance_ark_capability() -> None:
    cap = video_capability("seedance-ark")
    assert cap.max_duration == 15.0
    assert cap.min_duration == 4.0
    assert cap.integer_seconds is True
    assert cap.supports_image_urls is True


def test_unknown_provider_gets_conservative_default() -> None:
    """未知 provider 不得乐观假设——8s 上限、无 i2v，与 ai_video.py 的 getattr 默认一致。"""
    cap = video_capability("brand-new-model")
    assert cap.max_duration == 8.0
    assert cap.supports_image_urls is False
    assert isinstance(cap, VideoCapability)


# T3（2026-08-18）：一致性锁从硬编码 seedance-ark 扩为参数化——修前给表条目加
# 字段而类上不声明，现有测试全绿不拦（一致性锁的洞）；H3 声明按 _is_h3_model
# 门控，故用实例（property）而非类属性比对。seedance/happyhorse 随 T3 扩展步纳入。
@pytest.mark.parametrize("name", ["seedance-ark", "minimax-h3", "seedance", "happyhorse"])
def test_capability_matches_provider_class_attrs(name: str) -> None:
    """表与 provider 类属性必须一致——否则前端校验与实际下发会分叉。"""
    p = _make_provider(name)
    cap = video_capability(name)
    assert cap.max_duration == p.max_video_duration
    assert cap.min_duration == p.min_video_duration
    assert cap.supports_image_urls == p.supports_image_urls
    assert cap.integer_seconds == p.integer_seconds_only


def test_every_known_video_provider_is_registered() -> None:
    """名单里每个 provider 都要在表里显式登记——落到保守默认等于静默错报能力。"""
    from app.gateway.video_gen.providers import registry as reg
    from app.gateway.video_gen.providers.capabilities import _VIDEO_CAPABILITIES

    missing = [n for n in reg.ProviderRegistry.KNOWN_VIDEO_PROVIDERS if n not in _VIDEO_CAPABILITIES]
    assert missing == [], f"以下 provider 未登记能力：{missing}"


def test_capability_is_immutable() -> None:
    """能力表是共享单例，可变会让一处改动污染全进程。"""
    import dataclasses

    import pytest

    cap = video_capability("seedance-ark")
    with pytest.raises(dataclasses.FrozenInstanceError):
        cap.max_duration = 99.0  # type: ignore[misc]


def test_available_video_providers_include_capability() -> None:
    """能力元数据随可用列表下发，前端无需为每个新模型改代码。"""
    from app.gateway.video_gen.config import VideoGenConfig
    from app.gateway.video_gen.providers.registry import ProviderRegistry

    reg = ProviderRegistry(VideoGenConfig(qihu_api_key="k"))
    items = reg.available_video_providers()
    ark = next(i for i in items if i["name"] == "seedance-ark")
    assert "capability" in ark, "可用列表应带 capability"
    assert ark["capability"]["max_duration"] == 15.0
    assert ark["capability"]["integer_seconds"] is True


def test_availability_does_not_construct_providers() -> None:
    """能力端点**故意不实例化** provider——曾因构造泄漏句柄（2→29, commit 4b3413471）。"""
    from app.gateway.video_gen.config import VideoGenConfig
    from app.gateway.video_gen.providers.registry import ProviderRegistry

    reg = ProviderRegistry(VideoGenConfig(qihu_api_key="k", ark_api_key="a"))
    before = len(reg._closables)
    for _ in range(3):
        reg.available_video_providers()
    assert len(reg._closables) == before, "查能力不得构造 provider（句柄泄漏回潮）"


def test_image_list_unaffected() -> None:
    """本 task 只做 video 侧（image 无时长概念，YAGNI）。"""
    from app.gateway.video_gen.config import VideoGenConfig
    from app.gateway.video_gen.providers.registry import ProviderRegistry

    reg = ProviderRegistry(VideoGenConfig(qihu_api_key="k"))
    items = reg.available_image_providers()
    assert items and all("capability" not in i for i in items)
