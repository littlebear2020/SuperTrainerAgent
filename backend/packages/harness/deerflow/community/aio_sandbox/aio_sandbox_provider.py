"""AIO Sandbox Provider — orchestrates sandbox lifecycle with pluggable backends.

This provider composes:
- SandboxBackend: how sandboxes are provisioned (local container vs remote/K8s)

The provider itself handles:
- In-process caching for fast repeated access
- Idle timeout management
- Graceful shutdown with signal handling
- Mount computation (thread-specific, skills)
"""

import atexit
import logging
import os
import signal
import threading
import time
import uuid

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None  # type: ignore[assignment]
    import msvcrt

from deerflow.config import get_app_config
from deerflow.config.paths import VIRTUAL_PATH_PREFIX, get_paths
from deerflow.runtime.user_context import get_effective_user_id
from deerflow.sandbox.identity import derive_sandbox_scope_token
from deerflow.sandbox.sandbox import Sandbox
from deerflow.sandbox.sandbox_provider import SandboxProvider

from ..warm_pool_lifecycle import (
    DEFAULT_IDLE_TIMEOUT,
    DEFAULT_REPLICAS,
    WarmPoolLifecycleMixin,
)
from .aio_sandbox import SANDBOX_DEAD_MARKER, AioSandbox
from .backend import SandboxBackend, wait_for_sandbox_ready
from .local_backend import LocalContainerBackend
from .remote_backend import RemoteSandboxBackend
from .sandbox_info import SandboxInfo

logger = logging.getLogger(__name__)

# Default configuration
DEFAULT_IMAGE = "enterprise-public-cn-beijing.cr.volces.com/vefaas-public/all-in-one-sandbox:latest"
DEFAULT_PORT = 8080
DEFAULT_CONTAINER_PREFIX = "deer-flow-sandbox"
# DEFAULT_IDLE_TIMEOUT / DEFAULT_REPLICAS / IDLE_CHECK_INTERVAL now come from
# ..warm_pool_lifecycle (single source, shared with the mixin).


class SandboxIdentityCollisionError(RuntimeError):
    """A deterministic ID is already tracked for a different user/thread."""

    def __init__(
        self,
        sandbox_id: str,
        stored_key: tuple[str, str] | None,
        requested_key: tuple[str, str],
    ) -> None:
        super().__init__(f"sandbox ID collision for {sandbox_id}: tracked identity is {stored_key!r}, requested identity is {requested_key!r}")
        self.sandbox_id = sandbox_id


def _lock_file_exclusive(lock_file) -> None:
    if fcntl is not None:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        return

    lock_file.seek(0)
    msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)


def _unlock_file(lock_file) -> None:
    if fcntl is not None:
        fcntl.flock(lock_file, fcntl.LOCK_UN)
        return

    lock_file.seek(0)
    msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)


class AioSandboxProvider(WarmPoolLifecycleMixin[SandboxInfo], SandboxProvider):
    """Sandbox provider that manages containers running the AIO sandbox.

    Architecture:
        This provider composes a SandboxBackend (how to provision), enabling:
        - Local Docker/Apple Container mode (auto-start containers)
        - Remote/K8s mode (connect to pre-existing sandbox URL)

    Configuration options in config.yaml under sandbox:
        use: deerflow.community.aio_sandbox:AioSandboxProvider
        image: <container image>
        port: 8080                      # Base port for local containers
        container_prefix: deer-flow-sandbox
        idle_timeout: 600               # Idle timeout in seconds (0 to disable)
        replicas: 3                     # Max concurrent sandbox containers (LRU eviction when exceeded)
        mounts:                         # Volume mounts for local containers
          - host_path: /path/on/host
            container_path: /path/in/container
            read_only: false
        environment:                    # Environment variables for containers
          NODE_ENV: production
          API_KEY: $MY_API_KEY
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._sandboxes: dict[str, AioSandbox] = {}  # sandbox_id -> AioSandbox instance
        self._sandbox_infos: dict[str, SandboxInfo] = {}  # sandbox_id -> SandboxInfo (for destroy)
        self._thread_sandboxes: dict[tuple[str, str], str] = {}  # (user_id, thread_id) -> sandbox_id
        self._thread_locks: dict[tuple[str, str], list] = {}  # (user_id, thread_id) -> [in-process lock, refcount]
        self._last_activity: dict[str, float] = {}  # sandbox_id -> last activity timestamp
        # Warm pool: released sandboxes whose containers are still running.
        # Maps sandbox_id -> (SandboxInfo, release_timestamp).
        # Containers here can be reclaimed quickly (no cold-start) or destroyed
        # when replicas capacity is exhausted.
        self._warm_pool: dict[str, tuple[SandboxInfo, float]] = {}
        # Identity bookkeeping (P1-S1, B1): sandbox_id -> (user_id, thread_id).
        # Active and warm are exclusive states; entries move between the two
        # tables on release/reclaim. Startup-adopted warm entries carry None
        # (unknown identity) until their first reclaim.
        self._active_sandbox_identity: dict[str, tuple[str, str] | None] = {}
        self._warm_pool_identity: dict[str, tuple[str, str] | None] = {}
        self._shutdown_called = False
        self._idle_checker_stop = threading.Event()
        self._idle_checker_thread: threading.Thread | None = None

        self._config = self._load_config()
        self._backend: SandboxBackend = self._create_backend()

        # Register shutdown handler
        atexit.register(self.shutdown)
        self._register_signal_handlers()

        # Reconcile orphaned containers from previous process lifecycles
        self._reconcile_orphans()

        # Start idle checker if enabled
        if self._config.get("idle_timeout", DEFAULT_IDLE_TIMEOUT) > 0:
            self._start_idle_checker()

    @property
    def uses_thread_data_mounts(self) -> bool:
        """Whether thread workspace/uploads/outputs are visible via mounts.

        Local container backends bind-mount the thread data directories, so files
        written by the gateway are already visible when the sandbox starts.
        Remote backends may require explicit file sync.
        """
        return isinstance(self._backend, LocalContainerBackend)

    # ── Factory methods ──────────────────────────────────────────────────

    def _create_backend(self) -> SandboxBackend:
        """Create the appropriate backend based on configuration.

        Selection logic (checked in order):
        1. ``provisioner_url`` set → RemoteSandboxBackend (provisioner mode)
              Provisioner dynamically creates Pods + Services in k3s.
        2. Default → LocalContainerBackend (local mode)
              Local provider manages container lifecycle directly (start/stop).
        """
        provisioner_url = self._config.get("provisioner_url")
        if provisioner_url:
            logger.info(f"Using remote sandbox backend with provisioner at {provisioner_url}")
            return RemoteSandboxBackend(provisioner_url=provisioner_url)

        logger.info("Using local container sandbox backend")
        return LocalContainerBackend(
            image=self._config["image"],
            base_port=self._config["port"],
            container_prefix=self._config["container_prefix"],
            config_mounts=self._config["mounts"],
            environment=self._config["environment"],
            mem_limit=self._config.get("mem_limit"),
            shm_size=self._config.get("shm_size"),
        )

    # ── Configuration ────────────────────────────────────────────────────

    def _load_config(self) -> dict:
        """Load sandbox configuration from app config."""
        config = get_app_config()
        sandbox_config = config.sandbox

        idle_timeout = getattr(sandbox_config, "idle_timeout", None)
        replicas = getattr(sandbox_config, "replicas", None)

        return {
            "image": sandbox_config.image or DEFAULT_IMAGE,
            "port": sandbox_config.port or DEFAULT_PORT,
            "container_prefix": sandbox_config.container_prefix or DEFAULT_CONTAINER_PREFIX,
            "idle_timeout": idle_timeout if idle_timeout is not None else DEFAULT_IDLE_TIMEOUT,
            "replicas": replicas if replicas is not None else DEFAULT_REPLICAS,
            "mounts": sandbox_config.mounts or [],
            "environment": self._resolve_env_vars(sandbox_config.environment or {}),
            # F2（2026-09-29）：全局资源档（mem/shm）。env 仍在 local_backend 侧兜底
            # 优先（SANDBOX_MEM_LIMIT / SANDBOX_SHM_SIZE），此处只透传 config 值。
            # 注意：_load_config 仅在 __init__ 调用一次——改 config 须重启 gateway。
            "mem_limit": sandbox_config.mem_limit,
            "shm_size": sandbox_config.shm_size,
            # provisioner URL for dynamic pod management (e.g. http://provisioner:8002)
            "provisioner_url": getattr(sandbox_config, "provisioner_url", None) or "",
        }

    @staticmethod
    def _resolve_env_vars(env_config: dict[str, str]) -> dict[str, str]:
        """Resolve environment variable references (values starting with $)."""
        resolved = {}
        for key, value in env_config.items():
            if isinstance(value, str) and value.startswith("$"):
                env_name = value[1:]
                resolved[key] = os.environ.get(env_name, "")
            else:
                resolved[key] = str(value)
        return resolved

    # ── Startup reconciliation ────────────────────────────────────────────

    def _reconcile_orphans(self) -> None:
        """Reconcile orphaned containers left by previous process lifecycles.

        On startup, enumerate all running containers matching our prefix
        and adopt them all into the warm pool.  The idle checker will reclaim
        containers that nobody re-acquires within ``idle_timeout``.

        All containers are adopted unconditionally because we cannot
        distinguish "orphaned" from "actively used by another process"
        based on age alone — ``idle_timeout`` represents inactivity, not
        uptime.  Adopting into the warm pool and letting the idle checker
        decide avoids destroying containers that a concurrent process may
        still be using.

        This closes the fundamental gap where in-memory state loss (process
        restart, crash, SIGKILL) leaves Docker containers running forever.
        """
        try:
            running = self._backend.list_running()
        except Exception as e:
            logger.warning(f"Failed to enumerate running containers during startup reconciliation: {e}")
            return

        if not running:
            return

        current_time = time.time()
        adopted = 0

        for info in running:
            age = current_time - info.created_at if info.created_at > 0 else float("inf")
            # Single lock acquisition per container: atomic check-and-insert.
            # Avoids a TOCTOU window between the "already tracked?" check and
            # the warm-pool insert.
            with self._lock:
                if info.sandbox_id in self._sandboxes or info.sandbox_id in self._warm_pool:
                    continue
                self._warm_pool[info.sandbox_id] = (info, current_time)
                # Startup-adopted entries have unknown identity until their
                # first reclaim: None means "no warm identity to collide with".
                self._warm_pool_identity[info.sandbox_id] = None
            adopted += 1
            logger.info(f"Adopted container {info.sandbox_id} into warm pool (age: {age:.0f}s)")

        logger.info(f"Startup reconciliation complete: {adopted} adopted into warm pool, {len(running)} total found")

    # ── Deterministic ID ─────────────────────────────────────────────────

    @staticmethod
    def _effective_acquire_user_id(user_id: str | None) -> str:
        """P3 已知退化（评审登记）：无认证部署下 ``get_effective_user_id()``
        兜底 ``"default"``——全部匿名流量共用同一 user 桶，S1 的身份隔离退化
        为 thread 级。若未来需要匿名隔离，须引入会话级匿名标识，而非沿用
        全局 default 桶。
        """
        return user_id or get_effective_user_id()

    @staticmethod
    def _thread_key(thread_id: str, user_id: str) -> tuple[str, str]:
        return (user_id, thread_id)

    @staticmethod
    def _deterministic_sandbox_id(thread_id: str, user_id: str) -> str:
        """Generate a deterministic sandbox ID from user/thread scope.

        Includes user_id so a previously-created default-bucket sandbox cannot be
        reused for an auth/channel run that should mount a user-scoped bucket
        (B1: same thread_id, different user → different sandbox).

        During a mixed-version rollout, older 8-character containers are not
        reused under the new 16-character identity. They remain eligible for
        normal orphan cleanup while the first new-version acquire cold-starts.
        """
        return derive_sandbox_scope_token(user_id=user_id, thread_id=thread_id)

    def _assert_active_identity_available_locked(
        self,
        sandbox_id: str,
        requested_key: tuple[str, str],
    ) -> None:
        """Fail closed if an active ID belongs to a different identity."""
        if sandbox_id not in self._sandboxes and sandbox_id not in self._sandbox_infos:
            return

        stored_key = self._active_sandbox_identity.get(sandbox_id)
        if stored_key is None:
            matching_keys = [key for key, mapped_id in self._thread_sandboxes.items() if mapped_id == sandbox_id]
            # 仅在**恰好一个**映射时反推身份；零或多映射 → stored_key 保持
            # None → 下方不等比较必然触发碰撞异常（fail-closed，评审已核：
            # 多映射不会"取第一项"，上游逐字同形）。
            if len(matching_keys) == 1:
                stored_key = matching_keys[0]
        if stored_key != requested_key:
            raise SandboxIdentityCollisionError(sandbox_id, stored_key, requested_key)

    def _assert_warm_identity_available_locked(
        self,
        sandbox_id: str,
        requested_key: tuple[str, str],
    ) -> None:
        """Fail closed if a warm ID changed tenants during an acquire."""
        if sandbox_id not in self._warm_pool:
            return
        # Startup-adopted entries have unknown identity until their first reclaim.
        stored_key = self._warm_pool_identity.get(sandbox_id)
        if stored_key is not None and stored_key != requested_key:
            raise SandboxIdentityCollisionError(sandbox_id, stored_key, requested_key)

    # ── Mount helpers ────────────────────────────────────────────────────

    def _get_extra_mounts(self, thread_id: str | None, *, user_id: str | None = None) -> list[tuple[str, str, bool]]:
        """Collect all extra mounts for a sandbox (thread-specific + skills).

        ``user_id`` is the acquire-time resolved user: passing it keeps the
        mounted data dirs on the same user the sandbox ID was derived from,
        even if the contextvar has changed between acquire and create.
        """
        mounts: list[tuple[str, str, bool]] = []

        if thread_id:
            mounts.extend(self._get_thread_mounts(thread_id, user_id=user_id))
            logger.info(f"Adding thread mounts for thread {thread_id}: {mounts}")

        skills_mount = self._get_skills_mount()
        if skills_mount:
            mounts.append(skills_mount)
            logger.info(f"Adding skills mount: {skills_mount}")

        # P1-S3 双层挂载：本用户 custom（遮蔽全局 custom）+ legacy 兜底视图。
        user_skills_mounts = self._get_user_skills_mounts(user_id)
        if user_skills_mounts:
            mounts.extend(user_skills_mounts)
            logger.info(f"Adding user skills mounts for {user_id}: {user_skills_mounts}")

        return mounts

    @staticmethod
    def _get_thread_mounts(thread_id: str, *, user_id: str | None = None) -> list[tuple[str, str, bool]]:
        """Get volume mounts for a thread's data directories.

        Creates directories if they don't exist (lazy initialization).
        Mount sources use host_base_dir so that when running inside Docker with a
        mounted Docker socket (DooD), the host Docker daemon can resolve the paths.
        """
        paths = get_paths()
        resolved_user_id = user_id or get_effective_user_id()
        paths.ensure_thread_dirs(thread_id, user_id=resolved_user_id)
        user_id = resolved_user_id

        return [
            (paths.host_sandbox_work_dir(thread_id, user_id=user_id), f"{VIRTUAL_PATH_PREFIX}/workspace", False),
            (paths.host_sandbox_uploads_dir(thread_id, user_id=user_id), f"{VIRTUAL_PATH_PREFIX}/uploads", False),
            (paths.host_sandbox_outputs_dir(thread_id, user_id=user_id), f"{VIRTUAL_PATH_PREFIX}/outputs", False),
            # ACP workspace: read-only inside the sandbox (lead agent reads results;
            # the ACP subprocess writes from the host side, not from within the container).
            (paths.host_acp_workspace_dir(thread_id, user_id=user_id), "/mnt/acp-workspace", True),
        ]

    @staticmethod
    def _get_skills_mount() -> tuple[str, str, bool] | None:
        """Get the skills directory mount configuration.

        Mount source uses DEER_FLOW_HOST_SKILLS_PATH when running inside Docker (DooD)
        so the host Docker daemon can resolve the path.
        """
        try:
            config = get_app_config()
            skills_path = config.skills.get_skills_path()
            container_path = config.skills.container_path

            if skills_path.exists():
                # When running inside Docker with DooD, use host-side skills path.
                host_skills = os.environ.get("DEER_FLOW_HOST_SKILLS_PATH") or str(skills_path)
                return (host_skills, container_path, True)  # Read-only for security
        except Exception as e:
            logger.warning(f"Could not setup skills mount: {e}")
        return None

    @staticmethod
    def _get_user_skills_mounts(user_id: str | None) -> list[tuple[str, str, bool]]:
        """P1-S3（2026-09-30 上游移植，修 B5）：双层技能挂载。

        ① 全局根（public + 旧 custom）→ ``/mnt/skills``（只读，与此前一致）
        ② 本用户 custom 目录 → ``/mnt/skills/custom``（只读，嵌套 bind 遮蔽
           ① 里的旧 custom 子目录——用户 B 的技能不进用户 A 的沙箱；挂载点
           经 ① 解析到宿主 skills/custom，目录已存在故 mkdir 不需要）
        ③ 全局旧 custom → ``/mnt/skills-legacy``（只读兜底视图，迁移平滑）
        用户目录不存在时跳过（首装后下次容器启动即挂上）。

        ③ 必须挂顶层路径（DEF-1，2026-10-07 部署事故）：嵌套在只读 bind
        ``/mnt/skills`` 之内且宿主源无同名目录时，runc 需在只读 bind 里
        mkdir 挂载点 → EROFS → 沙箱容器创建必败。② 不受此限（见上）。
        """
        mounts: list[tuple[str, str, bool]] = []
        if not user_id:
            return mounts
        try:
            paths = get_paths()
            if paths.user_custom_skills_dir(user_id).exists():
                mounts.append((paths.host_user_custom_skills_dir(user_id), "/mnt/skills/custom", True))
            legacy_root = get_app_config().skills.get_skills_path() / "custom"
            if legacy_root.exists():
                # DooD：宿主侧技能根 + /custom（与全局挂载同一套 HOST_SKILLS 覆盖）。
                host_skills_root = os.environ.get("DEER_FLOW_HOST_SKILLS_PATH") or str(legacy_root.parent)
                mounts.append((f"{host_skills_root.rstrip('/')}/custom", "/mnt/skills-legacy", True))
        except Exception as e:
            logger.warning(f"Could not setup user skills mounts: {e}")
        return mounts

    # ── Idle timeout management (warm-pool mechanics via WarmPoolLifecycleMixin) ──

    def _active_count_locked(self) -> int:
        """Active entry count while ``_lock`` is held (mixin hook)."""
        return len(self._sandboxes)

    def _destroy_warm_entry(self, sandbox_id: str, entry: SandboxInfo, *, reason: str) -> None:
        """Destroy a warm-pool entry after its removal from the pool (mixin hook).

        The entry is already gone from ``_warm_pool`` when this runs, so no
        reclaim can find it in between; dropping the identity record here is
        race-free by the same argument.
        """
        with self._lock:
            self._warm_pool_identity.pop(sandbox_id, None)
        try:
            self._backend.destroy(entry)
            if reason == "idle_timeout":
                logger.info(f"Destroyed idle warm-pool sandbox {sandbox_id}")
            else:
                logger.info(f"Destroyed warm-pool sandbox {sandbox_id} ({reason})")
        except Exception as e:
            logger.error(f"Failed to destroy warm-pool sandbox {sandbox_id} ({reason}): {e}")

    def _cleanup_idle_resources(self, idle_timeout: float) -> None:
        """One idle-checker tick: active-idle destroy + warm-pool expiry."""
        self._cleanup_idle_sandboxes(idle_timeout)

    def _check_tracked_sandbox_alive(self, sandbox_id: str, info: SandboxInfo) -> bool | None:
        """Return whether a tracked sandbox appears alive, or None if unknown.

        对齐上游形态（P1-S1 B8 收窄）：探测失败返回 None（未知），只有
        明确 False 才判死——未知不阻断晋升。
        """
        try:
            return self._backend.is_alive(info)
        except Exception as e:
            logger.warning(f"Failed to check sandbox {sandbox_id} health: {e}")
            return None

    def _destroy_dead_warm_entry(self, sandbox_id: str, info: SandboxInfo) -> None:
        """Destroy a warm entry whose health check definitively failed.

        Called after the entry has already been popped from ``_warm_pool``;
        a racing same-id registration between pop and here would point at the
        SAME underlying container (deterministic id), so no map surgery —
        just stop the dead container and let the caller fall through to
        discovery/create.
        """
        try:
            self._backend.destroy(info)
            logger.info(f"Dropped dead warm-pool sandbox {sandbox_id} (health check failed); falling through to create")
        except Exception as e:
            logger.warning(f"Failed to destroy dead warm-pool sandbox {sandbox_id}: {e}")

    def _cleanup_idle_sandboxes(self, idle_timeout: float) -> None:
        current_time = time.time()
        active_to_destroy = []

        with self._lock:
            # Active sandboxes: tracked via _last_activity
            for sandbox_id, last_activity in self._last_activity.items():
                idle_duration = current_time - last_activity
                if idle_duration > idle_timeout:
                    active_to_destroy.append(sandbox_id)
                    logger.info(f"Sandbox {sandbox_id} idle for {idle_duration:.1f}s, marking for destroy")

        # Destroy active sandboxes (re-verify still idle before acting)
        for sandbox_id in active_to_destroy:
            try:
                # Re-verify the sandbox is still idle under the lock before destroying.
                # Between the snapshot above and here, the sandbox may have been
                # re-acquired (last_activity updated) or already released/destroyed.
                with self._lock:
                    last_activity = self._last_activity.get(sandbox_id)
                    if last_activity is None:
                        # Already released or destroyed by another path — skip.
                        logger.info(f"Sandbox {sandbox_id} already gone before idle destroy, skipping")
                        continue
                    if (time.time() - last_activity) < idle_timeout:
                        # Re-acquired (activity updated) since the snapshot — skip.
                        logger.info(f"Sandbox {sandbox_id} was re-acquired before idle destroy, skipping")
                        continue
                logger.info(f"Destroying idle sandbox {sandbox_id}")
                self.destroy(sandbox_id)
            except Exception as e:
                logger.error(f"Failed to destroy idle sandbox {sandbox_id}: {e}")

        # Warm pool: expiry/destroy handled by the shared mixin (P1-S1 B8
        # narrowing — same recycle form as upstream).
        self._reap_expired_warm(idle_timeout)

    # ── Signal handling ──────────────────────────────────────────────────

    def _register_signal_handlers(self) -> None:
        """Register signal handlers for graceful shutdown.

        Handles SIGTERM, SIGINT, and SIGHUP (terminal close) to ensure
        sandbox containers are cleaned up even when the user closes the terminal.
        """
        self._original_sigterm = signal.getsignal(signal.SIGTERM)
        self._original_sigint = signal.getsignal(signal.SIGINT)
        self._original_sighup = signal.getsignal(signal.SIGHUP) if hasattr(signal, "SIGHUP") else None

        def signal_handler(signum, frame):
            self.shutdown()
            if signum == signal.SIGTERM:
                original = self._original_sigterm
            elif hasattr(signal, "SIGHUP") and signum == signal.SIGHUP:
                original = self._original_sighup
            else:
                original = self._original_sigint
            if callable(original):
                original(signum, frame)
            elif original == signal.SIG_DFL:
                signal.signal(signum, signal.SIG_DFL)
                signal.raise_signal(signum)

        try:
            signal.signal(signal.SIGTERM, signal_handler)
            signal.signal(signal.SIGINT, signal_handler)
            if hasattr(signal, "SIGHUP"):
                signal.signal(signal.SIGHUP, signal_handler)
        except ValueError:
            logger.debug("Could not register signal handlers (not main thread)")

    # ── Thread locking (in-process) ──────────────────────────────────────

    def _get_thread_lock(self, key: tuple[str, str]) -> threading.Lock:
        """Get or create an in-process lock for a specific ``(user_id, thread_id)`` key.

        P1-S1 起锁键 = 身份元组（同 thread 不同 user 不互斥）。参数名沿用上游
        ``thread_id`` 语义已漂移，故更名 ``key``。
        Reference-counted (minimal stand-in for upstream bb75f8d7): each entry
        is ``[lock, refcount]``; every acquisition bumps the refcount and the
        paired :meth:`_release_thread_lock` drops the entry at zero, so the
        dict no longer grows monotonically with distinct identity keys.
        """
        with self._lock:
            entry = self._thread_locks.get(key)
            if entry is None:
                entry = [threading.Lock(), 0]
                self._thread_locks[key] = entry
            entry[1] += 1
            return entry[0]

    def _release_thread_lock(self, key: tuple[str, str]) -> None:
        """Drop one reference to an identity-key lock, removing the entry at zero."""
        with self._lock:
            entry = self._thread_locks.get(key)
            if entry is None:
                return
            entry[1] -= 1
            if entry[1] <= 0:
                del self._thread_locks[key]

    # ── Core: acquire / get / release / shutdown ─────────────────────────

    def acquire(self, thread_id: str | None = None, *, user_id: str | None = None) -> str:
        """Acquire a sandbox environment and return its ID.

        For the same (user_id, thread_id) scope, this method will return the
        same sandbox_id across multiple turns, multiple processes, and (with
        shared storage) multiple pods. Different users on the same thread_id
        get different sandboxes (B1).

        Thread-safe with both in-process and cross-process locking.

        Args:
            thread_id: Optional thread ID for thread-specific configurations.
            user_id: Optional explicit user scope; resolved from the current
                user context when omitted.

        Returns:
            The ID of the acquired sandbox environment.
        """
        effective_user_id = self._effective_acquire_user_id(user_id)
        if thread_id:
            key = self._thread_key(thread_id, effective_user_id)
            thread_lock = self._get_thread_lock(key)
            try:
                with thread_lock:
                    return self._acquire_internal(thread_id, user_id=effective_user_id)
            finally:
                self._release_thread_lock(key)
        else:
            return self._acquire_internal(thread_id, user_id=effective_user_id)

    def _acquire_internal(self, thread_id: str | None, *, user_id: str) -> str:
        """Internal sandbox acquisition with two-layer consistency.

        Layer 1: In-process cache (fastest, covers same-process repeated access)
        Layer 2: Backend discovery (covers containers started by other processes;
                 sandbox_id is deterministic from (user_id, thread_id) so no shared
                 state file is needed — any process can derive the same name)
        """
        # ── Layer 1: In-process cache (fast path) ──
        if thread_id:
            key = self._thread_key(thread_id, user_id)
            with self._lock:
                if key in self._thread_sandboxes:
                    existing_id = self._thread_sandboxes[key]
                    if existing_id in self._sandboxes:
                        logger.info(f"Reusing in-process sandbox {existing_id} for user/thread {user_id}/{thread_id}")
                        self._last_activity[existing_id] = time.time()
                        return existing_id
                    else:
                        del self._thread_sandboxes[key]

        # Deterministic ID for thread-specific, random for anonymous
        sandbox_id = self._deterministic_sandbox_id(thread_id, user_id) if thread_id else str(uuid.uuid4())[:8]

        if thread_id:
            key = self._thread_key(thread_id, user_id)
            # Fail closed if this truncated ID is already active for another
            # identity (cross-tenant overwrite, B1).
            with self._lock:
                self._assert_active_identity_available_locked(sandbox_id, key)

        # ── Layer 1.5: Warm pool (container still running, no cold-start) ──
        if thread_id:
            warm_info = None
            with self._lock:
                if sandbox_id in self._warm_pool:
                    self._assert_warm_identity_available_locked(sandbox_id, key)
                    warm_info, _ = self._warm_pool.pop(sandbox_id)
                    self._warm_pool_identity.pop(sandbox_id, None)

            if warm_info is not None:
                # B8（P1-S1 收窄）：温池晋升前先健康检查——探测是后端调用，
                # 必须在锁外做。死容器就地销毁并回落到发现/创建路径，而不是
                # 把指向死容器的 client 交给 agent。
                if self._check_tracked_sandbox_alive(sandbox_id, warm_info) is False:
                    self._destroy_dead_warm_entry(sandbox_id, warm_info)
                else:
                    sandbox = AioSandbox(id=sandbox_id, base_url=warm_info.sandbox_url)
                    with self._lock:
                        self._sandboxes[sandbox_id] = sandbox
                        self._sandbox_infos[sandbox_id] = warm_info
                        self._last_activity[sandbox_id] = time.time()
                        self._active_sandbox_identity[sandbox_id] = key
                        self._thread_sandboxes[key] = sandbox_id
                    logger.info(f"Reclaimed warm-pool sandbox {sandbox_id} for user/thread {user_id}/{thread_id} at {warm_info.sandbox_url}")
                    return sandbox_id

        # ── Layer 2: Backend discovery + create (protected by cross-process lock) ──
        # Use a file lock so that two processes racing to create the same sandbox
        # for the same thread_id serialize here: the second process will discover
        # the container started by the first instead of hitting a name-conflict.
        if thread_id:
            return self._discover_or_create_with_lock(thread_id, sandbox_id, user_id=user_id)

        return self._create_sandbox(thread_id, sandbox_id, user_id=user_id)

    def _discover_or_create_with_lock(self, thread_id: str, sandbox_id: str, *, user_id: str | None = None) -> str:
        """Discover an existing sandbox or create a new one under a cross-process file lock.

        The file lock serializes concurrent sandbox creation for the same thread_id
        across multiple processes, preventing container-name conflicts.
        """
        paths = get_paths()
        effective_user_id = self._effective_acquire_user_id(user_id)
        paths.ensure_thread_dirs(thread_id, user_id=effective_user_id)
        lock_path = paths.thread_dir(thread_id, user_id=effective_user_id) / f"{sandbox_id}.lock"
        key = self._thread_key(thread_id, effective_user_id)

        with open(lock_path, "a", encoding="utf-8") as lock_file:
            locked = False
            try:
                _lock_file_exclusive(lock_file)
                locked = True
                # Re-check in-process caches under the file lock in case another
                # thread in this process won the race while we were waiting.
                warm_info = None
                with self._lock:
                    if key in self._thread_sandboxes:
                        existing_id = self._thread_sandboxes[key]
                        if existing_id in self._sandboxes:
                            logger.info(f"Reusing in-process sandbox {existing_id} for user/thread {effective_user_id}/{thread_id} (post-lock check)")
                            self._last_activity[existing_id] = time.time()
                            return existing_id
                    if sandbox_id in self._warm_pool:
                        self._assert_warm_identity_available_locked(sandbox_id, key)
                        warm_info, _ = self._warm_pool.pop(sandbox_id)
                        self._warm_pool_identity.pop(sandbox_id, None)

                if warm_info is not None:
                    # Same B8 health-check form as the pre-lock warm branch.
                    if self._check_tracked_sandbox_alive(sandbox_id, warm_info) is False:
                        self._destroy_dead_warm_entry(sandbox_id, warm_info)
                    else:
                        sandbox = AioSandbox(id=sandbox_id, base_url=warm_info.sandbox_url)
                        with self._lock:
                            self._sandboxes[sandbox_id] = sandbox
                            self._sandbox_infos[sandbox_id] = warm_info
                            self._last_activity[sandbox_id] = time.time()
                            self._active_sandbox_identity[sandbox_id] = key
                            self._thread_sandboxes[key] = sandbox_id
                        logger.info(f"Reclaimed warm-pool sandbox {sandbox_id} for user/thread {effective_user_id}/{thread_id} (post-lock check)")
                        return sandbox_id

                # Backend discovery: another process may have created the container.
                discovered = self._backend.discover(sandbox_id)
                if discovered is not None:
                    with self._lock:
                        self._assert_active_identity_available_locked(discovered.sandbox_id, key)
                        self._assert_warm_identity_available_locked(discovered.sandbox_id, key)
                        sandbox = AioSandbox(id=discovered.sandbox_id, base_url=discovered.sandbox_url)
                        self._sandboxes[discovered.sandbox_id] = sandbox
                        self._sandbox_infos[discovered.sandbox_id] = discovered
                        self._last_activity[discovered.sandbox_id] = time.time()
                        self._active_sandbox_identity[discovered.sandbox_id] = key
                        self._thread_sandboxes[key] = discovered.sandbox_id
                    logger.info(f"Discovered existing sandbox {discovered.sandbox_id} for user/thread {effective_user_id}/{thread_id} at {discovered.sandbox_url}")
                    return discovered.sandbox_id

                return self._create_sandbox(thread_id, sandbox_id, user_id=effective_user_id)
            finally:
                if locked:
                    _unlock_file(lock_file)

    # _evict_oldest_warm / _replica_count / _reap_expired_warm / idle-checker
    # thread management come from WarmPoolLifecycleMixin (P1-S1 B8 narrowing).

    def _create_sandbox(self, thread_id: str | None, sandbox_id: str, *, user_id: str | None = None) -> str:
        """Create a new sandbox via the backend.

        Args:
            thread_id: Optional thread ID.
            sandbox_id: The sandbox ID to use.

        Returns:
            The sandbox_id.

        Raises:
            RuntimeError: If sandbox creation or readiness check fails.
            DiskAdmissionBlocked: If the host disk is below the admission
                threshold (C2, 2026-09-23 — container mode only by construction).
        """
        # C2（DEF-04，2026-09-23 批 1）：沙箱创建前磁盘准入——本路径天然只在
        # 容器模式执行。一次 disk_usage 探测，低于门槛直接拒绝并给出中文报错。
        # 取值用直接属性访问（review #9）：字段由 AppConfig schema 保证存在，
        # getattr 静默兜 0 会在字段改名时无声禁用 block 档。
        from deerflow.sandbox.disk_admission import check_disk_admission

        app_config = get_app_config()
        check_disk_admission(
            get_paths().base_dir,
            block_bytes=app_config.disk_min_free_bytes_block,
            warn_ratio=app_config.disk_warn_free_ratio,
            label="沙箱创建",
        )

        effective_user_id = self._effective_acquire_user_id(user_id)
        key = self._thread_key(thread_id, effective_user_id) if thread_id else None

        extra_mounts = self._get_extra_mounts(thread_id, user_id=effective_user_id)

        # Enforce replicas: only warm-pool containers count toward eviction budget.
        # Active sandboxes are in use by live threads and must not be forcibly stopped.
        replicas, total = self._replica_count()
        if total >= replicas:
            evicted = self._evict_oldest_warm()
            self._log_replicas_soft_cap(replicas, sandbox_id, evicted)

        info = self._backend.create(thread_id, sandbox_id, extra_mounts=extra_mounts or None)

        # Wait for sandbox to be ready
        if not wait_for_sandbox_ready(info.sandbox_url, timeout=60):
            self._backend.destroy(info)
            # C1（DEF-04，2026-09-23 批 1）：就绪探测超时 = 执行环境死亡，固定
            # 形态直接打标——acquire 路径的异常文本与 execute_command 的源头
            # 打标共用同一机器可读标记，熔断中间件只认它。
            raise RuntimeError(f"{SANDBOX_DEAD_MARKER} Sandbox {sandbox_id} failed to become ready within timeout at {info.sandbox_url}")

        sandbox = AioSandbox(id=sandbox_id, base_url=info.sandbox_url)
        with self._lock:
            if key is not None:
                self._assert_active_identity_available_locked(sandbox_id, key)
                self._assert_warm_identity_available_locked(sandbox_id, key)
            self._sandboxes[sandbox_id] = sandbox
            self._sandbox_infos[sandbox_id] = info
            self._last_activity[sandbox_id] = time.time()
            self._active_sandbox_identity[sandbox_id] = key
            if key is not None:
                self._thread_sandboxes[key] = sandbox_id

        logger.info(f"Created sandbox {sandbox_id} for user/thread {effective_user_id}/{thread_id} at {info.sandbox_url}")
        return sandbox_id

    def get(self, sandbox_id: str) -> Sandbox | None:
        """Get a sandbox by ID. Updates last activity timestamp.

        Args:
            sandbox_id: The ID of the sandbox.

        Returns:
            The sandbox instance if found, None otherwise.
        """
        with self._lock:
            sandbox = self._sandboxes.get(sandbox_id)
            if sandbox is not None:
                self._last_activity[sandbox_id] = time.time()
            return sandbox

    def release(self, sandbox_id: str) -> None:
        """Release a sandbox from active use into the warm pool.

        The container is kept running so it can be reclaimed quickly by the same
        thread on its next turn without a cold-start.  The container will only be
        stopped when the replicas limit forces eviction or during shutdown.

        Args:
            sandbox_id: The ID of the sandbox to release.
        """
        info = None
        thread_keys_to_remove: list[tuple[str, str]] = []

        with self._lock:
            self._sandboxes.pop(sandbox_id, None)
            info = self._sandbox_infos.pop(sandbox_id, None)
            active_identity = self._active_sandbox_identity.pop(sandbox_id, None)
            thread_keys_to_remove = [key for key, sid in self._thread_sandboxes.items() if sid == sandbox_id]
            for key in thread_keys_to_remove:
                del self._thread_sandboxes[key]
            self._last_activity.pop(sandbox_id, None)
            # Park in warm pool — container keeps running
            if info and sandbox_id not in self._warm_pool:
                self._warm_pool[sandbox_id] = (info, time.time())
                # Carry the releasing identity into the warm entry so a
                # different-tenant acquire of the same truncated ID fails
                # closed instead of silently reusing the container.
                self._warm_pool_identity[sandbox_id] = thread_keys_to_remove[0] if thread_keys_to_remove else active_identity

        logger.info(f"Released sandbox {sandbox_id} to warm pool (container still running)")

    def destroy(self, sandbox_id: str) -> None:
        """Destroy a sandbox: stop the container and free all resources.

        Unlike release(), this actually stops the container.  Use this for
        explicit cleanup, capacity-driven eviction, or shutdown.

        Args:
            sandbox_id: The ID of the sandbox to destroy.
        """
        info = None
        thread_keys_to_remove: list[tuple[str, str]] = []

        with self._lock:
            self._sandboxes.pop(sandbox_id, None)
            info = self._sandbox_infos.pop(sandbox_id, None)
            self._active_sandbox_identity.pop(sandbox_id, None)
            thread_keys_to_remove = [key for key, sid in self._thread_sandboxes.items() if sid == sandbox_id]
            for key in thread_keys_to_remove:
                del self._thread_sandboxes[key]
            self._last_activity.pop(sandbox_id, None)
            # Also pull from warm pool if it was parked there
            if info is None and sandbox_id in self._warm_pool:
                info, _ = self._warm_pool.pop(sandbox_id)
            else:
                self._warm_pool.pop(sandbox_id, None)
            self._warm_pool_identity.pop(sandbox_id, None)

        if info:
            self._backend.destroy(info)
            logger.info(f"Destroyed sandbox {sandbox_id}")

    def shutdown(self) -> None:
        """Shutdown all sandboxes. Thread-safe and idempotent."""
        with self._lock:
            if self._shutdown_called:
                return
            self._shutdown_called = True
            sandbox_ids = list(self._sandboxes.keys())
            warm_items = list(self._warm_pool.items())
            self._warm_pool.clear()
            self._warm_pool_identity.clear()

        # Stop idle checker (mixin helper — join skipped when called from the
        # checker thread itself)
        self._stop_idle_checker()
        logger.info("Stopped idle checker thread")

        logger.info(f"Shutting down {len(sandbox_ids)} active + {len(warm_items)} warm-pool sandbox(es)")

        for sandbox_id in sandbox_ids:
            try:
                self.destroy(sandbox_id)
            except Exception as e:
                logger.error(f"Failed to destroy sandbox {sandbox_id} during shutdown: {e}")

        for sandbox_id, (info, _) in warm_items:
            try:
                self._backend.destroy(info)
                logger.info(f"Destroyed warm-pool sandbox {sandbox_id} during shutdown")
            except Exception as e:
                logger.error(f"Failed to destroy warm-pool sandbox {sandbox_id} during shutdown: {e}")
