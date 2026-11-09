"""Authentication endpoints."""

import asyncio
import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr, Field, field_validator

from app.gateway._shared.proxy_trust import is_trusted_proxy, is_valid_ip
from app.gateway.auth import (
    InviteConfigResponse,
    InviteConfigUpdateRequest,
    UserResponse,
    create_access_token,
)
from app.gateway.auth.config import get_auth_config
from app.gateway.auth.errors import AuthErrorCode, AuthErrorResponse
from app.gateway.csrf_middleware import (
    CSRF_COOKIE_NAME,
    generate_csrf_token,
    is_secure_request,
)
from app.gateway.deps import get_current_user_from_request, get_local_provider, get_optional_user_from_request

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


# ── Request/Response Models ──────────────────────────────────────────────


class LoginResponse(BaseModel):
    """Response model for login — token only lives in HttpOnly cookie."""

    expires_in: int  # seconds
    needs_setup: bool = False


# Top common-password blocklist. Drawn from the public SecLists "10k worst
# passwords" set, lowercased + length>=8 only (shorter ones already fail
# the min_length check). Kept tight on purpose: this is the **lower bound**
# defense, not a full HIBP / passlib check, and runs in-process per request.
_COMMON_PASSWORDS: frozenset[str] = frozenset(
    {
        "password",
        "password1",
        "password12",
        "password123",
        "password1234",
        "12345678",
        "123456789",
        "1234567890",
        "qwerty12",
        "qwertyui",
        "qwerty123",
        "abc12345",
        "abcd1234",
        "iloveyou",
        "letmein1",
        "welcome1",
        "welcome123",
        "admin123",
        "administrator",
        "passw0rd",
        "p@ssw0rd",
        "monkey12",
        "trustno1",
        "sunshine",
        "princess",
        "football",
        "baseball",
        "superman",
        "batman123",
        "starwars",
        "dragon123",
        "master123",
        "shadow12",
        "michael1",
        "jennifer",
        "computer",
    }
)


def _password_is_common(password: str) -> bool:
    """Case-insensitive blocklist check.

    Lowercases the input so trivial mutations like ``Password`` /
    ``PASSWORD`` are also rejected. Does not normalize digit substitutions
    (``p@ssw0rd`` is included as a literal entry instead) — keeping the
    rule cheap and predictable.
    """
    return password.lower() in _COMMON_PASSWORDS


def _validate_strong_password(value: str) -> str:
    """Pydantic field-validator body shared by Register + ChangePassword.

    Constraint = function, not type-level mixin. The two request models
    have no "is-a" relationship; they only share the password-strength
    rule. Lifting it into a free function lets each model bind it via
    ``@field_validator(field_name)`` without inheritance gymnastics.
    """
    if _password_is_common(value):
        raise ValueError("Password is too common; choose a stronger password.")
    return value


class RegisterRequest(BaseModel):
    """Request model for user registration."""

    email: EmailStr
    password: str = Field(..., min_length=8)
    invite_code: str | None = Field(None, description="Invite code required when invite system is enabled")

    _strong_password = field_validator("password")(classmethod(lambda cls, v: _validate_strong_password(v)))


class ChangePasswordRequest(BaseModel):
    """Request model for password change (also handles setup flow)."""

    current_password: str
    new_password: str = Field(..., min_length=8)
    new_email: EmailStr | None = None

    _strong_password = field_validator("new_password")(classmethod(lambda cls, v: _validate_strong_password(v)))


class MessageResponse(BaseModel):
    """Generic message response."""

    message: str


# ── Helpers ───────────────────────────────────────────────────────────────


def _set_session_cookie(response: Response, token: str, request: Request) -> None:
    """Set the access_token HttpOnly cookie on the response."""
    config = get_auth_config()
    is_https = is_secure_request(request)
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        secure=is_https,
        samesite="lax",
        max_age=config.token_expiry_days * 24 * 3600 if is_https else None,
    )


# ── Rate Limiting ────────────────────────────────────────────────────────
# In-process dict — not shared across workers.
#
# **Limitation**: with multi-worker deployments (e.g., gunicorn -w N), each
# worker maintains its own lockout table, so an attacker effectively gets
# N × _MAX_LOGIN_ATTEMPTS guesses before being locked out everywhere. For
# production multi-worker setups, replace this with a shared store (Redis,
# database-backed counter) to enforce a true per-IP limit.

_MAX_LOGIN_ATTEMPTS = 5
_LOCKOUT_SECONDS = 300  # 5 minutes

# ip → (fail_count, lock_until_timestamp)
_login_attempts: dict[str, tuple[int, float]] = {}

# SEC-M2 (批次 2 项 2.7): per-ACCOUNT 维度（2026-09-03 需求方拍板：账号
# 10 次 / 锁 15 分钟；IP 维度 5 次 / 5 分钟不动）。
# 取值理由（设计陷阱，勿随手改齐两套阈值）：per-account 必须比 per-IP
# 宽松一档——否则攻击者可对任意已知邮箱故意输错密码，把他人账号锁死
# （针对性 DoS）。10 次仍足以拦在线爆破（配合 per-IP 5 次，攻击者每
# 5 次失败就被迫换 IP）。账号键做 strip+lower 归一（大小写变体不得
# 绕过计数）。企业共享出口 IP 误锁面：per-IP 计数只累计「该 IP 的失败」
# （既有行为，本项不放宽）；per-account 只累计「该账号的失败」——同事
# 各自输错不互相锁对方的账号，两套限制叠加不产生新的误锁路径。
_MAX_ACCOUNT_LOGIN_ATTEMPTS = 10
_ACCOUNT_LOCKOUT_SECONDS = 900  # 15 minutes

# normalized email → (fail_count, lock_until_timestamp)
_account_attempts: dict[str, tuple[int, float]] = {}


def _normalize_account_key(account: str | None) -> str | None:
    if account is None:
        return None
    return account.strip().lower()


def _get_client_ip(request: Request) -> str:
    """Extract the real client IP for rate limiting.

    Trust model:

    - The TCP peer (``request.client.host``) is always the baseline. It is
      whatever the kernel reports as the connecting socket — unforgeable
      by the client itself.
    - ``X-Real-IP`` is **only** honored if the TCP peer is in the
      ``AUTH_TRUSTED_PROXIES`` allowlist (set via env var, comma-separated
      CIDR or single IPs). When set, the gateway is assumed to be behind a
      reverse proxy (nginx, Cloudflare, ALB, …) that overwrites
      ``X-Real-IP`` with the original client address.
    - With no ``AUTH_TRUSTED_PROXIES`` set, ``X-Real-IP`` is silently
      ignored — closing the bypass where any client could rotate the
      header to dodge per-IP rate limits in dev / direct-gateway mode.

    ``X-Forwarded-For`` is intentionally NOT used because it is naturally
    client-controlled at the *first* hop and the trust chain is harder to
    audit per-request.
    """
    peer_host = request.client.host if request.client else None

    # SEC-M4 (R2, 2026-09-09): peer allowlist lives in _shared.proxy_trust.
    if is_trusted_proxy(peer_host):
        real_ip = request.headers.get("x-real-ip", "").strip()
        # 条目 32（2026-09-12 缺陷批）：格式校验——垃圾串头回落 peer，不进限流 key
        # 命名空间（网段内容器伪造面）。合法 IP 轮换绕锁的主风险由条目 8 网段收窄
        # + per-account 锁缓解，此处仅卫生加固。
        if real_ip and is_valid_ip(real_ip):
            return real_ip

    return peer_host or "unknown"


def _check_locked(table: dict[str, tuple[int, float]], key: str, max_attempts: int) -> None:
    """Raise 429 if the key is currently locked out in ``table``; prune expired."""
    record = table.get(key)
    if record is None:
        return
    fail_count, lock_until = record
    if fail_count >= max_attempts:
        if time.time() < lock_until:
            raise HTTPException(
                status_code=429,
                detail="Too many login attempts. Try again later.",
            )
        del table[key]


def _check_rate_limit(ip: str, account: str | None = None) -> None:
    """Raise 429 if the IP — or the account (SEC-M2) — is currently locked out."""
    _check_locked(_login_attempts, ip, _MAX_LOGIN_ATTEMPTS)
    normalized = _normalize_account_key(account)
    if normalized:
        _check_locked(_account_attempts, normalized, _MAX_ACCOUNT_LOGIN_ATTEMPTS)


_MAX_TRACKED_IPS = 10000


def _record_failure_in(table: dict[str, tuple[int, float]], key: str, max_attempts: int, lockout_seconds: float) -> None:
    """Record one failure against ``key`` in ``table`` (with bounded-size eviction)."""
    # Evict expired lockouts when dict grows too large
    if len(table) >= _MAX_TRACKED_IPS:
        now = time.time()
        expired = [k for k, (c, t) in table.items() if c >= max_attempts and now >= t]
        for k in expired:
            del table[k]
        # If still too large, evict cheapest-to-lose half: below-threshold
        # keys (lock_until=0.0) sort first, then earliest-expiring lockouts.
        if len(table) >= _MAX_TRACKED_IPS:
            by_time = sorted(table.items(), key=lambda kv: kv[1][1])
            for k, _ in by_time[: len(by_time) // 2]:
                del table[k]

    record = table.get(key)
    if record is None:
        table[key] = (1, 0.0)
    else:
        new_count = record[0] + 1
        lock_until = time.time() + lockout_seconds if new_count >= max_attempts else 0.0
        table[key] = (new_count, lock_until)


def _record_login_failure(ip: str, account: str | None = None) -> None:
    """Record a failed login attempt for the given IP — and account (SEC-M2)."""
    _record_failure_in(_login_attempts, ip, _MAX_LOGIN_ATTEMPTS, _LOCKOUT_SECONDS)
    normalized = _normalize_account_key(account)
    if normalized:
        _record_failure_in(_account_attempts, normalized, _MAX_ACCOUNT_LOGIN_ATTEMPTS, _ACCOUNT_LOCKOUT_SECONDS)


def _record_login_success(ip: str, account: str | None = None) -> None:
    """Clear failure counters for the given IP — and account — on successful login."""
    _login_attempts.pop(ip, None)
    normalized = _normalize_account_key(account)
    if normalized:
        _account_attempts.pop(normalized, None)


# ── Endpoints ─────────────────────────────────────────────────────────────


@router.post("/login/local", response_model=LoginResponse)
async def login_local(
    request: Request,
    response: Response,
    form_data: OAuth2PasswordRequestForm = Depends(),
):
    """Local email/password login."""
    client_ip = _get_client_ip(request)
    _check_rate_limit(client_ip, form_data.username)

    user = await get_local_provider().authenticate({"email": form_data.username, "password": form_data.password})

    if user is None:
        _record_login_failure(client_ip, form_data.username)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=AuthErrorResponse(code=AuthErrorCode.INVALID_CREDENTIALS, message="Incorrect email or password").model_dump(),
        )

    _record_login_success(client_ip, form_data.username)
    token = create_access_token(str(user.id), token_version=user.token_version)
    _set_session_cookie(response, token, request)

    return LoginResponse(
        expires_in=get_auth_config().token_expiry_days * 24 * 3600,
        needs_setup=user.needs_setup,
    )


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(request: Request, response: Response, body: RegisterRequest):
    """Register a new user account (always 'user' role).

    Admin is created via /initialize on first boot. This endpoint creates
    regular users only when an admin already exists.
    Auto-login by setting the session cookie.
    """
    # ── System initialization check ──
    admin_count = await get_local_provider().count_admin_users()
    if admin_count == 0:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=AuthErrorResponse(
                code=AuthErrorCode.SYSTEM_NOT_INITIALIZED,
                message="System not initialized. Please create an admin account first.",
            ).model_dump(),
        )

    # ── Invite code validation ──
    await _validate_invite_code(body.invite_code)

    try:
        user = await get_local_provider().create_user(email=body.email, password=body.password, system_role="user")
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=AuthErrorResponse(code=AuthErrorCode.EMAIL_ALREADY_EXISTS, message="Email already registered").model_dump(),
        )

    token = create_access_token(str(user.id), token_version=user.token_version)
    _set_session_cookie(response, token, request)

    return UserResponse(id=str(user.id), email=user.email, system_role=user.system_role)


@router.post("/logout", response_model=MessageResponse)
async def logout(request: Request, response: Response):
    """Logout current user: clear the cookie AND revoke the user's JWTs.

    Bumps the **user-level** ``token_version`` (audit §1-1 / C1, 2026-10-05) so
    this user's tokens on every device fail the version check with 401 —
    logout is no longer cookie-clearing only. Anonymous logout (no/invalid
    cookie) just clears the cookie.
    """
    response.delete_cookie(key="access_token", secure=is_secure_request(request), samesite="lax")
    user = await get_optional_user_from_request(request)
    if user is not None:
        user.token_version += 1
        await get_local_provider().update_user(user)
    return MessageResponse(message="Successfully logged out")


@router.post("/change-password", response_model=MessageResponse)
async def change_password(request: Request, response: Response, body: ChangePasswordRequest):
    """Change password for the currently authenticated user.

    Also handles the first-boot setup flow:
    - If new_email is provided, updates email (checks uniqueness)
    - If user.needs_setup is True and new_email is given, clears needs_setup
    - Always increments token_version to invalidate old sessions
    - Re-issues session cookie with new token_version
    """
    from app.gateway.auth.password import hash_password_async, verify_password_async

    user = await get_current_user_from_request(request)

    if user.password_hash is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=AuthErrorResponse(code=AuthErrorCode.INVALID_CREDENTIALS, message="OAuth users cannot change password").model_dump())

    if not await verify_password_async(body.current_password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=AuthErrorResponse(code=AuthErrorCode.INVALID_CREDENTIALS, message="Current password is incorrect").model_dump())

    provider = get_local_provider()

    # Update email if provided
    if body.new_email is not None:
        existing = await provider.get_user_by_email(body.new_email)
        if existing and str(existing.id) != str(user.id):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=AuthErrorResponse(code=AuthErrorCode.EMAIL_ALREADY_EXISTS, message="Email already in use").model_dump())
        user.email = body.new_email

    # Update password + bump version
    user.password_hash = await hash_password_async(body.new_password)
    user.token_version += 1

    # Clear setup flag if this is the setup flow
    if user.needs_setup and body.new_email is not None:
        user.needs_setup = False

    await provider.update_user(user)

    # Re-issue cookie with new token_version
    token = create_access_token(str(user.id), token_version=user.token_version)
    _set_session_cookie(response, token, request)

    return MessageResponse(message="Password changed successfully")


@router.get("/me", response_model=UserResponse)
async def get_me(request: Request):
    """Get current authenticated user info."""
    user = await get_current_user_from_request(request)
    return UserResponse(id=str(user.id), email=user.email, system_role=user.system_role, needs_setup=user.needs_setup)


# Per-IP cache: ip → (timestamp, result_dict).
# Returns the cached result within the TTL instead of 429, because
# the answer (whether an admin exists) rarely changes and returning
# 429 breaks multi-tab / post-restart reconnection storms.
_SETUP_STATUS_CACHE: dict[str, tuple[float, dict]] = {}
_SETUP_STATUS_CACHE_TTL_SECONDS = 60
_MAX_TRACKED_SETUP_STATUS_IPS = 10000
_SETUP_STATUS_INFLIGHT: dict[str, asyncio.Task[dict]] = {}
_SETUP_STATUS_INFLIGHT_GUARD = asyncio.Lock()


@router.get("/setup-status")
async def setup_status(request: Request):
    """Check if an admin account exists. Returns needs_setup=True when no admin exists."""
    client_ip = _get_client_ip(request)
    now = time.time()

    # Return cached result when within TTL — avoids 429 on multi-tab reconnection.
    cached = _SETUP_STATUS_CACHE.get(client_ip)
    if cached is not None:
        cached_time, cached_result = cached
        if now - cached_time < _SETUP_STATUS_CACHE_TTL_SECONDS:
            return cached_result

    async with _SETUP_STATUS_INFLIGHT_GUARD:
        # Recheck cache after waiting for the inflight guard.
        now = time.time()
        cached = _SETUP_STATUS_CACHE.get(client_ip)
        if cached is not None:
            cached_time, cached_result = cached
            if now - cached_time < _SETUP_STATUS_CACHE_TTL_SECONDS:
                return cached_result

        task = _SETUP_STATUS_INFLIGHT.get(client_ip)
        if task is None:
            # Evict stale entries when dict grows too large to bound memory usage.
            if len(_SETUP_STATUS_CACHE) >= _MAX_TRACKED_SETUP_STATUS_IPS:
                cutoff = now - _SETUP_STATUS_CACHE_TTL_SECONDS
                stale = [k for k, (t, _) in _SETUP_STATUS_CACHE.items() if t < cutoff]
                for k in stale:
                    del _SETUP_STATUS_CACHE[k]
                if len(_SETUP_STATUS_CACHE) >= _MAX_TRACKED_SETUP_STATUS_IPS:
                    by_time = sorted(_SETUP_STATUS_CACHE.items(), key=lambda entry: entry[1][0])
                    for k, _ in by_time[: len(by_time) // 2]:
                        del _SETUP_STATUS_CACHE[k]

            async def _compute_setup_status() -> dict:
                admin_count = await get_local_provider().count_admin_users()
                return {"needs_setup": admin_count == 0}

            task = asyncio.create_task(_compute_setup_status())
            _SETUP_STATUS_INFLIGHT[client_ip] = task

    try:
        result = await task
    finally:
        async with _SETUP_STATUS_INFLIGHT_GUARD:
            if _SETUP_STATUS_INFLIGHT.get(client_ip) is task:
                del _SETUP_STATUS_INFLIGHT[client_ip]

    # Cache only the stable "initialized" result to avoid stale setup redirects.
    if result["needs_setup"] is False:
        _SETUP_STATUS_CACHE[client_ip] = (time.time(), result)
    else:
        _SETUP_STATUS_CACHE.pop(client_ip, None)
    return result


class InitializeAdminRequest(BaseModel):
    """Request model for first-boot admin account creation."""

    email: EmailStr
    password: str = Field(..., min_length=8)

    _strong_password = field_validator("password")(classmethod(lambda cls, v: _validate_strong_password(v)))


@router.post("/initialize", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def initialize_admin(request: Request, response: Response, body: InitializeAdminRequest):
    """Create the first admin account on initial system setup.

    Only callable when no admin exists. Returns 409 Conflict if an admin
    already exists.

    On success, the admin account is created with ``needs_setup=False`` and
    the session cookie is set.
    """
    admin_count = await get_local_provider().count_admin_users()
    if admin_count > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=AuthErrorResponse(code=AuthErrorCode.SYSTEM_ALREADY_INITIALIZED, message="System already initialized").model_dump(),
        )

    try:
        user = await get_local_provider().create_user(email=body.email, password=body.password, system_role="admin", needs_setup=False)
    except ValueError:
        # DB unique-constraint race: another concurrent request beat us.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=AuthErrorResponse(code=AuthErrorCode.SYSTEM_ALREADY_INITIALIZED, message="System already initialized").model_dump(),
        )

    token = create_access_token(str(user.id), token_version=user.token_version)
    _set_session_cookie(response, token, request)

    return UserResponse(id=str(user.id), email=user.email, system_role=user.system_role)


# ── Invite Code Helpers & Admin Endpoints ──────────────────────────────────


async def _get_invite_config_row():
    """Get or create the single invite_config row."""
    from datetime import UTC, datetime

    from sqlalchemy import select

    from deerflow.persistence.engine import get_session_factory
    from deerflow.persistence.invite.model import InviteConfigRow

    sf = get_session_factory()
    async with sf() as session:
        result = await session.execute(select(InviteConfigRow).where(InviteConfigRow.id == 1))
        row = result.scalar_one_or_none()
        if row is None:
            # Auto-create default row
            row = InviteConfigRow(
                id=1,
                code="",
                enabled=False,
                max_uses=None,
                current_uses=0,
                expires_at=None,
                updated_at=datetime.now(UTC),
                updated_by="",
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
        return row


async def _validate_invite_code(submitted_code: str | None) -> None:
    """Validate invite code against global config. Raises HTTPException on failure."""
    from datetime import UTC, datetime

    from sqlalchemy import select

    from deerflow.persistence.engine import get_session_factory
    from deerflow.persistence.invite.model import InviteConfigRow

    row = await _get_invite_config_row()

    # Not enabled — skip validation
    if not row.enabled:
        return

    # Code required but not provided
    if not submitted_code:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=AuthErrorResponse(code=AuthErrorCode.INVITE_CODE_INVALID, message="Invite code is required").model_dump(),
        )

    # Code mismatch
    if submitted_code != row.code:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=AuthErrorResponse(code=AuthErrorCode.INVITE_CODE_INVALID, message="Invalid invite code").model_dump(),
        )

    # Usage limit exceeded
    if row.max_uses is not None and row.current_uses >= row.max_uses:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=AuthErrorResponse(code=AuthErrorCode.INVITE_CODE_EXHAUSTED, message="Invite code has been used up").model_dump(),
        )

    # Expired
    if row.expires_at is not None:
        expires = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=UTC)
        if datetime.now(UTC) > expires:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=AuthErrorResponse(code=AuthErrorCode.INVITE_CODE_EXPIRED, message="Invite code has expired").model_dump(),
            )

    # Increment usage counter
    sf = get_session_factory()
    async with sf() as session:
        result = await session.execute(select(InviteConfigRow).where(InviteConfigRow.id == 1))
        db_row = result.scalar_one()
        db_row.current_uses += 1
        await session.commit()


@router.get("/invite-config", response_model=InviteConfigResponse)
async def get_invite_config(request: Request):
    """Get current invite code configuration (admin only)."""
    user = await get_current_user_from_request(request)
    if user.system_role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")

    row = await _get_invite_config_row()
    # Mask the code for display: show first 2 and last 2 chars
    code_masked = ""
    if row.code:
        if len(row.code) <= 4:
            code_masked = row.code[:1] + "*" * (len(row.code) - 1)
        else:
            code_masked = row.code[:2] + "*" * (len(row.code) - 4) + row.code[-2:]

    return InviteConfigResponse(
        enabled=row.enabled,
        code_masked=code_masked,
        max_uses=row.max_uses,
        current_uses=row.current_uses,
        expires_at=row.expires_at,
    )


@router.put("/invite-config", response_model=InviteConfigResponse)
async def update_invite_config(request: Request, body: InviteConfigUpdateRequest):
    """Update invite code configuration (admin only)."""
    from datetime import UTC, datetime

    from sqlalchemy import select

    from deerflow.persistence.engine import get_session_factory
    from deerflow.persistence.invite.model import InviteConfigRow

    user = await get_current_user_from_request(request)
    if user.system_role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")

    sf = get_session_factory()
    async with sf() as session:
        result = await session.execute(select(InviteConfigRow).where(InviteConfigRow.id == 1))
        row = result.scalar_one_or_none()
        if row is None:
            row = InviteConfigRow(id=1)
            session.add(row)

        row.enabled = body.enabled
        row.code = body.code
        row.max_uses = body.max_uses
        row.expires_at = body.expires_at
        row.updated_at = datetime.now(UTC)
        row.updated_by = str(user.id)

        # Reset usage counter if code changed
        if row.code != body.code:
            row.current_uses = 0

        await session.commit()
        await session.refresh(row)

    code_masked = ""
    if row.code:
        if len(row.code) <= 4:
            code_masked = row.code[:1] + "*" * (len(row.code) - 1)
        else:
            code_masked = row.code[:2] + "*" * (len(row.code) - 4) + row.code[-2:]

    return InviteConfigResponse(
        enabled=row.enabled,
        code_masked=code_masked,
        max_uses=row.max_uses,
        current_uses=row.current_uses,
        expires_at=row.expires_at,
    )


# ── OAuth Endpoints (Future/Placeholder) ─────────────────────────────────


@router.get("/oauth/{provider}")
async def oauth_login(provider: str):
    """Initiate OAuth login flow.

    Redirects to the OAuth provider's authorization URL.
    Currently a placeholder - requires OAuth provider implementation.
    """
    if provider not in ["github", "google"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported OAuth provider: {provider}",
        )

    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="OAuth login not yet implemented",
    )


@router.get("/callback/{provider}")
async def oauth_callback(provider: str, code: str, state: str):
    """OAuth callback endpoint.

    Handles the OAuth provider's callback after user authorization.
    Currently a placeholder.
    """
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="OAuth callback not yet implemented",
    )


@router.get("/csrf-token")
async def get_csrf_token(request: Request) -> JSONResponse:
    """Issue/refresh the ``csrf_token`` cookie for the Double Submit pattern.

    The CSRF middleware only sets ``csrf_token`` on auth-endpoint POST
    responses (login/register/logout/initialize). Browsers that never log in
    — no-auth / dev-bypass mode where ``/api/v1/auth/me`` returns a synthetic
    user — therefore never receive the cookie, and every state-changing
    Gateway POST (e.g. ``POST /api/writer/docs``) is rejected with 403.
    The frontend calls this once on mount to guarantee the cookie exists.

    GET is exempt from CSRF validation (``should_check_csrf``), so this route
    needs no token to call. The cookie is JS-readable (``httponly=False``) so
    ``fetcher.ts`` can echo it back as ``X-CSRF-Token``.
    """
    token = request.cookies.get(CSRF_COOKIE_NAME) or generate_csrf_token()
    response = JSONResponse({"token": token})
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=token,
        httponly=False,
        secure=is_secure_request(request),
        samesite="strict",
    )
    return response
