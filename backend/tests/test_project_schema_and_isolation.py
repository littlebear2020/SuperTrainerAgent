"""E14: project 端点 schema 校验 + E15: 跨用户 project 隔离。"""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ── E14: create/update_project 须用 Pydantic schema（未知字段→422、错类型→422）────────


def _app_with_router():
    from fastapi import FastAPI

    from app.gateway.routers.video_gen import router

    app = FastAPI()
    app.include_router(router)
    app.state.video_gen_service = MagicMock(_storage_dir="/tmp/video-gen-isolation")
    return app


@pytest.mark.asyncio
async def test_create_project_rejects_unknown_field():
    """未知字段 → 422（修前 body: dict 静默丢弃未知字段）。"""
    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(transport=ASGITransport(app=_app_with_router()), base_url="http://test") as client:
        resp = await client.post("/api/video/projects", json={"title": "x", "evil_field": "hack"})
        assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_project_rejects_wrong_type():
    """已知字段传错类型（color_palette 传字符串）→ 422（修前存盘后下游崩）。"""
    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(transport=ASGITransport(app=_app_with_router()), base_url="http://test") as client:
        resp = await client.post("/api/video/projects", json={"title": "x", "color_palette": "not-a-dict"})
        assert resp.status_code == 422


@pytest.mark.asyncio
async def test_update_project_rejects_unknown_field():
    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(transport=ASGITransport(app=_app_with_router()), base_url="http://test") as client:
        resp = await client.put("/api/video/projects/cp_aaaaaaaaaaaa", json={"title": "x", "evil_field": "hack"})
        assert resp.status_code == 422


# ── E15: 三个同源函数不得跨用户 iterdir 命中──────────────────────────────────────


def _seed_project(base: Path, user: str, project_id: str, character_reference="/ref.png", sheets=None):
    d = Path(base) / "course_projects" / user
    d.mkdir(parents=True, exist_ok=True)
    data = {
        "id": project_id,
        "user_id": user,
        "title": "t",
        "character_reference": character_reference,
        "character_sheets": sheets or [],
    }
    (d / f"{project_id}.json").write_text(json.dumps(data), encoding="utf-8")


def test_read_character_reference_no_cross_user(tmp_path):
    """user_a 拥有 project；user_b 查 → None（不靠 iterdir 跨用户命中）。"""
    from app.gateway.video_gen.service import _read_course_project_character_reference

    _seed_project(tmp_path, "user_a", "cp_aaaaaaaaaaaa", character_reference="/a_ref.png")
    # user_b 直接路径不存在 → 必须返回 None（修前 fallback 跨用户扫到 user_a 的）
    assert _read_course_project_character_reference(tmp_path, "cp_aaaaaaaaaaaa", "user_b") is None
    # 属主 user_a 正常读到
    assert _read_course_project_character_reference(tmp_path, "cp_aaaaaaaaaaaa", "user_a") == "/a_ref.png"


def test_read_character_sheets_no_cross_user(tmp_path):
    """user_b 查 user_a 的 project → []（不跨用户泄漏 sheets）。"""
    from app.gateway.video_gen.service import _read_course_project_character_sheets

    _seed_project(tmp_path, "user_a", "cp_aaaaaaaaaaaa", sheets=[{"name": "hero", "front_url": "/f.png"}])
    assert _read_course_project_character_sheets(tmp_path, "cp_aaaaaaaaaaaa", "user_b") == []
    sheets = _read_course_project_character_sheets(tmp_path, "cp_aaaaaaaaaaaa", "user_a")
    assert len(sheets) == 1 and sheets[0].name == "hero"


def test_load_course_project_style_no_cross_user(tmp_path):
    """_load_course_project_style：user_b 查 user_a 的 project → {}（不跨用户）。"""
    from app.gateway.video_gen.service import VideoGenService

    _seed_project(tmp_path, "user_a", "cp_aaaaaaaaaaaa")
    svc = VideoGenService.__new__(VideoGenService)
    svc._storage_dir = str(tmp_path)
    # user_b 查 → 空（修前 iterdir fallback 扫到 user_a 的）
    assert svc._load_course_project_style("cp_aaaaaaaaaaaa", user_id="user_b") == {}
    # 属主 user_a 正常加载（有 style_prefix 之类）
    style = svc._load_course_project_style("cp_aaaaaaaaaaaa", user_id="user_a")
    assert isinstance(style, dict)
