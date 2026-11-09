"""P1-E8: 项目 color_palette 必须进入 StoryboardConfig（修前定义了但从不传值）。

链路：course_projects/{user}/{pid}.json → StoryboardConfig.color_palette
→ build_worker_payload asdict → worker _ffmpeg_colorgrade。
"""

import json

from app.gateway.video_gen.service import _read_course_project_color_palette


def test_read_color_palette_roundtrip(tmp_path):
    proj_dir = tmp_path / "course_projects" / "u1"
    proj_dir.mkdir(parents=True)
    (proj_dir / "cp_dddddddddddd.json").write_text(
        json.dumps({"id": "cp_dddddddddddd", "user_id": "u1", "title": "t", "color_palette": {"primary": "#FF0000", "grade": "warm"}}),
        encoding="utf-8",
    )
    assert _read_course_project_color_palette(tmp_path, "cp_dddddddddddd", "u1") == {"primary": "#FF0000", "grade": "warm"}


def test_read_color_palette_missing_returns_none(tmp_path):
    """无 project_id / 文件不存在 → None（worker 跳过 colorgrade）。"""
    assert _read_course_project_color_palette(tmp_path, None, "u1") is None
    assert _read_course_project_color_palette(tmp_path, "nonexistent", "u1") is None


def test_read_color_palette_none_when_field_absent(tmp_path):
    """项目存在但未设 color_palette → None（不爆，不误调色）。"""
    proj_dir = tmp_path / "course_projects" / "u1"
    proj_dir.mkdir(parents=True)
    (proj_dir / "cp_dddddddddddd.json").write_text(json.dumps({"id": "cp_dddddddddddd", "user_id": "u1", "title": "t"}), encoding="utf-8")
    assert _read_course_project_color_palette(tmp_path, "cp_dddddddddddd", "u1") is None


def test_read_color_palette_corrupt_file_returns_none(tmp_path):
    """损坏 JSON → None + 不抛（容错，镜像 character_reference 的 try/except）。"""
    proj_dir = tmp_path / "course_projects" / "u1"
    proj_dir.mkdir(parents=True)
    (proj_dir / "cp_dddddddddddd.json").write_text("{not valid json", encoding="utf-8")
    assert _read_course_project_color_palette(tmp_path, "cp_dddddddddddd", "u1") is None
