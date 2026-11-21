from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
import pytest_asyncio
from _router_auth_helpers import call_unwrapped, make_authed_test_app
from fastapi import HTTPException, Request
from fastapi.testclient import TestClient

from app.gateway._shared.personal_usage.types import make_usage_window
from app.gateway._shared.task_framework.models import TaskRow
from app.gateway.auth.models import User
from app.gateway.routers import personal_usage
from deerflow.persistence.engine import close_engine, get_session_factory, init_engine
from deerflow.persistence.run.model import RunRow
from deerflow.persistence.thread_meta.model import ThreadMetaRow

USER_A = "11111111-1111-1111-1111-111111111111"
USER_B = "22222222-2222-2222-2222-222222222222"
ADMIN = "33333333-3333-3333-3333-333333333333"
NOW = datetime(2026, 11, 21, 16, 30, tzinfo=UTC)
CREATED = datetime(2026, 11, 21, 12, tzinfo=UTC)
UPDATED = datetime(2026, 11, 21, 13, tzinfo=UTC)


@pytest_asyncio.fixture()
async def db(tmp_path, monkeypatch):
    monkeypatch.setattr("app.gateway.routers.console.get_app_config", lambda: SimpleNamespace(models=[]))
    monkeypatch.setattr(
        "app.gateway._shared.personal_usage.service.make_usage_window",
        lambda days, tz_offset_minutes: make_usage_window(days, tz_offset_minutes, now=NOW),
    )
    await init_engine("sqlite", url=f"sqlite+aiosqlite:///{tmp_path / 'personal_usage_router.db'}", sqlite_dir=str(tmp_path))
    session_factory = get_session_factory()
    async with session_factory() as session:
        session.add_all(
            [
                ThreadMetaRow(
                    thread_id="thread-a",
                    user_id=USER_A,
                    assistant_id="course-video",
                    display_name="真实费用任务",
                    status="idle",
                    metadata_json={},
                    created_at=CREATED,
                    updated_at=CREATED,
                ),
                ThreadMetaRow(thread_id="creative-a", user_id=USER_A, assistant_id="creative-studio", status="idle", created_at=CREATED, updated_at=CREATED),
                ThreadMetaRow(thread_id="ppt-a", user_id=USER_A, assistant_id="course-ppt", status="idle", created_at=CREATED, updated_at=CREATED),
                ThreadMetaRow(thread_id="podcast-a", user_id=USER_A, assistant_id="podcast-studio", status="idle", created_at=CREATED, updated_at=CREATED),
                ThreadMetaRow(thread_id="thread-b", user_id=USER_B, assistant_id="course-video", status="idle", created_at=CREATED, updated_at=CREATED),
                ThreadMetaRow(thread_id="thread-admin", user_id=ADMIN, assistant_id="course-video", status="idle", created_at=CREATED, updated_at=CREATED),
                RunRow(
                    run_id="run-a",
                    thread_id="thread-a",
                    user_id=USER_A,
                    status="success",
                    operation_kind="run",
                    assistant_id="course-video",
                    model_name="real-model",
                    total_input_tokens=60,
                    total_output_tokens=40,
                    total_tokens=999,
                    token_usage_by_model={"real-model": {"input_tokens": 60, "output_tokens": 40, "total_tokens": 998, "amount": 1.5}},
                    created_at=CREATED,
                    updated_at=UPDATED,
                ),
                RunRow(
                    run_id="run-b",
                    thread_id="thread-b",
                    user_id=USER_B,
                    status="success",
                    operation_kind="run",
                    assistant_id="course-video",
                    model_name="real-model",
                    total_input_tokens=999,
                    total_output_tokens=999,
                    total_tokens=1998,
                    token_usage_by_model={},
                    created_at=CREATED,
                    updated_at=UPDATED,
                ),
                TaskRow(
                    id="image-a",
                    user_id=USER_A,
                    domain="video_gen",
                    task_type="ai_image",
                    status="ready",
                    title="图片任务",
                    created_at=CREATED,
                    updated_at=CREATED,
                ),
                TaskRow(
                    id="video-a",
                    user_id=USER_A,
                    domain="video_gen",
                    task_type="ai_video",
                    status="running",
                    title="视频任务",
                    created_at=CREATED,
                    updated_at=CREATED,
                ),
            ]
        )
        await session.commit()
    yield session_factory
    await close_engine()


def client_for(user_id: str = USER_A, role: str = "user") -> TestClient:
    def factory() -> User:
        return User(id=user_id, email=f"{user_id}@example.com", password_hash="x", system_role=role)

    app = make_authed_test_app(user_factory=factory)
    app.include_router(personal_usage.router)
    return TestClient(app)


def test_usage_response_contract_defaults_and_real_amount(db):
    response = client_for().get("/api/usage")
    assert response.status_code == 200
    payload = response.json()
    assert payload["window"] == {"days": 7, "start_date": "2026-11-15", "end_date": "2026-11-21"}
    assert payload["total_tasks"] == 6
    assert payload["has_more"] is False
    assert payload["summary"] == {"cost": 1.5, "currency": "CNY", "cost_source": "real", "total_tokens": 100}
    task = next(item for item in payload["tasks"] if item["source_id"] == "thread-a")
    assert task["task_key"] == "agent:thread-a"
    assert task["title"] == "真实费用任务"
    assert task["status"] == "completed"
    assert task["cost"] == pytest.approx(1.5, abs=1e-9)
    assert task["llm_cost"] == pytest.approx(1.5, abs=1e-9)
    assert task["media_cost"] is None
    assert task["cost_source"] == "real"
    assert task["currency"] == "CNY"
    assert task["total_tokens"] == 100
    assert task["models"][0]["cache_read_tokens"] is None
    assert task["models"][0]["total_tokens"] == 100
    assert task["models"][0]["cost_source"] == "real"
    assert datetime.fromisoformat(task["updated_at"].replace("Z", "+00:00")) == UPDATED
    assert "activity_at" not in task


@pytest.mark.parametrize(
    ("days", "start_date"),
    [(7, "2026-11-15"), (14, "2026-11-08"), (30, "2026-10-23")],
)
def test_usage_days_and_local_timezone(db, days, start_date):
    response = client_for().get("/api/usage", params={"days": days, "tz_offset_minutes": 0})
    assert response.status_code == 200, response.text
    assert response.json()["window"] == {"days": days, "start_date": start_date, "end_date": "2026-11-21"}

    local = client_for().get("/api/usage", params={"days": days, "tz_offset_minutes": 480})
    assert local.status_code == 200
    assert local.json()["window"]["end_date"] == "2026-11-22"


@pytest.mark.parametrize(
    ("capability", "source_id"),
    [
        ("course-video", "thread-a"),
        ("creative-studio", "creative-a"),
        ("ppt-master", "ppt-a"),
        ("podcast-studio", "podcast-a"),
        ("ai_image", "image-a"),
        ("ai_video", "video-a"),
    ],
)
def test_usage_capability_filters(db, capability, source_id):
    response = client_for().get("/api/usage", params={"capability": capability})
    assert response.status_code == 200
    payload = response.json()
    assert payload["total_tasks"] == 1
    assert [item["source_id"] for item in payload["tasks"]] == [source_id]


def test_usage_offset_limit_and_user_id_query_cannot_change_scope(db):
    page = client_for().get("/api/usage", params={"offset": 1, "limit": 2, "user_id": USER_B})
    assert page.status_code == 200
    payload = page.json()
    assert len(payload["tasks"]) == 2
    assert payload["total_tasks"] == 6
    assert payload["has_more"] is True
    assert all(item["source_id"] != "thread-b" for item in payload["tasks"])

    last = client_for().get("/api/usage", params={"offset": 4, "limit": 2})
    assert last.status_code == 200
    assert len(last.json()["tasks"]) == 2
    assert last.json()["has_more"] is False


def test_usage_admin_only_sees_own_tasks(db):
    response = client_for(ADMIN, "admin").get("/api/usage", params={"user_id": USER_A})
    assert response.status_code == 200
    payload = response.json()
    assert payload["total_tasks"] == 1
    assert payload["tasks"][0]["source_id"] == "thread-admin"
    assert payload["summary"]["total_tokens"] is None


@pytest.mark.parametrize(
    "params",
    [
        {"days": 1},
        {"days": 8},
        {"days": 31},
        {"capability": "lead_agent"},
        {"limit": 0},
        {"limit": 101},
        {"offset": -1},
        {"tz_offset_minutes": -841},
        {"tz_offset_minutes": 841},
    ],
)
def test_usage_rejects_invalid_parameters(db, params):
    response = client_for().get("/api/usage", params=params)
    assert response.status_code == 422


def test_usage_returns_503_without_sql(monkeypatch):
    monkeypatch.setattr(personal_usage, "get_session_factory", lambda: None)
    response = client_for().get("/api/usage")
    assert response.status_code == 503


def test_main_app_registers_usage_route():
    from app.gateway.app import app

    assert any(getattr(route, "path", None) == "/api/usage" and "GET" in getattr(route, "methods", set()) for route in app.routes)


@pytest.mark.asyncio
async def test_usage_rejects_missing_resolved_user_context(monkeypatch):
    monkeypatch.setattr(personal_usage, "get_session_factory", lambda: object())

    async def unexpected_call(*args, **kwargs):
        raise AssertionError("usage aggregation must not run without a resolved user")

    monkeypatch.setattr(personal_usage, "get_personal_usage", unexpected_call)
    request = Request({"type": "http", "method": "GET", "path": "/api/usage", "headers": [], "query_string": b""})
    request.state.auth = SimpleNamespace(user=None)

    with pytest.raises(HTTPException) as exc:
        await call_unwrapped(
            personal_usage.personal_usage_endpoint,
            request,
            days=7,
            capability="all",
            tz_offset_minutes=0,
            offset=0,
            limit=20,
        )
    assert exc.value.status_code == 401
