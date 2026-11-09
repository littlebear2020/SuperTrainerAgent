"""Local container backend for sandbox provisioning.

Manages sandbox containers using Docker or Apple Container on the local machine.
Handles container lifecycle, port allocation, and cross-process container discovery.
"""

from __future__ import annotations

import csv
import ipaddress
import json
import logging
import os
import shlex
import socket
import subprocess
from datetime import datetime

from deerflow.sandbox.env_policy import is_blocked_env_name
from deerflow.utils.network import get_free_port, release_port

from .backend import SandboxBackend, wait_for_sandbox_ready
from .sandbox_info import SandboxInfo

logger = logging.getLogger(__name__)


def _parse_docker_timestamp(raw: str) -> float:
    """Parse Docker's ISO 8601 timestamp into a Unix epoch float.

    Docker returns timestamps with nanosecond precision and a trailing ``Z``
    (e.g. ``2026-04-08T01:22:50.123456789Z``).  Python's ``fromisoformat``
    accepts at most microseconds and (pre-3.11) does not accept ``Z``, so the
    string is normalized before parsing.  Returns ``0.0`` on empty input or
    parse failure so callers can use ``0.0`` as a sentinel for "unknown age".
    """
    if not raw:
        return 0.0
    try:
        s = raw.strip()
        if "." in s:
            dot_pos = s.index(".")
            tz_start = dot_pos + 1
            while tz_start < len(s) and s[tz_start].isdigit():
                tz_start += 1
            frac = s[dot_pos + 1 : tz_start][:6]  # truncate to microseconds
            tz_suffix = s[tz_start:]
            s = s[: dot_pos + 1] + frac + tz_suffix
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s).timestamp()
    except (ValueError, TypeError) as e:
        logger.debug(f"Could not parse docker timestamp {raw!r}: {e}")
        return 0.0


def _extract_host_port(inspect_entry: dict, container_port: int) -> int | None:
    """Extract the host port mapped to ``container_port/tcp`` from a docker inspect entry.

    Returns None if the container has no port mapping for that port.
    """
    try:
        ports = (inspect_entry.get("NetworkSettings") or {}).get("Ports") or {}
        bindings = ports.get(f"{container_port}/tcp") or []
        if bindings:
            host_port = bindings[0].get("HostPort")
            if host_port:
                return int(host_port)
    except (ValueError, TypeError, AttributeError):
        pass
    return None


def _format_container_mount(runtime: str, host_path: str, container_path: str, read_only: bool) -> list[str]:
    """Format a bind-mount argument for the selected runtime.

    Docker's ``-v host:container`` syntax is ambiguous for Windows drive-letter
    paths like ``D:/...`` because ``:`` is both the drive separator and the
    volume separator. Use ``--mount type=bind,...`` for Docker to avoid that
    parsing ambiguity. Apple Container keeps using ``-v``.
    """
    if runtime == "docker":
        mount_spec = f"type=bind,src={host_path},dst={container_path}"
        if read_only:
            mount_spec += ",readonly"
        return ["--mount", mount_spec]

    mount_spec = f"{host_path}:{container_path}"
    if read_only:
        mount_spec += ":ro"
    return ["-v", mount_spec]


def _redact_container_command_for_log(cmd: list[str]) -> list[str]:
    """Return a Docker/Container command with environment values redacted."""
    redacted: list[str] = []
    redact_next_env = False

    for arg in cmd:
        if redact_next_env:
            if "=" in arg:
                key = arg.split("=", 1)[0]
                redacted.append(f"{key}=<redacted>" if key else "<redacted>")
            else:
                redacted.append(arg)
            redact_next_env = False
            continue

        if arg in {"-e", "--env"}:
            redacted.append(arg)
            redact_next_env = True
            continue

        if arg.startswith("--env="):
            value = arg.removeprefix("--env=")
            if "=" in value:
                key = value.split("=", 1)[0]
                redacted.append(f"--env={key}=<redacted>" if key else "--env=<redacted>")
            else:
                redacted.append(arg)
            continue

        redacted.append(arg)

    return redacted


def _format_container_command_for_log(cmd: list[str]) -> str:
    if os.name == "nt":
        return subprocess.list2cmdline(cmd)
    return shlex.join(cmd)


def _normalize_sandbox_host(host: str) -> str:
    return host.strip().lower()


def _is_ipv6_loopback_sandbox_host(host: str) -> bool:
    return _normalize_sandbox_host(host) in {"::1", "[::1]"}


def _is_loopback_sandbox_host(host: str) -> bool:
    return _normalize_sandbox_host(host) in {"", "localhost", "127.0.0.1", "::1", "[::1]"}


# Fallback gateway of Docker's default bridge network (docker0). Used when the
# daemon cannot be queried (see _docker_bridge_gateway_ip) so non-loopback
# sandbox deployments still get a host-only bind instead of 0.0.0.0.
_DOCKER_BRIDGE_GATEWAY_FALLBACK = "172.17.0.1"

# Hardening default (P1-S2)：fork 只补 pids-limit；memory/shm 沿用本仓 F1/F2/D20
# 自己的 env 链（SANDBOX_MEM_LIMIT/SANDBOX_SHM_SIZE），CPU 上限按 D20 决策不设。
_DEFAULT_SANDBOX_PIDS_LIMIT = "512"

# Module-level Desktop-daemon detection cache (see _docker_server_is_desktop).
_DOCKER_SERVER_IS_DESKTOP: bool | None = None


def _env_flag_disabled(name: str) -> bool:
    """Return True when ``name`` is explicitly set to a negative value.

    For flags whose behavior defaults to ON, only an explicit opt-out
    (``0``/``false``/``no``/``off``) counts as disabled; any other value,
    including unset, keeps the default.
    """
    return os.environ.get(name, "").strip().lower() in {"0", "false", "no", "off"}


def _docker_resource_limit(env_name: str, default: str) -> str | None:
    """Resolve a Docker resource limit from the environment with a safe default.

    Unset/empty keeps the secure default; ``0`` or ``none`` disables the limit
    entirely (escape hatch for hosts where the default breaks a workload);
    any other value is passed through verbatim so operators can tune it.
    """
    raw = os.environ.get(env_name)
    if raw is None or not raw.strip():
        return default
    value = raw.strip()
    if value.lower() in {"0", "none"}:
        return None
    return value


def _effective_docker_network_target(raw: str) -> str:
    r"""Return the network Docker will actually attach to for ``--network raw``.

    Mirrors Docker CLI's parser (opts/network.go): a value without ``=`` is
    the short syntax — the whole value is a network name or ID. A value with
    ``=`` is the long syntax — a CSV of ``key=value`` fields in any order
    (``name=``, ``alias=``, ``ip=``, ``ip6=``, ``mac-address=``,
    ``link-local-ip=``, ``driver-opt=``, ``gw-priority=``) — and the network
    is the value of the ``name=`` field, with the last occurrence winning
    and fields lowercased, exactly as Docker does it.

    Validation must run on this effective value: neither ``name=host`` nor
    ``gw-priority=0,name=host`` reads as the bare word ``host``, but both
    attach the host network namespace all the same, silently voiding the
    port publish. A long-syntax value with no ``name=`` field cannot name a
    network at all (Docker rejects it as well), so the raw value is returned
    and falls through the checks harmlessly.
    """
    value = raw.strip()
    if "=" not in value:
        return value.lower()
    target = ""
    for field in next(csv.reader([value])):
        key, _, val = field.partition("=")
        key = key.strip().lower()
        val = val.strip().lower()  # Docker lowercases the whole field as well
        if key == "name":
            target = val  # last name= wins, mirroring the loop in network.go
    return target or value.lower()


def _docker_server_is_desktop() -> bool:
    """Detect Desktop from the daemon, including a Linux DooD Gateway."""
    global _DOCKER_SERVER_IS_DESKTOP
    if _DOCKER_SERVER_IS_DESKTOP is not None:
        return _DOCKER_SERVER_IS_DESKTOP
    try:
        result = subprocess.run(
            ["docker", "info", "--format", "{{json .OperatingSystem}}"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("Could not identify the Docker server platform; assuming non-Desktop: %s", exc)
        return False
    if result.returncode != 0:
        logger.warning("Could not identify the Docker server platform; assuming non-Desktop: %s", (result.stderr or "").strip())
        return False
    raw = (result.stdout or "").strip()
    try:
        operating_system = json.loads(raw)
    except json.JSONDecodeError:
        operating_system = raw
    is_desktop = isinstance(operating_system, str) and "docker desktop" in operating_system.lower()
    _DOCKER_SERVER_IS_DESKTOP = is_desktop
    return is_desktop


def _docker_bridge_gateway_ip() -> str | None:
    """Return the gateway IPv4 of Docker's default bridge network, or None.

    The gateway is discovered from the daemon (``docker network inspect
    bridge``) because the address is deployment-specific: daemons with a
    custom ``bip`` or rootless/multi-network setups do not use 172.17.0.1.
    Any failure (docker missing, daemon down, unparsable or non-IPv4 output)
    returns None so the caller can fall back to the well-known default.
    """
    try:
        result = subprocess.run(
            [
                "docker",
                "network",
                "inspect",
                "bridge",
                "--format",
                "{{(index .IPAM.Config 0).Gateway}}",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.debug(f"Could not query Docker bridge gateway: {e}")
        return None
    if result.returncode != 0:
        logger.debug(f"docker network inspect bridge failed: {(result.stderr or '').strip()}")
        return None
    gateway = (result.stdout or "").strip()
    if not gateway:
        return None
    try:
        if ipaddress.ip_address(gateway).version != 4:
            return None
    except ValueError:
        return None
    return gateway


def _strip_ipv6_brackets(value: str) -> str:
    """Return ``value`` without IPv6 URL-style brackets, if any."""
    inner = value.strip()
    if inner.startswith("[") and inner.endswith("]"):
        return inner[1:-1]
    return inner


def _resolve_sandbox_host_address(host: str) -> str | None:
    """Resolve ``host`` to the bind spec Docker should publish sandboxes on.

    ``host.docker.internal`` resolves to whatever the daemon's
    ``host-gateway-ip`` maps it to (customizable and possibly IPv6), so the
    address the gateway will actually *connect* to is exactly this
    resolution — binding it keeps the published port and the advertised
    sandbox URL on the same address instead of guessing the default bridge
    IPv4. IPv6 results are bracketed for Docker's ``-p`` syntax. Returns
    None when the name cannot be resolved.
    """
    # getaddrinfo takes the bare form; a bracketed IPv6 literal (legal in
    # DEER_FLOW_SANDBOX_HOST) would fail to resolve and silently fall back
    # to the IPv4 bridge gateway, splitting the bind from the URL address.
    lookup = _strip_ipv6_brackets(host)
    try:
        infos = socket.getaddrinfo(lookup, None)
    except OSError as e:
        logger.debug(f"Could not resolve sandbox host {host!r}: {e}")
        return None
    for family, _, _, _, sockaddr in infos:
        ip = sockaddr[0]
        if family == socket.AF_INET6:
            # Drop any zone id (%eth0) — Docker bind specs do not accept it.
            ip = ip.split("%", 1)[0]
            if ip in ("::",):
                continue
            return f"[{ip}]"
        if family == socket.AF_INET and ip not in ("0.0.0.0",):
            return ip
    return None


def _normalize_docker_bind_spec(value: str) -> str:
    """Bracket bare IPv6 literals for Docker's ``-p`` publish syntax.

    Docker requires the host part of a publish spec to be a bracketed IPv6
    literal (``[fd00::1]:port:8080``), but operators writing the bind override
    naturally give the bare address. Raw and already-bracketed IPv6 forms are
    normalized; IPv4 addresses and hostnames pass through unchanged.
    """
    candidate = value.strip()
    inner = candidate
    if candidate.startswith("[") and candidate.endswith("]"):
        inner = candidate[1:-1]
    try:
        if ipaddress.ip_address(inner).version == 6:
            return f"[{inner}]"
    except ValueError:
        pass
    return candidate


def _is_ip_bind_spec(value: str) -> bool:
    """Return True when ``value`` (bare or bracketed) is an IP literal."""
    inner = value.strip()
    if inner.startswith("[") and inner.endswith("]"):
        inner = inner[1:-1]
    try:
        ipaddress.ip_address(inner)
        return True
    except ValueError:
        return False


def _resolve_docker_bind_host(sandbox_host: str | None = None, bind_host: str | None = None) -> str:
    """Choose the host interface for legacy Docker ``-p`` sandbox publishing.

    Bare-metal/local runs talk to sandboxes through localhost and bind to
    127.0.0.1, so the sandbox HTTP API (which has no authentication — anyone
    who can reach it gets arbitrary shell execution) is never exposed on
    other host interfaces.

    Non-loopback sandbox hosts (typically Docker-outside-of-Docker via
    ``host.docker.internal``) used to bind 0.0.0.0, which published the
    unauthenticated exec API on every interface of the host (B3). They now
    bind the address the sandbox host itself resolves to: the published port
    and the advertised sandbox URL always match. On Docker Desktop,
    ``host.docker.internal`` resolves to a VM-internal address the host OS
    cannot bind, so Desktop daemons default to host loopback (127.0.0.1);
    Desktop forwards ``host.docker.internal`` to host loopback automatically.
    Only when resolution fails does the default bridge gateway serve as a
    best-effort fallback (with a warning). Operators that genuinely need the
    old broad bind can restore it with ``DEER_FLOW_SANDBOX_BIND_HOST=0.0.0.0``
    — that re-exposes an unauthenticated shell endpoint and should be paired
    with an external firewall.
    """
    explicit_bind = bind_host if bind_host is not None else os.environ.get("DEER_FLOW_SANDBOX_BIND_HOST", "").strip()
    if explicit_bind:
        explicit_bind = _normalize_docker_bind_spec(explicit_bind)
        if explicit_bind and not _is_ip_bind_spec(explicit_bind):
            resolved = _resolve_sandbox_host_address(explicit_bind)
            if resolved is None:
                raise RuntimeError(
                    f"DEER_FLOW_SANDBOX_BIND_HOST={explicit_bind!r} is not an IP literal and could not be resolved; "
                    "Docker publish specs require an IP address as the host part. "
                    "Set an IPv4/IPv6 literal (bare or bracketed) or a resolvable hostname."
                )
            explicit_bind = resolved
        if explicit_bind:
            logger.debug("Docker sandbox bind: %s (explicit bind host override)", explicit_bind)
            return explicit_bind

    host = sandbox_host if sandbox_host is not None else os.environ.get("DEER_FLOW_SANDBOX_HOST", "localhost")
    if _is_ipv6_loopback_sandbox_host(host):
        logger.debug("Docker sandbox bind: [::1] (IPv6 loopback sandbox host)")
        return "[::1]"
    if _is_loopback_sandbox_host(host):
        logger.debug("Docker sandbox bind: 127.0.0.1 (loopback default)")
        return "127.0.0.1"

    if _docker_server_is_desktop() and host.strip().rstrip(".").lower() in (
        "host.docker.internal",
        "gateway.docker.internal",
        "docker.for.mac.host.internal",
        "docker.for.mac.localhost",
        "docker.for.win.host.internal",
        "docker.for.win.localhost",
    ):
        logger.debug("Docker sandbox bind: 127.0.0.1 (Docker Desktop host loopback)")
        return "127.0.0.1"

    resolved = _resolve_sandbox_host_address(host)
    if resolved:
        logger.debug(
            "Docker sandbox bind: %s (resolved from sandbox host %r, follows the daemon host-gateway mapping)",
            resolved,
            host,
        )
        return resolved

    # Resolution failed (unusual — e.g. a custom hostname with no DNS entry
    # yet). Fall back to the default bridge gateway so non-loopback setups
    # still get a host-only bind, and tell the operator to set the explicit
    # override when their host-gateway-ip is customized or IPv6.
    gateway = _docker_bridge_gateway_ip() or _DOCKER_BRIDGE_GATEWAY_FALLBACK
    logger.warning(
        "Could not resolve sandbox host %r for the Docker bind; falling back to the default bridge gateway %s. If the daemon's host-gateway-ip is customized or IPv6, set DEER_FLOW_SANDBOX_BIND_HOST to that address explicitly.",
        host,
        gateway,
    )
    return gateway


class LocalContainerBackend(SandboxBackend):
    """Backend that manages sandbox containers locally using Docker or Apple Container.

    On macOS, automatically prefers Apple Container if available, otherwise falls back to Docker.
    On other platforms, uses Docker.

    Features:
    - Deterministic container naming for cross-process discovery
    - Port allocation with thread-safe utilities
    - Container lifecycle management (start/stop with --rm)
    - Support for volume mounts and environment variables
    """

    def __init__(
        self,
        *,
        image: str,
        base_port: int,
        container_prefix: str,
        config_mounts: list,
        environment: dict[str, str],
        mem_limit: str | None = None,
        shm_size: str | None = None,
    ):
        """Initialize the local container backend.

        Args:
            image: Container image to use.
            base_port: Base port number to start searching for free ports.
            container_prefix: Prefix for container names (e.g., "deer-flow-sandbox").
            config_mounts: Volume mount configurations from config (list of VolumeMountConfig).
            environment: Environment variables to inject into containers.
            mem_limit: Container memory limit from config.yaml (``sandbox.mem_limit``).
                ``None`` keeps the built-in default (3g); env ``SANDBOX_MEM_LIMIT``
                always wins over this value when set (F2, 2026-09-29).
            shm_size: Container ``/dev/shm`` size from config.yaml (``sandbox.shm_size``).
                ``None`` keeps the built-in default (2g); env ``SANDBOX_SHM_SIZE``
                always wins over this value when set (F1/F2, 2026-09-29).
        """
        self._image = image
        self._base_port = base_port
        self._container_prefix = container_prefix
        self._config_mounts = config_mounts
        self._environment = environment
        self._warn_if_environment_nonempty()
        self._mem_limit = mem_limit
        self._shm_size = shm_size
        self._runtime = self._detect_runtime()

    @property
    def runtime(self) -> str:
        """The detected container runtime ("docker" or "container")."""
        return self._runtime

    def _detect_runtime(self) -> str:
        """Detect which container runtime to use.

        On macOS, prefer Apple Container if available, otherwise fall back to Docker.
        On other platforms, use Docker.

        Returns:
            "container" for Apple Container, "docker" for Docker.
        """
        import platform

        if platform.system() == "Darwin":
            try:
                result = subprocess.run(
                    ["container", "--version"],
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=5,
                )
                logger.info(f"Detected Apple Container: {result.stdout.strip()}")
                return "container"
            except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
                logger.info("Apple Container not available, falling back to Docker")

        return "docker"

    # ── SandboxBackend interface ──────────────────────────────────────────

    def create(self, thread_id: str, sandbox_id: str, extra_mounts: list[tuple[str, str, bool]] | None = None) -> SandboxInfo:
        """Start a new container and return its connection info.

        Args:
            thread_id: Thread ID for which the sandbox is being created. Useful for backends that want to organize sandboxes by thread.
            sandbox_id: Deterministic sandbox identifier (used in container name).
            extra_mounts: Additional volume mounts as (host_path, container_path, read_only) tuples.

        Returns:
            SandboxInfo with container details.

        Raises:
            RuntimeError: If the container fails to start.
        """
        container_name = f"{self._container_prefix}-{sandbox_id}"

        # Retry loop: if Docker rejects the port (e.g. a stale container still
        # holds the binding after a process restart), skip that port and try the
        # next one.  The socket-bind check in get_free_port mirrors Docker's
        # 0.0.0.0 bind, but Docker's port-release can be slightly asynchronous,
        # so a reactive fallback here ensures we always make progress.
        _next_start = self._base_port
        container_id: str | None = None
        port: int = 0
        for _attempt in range(10):
            port = get_free_port(start_port=_next_start)
            try:
                container_id = self._start_container(container_name, port, extra_mounts)
                break
            except RuntimeError as exc:
                release_port(port)
                err = str(exc)
                err_lower = err.lower()
                # Port already bound: skip this port and retry with the next one.
                if "port is already allocated" in err or "address already in use" in err_lower:
                    logger.warning(f"Port {port} rejected by Docker (already allocated), retrying with next port")
                    _next_start = port + 1
                    continue
                # Container-name conflict: another process may have already started
                # the deterministic sandbox container for this sandbox_id. Try to
                # discover and adopt the existing container instead of failing.
                if "is already in use by container" in err_lower or "conflict. the container name" in err_lower:
                    logger.warning(f"Container name {container_name} already in use, attempting to discover existing sandbox instance")
                    existing = self.discover(sandbox_id)
                    if existing is not None:
                        return existing
                raise
        else:
            raise RuntimeError("Could not start sandbox container: all candidate ports are already allocated by Docker")

        # When running inside Docker (DooD), sandbox containers are reachable via
        # host.docker.internal rather than localhost (they run on the host daemon).
        sandbox_host = os.environ.get("DEER_FLOW_SANDBOX_HOST", "localhost")
        return SandboxInfo(
            sandbox_id=sandbox_id,
            sandbox_url=f"http://{sandbox_host}:{port}",
            container_name=container_name,
            container_id=container_id,
        )

    def destroy(self, info: SandboxInfo) -> None:
        """Stop the container and release its port."""
        # Prefer container_id, fall back to container_name (both accepted by docker stop).
        # This ensures containers discovered via list_running() (which only has the name)
        # can also be stopped.
        stop_target = info.container_id or info.container_name
        if stop_target:
            self._stop_container(stop_target)
        # Extract port from sandbox_url for release
        try:
            from urllib.parse import urlparse

            port = urlparse(info.sandbox_url).port
            if port:
                release_port(port)
        except Exception:
            pass

    def is_alive(self, info: SandboxInfo) -> bool:
        """Check if the container is still running (lightweight, no HTTP)."""
        if info.container_name:
            return self._is_container_running(info.container_name)
        return False

    def discover(self, sandbox_id: str) -> SandboxInfo | None:
        """Discover an existing container by its deterministic name.

        Checks if a container with the expected name is running, retrieves its
        port, and verifies it responds to health checks.

        Args:
            sandbox_id: The deterministic sandbox ID (determines container name).

        Returns:
            SandboxInfo if container found and healthy, None otherwise.
        """
        container_name = f"{self._container_prefix}-{sandbox_id}"

        if not self._is_container_running(container_name):
            return None

        port = self._get_container_port(container_name)
        if port is None:
            return None

        sandbox_host = os.environ.get("DEER_FLOW_SANDBOX_HOST", "localhost")
        sandbox_url = f"http://{sandbox_host}:{port}"
        if not wait_for_sandbox_ready(sandbox_url, timeout=5):
            return None

        return SandboxInfo(
            sandbox_id=sandbox_id,
            sandbox_url=sandbox_url,
            container_name=container_name,
        )

    def list_running(self) -> list[SandboxInfo]:
        """Enumerate all running containers matching the configured prefix.

        Uses a single ``docker ps`` call to list container names, then a
        single batched ``docker inspect`` call to retrieve creation timestamp
        and port mapping for all containers at once.  Total subprocess calls:
        2 (down from 2N+1 in the naive per-container approach).

        Note: Docker's ``--filter name=`` performs *substring* matching,
        so a secondary ``startswith`` check is applied to ensure only
        containers with the exact prefix are included.

        Containers without port mappings are still included (with empty
        sandbox_url) so that startup reconciliation can adopt orphans
        regardless of their port state.
        """
        # Step 1: enumerate container names via docker ps
        try:
            result = subprocess.run(
                [
                    self._runtime,
                    "ps",
                    "--filter",
                    f"name={self._container_prefix}-",
                    "--format",
                    "{{.Names}}",
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                stderr = (result.stderr or "").strip()
                logger.warning(
                    "Failed to list running containers with %s ps (returncode=%s, stderr=%s)",
                    self._runtime,
                    result.returncode,
                    stderr or "<empty>",
                )
                return []
            if not result.stdout.strip():
                return []
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
            logger.warning(f"Failed to list running containers: {e}")
            return []

        # Filter to names matching our exact prefix (docker filter is substring-based)
        container_names = [name.strip() for name in result.stdout.strip().splitlines() if name.strip().startswith(self._container_prefix + "-")]
        if not container_names:
            return []

        # Step 2: batched docker inspect — single subprocess call for all containers
        inspections = self._batch_inspect(container_names)

        infos: list[SandboxInfo] = []
        sandbox_host = os.environ.get("DEER_FLOW_SANDBOX_HOST", "localhost")
        for container_name in container_names:
            data = inspections.get(container_name)
            if data is None:
                # Container disappeared between ps and inspect, or inspect failed
                continue
            created_at, host_port = data
            sandbox_id = container_name[len(self._container_prefix) + 1 :]
            sandbox_url = f"http://{sandbox_host}:{host_port}" if host_port else ""

            infos.append(
                SandboxInfo(
                    sandbox_id=sandbox_id,
                    sandbox_url=sandbox_url,
                    container_name=container_name,
                    created_at=created_at,
                )
            )

        logger.info(f"Found {len(infos)} running sandbox container(s)")
        return infos

    def _batch_inspect(self, container_names: list[str]) -> dict[str, tuple[float, int | None]]:
        """Batch-inspect containers in a single subprocess call.

        Returns a mapping of ``container_name -> (created_at, host_port)``.
        Missing containers or parse failures are silently dropped from the result.
        """
        if not container_names:
            return {}
        try:
            result = subprocess.run(
                [self._runtime, "inspect", *container_names],
                capture_output=True,
                text=True,
                timeout=15,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
            logger.warning(f"Failed to batch-inspect containers: {e}")
            return {}

        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            logger.warning(
                "Failed to batch-inspect containers with %s inspect (returncode=%s, stderr=%s)",
                self._runtime,
                result.returncode,
                stderr or "<empty>",
            )
            return {}

        try:
            payload = json.loads(result.stdout or "[]")
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse docker inspect output as JSON: {e}")
            return {}

        out: dict[str, tuple[float, int | None]] = {}
        for entry in payload:
            # ``Name`` is prefixed with ``/`` in the docker inspect response
            name = (entry.get("Name") or "").lstrip("/")
            if not name:
                continue
            created_at = _parse_docker_timestamp(entry.get("Created", ""))
            host_port = _extract_host_port(entry, 8080)
            out[name] = (created_at, host_port)
        return out

    # ── Container operations ─────────────────────────────────────────────

    def _warn_if_environment_nonempty(self) -> None:
        """评审修复（2026-09-30）：config.yaml sandbox.environment 非空 fail-safe。

        P1-S2 第一道防线是"不注入"（配置面清空）；若生产 config.yaml 未同步
        更新，旧密钥会照常注入容器。这里在构造时告警——只列键名（可操作），
        值绝不落日志；命中密钥形态的键名升级为"密钥可能泄漏"措辞。
        不改数据本身：恢复 environment 块是文档化的分钟级回退杆。
        """
        if not self._environment:
            return
        keys = sorted(self._environment.keys())
        secret_like = [key for key in keys if is_blocked_env_name(key)]
        if secret_like:
            logger.warning(
                "P1-S2 fail-safe: sandbox.environment 非空（键：%s），其中密钥形态键 %r——容器内任意 env 可读走，请核对生产 config.yaml 是否已按 P1-S2 清空（回退杆恢复除外）；值不会打印。",
                ", ".join(keys),
                secret_like,
            )
        else:
            logger.warning(
                "sandbox.environment 非空（键：%s）——P1-S2 后应保持为空（密钥走 required-secrets 带外）；若为事故回退期恢复请忽略本告警。",
                ", ".join(keys),
            )

    def _start_container(
        self,
        container_name: str,
        port: int,
        extra_mounts: list[tuple[str, str, bool]] | None = None,
    ) -> str:
        """Start a new container.

        Args:
            container_name: Name for the container.
            port: Host port to map to container port 8080.
            extra_mounts: Additional volume mounts.

        Returns:
            The container ID.

        Raises:
            RuntimeError: If container fails to start.
        """
        cmd = [self._runtime, "run"]

        # Docker-only security hardening (P1-S2, 2026-09-30 upstream port). The
        # sandbox container executes untrusted, model-authored code, so it must
        # not run with the daemon's permissive defaults: all Linux capabilities
        # are dropped except a small compatibility set needed across supported
        # AIO image startup/runtime paths, privilege escalation (setuid/sudo)
        # is blocked, and the PID footprint is bounded so one runaway sandbox
        # cannot fork-bomb the host. Each knob has an env escape hatch.
        # Apple Container's CLI does not support these flags, so they are
        # Docker-only. (memory/shm 上限沿用本仓 F1/F2/D20 自己的 env 链，见下。)
        if self._runtime == "docker":
            # Supported shipped/recommended AIO images start as root, create
            # the gem account at runtime, chown -R /opt/jupyter, and drop to
            # that user via su. CHOWN/SETUID/SETGID cover that ownership
            # handoff. FOWNER is required by the newer 1.11.x startup path,
            # which chmods /run/user/1000 after capabilities are dropped. The
            # root nginx master also writes gem-owned logs under /var/log/nginx,
            # which requires DAC_OVERRIDE. Dropping ALL of these can make
            # root-initialized images fail before readiness. A pre-initialized
            # non-root image that needs none of these compatibility capabilities
            # opts out with DEER_FLOW_SANDBOX_IMAGE_STARTUP_CAPS=0.
            # no-new-privileges stays: it only blocks *gaining* privileges
            # through exec, it does not revoke the capabilities added here.
            if _env_flag_disabled("DEER_FLOW_SANDBOX_IMAGE_STARTUP_CAPS"):
                cmd.extend(["--cap-drop=ALL", "--security-opt", "no-new-privileges"])
            else:
                cmd.extend(
                    [
                        "--cap-drop=ALL",
                        "--cap-add=CHOWN",
                        "--cap-add=FOWNER",
                        "--cap-add=SETUID",
                        "--cap-add=SETGID",
                        "--cap-add=DAC_OVERRIDE",
                        "--security-opt",
                        "no-new-privileges",
                    ]
                )

            # The shipped AIO image runs a Chromium-based browser that does
            # not start under Docker's default seccomp profile — keep
            # seccomp=unconfined as the default so the shipped image keeps
            # working (plan §4.2 灰度序 ⑤：维持 unconfined，收紧留作后续
            # opt-in 评估). Two ways to tighten it for a known image:
            #   DEER_FLOW_SANDBOX_SECCOMP_PROFILE=/path/to/profile.json
            #   DEER_FLOW_SANDBOX_SECCOMP_UNCONFINED=0 → builtin default
            seccomp_profile = os.environ.get("DEER_FLOW_SANDBOX_SECCOMP_PROFILE", "").strip()
            if seccomp_profile:
                cmd.extend(["--security-opt", f"seccomp={seccomp_profile}"])
            elif not _env_flag_disabled("DEER_FLOW_SANDBOX_SECCOMP_UNCONFINED"):
                cmd.extend(["--security-opt", "seccomp=unconfined"])
            else:
                # The documented opt-out must actually enable Docker's
                # built-in filtering: merely omitting the option would
                # inherit the daemon's configured default.
                cmd.extend(["--security-opt", "seccomp=builtin"])

            # Pids cap (灰度序 ④)：fork-bomb 界面。DEER_FLOW_SANDBOX_PIDS_LIMIT
            # 覆盖，0/none 关闭（渲染色峰值验证后可调高）。
            if pids_limit := _docker_resource_limit("DEER_FLOW_SANDBOX_PIDS_LIMIT", _DEFAULT_SANDBOX_PIDS_LIMIT):
                cmd.extend(["--pids-limit", pids_limit])

            # Attach to a user-defined network when configured (e.g. so the
            # sandbox can egress and reach other services on the same compose
            # network instead of falling onto Docker's default bridge).
            sandbox_network = os.environ.get("DEER_FLOW_SANDBOX_NETWORK", "").strip()
            if sandbox_network:
                # Validate the *effective* target: Docker accepts the extended
                # "name=<network>" long syntax in addition to plain names and
                # network IDs, and "name=host" / "name=none" attach exactly
                # like the bare words while dodging a raw-string check.
                target = _effective_docker_network_target(sandbox_network)
                if target == "host" or target.startswith("container:"):
                    # Docker discards -p/--publish in host mode and
                    # container:<name> shares another container's network
                    # namespace, so either one voids the hardened bind below
                    # and re-exposes the unauthenticated sandbox exec API on
                    # the host's interfaces. Refuse instead of silently
                    # losing the bind.
                    raise RuntimeError(
                        f"DEER_FLOW_SANDBOX_NETWORK={sandbox_network!r} resolves to the {target.split(':', 1)[0]!r} network, "
                        "which would void the sandbox port bind (Docker drops -p/--publish in host mode and shares "
                        "the network namespace for container:<name>). Use a dedicated egress-controlled bridge "
                        "network instead."
                    )
                if target == "none":
                    # The none driver gives the container only a loopback
                    # interface, so the published sandbox HTTP API cannot
                    # receive traffic: readiness would time out (60s), the
                    # container would be destroyed, and every acquisition
                    # would fail. Refuse at start-up with a clear message.
                    raise RuntimeError(
                        f"DEER_FLOW_SANDBOX_NETWORK={sandbox_network!r} resolves to the 'none' network, which leaves the "
                        "container loopback-only, so the published sandbox API port cannot receive traffic (readiness "
                        "would time out and every acquisition would fail). Use a dedicated egress-controlled bridge "
                        "network instead."
                    )
                # Pass the raw value through: custom names, network IDs, and
                # the legit name=<custom-net> long form all keep working.
                cmd.extend(["--network", sandbox_network])

        cmd.extend(["--rm", "-d"])
        if self._runtime == "docker":
            port_mapping = f"{_resolve_docker_bind_host()}:{port}:8080"
        else:
            port_mapping = f"{port}:8080"

        cmd.extend(
            [
                "-p",
                port_mapping,
                "--name",
                container_name,
            ]
        )

        # D20（2026-09-08 remotion-course T14）：内存上限 3g（硬性，防 OOM 误杀宿主
        # 其他服务）+ memory-swap 同值（禁 swap 换出）。按 D20 **不设 CPU 上限**——
        # CPU 竞争只是软性变慢，列入监控项。此前 docker run 无任何资源参数，3 副本
        # agent 重负载可吃穿宿主内存。
        # ⚠️ 账本口径（2026-09-29 修正）：本块原注释按 128G 宿主机总账设计；公司
        # 生产机实际 64G（compose 服务上限合计已 66g，见部署指南 K17 内存账本），
        # 生产档位经 SANDBOX_MEM_LIMIT=6g 走 env 覆盖，并要求配套监控/回退口径。
        # F2（2026-09-29）：优先级 env（SANDBOX_MEM_LIMIT，运维逃生口，空串=显式
        # 关闭）> config.yaml（sandbox.mem_limit，经 provider 注入）> 内置默认 3g。
        mem_env = os.environ.get("SANDBOX_MEM_LIMIT")
        if mem_env is not None:
            sandbox_mem_limit = mem_env
        else:
            sandbox_mem_limit = self._mem_limit or "3g"
        if self._runtime == "docker" and sandbox_mem_limit:
            cmd.extend(["--memory", sandbox_mem_limit, "--memory-swap", sandbox_mem_limit])

        # F1（2026-09-29 长任务中断修复计划）：docker run 显式 --shm-size（默认 2g，
        # 对齐 remotion-worker 的 shm_size: 2g）。缺省 64M 的 /dev/shm 是 Chrome 多
        # 进程渲染在长片任务上崩溃（Target closed / SIGKILL）的高概率根因；tmpfs
        # 按需分配不占闲内存（但用量计入容器 cgroup，须与内存档配套）。优先级与
        # mem 块同口径：env（SANDBOX_SHM_SIZE，空串=显式关闭）> config（sandbox.
        # shm_size）> 内置默认 2g；Apple Container 路径不加（与 mem 块同口径）。
        shm_env = os.environ.get("SANDBOX_SHM_SIZE")
        if shm_env is not None:
            sandbox_shm_size = shm_env
        else:
            sandbox_shm_size = self._shm_size or "2g"
        if self._runtime == "docker" and sandbox_shm_size:
            cmd.extend(["--shm-size", sandbox_shm_size])

        # 2026-10-13 auto-video-flow 渲染加固：无人值守渲染不需要 core 文件，单个
        # core 可达 ~500MB，多份堆积会把 bundle 阶段内存推过 3G cgroup（实测 OOM）。
        # 固定禁用，不提供 env/config 覆盖口；Apple Container 路径不支持该 flag。
        if self._runtime == "docker":
            cmd.extend(["--ulimit", "core=0"])

        # Environment variables
        for key, value in self._environment.items():
            cmd.extend(["-e", f"{key}={value}"])

        # Config-level volume mounts
        for mount in self._config_mounts:
            cmd.extend(
                _format_container_mount(
                    self._runtime,
                    mount.host_path,
                    mount.container_path,
                    mount.read_only,
                )
            )

        # Extra mounts (thread-specific, skills, etc.)
        if extra_mounts:
            for host_path, container_path, read_only in extra_mounts:
                cmd.extend(
                    _format_container_mount(
                        self._runtime,
                        host_path,
                        container_path,
                        read_only,
                    )
                )

        cmd.append(self._image)

        log_cmd = _format_container_command_for_log(_redact_container_command_for_log(cmd))
        logger.info(f"Starting container using {self._runtime}: {log_cmd}")

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            container_id = result.stdout.strip()
            logger.info(f"Started container {container_name} (ID: {container_id}) using {self._runtime}")
            return container_id
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to start container using {self._runtime}: {e.stderr}")
            raise RuntimeError(f"Failed to start sandbox container: {e.stderr}")

    def _stop_container(self, container_id: str) -> None:
        """Stop a container (--rm ensures automatic removal)."""
        try:
            subprocess.run(
                [self._runtime, "stop", container_id],
                capture_output=True,
                text=True,
                check=True,
            )
            logger.info(f"Stopped container {container_id} using {self._runtime}")
        except subprocess.CalledProcessError as e:
            logger.warning(f"Failed to stop container {container_id}: {e.stderr}")

    def _is_container_running(self, container_name: str) -> bool:
        """Check if a named container is currently running.

        This enables cross-process container discovery — any process can detect
        containers started by another process via the deterministic container name.
        """
        try:
            result = subprocess.run(
                [self._runtime, "inspect", "-f", "{{.State.Running}}", container_name],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return result.returncode == 0 and result.stdout.strip().lower() == "true"
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return False

    def _get_container_port(self, container_name: str) -> int | None:
        """Get the host port of a running container.

        Args:
            container_name: The container name to inspect.

        Returns:
            The host port mapped to container port 8080, or None if not found.
        """
        try:
            result = subprocess.run(
                [self._runtime, "port", container_name, "8080"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                # Output format: "0.0.0.0:PORT" or ":::PORT"
                port_str = result.stdout.strip().split(":")[-1]
                return int(port_str)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError):
            pass
        return None
