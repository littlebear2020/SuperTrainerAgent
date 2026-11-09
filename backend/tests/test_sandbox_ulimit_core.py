"""2026-10-13 auto-video-flow 渲染加固：沙箱容器禁 core dump 守卫。

Chrome 崩溃遗留的单个 core 文件可到 ~500MB，无人值守渲染工程内多份堆积时会把
bundle 阶段内存推过 3G cgroup 上限。Docker runtime 必须显式注入
``--ulimit core=0``；Apple Container 路径不支持该 flag，保持不加。
"""

from __future__ import annotations

import pytest


class _FakeProc:
    def __init__(self, rc: int = 0) -> None:
        self.returncode = rc
        self.stdout = b""
        self.stderr = b""


def _make_backend(runtime: str = "docker"):
    """构造不触发 ``__init__`` 副作用的最小 LocalContainerBackend。

    该 helper 刻意经 ``__new__`` 绕过 ``__init__``，让测试保持离线且不启动真实容器。
    """
    from deerflow.community.aio_sandbox.local_backend import LocalContainerBackend

    provider = LocalContainerBackend.__new__(LocalContainerBackend)
    provider._runtime = runtime
    provider._environment = {"K": "V"}
    provider._config_mounts = []
    provider._image = "img:tag"
    provider._mem_limit = None
    provider._shm_size = None
    return provider


def _capture_run(runtime: str, monkeypatch) -> list[str]:
    captured: list[str] = []

    def fake_run(cmd, *a, **kw):  # noqa: ANN001, ANN202
        if cmd[:2] == [runtime, "run"]:
            captured.extend(cmd)
        return _FakeProc()

    monkeypatch.setattr("subprocess.run", fake_run)
    return captured


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("SANDBOX_MEM_LIMIT", raising=False)
    monkeypatch.delenv("SANDBOX_SHM_SIZE", raising=False)


class TestSandboxUlimitCore:
    def test_docker_run_disables_core_dump(self, monkeypatch):
        captured = _capture_run("docker", monkeypatch)
        provider = _make_backend()
        provider._start_container("sbx-test", 8081, [])
        assert "--ulimit" in captured
        i = captured.index("--ulimit")
        assert captured[i + 1] == "core=0"

    def test_non_docker_runtime_has_no_ulimit(self, monkeypatch):
        captured = _capture_run("container", monkeypatch)
        provider = _make_backend(runtime="container")
        provider._start_container("sbx-test", 8081, [])
        assert "--ulimit" not in captured
