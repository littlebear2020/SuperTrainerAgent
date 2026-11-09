"""Tests for the shared admin-only route dependency (C1/B4).

Why a *new* concept rather than a wrapper over ``@require_permission``:
``authz._authenticate`` hands **every** logged-in user the full
``_ALL_PERMISSIONS`` list, and that list contains no admin entry at all.
So the permission decorator structurally cannot express "admin only" —
which is exactly why ``console.console_users`` has to stack a hand-written
``_is_admin`` check *on top of* its decorator. This module pins the
replacement for those four hand-rolled idioms.

These tests drive the dependency through a real FastAPI app + TestClient
rather than calling it with a fabricated ``Request``: the thing under test
is a routing-layer guard, and a direct call would not prove that FastAPI
actually runs it before the handler body.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient
from starlette.middleware.base import BaseHTTPMiddleware

from app.gateway.admin_auth import require_admin
from app.gateway.authz import _ALL_PERMISSIONS, AuthContext


def _app_with_role(system_role: str | None, *, authenticated: bool = True) -> FastAPI:
    """A one-route app whose caller carries *system_role*.

    Mirrors what production ``AuthMiddleware`` stamps: ``request.state.auth``
    holding an ``AuthContext`` with the full permission set. Granting
    ``_ALL_PERMISSIONS`` here is deliberate — it reproduces the real
    authorization state in which a non-admin still must be refused.
    """
    app = FastAPI()

    class _Stamp(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            if authenticated:
                user = SimpleNamespace(id="u-1", system_role=system_role)
                request.state.auth = AuthContext(user=user, permissions=list(_ALL_PERMISSIONS))
                request.state.user = user
            return await call_next(request)

    app.add_middleware(_Stamp)

    @app.put("/guarded", dependencies=[Depends(require_admin)])
    async def guarded() -> dict:
        return {"ok": True}

    return app


class TestRequireAdmin:
    def test_admin_is_allowed_through(self) -> None:
        with TestClient(_app_with_role("admin")) as client:
            assert client.put("/guarded").status_code == 200

    def test_regular_user_is_refused_with_403(self) -> None:
        """A plain user holds every *permission* yet is still not an admin."""
        with TestClient(_app_with_role("user")) as client:
            response = client.put("/guarded")

        assert response.status_code == 403
        assert "admin" in response.json()["detail"].lower()

    def test_anonymous_caller_is_refused(self) -> None:
        """No auth context at all must fail closed, not crash with 500."""
        with TestClient(_app_with_role(None, authenticated=False)) as client:
            assert client.put("/guarded").status_code == 403

    def test_missing_system_role_is_refused(self) -> None:
        """A user object without the attribute must not be treated as admin."""
        with TestClient(_app_with_role(None)) as client:
            assert client.put("/guarded").status_code == 403

    def test_internal_role_is_not_admin(self) -> None:
        """``internal`` is the trusted-process identity, not a platform admin.

        ``internal_auth.get_internal_user`` stamps ``system_role="internal"``
        for same-process channel calls. That role must not silently satisfy an
        admin gate, or any IM channel integration would inherit admin rights.
        """
        with TestClient(_app_with_role("internal")) as client:
            assert client.put("/guarded").status_code == 403

    @pytest.mark.parametrize("impostor", ["Admin", "ADMIN", "admin ", "superadmin"])
    def test_role_match_is_exact(self, impostor: str) -> None:
        """Only the exact ``admin`` string grants access."""
        with TestClient(_app_with_role(impostor)) as client:
            assert client.put("/guarded").status_code == 403
