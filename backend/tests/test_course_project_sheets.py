"""Task 9: CourseProject 升级——character_sheets 三视图 + scene_style_refs 场景风格。

项目作为跨引擎共享增强层（不驱动引擎选择）。三视图 front/side/back 固化角色，
场景风格参考图统一画面风格。from_dict 兼容旧项目无字段。
"""

import json
from unittest.mock import MagicMock

import pytest

from app.gateway.video_gen.course_models import CharacterSheet, CourseProject


def test_character_sheets_field_default_empty():
    p = CourseProject(id="cp_dddddddddddd", user_id="u1", title="t")
    assert p.character_sheets == []
    assert p.scene_style_refs == []


def test_character_sheet_roundtrip():
    p = CourseProject(
        id="cp_dddddddddddd",
        user_id="u1",
        title="t",
        character_sheets=[
            CharacterSheet(
                name="小明",
                front_url="/img/f.png",
                side_url="/img/s.png",
                back_url="/img/b.png",
                features="短发，蓝衣",
            )
        ],
        scene_style_refs=["/img/style1.png"],
    )
    d = p.to_dict()
    assert d["character_sheets"][0]["front_url"] == "/img/f.png"
    p2 = CourseProject.from_dict(d)
    assert len(p2.character_sheets) == 1
    assert isinstance(p2.character_sheets[0], CharacterSheet)
    assert p2.character_sheets[0].name == "小明"
    assert p2.character_sheets[0].front_url == "/img/f.png"
    assert p2.scene_style_refs == ["/img/style1.png"]


def test_old_project_without_sheets_loads():
    """旧项目 JSON 无 character_sheets/scene_style_refs 字段 → 默认空，不崩。"""
    old = {
        "id": "x",
        "user_id": "u1",
        "title": "t",
        "visual_style_preset": "default",
    }
    p = CourseProject.from_dict(old)
    assert p.character_sheets == []
    assert p.scene_style_refs == []


def test_style_prefix_includes_scene_refs():
    """C2（决策 7，2026-08-24 计划）改写：scene_style_refs 拼接已退役。

    原 Task 14 断言「URL 被拼进 style_prefix」——该字段名存实亡（URL 当文字进 prompt，
    图片从未进生图参考图通道），用户拍板静默退役。新断言 = 前缀干净 + 风格翻译仍生效。
    """
    p = CourseProject(
        id="cp_dddddddddddd",
        user_id="u1",
        title="t",
        visual_style_preset="cinematic",
        scene_style_refs=["/img/style1.png"],
    )
    prefix = p.style_prefix
    assert "电影质感" in prefix, prefix
    assert "style1.png" not in prefix and "场景风格" not in prefix, f"拼接已退役，实际={prefix!r}"


def test_read_character_sheets_for_vimax(tmp_path):
    """Task 13: ViMax 注入——项目有 character_sheets → 读出三视图供复用。"""
    import json

    from app.gateway.video_gen.service import _read_course_project_character_sheets

    pdir = tmp_path / "course_projects" / "u1"
    pdir.mkdir(parents=True)
    (pdir / "cp_dddddddddddd.json").write_text(
        json.dumps(
            {
                "id": "cp_dddddddddddd",
                "user_id": "u1",
                "title": "t",
                "character_sheets": [
                    {
                        "name": "小明",
                        "front_url": "/f.png",
                        "side_url": "/s.png",
                        "back_url": "/b.png",
                        "features": "短发",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    sheets = _read_course_project_character_sheets(str(tmp_path), "cp_dddddddddddd", "u1")
    assert len(sheets) == 1
    assert sheets[0].front_url == "/f.png"
    assert sheets[0].side_url == "/s.png"


def test_read_character_sheets_empty_when_no_project(tmp_path):
    """无项目 → 返回空列表（不崩）。"""
    from app.gateway.video_gen.service import _read_course_project_character_sheets

    assert _read_course_project_character_sheets(str(tmp_path), None, "u1") == []


# ---------------------------------------------------------------------------
# Task 12 Step 4: service 层把项目三视图 front_url 作 reference_image_paths 注入
# ai_image render（既有 Task 12 只加了 visual_sources 参数，service 调用点未透传——补齐）。
# ---------------------------------------------------------------------------


def test_character_ref_paths_from_sheets():
    """payload 有 character_sheets → 返回 [front_url]（ai_image i2i reference）。"""
    from app.gateway.video_gen.service import _character_ref_paths_from_payload

    payload = {"character_sheets": [{"front_url": "/img/front.png"}]}
    assert _character_ref_paths_from_payload(payload) == ["/img/front.png"]


def test_character_ref_paths_falls_back_to_character_reference(tmp_path):
    """无 sheets 但有 character_reference → 回退 [reference]，仍给 ai_image 锚定。

    X11-4（2026-08-27 计划）坏路径守卫：值须为既有文件/URL/data: 才作 ref——
    纯文本描述（字段 UI 语义）不作 ref（防中文描述流向 360 edits）。"""
    from app.gateway.video_gen.service import _character_ref_paths_from_payload

    f = tmp_path / "r.png"
    f.write_bytes(b"\x89PNG")
    assert _character_ref_paths_from_payload({"character_reference": str(f)}) == [str(f)]


def test_character_ref_paths_none_when_empty():
    """无项目角色 → None（ai_image 不带 reference，向后兼容）。"""
    from app.gateway.video_gen.service import _character_ref_paths_from_payload

    assert _character_ref_paths_from_payload({}) is None


def test_inject_project_character_context_reads_fresh(tmp_path):
    """Task 12 Step 4: render 前重读项目 character_sheets 注入 payload（拾取最新改动）。"""
    from app.gateway.video_gen.service import _inject_project_character_context

    pdir = tmp_path / "course_projects" / "u1"
    pdir.mkdir(parents=True)
    (pdir / "cp_dddddddddddd.json").write_text(
        json.dumps(
            {
                "id": "cp_dddddddddddd",
                "user_id": "u1",
                "title": "t",
                "character_sheets": [{"name": "小明", "front_url": "/f.png"}],
            }
        ),
        encoding="utf-8",
    )
    payload = {"course_project_id": "cp_dddddddddddd"}
    _inject_project_character_context(str(tmp_path), payload, "u1")
    assert payload["character_sheets"][0]["front_url"] == "/f.png"
    assert payload["character_reference"] == "/f.png"


def test_inject_project_character_context_no_project_is_noop(tmp_path):
    from app.gateway.video_gen.service import _inject_project_character_context

    payload: dict = {}
    _inject_project_character_context(str(tmp_path), payload, "u1")
    assert "character_sheets" not in payload
    assert "character_reference" not in payload


def test_inject_project_character_context_force_overwrites_stale(tmp_path):
    """Task 20c: force=True 覆盖 payload 既有（过期）sheets，拾取项目最新——
    用于「项目已更新，重新生成」强制重跑视觉让新角色生效。"""
    from app.gateway.video_gen.service import _inject_project_character_context

    pdir = tmp_path / "course_projects" / "u1"
    pdir.mkdir(parents=True)
    (pdir / "cp_dddddddddddd.json").write_text(
        json.dumps(
            {
                "id": "cp_dddddddddddd",
                "user_id": "u1",
                "title": "t",
                "character_sheets": [{"name": "新小明", "front_url": "/new.png"}],
            }
        ),
        encoding="utf-8",
    )
    payload = {
        "course_project_id": "cp_dddddddddddd",
        "character_sheets": [{"front_url": "/stale.png"}],
    }
    _inject_project_character_context(str(tmp_path), payload, "u1", force=True)
    assert payload["character_sheets"][0]["front_url"] == "/new.png"
    assert payload["character_reference"] == "/new.png"


@pytest.mark.asyncio
async def test_generate_character_sheet_from_description(monkeypatch, tmp_path):
    """Task 10 方式 A：文字描述 → 三视图，side/back 用 front 作 reference。
    #14: front/side/back 须下载落盘为本地路径（不存 TOS URL）。
    """
    from app.gateway.video_gen import character_sheet_generator as csg
    from app.gateway.video_gen.character_sheet_generator import generate_character_sheet

    calls = []

    async def fake_gen(prompt, *, reference_image=None):
        calls.append((prompt, reference_image))
        return MagicMock(url=f"http://img/{len(calls)}.png")

    # #14: 桩下载（不真发 HTTP），把假 bytes 落盘
    async def fake_download(url, dest):
        from pathlib import Path

        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(b"fake-png")

    monkeypatch.setattr(csg, "_download_image_bytes", fake_download, raising=False)

    sheet = await generate_character_sheet(
        description="穿蓝色校服的短发男孩",
        name="小明",
        features="短发，蓝色校服",
        image_gen=fake_gen,
        storage_dir=str(tmp_path),
        project_id="cp_dddddddddddd",
    )
    assert isinstance(sheet, CharacterSheet)
    # A1: front/side/back 须是本地绝对路径（非 http TOS URL；绝对路径让 _to_data_uri 可解析，相对路径 os.path.exists 按 CWD 失败→Ark 4xx）
    # 文件名是 <name>_<uuid8>.png（_local_rel：唯一，防多次生成覆盖）→ 断言结构而非精确路径
    sheet_dir = str(tmp_path / "character_sheets" / "cp_dddddddddddd")
    for url, name in ((sheet.front_url, "front"), (sheet.side_url, "side"), (sheet.back_url, "back")):
        assert url.startswith(f"{sheet_dir}/{name}_") and url.endswith(".png"), f"{name} 须落盘 character_sheets/<pid>/<name>_<uuid8>.png（got {url}）"
    assert not sheet.front_url.startswith("http"), "front 不得存 TOS URL"
    # side/back 应以 front（绝对路径）为 reference——provider 桥 _to_data_uri 转 data-URI
    assert calls[1][1] == sheet.front_url, "side 应用 front 绝对路径作 reference"
    assert calls[2][1] == sheet.front_url, "back 应用 front 绝对路径作 reference"


@pytest.mark.asyncio
async def test_save_uploaded_character_sheet(tmp_path):
    """Task 11 方式 B：上传 front/side/back 图 → 落盘 CharacterSheet。"""
    from app.gateway.video_gen.character_sheet_generator import save_uploaded_character_sheet

    sheet = await save_uploaded_character_sheet(
        name="小明",
        features="短发",
        front_bytes=b"png1",
        side_bytes=b"png2",
        back_bytes=b"png3",
        storage_dir=str(tmp_path),
        project_id="cp_dddddddddddd",
    )
    assert sheet.front_url.startswith(str(tmp_path)) or "/character_sheets/" in sheet.front_url
    assert sheet.side_url and sheet.back_url
    # 文件实际写入
    from pathlib import Path

    assert Path(sheet.front_url).exists()
    assert Path(sheet.back_url).read_bytes() == b"png3"
