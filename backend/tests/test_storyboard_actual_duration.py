"""Task 1: actual_visual_duration 字段 + 前后兼容。"""

from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame


def test_actual_visual_duration_defaults_to_none() -> None:
    f = StoryboardFrame(index=0, narration="n")
    assert f.actual_visual_duration is None


def test_actual_visual_duration_survives_roundtrip() -> None:
    cfg = StoryboardConfig(video_type="ai_video", width=720, height=1280)
    f = StoryboardFrame(index=0, narration="n", duration=8.9, actual_visual_duration=5.0)
    sb = Storyboard(title="t", config=cfg, frames=[f])
    back = Storyboard.from_dict(sb.to_dict())
    assert back.frames[0].actual_visual_duration == 5.0


def test_old_storyboard_without_field_still_loads() -> None:
    """旧 storyboard.json 无此键 → 不得报错（from_dict 过滤未知键的反向保证）。"""
    data = {
        "title": "t",
        "config": {"video_type": "ai_video", "width": 720, "height": 1280},
        "frames": [{"index": 0, "narration": "n", "duration": 5.0}],
    }
    sb = Storyboard.from_dict(data)
    assert sb.frames[0].actual_visual_duration is None
