"""Process-local authentication for Gateway internal callers."""

from __future__ import annotations

import secrets
from types import SimpleNamespace

from deerflow.runtime.user_context import DEFAULT_USER_ID

INTERNAL_AUTH_HEADER_NAME = "X-DeerFlow-Internal-Token"
INTERNAL_USER_ID_HEADER_NAME = "X-DeerFlow-Internal-User-Id"
_INTERNAL_AUTH_TOKEN = secrets.token_urlsafe(32)


def create_internal_auth_headers(user_id: str | None = None) -> dict[str, str]:
    """Return headers that authenticate same-process Gateway internal calls.

    Args:
        user_id: When provided, the auth middleware will set this as the
            effective user identity instead of DEFAULT_USER_ID. Used by
            user-level IM channels to ensure per-user isolation.
    """
    headers = {INTERNAL_AUTH_HEADER_NAME: _INTERNAL_AUTH_TOKEN}
    if user_id is not None:
        headers[INTERNAL_USER_ID_HEADER_NAME] = user_id
    return headers


def is_valid_internal_auth_token(token: str | None) -> bool:
    """Return True when *token* matches the process-local internal token."""
    return bool(token) and secrets.compare_digest(token, _INTERNAL_AUTH_TOKEN)


def get_internal_user():
    """Return the synthetic user used for trusted internal channel calls."""
    return SimpleNamespace(id=DEFAULT_USER_ID, system_role="internal")
