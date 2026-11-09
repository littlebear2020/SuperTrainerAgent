import logging
import os
import re
import secrets
import shlex
from datetime import UTC, datetime
from pathlib import Path

from langchain.tools import ToolRuntime, tool
from langgraph.errors import GraphBubbleUp
from langgraph.typing import ContextT

from deerflow.agents.thread_state import ThreadDataState, ThreadState
from deerflow.config import get_app_config
from deerflow.config.paths import VIRTUAL_PATH_PREFIX
from deerflow.sandbox.exceptions import (
    SandboxError,
    SandboxNotFoundError,
    SandboxRuntimeError,
)
from deerflow.sandbox.failure_guidance import append_failure_guidance
from deerflow.sandbox.file_operation_lock import get_file_operation_lock
from deerflow.sandbox.sandbox import Sandbox
from deerflow.sandbox.sandbox_provider import get_sandbox_provider
from deerflow.sandbox.search import GrepMatch
from deerflow.sandbox.security import LOCAL_HOST_BASH_DISABLED_MESSAGE, is_host_bash_allowed

logger = logging.getLogger(__name__)

_ABSOLUTE_PATH_PATTERN = re.compile(r"(?<![:\w])(?<!:/)/(?:[^\s\"'`;&|<>()]+)")
_FILE_URL_PATTERN = re.compile(r"\bfile://\S+", re.IGNORECASE)
_URL_WITH_SCHEME_PATTERN = re.compile(r"^[a-z][a-z0-9+.-]*://", re.IGNORECASE)
_URL_IN_COMMAND_PATTERN = re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s\"'`;&|<>()]+", re.IGNORECASE)
_DOTDOT_PATH_SEGMENT_PATTERN = re.compile(r"(?:^|[/\\=])\.\.(?:$|[/\\])")
_LOCAL_BASH_SYSTEM_PATH_PREFIXES = (
    "/bin/",
    "/usr/bin/",
    "/usr/sbin/",
    "/sbin/",
    "/opt/homebrew/bin/",
    "/dev/",
)

_DEFAULT_SKILLS_CONTAINER_PATH = "/mnt/skills"
_ACP_WORKSPACE_VIRTUAL_PATH = "/mnt/acp-workspace"
_DEFAULT_GLOB_MAX_RESULTS = 200
_MAX_GLOB_MAX_RESULTS = 1000
_DEFAULT_GREP_MAX_RESULTS = 100
_MAX_GREP_MAX_RESULTS = 500

# Maximum bytes accepted in a single non-append write_file call.
# Oversized single-shot writes correlate with LLM streaming chunk-gap timeouts
# because the tool-call JSON payload (which the model must emit as one
# continuous stream) grows past the safe window, so the run stalls until the
# timeout fires instead of failing fast. 80 KB is roughly 20K tokens.
# Deployments can override via env var DEERFLOW_WRITE_FILE_MAX_BYTES; set to
# 0 (or negative) to disable the guard entirely.
_WRITE_FILE_CONTENT_MAX_BYTES = 80 * 1024
_WRITE_FILE_MAX_BYTES_ENV = "DEERFLOW_WRITE_FILE_MAX_BYTES"
_LOCAL_BASH_CWD_COMMANDS = {"cd", "pushd"}
_LOCAL_BASH_COMMAND_WRAPPERS = {"command", "builtin"}
_LOCAL_BASH_COMMAND_PREFIX_KEYWORDS = {"!", "{", "case", "do", "elif", "else", "for", "if", "select", "then", "time", "until", "while"}
_LOCAL_BASH_COMMAND_END_KEYWORDS = {"}", "done", "esac", "fi"}
_LOCAL_BASH_ROOT_PATH_COMMANDS = {
    "awk",
    "cat",
    "cp",
    "du",
    "find",
    "grep",
    "head",
    "less",
    "ln",
    "ls",
    "more",
    "mv",
    "rm",
    "sed",
    "tail",
    "tar",
}
_SHELL_COMMAND_SEPARATORS = {";", "&&", "||", "|", "|&", "&", "(", ")"}
_SHELL_REDIRECTION_OPERATORS = {
    "<",
    ">",
    "<<",
    ">>",
    "<<<",
    "<>",
    ">&",
    "<&",
    "&>",
    "&>>",
    ">|",
}


def _get_skills_container_path() -> str:
    """Get the skills container path from config, with fallback to default.

    Result is cached after the first successful config load.  If config loading
    fails the default is returned *without* caching so that a later call can
    pick up the real value once the config is available.
    """
    cached = getattr(_get_skills_container_path, "_cached", None)
    if cached is not None:
        return cached
    try:
        from deerflow.config import get_app_config

        value = get_app_config().skills.container_path
        _get_skills_container_path._cached = value  # type: ignore[attr-defined]
        return value
    except Exception:
        return _DEFAULT_SKILLS_CONTAINER_PATH


def _get_skills_host_path() -> str | None:
    """Get the skills host filesystem path from config.

    Returns None if the skills directory does not exist or config cannot be
    loaded.  Only successful lookups are cached; failures are retried on the
    next call so that a transiently unavailable skills directory does not
    permanently disable skills access.
    """
    cached = getattr(_get_skills_host_path, "_cached", None)
    if cached is not None:
        return cached
    try:
        from deerflow.config import get_app_config

        config = get_app_config()
        skills_path = config.skills.get_skills_path()
        if skills_path.exists():
            value = str(skills_path)
            _get_skills_host_path._cached = value  # type: ignore[attr-defined]
            return value
    except Exception:
        pass
    return None


def _is_skills_path(path: str) -> bool:
    """Check if a path is under the skills container path."""
    skills_prefix = _get_skills_container_path()
    return path == skills_prefix or path.startswith(f"{skills_prefix}/")


def _extract_skill_name_from_skills_path(path: str) -> str | None:
    """Extract a skill name from a virtual skills path.

    P1-S3（2026-09-30 上游移植）。

    /mnt/skills/public/bootstrap/SKILL.md → "bootstrap"
    /mnt/skills/custom/my-skill/SKILL.md → "my-skill"
    /mnt/skills/legacy/my-skill/references/... → "my-skill"
    /mnt/skills/integrations/lark-cli/lark-doc/SKILL.md → "lark-doc"
    /mnt/skills/public/bootstrap/ → "bootstrap"
    Returns None if the path doesn't contain a recognizable skill name pattern.
    """
    skills_prefix = _get_skills_container_path()
    if not _is_skills_path(path):
        return None
    # Strip the skills prefix, e.g. "/mnt/skills/"
    relative = path[len(skills_prefix) :].lstrip("/")
    if not relative:
        return None
    # Expected patterns: "public/<name>/...", "custom/<name>/...",
    # "legacy/<name>/...", "integrations/<provider>/<name>/..."
    # or "<name>/..." (direct skill access). Empty segments are dropped so a
    # directory entry ("public/", as `ls` emits for dirs) is still recognized as
    # a category root rather than yielding an empty skill name.
    parts = [part for part in relative.split("/") if part]
    if len(parts) >= 2 and parts[0] in ("public", "custom", "legacy"):
        return parts[1]
    if len(parts) >= 3 and parts[0] == "integrations":
        return parts[2]
    if len(parts) == 1 and parts[0] in ("public", "custom", "legacy", "integrations"):
        # Category root like /mnt/skills/custom — not a skill path.
        return None
    if len(parts) == 2 and parts[0] == "integrations":
        # Provider root like /mnt/skills/integrations/lark-cli.
        return None
    if len(parts) >= 1:
        # Direct path like /mnt/skills/my-skill/SKILL.md
        return parts[0]
    return None


def _is_disabled_skill_path(path: str, *, user_id: str | None = None) -> bool:
    """Check if a path belongs to a disabled skill.

    P1-S3（2026-09-30 上游移植）。PUBLIC skill enabled state is read from the
    global ``extensions_config.json``.  CUSTOM / LEGACY skill enabled state is
    read from the per-user ``_skill_states.json`` so that two users with
    same-named custom skills can toggle independently.

    Returns False for non-skills paths or paths whose skill is enabled.
    """
    import json as _json

    skill_name = _extract_skill_name_from_skills_path(path)
    if skill_name is None:
        return False
    try:
        from deerflow.runtime.user_context import get_effective_user_id
        from deerflow.skills.storage import get_or_new_user_skill_storage

        # Determine the category from the path
        skills_prefix = _get_skills_container_path()
        relative = path[len(skills_prefix) :].lstrip("/")
        if relative.startswith("public/"):
            category = "public"
        elif relative.startswith("custom/"):
            category = "custom"
        elif relative.startswith("legacy/"):
            category = "legacy"
        elif relative.startswith("integrations/"):
            category = "integrations"
        else:
            # Try to infer from storage
            effective_uid = user_id or get_effective_user_id()
            storage = get_or_new_user_skill_storage(effective_uid)
            all_skills = storage.load_skills(enabled_only=False)
            matching = next((s for s in all_skills if s.name == skill_name), None)
            if matching is None:
                return False  # Skill doesn't exist, not a disabled skill path
            category = matching.category.value

        if category == "public":
            from deerflow.config.extensions_config import ExtensionsConfig

            ext_config = ExtensionsConfig.from_file()
            return not ext_config.is_skill_enabled(skill_name, category)
        else:
            # CUSTOM / LEGACY: use per-user state
            effective_uid = user_id or get_effective_user_id()
            storage = get_or_new_user_skill_storage(effective_uid)
            return not storage.get_skill_enabled_state(skill_name)
    except (OSError, ValueError, KeyError, _json.JSONDecodeError) as exc:
        # Access-control check must fail closed: when we can't determine the
        # enabled state (corrupt _skill_states.json, mid-write race, missing
        # config), refuse access rather than silently serving a disabled
        # skill's files. See review feedback on PR #3889.
        logger.warning("Failed to determine enabled state, denying access: %s", exc)
        return True


def _resolve_and_contain(virtual: str, host_root: str, joined: str) -> str:
    """B7（§4.4，2026-09-30 上游移植）：resolve-then-contain。

    虚拟→宿主拼接后必须 resolve（解析符号链接）并验证包含在根内——
    字符串级 commonpath 挡不住「技能/ACP 目录内符号链接指向外部」的
    逃逸。Windows 风格宿主路径沿用上层分支处理，本闸只管 POSIX 风格。
    评审二轮标注（2026-09-30）：Windows 宿主下本闸**不防** symlink 逃逸——
    部署面=Linux 容器、开发面=macOS，均 POSIX；Windows 仅为本地开发兜底
    路径（B7 闸不构成其安全边界）。
    """
    if "/" not in host_root or "\\" in host_root:
        return joined
    resolved_root = Path(host_root).resolve()
    resolved = Path(joined).resolve()
    try:
        resolved.relative_to(resolved_root)
    except ValueError:
        raise PermissionError(f"Access denied: path traversal detected (symlink escape): {virtual}") from None
    # 返回保持拼接串（macOS 合成链接 /home→/System/Volumes/Data/home 会改写字符串，
    # 破坏既有调用方的等值断言）；逃逸判定已完成，open() 走真实路径无碍。
    return joined


def _resolve_skills_path(path: str) -> str:
    """Resolve a virtual skills path to a host filesystem path.

    Args:
        path: Virtual skills path (e.g. /mnt/skills/public/bootstrap/SKILL.md)

    Returns:
        Resolved host path.

    Raises:
        FileNotFoundError: If skills directory is not configured or doesn't exist.
    """
    skills_container = _get_skills_container_path()
    skills_host = _get_skills_host_path()
    if skills_host is None:
        raise FileNotFoundError(f"Skills directory not available for path: {path}")

    if path == skills_container:
        return skills_host

    relative = path[len(skills_container) :].lstrip("/")
    joined = _join_path_preserving_style(skills_host, relative)
    # B7（§4.4）：resolve-then-contain——技能树内符号链接指向外部即拒止。
    return _resolve_and_contain(path, skills_host, joined)


def _is_acp_workspace_path(path: str) -> bool:
    """Check if a path is under the ACP workspace virtual path."""
    return path == _ACP_WORKSPACE_VIRTUAL_PATH or path.startswith(f"{_ACP_WORKSPACE_VIRTUAL_PATH}/")


def _get_custom_mounts():
    """Get custom volume mounts from sandbox config.

    Result is cached after the first successful config load.  If config loading
    fails an empty list is returned *without* caching so that a later call can
    pick up the real value once the config is available.
    """
    cached = getattr(_get_custom_mounts, "_cached", None)
    if cached is not None:
        return cached
    try:
        from pathlib import Path

        from deerflow.config import get_app_config

        config = get_app_config()
        mounts = []
        if config.sandbox and config.sandbox.mounts:
            # Only include mounts whose host_path exists, consistent with
            # LocalSandboxProvider._setup_path_mappings() which also filters
            # by host_path.exists().
            mounts = [m for m in config.sandbox.mounts if Path(m.host_path).exists()]
        _get_custom_mounts._cached = mounts  # type: ignore[attr-defined]
        return mounts
    except Exception:
        # If config loading fails, return an empty list without caching so that
        # a later call can retry once the config is available.
        return []


def _is_custom_mount_path(path: str) -> bool:
    """Check if path is under a custom mount container_path."""
    for mount in _get_custom_mounts():
        if path == mount.container_path or path.startswith(f"{mount.container_path}/"):
            return True
    return False


def _get_custom_mount_for_path(path: str):
    """Get the mount config matching this path (longest prefix first)."""
    best = None
    for mount in _get_custom_mounts():
        if path == mount.container_path or path.startswith(f"{mount.container_path}/"):
            if best is None or len(mount.container_path) > len(best.container_path):
                best = mount
    return best


def _extract_thread_id_from_thread_data(thread_data: "ThreadDataState | None") -> str | None:
    """Extract thread_id from thread_data by inspecting workspace_path.

    The workspace_path has the form
    ``{base_dir}/threads/{thread_id}/user-data/workspace``, so
    ``Path(workspace_path).parent.parent.name`` yields the thread_id.
    """
    if thread_data is None:
        return None
    workspace_path = thread_data.get("workspace_path")
    if not workspace_path:
        return None
    try:
        # {base_dir}/threads/{thread_id}/user-data/workspace → parent.parent = threads/{thread_id}
        return Path(workspace_path).parent.parent.name
    except Exception:
        return None


def _get_acp_workspace_host_path(thread_id: str | None = None) -> str | None:
    """Get the ACP workspace host filesystem path.

    When *thread_id* is provided, returns the per-thread workspace
    ``{base_dir}/threads/{thread_id}/acp-workspace/`` (not cached — the
    directory is created on demand by ``invoke_acp_agent_tool``).

    Falls back to the global ``{base_dir}/acp-workspace/`` when *thread_id*
    is ``None``; that result is cached after the first successful resolution.
    Returns ``None`` if the directory does not exist.
    """
    if thread_id is not None:
        try:
            from deerflow.config.paths import get_paths
            from deerflow.runtime.user_context import get_effective_user_id

            host_path = get_paths().acp_workspace_dir(thread_id, user_id=get_effective_user_id())
            if host_path.exists():
                return str(host_path)
        except Exception:
            pass
        return None

    cached = getattr(_get_acp_workspace_host_path, "_cached", None)
    if cached is not None:
        return cached
    try:
        from deerflow.config.paths import get_paths

        host_path = get_paths().base_dir / "acp-workspace"
        if host_path.exists():
            value = str(host_path)
            _get_acp_workspace_host_path._cached = value  # type: ignore[attr-defined]
            return value
    except Exception:
        pass
    return None


def _resolve_acp_workspace_path(path: str, thread_id: str | None = None) -> str:
    """Resolve a virtual ACP workspace path to a host filesystem path.

    Args:
        path: Virtual path (e.g. /mnt/acp-workspace/hello_world.py)
        thread_id: Current thread ID for per-thread workspace resolution.
                   When ``None``, falls back to the global workspace.

    Returns:
        Resolved host path.

    Raises:
        FileNotFoundError: If ACP workspace directory does not exist.
        PermissionError: If path traversal is detected.
    """
    _reject_path_traversal(path)

    host_path = _get_acp_workspace_host_path(thread_id)
    if host_path is None:
        raise FileNotFoundError(f"ACP workspace directory not available for path: {path}")

    if path == _ACP_WORKSPACE_VIRTUAL_PATH:
        return host_path

    relative = path[len(_ACP_WORKSPACE_VIRTUAL_PATH) :].lstrip("/")
    resolved = _join_path_preserving_style(host_path, relative)

    if "/" in host_path and "\\" not in host_path:
        # B7（§4.4）：resolve-then-contain 取代字符串级 commonpath——解析符号链接后
        # 再验证包含，目录内链接指向外部即拒止。
        return _resolve_and_contain(path, host_path, resolved)

    resolved_path = Path(resolved).resolve()
    try:
        resolved_path.relative_to(Path(host_path).resolve())
    except ValueError:
        raise PermissionError("Access denied: path traversal detected")

    return str(resolved_path)


def _get_mcp_allowed_paths() -> list[str]:
    """Get the list of allowed paths from MCP config for file system server."""
    allowed_paths = []
    try:
        from deerflow.config.extensions_config import get_extensions_config

        extensions_config = get_extensions_config()

        for _, server in extensions_config.mcp_servers.items():
            if not server.enabled:
                continue

            # Only check the filesystem server
            args = server.args or []
            # Check if args has server-filesystem package
            has_filesystem = any("server-filesystem" in arg for arg in args)
            if not has_filesystem:
                continue
            # Unpack the allowed file system paths in config
            for arg in args:
                if not arg.startswith("-") and arg.startswith("/"):
                    allowed_paths.append(arg.rstrip("/") + "/")

    except Exception:
        pass

    return allowed_paths


def _get_tool_config_int(name: str, key: str, default: int) -> int:
    try:
        tool_config = get_app_config().get_tool_config(name)
        if tool_config is not None and key in tool_config.model_extra:
            value = tool_config.model_extra.get(key)
            if isinstance(value, int):
                return value
    except Exception:
        pass
    return default


def _clamp_max_results(value: int, *, default: int, upper_bound: int) -> int:
    if value <= 0:
        return default
    return min(value, upper_bound)


def _resolve_max_results(name: str, requested: int, *, default: int, upper_bound: int) -> int:
    requested_max_results = _clamp_max_results(requested, default=default, upper_bound=upper_bound)
    configured_max_results = _clamp_max_results(
        _get_tool_config_int(name, "max_results", default),
        default=default,
        upper_bound=upper_bound,
    )
    return min(requested_max_results, configured_max_results)


def _resolve_local_read_path(path: str, thread_data: ThreadDataState) -> str:
    validate_local_tool_path(path, thread_data, read_only=True)
    if _is_skills_path(path):
        return _resolve_skills_path(path)
    if _is_acp_workspace_path(path):
        return _resolve_acp_workspace_path(path, _extract_thread_id_from_thread_data(thread_data))
    # B11-a（§4.4）：custom mount 分支（对齐 ls/read_file 形态）——修前
    # glob/grep 走到 user-data 验证直接 PermissionError。
    if _is_custom_mount_path(path):
        mount = _get_custom_mount_for_path(path)
        relative = path[len(mount.container_path) :].lstrip("/")
        joined = _join_path_preserving_style(str(mount.host_path), relative)
        return _resolve_and_contain(path, str(mount.host_path), joined)
    return _resolve_and_validate_user_data_path(path, thread_data)


def _format_glob_results(root_path: str, matches: list[str], truncated: bool) -> str:
    if not matches:
        return f"No files matched under {root_path}"

    lines = [f"Found {len(matches)} paths under {root_path}"]
    if truncated:
        lines[0] += f" (showing first {len(matches)})"
    lines.extend(f"{index}. {path}" for index, path in enumerate(matches, start=1))
    if truncated:
        lines.append("Results truncated. Narrow the path or pattern to see fewer matches.")
    return "\n".join(lines)


def _format_grep_results(root_path: str, matches: list[GrepMatch], truncated: bool) -> str:
    if not matches:
        return f"No matches found under {root_path}"

    lines = [f"Found {len(matches)} matches under {root_path}"]
    if truncated:
        lines[0] += f" (showing first {len(matches)})"
    lines.extend(f"{match.path}:{match.line_number}: {match.line}" for match in matches)
    if truncated:
        lines.append("Results truncated. Narrow the path or add a glob filter.")
    return "\n".join(lines)


def _path_variants(path: str) -> set[str]:
    return {path, path.replace("\\", "/"), path.replace("/", "\\")}


def _path_separator_for_style(path: str) -> str:
    return "\\" if "\\" in path and "/" not in path else "/"


def _join_path_preserving_style(base: str, relative: str) -> str:
    if not relative:
        return base
    separator = _path_separator_for_style(base)
    normalized_relative = relative.replace("\\" if separator == "/" else "/", separator).lstrip("/\\")
    stripped_base = base.rstrip("/\\")
    return f"{stripped_base}{separator}{normalized_relative}"


def _sanitize_error(error: Exception, runtime: "ToolRuntime[ContextT, ThreadState] | None" = None) -> str:
    """Sanitize an error message to avoid leaking host filesystem paths.

    In local-sandbox mode, resolved host paths in the error string are masked
    back to their virtual equivalents so that user-visible output never exposes
    the host directory layout.

    The sandbox SDK's ``ApiError`` is special-cased: its ``__str__`` dumps
    response headers (including ``server: nginx/...``), which made incident
    reports misattribute sandbox-internal failures to external services. It is
    matched by type name + module so the SDK is never imported here. Note the
    SDK raises it from non-file APIs too (e.g. execute_command), so the wording
    stays generic ("沙箱服务").
    """
    if type(error).__name__ == "ApiError" and "agent_sandbox" in type(error).__module__:
        msg = f"SandboxServiceError: 沙箱服务暂时不可用（可重试）。原始信息: {type(error).__name__}: {error}"
    else:
        msg = f"{type(error).__name__}: {error}"
    if runtime is not None and is_local_sandbox(runtime):
        thread_data = get_thread_data(runtime)
        msg = mask_local_paths_in_output(msg, thread_data)
    return msg


def replace_virtual_path(path: str, thread_data: ThreadDataState | None) -> str:
    """Replace virtual /mnt/user-data paths with actual thread data paths.

    Mapping:
        /mnt/user-data/workspace/* -> thread_data['workspace_path']/*
        /mnt/user-data/uploads/* -> thread_data['uploads_path']/*
        /mnt/user-data/outputs/* -> thread_data['outputs_path']/*

    Args:
        path: The path that may contain virtual path prefix.
        thread_data: The thread data containing actual paths.

    Returns:
        The path with virtual prefix replaced by actual path.
    """
    if thread_data is None:
        return path

    mappings = _thread_virtual_to_actual_mappings(thread_data)
    if not mappings:
        return path

    # Longest-prefix-first replacement with segment-boundary checks.
    for virtual_base, actual_base in sorted(mappings.items(), key=lambda item: len(item[0]), reverse=True):
        if path == virtual_base:
            return actual_base
        if path.startswith(f"{virtual_base}/"):
            rest = path[len(virtual_base) :].lstrip("/")
            result = _join_path_preserving_style(actual_base, rest)
            if path.endswith("/") and not result.endswith(("/", "\\")):
                result += _path_separator_for_style(actual_base)
            return result

    return path


def _thread_virtual_to_actual_mappings(thread_data: ThreadDataState) -> dict[str, str]:
    """Build virtual-to-actual path mappings for a thread."""
    mappings: dict[str, str] = {}

    workspace = thread_data.get("workspace_path")
    uploads = thread_data.get("uploads_path")
    outputs = thread_data.get("outputs_path")

    if workspace:
        mappings[f"{VIRTUAL_PATH_PREFIX}/workspace"] = workspace
    if uploads:
        mappings[f"{VIRTUAL_PATH_PREFIX}/uploads"] = uploads
    if outputs:
        mappings[f"{VIRTUAL_PATH_PREFIX}/outputs"] = outputs

    # Also map the virtual root when all known dirs share the same parent.
    actual_dirs = [Path(p) for p in (workspace, uploads, outputs) if p]
    if actual_dirs:
        common_parent = str(Path(actual_dirs[0]).parent)
        if all(str(path.parent) == common_parent for path in actual_dirs):
            mappings[VIRTUAL_PATH_PREFIX] = common_parent

    return mappings


def _thread_actual_to_virtual_mappings(thread_data: ThreadDataState) -> dict[str, str]:
    """Build actual-to-virtual mappings for output masking."""
    return {actual: virtual for virtual, actual in _thread_virtual_to_actual_mappings(thread_data).items()}


_SECRET_VALUE_MASK = "***"


def _sandbox_secret_values() -> list[str]:
    """SEC-C4① (批次 2 项 2.13): resolve the sandbox.environment injection set.

    Values referenced as ``$VAR`` resolve against the host process env (the
    same resolution aio_sandbox_provider performs when injecting); literal
    values pass through. Unresolvable references are skipped.
    """
    values: list[str] = []
    try:
        env_cfg = getattr(get_app_config().sandbox, "environment", None) or {}
    except Exception:
        return values
    for raw in env_cfg.values():
        if not isinstance(raw, str) or not raw:
            continue
        if raw.startswith("$"):
            resolved = os.environ.get(raw[1:])
            if resolved:
                values.append(resolved)
        else:
            values.append(raw)
    return values


# ── 评审二轮修复（2026-09-30，H1）：required-secrets 注入装配端 ─────────────
#
# 上游链路 = skill_activation_middleware（把 request secrets 解析成
# ``__active_skill_secrets``）→ bash 工具 env 注入。middleware 属 P2/P3
# 移植面；fork 简化装配读两源：① run context 的 active/request secrets
# （上游原生，middleware 接通后即刻生效）；② AUTOBIND=1 时按启用技能声明
# 并集从网关进程 env 代填（部署侧显式开启的受控通道——范围=声明键白名单，
# per-call 注入不驻留容器 env；开启即意味着 agent 可经 echo 读到，与 P1-S2
# 前"全平台密钥进容器 env"相比是范围收窄+瞬时化，不是对 agent 保密）。

_AUTOBIND_ENV_FLAG = "DEER_FLOW_SKILL_SECRET_AUTOBIND"
_DECLARED_NAMES_CACHE_TTL_SECONDS = 60.0
_declared_names_cache: tuple[float, set[str]] | None = None


def _ttl_cached_declared_names(compute) -> set[str]:
    """声明并集 TTL 缓存——安装/启停运行期少见，60s 窗口足够新鲜且免每次 bash 扫盘。"""
    global _declared_names_cache
    import time as _time

    now = _time.monotonic()
    if _declared_names_cache is not None and (now - _declared_names_cache[0]) < _DECLARED_NAMES_CACHE_TTL_SECONDS:
        return _declared_names_cache[1]
    names = compute()
    _declared_names_cache = (now, names)
    return names


def _declared_skill_secret_names() -> set[str]:
    """启用技能 required-secrets 声明并集（经 TTL 缓存）。"""

    def _compute() -> set[str]:
        try:
            from deerflow.skills import load_skills

            names: set[str] = set()
            for skill in load_skills(enabled_only=True):
                for req in getattr(skill, "required_secrets", ()) or ():
                    names.add(req.name)
            return names
        except Exception:
            logger.warning("Failed to collect declared required-secrets; skipping autobind", exc_info=True)
            return set()

    return _ttl_cached_declared_names(_compute)


def _autobind_skill_secrets() -> dict[str, str] | None:
    """AUTOBIND 通道：声明并集 ∩ 网关 env（有值者）。默认关。"""
    import os as _os

    if _os.environ.get(_AUTOBIND_ENV_FLAG, "").strip().lower() not in {"1", "true", "yes", "on"}:
        return None
    declared = _declared_skill_secret_names()
    if not declared:
        return None
    from deerflow.sandbox.env_policy import is_blocked_env_name

    injected: dict[str, str] = {}
    for name in sorted(declared):
        value = _os.environ.get(name)
        if not value:
            continue
        if is_blocked_env_name(name):
            # 评审 #7：注入名本身命中封锁名单（如技能声明了 DATABASE_URL）——
            # 留审计痕；仍注入（required-secrets 语义即"技能确需此键"）。
            logger.warning("required-secrets 注入键 %r 命中 env_policy 封锁名单（声明即确需，注入并留痕）", name)
        injected[name] = value
    return injected or None


def _resolve_injected_secrets(runtime) -> dict[str, str] | None:
    """bash 工具注入解析：active secrets → request secrets → AUTOBIND（都空则 None）。"""
    try:
        from deerflow.runtime.secret_context import read_active_secrets

        active = read_active_secrets(getattr(runtime, "context", None))
        if active:
            return active
        request = None
        ctx = getattr(runtime, "context", None)
        if isinstance(ctx, dict):
            from deerflow.runtime.secret_context import extract_request_secrets

            request = extract_request_secrets(ctx)
        if request:
            return request
        return _autobind_skill_secrets()
    except Exception:
        logger.warning("Failed to resolve injected secrets; executing without env injection", exc_info=True)
        return None


def mask_secret_values_in_output(output: str, injected_values: list[str] | None = None) -> str:
    """SEC-C4① (批次 2 项 2.13): mask injected secret VALUES in bash output.

    An ``env``/echo/log dump from the sandbox would otherwise leak the injected
    key material verbatim into the conversation. Applied after (and alongside)
    the existing path masking. GITHUB_TOKEN / QIHU_TTS voice-id keys are no
    longer injected at all (removed from config.yaml); this masks what remains.

    评审二轮修复（H1 配套，对齐上游 mask_secret_values）：``injected_values``
    携带本次 bash 调用注入的 required-secrets 值清单——注入通道打开后，
    ``echo $KEY`` 的回显必须同样被 mask，否则注入即输出泄露面。短值
    （<6 字符）不 mask，防误伤。
    """
    for value in [*(injected_values or []), *_sandbox_secret_values()]:
        if len(value) >= 6:
            output = output.replace(value, _SECRET_VALUE_MASK)
    return output


def mask_local_paths_in_output(output: str, thread_data: ThreadDataState | None) -> str:
    """Mask host absolute paths from local sandbox output using virtual paths.

    Handles user-data paths (per-thread), skills paths, and ACP workspace paths (global).
    """
    result = output

    # Mask skills host paths
    skills_host = _get_skills_host_path()
    skills_container = _get_skills_container_path()
    if skills_host:
        raw_base = str(Path(skills_host))
        resolved_base = str(Path(skills_host).resolve())
        for base in _path_variants(raw_base) | _path_variants(resolved_base):
            escaped = re.escape(base).replace(r"\\", r"[/\\]")
            pattern = re.compile(escaped + r"(?:[/\\][^\s\"';&|<>()]*)?")

            def replace_skills(match: re.Match, _base: str = base) -> str:
                matched_path = match.group(0)
                if matched_path == _base:
                    return skills_container
                relative = matched_path[len(_base) :].lstrip("/\\")
                return f"{skills_container}/{relative}" if relative else skills_container

            result = pattern.sub(replace_skills, result)

    # Mask ACP workspace host paths
    _thread_id = _extract_thread_id_from_thread_data(thread_data)
    acp_host = _get_acp_workspace_host_path(_thread_id)
    if acp_host:
        raw_base = str(Path(acp_host))
        resolved_base = str(Path(acp_host).resolve())
        for base in _path_variants(raw_base) | _path_variants(resolved_base):
            escaped = re.escape(base).replace(r"\\", r"[/\\]")
            pattern = re.compile(escaped + r"(?:[/\\][^\s\"';&|<>()]*)?")

            def replace_acp(match: re.Match, _base: str = base) -> str:
                matched_path = match.group(0)
                if matched_path == _base:
                    return _ACP_WORKSPACE_VIRTUAL_PATH
                relative = matched_path[len(_base) :].lstrip("/\\")
                return f"{_ACP_WORKSPACE_VIRTUAL_PATH}/{relative}" if relative else _ACP_WORKSPACE_VIRTUAL_PATH

            result = pattern.sub(replace_acp, result)

    # Custom mount host paths are masked by LocalSandbox._reverse_resolve_paths_in_output()

    # Mask user-data host paths
    if thread_data is None:
        return result

    mappings = _thread_actual_to_virtual_mappings(thread_data)
    if not mappings:
        return result

    for actual_base, virtual_base in sorted(mappings.items(), key=lambda item: len(item[0]), reverse=True):
        raw_base = str(Path(actual_base))
        resolved_base = str(Path(actual_base).resolve())
        for base in _path_variants(raw_base) | _path_variants(resolved_base):
            escaped_actual = re.escape(base).replace(r"\\", r"[/\\]")
            pattern = re.compile(escaped_actual + r"(?:[/\\][^\s\"';&|<>()]*)?")

            def replace_match(match: re.Match, _base: str = base, _virtual: str = virtual_base) -> str:
                matched_path = match.group(0)
                if matched_path == _base:
                    return _virtual
                relative = matched_path[len(_base) :].lstrip("/\\")
                return f"{_virtual}/{relative}" if relative else _virtual

            result = pattern.sub(replace_match, result)

    return result


def _reject_path_traversal(path: str) -> None:
    """Reject paths that contain '..' segments to prevent directory traversal."""
    # Normalise to forward slashes, then check for '..' segments.
    normalised = path.replace("\\", "/")
    for segment in normalised.split("/"):
        if segment == "..":
            raise PermissionError("Access denied: path traversal detected")


def validate_local_tool_path(path: str, thread_data: ThreadDataState | None, *, read_only: bool = False) -> None:
    """Validate that a virtual path is allowed for local-sandbox access.

    This function is a security gate — it checks whether *path* may be
    accessed and raises on violation.  It does **not** resolve the virtual
    path to a host path; callers are responsible for resolution via
    ``resolve_and_validate_user_data_path`` or ``_resolve_skills_path``.

    Allowed virtual-path families:
      - ``/mnt/user-data/*``  — always allowed (read + write)
      - ``/mnt/skills/*``     — allowed only when *read_only* is True
      - ``/mnt/acp-workspace/*`` — allowed only when *read_only* is True
      - Custom mount paths (from config.yaml) — respects per-mount ``read_only`` flag

    Args:
        path: The virtual path to validate.
        thread_data: Thread data (must be present for local sandbox).
        read_only: When True, skills and ACP workspace paths are permitted.

    Raises:
        SandboxRuntimeError: If thread data is missing.
        PermissionError: If the path is not allowed or contains traversal.
    """
    if thread_data is None:
        raise SandboxRuntimeError("Thread data not available for local sandbox")

    _reject_path_traversal(path)

    # Skills paths — read-only access only
    if _is_skills_path(path):
        if not read_only:
            raise PermissionError(f"Write access to skills path is not allowed: {path}")
        return

    # ACP workspace paths — read-only access only
    if _is_acp_workspace_path(path):
        if not read_only:
            raise PermissionError(f"Write access to ACP workspace is not allowed: {path}")
        return

    # User-data paths
    if path.startswith(f"{VIRTUAL_PATH_PREFIX}/"):
        return

    # Custom mount paths — respect read_only config
    if _is_custom_mount_path(path):
        mount = _get_custom_mount_for_path(path)
        if mount and mount.read_only and not read_only:
            raise PermissionError(f"Write access to read-only mount is not allowed: {path}")
        return

    raise PermissionError(f"Only paths under {VIRTUAL_PATH_PREFIX}/, {_get_skills_container_path()}/, {_ACP_WORKSPACE_VIRTUAL_PATH}/, or configured mount paths are allowed")


def _validate_resolved_user_data_path(resolved: Path, thread_data: ThreadDataState) -> None:
    """Verify that a resolved host path stays inside allowed per-thread roots.

    Raises PermissionError if the path escapes workspace/uploads/outputs.
    """
    allowed_roots = [
        Path(p).resolve()
        for p in (
            thread_data.get("workspace_path"),
            thread_data.get("uploads_path"),
            thread_data.get("outputs_path"),
        )
        if p is not None
    ]

    if not allowed_roots:
        raise SandboxRuntimeError("No allowed local sandbox directories configured")

    for root in allowed_roots:
        try:
            resolved.relative_to(root)
            return
        except ValueError:
            continue

    raise PermissionError("Access denied: path traversal detected")


def _resolve_and_validate_user_data_path(path: str, thread_data: ThreadDataState) -> str:
    """Resolve a /mnt/user-data virtual path and validate it stays in bounds.

    Returns the resolved host path string.
    """
    resolved_str = replace_virtual_path(path, thread_data)
    resolved = Path(resolved_str).resolve()
    _validate_resolved_user_data_path(resolved, thread_data)
    return str(resolved)


def _is_non_file_url_token(token: str) -> bool:
    """Return True for URL tokens that should not be interpreted as paths."""
    values = [token]
    if "=" in token:
        values.append(token.split("=", 1)[1])

    for value in values:
        match = _URL_WITH_SCHEME_PATTERN.match(value)
        if match and not value.lower().startswith("file://"):
            return True
    return False


def _non_file_url_spans(command: str) -> list[tuple[int, int]]:
    spans = []
    for match in _URL_IN_COMMAND_PATTERN.finditer(command):
        if not match.group().lower().startswith("file://"):
            spans.append(match.span())
    return spans


def _is_in_spans(position: int, spans: list[tuple[int, int]]) -> bool:
    return any(start <= position < end for start, end in spans)


def _has_dotdot_path_segment(token: str) -> bool:
    if _is_non_file_url_token(token):
        return False
    return bool(_DOTDOT_PATH_SEGMENT_PATTERN.search(token))


def _split_shell_tokens(command: str) -> list[str]:
    try:
        normalized = command.replace("\r\n", "\n").replace("\r", "\n").replace("\n", " ; ")
        lexer = shlex.shlex(normalized, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        lexer.commenters = ""
        return list(lexer)
    except ValueError:
        # The shell will reject malformed quoting later; keep validation
        # best-effort instead of turning syntax errors into security messages.
        return command.split()


def _is_shell_command_separator(token: str) -> bool:
    return token in _SHELL_COMMAND_SEPARATORS


def _is_shell_redirection_operator(token: str) -> bool:
    return token in _SHELL_REDIRECTION_OPERATORS


def _is_shell_assignment(token: str) -> bool:
    name, separator, _ = token.partition("=")
    if not separator or not name:
        return False
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name))


def _is_allowed_local_bash_absolute_path(path: str, allowed_paths: list[str], *, allow_system_paths: bool) -> bool:
    # Check for MCP filesystem server allowed paths
    if any(path.startswith(allowed_path) or path == allowed_path.rstrip("/") for allowed_path in allowed_paths):
        _reject_path_traversal(path)
        return True

    if path == VIRTUAL_PATH_PREFIX or path.startswith(f"{VIRTUAL_PATH_PREFIX}/"):
        _reject_path_traversal(path)
        return True

    # Allow skills container path (resolved by tools.py before passing to sandbox)
    if _is_skills_path(path):
        _reject_path_traversal(path)
        return True

    # Allow ACP workspace path (path-traversal check only)
    if _is_acp_workspace_path(path):
        _reject_path_traversal(path)
        return True

    # Allow custom mount container paths
    if _is_custom_mount_path(path):
        _reject_path_traversal(path)
        return True

    if allow_system_paths and any(path == prefix.rstrip("/") or path.startswith(prefix) for prefix in _LOCAL_BASH_SYSTEM_PATH_PREFIXES):
        return True

    return False


def _next_cd_target(tokens: list[str], start_index: int) -> tuple[str | None, int]:
    index = start_index
    while index < len(tokens):
        token = tokens[index]
        if _is_shell_command_separator(token):
            return None, index
        if _is_shell_redirection_operator(token):
            index += 2
            continue
        if token == "--":
            index += 1
            continue
        if token in {"-L", "-P", "-e", "-@"}:
            index += 1
            continue
        if token.startswith("-") and token != "-":
            index += 1
            continue
        return token, index + 1
    return None, index


def _validate_local_bash_cwd_target(command_name: str, target: str | None, allowed_paths: list[str]) -> None:
    if target is None or target == "-":
        raise PermissionError(f"Unsafe working directory change in command: {command_name}. Use paths under {VIRTUAL_PATH_PREFIX}")
    if target.startswith(("$", "`")):
        raise PermissionError(f"Unsafe working directory change in command: {command_name} {target}. Use paths under {VIRTUAL_PATH_PREFIX}")
    if target.startswith("~"):
        raise PermissionError(f"Unsafe working directory change in command: {command_name} {target}. Use paths under {VIRTUAL_PATH_PREFIX}")
    if target.startswith("/"):
        _reject_path_traversal(target)
        if not _is_allowed_local_bash_absolute_path(target, allowed_paths, allow_system_paths=False):
            raise PermissionError(f"Unsafe working directory change in command: {command_name} {target}. Use paths under {VIRTUAL_PATH_PREFIX}")


def _looks_like_unsafe_cwd_target(target: str | None) -> bool:
    if target is None:
        return False
    return target == "-" or target.startswith(("$", "`", "~", "/", "..")) or _has_dotdot_path_segment(target)


def _validate_local_bash_root_path_args(command_name: str, tokens: list[str], start_index: int) -> None:
    if command_name not in _LOCAL_BASH_ROOT_PATH_COMMANDS:
        return

    index = start_index
    while index < len(tokens):
        token = tokens[index]
        if _is_shell_command_separator(token):
            return
        if _is_shell_redirection_operator(token):
            index += 2
            continue
        if token == "/" and not _is_non_file_url_token(token):
            raise PermissionError(f"Unsafe absolute paths in command: /. Use paths under {VIRTUAL_PATH_PREFIX}")
        index += 1


def _validate_local_bash_shell_tokens(command: str, allowed_paths: list[str]) -> None:
    """Conservatively reject relative path escapes missed by absolute-path scanning."""
    if re.search(r"\$\([^)]*\b(?:cd|pushd)\b", command):
        raise PermissionError(f"Unsafe working directory change in command substitution. Use paths under {VIRTUAL_PATH_PREFIX}")

    tokens = _split_shell_tokens(command)

    for token in tokens:
        if _is_shell_command_separator(token) or _is_shell_redirection_operator(token):
            continue
        if _has_dotdot_path_segment(token):
            raise PermissionError("Access denied: path traversal detected")

    at_command_start = True
    index = 0
    while index < len(tokens):
        token = tokens[index]

        if _is_shell_command_separator(token):
            at_command_start = True
            index += 1
            continue

        if _is_shell_redirection_operator(token):
            index += 1
            continue

        if at_command_start and _is_shell_assignment(token):
            index += 1
            continue

        command_name = token.rsplit("/", 1)[-1]
        if at_command_start and command_name in _LOCAL_BASH_COMMAND_PREFIX_KEYWORDS | _LOCAL_BASH_COMMAND_END_KEYWORDS:
            index += 1
            continue

        if not at_command_start:
            index += 1
            continue

        at_command_start = False
        if command_name in _LOCAL_BASH_COMMAND_WRAPPERS and index + 1 < len(tokens):
            wrapped_name = tokens[index + 1].rsplit("/", 1)[-1]
            if wrapped_name in _LOCAL_BASH_CWD_COMMANDS:
                target, next_index = _next_cd_target(tokens, index + 2)
                _validate_local_bash_cwd_target(wrapped_name, target, allowed_paths)
                index = next_index
                continue
            _validate_local_bash_root_path_args(wrapped_name, tokens, index + 2)

        if command_name not in _LOCAL_BASH_CWD_COMMANDS:
            _validate_local_bash_root_path_args(command_name, tokens, index + 1)
            index += 1
            continue

        target, next_index = _next_cd_target(tokens, index + 1)
        _validate_local_bash_cwd_target(command_name, target, allowed_paths)
        index = next_index


def resolve_and_validate_user_data_path(path: str, thread_data: ThreadDataState) -> str:
    """Resolve a /mnt/user-data virtual path and validate it stays in bounds."""
    return _resolve_and_validate_user_data_path(path, thread_data)


def validate_local_bash_command_paths(command: str, thread_data: ThreadDataState | None) -> None:
    """Validate absolute paths in local-sandbox bash commands.

    This validation is only a best-effort guard for the explicit
    ``sandbox.allow_host_bash: true`` opt-in. It is not a secure sandbox
    boundary and must not be treated as isolation from the host filesystem.

    In local mode, commands must use virtual paths under /mnt/user-data for
    user data access. Skills paths under /mnt/skills, ACP workspace paths
    under /mnt/acp-workspace, and custom mount container paths (configured in
    config.yaml) are allowed (path-traversal checks only; write prevention
    for bash commands is not enforced here).
    A small allowlist of common system path prefixes is kept for executable
    and device references (e.g. /bin/sh, /dev/null).
    """
    if thread_data is None:
        raise SandboxRuntimeError("Thread data not available for local sandbox")

    # Block file:// URLs which bypass the absolute-path regex but allow local file exfiltration
    file_url_match = _FILE_URL_PATTERN.search(command)
    if file_url_match:
        raise PermissionError(f"Unsafe file:// URL in command: {file_url_match.group()}. Use paths under {VIRTUAL_PATH_PREFIX}")

    unsafe_paths: list[str] = []
    allowed_paths = _get_mcp_allowed_paths()
    _validate_local_bash_shell_tokens(command, allowed_paths)
    url_spans = _non_file_url_spans(command)

    for match in _ABSOLUTE_PATH_PATTERN.finditer(command):
        if _is_in_spans(match.start(), url_spans):
            continue
        absolute_path = match.group()
        if _is_allowed_local_bash_absolute_path(absolute_path, allowed_paths, allow_system_paths=True):
            continue

        unsafe_paths.append(absolute_path)

    if unsafe_paths:
        unsafe = ", ".join(sorted(dict.fromkeys(unsafe_paths)))
        raise PermissionError(f"Unsafe absolute paths in command: {unsafe}. Use paths under {VIRTUAL_PATH_PREFIX}")


def replace_virtual_paths_in_command(command: str, thread_data: ThreadDataState | None) -> str:
    """Replace all virtual paths (/mnt/user-data, /mnt/skills, /mnt/acp-workspace) in a command string.

    Args:
        command: The command string that may contain virtual paths.
        thread_data: The thread data containing actual paths.

    Returns:
        The command with all virtual paths replaced.
    """
    result = command

    # Replace skills paths
    skills_container = _get_skills_container_path()
    skills_host = _get_skills_host_path()
    if skills_host and skills_container in result:
        skills_pattern = re.compile(rf"{re.escape(skills_container)}(/[^\s\"';&|<>()]*)?")

        def replace_skills_match(match: re.Match) -> str:
            return _resolve_skills_path(match.group(0))

        result = skills_pattern.sub(replace_skills_match, result)

    # Replace ACP workspace paths
    _thread_id = _extract_thread_id_from_thread_data(thread_data)
    acp_host = _get_acp_workspace_host_path(_thread_id)
    if acp_host and _ACP_WORKSPACE_VIRTUAL_PATH in result:
        acp_pattern = re.compile(rf"{re.escape(_ACP_WORKSPACE_VIRTUAL_PATH)}(/[^\s\"';&|<>()]*)?")

        def replace_acp_match(match: re.Match, _tid: str | None = _thread_id) -> str:
            return _resolve_acp_workspace_path(match.group(0), _tid)

        result = acp_pattern.sub(replace_acp_match, result)

    # Custom mount paths are resolved by LocalSandbox._resolve_paths_in_command()

    # Replace user-data paths
    if VIRTUAL_PATH_PREFIX in result and thread_data is not None:
        pattern = re.compile(rf"{re.escape(VIRTUAL_PATH_PREFIX)}(/[^\s\"';&|<>()]*)?")

        def replace_user_data_match(match: re.Match) -> str:
            return replace_virtual_path(match.group(0), thread_data)

        result = pattern.sub(replace_user_data_match, result)

    return result


def _apply_cwd_prefix(command: str, thread_data: ThreadDataState | None) -> str:
    """Prepend 'cd <workspace> &&' so relative paths are anchored to the thread workspace.

    Args:
        command: The bash command to execute.
        thread_data: The thread data containing the workspace path.

    Returns:
        The command prefixed with 'cd <workspace> &&' if workspace_path is available,
        otherwise the original command unchanged.
    """
    if thread_data and (workspace := thread_data.get("workspace_path")):
        return f"cd {shlex.quote(workspace)} && {command}"
    return command


def get_thread_data(runtime: ToolRuntime[ContextT, ThreadState] | None) -> ThreadDataState | None:
    """Extract thread_data from runtime state."""
    if runtime is None:
        return None
    if runtime.state is None:
        return None
    return runtime.state.get("thread_data")


def is_local_sandbox(runtime: ToolRuntime[ContextT, ThreadState] | None) -> bool:
    """Check if the current sandbox is a local sandbox.

    Accepts both the legacy generic id ``"local"`` (acquire with no thread
    context) and the per-thread id format ``"local:{thread_id}"`` produced by
    :meth:`LocalSandboxProvider.acquire` once a thread is known.
    """
    if runtime is None:
        return False
    if runtime.state is None:
        return False
    sandbox_state = runtime.state.get("sandbox")
    if sandbox_state is None:
        return False
    sandbox_id = sandbox_state.get("sandbox_id")
    if not isinstance(sandbox_id, str):
        return False
    return sandbox_id == "local" or sandbox_id.startswith("local:")


def sandbox_from_runtime(runtime: ToolRuntime[ContextT, ThreadState] | None = None) -> Sandbox:
    """Extract sandbox instance from tool runtime.

    DEPRECATED: Use ensure_sandbox_initialized() for lazy initialization support.
    This function assumes sandbox is already initialized and will raise error if not.

    Raises:
        SandboxRuntimeError: If runtime is not available or sandbox state is missing.
        SandboxNotFoundError: If sandbox with the given ID cannot be found.
    """
    if runtime is None:
        raise SandboxRuntimeError("Tool runtime not available")
    if runtime.state is None:
        raise SandboxRuntimeError("Tool runtime state not available")
    sandbox_state = runtime.state.get("sandbox")
    if sandbox_state is None:
        raise SandboxRuntimeError("Sandbox state not initialized in runtime")
    sandbox_id = sandbox_state.get("sandbox_id")
    if sandbox_id is None:
        raise SandboxRuntimeError("Sandbox ID not found in state")
    sandbox = get_sandbox_provider().get(sandbox_id)
    if sandbox is None:
        raise SandboxNotFoundError(f"Sandbox with ID '{sandbox_id}' not found", sandbox_id=sandbox_id)

    if runtime.context is not None:
        runtime.context["sandbox_id"] = sandbox_id  # Ensure sandbox_id is in context for downstream use
    return sandbox


def ensure_sandbox_initialized(runtime: ToolRuntime[ContextT, ThreadState] | None = None) -> Sandbox:
    """Ensure sandbox is initialized, acquiring lazily if needed.

    On first call, acquires a sandbox from the provider and stores it in runtime state.
    Subsequent calls return the existing sandbox.

    Thread-safety is guaranteed by the provider's internal locking mechanism.

    Args:
        runtime: Tool runtime containing state and context.

    Returns:
        Initialized sandbox instance.

    Raises:
        SandboxRuntimeError: If runtime is not available or thread_id is missing.
        SandboxNotFoundError: If sandbox acquisition fails.
    """
    if runtime is None:
        raise SandboxRuntimeError("Tool runtime not available")

    if runtime.state is None:
        raise SandboxRuntimeError("Tool runtime state not available")

    # Check if sandbox already exists in state
    sandbox_state = runtime.state.get("sandbox")
    if sandbox_state is not None:
        sandbox_id = sandbox_state.get("sandbox_id")
        if sandbox_id is not None:
            sandbox = get_sandbox_provider().get(sandbox_id)
            if sandbox is not None:
                if runtime.context is not None:
                    runtime.context["sandbox_id"] = sandbox_id  # Ensure sandbox_id is in context for releasing in after_agent
                return sandbox
            # Sandbox was released, fall through to acquire new one

    # Lazy acquisition: get thread_id and acquire sandbox
    thread_id = runtime.context.get("thread_id") if runtime.context else None
    if thread_id is None:
        thread_id = runtime.config.get("configurable", {}).get("thread_id") if runtime.config else None
    if thread_id is None:
        raise SandboxRuntimeError("Thread ID not available in runtime context")

    provider = get_sandbox_provider()
    sandbox_id = provider.acquire(thread_id)

    # Update runtime state - this persists across tool calls
    runtime.state["sandbox"] = {"sandbox_id": sandbox_id}

    # Retrieve and return the sandbox
    sandbox = provider.get(sandbox_id)
    if sandbox is None:
        raise SandboxNotFoundError("Sandbox not found after acquisition", sandbox_id=sandbox_id)

    if runtime.context is not None:
        runtime.context["sandbox_id"] = sandbox_id  # Ensure sandbox_id is in context for releasing in after_agent
    return sandbox


def ensure_thread_directories_exist(runtime: ToolRuntime[ContextT, ThreadState] | None) -> None:
    """Ensure thread data directories (workspace, uploads, outputs) exist.

    This function is called lazily when any sandbox tool is first used.
    For local sandbox, it creates the directories on the filesystem.
    For other sandboxes (like aio), directories are already mounted in the container.

    Args:
        runtime: Tool runtime containing state and context.
    """
    if runtime is None:
        return

    # Only create directories for local sandbox
    if not is_local_sandbox(runtime):
        return

    thread_data = get_thread_data(runtime)
    if thread_data is None:
        return

    # Check if directories have already been created
    if runtime.state.get("thread_directories_created"):
        return

    # Create the three directories
    import os

    for key in ["workspace_path", "uploads_path", "outputs_path"]:
        path = thread_data.get(key)
        if path:
            os.makedirs(path, exist_ok=True)

    # Mark as created to avoid redundant operations
    runtime.state["thread_directories_created"] = True


_TOOL_RESULTS_VIRTUAL_DIR = f"{VIRTUAL_PATH_PREFIX}/workspace/.tool-results"


def _bash_truncation_marker(skipped: int, offloaded_path: str | None) -> str:
    if offloaded_path:
        return f"\n... [middle truncated: {skipped} chars skipped — full output saved to {offloaded_path}; read it in slices with read_file] ...\n"
    return f"\n... [middle truncated: {skipped} chars skipped] ...\n"


def _truncate_bash_output(output: str, max_chars: int, offloaded_path: str | None = None) -> str:
    """Middle-truncate bash output, preserving head and tail (50/50 split).

    bash output may have errors at either end (stderr/stdout ordering is
    non-deterministic), so both ends are preserved equally.

    When *offloaded_path* is given, the truncation marker points at the file
    holding the full output so the agent can read the elided middle in slices.

    The returned string (including the truncation marker) is guaranteed to be
    no longer than max_chars characters. Pass max_chars=0 to disable truncation
    and return the full output unchanged.
    """
    if max_chars == 0:
        return output
    if len(output) <= max_chars:
        return output
    total_len = len(output)
    # Compute the exact worst-case marker length: skipped chars is at most
    # total_len, so this is a tight upper bound.
    marker_max_len = len(_bash_truncation_marker(total_len, offloaded_path))
    kept = max(0, max_chars - marker_max_len)
    if kept == 0 and offloaded_path is not None:
        # The path-carrying marker does not fit within max_chars; fall back to
        # the plain marker so the output is still marked as truncated.
        marker_max_len = len(_bash_truncation_marker(total_len, None))
        kept = max(0, max_chars - marker_max_len)
        offloaded_path = None
    if kept == 0:
        return output[:max_chars]
    head_len = kept // 2
    tail_len = kept - head_len
    skipped = total_len - kept
    marker = _bash_truncation_marker(skipped, offloaded_path)
    return f"{output[:head_len]}{marker}{output[-tail_len:] if tail_len > 0 else ''}"


def _offload_full_bash_output(sandbox: Sandbox, runtime: ToolRuntime[ContextT, ThreadState] | None, output: str, max_chars: int) -> str | None:
    """Persist over-cap bash output to the thread workspace before truncation.

    Returns the virtual path of the saved file, or None when the output fits
    the cap (or truncation is disabled) and — crucially — when the write fails
    for any reason: offloading must never break the bash tool, so callers fall
    back to the plain truncation marker on None.
    """
    if max_chars == 0 or len(output) <= max_chars:
        return None
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    virtual_path = f"{_TOOL_RESULTS_VIRTUAL_DIR}/bash-{timestamp}-{secrets.token_hex(3)}.log"
    try:
        if is_local_sandbox(runtime):
            # Local sandbox: resolve the virtual path to the host path in the
            # tool layer (same pattern as write_file_tool) so the security
            # validation runs; LocalSandbox.write_file creates parent dirs.
            thread_data = get_thread_data(runtime)
            if thread_data is None:
                return None
            sandbox.write_file(_resolve_and_validate_user_data_path(virtual_path, thread_data), output)
        else:
            # Remote sandboxes bind-mount /mnt/user-data per thread, so the
            # virtual path is valid inside the container. The file API does
            # not guarantee parent creation, so mkdir first (best-effort).
            sandbox.execute_command(f"mkdir -p {_TOOL_RESULTS_VIRTUAL_DIR}", timeout=30)
            sandbox.write_file(virtual_path, output)
    except Exception:
        logger.warning("Failed to offload full bash output to %s; falling back to plain truncation", virtual_path, exc_info=True)
        return None
    return virtual_path


# 上游定义在 read_file_contract.py；本仓库没有该文件，截断标记前缀在此直接定义。
READ_FILE_TRUNCATION_PREFIX = "... [truncated:"


# A read_file cut prefers the last line boundary before the limit, so the model
# never sees a partial line that reads as complete and the marker can name the
# exact next ``start_line``. When the partial line at the cut is longer than
# this, dropping it would throw away most of the budget (minified sources,
# one-line JSON), so the cut stays at the character limit and the marker names
# the line it fell inside instead.
_READ_FILE_LINE_CUT_SLACK = 4096


def _read_file_truncation_marker(*, line_offset: int, shown_lines: int, total_lines: int, kept: int, total: int, inside_line: int | None, continuation: str, ends_at_eof: bool = True) -> str:
    """Marker appended to a truncated read_file result.

    Line numbers are file line numbers: ``line_offset`` is the number of file
    lines before the first line of ``output`` (``start_line - 1`` for a ranged
    read), so a continuation named here can be passed straight back to
    ``read_file``. ``inside_line`` is None when the kept text ends on a line
    boundary; otherwise it is the 1-based line of ``output`` the cut fell in
    and ``continuation`` says how to go on from there ("next", "whole_line" or
    "bash"). ``ends_at_eof`` is False when ``output`` is a bounded slice that
    may stop before the end of the file, so a line after its last line can
    still be named. The continuation is stated in lines because that is what
    ``start_line`` and ``end_line`` take; character counts are kept for
    reference.
    """
    first = line_offset + 1
    span = f"of {total_lines}" if line_offset == 0 else f"of {first}-{line_offset + total_lines}"
    if inside_line is None:
        shown = f"first {shown_lines}" if line_offset == 0 else f"lines {first}-{line_offset + shown_lines}"
        return f"{READ_FILE_TRUNCATION_PREFIX} showing {shown} {span} lines ({kept} of {total} chars). Continue with start_line={line_offset + shown_lines + 1}, or use start_line/end_line to read a specific range] ..."
    line = line_offset + inside_line
    head = f"\n{READ_FILE_TRUNCATION_PREFIX} showing first {kept} of {total} chars, cut inside line {line} {span} lines"
    if continuation == "next":
        return f"{head}. Continue with start_line={line}, or use start_line/end_line to read a specific range] ..."
    if continuation == "whole_line":
        # The line is longer than a read that also has to carry a marker, but
        # a read of that line alone comes back whole. After the last line of a
        # read that reached the end of the file there is nothing to name; a
        # bounded slice may stop mid-file, and a read one past the end only
        # answers that the line does not exist.
        after = f", then continue with start_line={line + 1}" if inside_line < total_lines or not ends_at_eof else ""
        return f"{head}. Read that line whole with start_line={line}, end_line={line}{after}] ..."
    return f"{head}; that line is longer than a read can return. Use bash (for example cut -c) to read the rest of that line, or start_line/end_line for other lines] ..."


# Digits assumed when estimating the marker a follow-up read will carry. The
# follow-up may span more of the file than the current read did, so its counts
# are unknown here; over-reserving by a few characters only makes the "fits a
# fresh read" decision more cautious.
_READ_FILE_PESSIMISTIC_COUNT = 999_999_999


def _read_file_marker_reserve(*, line_offset: int, total_lines: int, total: int) -> int:
    """Longest marker any form can produce for these bounds (every field at its maximum)."""
    forms = [
        dict(shown_lines=total_lines, inside_line=None, continuation="next"),
        dict(shown_lines=0, inside_line=total_lines, continuation="next"),
        dict(shown_lines=0, inside_line=total_lines, continuation="whole_line"),
        dict(shown_lines=0, inside_line=total_lines, continuation="bash"),
    ]
    return max(len(_read_file_truncation_marker(line_offset=line_offset, total_lines=total_lines, kept=total, total=total, **form)) for form in forms)


# Emitted when the budget cannot even hold a marker: the model must still
# learn the output was cut and how to read it in pieces.
_READ_FILE_TINY_BUDGET_MARKER = READ_FILE_TRUNCATION_PREFIX + " {total} chars exceed the {max_chars}-char read limit; use start_line/end_line to read a smaller range] ..."


def _truncate_read_file_output(output: str, max_chars: int, *, line_offset: int = 0, joined_lines: bool = False, ends_at_eof: bool = True) -> str:
    """Head-truncate read_file output, preserving the beginning of the file.

    Source code and documents are read top-to-bottom; the head contains the
    most context (imports, class definitions, function signatures).

    The cut lands on the last line boundary the budget allows, so the kept
    text ends with a complete line and the marker names the next
    ``start_line`` in file line numbers (``line_offset`` is the number of file
    lines before ``output``, i.e. ``start_line - 1`` of a ranged read;
    ``joined_lines`` says the output is a provider slice of lines joined with
    newlines, where a trailing newline is an empty last line rather than a
    line terminator; ``ends_at_eof`` is False for a slice bounded by an
    ``end_line`` that may stop before the end of the file). Only when the
    line at the cut is longer than
    ``_READ_FILE_LINE_CUT_SLACK`` does
    the cut stay at the character limit; the marker then names the line it
    fell inside and a continuation that is guaranteed to make progress: a
    read from that line when the whole line fits such a read, a single-line
    read when only the line alone fits, and bash when even that cannot return
    it.

    The returned string (including the truncation marker) is guaranteed to be
    no longer than max_chars characters. Pass max_chars=0 to disable truncation
    and return the full output unchanged.
    """
    if max_chars == 0:
        return output
    if len(output) <= max_chars:
        return output
    total = len(output)
    total_lines = output.count("\n") + (1 if joined_lines or not output.endswith("\n") else 0)
    # Reserve the longest marker plus one character, so a newline sitting
    # exactly at the budget can still be kept as a complete line.
    kept = max(0, max_chars - _read_file_marker_reserve(line_offset=line_offset, total_lines=total_lines, total=total) - 1)
    if kept == 0:
        # Too small a budget for any text plus a marker: say so instead of
        # returning a bare prefix that reads as the whole file.
        return _READ_FILE_TINY_BUDGET_MARKER.format(total=total, max_chars=max_chars)[:max_chars]
    boundary = output.rfind("\n", 0, kept + 1)
    if boundary != -1 and kept - (boundary + 1) <= _READ_FILE_LINE_CUT_SLACK:
        kept = boundary + 1
        marker = _read_file_truncation_marker(line_offset=line_offset, shown_lines=output[:kept].count("\n"), total_lines=total_lines, kept=kept, total=total, inside_line=None, continuation="next")
        return f"{output[:kept]}{marker}"
    line_start = boundary + 1
    line_end = output.find("\n", kept)
    line_len = (total if line_end == -1 else line_end) - line_start
    inside_line = output[:kept].count("\n") + 1
    # What a read starting at this line could keep, estimated pessimistically:
    # its marker may carry larger counts than this read's.
    next_kept = max_chars - _read_file_marker_reserve(line_offset=line_offset + inside_line - 1, total_lines=_READ_FILE_PESSIMISTIC_COUNT, total=_READ_FILE_PESSIMISTIC_COUNT) - 1
    if line_len <= next_kept:
        continuation = "next"
    elif line_len <= max_chars:
        continuation = "whole_line"
    else:
        continuation = "bash"
    marker = _read_file_truncation_marker(line_offset=line_offset, shown_lines=0, total_lines=total_lines, kept=kept, total=total, inside_line=inside_line, continuation=continuation, ends_at_eof=ends_at_eof)
    return f"{output[:kept]}{marker}"


def _truncate_ls_output(output: str, max_chars: int) -> str:
    """Head-truncate ls output, preserving the beginning of the listing.

    Directory listings are read top-to-bottom; the head shows the most
    relevant structure.

    The returned string (including the truncation marker) is guaranteed to be
    no longer than max_chars characters. Pass max_chars=0 to disable truncation
    and return the full output unchanged.
    """
    if max_chars == 0:
        return output
    if len(output) <= max_chars:
        return output
    total = len(output)
    marker_max_len = len(f"\n... [truncated: showing first {total} of {total} chars. Use a more specific path to see fewer results] ...")
    kept = max(0, max_chars - marker_max_len)
    if kept == 0:
        return output[:max_chars]
    marker = f"\n... [truncated: showing first {kept} of {total} chars. Use a more specific path to see fewer results] ..."
    return f"{output[:kept]}{marker}"


@tool("bash", parse_docstring=True)
def bash_tool(runtime: ToolRuntime[ContextT, ThreadState], description: str, command: str) -> str:
    """Execute a bash command in a Linux environment.


    - Use `python` to run Python code.
    - Prefer a thread-local virtual environment in `/mnt/user-data/workspace/.venv`.
    - Use `python -m pip` (inside the virtual environment) to install Python packages.

    Args:
        description: Explain why you are running this command in short words. ALWAYS PROVIDE THIS PARAMETER FIRST.
        command: The bash command to execute. Always use absolute paths for files and directories.
    """
    try:
        sandbox = ensure_sandbox_initialized(runtime)
        # 评审二轮修复（H1 消费端接线）：解析注入 secrets（两源+AUTOBIND），
        # per-call env 注入；输出回显对注入值打码。
        injected_env = _resolve_injected_secrets(runtime)
        injected_values = list((injected_env or {}).values())
        if is_local_sandbox(runtime):
            if not is_host_bash_allowed():
                return f"Error: {LOCAL_HOST_BASH_DISABLED_MESSAGE}"
            ensure_thread_directories_exist(runtime)
            thread_data = get_thread_data(runtime)
            validate_local_bash_command_paths(command, thread_data)
            command = replace_virtual_paths_in_command(command, thread_data)
            command = _apply_cwd_prefix(command, thread_data)
            # T27 (2026-08-13): per-command timeout from sandbox config
            # (default 300s) replaces the hardcoded 600s in the sandbox impls.
            try:
                from deerflow.config.app_config import get_app_config

                sandbox_cfg = get_app_config().sandbox
                max_chars = sandbox_cfg.bash_output_max_chars if sandbox_cfg else 20000
                command_timeout = sandbox_cfg.bash_command_timeout if sandbox_cfg else 300
            except Exception:
                max_chars = 20000
                command_timeout = 300
            output = sandbox.execute_command(command, timeout=command_timeout, env=injected_env)
            masked = mask_secret_values_in_output(mask_local_paths_in_output(output, thread_data), injected_values)
            offloaded_path = _offload_full_bash_output(sandbox, runtime, masked, max_chars)
            # F3（2026-09-29）：资源类失败签名（SIGKILL/Target closed/无法分配内存）
            # 命中时在回执尾部追加中文指引块（尾部追加 → 可扛住中部截断）。
            return _truncate_bash_output(append_failure_guidance(masked), max_chars, offloaded_path)
        ensure_thread_directories_exist(runtime)
        try:
            from deerflow.config.app_config import get_app_config

            sandbox_cfg = get_app_config().sandbox
            max_chars = sandbox_cfg.bash_output_max_chars if sandbox_cfg else 20000
            command_timeout = sandbox_cfg.bash_command_timeout if sandbox_cfg else 300
        except Exception:
            max_chars = 20000
            command_timeout = 300
        # SEC-C4①: remote sandbox branch gets the same secret-value masking
        # (this is the branch the sandbox.environment injection feeds).
        output = sandbox.execute_command(command, timeout=command_timeout, env=injected_env)
        masked = mask_secret_values_in_output(output, injected_values)
        offloaded_path = _offload_full_bash_output(sandbox, runtime, masked, max_chars)
        # F3（2026-09-29）：同本地分支——offload 留原始证据，指引只进模型回执。
        return _truncate_bash_output(append_failure_guidance(masked), max_chars, offloaded_path)
    except SandboxError as e:
        return f"Error: {e}"
    except PermissionError as e:
        return f"Error: {e}"
    except GraphBubbleUp:
        # Disk-admission / sandbox-breaker control-flow must escape the tool-error fallback.
        raise
    except Exception as e:
        return f"Error: Unexpected error executing command: {_sanitize_error(e, runtime)}"


@tool("ls", parse_docstring=True)
def ls_tool(runtime: ToolRuntime[ContextT, ThreadState], description: str, path: str) -> str:
    """List the contents of a directory up to 2 levels deep in tree format.

    Args:
        description: Explain why you are listing this directory in short words. ALWAYS PROVIDE THIS PARAMETER FIRST.
        path: The **absolute** path to the directory to list.
    """
    try:
        sandbox = ensure_sandbox_initialized(runtime)
        ensure_thread_directories_exist(runtime)
        requested_path = path
        thread_data = None
        if is_local_sandbox(runtime):
            thread_data = get_thread_data(runtime)
            validate_local_tool_path(path, thread_data, read_only=True)
            if _is_skills_path(path):
                path = _resolve_skills_path(path)
            elif _is_acp_workspace_path(path):
                path = _resolve_acp_workspace_path(path, _extract_thread_id_from_thread_data(thread_data))
            elif not _is_custom_mount_path(path):
                path = _resolve_and_validate_user_data_path(path, thread_data)
            # Custom mount paths are resolved by LocalSandbox._resolve_path()
        children = sandbox.list_dir(path)
        if not children:
            return "(empty)"
        output = "\n".join(children)
        if thread_data is not None:
            output = mask_local_paths_in_output(output, thread_data)
        try:
            from deerflow.config.app_config import get_app_config

            sandbox_cfg = get_app_config().sandbox
            max_chars = sandbox_cfg.ls_output_max_chars if sandbox_cfg else 20000
        except Exception:
            max_chars = 20000
        return _truncate_ls_output(output, max_chars)
    except SandboxError as e:
        return f"Error: {e}"
    except FileNotFoundError:
        return f"Error: Directory not found: {requested_path}"
    except PermissionError:
        return f"Error: Permission denied: {requested_path}"
    except GraphBubbleUp:
        # Disk-admission / sandbox-breaker control-flow must escape the tool-error fallback.
        raise
    except Exception as e:
        return f"Error: Unexpected error listing directory: {_sanitize_error(e, runtime)}"


@tool("glob", parse_docstring=True)
def glob_tool(
    runtime: ToolRuntime[ContextT, ThreadState],
    description: str,
    pattern: str,
    path: str,
    include_dirs: bool = False,
    max_results: int = _DEFAULT_GLOB_MAX_RESULTS,
) -> str:
    """Find files or directories that match a glob pattern under a root directory.

    Args:
        description: Explain why you are searching for these paths in short words. ALWAYS PROVIDE THIS PARAMETER FIRST.
        pattern: The glob pattern to match relative to the root path, for example `**/*.py`.
        path: The **absolute** root directory to search under.
        include_dirs: Whether matching directories should also be returned. Default is False.
        max_results: Maximum number of paths to return. Default is 200.
    """
    try:
        sandbox = ensure_sandbox_initialized(runtime)
        ensure_thread_directories_exist(runtime)
        requested_path = path
        effective_max_results = _resolve_max_results(
            "glob",
            max_results,
            default=_DEFAULT_GLOB_MAX_RESULTS,
            upper_bound=_MAX_GLOB_MAX_RESULTS,
        )
        thread_data = None
        if is_local_sandbox(runtime):
            thread_data = get_thread_data(runtime)
            if thread_data is None:
                raise SandboxRuntimeError("Thread data not available for local sandbox")
            path = _resolve_local_read_path(path, thread_data)
        matches, truncated = sandbox.glob(path, pattern, include_dirs=include_dirs, max_results=effective_max_results)
        if thread_data is not None:
            matches = [mask_local_paths_in_output(match, thread_data) for match in matches]
        return _format_glob_results(requested_path, matches, truncated)
    except SandboxError as e:
        return f"Error: {e}"
    except FileNotFoundError:
        return f"Error: Directory not found: {requested_path}"
    except NotADirectoryError:
        return f"Error: Path is not a directory: {requested_path}"
    except PermissionError:
        return f"Error: Permission denied: {requested_path}"
    except GraphBubbleUp:
        # Disk-admission / sandbox-breaker control-flow must escape the tool-error fallback.
        raise
    except Exception as e:
        return f"Error: Unexpected error searching paths: {_sanitize_error(e, runtime)}"


@tool("grep", parse_docstring=True)
def grep_tool(
    runtime: ToolRuntime[ContextT, ThreadState],
    description: str,
    pattern: str,
    path: str,
    glob: str | None = None,
    literal: bool = False,
    case_sensitive: bool = False,
    max_results: int = _DEFAULT_GREP_MAX_RESULTS,
) -> str:
    """Search for matching lines inside text files under a root directory.

    Args:
        description: Explain why you are searching file contents in short words. ALWAYS PROVIDE THIS PARAMETER FIRST.
        pattern: The string or regex pattern to search for.
        path: The **absolute** root directory to search under.
        glob: Optional glob filter for candidate files, for example `**/*.py`.
        literal: Whether to treat `pattern` as a plain string. Default is False.
        case_sensitive: Whether matching is case-sensitive. Default is False.
        max_results: Maximum number of matching lines to return. Default is 100.
    """
    try:
        sandbox = ensure_sandbox_initialized(runtime)
        ensure_thread_directories_exist(runtime)
        requested_path = path
        effective_max_results = _resolve_max_results(
            "grep",
            max_results,
            default=_DEFAULT_GREP_MAX_RESULTS,
            upper_bound=_MAX_GREP_MAX_RESULTS,
        )
        thread_data = None
        if is_local_sandbox(runtime):
            thread_data = get_thread_data(runtime)
            if thread_data is None:
                raise SandboxRuntimeError("Thread data not available for local sandbox")
            path = _resolve_local_read_path(path, thread_data)
        matches, truncated = sandbox.grep(
            path,
            pattern,
            glob=glob,
            literal=literal,
            case_sensitive=case_sensitive,
            max_results=effective_max_results,
        )
        if thread_data is not None:
            matches = [
                GrepMatch(
                    path=mask_local_paths_in_output(match.path, thread_data),
                    line_number=match.line_number,
                    line=match.line,
                )
                for match in matches
            ]
        return _format_grep_results(requested_path, matches, truncated)
    except SandboxError as e:
        return f"Error: {e}"
    except FileNotFoundError:
        return f"Error: Directory not found: {requested_path}"
    except NotADirectoryError:
        return f"Error: Path is not a directory: {requested_path}"
    except re.error as e:
        return f"Error: Invalid regex pattern: {e}"
    except PermissionError:
        return f"Error: Permission denied: {requested_path}"
    except GraphBubbleUp:
        # Disk-admission / sandbox-breaker control-flow must escape the tool-error fallback.
        raise
    except Exception as e:
        return f"Error: Unexpected error searching file contents: {_sanitize_error(e, runtime)}"


@tool("read_file", parse_docstring=True)
def read_file_tool(
    runtime: ToolRuntime[ContextT, ThreadState],
    description: str,
    path: str,
    start_line: int | None = None,
    end_line: int | None = None,
) -> str:
    """Read the contents of a text file. Use this to examine source code, configuration files, logs, or any text-based file.

    Args:
        description: Explain why you are reading this file in short words. ALWAYS PROVIDE THIS PARAMETER FIRST.
        path: The **absolute** path to the file to read.
        start_line: Optional starting line number (1-indexed, inclusive). Omit to start at the first line.
        end_line: Optional ending line number (1-indexed, inclusive). Omit to read through the last line.
    """
    try:
        # Validate the range before touching the sandbox: a bad range must be an
        # explicit message, never a silently-ignored argument. Python's negative
        # indexing would otherwise turn start_line=-2 into a tail read.
        if start_line is not None and start_line < 1:
            return f"Error: start_line must be >= 1 (got {start_line})"
        if end_line is not None and end_line < 1:
            return f"Error: end_line must be >= 1 (got {end_line})"
        if start_line is not None and end_line is not None and start_line > end_line:
            return f"Error: start_line ({start_line}) must not be greater than end_line ({end_line})"

        sandbox = ensure_sandbox_initialized(runtime)
        ensure_thread_directories_exist(runtime)
        requested_path = path
        if is_local_sandbox(runtime):
            thread_data = get_thread_data(runtime)
            validate_local_tool_path(path, thread_data, read_only=True)
            if _is_skills_path(path):
                path = _resolve_skills_path(path)
            elif _is_acp_workspace_path(path):
                path = _resolve_acp_workspace_path(path, _extract_thread_id_from_thread_data(thread_data))
            elif not _is_custom_mount_path(path):
                path = _resolve_and_validate_user_data_path(path, thread_data)
            # Custom mount paths are resolved by LocalSandbox._resolve_path()
        content = sandbox.read_file(path)
        if not content:
            return "(empty)"
        # Either bound alone is meaningful: start_line reads to EOF, end_line
        # reads from the first line. Requiring both made "give me line 3
        # onwards" return the entire file with no indication it was ignored.
        if start_line is not None or end_line is not None:
            lines = content.splitlines()
            if start_line is not None and start_line > len(lines):
                return f"Error: start_line ({start_line}) exceeds file length ({len(lines)} lines)"
            # end_line past EOF clamps rather than errors — over-reaching the
            # end of a file is an ordinary request, unlike starting past it.
            content = "\n".join(lines[(start_line - 1) if start_line else 0 : end_line])
        try:
            from deerflow.config.app_config import get_app_config

            sandbox_cfg = get_app_config().sandbox
            max_chars = sandbox_cfg.read_file_output_max_chars if sandbox_cfg else 50000
        except Exception:
            max_chars = 50000
        return _truncate_read_file_output(
            content,
            max_chars,
            line_offset=(start_line - 1) if start_line else 0,
            joined_lines=start_line is not None or end_line is not None,
            ends_at_eof=end_line is None,
        )
    except SandboxError as e:
        return f"Error: {e}"
    except FileNotFoundError:
        return f"Error: File not found: {requested_path}"
    except PermissionError:
        return f"Error: Permission denied reading file: {requested_path}"
    except IsADirectoryError:
        return f"Error: Path is a directory, not a file: {requested_path}"
    except GraphBubbleUp:
        # Disk-admission / sandbox-breaker control-flow must escape the tool-error fallback.
        raise
    except Exception as e:
        return f"Error: Unexpected error reading file: {_sanitize_error(e, runtime)}"


def _effective_write_file_max_bytes() -> int:
    """Return the active size cap for non-append write_file calls.

    Reads DEERFLOW_WRITE_FILE_MAX_BYTES at call time (not import time) so
    deployments can retune it without a restart. Falls back to the default on
    a missing or malformed value — dropping the ceiling because of a typo
    would be worse than ignoring the override. A non-positive value disables
    the guard.
    """
    raw = os.environ.get(_WRITE_FILE_MAX_BYTES_ENV)
    if raw is None:
        return _WRITE_FILE_CONTENT_MAX_BYTES
    try:
        return int(raw)
    except ValueError:
        return _WRITE_FILE_CONTENT_MAX_BYTES


@tool("write_file", parse_docstring=True)
def write_file_tool(
    runtime: ToolRuntime[ContextT, ThreadState],
    description: str,
    path: str,
    content: str,
    append: bool = False,
) -> str:
    """Write text content to a file.

    A single non-append call must not exceed 80 KB of UTF-8 content; oversized
    payloads are rejected with an error rather than stalling the run until the
    streaming timeout fires. For larger documents either write the first
    section and use `str_replace` for subsequent edits, or split the content
    and append the remaining sections with append=True (the cap does not apply
    to append=True calls). Operators can override the cap via env var
    DEERFLOW_WRITE_FILE_MAX_BYTES (0 disables it), at the risk of streaming
    timeouts.

    Args:
        description: Explain why you are writing to this file in short words. ALWAYS PROVIDE THIS PARAMETER FIRST.
        path: The **absolute** path to the file to write to. ALWAYS PROVIDE THIS PARAMETER SECOND.
        content: The content to write to the file. ALWAYS PROVIDE THIS PARAMETER THIRD.
    """
    if not append:
        max_bytes = _effective_write_file_max_bytes()
        if max_bytes > 0:
            content_bytes = len(content.encode("utf-8"))
            if content_bytes > max_bytes:
                return (
                    f"Error: write_file content ({content_bytes} bytes) exceeds the "
                    f"{max_bytes}-byte single-call limit. Split the content into smaller "
                    "pieces: either (a) write the first section now, then use `str_replace` "
                    "for further edits, or (b) call write_file again with append=True "
                    "carrying the next section."
                )
    try:
        sandbox = ensure_sandbox_initialized(runtime)
        ensure_thread_directories_exist(runtime)
        requested_path = path
        if is_local_sandbox(runtime):
            thread_data = get_thread_data(runtime)
            validate_local_tool_path(path, thread_data)
            if not _is_custom_mount_path(path):
                path = _resolve_and_validate_user_data_path(path, thread_data)
            # Custom mount paths are resolved by LocalSandbox._resolve_path()
        with get_file_operation_lock(sandbox, path):
            sandbox.write_file(path, content, append)
        return "OK"
    except SandboxError as e:
        return f"Error: {e}"
    except PermissionError:
        return f"Error: Permission denied writing to file: {requested_path}"
    except IsADirectoryError:
        return f"Error: Path is a directory, not a file: {requested_path}"
    except OSError as e:
        return f"Error: Failed to write file '{requested_path}': {_sanitize_error(e, runtime)}"
    except GraphBubbleUp:
        # Disk-admission / sandbox-breaker control-flow must escape the tool-error fallback.
        raise
    except Exception as e:
        return f"Error: Unexpected error writing file: {_sanitize_error(e, runtime)}"


@tool("str_replace", parse_docstring=True)
def str_replace_tool(
    runtime: ToolRuntime[ContextT, ThreadState],
    description: str,
    path: str,
    old_str: str,
    new_str: str,
    replace_all: bool = False,
) -> str:
    """Replace a substring in a file with another substring.
    If `replace_all` is False (default), the substring to replace must appear **exactly once** in the file.

    Args:
        description: Explain why you are replacing the substring in short words. ALWAYS PROVIDE THIS PARAMETER FIRST.
        path: The **absolute** path to the file to replace the substring in. ALWAYS PROVIDE THIS PARAMETER SECOND.
        old_str: The substring to replace. ALWAYS PROVIDE THIS PARAMETER THIRD.
        new_str: The new substring. ALWAYS PROVIDE THIS PARAMETER FOURTH.
        replace_all: Whether to replace all occurrences of the substring. If False, only the first occurrence will be replaced. Default is False.
    """
    try:
        sandbox = ensure_sandbox_initialized(runtime)
        ensure_thread_directories_exist(runtime)
        requested_path = path
        if is_local_sandbox(runtime):
            thread_data = get_thread_data(runtime)
            validate_local_tool_path(path, thread_data)
            if not _is_custom_mount_path(path):
                path = _resolve_and_validate_user_data_path(path, thread_data)
            # Custom mount paths are resolved by LocalSandbox._resolve_path()
        # An empty old_str would make str.replace() insert new_str between every
        # character, silently corrupting the file while reporting success.
        if not old_str:
            return f"Error: old_str must not be empty when replacing in file: {requested_path}"
        with get_file_operation_lock(sandbox, path):
            content = sandbox.read_file(path)
            if not content or old_str not in content:
                return f"Error: String to replace not found in file: {requested_path}"
            if replace_all:
                content = content.replace(old_str, new_str)
            else:
                content = content.replace(old_str, new_str, 1)
            sandbox.write_file(path, content)
        return "OK"
    except SandboxError as e:
        return f"Error: {e}"
    except FileNotFoundError:
        return f"Error: File not found: {requested_path}"
    except PermissionError:
        return f"Error: Permission denied accessing file: {requested_path}"
    except GraphBubbleUp:
        # Disk-admission / sandbox-breaker control-flow must escape the tool-error fallback.
        raise
    except Exception as e:
        return f"Error: Unexpected error replacing string: {_sanitize_error(e, runtime)}"
