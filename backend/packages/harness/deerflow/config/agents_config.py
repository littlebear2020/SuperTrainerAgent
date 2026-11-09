"""Configuration and loaders for custom agents."""

import logging
import re
from typing import Any

import yaml
from pydantic import BaseModel

from deerflow.config.paths import get_paths

logger = logging.getLogger(__name__)

SOUL_FILENAME = "SOUL.md"
AGENT_NAME_PATTERN = re.compile(r"^[A-Za-z0-9-]+$")

# SEC-C3/M14 convergence defaults (2026-09-04): a custom agent whose
# config.yaml predates the write-side hardening (536b2f392) can lack the
# tool_groups/skills keys. At runtime None means "all tool groups"/"all
# enabled skills", which over-grants for user-created agents (bash/file
# groups; other users' custom skills from the shared skills directory).
# The load side converges legacy files to the same defaults POST applies
# today; lead_agent keeps a runtime hard gate as second line of defense.
CONVERGED_DEFAULT_TOOL_GROUPS = ["web"]
CONVERGED_DEFAULT_SKILLS: list[str] = []


def validate_agent_name(name: str | None) -> str | None:
    """Validate a custom agent name before using it in filesystem paths."""
    if name is None:
        return None
    if not isinstance(name, str):
        raise ValueError("Invalid agent name. Expected a string or None.")
    if not AGENT_NAME_PATTERN.fullmatch(name):
        raise ValueError(f"Invalid agent name '{name}'. Must match pattern: {AGENT_NAME_PATTERN.pattern}")
    return name


class AgentConfig(BaseModel):
    """Configuration for a custom agent."""

    name: str
    description: str = ""
    model: str | None = None
    tool_groups: list[str] | None = None
    # skills controls which skills are loaded into the agent's prompt:
    # - None (or omitted): load all enabled skills (default fallback behavior)
    # - [] (explicit empty list): disable all skills
    # - ["skill1", "skill2"]: load only the specified skills
    skills: list[str] | None = None

    # Memory write target:
    # - None or "agent": write to per-agent memory file (agents/{name}/memory.json)
    # - "global": write to global user memory file (memory.json)
    memory_scope: str | None = None

    # New-session suggestion chips (B.3, 2026-09-11 ppt-master 6.3.0 批次)：
    # [{label, prompt}] — natural-language prompt chips for the agent chat
    # empty state; unlike skill chips these prefill a full message (no /skill
    # insertion). None/omitted = no extra chips. Seed-authored only — the
    # update API does not manage this field (preserve_non_managed_fields
    # carries it forward on PUT).
    suggestion_prompts: list[dict[str, str]] | None = None

    # Management metadata — used by Gateway for permission control;
    # Harness runtime (lead_agent) ignores these fields.
    category: str = "custom"  # "public" (admin-published) or "custom" (user-created)
    created_by: str | None = None  # user_id of the creator
    created_at: str | None = None  # ISO timestamp


# Fields the Gateway PUT /api/agents/{name} surface manages explicitly.
# Everything else on the config (memory_scope, category, created_by,
# created_at, and any future field) must be carried forward untouched.
_MANAGED_AGENT_CONFIG_FIELDS = frozenset({"description", "model", "tool_groups", "skills"})


def preserve_non_managed_fields(existing_cfg: AgentConfig) -> dict:
    """Return every top-level field on ``existing_cfg`` not managed by the
    update API.

    Used by the surface that rewrites a custom agent's ``config.yaml``
    (``PUT /api/agents/{name}``) to carry forward hand-authored fields —
    currently ``memory_scope`` (e.g. the co-writer seed's ``memory_scope:
    none``) — that the update API does not expose as arguments. Without this,
    any PUT that touches description/model/tool_groups/skills silently strips
    them (R-06, 2026-08-13).

    ``exclude_unset=True`` keeps the file round-trip visually intact: a
    sub-field the author did not write (and that defaulted to a Pydantic
    default) is not materialized into the dict.
    """
    return existing_cfg.model_dump(exclude_unset=True, exclude=_MANAGED_AGENT_CONFIG_FIELDS)


def load_agent_config(name: str | None) -> AgentConfig | None:
    """Load the custom or default agent's config from its directory.

    Args:
        name: The agent name.

    Returns:
        AgentConfig instance.

    Raises:
        FileNotFoundError: If the agent directory or config.yaml does not exist.
        ValueError: If config.yaml cannot be parsed.
    """

    if name is None:
        return None

    name = validate_agent_name(name)
    agent_dir = get_paths().agent_dir(name)
    config_file = agent_dir / "config.yaml"

    if not agent_dir.exists():
        raise FileNotFoundError(f"Agent directory not found: {agent_dir}")

    if not config_file.exists():
        raise FileNotFoundError(f"Agent config not found: {config_file}")

    try:
        with open(config_file, encoding="utf-8") as f:
            data: dict[str, Any] = yaml.safe_load(f) or {}
    except yaml.YAMLError as e:
        raise ValueError(f"Failed to parse agent config {config_file}: {e}") from e

    # Ensure name is set from directory name if not in file
    if "name" not in data:
        data["name"] = name

    # Strip unknown fields before passing to Pydantic (e.g. legacy prompt_file)
    known_fields = set(AgentConfig.model_fields.keys())
    data = {k: v for k, v in data.items() if k in known_fields}

    # SEC-C3/M14: converge missing/null tool_groups/skills to the write-side
    # POST defaults. Only fires for custom agents (name=None returns above) —
    # the default lead agent and bootstrap never pass through this path, so
    # the None=全组 semantics they rely on (tools.py) are untouched.
    if data.get("tool_groups") is None:
        logger.warning(
            "Agent '%s' config has no explicit tool_groups (pre-hardening legacy file); converging to %s",
            name,
            CONVERGED_DEFAULT_TOOL_GROUPS,
        )
        data["tool_groups"] = list(CONVERGED_DEFAULT_TOOL_GROUPS)
    if data.get("skills") is None:
        logger.warning(
            "Agent '%s' config has no explicit skills (pre-hardening legacy file); converging to [] (all skills disabled)",
            name,
        )
        data["skills"] = list(CONVERGED_DEFAULT_SKILLS)

    return AgentConfig(**data)


def can_see_agent(agent_cfg: AgentConfig, user_id: str | None, is_admin: bool) -> bool:
    """Whether a user may see (and therefore run) a given custom agent.

    SEC-H8 shared predicate (moved from routers/agents.py so the run-path
    services layer can enforce the same visibility without importing routers):

    - public agents: visible to everyone (incl. anonymous)
    - custom agents with created_by: creator or admin only
    - custom agents without created_by (legacy pre-migration): any
      AUTHENTICATED user (anonymous is rejected — 免登录形态与认证形态行为
      统一, 2026-09-03 决策)
    """
    if agent_cfg.category == "public":
        return True
    if is_admin:
        return True
    # Legacy agents without created_by are visible to any authenticated user
    if agent_cfg.created_by is None:
        return user_id is not None
    if user_id is not None and agent_cfg.created_by == user_id:
        return True
    return False


def load_agent_soul(agent_name: str | None) -> str | None:
    """Read the SOUL.md file for a custom agent, if it exists.

    SOUL.md defines the agent's personality, values, and behavioral guardrails.
    It is injected into the lead agent's system prompt as additional context.

    Args:
        agent_name: The name of the agent or None for the default agent.

    Returns:
        The SOUL.md content as a string, or None if the file does not exist.
    """
    agent_dir = get_paths().agent_dir(agent_name) if agent_name else get_paths().base_dir
    soul_path = agent_dir / SOUL_FILENAME
    if not soul_path.exists():
        return None
    content = soul_path.read_text(encoding="utf-8").strip()
    return content or None


def list_custom_agents() -> list[AgentConfig]:
    """Scan the agents directory and return all valid custom agents.

    Returns:
        List of AgentConfig for each valid agent directory found.
    """
    agents_dir = get_paths().agents_dir

    if not agents_dir.exists():
        return []

    agents: list[AgentConfig] = []

    for entry in sorted(agents_dir.iterdir()):
        if not entry.is_dir():
            continue

        config_file = entry / "config.yaml"
        if not config_file.exists():
            logger.debug(f"Skipping {entry.name}: no config.yaml")
            continue

        try:
            agent_cfg = load_agent_config(entry.name)
            agents.append(agent_cfg)
        except Exception as e:
            logger.warning(f"Skipping agent '{entry.name}': {e}")

    return agents
