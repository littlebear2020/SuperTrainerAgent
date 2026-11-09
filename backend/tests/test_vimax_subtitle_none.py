"""行为锁：ViMax 选「无字幕」必须真的没有字幕。

复核确认（2026-07-29）：ViMax 不过 build_worker_payload（_should_handoff_to_worker
只含 ai_image/ai_video/ai_visual），烧录由全局 vimax_burn_subtitles（默认 True）
决定；per-task subtitle_style 只用于选样式串，而 _SUBTITLE_STYLE_MAP 无 "none" 键
→ 静默回落 default → 用户选「无字幕」照样得到字幕。

方向与 segments 引擎那条 P0 相反（那边是选了字幕却没有），同一根因：
「谁拥有烧录决策」不清楚。
"""

from app.gateway.video_gen.subtitle_filter import subtitle_force_style


def test_none_style_yields_no_burn_decision():
    """should_burn_subtitles: 全局开 + per-task none → 不烧。"""
    from app.gateway.video_gen.service import should_burn_subtitles

    assert should_burn_subtitles(global_burn=True, subtitle_style="none") is False
    assert should_burn_subtitles(global_burn=True, subtitle_style="default") is True
    assert should_burn_subtitles(global_burn=False, subtitle_style="default") is False
    assert should_burn_subtitles(global_burn=True, subtitle_style=None) is True


def test_global_off_still_wins_when_style_set():
    """全局关掉时，per-task 有样式也不烧——本函数只让「none」多一条否决权。"""
    from app.gateway.video_gen.service import should_burn_subtitles

    assert should_burn_subtitles(global_burn=False, subtitle_style="bold") is False
    assert should_burn_subtitles(global_burn=False, subtitle_style="none") is False


def test_force_style_none_is_not_silently_default():
    """ "none" 不得静默回落成 default 样式串（掩盖配置错误）。"""
    assert subtitle_force_style("none") == ""


def test_force_style_known_values_unchanged():
    for style in ("default", "bold", "minimal", "outline"):
        assert subtitle_force_style(style), f"{style} 应有样式串"


def test_force_style_unknown_still_falls_back_to_default():
    """未知值仍回落 default——只有显式的 "none" 才代表「不要字幕」。"""
    assert subtitle_force_style("no_such_style") == subtitle_force_style("default")
