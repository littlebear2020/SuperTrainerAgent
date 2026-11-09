"""Tests for Week 3 remediation: CourseProject CRUD, style_context injection."""

from unittest.mock import MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_course_project_crud_full():
    import tempfile

    from app.gateway.video_gen.course_project_store import CourseProjectStore

    with tempfile.TemporaryDirectory() as tmpdir:
        store = CourseProjectStore(base_dir=tmpdir)
        proj = store.create(user_id="user1", title="领导力培训", visual_style_preset="flat illustration", color_palette={"primary": "#2C3E50"})
        assert proj.id.startswith("cp_")
        # 2026-08-01（Task 2 Step 4b）：style_prefix 已中文化，英文字面量不再原样进 prompt。
        # 本用例意图是「CRUD 后 style_prefix 有内容」，意图不变，仅期望值改为中文。
        assert "扁平插画" in proj.style_prefix, proj.style_prefix
        assert "flat illustration" not in proj.style_prefix

        fetched = store.get(proj.id, user_id="user1")
        assert fetched.title == "领导力培训"

        projects = store.list(user_id="user1")
        assert len(projects) == 1

        updated = store.update(proj.id, user_id="user1", title="高级领导力")
        assert updated.title == "高级领导力"

        store.delete(proj.id, user_id="user1")
        assert store.get(proj.id, user_id="user1") is None


@pytest.mark.asyncio
async def test_course_project_api_create_and_get():
    import tempfile

    from fastapi import FastAPI

    from app.gateway.routers.video_gen import router

    app = FastAPI()
    app.include_router(router)

    with tempfile.TemporaryDirectory() as tmpdir:
        mock_svc = MagicMock()
        mock_svc._storage_dir = tmpdir
        app.state.video_gen_service = mock_svc

        with patch("app.gateway.routers.video_gen.get_effective_user_id", return_value="default"):
            from httpx import ASGITransport, AsyncClient

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                resp = await client.post(
                    "/api/video/projects",
                    json={
                        "title": "测试项目",
                        "visual_style_preset": "flat illustration",
                        "color_palette": {"primary": "#2C3E50"},
                    },
                )
                assert resp.status_code == 200
                data = resp.json()
                assert data["id"].startswith("cp_")
                assert data["title"] == "测试项目"

                # GET
                proj_id = data["id"]
                resp2 = await client.get(f"/api/video/projects/{proj_id}")
                assert resp2.status_code == 200

                # LIST
                resp3 = await client.get("/api/video/projects")
                assert resp3.status_code == 200
                assert len(resp3.json()["projects"]) == 1

                # PUT (update)
                resp4 = await client.put(
                    f"/api/video/projects/{proj_id}",
                    json={"title": "更新后的项目", "visual_style_preset": "watercolor"},
                )
                assert resp4.status_code == 200
                assert resp4.json()["title"] == "更新后的项目"
                assert resp4.json()["visual_style_preset"] == "watercolor"

                # Verify update persisted via GET
                resp5 = await client.get(f"/api/video/projects/{proj_id}")
                assert resp5.json()["visual_style_preset"] == "watercolor"

                # Cleanup
                await client.delete(f"/api/video/projects/{proj_id}")


@pytest.mark.asyncio
async def test_load_course_project_style():
    """_load_course_project_style should return style_context from a CourseProject file."""
    import json
    import tempfile
    from pathlib import Path

    from app.gateway.video_gen.config import VideoGenConfig
    from app.gateway.video_gen.service import VideoGenService

    with tempfile.TemporaryDirectory() as tmpdir:
        svc = VideoGenService(repo=MagicMock(), config=VideoGenConfig(), providers=MagicMock())
        svc._storage_dir = tmpdir

        # Create a project file
        proj_dir = Path(tmpdir) / "course_projects" / "default"
        proj_dir.mkdir(parents=True)
        (proj_dir / "cp_bbbbbbbbbbbb.json").write_text(
            json.dumps({"id": "cp_bbbbbbbbbbbb", "user_id": "default", "title": "test", "visual_style_preset": "flat illustration"}),
            encoding="utf-8",
        )

        ctx = svc._load_course_project_style("cp_bbbbbbbbbbbb")
        assert "style_prefix" in ctx
        # 2026-08-01（Task 2 Step 4b）：同上，存量英文值须被翻译成中文。
        assert "扁平插画" in ctx["style_prefix"], ctx["style_prefix"]
        assert "flat illustration" not in ctx["style_prefix"]

        # Non-existent project returns empty dict
        assert svc._load_course_project_style("cp_cccccccccccc") == {}
        assert svc._load_course_project_style(None) == {}
