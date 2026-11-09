"""Task 3k: image_prompt 后处理追加质量词（按 style 选配），提升生图上限。

2026-08-12（prompt-optimization-v1 T1）：_QUALITY_SUFFIX 从泛化「高质量」
改为每风格具体技术词（realistic→浅景深/自然光线/材质质感 等），
_negative suffix 删除（设计反转，详见 test_prompt_quality_suffix.py 文件头）。
"""

from app.gateway.video_gen.orchestration.storyboard_planner import _enhance_prompt_quality


def test_prompt_gets_quality_suffix():
    enhanced = _enhance_prompt_quality("a teacher in classroom", style="realistic")
    assert "浅景深" in enhanced
    assert "high quality" not in enhanced.lower(), "不得残留英文质量词"


def test_different_style_different_suffix():
    realistic = _enhance_prompt_quality("a girl", style="realistic")
    anime = _enhance_prompt_quality("a girl", style="anime")
    assert realistic != anime
    assert "赛璐珞" in anime


def test_default_style_has_quality():
    enhanced = _enhance_prompt_quality("a scene", style="default")
    assert "质感" in enhanced
    assert "high quality" not in enhanced.lower(), "不得残留英文质量词"
