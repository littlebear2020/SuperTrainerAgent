"""Task 16: 视觉源工厂抽出后行为不变（守护测试）。

⚠️ 计划里的测试签名与真实实现不符，已按**实现为准**改写（守护测试的目的是锁住
现状，不是规定新行为）：
- ``_has_mixed_sources`` 收的是 **Storyboard**（要读 ``config.video_type`` 作缺省），
  不是 ``frames`` 列表。故 ``has_mixed_sources(storyboard)``。
- 因此「visual_source 全为 None」**不是** not-mixed 因为 None 被折叠，而是因为
  它们都回落到同一个 ``config.video_type``；空 frames 集合为空 → len 0 → False。
"""

from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame
from app.gateway.video_gen.visual_factory import has_mixed_sources


def _sb(*sources: str | None, video_type: str = "ai_image") -> Storyboard:
    cfg = StoryboardConfig(video_type=video_type, width=1080, height=1920)
    frames = [StoryboardFrame(index=i, narration="n", visual_source=s) for i, s in enumerate(sources)]
    return Storyboard(title="t", config=cfg, frames=frames)


def test_single_source_not_mixed() -> None:
    assert has_mixed_sources(_sb("ai_image", "ai_image")) is False


def test_two_sources_is_mixed() -> None:
    assert has_mixed_sources(_sb("ai_image", "ai_video")) is True


def test_none_source_falls_back_to_config_type() -> None:
    """visual_source 全 None → 都回落 config.video_type → 单一来源。"""
    assert has_mixed_sources(_sb(None, None, video_type="ai_video")) is False


def test_none_mixed_with_explicit_other_is_mixed() -> None:
    """None 回落成 ai_image，与显式 ai_video 并存 → 混合（真实生产形态）。"""
    assert has_mixed_sources(_sb(None, "ai_video", video_type="ai_image")) is True


def test_none_matching_config_type_not_mixed() -> None:
    """显式值与 config 缺省相同 → 仍是单一来源。"""
    assert has_mixed_sources(_sb(None, "ai_image", video_type="ai_image")) is False


def test_empty_frames_not_mixed() -> None:
    assert has_mixed_sources(_sb()) is False


def test_service_methods_delegate() -> None:
    """service 的四个方法保留为薄委托，调用点零改动。"""
    import inspect

    from app.gateway.video_gen.service import VideoGenService

    for name, target in (
        ("_has_mixed_sources", "has_mixed_sources"),
        ("_make_visual_source", "make_visual_source"),
        ("_make_visual_source_mixed", "make_visual_source_mixed"),
        ("_make_visual_budget", "make_visual_budget"),
    ):
        src = inspect.getsource(getattr(VideoGenService, name))
        assert target in src, f"{name} 应委托给 visual_factory.{target}"


def test_budget_unit_costs_preserved() -> None:
    """单价必须与 provider_bridge 保持一致（video 1.5 / image 0.3）——搬迁不得改数。"""
    from types import SimpleNamespace

    from app.gateway.video_gen.visual_factory import make_visual_budget

    cfg = SimpleNamespace(ai_video_max_gen=7, ai_image_max_gen=9, ai_visual_budget_cny=100.0)
    vb = make_visual_budget("video", config=cfg)
    ib = make_visual_budget("image", config=cfg)
    assert vb._unit_cost_cny == 1.5
    assert ib._unit_cost_cny == 0.3
    assert vb._max_calls == 7
    assert ib._max_calls == 9


def test_vimax_still_raises_misconfiguration_error() -> None:
    """vimax 走 legacy 路径是配置错误，必须显式报错（vg_7477487923ef 根因）。"""
    import pytest

    from app.gateway.video_gen.visual_factory import make_visual_source

    with pytest.raises(ValueError, match="engine_arch"):
        make_visual_source("vimax", providers=None, config=None)


def test_unknown_task_type_raises() -> None:
    import pytest

    from app.gateway.video_gen.visual_factory import make_visual_source

    with pytest.raises(ValueError, match="not implemented"):
        make_visual_source("comic", providers=None, config=None)
