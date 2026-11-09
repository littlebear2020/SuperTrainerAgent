"""P0-3: target_duration 双向约束——事前字数反推(script.py 已有,补测试) + 事后偏差回传。

根因：service.py target_duration 仅转 max_scenes 上限(软约束)，120s 请求出 56s。
修复：_target_to_scene_bounds 作下限保证(每镜8s) + _check_duration_drift 偏差>20% 回传。
"""

from app.gateway.video_gen.orchestration import script as script_mod
from app.gateway.video_gen.service import (
    _check_duration_drift,
    _resolve_n_scenes,
    _target_to_scene_bounds,
)


def test_duration_within_tolerance_no_warning():
    assert _check_duration_drift(actual=105.0, target=120.0) is None


def test_duration_drift_too_short_warning():
    w = _check_duration_drift(actual=56.0, target=120.0)
    assert w is not None
    assert "短" in w or "short" in w.lower()


def test_duration_drift_too_long_warning():
    w = _check_duration_drift(actual=160.0, target=120.0)
    assert w is not None
    assert "长" in w or "long" in w.lower()


def test_no_target_no_warning():
    assert _check_duration_drift(actual=56.0, target=None) is None


def test_scenes_floor_for_target():
    """target_duration 120s → max_scenes 至少够撑 120s（每镜 ~8s → ≥15）。"""
    max_scenes, _max_shots = _target_to_scene_bounds(target_duration=120)
    assert max_scenes >= 15, f"120s 应至少 15 镜，实际 {max_scenes}"


def test_no_target_returns_default_bounds():
    max_scenes, _ = _target_to_scene_bounds(target_duration=None)
    assert max_scenes >= 8  # 有合理默认


def test_compute_word_bounds_reverse_calc():
    """反馈点 2：script.py 已有 target_duration→字数反推，测它算对。

    120s / 8 镜 = 15s/镜；3.33 字/s × 15s ≈ 50 字；min_words = 50×0.8 = 40。
    """
    min_w, max_w = script_mod._compute_word_bounds(target_duration=120, n_scenes=8, tts_speed=1.0)
    assert min_w >= 30, f"120s/8镜 min_words 应≥30，实际 {min_w}"
    assert max_w > min_w


def test_resolve_n_scenes_explicit_wins():
    assert _resolve_n_scenes({"n_scenes": 12, "target_duration": 120}) == 12


def test_resolve_n_scenes_lower_bound_from_target():
    """H: 120s → max(8, 15) = 15 镜（之前死代码、默认 5 镜无视 target）。"""
    assert _resolve_n_scenes({"target_duration": 120}) == 15


def test_resolve_n_scenes_minimum_8():
    assert _resolve_n_scenes({"target_duration": 30}) == 8  # max(8, 3)


def test_resolve_n_scenes_default_when_nothing():
    assert _resolve_n_scenes({}) == 5


def test_resolve_n_scenes_seconds_per_frame_wins():
    """E4: seconds_per_frame=5 + target=60 → 12 帧（用户值胜过 8s 下限的 8 帧）。"""
    assert _resolve_n_scenes({"target_duration": 60, "seconds_per_frame": 5}) == 12


def test_resolve_n_scenes_seconds_per_frame_clamped():
    """E4: seconds_per_frame 限幅 3-8——spf=2 按 3 算（ceil(60/3)=20），spf=99 按 8 算。"""
    assert _resolve_n_scenes({"target_duration": 60, "seconds_per_frame": 2}) == 20
    assert _resolve_n_scenes({"target_duration": 60, "seconds_per_frame": 99}) == 8  # ceil(60/8)=8


def test_split_fixed_script_merges_to_target_count():
    """E4: Path C 长脚本（12 句）+ target_count=4 → 合并到 4 段（不爆 54 帧），内容不丢。"""
    from app.gateway.video_gen.orchestration import script as script_mod

    text = "第一句。第二句。第三句。第四句。第五句。第六句。第七句。第八句。第九句。第十句。第十一句。第十二句。"
    parts = script_mod.split_fixed_script(text, target_count=4)
    assert len(parts) == 4, f"须合并到 4 段，got {len(parts)}"
    assert "第一句" in parts[0] and "第十二句" in parts[-1]  # 内容不丢


def test_split_fixed_script_no_target_count_unchanged():
    """E4: 无 target_count 时保持现状（1 句=1 帧，不强制合并）。"""
    from app.gateway.video_gen.orchestration import script as script_mod

    text = "第一句。第二句。第三句。"
    parts = script_mod.split_fixed_script(text)
    assert len(parts) == 3
