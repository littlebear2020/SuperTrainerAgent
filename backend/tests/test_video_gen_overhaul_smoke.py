"""Task 21: 端到端验收 smoke test——验证四批次核心函数协同工作。

非真实视频生成（需 provider/docker），而是验证本次 overhaul 的关键函数
都能正确 import + 基本行为正确，作为回归守护。
"""

from app.gateway.video_gen.course_models import CharacterSheet, CourseProject
from app.gateway.video_gen.orchestration.script import (
    _annotate_emotion,
    _compute_word_bounds,
)
from app.gateway.video_gen.orchestration.storyboard_planner import (
    _enhance_prompt_quality,
)
from app.gateway.video_gen.service import (
    _check_duration_drift,
    _extract_subtitle_status,
)
from app.gateway.video_gen.subtitle_engine import layout_two_lines


# ── 批次 1 P0 ──────────────────────────────────────────────────────────────
def test_p0_duration_two_way_constraint():
    assert _check_duration_drift(56.0, 120.0) is not None  # 短了→告警
    assert _check_duration_drift(105.0, 120.0) is None  # 容差内→无


def test_p0_subtitle_status_extractable(tmp_path):
    import json

    (tmp_path / "subtitle_status.json").write_text(json.dumps({"status": "failed", "error": "x"}))
    assert _extract_subtitle_status(tmp_path)["status"] == "failed"


def test_p0_subtitle_wrap():
    # 2026-08-14 B1：_wrap_subtitle 已删，等价断言走字幕引擎 layout_two_lines
    assert "\\N" in layout_two_lines("一个很长的字幕需要断行显示在画面底部区域", 13.0)


# ── 批次 2 引擎+项目 ────────────────────────────────────────────────────────
def test_project_character_sheets_roundtrip():
    p = CourseProject(id="p1", user_id="u1", title="t", character_sheets=[CharacterSheet(name="X", front_url="/f.png")])
    p2 = CourseProject.from_dict(p.to_dict())
    assert len(p2.character_sheets) == 1 and p2.character_sheets[0].front_url == "/f.png"


def test_compute_word_bounds():
    mn, mx = _compute_word_bounds(120, 8, 1.0)
    assert mn >= 30 and mx > mn


# ── 批次 3 成片效果 ──────────────────────────────────────────────────────────
def test_prompt_quality_enhancement():
    assert "浅景深" in _enhance_prompt_quality("scene", "realistic")


def test_ssml_emotion():
    assert "high" in _annotate_emotion("为何？", "question")["ssml"]
