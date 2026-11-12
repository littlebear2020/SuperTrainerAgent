"""Isolated real-API acceptance for the workspace experience batch.

Threads and projects are created through authenticated HTTP APIs. Video tasks and
console runs are database fixtures in a spawned process and are read through the
same real APIs; no task-creation or paid-provider endpoint is called.
"""

from __future__ import annotations

import asyncio
import json
import multiprocessing
import os
import time
import traceback
from pathlib import Path

import pytest

USER_A = "11111111-1111-1111-1111-111111111111"
USER_B = "22222222-2222-2222-2222-222222222222"
CSRF = "task8-isolated-csrf"
CONSOLE_PATHS = (
    "/api/console/stats",
    "/api/console/runs",
    "/api/console/usage",
    "/api/console/llm-usage",
    "/api/console/external-calls",
)


def _create_thread(client, thread_id: str, metadata: dict, title: str) -> None:
    headers = {"X-CSRF-Token": CSRF}
    response = client.post(
        "/api/threads",
        json={"thread_id": thread_id, "metadata": metadata},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    response = client.post(
        f"/api/threads/{thread_id}/state",
        json={"values": {"title": title}},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    time.sleep(0.002)


async def _run_isolated(root: Path) -> dict:
    os.chdir(root)
    home = root / "deer-flow-home"
    database_dir = root / "database"
    video_storage = root / "video-storage"
    for directory in (home, database_dir, video_storage):
        directory.mkdir(parents=True, exist_ok=True)
    import yaml

    config_path = root / "isolated-config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "models": [],
                "database": {"backend": "sqlite", "sqlite_dir": str(database_dir)},
                "video_gen": {"storage_dir": str(video_storage)},
            }
        ),
        encoding="utf-8",
    )
    os.environ["DEER_FLOW_HOME"] = str(home)
    os.environ["DEER_FLOW_CONFIG_PATH"] = str(config_path)
    os.environ["AUTH_JWT_SECRET"] = "task8-test-only-jwt-secret-32bytes"
    os.environ.pop("RUN_LIVE", None)

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import app.gateway.content.models  # noqa: F401
    import app.gateway.voice_twin.models  # noqa: F401
    import deerflow.persistence.external_api_calls.model  # noqa: F401
    from app.gateway._shared.task_framework.repository import TaskRepository
    from app.gateway.auth.config import AuthConfig, set_auth_config
    from app.gateway.auth.jwt import create_access_token
    from app.gateway.auth.models import User
    from app.gateway.auth.repositories.sqlite import SQLiteUserRepository
    from app.gateway.auth_middleware import AuthMiddleware
    from app.gateway.csrf_middleware import CSRFMiddleware
    from app.gateway.routers import console, threads, video_gen
    from app.gateway.video_gen.config import VideoGenConfig
    from app.gateway.video_gen.providers.registry import ProviderRegistry
    from app.gateway.video_gen.service import VideoGenService
    from deerflow.config.database_config import DatabaseConfig
    from deerflow.persistence.base import Base
    from deerflow.persistence.engine import close_engine, get_engine, get_session_factory, init_engine
    from deerflow.persistence.run.model import RunRow
    from deerflow.persistence.thread_meta import make_thread_store
    from deerflow.runtime.checkpointer.async_provider import _async_checkpointer_from_database

    database_url = f"sqlite+aiosqlite:///{database_dir / 'deerflow.db'}"
    await init_engine("sqlite", url=database_url, sqlite_dir=str(database_dir))
    try:
        engine = get_engine()
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        session_factory = get_session_factory()
        assert session_factory is not None

        set_auth_config(AuthConfig(jwt_secret="task8-test-only-jwt-secret-32bytes"))
        users = SQLiteUserRepository(session_factory)
        for user_id, email in ((USER_A, "a@example.com"), (USER_B, "b@example.com")):
            await users.create_user(User(id=user_id, email=email, password_hash="test-only", system_role="user"))
        token_a = create_access_token(USER_A)
        token_b = create_access_token(USER_B)

        config = VideoGenConfig(storage_dir=str(video_storage), qihu_api_key="test-only", script_model="offline")
        service = VideoGenService(
            repo=TaskRepository(session_factory),
            config=config,
            providers=ProviderRegistry(config),
            storage_dir=str(video_storage),
        )
        async with _async_checkpointer_from_database(DatabaseConfig(backend="sqlite", sqlite_dir=str(database_dir))) as checkpointer:
            app = FastAPI()
            app.add_middleware(AuthMiddleware)
            app.add_middleware(CSRFMiddleware)
            app.include_router(threads.router)
            app.include_router(console.router)
            app.include_router(video_gen.router)
            app.state.checkpointer = checkpointer
            app.state.thread_store = make_thread_store(session_factory, None)
            app.state.video_gen_service = service

            def make_client(token: str) -> TestClient:
                client = TestClient(app, base_url="https://isolated.test")
                client.cookies.set("access_token", token)
                client.cookies.set("csrf_token", CSRF)
                return client

            with make_client(token_a) as client_a, make_client(token_b) as client_b:
                unauthenticated = TestClient(app, base_url="https://isolated.test")
                unauthenticated_status = unauthenticated.get("/api/threads/offset-target").status_code
                missing_csrf_status = client_a.post(
                    "/api/threads",
                    json={"thread_id": "csrf-negative", "metadata": {}},
                ).status_code
                assert unauthenticated_status == 401
                assert missing_csrf_status == 403

                _create_thread(client_a, "offset-target", {"agent_name": "course-video"}, "最旧目标")
                for index in range(50):
                    _create_thread(
                        client_a,
                        f"course-{index:02d}",
                        {"agent_name": "course-video"},
                        f"本人课程 {index:02d}",
                    )

                aliases = {
                    "legacy-ppt": {"project_type": "course-ppt"},
                    "creative-card": {"project_type": "card-poster"},
                    "creative-article": {"project_type": "case-article"},
                    "creative-design": {"project_type": "design-specialist"},
                    "creative-knowledge": {"project_type": "knowledge-extraction"},
                    "creative-video": {"project_type": "video-clip"},
                    "podcast-thread": {"agent_name": "podcast-studio"},
                }
                for thread_id, metadata in aliases.items():
                    _create_thread(client_a, thread_id, metadata, thread_id)
                _create_thread(client_b, "other-thread", {"agent_name": "course-video"}, "他人会话")

                first_page = client_a.post(
                    "/api/threads/search",
                    json={"metadata": {"agent_name": "course-video"}, "limit": 50, "offset": 0},
                    headers={"X-CSRF-Token": CSRF},
                )
                second_page = client_a.post(
                    "/api/threads/search",
                    json={"metadata": {"agent_name": "course-video"}, "limit": 50, "offset": 50},
                    headers={"X-CSRF-Token": CSRF},
                )
                assert first_page.status_code == second_page.status_code == 200
                first_rows = first_page.json()
                second_rows = second_page.json()
                assert len(first_rows) == 50
                assert len(second_rows) == 1
                assert "offset-target" not in {row["thread_id"] for row in first_rows}
                assert second_rows[0]["thread_id"] == "offset-target"
                assert second_rows[0]["values"]["title"] == "最旧目标"
                assert all("context" not in row for row in [*first_rows, *second_rows])

                all_threads = client_a.post(
                    "/api/threads/search",
                    json={"limit": 100, "offset": 0},
                    headers={"X-CSRF-Token": CSRF},
                )
                assert all_threads.status_code == 200
                alias_metadata = {row["thread_id"]: row["metadata"] for row in all_threads.json()}
                for thread_id, metadata in aliases.items():
                    assert alias_metadata[thread_id] == metadata
                assert "other-thread" not in alias_metadata

                own_thread_status = client_a.get("/api/threads/offset-target").status_code
                other_owner_status = client_b.get("/api/threads/other-thread").status_code
                other_thread_status = client_a.get("/api/threads/other-thread").status_code
                assert own_thread_status == other_owner_status == 200
                assert other_thread_status == 404

                palette = {
                    "primary": "#112233",
                    "saturation": 1.25,
                    "contrast": 0.85,
                    "brightness": 0.15,
                    "grade": "legacy-grade",
                    "old": "preserve-old-key",
                    "old_extension": 42,
                }
                brand = {
                    "remotion_theme": "warm",
                    "font_family": "Georgia,serif",
                    "logo_path": "brand/existing-logo.png",
                    "intro_outro": {
                        "intro": {"headline": "保留片头", "subtitle": "保留副标题"},
                        "outro": {"headline": "保留片尾", "follow": "保留关注语"},
                    },
                }
                created_project = client_a.post(
                    "/api/video/projects",
                    json={
                        "title": "原始项目",
                        "visual_style_preset": "flat",
                        "color_palette": palette,
                        **brand,
                    },
                    headers={"X-CSRF-Token": CSRF},
                )
                assert created_project.status_code == 200, created_project.text
                project = created_project.json()
                project_id = project["id"]
                project_path = video_storage / "course_projects" / USER_A / f"{project_id}.json"
                assert project_path.is_file()
                assert project_path.resolve().is_relative_to(video_storage.resolve())

                equivalent_title_save = client_a.put(
                    f"/api/video/projects/{project_id}",
                    json={
                        "title": "仅修改标题",
                        "visual_style_preset": "flat",
                        "voice_id": "",
                        "tts_speed": None,
                        "subtitle_style": "default",
                        "color_palette": dict(project["color_palette"]),
                    },
                    headers={"X-CSRF-Token": CSRF},
                )
                assert equivalent_title_save.status_code == 200, equivalent_title_save.text
                saved_project = equivalent_title_save.json()
                assert saved_project["title"] == "仅修改标题"
                assert saved_project["color_palette"] == palette
                for key, value in brand.items():
                    assert saved_project[key] == value
                reread_project = client_a.get(f"/api/video/projects/{project_id}")
                assert reread_project.status_code == 200
                assert reread_project.json()["color_palette"] == palette
                assert client_a.get("/api/video/projects").status_code == 200
                assert client_b.get(f"/api/video/projects/{project_id}").status_code == 404

                task_repo = TaskRepository(session_factory)
                task_fixtures = (
                    ("vg-image", "ai_image", "隔离图片", {}),
                    ("vg-visual", "ai_visual", "隔离视觉", {}),
                    ("vg-video", "ai_video", "隔离视频", {}),
                    ("vg-course", "remotion_course", "隔离课程", {"course_project_id": project_id}),
                )
                for task_id, task_type, title, payload in task_fixtures:
                    await task_repo.create(
                        id=task_id,
                        user_id=USER_A,
                        domain="video_gen",
                        task_type=task_type,
                        payload=json.dumps(payload),
                        title=title,
                    )
                    await task_repo.update(task_id, status="ready", progress=100)
                await task_repo.create(
                    id="vg-other",
                    user_id=USER_B,
                    domain="video_gen",
                    task_type="ai_image",
                    payload="{}",
                    title="他人图片",
                )
                tasks_response = client_a.get("/api/video/tasks")
                assert tasks_response.status_code == 200
                tasks = tasks_response.json()
                task_types = {task["task_type"] for task in tasks}
                assert task_types == {"ai_image", "ai_visual", "ai_video", "remotion_course"}
                assert "vg-other" not in {task["id"] for task in tasks}
                course_response = client_a.get("/api/video/tasks/vg-course")
                assert course_response.status_code == 200
                course_task = course_response.json()
                assert course_task["task_type"] == "remotion_course"
                assert course_task["payload"]["course_project_id"] == project_id
                assert client_b.get("/api/video/tasks/vg-course").status_code == 404

                async with session_factory() as session:
                    session.add_all(
                        [
                            RunRow(
                                run_id="console-a",
                                thread_id="offset-target",
                                user_id=USER_A,
                                status="success",
                                operation_kind="run",
                                total_tokens=1,
                            ),
                            RunRow(
                                run_id="console-b",
                                thread_id="other-thread",
                                user_id=USER_B,
                                status="success",
                                operation_kind="run",
                                total_tokens=1,
                            ),
                        ]
                    )
                    await session.commit()
                console_status = {}
                for path in CONSOLE_PATHS:
                    own = client_a.get(path, params={"user_id": USER_A})
                    forged = client_a.get(path, params={"user_id": USER_B})
                    assert own.status_code == 200, (path, own.text)
                    assert own.json()["scope"]["user_id"] == USER_A
                    assert forged.status_code == 403, (path, forged.text)
                    console_status[path] = {"own": own.status_code, "forged_other": forged.status_code}
                own_runs = client_a.get("/api/console/runs")
                assert own_runs.status_code == 200
                assert {run["run_id"] for run in own_runs.json()["runs"]} == {"console-a"}

                return {
                    "middleware": {
                        "unauthenticated": unauthenticated_status,
                        "missing_csrf": missing_csrf_status,
                        "jwt_and_real_user_context": True,
                    },
                    "threads": {
                        "own_course_threads": 51,
                        "first_page": len(first_rows),
                        "second_page": len(second_rows),
                        "offset_50_target": second_rows[0]["thread_id"],
                        "thread_response_has_context": False,
                        "aliases": alias_metadata,
                        "own": own_thread_status,
                        "other_owner": other_owner_status,
                        "other_thread": other_thread_status,
                    },
                    "console": console_status,
                    "project": {
                        "id": project_id,
                        "palette": palette,
                        "brand": brand,
                        "title": saved_project["title"],
                        "isolated_json": True,
                    },
                    "video": {
                        "task_types": sorted(task_types),
                        "remotion_course_id": course_task["id"],
                        "course_project_id": project_id,
                        "seed": "isolated DB fixtures only; real API reads; no create/paid pipeline",
                    },
                    "isolation": {
                        "process": "spawn",
                        "database": "temporary SQLite + real checkpointer",
                        "deer_flow_home": "temporary",
                        "video_storage": "temporary course_projects JSON",
                    },
                }
    finally:
        await close_engine()


def _worker(root: str, queue) -> None:
    try:
        summary = asyncio.run(_run_isolated(Path(root)))
    except Exception:
        queue.put({"ok": False, "error": traceback.format_exc()})
    else:
        queue.put({"ok": True, "summary": summary})


@pytest.mark.integration
def test_workspace_experience_isolated_real_api(tmp_path):
    context = multiprocessing.get_context("spawn")
    queue = context.Queue()
    process = context.Process(target=_worker, args=(str(tmp_path), queue))
    process.start()
    process.join(180)
    if process.is_alive():
        process.terminate()
        process.join(10)
        pytest.fail("isolated API worker timed out")
    assert not queue.empty(), f"worker returned no evidence (exit={process.exitcode})"
    result = queue.get()
    assert result["ok"], result.get("error")
    assert process.exitcode == 0
    summary = result["summary"]
    assert summary["threads"]["second_page"] == 1
    assert summary["console"]["/api/console/runs"] == {"own": 200, "forged_other": 403}

    evidence_dir = Path(__file__).resolve().parents[2] / "temp" / "workspace-experience-2026-11-12"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    evidence = evidence_dir / "task8-isolated-api-summary.json"
    evidence.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"ISOLATED_API_OK threads=51 offset50={summary['threads']['offset_50_target']} aliases={len(summary['threads']['aliases'])} video_types={summary['video']['task_types']} console=200/403 palette=preserved")
