"""CRUD API for custom agents.

Phase 0: Authentication, permission control, active-thread guard.
Phase 1: category / created_by / created_at metadata fields.
"""

import logging
import shutil
from datetime import UTC, datetime

import yaml
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.gateway.authz import _authenticate
from deerflow.config.agents_api_config import get_agents_api_config
from deerflow.config.agents_config import AGENT_NAME_PATTERN, AgentConfig, can_see_agent, list_custom_agents, load_agent_config, load_agent_soul, preserve_non_managed_fields
from deerflow.config.paths import get_paths

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["agents"])


# ---------------------------------------------------------------------------
# Response / request models
# ---------------------------------------------------------------------------


class AgentResponse(BaseModel):
    """Response model for a custom agent."""

    name: str = Field(..., description="Agent name (hyphen-case)")
    description: str = Field(default="", description="Agent description")
    model: str | None = Field(default=None, description="Optional model override")
    tool_groups: list[str] | None = Field(default=None, description="Optional tool group whitelist")
    skills: list[str] | None = Field(default=None, description="Optional skill whitelist (None=all, []=none)")
    soul: str | None = Field(default=None, description="SOUL.md content")
    suggestion_prompts: list[dict[str, str]] | None = Field(
        default=None,
        description="New-session natural-language suggestion chips [{label, prompt}] (seed-authored)",
    )

    # Phase 1 metadata
    category: str = Field(default="custom", description="Agent category: public or custom")
    created_by: str | None = Field(default=None, description="Creator user_id")
    created_at: str | None = Field(default=None, description="ISO creation timestamp")
    creator_name: str | None = Field(default=None, description="Creator display name (email)")


class AgentsListResponse(BaseModel):
    """Response model for listing all custom agents."""

    agents: list[AgentResponse]


class AgentCreateRequest(BaseModel):
    """Request body for creating a custom agent."""

    name: str = Field(..., description="Agent name (must match ^[A-Za-z0-9-]+$, stored as lowercase)")
    description: str = Field(default="", description="Agent description")
    model: str | None = Field(default=None, description="Optional model override")
    tool_groups: list[str] | None = Field(default=None, description="Optional tool group whitelist")
    skills: list[str] | None = Field(default=None, description="Optional skill whitelist (None=all enabled, []=none)")
    soul: str = Field(default="", description="SOUL.md content — agent personality and behavioral guardrails")


class AgentUpdateRequest(BaseModel):
    """Request body for updating a custom agent."""

    description: str | None = Field(default=None, description="Updated description")
    model: str | None = Field(default=None, description="Updated model override")
    tool_groups: list[str] | None = Field(default=None, description="Updated tool group whitelist")
    skills: list[str] | None = Field(default=None, description="Updated skill whitelist (None=all, []=none)")
    soul: str | None = Field(default=None, description="Updated SOUL.md content")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _validate_agent_name(name: str) -> None:
    """Validate agent name against allowed pattern."""
    if not AGENT_NAME_PATTERN.fullmatch(name):
        raise HTTPException(
            status_code=422,
            detail=f"Invalid agent name '{name}'. Must match ^[A-Za-z0-9-]+$ (letters, digits, and hyphens only).",
        )


# SEC-C2 (security plan 2026-09-02 B2): tool groups a custom agent may mount
# through the self-service API. Deliberately minimal — ``bash``/``file:write``
# are arbitrary-code-execution surfaces on the host sandbox and
# ``video_engine`` is a heavy paid-resource group; letting any authenticated
# user grant them to a custom agent is self-service RCE. ``file:read`` (read
# the caller's own thread sandbox) is pending a product decision — add it
# here when approved. Pre-existing agents are NOT re-validated on unrelated
# updates (存量处置 belongs to the SEC-C3 follow-up batch).
CUSTOM_AGENT_ALLOWED_TOOL_GROUPS: frozenset[str] = frozenset({"web"})


def _validate_tool_groups(tool_groups: list[str] | None) -> None:
    """Reject tool_groups outside CUSTOM_AGENT_ALLOWED_TOOL_GROUPS (SEC-C2)."""
    if tool_groups is None:
        return
    denied = sorted({g for g in tool_groups if g not in CUSTOM_AGENT_ALLOWED_TOOL_GROUPS})
    if denied:
        raise HTTPException(
            status_code=422,
            detail=f"自定义 agent 不允许挂载该工具组: {', '.join(denied)}（允许: {', '.join(sorted(CUSTOM_AGENT_ALLOWED_TOOL_GROUPS))}）",
        )


def _normalize_agent_name(name: str) -> str:
    """Normalize agent name to lowercase for filesystem storage."""
    return name.lower()


def _require_agents_api_enabled() -> None:
    """Reject access unless the custom-agent management API is explicitly enabled."""
    if not get_agents_api_config().enabled:
        raise HTTPException(
            status_code=403,
            detail=("Custom-agent management API is disabled. Set agents_api.enabled=true to expose agent and user-profile routes over HTTP."),
        )


async def _ensure_auth(request: Request) -> None:
    """Ensure the request is authenticated. Must be called AFTER _require_agents_api_enabled().

    This is an inline auth check rather than a decorator, so that the
    agents-api-enabled guard runs first and returns 403 (not 401) when disabled.
    """
    auth = getattr(request.state, "auth", None)
    if auth is None:
        auth = await _authenticate(request)
        request.state.auth = auth
    if not auth.is_authenticated:
        raise HTTPException(status_code=401, detail="Authentication required")


async def _resolve_creator_name_async(created_by: str | None, request: Request) -> str | None:
    """Async version of creator name resolution."""
    if created_by is None:
        return None
    try:
        from app.gateway.deps import get_local_provider

        provider = get_local_provider()
        user = await provider.get_user(created_by)
        return user.email if user else None
    except Exception:
        return None


def _agent_config_to_response(
    agent_cfg: AgentConfig,
    include_soul: bool = False,
    creator_name: str | None = None,
) -> AgentResponse:
    """Convert AgentConfig to AgentResponse."""
    soul: str | None = None
    if include_soul:
        soul = load_agent_soul(agent_cfg.name) or ""

    return AgentResponse(
        name=agent_cfg.name,
        description=agent_cfg.description,
        model=agent_cfg.model,
        tool_groups=agent_cfg.tool_groups,
        skills=agent_cfg.skills,
        soul=soul,
        suggestion_prompts=agent_cfg.suggestion_prompts,
        category=agent_cfg.category,
        created_by=agent_cfg.created_by,
        created_at=agent_cfg.created_at,
        creator_name=creator_name,
    )


async def _check_delete_permission(agent_name: str, request: Request) -> AgentConfig:
    """Load agent and verify the current user has permission to delete it.

    Returns the loaded AgentConfig if deletion is allowed.

    Raises:
        HTTPException: 404 if agent not found
        HTTPException: 403 if user lacks permission
    """
    try:
        agent_cfg = load_agent_config(agent_name)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_name}' not found")

    auth = getattr(request.state, "auth", None)
    user = getattr(auth, "user", None) if auth else None

    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required")

    # Admin can delete anything
    if user.system_role == "admin":
        return agent_cfg

    # Public agents: only admin can delete
    if agent_cfg.category == "public":
        raise HTTPException(status_code=403, detail="Public agents can only be deleted by admin")

    # Custom agents: only creator can delete (return 404 to avoid leaking existence)
    if agent_cfg.created_by is not None and agent_cfg.created_by != str(user.id):
        raise HTTPException(status_code=404, detail=f"Agent '{agent_name}' not found")

    # SEC-M22 (批次 2 项 2.9): legacy ownerless agents are admin-only to delete
    if agent_cfg.created_by is None:
        raise HTTPException(status_code=403, detail="Legacy agents without an owner can only be deleted by admin")

    return agent_cfg


async def _check_active_threads(agent_name: str, request: Request) -> None:
    """Check if any thread is currently using this agent.

    Raises HTTPException 409 if active threads found.
    """
    try:
        from app.gateway.deps import get_thread_store

        thread_store = get_thread_store(request)
        rows = await thread_store.search(
            metadata={"agent_name": agent_name},
            limit=1,
        )
        if rows:
            raise HTTPException(
                status_code=409,
                detail="Cannot delete: this agent is used by existing threads",
            )
    except HTTPException as exc:
        # Only propagate the 409 active-thread conflict; treat other errors
        # (e.g. 503 thread store unavailable) as non-fatal.
        if exc.status_code == 409:
            raise
        logger.debug("Could not check active threads for agent %s (non-fatal)", agent_name)
    except Exception:
        # If thread store is unavailable, skip the check rather than block deletion
        logger.debug("Could not check active threads for agent %s (non-fatal)", agent_name)


def _get_user_from_request(request: Request):
    """Extract user from request auth context."""
    auth = getattr(request.state, "auth", None)
    return getattr(auth, "user", None) if auth else None


async def _get_optional_user(request: Request):
    """Try to resolve the authenticated user without enforcing auth.

    Returns User if authenticated, None otherwise.  Does not raise.
    Logs unexpected errors at WARNING so auth failures are not silent.
    """
    try:
        auth = getattr(request.state, "auth", None)
        if auth is None:
            auth = await _authenticate(request)
            request.state.auth = auth
        if auth.is_authenticated:
            return auth.user
        return None
    except Exception:
        logger.warning("Unexpected error resolving optional user", exc_info=True)
        return None


async def _check_update_permission(agent_cfg: AgentConfig, request: Request) -> None:
    """Verify the current user has permission to update the agent.

    - Public agents: only admin
    - Custom agents with created_by: only creator or admin
    - Custom agents without created_by (legacy): admin only (SEC-M22,
      批次 2 项 2.9 — 原为任意认证用户)

    Raises HTTPException on permission denial.
    """
    user = _get_user_from_request(request)
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required")

    if user.system_role == "admin":
        return

    if agent_cfg.category == "public":
        raise HTTPException(status_code=403, detail="Public agents can only be updated by admin")

    # SEC-M22 (批次 2 项 2.9): legacy ownerless agents are admin-only to edit —
    # 同 skills：SOUL/配置进所有运行该 agent 的会话，任意用户可改是跨用户
    # 提示注入原语（原来任何认证用户都能改）。
    if agent_cfg.created_by is None:
        raise HTTPException(status_code=403, detail="Legacy agents without an owner can only be updated by admin")

    if agent_cfg.created_by != str(user.id):
        # Return 404 to avoid leaking existence of other users' agents
        raise HTTPException(status_code=404, detail=f"Agent '{agent_cfg.name}' not found")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/agents",
    response_model=AgentsListResponse,
    summary="List Custom Agents",
    description="List agents visible to the current user: all public agents plus own custom agents.",
)
async def list_agents(request: Request) -> AgentsListResponse:
    """List agents visible to the current user.

    - Public agents: visible to everyone (including anonymous)
    - Custom agents: visible only to their creator or admin

    Read-only: always available so the agent chat UI can resolve skill lists.
    """
    # NOTE: no _require_agents_api_enabled() guard — the agent chat page
    # needs this endpoint to render per-agent skill buttons even when the
    # management API is disabled.

    try:
        user = await _get_optional_user(request)
        user_id = str(user.id) if user else None
        is_admin = user.system_role == "admin" if user else False

        agents = list_custom_agents()
        visible = [a for a in agents if can_see_agent(a, user_id, is_admin)]

        # Batch-resolve creator names to avoid N+1 queries
        creator_ids = {a.created_by for a in visible if a.created_by is not None}
        creator_map: dict[str, str | None] = {}
        if creator_ids:
            try:
                from app.gateway.deps import get_local_provider

                provider = get_local_provider()
                for uid in creator_ids:
                    u = await provider.get_user(uid)
                    creator_map[uid] = u.email if u else None
            except Exception:
                logger.debug("Could not batch-resolve creator names (non-fatal)", exc_info=True)

        responses = [_agent_config_to_response(a, include_soul=True, creator_name=creator_map.get(a.created_by) if a.created_by else None) for a in visible]
        return AgentsListResponse(agents=responses)
    except Exception as e:
        logger.error(f"Failed to list agents: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list agents")


@router.get(
    "/agents/check",
    summary="Check Agent Name",
    description="Validate an agent name and check if it is available (case-insensitive).",
)
async def check_agent_name(name: str) -> dict:
    """Check whether an agent name is valid and not yet taken."""
    _require_agents_api_enabled()
    _validate_agent_name(name)
    normalized = _normalize_agent_name(name)
    available = not get_paths().agent_dir(normalized).exists()
    return {"available": available, "name": normalized}


@router.get(
    "/agents/{name}",
    response_model=AgentResponse,
    summary="Get Custom Agent",
    description="Retrieve details and SOUL.md content for a specific custom agent. Public agents visible to all; custom agents only to creator/admin.",
)
async def get_agent(name: str, request: Request) -> AgentResponse:
    """Get a specific custom agent by name.

    Access control:
    - Public agents: visible to everyone
    - Custom agents: visible only to their creator or admin

    Read-only: always available so the agent chat UI can resolve skill lists.
    """
    # NOTE: no _require_agents_api_enabled() guard — the agent chat page
    # needs this endpoint to render per-agent skill buttons even when the
    # management API is disabled.
    _validate_agent_name(name)
    name = _normalize_agent_name(name)

    try:
        agent_cfg = load_agent_config(name)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")

    user = await _get_optional_user(request)
    user_id = str(user.id) if user else None
    is_admin = user.system_role == "admin" if user else False
    if not can_see_agent(agent_cfg, user_id, is_admin):
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")

    try:
        creator_name = await _resolve_creator_name_async(agent_cfg.created_by, request)
        return _agent_config_to_response(agent_cfg, include_soul=True, creator_name=creator_name)
    except Exception as e:
        logger.error(f"Failed to get agent '{name}': {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get agent")


@router.post(
    "/agents",
    response_model=AgentResponse,
    status_code=201,
    summary="Create Custom Agent",
    description="Create a new custom agent with its config and SOUL.md.",
)
async def create_agent_endpoint(request: Request, body: AgentCreateRequest) -> AgentResponse:
    """Create a new custom agent.

    Automatically sets category='custom', created_by, and created_at from
    the authenticated user context.
    """
    _require_agents_api_enabled()
    await _ensure_auth(request)
    _validate_agent_name(body.name)
    _validate_tool_groups(body.tool_groups)  # SEC-C2
    normalized_name = _normalize_agent_name(body.name)

    agent_dir = get_paths().agent_dir(normalized_name)

    user = _get_user_from_request(request)
    user_id = str(user.id) if user else None
    now_iso = datetime.now(UTC).isoformat()

    try:
        # Atomic directory creation — avoids TOCTOU race between exists() and mkdir()
        try:
            agent_dir.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            raise HTTPException(status_code=409, detail=f"Agent '{normalized_name}' already exists")

        # Write config.yaml — include management metadata
        config_data: dict = {
            "name": normalized_name,
            "category": "custom",
            "created_by": user_id,
            "created_at": now_iso,
        }
        if body.description:
            config_data["description"] = body.description
        if body.model is not None:
            config_data["model"] = body.model
        if body.tool_groups is not None:
            config_data["tool_groups"] = body.tool_groups
        else:
            # Plan B2 step 5 (2026-09-02 复核落定): a create without tool_groups
            # must NOT implicitly mount every tool — it lands on the allowlist
            # default instead. Only pre-existing agents keep their legacy null
            # (存量处置属 SEC-C3 后续批次).
            config_data["tool_groups"] = sorted(CUSTOM_AGENT_ALLOWED_TOOL_GROUPS)
        if body.skills is not None:
            config_data["skills"] = body.skills

        config_file = agent_dir / "config.yaml"
        with open(config_file, "w", encoding="utf-8") as f:
            yaml.dump(config_data, f, default_flow_style=False, allow_unicode=True)

        # Write SOUL.md
        soul_file = agent_dir / "SOUL.md"
        soul_file.write_text(body.soul, encoding="utf-8")

        logger.info(f"Created agent '{normalized_name}' at {agent_dir}")

        agent_cfg = load_agent_config(normalized_name)
        creator_name = user.email if user else None
        return _agent_config_to_response(agent_cfg, include_soul=True, creator_name=creator_name)

    except HTTPException:
        raise
    except Exception as e:
        # Clean up on failure
        if agent_dir.exists():
            shutil.rmtree(agent_dir)
        logger.error(f"Failed to create agent '{body.name}': {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create agent")


@router.put(
    "/agents/{name}",
    response_model=AgentResponse,
    summary="Update Custom Agent",
    description="Update an existing custom agent's config and/or SOUL.md.",
)
async def update_agent(name: str, request: Request, body: AgentUpdateRequest) -> AgentResponse:
    """Update an existing custom agent."""
    _require_agents_api_enabled()
    await _ensure_auth(request)
    _validate_agent_name(name)
    name = _normalize_agent_name(name)

    try:
        agent_cfg = load_agent_config(name)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")

    # Permission check
    await _check_update_permission(agent_cfg, request)

    # SEC-C2: validate only the newly supplied value — an unrelated PUT must
    # not start failing for a legacy agent that already carries a denied group.
    if "tool_groups" in body.model_fields_set and body.tool_groups is not None:
        _validate_tool_groups(body.tool_groups)

    agent_dir = get_paths().agent_dir(name)

    try:
        # Update config if any config fields changed
        # Use model_fields_set to distinguish "field omitted" from "explicitly set to null".
        fields_set = body.model_fields_set
        config_changed = bool(fields_set & {"description", "model", "tool_groups", "skills"})

        if config_changed:
            # R-06 (2026-08-13): start from the existing config's non-managed
            # fields (memory_scope, category, created_by, created_at, ...) so a
            # hand-authored key is never silently stripped by an unrelated
            # update. Only the PUT-managed fields below are overwritten.
            updated: dict = preserve_non_managed_fields(agent_cfg)
            updated["description"] = body.description if "description" in fields_set else agent_cfg.description

            new_model = body.model if "model" in fields_set else agent_cfg.model
            if new_model is not None:
                updated["model"] = new_model

            # SEC-C2 (复核修复, 反馈 1 问题 2): an explicit ``tool_groups: null``
            # must NOT strip an existing explicit whitelist. tool_groups is a
            # managed field, so omitting it here would drop the key from the
            # rewritten config.yaml — silently reverting the agent to null =
            # mount-every-tool (an escalation hole while legacy created_by=null
            # agents are editable by any authenticated user). Explicit null and
            # an omitted field both inherit the current value.
            if "tool_groups" in fields_set and body.tool_groups is not None:
                new_tool_groups = body.tool_groups
            else:
                new_tool_groups = agent_cfg.tool_groups
            if new_tool_groups is not None:
                updated["tool_groups"] = new_tool_groups

            # skills: None = inherit all, [] = no skills, ["a","b"] = whitelist
            if "skills" in fields_set:
                new_skills = body.skills
            else:
                new_skills = agent_cfg.skills
            if new_skills is not None:
                updated["skills"] = new_skills

            config_file = agent_dir / "config.yaml"
            with open(config_file, "w", encoding="utf-8") as f:
                yaml.dump(updated, f, default_flow_style=False, allow_unicode=True)

        # Update SOUL.md if provided
        if body.soul is not None:
            soul_path = agent_dir / "SOUL.md"
            soul_path.write_text(body.soul, encoding="utf-8")

        logger.info(f"Updated agent '{name}'")

        refreshed_cfg = load_agent_config(name)
        creator_name = await _resolve_creator_name_async(refreshed_cfg.created_by, request)
        return _agent_config_to_response(refreshed_cfg, include_soul=True, creator_name=creator_name)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update agent '{name}': {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to update agent")


@router.delete(
    "/agents/{name}",
    status_code=204,
    summary="Delete Custom Agent",
    description="Delete a custom agent and all its files (config, SOUL.md, memory). Requires ownership or admin role.",
)
async def delete_agent(name: str, request: Request) -> None:
    """Delete a custom agent with permission check.

    - Public agents: only admin can delete
    - Custom agents: creator or admin can delete
    - Checks for active threads using this agent before deletion
    """
    _require_agents_api_enabled()
    await _ensure_auth(request)
    _validate_agent_name(name)
    name = _normalize_agent_name(name)

    # Permission check
    await _check_delete_permission(name, request)

    # Active thread guard
    await _check_active_threads(name, request)

    agent_dir = get_paths().agent_dir(name)

    if not agent_dir.exists():
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")

    try:
        shutil.rmtree(agent_dir)
        logger.info(f"Deleted agent '{name}' from {agent_dir}")
    except Exception as e:
        logger.error(f"Failed to delete agent '{name}': {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to delete agent")


# ---------------------------------------------------------------------------
# User Profile (unchanged, kept for backward compatibility)
# ---------------------------------------------------------------------------


class UserProfileResponse(BaseModel):
    """Response model for the global user profile (USER.md)."""

    content: str | None = Field(default=None, description="USER.md content, or null if not yet created")


class UserProfileUpdateRequest(BaseModel):
    """Request body for setting the global user profile."""

    content: str = Field(default="", description="USER.md content — describes the user's background and preferences")


@router.get(
    "/user-profile",
    response_model=UserProfileResponse,
    summary="Get User Profile",
    description="Read the global USER.md file that is injected into all custom agents. Requires authentication.",
)
async def get_user_profile(request: Request) -> UserProfileResponse:
    """Return the current USER.md content."""
    _require_agents_api_enabled()
    await _ensure_auth(request)

    try:
        user_md_path = get_paths().user_md_file
        if not user_md_path.exists():
            return UserProfileResponse(content=None)
        raw = user_md_path.read_text(encoding="utf-8").strip()
        return UserProfileResponse(content=raw or None)
    except Exception as e:
        logger.error(f"Failed to read user profile: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to read user profile")


@router.put(
    "/user-profile",
    response_model=UserProfileResponse,
    summary="Update User Profile",
    description="Write the global USER.md file that is injected into all custom agents. Requires authentication.",
)
async def update_user_profile(request: Request, body: UserProfileUpdateRequest) -> UserProfileResponse:
    """Create or overwrite the global USER.md."""
    _require_agents_api_enabled()
    await _ensure_auth(request)

    try:
        paths = get_paths()
        paths.base_dir.mkdir(parents=True, exist_ok=True)
        paths.user_md_file.write_text(body.content, encoding="utf-8")
        logger.info(f"Updated USER.md at {paths.user_md_file}")
        return UserProfileResponse(content=body.content or None)
    except Exception as e:
        logger.error(f"Failed to update user profile: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to update user profile")
