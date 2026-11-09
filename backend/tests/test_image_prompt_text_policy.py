# backend/tests/test_image_prompt_text_policy.py
from app.gateway.video_gen.orchestration.storyboard_planner import (
    _IMAGE_PROMPT_SYSTEM,
    _TEXT_POLICY_INSTRUCTION,
)


def test_image_prompt_system_uses_text_policy():
    assert _TEXT_POLICY_INSTRUCTION in _IMAGE_PROMPT_SYSTEM


def test_old_no_text_clause_removed():
    assert "全程无字幕" not in _IMAGE_PROMPT_SYSTEM
    assert "无任何文字" not in _IMAGE_PROMPT_SYSTEM
