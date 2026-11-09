"""Platform-admin gate for routes that must not be reachable by every user.

Why this exists as its own concept
----------------------------------

``authz.require_permission`` cannot express "admin". ``authz._authenticate``
grants **every** authenticated user the entire ``_ALL_PERMISSIONS`` list, and
that list holds no admin entry — so a permission check is, for admin purposes,
always true. That is why ``console.console_users`` stacks a hand-written
``_is_admin`` test *on top of* its ``@require_permission`` decorator rather
than relying on it.

Before this module the repo had four separate hand-rolled spellings of the
same idea (``console.py``'s ``_is_admin`` helper, ``auth.py``'s inline
``!=`` checks, ``agents.py``'s positive early-return form). New admin routes
use this dependency instead of inventing a fifth.

Usage::

    @router.put("/config", dependencies=[Depends(require_admin)])
    async def update_config(...): ...

Declaring it as a *dependency* rather than an in-body check is deliberate:
FastAPI resolves dependencies before entering the handler, so the guard still
fires on handlers whose body is wrapped in a broad ``except Exception`` — an
in-body ``raise HTTPException(403)`` inside such a handler would be caught and
re-raised as a 500.
"""

from __future__ import annotations

from fastapi import HTTPException, Request

# The one role string that counts as a platform admin. Matches the
# ``UserRow.system_role`` value written by ``auth.initialize_admin`` and read
# by every pre-existing admin check. Note that ``"internal"`` (the
# same-process channel identity from ``internal_auth``) is deliberately *not*
# admin: IM channel calls must not inherit platform-administration rights.
ADMIN_ROLE = "admin"


def is_admin_request(request: Request) -> bool:
    """True when *request*'s resolved caller holds the platform admin role.

    Reads the identity ``AuthMiddleware`` already stamped on
    ``request.state.auth`` — no second auth pipeline, and no chance of the
    role and the user id being resolved from different sources.
    """
    auth = getattr(request.state, "auth", None)
    user = getattr(auth, "user", None) if auth is not None else None
    return getattr(user, "system_role", None) == ADMIN_ROLE


def require_admin(request: Request) -> None:
    """Refuse the request with 403 unless the caller is a platform admin.

    Fails closed: an absent auth context (anonymous, or a router mounted
    without ``AuthMiddleware``) is refused rather than allowed.
    """
    if not is_admin_request(request):
        raise HTTPException(status_code=403, detail="Admin access required")
