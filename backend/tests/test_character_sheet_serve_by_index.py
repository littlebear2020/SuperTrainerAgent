"""F2: serve 路由 + storage 边界（修 Bug 1 后端；M1 起按稳定 id 寻址——下标随删除/排序漂移会串图）。"""

import pytest

from app.gateway.routers.video_gen import _resolve_sheet_image_path
from app.gateway.video_gen.course_models import CharacterSheet, CourseProject


def _proj(sheets):
    p = CourseProject(id="p1", user_id="u", title="t")
    p.character_sheets = sheets
    return p


def test_resolve_returns_sheet_id_path() -> None:
    """M1 改写：按稳定 id 解析（原按数组下标——id 化后 0/1 会被当 id 查而恒 404）。"""
    proj = _proj([CharacterSheet(id="aaaa1111", front_url="/a/front.png"), CharacterSheet(id="bbbb2222", front_url="/b/front.png")])
    assert str(_resolve_sheet_image_path(proj, "aaaa1111", "front")) == "/a/front.png"
    assert str(_resolve_sheet_image_path(proj, "bbbb2222", "front")) == "/b/front.png"


def test_resolve_bad_tag_404() -> None:
    with pytest.raises(Exception):
        _resolve_sheet_image_path(_proj([CharacterSheet(id="aaaa1111", front_url="/a")]), "aaaa1111", "top")


def test_resolve_unknown_id_404() -> None:
    """M1 改写：未知 id（含旧式数字下标串）→ 404。"""
    with pytest.raises(Exception):
        _resolve_sheet_image_path(_proj([CharacterSheet(id="aaaa1111", front_url="/a")]), "zzzz9999", "front")


def test_resolve_missing_url_404() -> None:
    with pytest.raises(Exception):
        _resolve_sheet_image_path(_proj([CharacterSheet(id="aaaa1111", front_url="")]), "aaaa1111", "side")
