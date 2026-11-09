# backend/tests/test_preview_all_skip_no_prompt.py
from types import SimpleNamespace

from app.gateway.routers.video_gen import missing_prompt_message


def _frame(image_prompt, visual_url=None):
    return SimpleNamespace(image_prompt=image_prompt, visual_url=visual_url)


def test_empty_prompt_frame_flagged():
    assert missing_prompt_message(_frame("")) is not None
    assert missing_prompt_message(_frame(None)) is not None
    assert missing_prompt_message(_frame("   ")) is not None


def test_normal_frame_not_flagged():
    assert missing_prompt_message(_frame("办公室场景")) is None


def test_dict_frame_flagged():
    """_preview_all_bg 循环里 frame 是 storyboard.json 原始 dict，非 StoryboardFrame。"""
    assert missing_prompt_message({"index": 3, "image_prompt": ""}) is not None
    assert missing_prompt_message({"index": 3, "image_prompt": "办公室场景"}) is None
    assert missing_prompt_message({"index": 3}) is not None
