"""F1（2026-09-29 长任务中断修复计划）：沙箱容器 --shm-size 守卫。

_start_container 的 docker run 必须带 --shm-size=2g（对齐 remotion-worker 的
shm_size: 2g；缺省 64M 共享内存是 Chrome 多进程渲染崩溃 [高概率] 根因）。
优先级：env SANDBOX_SHM_SIZE（运维逃生口，空串=显式关闭）> config
SandboxConfig.shm_size > 内置默认 2g。Apple Container 路径不加（与 mem 块同口径）。
"""

from __future__ import annotations

import pytest


class _FakeProc:
    def __init__(self, rc: int = 0) -> None:
        self.returncode = rc
        self.stdout = b""
        self.stderr = b""


def _make_backend(runtime: str = "docker", shm_size=None):
    """Build a minimal LocalContainerBackend without triggering __init__ side effects.

    WARNING: This helper intentionally bypasses ``__init__`` via ``__new__`` so
    tests don't depend on Docker or touch real containers.  The downside is that
    this helper is tightly coupled to the set of attributes set up in
    ``LocalContainerBackend.__init__``.  If ``__init__`` gains a new attribute
    that ``_start_container`` (or other methods under test) reads, this helper
    must be updated in lockstep — otherwise tests will fail with a confusing
    ``AttributeError`` instead of a meaningful assertion failure.
    """
    from deerflow.community.aio_sandbox.local_backend import LocalContainerBackend

    provider = LocalContainerBackend.__new__(LocalContainerBackend)
    provider._runtime = runtime
    provider._environment = {"K": "V"}
    provider._config_mounts = []
    provider._image = "img:tag"
    provider._mem_limit = None
    provider._shm_size = shm_size
    return provider


def _capture_run(monkeypatch) -> list[str]:
    captured: list[str] = []

    def fake_run(cmd, *a, **kw):  # noqa: ANN001, ANN202
        if cmd[:2] == ["docker", "run"]:
            captured.extend(cmd)
            return _FakeProc()
        return _FakeProc()

    monkeypatch.setattr("subprocess.run", fake_run)
    return captured


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("SANDBOX_SHM_SIZE", raising=False)
    monkeypatch.delenv("SANDBOX_MEM_LIMIT", raising=False)


class TestSandboxShmSize:
    def test_docker_run_has_default_shm_size(self, monkeypatch):
        captured = _capture_run(monkeypatch)
        provider = _make_backend()
        provider._start_container("sbx-test", 8081, [])
        assert "--shm-size" in captured
        i = captured.index("--shm-size")
        assert captured[i + 1] == "2g"

    def test_env_override_respected(self, monkeypatch):
        monkeypatch.setenv("SANDBOX_SHM_SIZE", "1g")
        captured = _capture_run(monkeypatch)
        provider = _make_backend()
        provider._start_container("sbx-test", 8081, [])
        i = captured.index("--shm-size")
        assert captured[i + 1] == "1g"

    def test_env_empty_disables_shm_size(self, monkeypatch):
        """空串 = 显式关闭（回旧行为，运维逃生口）。"""
        monkeypatch.setenv("SANDBOX_SHM_SIZE", "")
        captured = _capture_run(monkeypatch)
        provider = _make_backend()
        provider._start_container("sbx-test", 8081, [])
        assert "--shm-size" not in captured

    def test_config_value_used_when_env_unset(self, monkeypatch):
        """F2：env 未设时 config 档位（构造器参数）生效。"""
        captured = _capture_run(monkeypatch)
        provider = _make_backend(shm_size="4g")
        provider._start_container("sbx-test", 8081, [])
        i = captured.index("--shm-size")
        assert captured[i + 1] == "4g"

    def test_env_wins_over_config(self, monkeypatch):
        """F2：env 优先于 config。"""
        monkeypatch.setenv("SANDBOX_SHM_SIZE", "3g")
        captured = _capture_run(monkeypatch)
        provider = _make_backend(shm_size="4g")
        provider._start_container("sbx-test", 8081, [])
        i = captured.index("--shm-size")
        assert captured[i + 1] == "3g"

    def test_empty_env_disables_even_when_config_set(self, monkeypatch):
        """空串 env = 显式关闭，压过 config 档位。"""
        monkeypatch.setenv("SANDBOX_SHM_SIZE", "")
        captured = _capture_run(monkeypatch)
        provider = _make_backend(shm_size="4g")
        provider._start_container("sbx-test", 8081, [])
        assert "--shm-size" not in captured

    def test_non_docker_runtime_has_no_shm_size(self, monkeypatch):
        """Apple Container 路径不加 shm（与 mem 块同口径）。"""
        captured = _capture_run(monkeypatch)
        provider = _make_backend(runtime="container")
        provider._start_container("sbx-test", 8081, [])
        assert "--shm-size" not in captured
        assert "--memory" not in captured


class TestProviderPassesResourceConfig:
    """F2：SandboxConfig.mem_limit/shm_size 经 provider 到达 LocalContainerBackend。"""

    def test_config_values_reach_backend(self, monkeypatch):
        from types import SimpleNamespace

        from deerflow.community.aio_sandbox import aio_sandbox_provider as provider_mod

        fake_sandbox = SimpleNamespace(
            image="img:tag",
            port=8080,
            container_prefix="pfx",
            idle_timeout=600,
            replicas=3,
            mounts=[],
            environment={},
            provisioner_url=None,
            mem_limit="6g",
            shm_size="4g",
        )
        monkeypatch.setattr(provider_mod, "get_app_config", lambda: SimpleNamespace(sandbox=fake_sandbox))

        provider = provider_mod.AioSandboxProvider.__new__(provider_mod.AioSandboxProvider)
        provider._config = provider._load_config()
        assert provider._config["mem_limit"] == "6g"
        assert provider._config["shm_size"] == "4g"

        backend = provider._create_backend()
        assert isinstance(backend, provider_mod.LocalContainerBackend)
        assert backend._mem_limit == "6g"
        assert backend._shm_size == "4g"

    def test_unset_config_fields_default_to_none(self, monkeypatch):
        from types import SimpleNamespace

        from deerflow.community.aio_sandbox import aio_sandbox_provider as provider_mod

        fake_sandbox = SimpleNamespace(
            image="img:tag",
            port=8080,
            container_prefix="pfx",
            idle_timeout=600,
            replicas=3,
            mounts=[],
            environment={},
            provisioner_url=None,
            mem_limit=None,
            shm_size=None,
        )
        monkeypatch.setattr(provider_mod, "get_app_config", lambda: SimpleNamespace(sandbox=fake_sandbox))

        provider = provider_mod.AioSandboxProvider.__new__(provider_mod.AioSandboxProvider)
        provider._config = provider._load_config()
        backend = provider._create_backend()
        assert backend._mem_limit is None
        assert backend._shm_size is None
