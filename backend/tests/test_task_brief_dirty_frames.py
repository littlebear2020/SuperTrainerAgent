"""Test TaskBrief exposes dirty_frames as list[int] (parsed from JSON string)."""

from app.gateway._shared.task_framework.schemas import TaskBrief


def test_task_brief_parses_dirty_frames_json_string():
    brief = TaskBrief(
        id="t1",
        user_id="u1",
        domain="video_gen",
        task_type="ai_image",
        status="ready",
        progress=100,
        dirty_frames="[0, 3, 5]",
    )
    assert brief.dirty_frames == [0, 3, 5]


def test_task_brief_dirty_frames_defaults_empty_list():
    brief = TaskBrief(
        id="t1",
        user_id="u1",
        domain="video_gen",
        task_type="ai_image",
        status="ready",
        progress=100,
    )
    assert brief.dirty_frames == []


def test_task_brief_from_attributes_parses_task_row_string():
    """The real usage: model_validate(task_row, from_attributes=True)."""

    class FakeRow:
        id = "t1"
        user_id = "u1"
        domain = "video_gen"
        task_type = "ai_image"
        status = "ready"
        progress = 100
        title = None
        error_message = None
        retry_count = 0
        dirty_frames = "[1, 2]"
        created_at = None
        updated_at = None
        thumbnail_url = None

    brief = TaskBrief.model_validate(FakeRow(), from_attributes=True)
    assert brief.dirty_frames == [1, 2]


def test_task_brief_dirty_frames_accepts_list_input():
    """If already a list (e.g. from another Brief), pass through."""
    brief = TaskBrief(
        id="t1",
        user_id="u1",
        domain="video_gen",
        task_type="ai_image",
        status="ready",
        progress=100,
        dirty_frames=[4, 5],
    )
    assert brief.dirty_frames == [4, 5]
