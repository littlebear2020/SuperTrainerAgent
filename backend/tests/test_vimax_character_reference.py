"""HR-T4: ViMax 用户角色参考图接入。

CourseProject.character_reference 已存在但 ViMax 不消费。链路：
gateway _read_course_project_character_reference 读项目 → build_vimax_config 透传 →
vendored CharacterPortraitsGenerator.__init__ 收 character_reference →
generate_front_portrait 传 reference_image_paths 给 generate_single_image（bridge 已支持）。
"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

# ── gateway: 从 CourseProject 解析 character_reference ──────────────────────


def test_read_character_reference_from_project(tmp_path):
    """项目有 character_reference → 返回路径。"""
    from app.gateway.video_gen.service import _read_course_project_character_reference

    storage = tmp_path / "storage"
    proj_dir = storage / "course_projects" / "u1"
    proj_dir.mkdir(parents=True)
    (proj_dir / "cp_aaaaaaaaaaaa.json").write_text(
        json.dumps({"id": "cp_aaaaaaaaaaaa", "user_id": "u1", "title": "T", "character_reference": "/data/ref.png"}),
        encoding="utf-8",
    )
    ref = _read_course_project_character_reference(str(storage), "cp_aaaaaaaaaaaa", "u1")
    assert ref == "/data/ref.png"


def test_read_character_reference_none_when_no_project(tmp_path):
    """无 project_id 或无文件 → None。"""
    from app.gateway.video_gen.service import _read_course_project_character_reference

    assert _read_course_project_character_reference(str(tmp_path), None, "u1") is None
    assert _read_course_project_character_reference(str(tmp_path), "nope", "u1") is None


def test_read_character_reference_none_when_field_missing(tmp_path):
    """项目存在但无 character_reference → None。"""
    from app.gateway.video_gen.service import _read_course_project_character_reference

    proj_dir = tmp_path / "course_projects" / "u1"
    proj_dir.mkdir(parents=True)
    (proj_dir / "cp_aaaaaaaaaaaa.json").write_text(json.dumps({"id": "cp_aaaaaaaaaaaa", "user_id": "u1", "title": "T"}), encoding="utf-8")
    assert _read_course_project_character_reference(str(tmp_path), "cp_aaaaaaaaaaaa", "u1") is None


# ── build_vimax_config 透传 character_reference ──────────────────────────────


def test_build_vimax_config_includes_character_reference(tmp_path):
    """build_vimax_config(character_reference=...) → dict 含 character_reference。"""
    from app.gateway.video_gen.engines.vimax.provider_bridge import build_vimax_config

    cfg = build_vimax_config(
        working_dir=tmp_path,
        llm_model="m",
        llm_base_url="u",
        llm_api_key="k",
        image_api_key="k",
        image_base_url="u",
        video_api_key="k",
        video_base_url="u",
        character_reference="/ref.png",
    )
    assert cfg.get("character_reference") == "/ref.png"


# ── vendored: generate_front_portrait 注入 reference_image_paths ─────────────


def _character():
    from vimax.interfaces import CharacterInScene

    return CharacterInScene(
        idx=0,
        identifier_in_scene="主角",
        is_visible=True,
        static_features="young male, short black hair",
        dynamic_features="wearing a blue jacket",
    )


@pytest.mark.asyncio
async def test_front_portrait_passes_reference_image(tmp_path):
    """真实磁盘文件 reference → generate_front_portrait 传 reference_image_paths。"""
    from vimax.agents.character_portraits_generator import CharacterPortraitsGenerator

    ref = tmp_path / "ref.png"
    ref.write_bytes(b"fake-png")
    gen = CharacterPortraitsGenerator(image_generator=MagicMock(), character_reference=str(ref))
    gen.image_generator.generate_single_image = AsyncMock(return_value=MagicMock())
    await gen.generate_front_portrait(_character(), "写实")

    call = gen.image_generator.generate_single_image.await_args
    assert call.kwargs.get("reference_image_paths") == [str(ref)]


@pytest.mark.asyncio
async def test_url_reference_passed_as_image_path():
    """URL reference → 透传 reference_image_paths。"""
    from vimax.agents.character_portraits_generator import CharacterPortraitsGenerator

    gen = CharacterPortraitsGenerator(
        image_generator=MagicMock(),
        character_reference="https://example.com/char.png",
    )
    gen.image_generator.generate_single_image = AsyncMock(return_value=MagicMock())
    await gen.generate_front_portrait(_character(), "写实")

    call = gen.image_generator.generate_single_image.await_args
    assert call.kwargs.get("reference_image_paths") == ["https://example.com/char.png"]


@pytest.mark.asyncio
async def test_text_character_reference_not_passed_as_image_path():
    """纯文本角色描述（字段 UI 语义）绝不能进 reference_image_paths——

    否则 NanoBanana._normalize_reference 原样返回，中文描述被当图片 POST
    到 /v1/images/edits → 360 报错 → ViMax 肖像管线必挂。文本应注入 prompt。"""
    from vimax.agents.character_portraits_generator import CharacterPortraitsGenerator

    gen = CharacterPortraitsGenerator(
        image_generator=MagicMock(),
        character_reference="一位戴眼镜的年轻女讲师，白色衬衫",
    )
    gen.image_generator.generate_single_image = AsyncMock(return_value=MagicMock())
    await gen.generate_front_portrait(_character(), "写实")

    call = gen.image_generator.generate_single_image.await_args
    # 不作为图片路径
    assert not call.kwargs.get("reference_image_paths")
    # 仍通过 prompt 影响肖像生成
    assert "戴眼镜" in call.kwargs.get("prompt", "")


@pytest.mark.asyncio
async def test_front_portrait_no_reference_when_unset():
    """无 character_reference → generate_single_image 不传 reference_image_paths（向后兼容）。"""
    from vimax.agents.character_portraits_generator import CharacterPortraitsGenerator

    gen = CharacterPortraitsGenerator(image_generator=MagicMock())
    gen.image_generator.generate_single_image = AsyncMock(return_value=MagicMock())
    await gen.generate_front_portrait(_character(), "写实")

    call = gen.image_generator.generate_single_image.await_args
    assert not call.kwargs.get("reference_image_paths")
