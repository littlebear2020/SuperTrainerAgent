"""Tests for GET /api/v1/auth/csrf-token — bootstraps the csrf cookie in no-auth mode.

The gateway only sets ``csrf_token`` on auth-endpoint POST responses
(``csrf_middleware.py``), so browsers that never log in (no-auth / dev-bypass
mode) have no cookie and every state-changing Gateway POST returns 403.
This endpoint lets the frontend obtain/refresh the cookie on mount.
"""

from fastapi import FastAPI
from starlette.testclient import TestClient

from app.gateway.csrf_middleware import CSRFMiddleware
from app.gateway.routers.auth import router as auth_router


def _make_app() -> FastAPI:
    """Minimal app: real auth router (for /csrf-token) + CSRF middleware + a
    dummy state-changing route to prove the bootstrapped cookie unblocks POSTs.
    """
    app = FastAPI()
    app.add_middleware(CSRFMiddleware)
    app.include_router(auth_router)

    @app.post("/api/echo")
    async def echo() -> dict[str, bool]:
        return {"ok": True}

    return app


def test_csrf_token_endpoint_sets_readable_cookie() -> None:
    """GET /api/v1/auth/csrf-token returns 200 and sets a csrf_token cookie
    whose value matches the JSON body (Double Submit Cookie)."""
    client = TestClient(_make_app())
    response = client.get("/api/v1/auth/csrf-token")

    assert response.status_code == 200
    body = response.json()
    assert isinstance(body.get("token"), str)
    assert body["token"]

    cookie = response.cookies.get("csrf_token")
    assert cookie is not None
    assert cookie == body["token"]


def test_csrf_token_endpoint_repeat_call_returns_token() -> None:
    """Repeated calls always yield a usable token (idempotent bootstrap/refresh)."""
    client = TestClient(_make_app())
    r1 = client.get("/api/v1/auth/csrf-token")
    r2 = client.get("/api/v1/auth/csrf-token")

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["token"]
    assert r2.cookies.get("csrf_token")


def test_bootstrapped_cookie_unblocks_state_changing_post() -> None:
    """The cookie obtained from /csrf-token must unblock a CSRF-protected POST
    (no more 403) — this is the actual fix for the silent create-doc failure."""
    client = TestClient(_make_app())

    bootstrap = client.get("/api/v1/auth/csrf-token")
    assert bootstrap.status_code == 200
    token = bootstrap.json()["token"]

    # Without the header → 403 (proves the route is actually protected)
    no_header = client.post("/api/echo")
    assert no_header.status_code == 403

    # With cookie (auto-sent by TestClient) + matching header → 200
    with_header = client.post("/api/echo", headers={"X-CSRF-Token": token})
    assert with_header.status_code == 200
    assert with_header.json() == {"ok": True}
