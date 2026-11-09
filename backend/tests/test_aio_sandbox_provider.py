"""Tests for AioSandboxProvider mount helpers."""

import hashlib
import importlib
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from deerflow.config.paths import Paths, join_host_path

# CI 守卫：部分用例会经 provider 初始化读到本地 gitignored config.yaml
# （CI fresh checkout 不存在）——缺失时跳过，本地照常执行。同
# test_skills_custom_router.py 惯例。
_REAL_CONFIG_EXISTS = any((Path(__file__).resolve().parents[i] / "config.yaml").exists() for i in (1, 2))
requires_real_config = pytest.mark.skipif(not _REAL_CONFIG_EXISTS, reason="依赖本地 gitignored config.yaml，CI 跳过")

# ── ensure_thread_dirs ───────────────────────────────────────────────────────


def test_ensure_thread_dirs_creates_acp_workspace(tmp_path):
    """ACP workspace directory must be created alongside user-data dirs."""
    paths = Paths(base_dir=tmp_path)
    paths.ensure_thread_dirs("thread-1")

    assert (tmp_path / "threads" / "thread-1" / "user-data" / "workspace").exists()
    assert (tmp_path / "threads" / "thread-1" / "user-data" / "uploads").exists()
    assert (tmp_path / "threads" / "thread-1" / "user-data" / "outputs").exists()
    assert (tmp_path / "threads" / "thread-1" / "acp-workspace").exists()


def test_ensure_thread_dirs_acp_workspace_is_world_writable(tmp_path):
    """ACP workspace must be chmod 0o777 so the ACP subprocess can write into it."""
    paths = Paths(base_dir=tmp_path)
    paths.ensure_thread_dirs("thread-2")

    acp_dir = tmp_path / "threads" / "thread-2" / "acp-workspace"
    mode = oct(acp_dir.stat().st_mode & 0o777)
    assert mode == oct(0o777)


def test_host_thread_dir_rejects_invalid_thread_id(tmp_path):
    paths = Paths(base_dir=tmp_path)

    with pytest.raises(ValueError, match="Invalid thread_id"):
        paths.host_thread_dir("../escape")


# ── _thread_locks 引用计数（上游 bb75f8d7 的最小替代）───────────────────────


def _make_lock_provider():
    """构造仅含线程锁设施的最小 provider 实例（不起后台线程、不触沙箱后端）。"""
    import threading

    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    provider = aio_mod.AioSandboxProvider.__new__(aio_mod.AioSandboxProvider)
    provider._lock = threading.Lock()
    provider._thread_locks = {}
    return provider


def _lock_key(thread_id: str) -> tuple[str, str]:
    """与 acquire() 内部一致的身份键（P1-S1 起锁键 = (user_id, thread_id)）。"""
    from deerflow.runtime.user_context import get_effective_user_id

    return (get_effective_user_id(), thread_id)


def test_thread_lock_entry_removed_after_acquire(monkeypatch):
    """acquire 完成后对应身份键的锁条目须被清除（引用计数归零删条目）。"""
    provider = _make_lock_provider()
    monkeypatch.setattr(provider, "_acquire_internal", lambda thread_id, *, user_id=None: "sandbox-x")

    assert provider.acquire("thread-lock-1") == "sandbox-x"
    assert _lock_key("thread-lock-1") not in provider._thread_locks


def test_thread_lock_entry_alive_while_held(monkeypatch):
    """持锁期间条目存活；锁释放后才从 _thread_locks 移除。"""
    import threading

    provider = _make_lock_provider()
    entered = threading.Event()
    finish = threading.Event()

    def _blocking_acquire(thread_id, *, user_id=None):
        entered.set()
        assert finish.wait(timeout=10)
        return "sandbox-y"

    monkeypatch.setattr(provider, "_acquire_internal", _blocking_acquire)

    holder = threading.Thread(target=provider.acquire, args=("thread-lock-2",))
    holder.start()
    try:
        assert entered.wait(timeout=10)
        # 持锁期间：条目存在且锁处于持有态。工作线程无 user contextvar，
        # 身份键的用户段是 DEFAULT_USER_ID——断言取实际条目而非猜键。
        assert len(provider._thread_locks) == 1
        lock_entry = next(iter(provider._thread_locks.values()))
        assert lock_entry[0].locked()
    finally:
        finish.set()
        holder.join(timeout=10)

    assert not provider._thread_locks


def _make_provider(tmp_path):
    """Build a minimal AioSandboxProvider instance without starting the idle checker."""
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    with patch.object(aio_mod.AioSandboxProvider, "_start_idle_checker"):
        provider = aio_mod.AioSandboxProvider.__new__(aio_mod.AioSandboxProvider)
        provider._config = {}
        provider._sandboxes = {}
        provider._lock = MagicMock()
        provider._idle_checker_stop = MagicMock()
    return provider


def test_get_thread_mounts_includes_acp_workspace(tmp_path, monkeypatch):
    """_get_thread_mounts must include /mnt/acp-workspace (read-only) for docker sandbox."""
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    monkeypatch.setattr(aio_mod, "get_paths", lambda: Paths(base_dir=tmp_path))
    monkeypatch.setattr(aio_mod, "get_effective_user_id", lambda: None)

    mounts = aio_mod.AioSandboxProvider._get_thread_mounts("thread-3")

    container_paths = {m[1]: (m[0], m[2]) for m in mounts}

    assert "/mnt/acp-workspace" in container_paths, "ACP workspace mount is missing"
    expected_host = str(tmp_path / "threads" / "thread-3" / "acp-workspace")
    actual_host, read_only = container_paths["/mnt/acp-workspace"]
    assert actual_host == expected_host
    assert read_only is True, "ACP workspace should be read-only inside the sandbox"


def test_get_thread_mounts_includes_user_data_dirs(tmp_path, monkeypatch):
    """Baseline: user-data mounts must still be present after the ACP workspace change."""
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    monkeypatch.setattr(aio_mod, "get_paths", lambda: Paths(base_dir=tmp_path))

    mounts = aio_mod.AioSandboxProvider._get_thread_mounts("thread-4")
    container_paths = {m[1] for m in mounts}

    assert "/mnt/user-data/workspace" in container_paths
    assert "/mnt/user-data/uploads" in container_paths
    assert "/mnt/user-data/outputs" in container_paths


def test_join_host_path_preserves_windows_drive_letter_style():
    base = r"C:\Users\demo\deer-flow\backend\.deer-flow"

    joined = join_host_path(base, "threads", "thread-9", "user-data", "outputs")

    assert joined == r"C:\Users\demo\deer-flow\backend\.deer-flow\threads\thread-9\user-data\outputs"


def test_get_thread_mounts_preserves_windows_host_path_style(tmp_path, monkeypatch):
    """Docker bind mount sources must keep Windows-style paths intact."""
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    monkeypatch.setenv("DEER_FLOW_HOST_BASE_DIR", r"C:\Users\demo\deer-flow\backend\.deer-flow")
    monkeypatch.setattr(aio_mod, "get_paths", lambda: Paths(base_dir=tmp_path))
    monkeypatch.setattr(aio_mod, "get_effective_user_id", lambda: None)

    mounts = aio_mod.AioSandboxProvider._get_thread_mounts("thread-10")

    container_paths = {container_path: host_path for host_path, container_path, _ in mounts}

    assert container_paths["/mnt/user-data/workspace"] == r"C:\Users\demo\deer-flow\backend\.deer-flow\threads\thread-10\user-data\workspace"
    assert container_paths["/mnt/user-data/uploads"] == r"C:\Users\demo\deer-flow\backend\.deer-flow\threads\thread-10\user-data\uploads"
    assert container_paths["/mnt/user-data/outputs"] == r"C:\Users\demo\deer-flow\backend\.deer-flow\threads\thread-10\user-data\outputs"
    assert container_paths["/mnt/acp-workspace"] == r"C:\Users\demo\deer-flow\backend\.deer-flow\threads\thread-10\acp-workspace"


def test_discover_or_create_only_unlocks_when_lock_succeeds(tmp_path, monkeypatch):
    """Unlock should not run if exclusive locking itself fails."""
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    provider = _make_provider(tmp_path)
    provider._discover_or_create_with_lock = aio_mod.AioSandboxProvider._discover_or_create_with_lock.__get__(
        provider,
        aio_mod.AioSandboxProvider,
    )

    monkeypatch.setattr(aio_mod, "get_paths", lambda: Paths(base_dir=tmp_path))
    monkeypatch.setattr(
        aio_mod,
        "_lock_file_exclusive",
        lambda _lock_file: (_ for _ in ()).throw(RuntimeError("lock failed")),
    )

    unlock_calls: list[object] = []
    monkeypatch.setattr(
        aio_mod,
        "_unlock_file",
        lambda lock_file: unlock_calls.append(lock_file),
    )

    with patch.object(provider, "_create_sandbox", return_value="sandbox-id"):
        with pytest.raises(RuntimeError, match="lock failed"):
            provider._discover_or_create_with_lock("thread-5", "sandbox-5")

    assert unlock_calls == []


@requires_real_config
def test_create_sandbox_readiness_timeout_tags_sandbox_dead(monkeypatch):
    """R8（2026-09-24 批 2）：就绪超时 RuntimeError 必须以 [SANDBOX_DEAD] 前缀打标.

    C1（DEF-04）：acquire 路径与 execute_command 源头打标共用同一机器可读标记，
    熔断中间件只认它（R7 首行前缀匹配的路径②）。指纹矩阵四类命中
    （test_fingerprint_matrix 的 4 个 True 项）与此处共同构成死亡指纹全集。
    """
    from deerflow.community.aio_sandbox import aio_sandbox_provider as prov_mod
    from deerflow.community.aio_sandbox.aio_sandbox import SANDBOX_DEAD_MARKER

    provider = prov_mod.AioSandboxProvider()
    provider._config = {"replicas": 10}

    class _Info:
        sandbox_url = "http://127.0.0.1:8392"
        sandbox_id = "sb-timeout"

    destroyed: list[object] = []

    class _Backend:
        def create(self, thread_id, sandbox_id, extra_mounts=None):
            return _Info()

        def destroy(self, info):
            destroyed.append(info)

    provider._backend = _Backend()

    monkeypatch.setattr(prov_mod, "wait_for_sandbox_ready", lambda url, timeout: False)
    monkeypatch.setattr(prov_mod, "get_paths", lambda: Paths(base_dir=Path("/tmp")))
    import deerflow.sandbox.disk_admission as disk_admission_mod

    monkeypatch.setattr(
        disk_admission_mod,
        "check_disk_admission",
        lambda *a, **k: None,
    )
    # get_app_config 经 deerflow.config 聚合导入，provider 模块内是本地名
    monkeypatch.setattr(prov_mod, "get_app_config", lambda: type("C", (), {"disk_min_free_bytes_block": 0, "disk_warn_free_ratio": 0.15})())
    monkeypatch.setattr(provider, "_get_extra_mounts", lambda thread_id, *, user_id=None: None)

    with pytest.raises(RuntimeError) as exc_info:
        provider._create_sandbox("thread-r8", "sb-timeout")

    msg = str(exc_info.value)
    assert msg.startswith(SANDBOX_DEAD_MARKER), f"就绪超时未打 [SANDBOX_DEAD] 前缀: {msg!r}"
    assert "failed to become ready" in msg
    # 失败容器须回收（不留僵尸占 replicas 预算）
    assert len(destroyed) == 1


# ── 沙箱身份 user 化（P1-S1，2026-09-30 上游移植；上游 test_aio_sandbox_provider.py :2270/:2311/:2540）──

# 上游实测的旧 8-hex 派生式碰撞对：sha256(f"{user}:{thread}")[:8] 同值。
# 新 16-hex scope token 必须把这对分开。
_LEGACY_COLLIDING_IDENTITIES = (
    ("user-9721", "thread-9721"),
    ("user-94361", "thread-94361"),
)


def _make_tenant_isolation_provider(tmp_path, monkeypatch):
    """构造可跑完整 acquire 链的最小 provider（无后台线程、假后端）。

    对齐上游 _make_tenant_isolation_provider（上游文件 :2224），但按本仓
    provider 形态裁剪：本仓无 ownership store / teardown lease 机制，只保留
    身份双表（_active_sandbox_identity / _warm_pool_identity）与 acquire
    全链路所需的跟踪表。
    """
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    with patch.object(aio_mod.AioSandboxProvider, "_start_idle_checker"):
        provider = aio_mod.AioSandboxProvider.__new__(aio_mod.AioSandboxProvider)
    provider._lock = threading.Lock()
    provider._config = {"command_timeout": 600.0, "replicas": 3, "idle_timeout": 0}
    provider._sandboxes = {}
    provider._sandbox_infos = {}
    provider._thread_sandboxes = {}
    provider._thread_locks = {}
    provider._last_activity = {}
    provider._warm_pool = {}
    provider._active_sandbox_identity = {}
    provider._warm_pool_identity = {}
    provider._shutdown_called = False

    create_calls: list[tuple[str | None, str, str | None]] = []

    def _create(thread_id, sandbox_id, **kwargs):
        create_calls.append((thread_id, sandbox_id, kwargs.get("user_id")))
        return aio_mod.SandboxInfo(
            sandbox_id=sandbox_id,
            sandbox_url=f"http://sandbox-{len(create_calls)}.local",
            container_name=f"deer-flow-sandbox-{sandbox_id}",
        )

    provider._backend = SimpleNamespace(
        create=MagicMock(side_effect=_create),
        destroy=MagicMock(),
        discover=MagicMock(return_value=None),
        is_alive=MagicMock(return_value=True),
        list_running=MagicMock(return_value=[]),
    )

    monkeypatch.setattr(aio_mod, "get_paths", lambda: Paths(base_dir=tmp_path))
    monkeypatch.setattr(
        aio_mod.AioSandboxProvider,
        "_get_extra_mounts",
        lambda self, thread_id, *, user_id=None: [],
    )
    monkeypatch.setattr(
        aio_mod,
        "wait_for_sandbox_ready",
        lambda _url, timeout=60: True,
    )
    import deerflow.sandbox.disk_admission as disk_admission_mod

    monkeypatch.setattr(disk_admission_mod, "check_disk_admission", lambda *a, **k: None)
    monkeypatch.setattr(
        aio_mod,
        "get_app_config",
        lambda: type("C", (), {"disk_min_free_bytes_block": 0, "disk_warn_free_ratio": 0.15})(),
    )
    return provider, create_calls, aio_mod


def test_aio_wider_id_separates_known_legacy_collision():
    """16-hex scope token 必须分开旧 8-hex 派生式下碰撞的用户/线程对。"""
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    identity_a, identity_b = _LEGACY_COLLIDING_IDENTITIES
    user_a, thread_a = identity_a
    user_b, thread_b = identity_b

    old_a = hashlib.sha256(f"{user_a}:{thread_a}".encode()).hexdigest()[:8]
    old_b = hashlib.sha256(f"{user_b}:{thread_b}".encode()).hexdigest()[:8]

    assert old_a == old_b
    assert aio_mod.AioSandboxProvider._deterministic_sandbox_id(
        thread_a,
        user_a,
    ) != aio_mod.AioSandboxProvider._deterministic_sandbox_id(
        thread_b,
        user_b,
    )


def test_aio_forced_collision_never_overwrites_active_tenant(tmp_path, monkeypatch):
    """同 scope_token 不同身份 → fail-closed（SandboxIdentityCollisionError），温池条目原样保留。"""
    provider, create_calls, aio_mod = _make_tenant_isolation_provider(
        tmp_path,
        monkeypatch,
    )
    monkeypatch.setattr(
        aio_mod.AioSandboxProvider,
        "_deterministic_sandbox_id",
        staticmethod(lambda thread_id, user_id: "deadbeefdeadbeef"),
    )

    sandbox_id = provider.acquire("thread-a", user_id="user-a")
    info_a = provider._sandbox_infos[sandbox_id]
    provider.release(sandbox_id)

    assert sandbox_id in provider._warm_pool

    with pytest.raises(aio_mod.SandboxIdentityCollisionError):
        provider.acquire("thread-b", user_id="user-b")

    assert provider._warm_pool[sandbox_id][0] is info_a
    provider._backend.destroy.assert_not_called()
    assert len(create_calls) == 1
    assert provider.acquire("thread-a", user_id="user-a") == sandbox_id
    assert provider._sandbox_infos[sandbox_id] is info_a


def test_deterministic_sandbox_id_matches_shared_identity():
    """provider 的确定性 ID 必须逐字节等于共享 identity 模块的派生。"""
    from deerflow.sandbox.identity import derive_sandbox_scope_token

    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    assert aio_mod.AioSandboxProvider._deterministic_sandbox_id("t-1", "u-1") == derive_sandbox_scope_token(user_id="u-1", thread_id="t-1")


def test_acquire_same_thread_different_user_gets_different_sandbox(tmp_path, monkeypatch):
    """B1 验收：同 thread_id 不同 user 必须得到不同沙箱（不同容器命名）。"""
    provider, create_calls, aio_mod = _make_tenant_isolation_provider(
        tmp_path,
        monkeypatch,
    )

    id_a = provider.acquire("thread-shared", user_id="user-a")
    provider.release(id_a)
    id_b = provider.acquire("thread-shared", user_id="user-b")

    assert id_a != id_b
    assert len(create_calls) == 2
    # id_a 已 release：身份随温池走；id_b 在役：身份在 active 表
    assert provider._active_sandbox_identity[id_b] == ("user-b", "thread-shared")
    assert provider._thread_sandboxes[("user-b", "thread-shared")] == id_b
    assert provider._warm_pool_identity[id_a] == ("user-a", "thread-shared")


def test_user_skills_dual_layer_mount(tmp_path, monkeypatch):
    """P1-S3 双层挂载：全局根 + 本用户 custom（遮蔽）+ legacy 兜底视图（顶层挂载，DEF-1）。"""
    aio_mod = importlib.import_module("deerflow.community.aio_sandbox.aio_sandbox_provider")
    paths = Paths(base_dir=tmp_path)
    monkeypatch.setattr(aio_mod, "get_paths", lambda: paths)
    monkeypatch.setattr(aio_mod, "get_app_config", lambda: type("C", (), {"skills": type("S", (), {"get_skills_path": staticmethod(lambda: tmp_path / "skills")})()})())

    skills_public = tmp_path / "skills" / "public"
    skills_public.mkdir(parents=True)
    (tmp_path / "skills" / "custom").mkdir()

    with patch.object(aio_mod.AioSandboxProvider, "_start_idle_checker"):
        provider = aio_mod.AioSandboxProvider.__new__(aio_mod.AioSandboxProvider)

    # 无用户 custom 目录：不挂用户层
    mounts = provider._get_user_skills_mounts("user-a")
    assert [m[1] for m in mounts] == ["/mnt/skills-legacy"]

    # 有用户 custom 目录：双层齐挂
    user_custom = paths.user_custom_skills_dir("user-a")
    user_custom.mkdir(parents=True)
    mounts = provider._get_user_skills_mounts("user-a")
    assert [m[1] for m in mounts] == ["/mnt/skills/custom", "/mnt/skills-legacy"]
    assert mounts[0][0].endswith("users/user-a/skills/custom")
    assert all(ro is True for _, _, ro in mounts)

    # 无用户身份：零挂载（匿名沙箱不挂用户层）
    assert provider._get_user_skills_mounts(None) == []
