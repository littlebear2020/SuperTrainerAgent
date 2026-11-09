"""BUG-B（2026-08-03）：能力属性必须穿透生产包裹链。

生产上 provider **一律**经 ``ThrottledAIVideoProvider``（+ 可选
``FallbackAIVideoProvider``）包裹后才交给 ``AIVideoVisualSource``。修前
``min_video_duration`` / ``integer_seconds_only`` 没有透传 property，也无
``__getattr__`` 兜底 → ``ai_video.fill()`` 的 getattr 恒取兜底值
（floor=0.0 / integer_only=False），Task 3 的栅格对齐在生产链上**完全不生效**。

原单测（test_ai_video_duration_validation）用裸 MagicMock 直接声明这些属性，
绕过包裹层，所以全绿却测的是生产里不存在的配置——本文件用**真实包裹链**补上。
"""

import pytest

from app.gateway.video_gen.duration_policy import align_to_provider_grid
from app.gateway.video_gen.providers.qihu_video import QihuVideoProvider
from app.gateway.video_gen.providers.registry import FallbackAIVideoProvider
from app.gateway.video_gen.providers.seedance_ark import SeedanceArkVideoProvider
from app.gateway.video_gen.providers.throttle import ThrottledAIVideoProvider


def _ark() -> SeedanceArkVideoProvider:
    return SeedanceArkVideoProvider(api_key="x", base_url="x", model="m")


def _qihu() -> QihuVideoProvider:
    return QihuVideoProvider(api_key="x", base_url="x", model="m")


def _throttled(inner):  # noqa: ANN001,ANN202
    return ThrottledAIVideoProvider(inner, qps=0.5, max_retries=5)


def test_grid_attrs_survive_throttle_wrapper() -> None:
    t = _throttled(_ark())
    assert t.max_video_duration == 15.0
    assert t.min_video_duration == 4.0, "修复前为 0.0（属性未透传）"
    assert t.integer_seconds_only is True, "修复前为 False（属性未透传）"


def test_grid_attrs_survive_throttle_and_fallback_wrappers() -> None:
    """min_video_duration / integer_seconds_only 必须经 Throttle→Fallback 包裹后仍可见。"""
    chain = FallbackAIVideoProvider(_throttled(_ark()), _throttled(_qihu()))
    assert chain.max_video_duration == 15.0
    assert chain.min_video_duration == 4.0
    assert chain.integer_seconds_only is True


def test_non_ark_provider_keeps_conservative_defaults() -> None:
    """qihu 未声明栅格属性 → 包裹后仍是保守默认（不得凭空变成 Ark 的值）。"""
    t = _throttled(_qihu())
    assert t.min_video_duration == 0.0
    assert t.integer_seconds_only is False


def test_alignment_actually_works_on_production_chain() -> None:
    """端到端判据：用生产链的能力值算对齐，必须得到设计值。

    这是 BUG-B 的用户可感后果——修前 8.9 → 8.9（浮点直发，Ark 端 round 兜底，
    且 round(8.5)=8 是 banker's rounding，方向与「向上取整」相反）。
    """
    chain = FallbackAIVideoProvider(_throttled(_ark()), _throttled(_qihu()))
    floor = float(getattr(chain, "min_video_duration", 0.0) or 0.0)
    integer_only = bool(getattr(chain, "integer_seconds_only", False))
    cap = getattr(chain, "max_video_duration", 8.0)

    assert align_to_provider_grid(8.9, cap=cap, floor=floor, integer_seconds=integer_only) == 9.0
    assert align_to_provider_grid(8.5, cap=cap, floor=floor, integer_seconds=integer_only) == 9.0, "banker's rounding 会给 8"
    assert align_to_provider_grid(2.5, cap=cap, floor=floor, integer_seconds=integer_only) == 4.0, "须抬到 Ark 下限"
    assert align_to_provider_grid(20.0, cap=cap, floor=floor, integer_seconds=integer_only) == 15.0


@pytest.mark.parametrize("attr", ["supports_image_urls", "max_video_duration", "min_video_duration", "integer_seconds_only", "supports_tail_frame"])
def test_all_capability_attrs_declared_on_both_wrappers(attr: str) -> None:
    """防漂移：新增能力属性时两个包裹层都要补，漏一个就会重演 BUG-B。"""
    assert hasattr(ThrottledAIVideoProvider, attr), f"ThrottledAIVideoProvider 缺 {attr} 透传"
    assert hasattr(FallbackAIVideoProvider, attr), f"FallbackAIVideoProvider 缺 {attr} 透传"


def test_capability_table_matches_wrapped_chain() -> None:
    """Task 13 能力表下发给前端的值，必须与生产链实际暴露的一致。

    修前表说 min=4.0/integer=True，链上却是 0.0/False —— 前端校验与实际下发分叉。
    """
    from app.gateway.video_gen.providers.capabilities import video_capability

    cap = video_capability("seedance-ark")
    chain = FallbackAIVideoProvider(_throttled(_ark()), _throttled(_qihu()))
    assert cap.min_duration == chain.min_video_duration
    assert cap.integer_seconds == chain.integer_seconds_only
    assert cap.max_duration == chain.max_video_duration
    assert cap.supports_image_urls == chain.supports_image_urls


# ── T1（2026-08-17 H3 duration 400 修复批）：H3 / seedance-360 栅格上生产链 ──


def _h3() -> QihuVideoProvider:
    return QihuVideoProvider(api_key="x", base_url="x", model="minimax/MiniMax-H3")


def _seedance360():
    from app.gateway.video_gen.providers.seedance import SeedanceVideoProvider

    return SeedanceVideoProvider(api_key="x", base_url="x")


def test_h3_grid_survives_wrappers() -> None:
    """H3 栅格（4/整数/15）必须穿透 Throttle（生产链 ai_video.fill 的实际读法）。"""
    t = _throttled(_h3())
    assert t.max_video_duration == 15.0
    assert t.min_video_duration == 4.0
    assert t.integer_seconds_only is True


def test_seedance_360_grid_survives_wrappers() -> None:
    t = _throttled(_seedance360())
    assert t.max_video_duration == 15.0
    assert t.min_video_duration == 4.0
    assert t.integer_seconds_only is True


def test_h3_alignment_end_to_end() -> None:
    """端到端判据（事故数值）：3.84s 旁白经生产链对齐必须得 4.0（修前浮点直发 3s -> 400）。"""
    t = _throttled(_h3())
    assert (
        align_to_provider_grid(
            3.84,
            cap=getattr(t, "max_video_duration", 8.0),
            floor=float(getattr(t, "min_video_duration", 0.0) or 0.0),
            integer_seconds=bool(getattr(t, "integer_seconds_only", False)),
        )
        == 4.0
    )


def _happyhorse():
    from app.gateway.video_gen.providers.happyhorse import HappyhorseVideoProvider

    return HappyhorseVideoProvider(api_key="x", base_url="x")


@pytest.mark.parametrize("name,ctor", [("minimax-h3", _h3), ("seedance", _seedance360), ("happyhorse", _happyhorse)])
def test_capability_table_matches_wrapped_chain_new_grid(name: str, ctor) -> None:
    """防漂移（P1-1）：静态能力表下发给前端的值 == 生产包裹链实际暴露的值。

    T3（2026-08-18）比对维度加 supports_image_urls、参数加 happyhorse——修前
    只比 min/integer/max 三项，给表加字段而类上不声明（或反之）均全绿不拦。
    """
    from app.gateway.video_gen.providers.capabilities import video_capability

    cap = video_capability(name)
    chain = _throttled(ctor())
    assert cap.min_duration == chain.min_video_duration
    assert cap.integer_seconds == chain.integer_seconds_only
    assert cap.max_duration == chain.max_video_duration
    assert cap.supports_image_urls == chain.supports_image_urls


@pytest.mark.parametrize("name,ctor", [("minimax-h3", _h3), ("seedance", _seedance360), ("happyhorse", _happyhorse)])
def test_i2v_capability_survives_throttle(name: str, ctor) -> None:
    """T3（2026-08-18）：声明 supports_image_urls 后必须穿透 Throttle——生产链
    ai_video.fill 的 supports_image_urls 闸门（refs 忽略判定/preview 继承/T-S2
    组锚定）读的正是包裹层（记忆教训：能力必须经真实包裹链验证）。"""
    t = _throttled(ctor())
    assert t.supports_image_urls is True, f"{name} i2v 能力经 Throttle 不可见 = 生产闸门仍判不支持"
