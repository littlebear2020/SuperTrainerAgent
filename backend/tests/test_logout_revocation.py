"""Logout must revoke the user's JWT by bumping token_version (审计 §1-1 / C1).

Behavior change (2026-10-05): logout is no longer cookie-clearing only — it
bumps the **user-level** ``token_version`` and persists it, so this user's
tokens on ALL devices fail the ``deps.get_current_user_from_request`` version
check with 401.
"""

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.gateway.auth.models import User
from app.gateway.routers.auth import router as auth_router


def _user(token_version: int = 0) -> User:
    return User(email="logout-test@example.com", password_hash="x", system_role="user", id=uuid4(), token_version=token_version)


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(auth_router)
    return app


def test_logout_bumps_token_version_and_persists():
    """Logout bumps token_version (0→1) and calls provider.update_user."""
    app = _app()
    user = _user(token_version=0)
    provider = AsyncMock()
    provider.update_user = AsyncMock()

    with (
        patch("app.gateway.routers.auth.get_optional_user_from_request", AsyncMock(return_value=user)),
        patch("app.gateway.routers.auth.get_local_provider", return_value=provider),
    ):
        with TestClient(app) as client:
            resp = client.post("/api/v1/auth/logout")

    assert resp.status_code == 200
    assert user.token_version == 1, "logout must bump the user-level token_version"
    provider.update_user.assert_awaited_once_with(user)


def test_logout_without_authenticated_user_still_clears_cookie():
    """Anonymous logout (no/invalid cookie) must stay 200 and not touch any user."""
    app = _app()
    provider = AsyncMock()

    with (
        patch("app.gateway.routers.auth.get_optional_user_from_request", AsyncMock(return_value=None)),
        patch("app.gateway.routers.auth.get_local_provider", return_value=provider),
    ):
        with TestClient(app) as client:
            resp = client.post("/api/v1/auth/logout")

    assert resp.status_code == 200
    provider.update_user.assert_not_awaited()
    assert any("access_token" in c and ('""' in c or "max-age=0" in c) for c in resp.headers.get("set-cookie", "").split("&&")) or "access_token" in resp.headers.get("set-cookie", "")


@pytest.mark.asyncio
async def test_old_token_401_after_logout_bump():
    """A token minted before the logout bump fails the version check with 401.

    This pins the revocation semantics end-to-end at the deps layer: the same
    ``get_current_user_from_request`` used by protected endpoints must reject
    a ver=0 token once the user's token_version is 1.
    """
    from fastapi import HTTPException

    from app.gateway.auth import create_access_token
    from app.gateway.deps import get_current_user_from_request

    user = _user(token_version=1)  # post-logout state
    stale_token = create_access_token(str(user.id), token_version=0)

    class _FakeRequest:
        cookies = {"access_token": stale_token}

    provider = AsyncMock()
    provider.get_user = AsyncMock(return_value=user)

    with patch("app.gateway.deps.get_local_provider", return_value=provider):
        try:
            await get_current_user_from_request(_FakeRequest())  # type: ignore[arg-type]
        except HTTPException as exc:
            assert exc.status_code == 401
        else:
            raise AssertionError("stale token must be rejected after logout bump")


def test_auth_token_expiry_days_env_override(monkeypatch):
    """AUTH_TOKEN_EXPIRY_DAYS env overrides the token lifetime (D-6 deployment knob).

    Default stays 7 days (D-6 ruling: don't change the code default); the env
    knob exists so the ops-manual note "生产建议 1–2 天" is actionable.
    """
    from app.gateway.auth import config as auth_config_mod

    monkeypatch.setenv("AUTH_JWT_SECRET", "test-secret-for-expiry-override")
    monkeypatch.setenv("AUTH_TOKEN_EXPIRY_DAYS", "2")
    monkeypatch.setattr(auth_config_mod, "_auth_config", None)

    cfg = auth_config_mod.get_auth_config()
    assert cfg.token_expiry_days == 2

    # restore the singleton for other tests (monkeypatch undoes setattr, but the
    # parsed instance must not leak the override)
    monkeypatch.setattr(auth_config_mod, "_auth_config", None)
    monkeypatch.delenv("AUTH_TOKEN_EXPIRY_DAYS")
    assert auth_config_mod.get_auth_config().token_expiry_days == 7
