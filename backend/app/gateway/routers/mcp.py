from __future__ import annotations

import errno
import json
import logging
import os
import tempfile
import threading
from pathlib import Path
from typing import Literal, NamedTuple

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.gateway.admin_auth import require_admin
from deerflow.config.extensions_config import ExtensionsConfig, get_extensions_config, reload_extensions_config

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["mcp"])

# Serializes concurrent PUT /api/mcp/config writers within this process so two
# requests cannot interleave read-merge-write and lose one another's servers.
_config_write_lock = threading.Lock()

# ── A5（2026-10-07 反馈核实批）：stdio launcher 校验，整段移植上游 ──────────
# ``command``/``args`` 会原样变成 gateway 主机上的进程 argv；require_admin 是
# 唯一防线时，admin 会话被盗即宿主机 RCE。以下三层校验提供纵深（默认拒绝
# 姿态已拍板 D2）：allowlist + 任意执行旗标 + 代码注入型环境变量。

# 默认 allowlist 硬编码为代码默认；扩展点 = extensions_config.json 顶层
# ``mcp_stdio_launcher_allowlist`` 键（并集语义），刻意不进 config.yaml、不
# 递增 config_version（避免与 pending W2 锁定的 24→25 撞车）。
_DEFAULT_MCP_STDIO_COMMAND_ALLOWLIST = frozenset({"npx", "uvx"})
_SHELL_METACHARS = frozenset(";|&`$<>\n\r")

# Flags that turn an allowlisted launcher into an arbitrary code evaluator.
# Validating only the command name leaves the allowlist naming a binary
# without constraining what that binary runs, so these are screened too.
# The spellings below mean "evaluate this string" across every launcher an
# operator would plausibly allowlist (npx/uvx `--call`, python/sh `-c`,
# node/perl/ruby `-e`/`--eval`, node `--print`), plus npx's pass-through into
# node's own argv.
#
# This is defense in depth, not a trust boundary. `npx`/`uvx` exist to fetch
# and run remote code, so an admin can still point one at a package they
# published; the boundary remains admin authentication plus not exposing the
# Gateway to untrusted networks.
_ARBITRARY_EXEC_ARGS = frozenset(
    {
        "-c",
        "--call",
        "-e",
        "--eval",
        "--print",
        "--shell",
        "--node-arg",
        "--node-options",
    }
)


# Package launchers parse their own options only until the package name; every
# later token is handed to the spawned server's own CLI, where `-c` is commonly
# "config" and `-e` "env". Screening those rejected ordinary third-party servers
# without covering anything, so the screen is scoped to the option region.
#
# Finding that region needs each launcher's option *arity*, because a value is
# not a positional: `npx -p <pkg> -c '<command>'` runs the command -- `-p` is
# exec's `--package`, so `<pkg>` is its value and npm keeps parsing its own
# flags. Ending the region at the first non-flag token would walk past it.
# (Verified against npm 10.9.4 / uv 0.11.1.)
#
# The two launchers get opposite defaults for an option neither table lists,
# and the reason is the exec set above, not symmetry:
#
#   `npx` really does own exec flags here (`-c`/`--call`), so an unlisted option
#   must not be able to hide one. Unknown therefore consumes a value, keeping
#   the region open. npm *errors* on an option it does not define, so this
#   cannot reject an invocation that would otherwise work; enumerating npm's
#   booleans (rather than its much larger value-taking set) is what makes the
#   common `npx -y <pkg> ...` shape land on the package name.
#
#   `uvx` owns no exec flag at all -- uv has no "evaluate this string" option --
#   so its screen is a tripwire, not a control, and an imprecise region cannot
#   walk past anything real. Unknown therefore consumes nothing, which keeps
#   uv's large and growing boolean surface from over-blocking.
#
# A launcher outside this table is not a package runner and keeps the
# conservative whole-args screen below.
class _LauncherGrammar(NamedTuple):
    """How one package launcher separates its own options from the server's."""

    exec_args: frozenset[str]
    known_args: frozenset[str]
    unknown_consumes_value: bool

    def consumes_value(self, flag: str) -> bool:
        if self.unknown_consumes_value:
            return flag not in self.known_args
        return flag in self.known_args


# npm's boolean configs, i.e. the options that do *not* consume the next token.
# Generated from `@npmcli/config`'s definitions (npm 10.9.4): every config whose
# type is Boolean, plus every nopt shorthand expanding to one of them or to a
# complete assignment such as `-d` -> `--loglevel info`. Regenerate against a
# newer npm rather than editing by hand. A boolean missing here over-blocks one
# invocation and names the flag in the rejection, which is the failure direction
# this file prefers.
_NPM_BOOLEAN_ARGS = frozenset(
    {
        "--all",
        "--allow-same-version",
        "--audit",
        "--bin-links",
        "--commit-hooks",
        "--description",
        "--dev",
        "--diff-ignore-all-space",
        "--diff-name-only",
        "--diff-no-prefix",
        "--diff-text",
        "--dry-run",
        "--engine-strict",
        "--expect-results",
        "--force",
        "--foreground-scripts",
        "--format-package-lock",
        "--fund",
        "--git-tag-version",
        "--global",
        "--global-style",
        "--if-present",
        "--ignore-scripts",
        "--include-staged",
        "--include-workspace-root",
        "--install-links",
        "--json",
        "--legacy-bundling",
        "--legacy-peer-deps",
        "--link",
        "--long",
        "--offline",
        "--omit-lockfile-registry-resolved",
        "--optional",
        "--package-lock",
        "--package-lock-only",
        "--parseable",
        "--prefer-dedupe",
        "--prefer-offline",
        "--prefer-online",
        "--production",
        "--progress",
        "--provenance",
        "--read-only",
        "--rebuild-bundle",
        "--save",
        "--save-bundle",
        "--save-dev",
        "--save-exact",
        "--save-optional",
        "--save-peer",
        "--save-prod",
        "--shrinkwrap",
        "--sign-git-commit",
        "--sign-git-tag",
        "--strict-peer-deps",
        "--strict-ssl",
        "--timing",
        "--unicode",
        "--update-notifier",
        "--usage",
        "--version",
        "--versions",
        "--workspaces",
        "--workspaces-update",
        "--yes",
        "-?",
        "-B",
        "-D",
        "-E",
        "-H",
        "-O",
        "-P",
        "-S",
        "-a",
        "-d",
        "-dd",
        "-ddd",
        "-desc",
        "-f",
        "-g",
        "-h",
        "-help",
        "-iwr",
        "-l",
        "-local",
        "-n",
        "-no",
        "-porcelain",
        "-q",
        "-quiet",
        "-readonly",
        "-s",
        "-silent",
        "-v",
        "-verbose",
        "-ws",
        "-y",
    }
)

# `npm exec` overrides the global `-p` shorthand: it is `--package <spec>` there,
# not the boolean `--parseable`. Confirmed by running it -- `npx -p . -c '<cmd>'`
# executes the command, i.e. `.` was consumed as a value and never ended the
# option region. Treating it as boolean is exactly the bypass this table exists
# to prevent, so the override is applied explicitly rather than left implicit.
_NPX_BOOLEAN_ARGS = _NPM_BOOLEAN_ARGS - {"-p"}

# uv's value-taking options (`uvx --help`, uv 0.11.1). Everything absent is
# treated as boolean; see the unknown-option note above for why that default
# is safe here and inverted for npx.
_UVX_VALUE_ARGS = frozenset(
    {
        "--allow-insecure-host",
        "--build-constraints",
        "--cache-dir",
        "--color",
        "--config-file",
        "--config-setting",
        "--config-settings-package",
        "--constraints",
        "--default-index",
        "--directory",
        "--env-file",
        "--exclude-newer",
        "--exclude-newer-package",
        "--extra-index-url",
        "--find-links",
        "--fork-strategy",
        "--from",
        "--index",
        "--index-strategy",
        "--index-url",
        "--keyring-provider",
        "--link-mode",
        "--no-binary-package",
        "--no-build-isolation-package",
        "--no-build-package",
        "--no-sources-package",
        "--overrides",
        "--prerelease",
        "--project",
        "--python",
        "--python-platform",
        "--refresh-package",
        "--reinstall-package",
        "--resolution",
        "--torch-backend",
        "--upgrade-package",
        "--with",
        "--with-editable",
        "--with-requirements",
        "-C",
        "-P",
        "-b",
        "-c",
        "-f",
        "-i",
        "-p",
        "-w",
    }
)

_PACKAGE_LAUNCHERS: dict[str, _LauncherGrammar] = {
    "npx": _LauncherGrammar(
        exec_args=_ARBITRARY_EXEC_ARGS,
        known_args=_NPX_BOOLEAN_ARGS,
        unknown_consumes_value=True,
    ),
    # uv spells `-c` `--constraints` and `-p` `--python`, so the short forms are
    # dropped from its exec set; the long spellings stay as a tripwire in case a
    # future uv grows one. Derived so a new entry above cannot forget this.
    "uvx": _LauncherGrammar(
        exec_args=frozenset(flag for flag in _ARBITRARY_EXEC_ARGS if flag.startswith("--")),
        known_args=_UVX_VALUE_ARGS,
        unknown_consumes_value=False,
    ),
}

# `-p` is `--print` (evaluate and print) on node, so exempting it everywhere
# left the short and long spellings of one flag disagreeing as soon as an
# operator extended the allowlist. It stays scoped to commands outside
# `_PACKAGE_LAUNCHERS`, where it is an ordinary selector (`--package` for npx,
# `--python` for uv), so the default allowlist is unaffected.
_EXEC_ARGS_OUTSIDE_PACKAGE_LAUNCHERS = frozenset({"-p"})

# Short options combine into one token (`node -pe`, `perl -we`, `python -Ic`),
# which whole-token matching does not see. Derived rather than restated so a
# new single-letter entry above cannot forget its clustered spelling.
_CLUSTERED_EXEC_LETTERS = frozenset(flag[1] for flag in _ARBITRARY_EXEC_ARGS | _EXEC_ARGS_OUTSIDE_PACKAGE_LAUNCHERS if len(flag) == 2 and flag.startswith("-"))

# Environment variables that inject code into a process at startup, which is
# the same bypass as an exec flag by another name.
#
# `PYTHONPATH` matters most: `site` imports `sitecustomize.py` from any
# `sys.path` entry before the tool's entry point runs, so a caller-controlled
# directory is code execution under `uvx` -- on the *default* allowlist.
# `PYTHONSTARTUP` is inert for the non-interactive launchers in scope and is
# kept only as belt-and-braces for an operator who allowlists a REPL.
#
# Known residual, accepted (upstream同款取舍): search-path 型变量
# （LD_LIBRARY_PATH/NODE_PATH 等）只在进程恰加载可被遮蔽的名称时才可达代码，
# 不在本表——denylist 不是 MCP 注册对不可信 admin 安全的原因，边界仍是
# admin 鉴权本身。
_CODE_INJECTING_ENV_VARS = frozenset(
    {
        "BASH_ENV",
        "DYLD_INSERT_LIBRARIES",
        "ENV",
        "LD_AUDIT",
        "LD_PRELOAD",
        "NODE_OPTIONS",
        "PERL5OPT",
        "PYTHONHOME",
        "PYTHONPATH",
        "PYTHONSTARTUP",
        "RUBYOPT",
    }
)


def _allowed_stdio_commands() -> set[str]:
    """API 可写的 stdio 启动器名集合：内置默认 ∪ extensions_config 扩展键。"""
    base = set(_DEFAULT_MCP_STDIO_COMMAND_ALLOWLIST)
    extra = getattr(get_extensions_config(), "mcp_stdio_launcher_allowlist", None)
    if extra:
        base |= {item.strip() for item in extra if isinstance(item, str) and item.strip()}
    return base


def _stdio_command_name(command: str | None, *, server_name: str) -> str:
    """Normalize and validate a stdio command field from the API boundary."""
    if command is None or not command.strip():
        raise HTTPException(
            status_code=400,
            detail=f"MCP server '{server_name}' with stdio transport requires a command.",
        )

    stripped = command.strip()
    has_path_separator = "/" in stripped or "\\" in stripped
    if stripped != command or has_path_separator or any(ch.isspace() for ch in stripped) or any(ch in stripped for ch in _SHELL_METACHARS):
        raise HTTPException(
            status_code=400,
            detail=(f"MCP server '{server_name}' command must be a single executable name; put parameters in args instead."),
        )

    return stripped


def _launcher_option_region(args: list[str], *, grammar: _LauncherGrammar) -> list[str]:
    """Return the leading args a package launcher parses as its own options.

    The region ends at a bare ``--`` or at the package name -- the first token
    that is neither a flag nor the value of one. A ``--flag=value`` token
    carries its own value and never consumes the next one.

    Arity is looked up case-sensitively, because a launcher's short options are:
    npm reads ``-c`` as ``--call`` but ``-C`` as ``--prefix``, which takes a
    value.
    """
    region: list[str] = []
    index = 0
    while index < len(args):
        arg = args[index]
        if not isinstance(arg, str):
            break
        token = arg.strip()
        if token == "--" or token == "-" or not token.startswith("-"):
            break
        region.append(token)
        index += 1
        if "=" not in token and grammar.consumes_value(token):
            index += 1
    return region


def _arbitrary_exec_arg(args: list[str], *, command: str) -> str | None:
    """Return the offending flag when an argument makes the launcher eval a string.

    Handles both ``--call value`` and ``--call=value`` spellings.

    For a package launcher (:data:`_PACKAGE_LAUNCHERS`) only the launcher's own
    option region is screened, because everything from the package name onward
    is the spawned server's argv -- ``npx -y <pkg> -c config.json`` hands
    ``-c config.json`` to the server, where it is "config", not eval. A bare
    ``--`` ends the region too: only the *first* token after it is the package
    name, and the rest are that package's arguments.

    Every other command is screened whole, and two extra rules apply because
    such a command is an interpreter rather than a package runner: ``-p`` is an
    exec flag (node's ``--print``) instead of a package/python selector, and
    combined short-option clusters are decomposed so ``-pe`` cannot smuggle
    past a check that only splits on ``=``.

    Only the normalized flag is returned, never the caller's value, so the
    rejection message does not echo a payload string back into the response.
    """
    grammar = _PACKAGE_LAUNCHERS.get(command.lower())
    if grammar is not None:
        for token in _launcher_option_region(args, grammar=grammar):
            flag = token.split("=", 1)[0]
            # Long options are matched case-insensitively as before; a short one
            # is not, because its case selects a different option -- npm's `-C`
            # is `--prefix`, and folding it onto `-c` rejected an ordinary flag.
            flag = flag.lower() if flag.startswith("--") else flag
            if flag in grammar.exec_args:
                return flag
        return None

    denied = _ARBITRARY_EXEC_ARGS | _EXEC_ARGS_OUTSIDE_PACKAGE_LAUNCHERS
    for arg in args:
        if not isinstance(arg, str):
            continue
        flag = arg.split("=", 1)[0].strip().lower()
        if flag in denied:
            return flag
        if not flag.startswith("-") or flag.startswith("--"):
            continue
        for letter in flag[1:]:
            if letter in _CLUSTERED_EXEC_LETTERS:
                return f"-{letter}"
    return None


def _validate_mcp_update_request(request: McpConfigUpdateRequest) -> None:
    """Validate API-submitted MCP config before it is persisted (A5).

    Local config files can still express arbitrary advanced setups, but the
    HTTP API is a boundary. Restricting stdio commands here reduces the blast
    radius of a compromised authenticated browser session. Must run BEFORE the
    handler's ``except Exception -> 500`` wrapper so the 400s reach the client.
    """
    allowed_commands = _allowed_stdio_commands()
    for name, server in request.mcp_servers.items():
        transport_type = (server.type or "stdio").lower()
        if transport_type != "stdio":
            continue
        command_name = _stdio_command_name(server.command, server_name=name)
        if command_name not in allowed_commands:
            allowed = ", ".join(sorted(allowed_commands)) or "<none>"
            raise HTTPException(
                status_code=400,
                detail=(f"MCP server '{name}' uses disallowed stdio command '{command_name}'. Allowed commands: {allowed}. Extend via the 'mcp_stdio_launcher_allowlist' key in extensions_config.json."),
            )

        exec_flag = _arbitrary_exec_arg(server.args, command=command_name)
        if exec_flag is not None:
            raise HTTPException(
                status_code=400,
                detail=(f"MCP server '{name}' passes '{exec_flag}' to '{command_name}', which would run arbitrary code. Point the server at a package or module instead."),
            )

        for env_name in server.env:
            if env_name.strip().upper() in _CODE_INJECTING_ENV_VARS:
                raise HTTPException(
                    status_code=400,
                    detail=(f"MCP server '{name}' sets environment variable '{env_name}', which would run arbitrary code at process startup."),
                )


# ── A5 移植段结束 ────────────────────────────────────────────────────────────


def _atomic_write_json(file_path: Path, data: dict) -> None:
    """Write JSON durably: temp file in the same dir, fsync, then atomic replace.

    A bare ``open(w) + json.dump`` truncates the real config first, so a crash or
    cancellation mid-write leaves an empty/partial file — wiping every configured
    MCP server.

    DEF-2 (2026-10-07): ``os.replace`` fails with EBUSY when the destination is
    itself a single-file Docker bind mount (the legacy deploy compose mounted
    extensions_config.json as a file — renaming onto a bind-mounted file is
    denied by the kernel, so every skills/MCP toggle 500'd). Only for EBUSY do
    we fall back to a non-atomic in-place write: the crash window of that
    fallback is an accepted degraded cost, reached only under this mount
    anomaly. Deploy compose now mounts the parent directory (rename works there),
    making the fallback pure defense.
    """
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=file_path.parent, delete=False, encoding="utf-8") as tf:
        temp_name = tf.name
        json.dump(data, tf, indent=2)
        tf.flush()
        os.fsync(tf.fileno())
    try:
        os.replace(temp_name, file_path)
    except OSError as exc:
        _remove_temp_file(temp_name)
        if exc.errno != errno.EBUSY:
            raise
        logger.warning(
            "Atomic replace of %s hit EBUSY (single-file bind mount?); falling back to in-place non-atomic write",
            file_path,
        )
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
    except Exception:
        _remove_temp_file(temp_name)
        raise


def _remove_temp_file(temp_name: str) -> None:
    """Best-effort unlink of the abandoned temp file from an atomic write."""
    try:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
    except OSError:
        logger.warning("Failed to remove temp file %s", temp_name, exc_info=True)


class McpOAuthConfigResponse(BaseModel):
    """OAuth configuration for an MCP server."""

    enabled: bool = Field(default=True, description="Whether OAuth token injection is enabled")
    token_url: str = Field(default="", description="OAuth token endpoint URL")
    grant_type: Literal["client_credentials", "refresh_token"] = Field(default="client_credentials", description="OAuth grant type")
    client_id: str | None = Field(default=None, description="OAuth client ID")
    client_secret: str | None = Field(default=None, description="OAuth client secret")
    refresh_token: str | None = Field(default=None, description="OAuth refresh token")
    scope: str | None = Field(default=None, description="OAuth scope")
    audience: str | None = Field(default=None, description="OAuth audience")
    token_field: str = Field(default="access_token", description="Token response field containing access token")
    token_type_field: str = Field(default="token_type", description="Token response field containing token type")
    expires_in_field: str = Field(default="expires_in", description="Token response field containing expires-in seconds")
    default_token_type: str = Field(default="Bearer", description="Default token type when response omits token_type")
    refresh_skew_seconds: int = Field(default=60, description="Refresh this many seconds before expiry")
    extra_token_params: dict[str, str] = Field(default_factory=dict, description="Additional form params sent to token endpoint")


class McpServerConfigResponse(BaseModel):
    """Response model for MCP server configuration."""

    enabled: bool = Field(default=True, description="Whether this MCP server is enabled")
    type: str = Field(default="stdio", description="Transport type: 'stdio', 'sse', or 'http'")
    command: str | None = Field(default=None, description="Command to execute to start the MCP server (for stdio type)")
    args: list[str] = Field(default_factory=list, description="Arguments to pass to the command (for stdio type)")
    env: dict[str, str] = Field(default_factory=dict, description="Environment variables for the MCP server")
    url: str | None = Field(default=None, description="URL of the MCP server (for sse or http type)")
    headers: dict[str, str] = Field(default_factory=dict, description="HTTP headers to send (for sse or http type)")
    oauth: McpOAuthConfigResponse | None = Field(default=None, description="OAuth configuration for MCP HTTP/SSE servers")
    description: str = Field(default="", description="Human-readable description of what this MCP server provides")


class McpConfigResponse(BaseModel):
    """Response model for MCP configuration."""

    mcp_servers: dict[str, McpServerConfigResponse] = Field(
        default_factory=dict,
        description="Map of MCP server name to configuration",
    )


class McpConfigUpdateRequest(BaseModel):
    """Request model for updating MCP configuration."""

    mcp_servers: dict[str, McpServerConfigResponse] = Field(
        ...,
        description="Map of MCP server name to configuration",
    )


_MASKED_VALUE = "***"


def _mask_server_config(server: McpServerConfigResponse) -> McpServerConfigResponse:
    """Return a copy of server config with sensitive fields masked.

    Masks env values, header values, and removes OAuth secrets so they
    are not exposed through the GET API endpoint.
    """
    masked_env = {k: _MASKED_VALUE for k in server.env}
    masked_headers = {k: _MASKED_VALUE for k in server.headers}
    masked_oauth = None
    if server.oauth is not None:
        masked_oauth = server.oauth.model_copy(
            update={
                "client_secret": None,
                "refresh_token": None,
            }
        )
    return server.model_copy(
        update={
            "env": masked_env,
            "headers": masked_headers,
            "oauth": masked_oauth,
        }
    )


def _merge_preserving_secrets(
    incoming: McpServerConfigResponse,
    existing: McpServerConfigResponse,
) -> McpServerConfigResponse:
    """Merge incoming config with existing, preserving secrets masked by GET.

    When the frontend toggles ``enabled`` it round-trips the full config:
    GET (masked) → modify enabled → PUT (masked values sent back).
    This function ensures masked values (``***``) are replaced with the
    real secrets from the current on-disk config.

    ``***`` is only accepted for keys that already exist in *existing*.
    New keys must provide a real value.

    For OAuth secrets, ``None`` means "preserve the existing stored value"
    so masked GET responses can be safely round-tripped. To explicitly clear
    a stored secret, clients may send an empty string, which is converted
    to ``None`` before persisting.
    """
    merged_env = {}
    for k, v in incoming.env.items():
        if v == _MASKED_VALUE:
            if k in existing.env:
                merged_env[k] = existing.env[k]
            else:
                raise HTTPException(
                    status_code=400,
                    detail=f"Cannot set env key '{k}' to masked value '***'; provide a real value.",
                )
        else:
            merged_env[k] = v

    merged_headers = {}
    for k, v in incoming.headers.items():
        if v == _MASKED_VALUE:
            if k in existing.headers:
                merged_headers[k] = existing.headers[k]
            else:
                raise HTTPException(
                    status_code=400,
                    detail=f"Cannot set header '{k}' to masked value '***'; provide a real value.",
                )
        else:
            merged_headers[k] = v

    merged_oauth = incoming.oauth
    if incoming.oauth is not None and existing.oauth is not None:
        # None = preserve (masked round-trip), "" = explicitly clear, else = new value
        merged_client_secret = existing.oauth.client_secret if incoming.oauth.client_secret is None else (None if incoming.oauth.client_secret == "" else incoming.oauth.client_secret)
        merged_refresh_token = existing.oauth.refresh_token if incoming.oauth.refresh_token is None else (None if incoming.oauth.refresh_token == "" else incoming.oauth.refresh_token)
        merged_oauth = incoming.oauth.model_copy(
            update={
                "client_secret": merged_client_secret,
                "refresh_token": merged_refresh_token,
            }
        )
    return incoming.model_copy(
        update={
            "env": merged_env,
            "headers": merged_headers,
            "oauth": merged_oauth,
        }
    )


@router.get(
    "/mcp/config",
    response_model=McpConfigResponse,
    summary="Get MCP Configuration",
    description="Admin-only: retrieve the current Model Context Protocol (MCP) server configurations.",
    dependencies=[Depends(require_admin)],
)
async def get_mcp_configuration() -> McpConfigResponse:
    """Get the current MCP configuration.

    Returns:
        The current MCP configuration with all servers.

    Example:
        ```json
        {
            "mcp_servers": {
                "github": {
                    "enabled": true,
                    "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-github"],
                    "env": {"GITHUB_TOKEN": "***"},
                    "description": "GitHub MCP server for repository operations"
                }
            }
        }
        ```
    """
    config = get_extensions_config()

    servers = {name: _mask_server_config(McpServerConfigResponse(**server.model_dump())) for name, server in config.mcp_servers.items()}
    return McpConfigResponse(mcp_servers=servers)


@router.put(
    "/mcp/config",
    response_model=McpConfigResponse,
    summary="Update MCP Configuration",
    description="Admin-only: update Model Context Protocol (MCP) server configurations and save to file.",
    dependencies=[Depends(require_admin)],
)
async def update_mcp_configuration(request: McpConfigUpdateRequest) -> McpConfigResponse:
    """Update the MCP configuration.

    **Admin-only.** ``command``/``args`` here become the argv of a process
    ``deerflow.mcp.client`` spawns, so write access to this endpoint is write
    access to arbitrary process execution on the Gateway host. The guard is a
    route dependency rather than an in-body check because this handler wraps
    its body in ``except Exception -> 500``, which would otherwise swallow a
    403 and report it as a server error.

    This will:
    1. Save the new configuration to the mcp_config.json file
    2. Reload the configuration cache
    3. Reset MCP tools cache to trigger reinitialization

    Args:
        request: The new MCP configuration to save.

    Returns:
        The updated MCP configuration.

    Raises:
        HTTPException: 500 if the configuration file cannot be written.

    Example Request:
        ```json
        {
            "mcp_servers": {
                "github": {
                    "enabled": true,
                    "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-github"],
                    "env": {"GITHUB_TOKEN": "$GITHUB_TOKEN"},
                    "description": "GitHub MCP server for repository operations"
                }
            }
        }
        ```
    """
    # A5（2026-10-07）：校验先于 try 块——handler 尾部 ``except Exception -> 500``
    # 会把 HTTPException(400) 吞成 500（require_admin 走依赖而非体内检查是同因）。
    _validate_mcp_update_request(request)
    try:
        # The whole read-merge-write sequence is serialized: two concurrent PUTs
        # would otherwise both read the same on-disk snapshot and the later write
        # would drop the earlier one's servers.
        with _config_write_lock:
            # Get the current config path (or determine where to save it)
            config_path = ExtensionsConfig.resolve_config_path()

            # If no config file exists, create one in the parent directory (project root)
            if config_path is None:
                config_path = Path.cwd().parent / "extensions_config.json"
                logger.info(f"No existing extensions config found. Creating new config at: {config_path}")

            # Load current config to preserve skills
            current_config = get_extensions_config()

            # Load raw (un-resolved) JSON from disk to use as the merge source.
            # This preserves $VAR placeholders in env values and top-level keys
            # like mcpInterceptors that would otherwise be lost.
            raw_servers: dict[str, dict] = {}
            raw_other_keys: dict = {}
            if config_path is not None and config_path.exists():
                with open(config_path, encoding="utf-8") as f:
                    raw_data = json.load(f)
                raw_servers = raw_data.get("mcpServers", {})
                # Preserve any top-level keys beyond mcpServers/skills
                for key, value in raw_data.items():
                    if key not in ("mcpServers", "skills"):
                        raw_other_keys[key] = value

            # Merge incoming server configs with raw on-disk secrets
            merged_servers: dict[str, McpServerConfigResponse] = {}
            for name, incoming in request.mcp_servers.items():
                raw_server = raw_servers.get(name)
                if raw_server is not None:
                    merged_servers[name] = _merge_preserving_secrets(
                        incoming,
                        McpServerConfigResponse(**raw_server),
                    )
                else:
                    merged_servers[name] = incoming

            # Build config data preserving all top-level keys from the original file
            config_data = dict(raw_other_keys)
            config_data["mcpServers"] = {name: server.model_dump() for name, server in merged_servers.items()}
            config_data["skills"] = {name: {"enabled": skill.enabled} for name, skill in current_config.skills.items()}

            # Write the configuration atomically so a crash cannot truncate it
            _atomic_write_json(config_path, config_data)

        logger.info(f"MCP configuration updated and saved to: {config_path}")

        # NOTE: No need to reload/reset cache here - LangGraph Server (separate process)
        # will detect config file changes via mtime and reinitialize MCP tools automatically

        # Reload the configuration and update the global cache
        reloaded_config = reload_extensions_config()
        servers = {name: _mask_server_config(McpServerConfigResponse(**server.model_dump())) for name, server in reloaded_config.mcp_servers.items()}
        return McpConfigResponse(mcp_servers=servers)

    except Exception as e:
        logger.error(f"Failed to update MCP configuration: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to update MCP configuration")
