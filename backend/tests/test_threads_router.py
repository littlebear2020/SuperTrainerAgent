from unittest.mock import patch

import pytest
from _router_auth_helpers import make_authed_test_app
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.gateway.routers import threads
from deerflow.config.paths import Paths


def test_delete_thread_data_removes_thread_directory(tmp_path):
    paths = Paths(tmp_path)
    thread_dir = paths.thread_dir("thread-cleanup")
    workspace = paths.sandbox_work_dir("thread-cleanup")
    uploads = paths.sandbox_uploads_dir("thread-cleanup")
    outputs = paths.sandbox_outputs_dir("thread-cleanup")

    for directory in [workspace, uploads, outputs]:
        directory.mkdir(parents=True, exist_ok=True)
    (workspace / "notes.txt").write_text("hello", encoding="utf-8")
    (uploads / "report.pdf").write_bytes(b"pdf")
    (outputs / "result.json").write_text("{}", encoding="utf-8")

    assert thread_dir.exists()

    response = threads._delete_thread_data("thread-cleanup", paths=paths)

    assert response.success is True
    assert not thread_dir.exists()


def test_delete_thread_data_is_idempotent_for_missing_directory(tmp_path):
    paths = Paths(tmp_path)

    response = threads._delete_thread_data("missing-thread", paths=paths)

    assert response.success is True
    assert not paths.thread_dir("missing-thread").exists()


def test_delete_thread_data_rejects_invalid_thread_id(tmp_path):
    paths = Paths(tmp_path)

    with pytest.raises(HTTPException) as exc_info:
        threads._delete_thread_data("../escape", paths=paths)

    assert exc_info.value.status_code == 422
    assert "Invalid thread_id" in exc_info.value.detail


def test_delete_thread_route_cleans_thread_directory(tmp_path):
    from deerflow.runtime.user_context import get_effective_user_id

    paths = Paths(tmp_path)
    user_id = get_effective_user_id()
    thread_dir = paths.thread_dir("thread-route", user_id=user_id)
    paths.sandbox_work_dir("thread-route", user_id=user_id).mkdir(parents=True, exist_ok=True)
    (paths.sandbox_work_dir("thread-route", user_id=user_id) / "notes.txt").write_text("hello", encoding="utf-8")

    app = make_authed_test_app()
    app.include_router(threads.router)

    with patch("app.gateway.routers.threads.get_paths", return_value=paths):
        with TestClient(app) as client:
            response = client.delete("/api/threads/thread-route")

    assert response.status_code == 200
    assert response.json() == {"success": True, "message": "Deleted local thread data for thread-route"}
    assert not thread_dir.exists()


def test_delete_thread_route_rejects_invalid_thread_id(tmp_path):
    paths = Paths(tmp_path)

    app = make_authed_test_app()
    app.include_router(threads.router)

    with patch("app.gateway.routers.threads.get_paths", return_value=paths):
        with TestClient(app) as client:
            response = client.delete("/api/threads/../escape")

    assert response.status_code == 404


def test_delete_thread_route_returns_422_for_route_safe_invalid_id(tmp_path):
    paths = Paths(tmp_path)

    app = make_authed_test_app()
    app.include_router(threads.router)

    with patch("app.gateway.routers.threads.get_paths", return_value=paths):
        with TestClient(app) as client:
            response = client.delete("/api/threads/thread.with.dot")

    assert response.status_code == 422
    assert "Invalid thread_id" in response.json()["detail"]


def test_delete_thread_data_returns_generic_500_error(tmp_path):
    paths = Paths(tmp_path)

    with (
        patch.object(paths, "delete_thread_dir", side_effect=OSError("/secret/path")),
        patch.object(threads.logger, "exception") as log_exception,
    ):
        with pytest.raises(HTTPException) as exc_info:
            threads._delete_thread_data("thread-cleanup", paths=paths)

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "Failed to delete local thread data."
    assert "/secret/path" not in exc_info.value.detail
    log_exception.assert_called_once_with("Failed to delete thread data for %s", "thread-cleanup")


def test_delete_thread_route_clears_feedback_rows(tmp_path):
    """删线程时级联清理 feedback 行（上游 3776f6f5 思路适配）。

    预置本线程两条 + 其它线程一条 feedback；删除本线程后本线程行须清空、
    其它线程行不受影响。user 作用域与 delete 端点一致（get_effective_user_id）。
    """
    import anyio

    from deerflow.persistence.engine import close_engine, get_session_factory, init_engine
    from deerflow.persistence.feedback import FeedbackRepository
    from deerflow.runtime.user_context import get_effective_user_id

    paths = Paths(tmp_path)
    user_id = get_effective_user_id()
    paths.sandbox_work_dir("thread-fb", user_id=user_id).mkdir(parents=True, exist_ok=True)

    async def _seed() -> FeedbackRepository:
        await init_engine("sqlite", url=f"sqlite+aiosqlite:///{tmp_path / 'feedback.db'}", sqlite_dir=str(tmp_path))
        repo = FeedbackRepository(get_session_factory())
        await repo.create(run_id="r1", thread_id="thread-fb", rating=1, user_id=user_id)
        await repo.create(run_id="r2", thread_id="thread-fb", rating=-1, user_id=user_id)
        await repo.create(run_id="r3", thread_id="thread-fb-other", rating=1, user_id=user_id)
        return repo

    async def _list_all(repo: FeedbackRepository, thread_id: str) -> list[dict]:
        # user_id=None = 不做用户过滤，看物理全量
        return await repo.list_by_thread(thread_id, user_id=None)

    repo = anyio.run(_seed)
    try:
        app = make_authed_test_app()
        app.include_router(threads.router)
        app.state.feedback_repo = repo

        with patch("app.gateway.routers.threads.get_paths", return_value=paths):
            with TestClient(app) as client:
                response = client.delete("/api/threads/thread-fb")

        assert response.status_code == 200
        assert anyio.run(_list_all, repo, "thread-fb") == []
        assert len(anyio.run(_list_all, repo, "thread-fb-other")) == 1
    finally:
        anyio.run(close_engine)


# ── Server-reserved metadata key stripping ──────────────────────────────────


def test_strip_reserved_metadata_removes_user_id():
    """Client-supplied user_id is dropped to prevent reflection attacks."""
    out = threads._strip_reserved_metadata({"user_id": "victim-id", "title": "ok"})
    assert out == {"title": "ok"}


def test_strip_reserved_metadata_passes_through_safe_keys():
    """Non-reserved keys are preserved verbatim."""
    md = {"title": "ok", "tags": ["a", "b"], "custom": {"x": 1}}
    assert threads._strip_reserved_metadata(md) == md


def test_strip_reserved_metadata_empty_input():
    """Empty / None metadata returns same object — no crash."""
    assert threads._strip_reserved_metadata({}) == {}


def test_strip_reserved_metadata_strips_all_reserved_keys():
    out = threads._strip_reserved_metadata({"user_id": "x", "keep": "me"})
    assert out == {"keep": "me"}


def test_strip_reserved_metadata_keeps_pinned_key():
    """deerflow_pinned is a user-controlled key and must survive the strip."""
    out = threads._strip_reserved_metadata({"deerflow_pinned": True, "user_id": "victim"})
    assert out == {"deerflow_pinned": True}


def test_is_pin_metadata_patch_shapes():
    assert threads._is_pin_metadata_patch({"deerflow_pinned": True}) is True
    assert threads._is_pin_metadata_patch({"deerflow_pinned": False}) is True
    # Non-bool values, extra keys, and other keys are not the pin shape.
    assert threads._is_pin_metadata_patch({"deerflow_pinned": "true"}) is False
    assert threads._is_pin_metadata_patch({"deerflow_pinned": True, "other": 1}) is False
    assert threads._is_pin_metadata_patch({"other": 1}) is False
    assert threads._is_pin_metadata_patch({}) is False


def _make_patch_app(record_metadata):
    """Authed test app whose thread_store returns a fixed record."""
    from unittest.mock import AsyncMock

    app = make_authed_test_app()
    app.include_router(threads.router)
    app.state.thread_store.get = AsyncMock(
        return_value={
            "thread_id": "t1",
            "status": "idle",
            "metadata": record_metadata,
            "created_at": "2026-08-04T10:00:00+00:00",
            "updated_at": "2026-08-04T10:00:00+00:00",
        }
    )
    app.state.thread_store.update_metadata = AsyncMock()
    return app


def test_patch_thread_pin_only_metadata_does_not_touch_updated_at():
    """Pin/unpin is not conversation activity → update_metadata(touch=False)."""
    app = _make_patch_app({})

    with TestClient(app) as client:
        response = client.patch("/api/threads/t1", json={"metadata": {"deerflow_pinned": True}})

    assert response.status_code == 200
    app.state.thread_store.update_metadata.assert_awaited_once()
    args, kwargs = app.state.thread_store.update_metadata.call_args
    assert args[0] == "t1"
    assert args[1] == {"deerflow_pinned": True}
    assert kwargs.get("touch") is False


def test_patch_thread_other_metadata_keeps_touch_true():
    app = _make_patch_app({})

    with TestClient(app) as client:
        response = client.patch("/api/threads/t1", json={"metadata": {"custom": 1}})

    assert response.status_code == 200
    args, kwargs = app.state.thread_store.update_metadata.call_args
    assert args[1] == {"custom": 1}
    assert kwargs.get("touch") is True


def test_patch_thread_pin_with_reserved_key_strips_and_stays_pin_only():
    """Reserved-key injection is stripped; the remaining pin shape is still no-touch."""
    app = _make_patch_app({})

    with TestClient(app) as client:
        response = client.patch(
            "/api/threads/t1",
            json={"metadata": {"deerflow_pinned": True, "user_id": "victim"}},
        )

    assert response.status_code == 200
    args, kwargs = app.state.thread_store.update_metadata.call_args
    assert args[1] == {"deerflow_pinned": True}
    assert kwargs.get("touch") is False
