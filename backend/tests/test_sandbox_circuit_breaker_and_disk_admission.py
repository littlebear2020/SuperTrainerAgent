"""Sandbox-death circuit breaker + disk admission tests (DEF-04 C1/C2, 2026-09-23 批 1).

覆盖：
* 指纹矩阵——源头打标的死亡指纹命中、正常报错无标记不计数、混合序列；
* 熔断器——只认 ``[SANDBOX_DEAD]`` 标记、连续计数、达标 GraphBubbleUp 上抛、
  健康结果重置、无标记错误文案零容忍、注册进运行时链；
* 磁盘准入双档——block 档中文拒绝、warn 档仅 WARNING、模式门控（容器化才强制）。
"""

from __future__ import annotations

import logging
import sys
from enum import Enum
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import ToolMessage
from langgraph.errors import GraphBubbleUp

from deerflow.agents.middlewares.sandbox_circuit_breaker_middleware import (
    SANDBOX_DEAD_MARKER,
    SandboxCircuitBreakerError,
    SandboxCircuitBreakerMiddleware,
)
from deerflow.community.aio_sandbox.aio_sandbox import (
    AioSandbox,
    is_sandbox_dead_error,
    tag_sandbox_dead,
)
from deerflow.sandbox.disk_admission import (
    DiskAdmissionBlocked,
    check_disk_admission,
    sandbox_mode_is_containerized,
)


def _runtime(thread_id="t1", run_id="r1"):
    return SimpleNamespace(context={"thread_id": thread_id, "run_id": run_id})


def _request(name="bash", tool_call_id="c1"):
    return SimpleNamespace(
        tool_call={"name": name, "id": tool_call_id, "args": {}},
        runtime=_runtime(),
    )


class TestDeathFingerprints:
    """指纹矩阵：死亡指纹命中 / 正常报错不命中。"""

    @pytest.mark.parametrize(
        "message,hit",
        [
            ("docker: Error response from daemon: No such container: abc123", True),
            ("HTTPConnectionError(host='localhost', port=8392): connection refused", True),
            ("Cannot connect to the Docker daemon at unix:///var/run/docker.sock", True),
            ("Container abc is not running", True),
            ("Error response from daemon: driver failed programming external connectivity", False),
            ("grep: no matches found", False),
            ("SyntaxError: invalid syntax (file.py, line 3)", False),
            ("command exited with non-zero status 1", False),
            ("ModuleNotFoundError: No module named 'numpy'", False),
        ],
    )
    def test_fingerprint_matrix(self, message, hit):
        assert is_sandbox_dead_error(RuntimeError(message)) is hit

    def test_tag_prefix_keeps_original_text(self):
        tagged = tag_sandbox_dead(RuntimeError("No such container: abc"))
        assert tagged.startswith(f"Error: {SANDBOX_DEAD_MARKER} ")
        # 红线 4：agent 可见的错误文本仍含原始错误内容
        assert "No such container: abc" in tagged

    def test_execute_command_tags_dead_sandbox(self, monkeypatch):
        sandbox = AioSandbox(id="s1", base_url="http://dead:1")

        def raise_dead(self, **kwargs):
            raise ConnectionError("HTTPConnectionPool(host='127.0.0.1', port=8392): Max retries exceeded ... Connection refused")

        monkeypatch.setattr(type(sandbox._client.shell), "exec_command", raise_dead)
        output = sandbox.execute_command("ls /")
        assert output.startswith(f"Error: {SANDBOX_DEAD_MARKER}")

    def test_execute_command_normal_output_untagged(self, monkeypatch):
        sandbox = AioSandbox(id="s1", base_url="http://ok:1")

        result_holder = SimpleNamespace(data=SimpleNamespace(output="file.py\n", status="completed"))

        monkeypatch.setattr(type(sandbox._client.shell), "exec_command", lambda self, **kwargs: result_holder)
        assert sandbox.execute_command("ls /") == "file.py\n"


class TestCircuitBreaker:
    def _dead_result(self, n=1):
        return ToolMessage(content=f"Error: {SANDBOX_DEAD_MARKER} connection refused (attempt {n})", tool_call_id=f"c{n}")

    def _alive_result(self, n=1):
        return ToolMessage(content="ok output", tool_call_id=f"c{n}")

    def test_no_marker_never_counts(self):
        mw = SandboxCircuitBreakerMiddleware(failure_threshold=2)
        # 无标记错误文案零容忍：自然语言措辞相近也不计数
        for i in range(10):
            result = mw.wrap_tool_call(_request(tool_call_id=f"c{i}"), lambda r: ToolMessage(content=f"Error: command failed with exit status {i}", tool_call_id=f"c{i}"))
            assert isinstance(result, ToolMessage)
        # 计数器恒为空（happy path 零残留）
        assert not mw._consecutive

    def test_consecutive_hits_trip_at_threshold(self):
        mw = SandboxCircuitBreakerMiddleware(failure_threshold=5)
        for i in range(4):
            mw.wrap_tool_call(_request(tool_call_id=f"c{i + 1}"), lambda r, i=i: self._dead_result(i))
        with pytest.raises(SandboxCircuitBreakerError) as exc_info:
            mw.wrap_tool_call(_request(tool_call_id="c5"), lambda r: self._dead_result(5))
        # GraphBubbleUp 透传通道直达 worker error 路径
        assert isinstance(exc_info.value, GraphBubbleUp)
        assert "执行环境异常" in str(exc_info.value)
        assert "继续" in str(exc_info.value)

    def test_mixed_sequence_resets_on_healthy_result(self):
        mw = SandboxCircuitBreakerMiddleware(failure_threshold=5)
        for i in range(3):
            mw.wrap_tool_call(_request(tool_call_id=f"d{i + 1}"), lambda r, i=i: self._dead_result(i))
        mw.wrap_tool_call(_request(tool_call_id="d4"), lambda r: self._alive_result(4))  # reset
        for i in range(2):
            mw.wrap_tool_call(_request(tool_call_id=f"d{5 + i}"), lambda r, i=i: self._dead_result(5 + i))
        # 3 + reset + 2 = 连续只有 2，未到阈值，不熔断
        assert mw._consecutive[mw._key(_runtime())] == 2

    def test_threshold_one_trips_immediately(self):
        mw = SandboxCircuitBreakerMiddleware(failure_threshold=1)
        with pytest.raises(SandboxCircuitBreakerError):
            mw.wrap_tool_call(_request(), lambda r: self._dead_result())

    @pytest.mark.asyncio
    async def test_async_path_matches_sync(self):
        mw = SandboxCircuitBreakerMiddleware(failure_threshold=2)

        async def handler(request):
            return self._dead_result()

        await mw.awrap_tool_call(_request(), handler)
        with pytest.raises(SandboxCircuitBreakerError):
            await mw.awrap_tool_call(_request(), handler)

    def test_non_tool_message_results_ignored(self):
        """Command 形态的工具结果（无 ToolMessage 内容）不参与计数。"""
        mw = SandboxCircuitBreakerMiddleware(failure_threshold=1)
        result = mw.wrap_tool_call(_request(), lambda r: SimpleNamespace(update="command-like"))
        assert result.update == "command-like"  # 原样返回，不熔断

    def test_before_agent_clears_per_run(self):
        mw = SandboxCircuitBreakerMiddleware(failure_threshold=3)
        for i in range(2):
            mw.wrap_tool_call(_request(tool_call_id=f"e{i + 1}"), lambda r, i=i: self._dead_result(i))
        mw.before_agent({}, _runtime())
        assert not mw._consecutive
        # 新 run 重新计满 3 次才熔断
        for i in range(2):
            mw.wrap_tool_call(_request(tool_call_id=f"e{3 + i}"), lambda r, i=i: self._dead_result(i))
        assert mw._consecutive[mw._key(_runtime())] == 2

    def _file_content_result(self, marker_line: int = 5):
        """读文件结果形态：暗号在文件内容中部（非首行、非 Error 行）。"""
        lines = [f"log line {i}" for i in range(1, 8)]
        lines.insert(marker_line - 1, f"2026-09-23 10:00:00 ERROR somewhere said {SANDBOX_DEAD_MARKER} in passing")
        return ToolMessage(content="\n".join(lines), tool_call_id="c-file")

    def test_marker_in_file_content_middle_never_counts(self):
        """R7（Q5，2026-09-24）：agent 连读 5 个含暗号的文件不熔断（修前误报场景）。"""
        mw = SandboxCircuitBreakerMiddleware(failure_threshold=5)
        for i in range(5):
            result = mw.wrap_tool_call(_request(tool_call_id=f"f{i + 1}"), lambda r: self._file_content_result())
            assert isinstance(result, ToolMessage)
        assert not mw._consecutive

    def test_marker_in_first_line_without_error_lead_never_counts(self):
        """文件内容恰好以暗号文本开头（无 Error: 引导）也不计——前缀匹配须带 Error 引导。"""
        mw = SandboxCircuitBreakerMiddleware(failure_threshold=1)
        mw.wrap_tool_call(_request(tool_call_id="g1"), lambda r: ToolMessage(content=f"{SANDBOX_DEAD_MARKER} raw text from a file", tool_call_id="g1"))
        assert not mw._consecutive

    def test_readiness_timeout_shape_counts(self):
        """R7：真实死亡路径②——acquire 就绪超时经工具层兜成的首行 Error 文本仍计数。"""
        mw = SandboxCircuitBreakerMiddleware(failure_threshold=1)
        content = f"Error: Unexpected error executing command: {SANDBOX_DEAD_MARKER} Sandbox s1 failed to become ready within timeout at http://x"
        with pytest.raises(SandboxCircuitBreakerError):
            mw.wrap_tool_call(_request(tool_call_id="h1"), lambda r: ToolMessage(content=content, tool_call_id="h1"))


# CI 守卫：链构造用例读真实 config.yaml（gitignored）——缺失时跳过。
_REAL_CONFIG_EXISTS = any((Path(__file__).resolve().parents[i] / "config.yaml").exists() for i in (1, 2))
requires_real_config = pytest.mark.skipif(not _REAL_CONFIG_EXISTS, reason="依赖本地 gitignored config.yaml，CI 跳过")


@requires_real_config
class TestWiredIntoRuntimeChain:
    def test_registered_after_error_handling(self):
        from deerflow.agents.middlewares.tool_error_handling_middleware import build_lead_runtime_middlewares
        from deerflow.config import get_app_config

        names = [type(mw).__name__ for mw in build_lead_runtime_middlewares(app_config=get_app_config(), lazy_init=True)]
        assert "SandboxCircuitBreakerMiddleware" in names
        # C1 定稿：内层（列表序在 ToolErrorHandlingMiddleware 之后），
        # GraphBubbleUp 经外层透传通道上抛。
        assert names.index("SandboxCircuitBreakerMiddleware") > names.index("ToolErrorHandlingMiddleware")


class TestDiskAdmission:
    def test_block_tier_rejects_with_chinese_message(self, tmp_path):
        with pytest.raises(DiskAdmissionBlocked) as exc_info:
            check_disk_admission(tmp_path, block_bytes=10 * 1024**4, warn_ratio=0.15, label="任务")  # 10 TiB — 必然不足
        assert "磁盘可用空间不足" in str(exc_info.value)
        assert "清理磁盘" in str(exc_info.value)

    def test_warn_tier_only_warns_never_blocks(self, tmp_path, caplog):
        with caplog.at_level(logging.WARNING, logger="deerflow.sandbox.disk_admission"):
            check_disk_admission(tmp_path, block_bytes=0, warn_ratio=0.999999, label="任务")
        assert any("below the warn ratio" in record.message for record in caplog.records)
        # block 档关闭时 warn 档不抛异常

    def test_healthy_disk_passes_silently(self, tmp_path, caplog):
        with caplog.at_level(logging.WARNING, logger="deerflow.sandbox.disk_admission"):
            check_disk_admission(tmp_path, block_bytes=0, warn_ratio=0.0, label="任务")
        assert not caplog.records  # happy path 零输出

    @pytest.mark.parametrize(
        "use,expected",
        [
            ("deerflow.community.aio_sandbox.aio_sandbox_provider:AioSandboxProvider", True),
            ("deerflow.community.aio_sandbox:DockerSandboxProvider", True),
            ("deerflow.sandbox.local:LocalSandboxProvider", False),
            (None, False),
            ("", False),
        ],
    )
    def test_containerized_mode_detection(self, use, expected):
        assert sandbox_mode_is_containerized(use) is expected

    def test_probe_failure_degrades_to_noop(self, monkeypatch, tmp_path):
        import deerflow.sandbox.disk_admission as da

        def broken(path):
            raise PermissionError("no stat for you")

        monkeypatch.setattr(da.shutil, "disk_usage", broken)
        check_disk_admission(tmp_path, block_bytes=10 * 1024**4, warn_ratio=0.99, label="任务")  # 不抛


class TestWorkerDiskAdmissionWiring:
    def test_disk_admission_blocked_is_graph_bubble_up(self):
        """GraphBubbleUp 子类（2026-09-23 review #3）：经 ToolErrorHandling 的
        透传通道直达 worker 的 generic except Exception（其基类为 Exception），
        落 run error + SSE error 事件——而非被兜成错误 ToolMessage 让 agent
        在满盘上无限重试 bash。"""
        assert issubclass(DiskAdmissionBlocked, GraphBubbleUp)
        assert issubclass(DiskAdmissionBlocked, Exception)


class TestDiskAdmissionPassthrough:
    """lazy 沙箱路径：acquire 抛 DiskAdmissionBlocked 不得被兜成错误 ToolMessage。"""

    def test_sync_tool_call_raises_through_error_fallback(self):
        from deerflow.agents.middlewares.tool_error_handling_middleware import ToolErrorHandlingMiddleware

        middleware = ToolErrorHandlingMiddleware()
        req = _request(name="bash", tool_call_id="tc-disk")

        def _acquire_denied(_req):
            raise DiskAdmissionBlocked("服务器磁盘可用空间不足（1.2 GiB < 准入门槛 5.0 GiB），任务已拒绝启动：请清理磁盘空间后重试；已开始的任务不受影响。")

        with pytest.raises(DiskAdmissionBlocked) as exc_info:
            middleware.wrap_tool_call(req, _acquire_denied)
        # 中文文案原样抵达 worker error 路径
        assert "磁盘可用空间不足" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_async_tool_call_raises_through_error_fallback(self):
        from deerflow.agents.middlewares.tool_error_handling_middleware import ToolErrorHandlingMiddleware

        middleware = ToolErrorHandlingMiddleware()
        req = _request(name="bash", tool_call_id="tc-disk-a")

        async def _acquire_denied(_req):
            raise DiskAdmissionBlocked("服务器磁盘可用空间不足")

        with pytest.raises(DiskAdmissionBlocked):
            await middleware.awrap_tool_call(req, _acquire_denied)


# 跨文件 sys.modules 污染防御：部分先序测试文件（如 test_subagent_executor）
# 会把 deerflow.sandbox 系列换成 MagicMock 且不恢复——本类必须在真实模块上
# 打 patch，否则 bash_tool.func() 返回 MagicMock。类内换装、类后还原。
@pytest.fixture(scope="class", autouse=True)
def _real_sandbox_tools():
    import importlib

    saved = {n: sys.modules.get(n) for n in ("deerflow.sandbox", "deerflow.sandbox.tools")}
    for n in ("deerflow.sandbox.tools", "deerflow.sandbox"):
        mod = sys.modules.get(n)
        if mod is None or isinstance(mod, MagicMock):
            sys.modules.pop(n, None)
    tools = importlib.import_module("deerflow.sandbox.tools")
    yield tools
    for n, m in saved.items():
        if m is None:
            sys.modules.pop(n, None)
        else:
            sys.modules[n] = m


class TestRealSandboxToolPassthrough:
    """R1 (2026-09-24 批 0 review C-B1): the real sandbox tools must let GraphBubbleUp escape.

    lazy-acquire DiskAdmissionBlocked raised inside a REAL tool body (not a
    synthetic handler like TestDiskAdmissionPassthrough) must propagate as an
    exception — previously each tool's generic ``except Exception`` stringified
    it, so the run never landed on the error terminal and the agent retried
    until recursion_limit (the DEF-04 dead-loop replay).
    """

    _DISK_MSG = "服务器磁盘可用空间不足（1.2 GiB < 准入门槛 5.0 GiB），任务已拒绝启动：请清理磁盘空间后重试。"

    def _deny_provider(self, monkeypatch, sandbox_tools):
        class _DeniedProvider:
            def acquire(self, thread_id):
                raise DiskAdmissionBlocked(TestRealSandboxToolPassthrough._DISK_MSG)

            def get(self, sandbox_id):
                raise AssertionError("get() must not be reached when acquire is denied")

        monkeypatch.setattr(sandbox_tools, "get_sandbox_provider", lambda: _DeniedProvider())
        return sandbox_tools

    def _runtime(self):
        # state without "sandbox" → ensure_sandbox_initialized goes down the
        # lazy-acquire path and calls provider.acquire(thread_id).
        return SimpleNamespace(state={}, context={"thread_id": "t-disk"}, config={})

    def test_bash_tool_lazy_acquire_blocked_raises(self, monkeypatch, _real_sandbox_tools):
        sandbox_tools = self._deny_provider(monkeypatch, _real_sandbox_tools)

        with pytest.raises(DiskAdmissionBlocked) as exc_info:
            sandbox_tools.bash_tool.func(self._runtime(), description="run command", command="echo hi")
        # 中文文案原样上抛（worker error 路径用户可见）
        assert "磁盘可用空间不足" in str(exc_info.value)

    @pytest.mark.parametrize(
        "tool_name,args",
        [
            ("ls_tool", {"path": "/mnt/user-data/workspace"}),
            ("glob_tool", {"pattern": "**/*.py", "path": "/mnt/user-data/workspace"}),
            ("grep_tool", {"pattern": "x", "path": "/mnt/user-data/workspace"}),
            ("read_file_tool", {"path": "/mnt/user-data/workspace/a.txt"}),
            ("write_file_tool", {"path": "/mnt/user-data/workspace/a.txt", "content": "x"}),
            ("str_replace_tool", {"path": "/mnt/user-data/workspace/a.txt", "old_str": "a", "new_str": "b"}),
        ],
    )
    def test_file_tools_lazy_acquire_blocked_raise(self, monkeypatch, _real_sandbox_tools, tool_name, args):
        sandbox_tools = self._deny_provider(monkeypatch, _real_sandbox_tools)

        tool = getattr(sandbox_tools, tool_name)
        with pytest.raises(DiskAdmissionBlocked):
            tool.func(self._runtime(), description="d", **args)

    def test_bash_tool_generic_exception_still_stringified(self, monkeypatch, _real_sandbox_tools):
        """守卫不得改变既有语义：普通异常仍兜成错误字符串（happy path 零损耗）。"""
        sandbox_tools = _real_sandbox_tools

        class _BrokenProvider:
            def acquire(self, thread_id):
                raise RuntimeError("boom-not-bubble-up")

            def get(self, sandbox_id):
                raise AssertionError("unreachable")

        monkeypatch.setattr(sandbox_tools, "get_sandbox_provider", lambda: _BrokenProvider())

        result = sandbox_tools.bash_tool.func(self._runtime(), description="d", command="echo hi")
        assert isinstance(result, str)
        assert "Error" in result


# conftest stubs deerflow.subagents.executor with a MagicMock to break a
# circular import chain; these tests need the REAL executor, loaded with the
# same module-swap pattern as tests/test_subagent_recursion_partial.py.
_BUBBLE_MOCKED_MODULES = [
    "deerflow.agents",
    "deerflow.agents.thread_state",
    "deerflow.agents.middlewares",
    "deerflow.agents.middlewares.thread_data_middleware",
    "deerflow.sandbox",
    "deerflow.sandbox.middleware",
    "deerflow.sandbox.security",
    "deerflow.models",
]


@pytest.fixture(scope="class")
def real_executor_mod():
    """Import the real executor module past conftest's circular-import mocks."""
    import importlib

    original = {name: sys.modules.get(name) for name in _BUBBLE_MOCKED_MODULES}
    original_executor = sys.modules.get("deerflow.subagents.executor")
    sys.modules.pop("deerflow.subagents.executor", None)
    for name in _BUBBLE_MOCKED_MODULES:
        sys.modules[name] = MagicMock()

    module = importlib.import_module("deerflow.subagents.executor")
    yield module

    for name, mod in original.items():
        if mod is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = mod
    if original_executor is not None:
        sys.modules["deerflow.subagents.executor"] = original_executor
    else:
        sys.modules.pop("deerflow.subagents.executor", None)


class TestSubagentBubbleUpPassthrough:
    """R1 §10.2.1 (2026-09-24 批 0): 子代理图逃逸的 GraphBubbleUp 必须抵达 lead run 的 error 终态.

    链路三段：``_aexecute`` 重抛 → ``run_task`` 把异常对象挂到 ``bubble_up``
    字段（不丢中文文案）→ task 工具在 FAILED 分支重抛。任何一段兜成字符串
    都会让 lead 拿到 "Task failed" 文案并继续重试。
    """

    @staticmethod
    def _raising_agent(exc: Exception):
        class _RaisingAgent:
            def astream(self, *a, **k):
                async def _gen():
                    raise exc
                    yield  # pragma: no cover — makes this an async generator

                return _gen()

        return _RaisingAgent()

    def _make_executor(self, real_executor_mod):
        from deerflow.subagents.config import SubagentConfig

        config = SubagentConfig(
            name="general-purpose",
            description="test",
            system_prompt="test",
            skills=[],  # hermetic: never load real skills
            timeout_seconds=30,
        )
        return real_executor_mod.SubagentExecutor(
            config=config,
            tools=[],
            app_config=SimpleNamespace(token_usage=SimpleNamespace(enabled=False)),
        )

    @pytest.mark.asyncio
    async def test_aexecute_reraises_bubble_up(self, real_executor_mod, monkeypatch):
        exc = SandboxCircuitBreakerError(5, 5)
        monkeypatch.setattr(real_executor_mod.SubagentExecutor, "_create_agent", lambda self: TestSubagentBubbleUpPassthrough._raising_agent(exc))
        executor = self._make_executor(real_executor_mod)

        with pytest.raises(SandboxCircuitBreakerError) as exc_info:
            await executor._aexecute("do something")
        assert "熔断阈值" in str(exc_info.value)

    def test_execute_async_stamps_bubble_up_on_result(self, real_executor_mod, monkeypatch):
        """run_task 包装层：异常对象随结果存活（FAILED + 中文 error + bubble_up）。"""
        import time

        exc = DiskAdmissionBlocked("服务器磁盘可用空间不足（0.4 GiB < 准入门槛 5.0 GiB）。")
        monkeypatch.setattr(real_executor_mod.SubagentExecutor, "_create_agent", lambda self: TestSubagentBubbleUpPassthrough._raising_agent(exc))
        executor = self._make_executor(real_executor_mod)

        task_id = executor.execute_async("do something", task_id="t-bubble")
        deadline = time.monotonic() + 15
        result = None
        while time.monotonic() < deadline:
            result = real_executor_mod.get_background_task_result(task_id)
            status = getattr(result, "status", None)
            if status is not None and status != real_executor_mod.SubagentStatus.PENDING and status != real_executor_mod.SubagentStatus.RUNNING:
                break
            time.sleep(0.05)

        assert result is not None, "background task never reached a terminal status"
        assert result.status == real_executor_mod.SubagentStatus.FAILED
        assert "磁盘可用空间不足" in (result.error or "")
        assert result.bubble_up is exc

    def _patch_task_tool_env(self, monkeypatch, task_tool_module, failed_result):
        """Common stubs for driving task_tool without the real executor/registry."""
        from deerflow.subagents.config import SubagentConfig

        config = SubagentConfig(name="general-purpose", description="test", system_prompt="test", timeout_seconds=900)
        monkeypatch.setattr(task_tool_module, "get_available_subagent_names", lambda: ["general-purpose"])
        monkeypatch.setattr(task_tool_module, "get_subagent_config", lambda name: config if name == "general-purpose" else None)
        monkeypatch.setattr("deerflow.tools.get_available_tools", lambda **kwargs: [])

        class _FakeExecutor:
            def __init__(self, **kwargs):
                pass

            def execute_async(self, prompt, task_id=None):
                return task_id

        monkeypatch.setattr(task_tool_module, "SubagentExecutor", _FakeExecutor)
        monkeypatch.setattr(task_tool_module, "get_background_task_result", lambda task_id: failed_result)
        monkeypatch.setattr(task_tool_module, "cleanup_background_task", lambda task_id: None)
        monkeypatch.setattr(task_tool_module, "get_stream_writer", lambda: lambda payload: None)

    @staticmethod
    def _task_runtime():
        return SimpleNamespace(state={}, context={"thread_id": "t"}, config={"metadata": {}, "configurable": {"thread_id": "t"}})

    @pytest.mark.asyncio
    async def test_task_tool_reraises_bubble_up(self, monkeypatch):
        """task 工具 FAILED 分支：带 bubble_up 的结果必须重抛而非返回字符串。"""
        import importlib

        task_tool_module = importlib.import_module("deerflow.tools.builtins.task_tool")

        class _Status(Enum):
            PENDING = "pending"
            RUNNING = "running"
            COMPLETED = "completed"
            FAILED = "failed"
            CANCELLED = "cancelled"
            TIMED_OUT = "timed_out"

        monkeypatch.setattr(task_tool_module, "SubagentStatus", _Status)
        exc = SandboxCircuitBreakerError(5, 5)
        failed = SimpleNamespace(
            status=_Status.FAILED,
            error=str(exc),
            bubble_up=exc,
            token_records=[],
            ai_messages=[],
            stop_reason=None,
        )
        self._patch_task_tool_env(monkeypatch, task_tool_module, failed)

        with pytest.raises(SandboxCircuitBreakerError) as exc_info:
            await task_tool_module.task_tool.coroutine(
                self._task_runtime(),
                description="delegate",
                prompt="do something",
                subagent_type="general-purpose",
                tool_call_id="tc-bubble",
            )
        assert "熔断阈值" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_task_tool_plain_failure_still_returns_string(self, monkeypatch):
        """普通 FAILED（无 bubble_up）维持既有字符串返回（默认行为不变）。"""
        import importlib

        task_tool_module = importlib.import_module("deerflow.tools.builtins.task_tool")

        class _Status(Enum):
            PENDING = "pending"
            RUNNING = "running"
            COMPLETED = "completed"
            FAILED = "failed"
            CANCELLED = "cancelled"
            TIMED_OUT = "timed_out"

        monkeypatch.setattr(task_tool_module, "SubagentStatus", _Status)
        failed = SimpleNamespace(
            status=_Status.FAILED,
            error="boom",
            bubble_up=None,
            token_records=[],
            ai_messages=[],
            stop_reason=None,
        )
        self._patch_task_tool_env(monkeypatch, task_tool_module, failed)

        out = await task_tool_module.task_tool.coroutine(
            self._task_runtime(),
            description="delegate",
            prompt="do something",
            subagent_type="general-purpose",
            tool_call_id="tc-plain",
        )
        assert out.startswith("Task failed. Error: boom")
