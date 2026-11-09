"""T14（2026-09-08 remotion-course 计划 D20）：沙箱容器启动参数守卫。

_start_container 的 docker run 必须带 --memory=3g --memory-swap=3g（128G 总账把
agent 沙箱 3×3G 纳入账本——此前无任何资源参数属账本外缺口），且**不设 --cpus**
（D20 拍板：CPU 竞争只是软性变慢，列为监控项，出现互抢再加）。
"""

from __future__ import annotations

import pytest


class _FakeProc:
    def __init__(self, rc: int = 0) -> None:
        self.returncode = rc
        self.stdout = b""
        self.stderr = b""


def _make_backend(mem_limit=None):
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
    provider._runtime = "docker"
    provider._environment = {"K": "V"}
    provider._config_mounts = []
    provider._image = "img:tag"
    provider._mem_limit = mem_limit
    provider._shm_size = None
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
    monkeypatch.delenv("SANDBOX_MEM_LIMIT", raising=False)


class TestSandboxMemLimit:
    def test_docker_run_has_memory_limit(self, monkeypatch):
        captured = _capture_run(monkeypatch)
        provider = _make_backend()
        provider._start_container("sbx-test", 8081, [])
        assert "--memory" in captured
        i = captured.index("--memory")
        assert captured[i + 1] == "3g"
        assert captured[i + 2] == "--memory-swap"
        assert captured[i + 3] == "3g"

    def test_docker_run_has_no_cpu_limit(self, monkeypatch):
        """D20：只设内存，不设 CPU 上限。"""
        captured = _capture_run(monkeypatch)
        provider = _make_backend()
        provider._start_container("sbx-test", 8081, [])
        assert "--cpus" not in captured

    def test_env_override_respected(self, monkeypatch):
        monkeypatch.setenv("SANDBOX_MEM_LIMIT", "2g")
        captured = _capture_run(monkeypatch)
        provider = _make_backend()
        provider._start_container("sbx-test", 8081, [])
        i = captured.index("--memory")
        assert captured[i + 1] == "2g"

    def test_env_empty_disables_limit(self, monkeypatch):
        """空串 = 显式关闭（回旧行为，运维逃生口）。"""
        monkeypatch.setenv("SANDBOX_MEM_LIMIT", "")
        captured = _capture_run(monkeypatch)
        provider = _make_backend()
        provider._start_container("sbx-test", 8081, [])
        assert "--memory" not in captured

    def test_config_value_used_when_env_unset(self, monkeypatch):
        """F2（2026-09-29）：env 未设时 config 档位（构造器参数）生效。"""
        captured = _capture_run(monkeypatch)
        provider = _make_backend(mem_limit="6g")
        provider._start_container("sbx-test", 8081, [])
        i = captured.index("--memory")
        assert captured[i + 1] == "6g"
        assert captured[i + 2] == "--memory-swap"
        assert captured[i + 3] == "6g"

    def test_env_wins_over_config(self, monkeypatch):
        """F2：env 优先于 config（运维逃生口）。"""
        monkeypatch.setenv("SANDBOX_MEM_LIMIT", "4g")
        captured = _capture_run(monkeypatch)
        provider = _make_backend(mem_limit="6g")
        provider._start_container("sbx-test", 8081, [])
        i = captured.index("--memory")
        assert captured[i + 1] == "4g"

    def test_empty_env_disables_even_when_config_set(self, monkeypatch):
        """空串 env = 显式关闭，压过 config 档位。"""
        monkeypatch.setenv("SANDBOX_MEM_LIMIT", "")
        captured = _capture_run(monkeypatch)
        provider = _make_backend(mem_limit="6g")
        provider._start_container("sbx-test", 8081, [])
        assert "--memory" not in captured
