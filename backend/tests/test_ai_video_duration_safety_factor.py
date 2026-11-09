"""E9 超发裁剪（2026-08-25 补齐）：请求时长 = min(单次上限, ceil(旁白 × 安全系数))。

计划 `docs/plans/2026-08-21-video-gen-360-switch-impl-plan.md` E9（L100/L104）：
- 时长计算：ceil(旁白 × 1.25) 且不超过单次上限（旁白 12.3s → 请求 15s；
  旁白 20s → 走续写而非超发）；
- 安全系数默认 1.25 可配（`video_gen.duration_safety_factor`）——保险项，
  欠交付率长期为 0 可调回 1.0（= 旧行为）。

语义：模型欠交付（产物 < 旁白）时 worker 只能 tpad 定格凑时长；×1.25 让请求
恒 ≥ 旁白，合成端永远走 `normalize_clip_resolution(max_duration=帧时长)` 的
无损裁剪（H5），轮不到定格。合成端裁剪的 worker 锁定用例（计划测试②）在
`test_ai_video_duration_alignment.py`。
"""

import pytest

from app.gateway.video_gen.config import VideoGenConfig
from app.gateway.video_gen.duration_policy import align_to_provider_grid
from app.gateway.video_gen.providers.base import VideoRef
from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame
from app.gateway.video_gen.visual_sources.ai_video import AIVideoVisualSource

# ── 时长计算（计划测试①，纯函数级）─────────────────────────────────────────


def test_factor_caps_at_provider_limit_float_grid():
    """旁白 12.3s × 1.25 = 15.375 → 封顶单次上限 15（float 栅格）。"""
    assert align_to_provider_grid(12.3, cap=15.0, integer_seconds=False, safety_factor=1.25) == 15.0


def test_factor_caps_at_provider_limit_integer_grid():
    """同上，integer 栅格：ceil(15.375)=16 → min(cap)=15。"""
    assert align_to_provider_grid(12.3, cap=15.0, integer_seconds=True, safety_factor=1.25) == 15.0


def test_factor_never_exceeds_single_pass_limit():
    """旁白 20s（>单次上限）→ 15.0：走续写而非超发，请求不得突破 cap。"""
    assert align_to_provider_grid(20.0, cap=15.0, integer_seconds=False, safety_factor=1.25) == 15.0


def test_factor_applies_before_ceil():
    """系数先于取整生效：8.4×1.25=10.5 → ceil 11（而非 ceil(8.4)=9）。"""
    assert align_to_provider_grid(8.4, cap=15.0, integer_seconds=True, safety_factor=1.25) == 11.0


def test_factor_ark_grid_ceil_and_floor():
    """Ark 栅格（integer + floor 4）：3.0×1.25=3.75 → ceil 4 → floor 4 → 4。"""
    assert align_to_provider_grid(3.0, cap=15.0, floor=4.0, integer_seconds=True, safety_factor=1.25) == 4.0


def test_factor_one_preserves_legacy_behavior():
    """safety_factor=1.0（配置回退）→ 与旧逻辑逐值一致。"""
    assert align_to_provider_grid(12.3, cap=15.0, integer_seconds=False, safety_factor=1.0) == 12.3
    assert align_to_provider_grid(12.3, cap=15.0, integer_seconds=True, safety_factor=1.0) == 13.0


def test_factor_default_kwarg_is_passthrough():
    """不传 safety_factor（默认 1.0）→ 既有调用方（续写段等）零影响。"""
    assert align_to_provider_grid(12.3, cap=15.0, integer_seconds=False) == 12.3


# ── source 接线（AIVideoVisualSource 默认 1.25）──────────────────────────────


class _RecordingProvider:
    max_video_duration = 15.0
    integer_seconds_only = False

    def __init__(self):
        self.received: list[float] = []

    async def generate_video(self, prompt: str, **kw):
        self.received.append(kw["duration"])
        return VideoRef(url="http://v/1.mp4", duration=kw["duration"], source="test")


def _sb(duration: float) -> Storyboard:
    return Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_video", width=1920, height=1080),
        frames=[StoryboardFrame(index=0, narration="n", image_prompt="p", duration=duration)],
    )


@pytest.mark.asyncio
async def test_source_overprovisions_by_default():
    """默认 1.25：旁白 12.3s → 请求 15.0（×1.25=15.375 封顶）。"""
    p = _RecordingProvider()
    src = AIVideoVisualSource(p, provider_name="seedance")
    await src.render(_sb(12.3), task_dir=None)
    assert p.received == [15.0]


@pytest.mark.asyncio
async def test_source_never_exceeds_cap_for_long_narration():
    """旁白 20s（续写场景）→ 请求 15.0：超发不突破单次上限（走续写而非超发）。"""
    p = _RecordingProvider()
    src = AIVideoVisualSource(p, provider_name="seedance")
    await src.render(_sb(20.0), task_dir=None)
    assert p.received == [15.0]


@pytest.mark.asyncio
async def test_source_factor_one_legacy():
    """duration_safety_factor=1.0 → 12.3 直发（配置级回退旧行为）。"""
    p = _RecordingProvider()
    src = AIVideoVisualSource(p, provider_name="seedance", duration_safety_factor=1.0)
    await src.render(_sb(12.3), task_dir=None)
    assert p.received == [12.3]


# ── config 字段（E10 同款模式）──────────────────────────────────────────────


def test_config_default_factor():
    assert VideoGenConfig().duration_safety_factor == 1.25


def test_config_factor_overridable():
    assert VideoGenConfig(duration_safety_factor=1.0).duration_safety_factor == 1.0


# ── factory 接线（config → source；防「拆线测试照绿」——评审 P3-2）──────────
# ctor 默认与 config 默认同为 1.25 时，visual_factory 不传参测试也全绿；
# 只有显式回退（1.0）经 factory 生效，接线才算真实存在。


class _StubProviders:
    def make_ai_video(self, name=None):  # noqa: ANN001,ANN002,ANN201,ARG002
        return _RecordingProvider()

    def make_ai_image(self, name=None):  # noqa: ANN001,ANN002,ANN201,ARG002
        return object()


def test_factory_single_source_wires_config_factor():
    """make_visual_source("ai_video") 须把 config.duration_safety_factor 接进 source。"""
    from app.gateway.video_gen.visual_factory import make_visual_source

    cfg = VideoGenConfig(duration_safety_factor=1.0)
    src = make_visual_source("ai_video", providers=_StubProviders(), config=cfg)
    assert src._duration_safety_factor == 1.0


def test_factory_mixed_source_wires_config_factor():
    """make_visual_source_mixed 同路径接线（mixed 是混合任务的生产构造点）。"""
    from app.gateway.video_gen.visual_factory import make_visual_source_mixed

    cfg = VideoGenConfig(duration_safety_factor=1.0)
    sb = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_video", width=1920, height=1080),
        frames=[StoryboardFrame(index=0, narration="n", image_prompt="p", duration=5.0)],
    )
    mixed = make_visual_source_mixed(sb, providers=_StubProviders(), config=cfg)
    assert mixed._sources["ai_video"]._duration_safety_factor == 1.0
