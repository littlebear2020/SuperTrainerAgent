"""Router-level tests for ``app.gateway.routers.feedback``.

``tests/test_feedback.py`` covers ``FeedbackRepository`` in isolation; this
file pins the HTTP contract of the feedback router itself: rating validation
(400), run/thread scoping (404 when the run is missing or belongs to another
thread), idempotent upsert, both delete surfaces, the stats endpoint, and the
authz boundary (401 unauthenticated / 403 missing permission / 404 when the
thread owner check fails).

Setup mirrors the established router-test idiom: ``make_authed_test_app``
stamps a stub auth context and a permissive thread_store, persistence is a
real SQLite database via ``init_engine``/``Base.metadata.create_all``, and two
shims replay what production ``AuthMiddleware`` provides but the bare test app
lacks:

- ``_UserContextMiddleware`` sets the ``user_context`` contextvar around each
  request — repository methods resolve ``user_id=AUTO`` from it, exactly as in
  production;
- ``feedback.get_current_user`` is monkeypatched to identify the *same* stub
  user (in production the cookie-derived user and the contextvar user are the
  same identity, so keeping them equal preserves real behaviour).
"""

from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace
from uuid import UUID

import pytest
from _router_auth_helpers import make_authed_test_app
from fastapi import FastAPI, Request, Response
from fastapi.testclient import TestClient
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from app.gateway.auth.models import User
from app.gateway.authz import AuthContext
from app.gateway.routers import feedback as feedback_router
from deerflow.runtime.user_context import reset_current_user, set_current_user

USER_ID = "11111111-1111-1111-1111-111111111111"
THREAD_ID = "thread-fb-1"
RUN_ID = "run-fb-1"


def _make_user() -> User:
    """Deterministic stub user — fixed id so contextvar and cookie paths agree."""
    return User(email="fb-router@example.com", password_hash="x", system_role="user", id=UUID(USER_ID))


class _UserContextMiddleware(BaseHTTPMiddleware):
    """Replay production AuthMiddleware's contextvar write in the bare test app."""

    def __init__(self, app: ASGIApp, user_factory: Callable[[], User]) -> None:
        super().__init__(app)
        self._user_factory = user_factory

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        token = set_current_user(self._user_factory())
        try:
            return await call_next(request)
        finally:
            reset_current_user(token)


class _NoPermissionsAuthMiddleware(BaseHTTPMiddleware):
    """Stamp an authenticated context with an empty permission set (→ 403)."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        user = _make_user()
        request.state.user = user
        request.state.auth = AuthContext(user=user, permissions=[])
        return await call_next(request)


@pytest.fixture()
async def router_env(tmp_path, monkeypatch):
    """Feedback router wired to a real SQLite DB, one seeded run, stub auth."""
    from deerflow.persistence.base import Base
    from deerflow.persistence.engine import close_engine, get_engine, get_session_factory, init_engine
    from deerflow.persistence.feedback import FeedbackRepository
    from deerflow.persistence.run import RunRepository

    await init_engine("sqlite", url=f"sqlite+aiosqlite:///{tmp_path / 'feedback_router.db'}", sqlite_dir=str(tmp_path))
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = get_session_factory()
    run_store = RunRepository(session_factory)
    await run_store.put(RUN_ID, thread_id=THREAD_ID, user_id=USER_ID)

    async def _stub_get_current_user(request: Request) -> str | None:
        user = getattr(request.state, "user", None)
        return str(user.id) if user is not None else None

    monkeypatch.setattr(feedback_router, "get_current_user", _stub_get_current_user)

    app = make_authed_test_app(user_factory=_make_user)
    app.add_middleware(_UserContextMiddleware, user_factory=_make_user)
    app.include_router(feedback_router.router)
    app.state.run_store = run_store
    feedback_repo = FeedbackRepository(session_factory)
    app.state.feedback_repo = feedback_repo

    with TestClient(app) as client:
        yield SimpleNamespace(client=client, feedback_repo=feedback_repo)

    await close_engine()


def _url(thread_id: str = THREAD_ID, run_id: str = RUN_ID) -> str:
    return f"/api/threads/{thread_id}/runs/{run_id}/feedback"


class TestCreateFeedback:
    @pytest.mark.anyio
    async def test_create_happy_path(self, router_env) -> None:
        resp = router_env.client.post(_url(), json={"rating": 1, "comment": "great answer", "message_id": "msg-1"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["feedback_id"]
        assert body["run_id"] == RUN_ID
        assert body["thread_id"] == THREAD_ID
        assert body["user_id"] == USER_ID
        assert body["message_id"] == "msg-1"
        assert body["rating"] == 1
        assert body["comment"] == "great answer"
        assert body["created_at"]

    @pytest.mark.anyio
    async def test_create_negative_rating(self, router_env) -> None:
        resp = router_env.client.post(_url(), json={"rating": -1})
        assert resp.status_code == 200
        assert resp.json()["rating"] == -1

    @pytest.mark.anyio
    @pytest.mark.parametrize("rating", [0, 2, -2, 5])
    async def test_create_rejects_invalid_rating(self, router_env, rating: int) -> None:
        resp = router_env.client.post(_url(), json={"rating": rating})
        assert resp.status_code == 400
        assert resp.json()["detail"] == "rating must be +1 or -1"

    @pytest.mark.anyio
    async def test_create_requires_rating_field(self, router_env) -> None:
        resp = router_env.client.post(_url(), json={"comment": "no rating"})
        assert resp.status_code == 422

    @pytest.mark.anyio
    async def test_create_unknown_run_404(self, router_env) -> None:
        resp = router_env.client.post(_url(run_id="run-missing"), json={"rating": 1})
        assert resp.status_code == 404
        assert "run-missing" in resp.json()["detail"]

    @pytest.mark.anyio
    async def test_create_run_in_other_thread_404(self, router_env) -> None:
        """A run that exists but belongs to another thread is a 404, not a cross-thread write."""
        resp = router_env.client.post(_url(thread_id="thread-other"), json={"rating": 1})
        assert resp.status_code == 404


class TestUpsertFeedback:
    @pytest.mark.anyio
    async def test_upsert_creates_then_updates_idempotent(self, router_env) -> None:
        first = router_env.client.put(_url(), json={"rating": 1})
        assert first.status_code == 200
        second = router_env.client.put(_url(), json={"rating": -1, "comment": "changed my mind"})
        assert second.status_code == 200
        assert second.json()["feedback_id"] == first.json()["feedback_id"]
        assert second.json()["rating"] == -1
        assert second.json()["comment"] == "changed my mind"

        listing = router_env.client.get(_url())
        assert listing.status_code == 200
        assert len(listing.json()) == 1

    @pytest.mark.anyio
    async def test_upsert_rejects_invalid_rating(self, router_env) -> None:
        resp = router_env.client.put(_url(), json={"rating": 0})
        assert resp.status_code == 400
        assert resp.json()["detail"] == "rating must be +1 or -1"

    @pytest.mark.anyio
    async def test_upsert_unknown_run_404(self, router_env) -> None:
        resp = router_env.client.put(_url(run_id="run-missing"), json={"rating": 1})
        assert resp.status_code == 404


class TestListAndStats:
    @pytest.mark.anyio
    async def test_list_returns_only_caller_own_feedback(self, router_env) -> None:
        """GET list filters by the request user: another user's row on the same run is hidden.

        The ``feedback`` table enforces one row per (thread, run, user), so a
        caller can only ever see their own single row here.
        """
        router_env.client.post(_url(), json={"rating": 1, "message_id": "msg-9"})
        await router_env.feedback_repo.create(run_id=RUN_ID, thread_id=THREAD_ID, rating=-1, user_id="other-user")

        resp = router_env.client.get(_url())
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 1
        assert body[0]["rating"] == 1
        assert body[0]["user_id"] == USER_ID
        assert body[0]["run_id"] == RUN_ID

    @pytest.mark.anyio
    async def test_list_empty_for_untouched_run(self, router_env) -> None:
        resp = router_env.client.get(_url())
        assert resp.status_code == 200
        assert resp.json() == []

    @pytest.mark.anyio
    async def test_stats_aggregates_across_users(self, router_env) -> None:
        """Stats are run-scoped with no user filter: all users' feedback counts."""
        await router_env.feedback_repo.create(run_id=RUN_ID, thread_id=THREAD_ID, rating=1, user_id="u1")
        await router_env.feedback_repo.create(run_id=RUN_ID, thread_id=THREAD_ID, rating=1, user_id="u2")
        await router_env.feedback_repo.create(run_id=RUN_ID, thread_id=THREAD_ID, rating=-1, user_id="u3")
        resp = router_env.client.get(f"{_url()}/stats")
        assert resp.status_code == 200
        assert resp.json() == {"run_id": RUN_ID, "total": 3, "positive": 2, "negative": 1}

    @pytest.mark.anyio
    async def test_stats_empty_run_is_zeros(self, router_env) -> None:
        resp = router_env.client.get(f"{_url()}/stats")
        assert resp.status_code == 200
        assert resp.json() == {"run_id": RUN_ID, "total": 0, "positive": 0, "negative": 0}


class TestDeleteFeedback:
    @pytest.mark.anyio
    async def test_delete_by_run_then_second_delete_404(self, router_env) -> None:
        router_env.client.put(_url(), json={"rating": 1})
        resp = router_env.client.delete(_url())
        assert resp.status_code == 200
        assert resp.json() == {"success": True}

        assert router_env.client.get(_url()).json() == []

        second = router_env.client.delete(_url())
        assert second.status_code == 404
        assert second.json()["detail"] == "No feedback found for this run"

    @pytest.mark.anyio
    async def test_delete_by_id_happy_path_then_404(self, router_env) -> None:
        created = router_env.client.post(_url(), json={"rating": 1}).json()
        resp = router_env.client.delete(f"{_url()}/{created['feedback_id']}")
        assert resp.status_code == 200
        assert resp.json() == {"success": True}

        assert router_env.client.get(_url()).json() == []

        again = router_env.client.delete(f"{_url()}/{created['feedback_id']}")
        assert again.status_code == 404

    @pytest.mark.anyio
    async def test_delete_by_id_wrong_scope_404(self, router_env) -> None:
        """A feedback id addressed under a foreign thread/run path must not be deleted."""
        created = router_env.client.post(_url(), json={"rating": 1}).json()
        resp = router_env.client.delete(_url(thread_id="thread-other", run_id="run-other") + f"/{created['feedback_id']}")
        assert resp.status_code == 404

        still_there = router_env.client.get(_url()).json()
        assert [row["feedback_id"] for row in still_there] == [created["feedback_id"]]

    @pytest.mark.anyio
    async def test_delete_by_unknown_id_404(self, router_env) -> None:
        resp = router_env.client.delete(f"{_url()}/feedback-missing")
        assert resp.status_code == 404


class TestAuthBoundary:
    """The authz decorators run inside the bare router app — no DB needed."""

    def test_unauthenticated_request_401(self) -> None:
        """No auth middleware at all → require_permission cannot authenticate → 401."""
        app = FastAPI()
        app.include_router(feedback_router.router)
        with TestClient(app) as client:
            resp = client.post(_url(), json={"rating": 1})
        assert resp.status_code == 401

    def test_authenticated_without_permission_403(self) -> None:
        """Authenticated user lacking ``threads:write`` → 403 before any owner check."""
        app = FastAPI()
        app.add_middleware(_NoPermissionsAuthMiddleware)
        app.include_router(feedback_router.router)
        with TestClient(app) as client:
            resp = client.post(_url(), json={"rating": 1})
        assert resp.status_code == 403
        assert "threads:write" in resp.json()["detail"]

    def test_failed_owner_check_404(self) -> None:
        """``thread_store.check_access`` denial surfaces as 404 (thread hidden), not 403."""
        app = make_authed_test_app(user_factory=_make_user, owner_check_passes=False)
        app.include_router(feedback_router.router)
        with TestClient(app) as client:
            resp = client.post(_url(), json={"rating": 1})
        assert resp.status_code == 404
        assert THREAD_ID in resp.json()["detail"]
