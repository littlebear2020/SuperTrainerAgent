import json

from app.gateway.video_gen.course_models import CourseOutline, CourseProject, OutlineChapter
from app.gateway.video_gen.course_project_store import CourseProjectStore


def test_outline_chapter_defaults():
    ch = OutlineChapter(title="领导力基础", key_points=["定义", "维度"])
    assert ch.title == "领导力基础"
    assert ch.key_points == ["定义", "维度"]
    assert ch.estimated_minutes == 3


def test_course_outline_creation():
    outline = CourseOutline(
        title="新经理培训课程",
        chapters=[
            OutlineChapter(title="第一章", key_points=["a", "b"], estimated_minutes=5),
            OutlineChapter(title="第二章", key_points=["c"], estimated_minutes=3),
        ],
        total_estimated_minutes=8,
    )
    assert len(outline.chapters) == 2
    assert outline.total_estimated_minutes == 8


def test_course_outline_to_dict():
    outline = CourseOutline(
        title="测试课程",
        chapters=[OutlineChapter(title="ch1", key_points=["p1"])],
        total_estimated_minutes=3,
    )
    data = outline.to_dict()
    assert data["title"] == "测试课程"
    assert data["chapters"][0]["title"] == "ch1"
    # round-trip JSON
    json.dumps(data)


def test_course_outline_from_dict():
    data = {
        "title": "课程",
        "chapters": [{"title": "ch", "key_points": ["p"], "estimated_minutes": 5}],
        "total_estimated_minutes": 5,
    }
    outline = CourseOutline.from_dict(data)
    assert outline.title == "课程"
    assert outline.chapters[0].title == "ch"


def test_course_project_defaults():
    proj = CourseProject(id="cp_abc", user_id="u1", title="我的课程")
    assert proj.visual_style_preset == "default"
    assert proj.character_reference is None
    assert proj.voice_id == ""


def test_course_project_store_persists_character_reference(tmp_path):
    """character_reference (the P1 editor field) must round-trip through the store
    so the CourseProject detail editor's save actually persists. Guards the generic
    CourseProjectStore.update path against silently dropping the field.
    """
    store = CourseProjectStore(base_dir=tmp_path)
    proj = store.create(user_id="u1", title="AI 入门课程")

    updated = store.update(proj.id, "u1", character_reference="水彩风格，主角为戴眼镜的女孩")

    reloaded = store.get(proj.id, "u1")
    assert reloaded is not None
    assert updated.character_reference == "水彩风格，主角为戴眼镜的女孩"
    assert reloaded.character_reference == "水彩风格，主角为戴眼镜的女孩"

    # Clearing via empty string also persists (frontend sends "" to clear).
    cleared = store.update(proj.id, "u1", character_reference="")
    assert cleared.character_reference == ""
    assert store.get(proj.id, "u1").character_reference == ""
