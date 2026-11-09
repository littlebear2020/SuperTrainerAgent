"""Tests for AioSandbox concurrent command serialization (#1433)."""

import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture()
def sandbox():
    """Create an AioSandbox with a mocked client."""
    with patch("deerflow.community.aio_sandbox.aio_sandbox.AioSandboxClient"):
        from deerflow.community.aio_sandbox.aio_sandbox import AioSandbox

        sb = AioSandbox(id="test-sandbox", base_url="http://localhost:8080")
        return sb


class TestExecuteCommandSerialization:
    """Verify that concurrent exec_command calls are serialized."""

    def test_lock_prevents_concurrent_execution(self, sandbox):
        """Concurrent threads should not overlap inside execute_command."""
        call_log = []
        barrier = threading.Barrier(3)

        def slow_exec(command, **kwargs):
            call_log.append(("enter", command))
            import time

            time.sleep(0.05)
            call_log.append(("exit", command))
            return SimpleNamespace(data=SimpleNamespace(output=f"ok: {command}"))

        sandbox._client.shell.exec_command = slow_exec

        def worker(cmd):
            barrier.wait()  # ensure all threads contend for the lock simultaneously
            sandbox.execute_command(cmd)

        threads = []
        for i in range(3):
            t = threading.Thread(target=worker, args=(f"cmd-{i}",))
            threads.append(t)

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Verify serialization: each "enter" should be followed by its own
        # "exit" before the next "enter" (no interleaving).
        enters = [i for i, (action, _) in enumerate(call_log) if action == "enter"]
        exits = [i for i, (action, _) in enumerate(call_log) if action == "exit"]
        assert len(enters) == 3
        assert len(exits) == 3
        for e_idx, x_idx in zip(enters, exits):
            assert x_idx == e_idx + 1, f"Interleaved execution detected: {call_log}"


class TestErrorObservationRetry:
    """Verify ErrorObservation detection and fresh-session retry."""

    def test_retry_on_error_observation(self, sandbox):
        """When output contains ErrorObservation, retry with a fresh session."""
        call_count = 0

        def mock_exec(command, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return SimpleNamespace(data=SimpleNamespace(output="'ErrorObservation' object has no attribute 'exit_code'"))
            return SimpleNamespace(data=SimpleNamespace(output="success"))

        sandbox._client.shell.exec_command = mock_exec

        result = sandbox.execute_command("echo hello")
        assert result == "success"
        assert call_count == 2

    def test_retry_passes_fresh_session_id(self, sandbox):
        """The retry call should include a new session id kwarg."""
        calls = []

        def mock_exec(command, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                return SimpleNamespace(data=SimpleNamespace(output="'ErrorObservation' object has no attribute 'exit_code'"))
            return SimpleNamespace(data=SimpleNamespace(output="ok"))

        sandbox._client.shell.exec_command = mock_exec

        sandbox.execute_command("test")
        assert len(calls) == 2
        assert "id" not in calls[0]
        assert "id" in calls[1]
        assert len(calls[1]["id"]) == 36  # UUID format

    def test_no_retry_on_clean_output(self, sandbox):
        """Normal output should not trigger a retry."""
        call_count = 0

        def mock_exec(command, **kwargs):
            nonlocal call_count
            call_count += 1
            return SimpleNamespace(data=SimpleNamespace(output="all good"))

        sandbox._client.shell.exec_command = mock_exec

        result = sandbox.execute_command("echo hello")
        assert result == "all good"
        assert call_count == 1


class TestListDirSerialization:
    """Verify that list_dir also acquires the lock."""

    def test_list_dir_uses_lock(self, sandbox):
        """list_dir should hold the lock during execution."""
        lock_was_held = []

        original_exec = MagicMock(return_value=SimpleNamespace(data=SimpleNamespace(output="/a\n/b")))

        def tracking_exec(command, **kwargs):
            lock_was_held.append(sandbox._lock.locked())
            return original_exec(command, **kwargs)

        sandbox._client.shell.exec_command = tracking_exec

        result = sandbox.list_dir("/test")
        assert result == ["/a", "/b"]
        assert lock_was_held == [True], "list_dir must hold the lock during exec_command"


class TestWriteFile:
    def test_append_uses_server_append_without_pre_read(self, sandbox):
        sandbox._client.file.read_file = MagicMock(side_effect=RuntimeError("read timed out"))
        sandbox._client.file.write_file = MagicMock()

        sandbox.write_file("/mnt/user-data/workspace/report.txt", "tail", append=True)

        sandbox._client.file.read_file.assert_not_called()
        sandbox._client.file.write_file.assert_called_once_with(
            file="/mnt/user-data/workspace/report.txt",
            content="tail",
            append=True,
        )

    def test_overwrite_keeps_existing_request_shape(self, sandbox):
        sandbox._client.file.write_file = MagicMock()

        sandbox.write_file("/mnt/user-data/workspace/report.txt", "replacement")

        sandbox._client.file.write_file.assert_called_once_with(
            file="/mnt/user-data/workspace/report.txt",
            content="replacement",
        )


class TestConcurrentFileWrites:
    """Verify file write paths do not lose concurrent updates."""

    def test_append_should_preserve_both_parallel_writes(self, sandbox):
        storage = {"content": "seed\n"}
        state_lock = threading.Lock()

        def write_back(*, file, content, append=False, **kwargs):
            with state_lock:
                if append:
                    storage["content"] += content
                else:
                    storage["content"] = content
            return SimpleNamespace(data=SimpleNamespace())

        sandbox.read_file = MagicMock(side_effect=AssertionError("native append must not pre-read"))
        sandbox._client.file.write_file = write_back

        barrier = threading.Barrier(2)

        def writer(payload: str):
            barrier.wait()
            sandbox.write_file("/tmp/shared.log", payload, append=True)

        threads = [
            threading.Thread(target=writer, args=("A\n",)),
            threading.Thread(target=writer, args=("B\n",)),
        ]

        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert storage["content"] in {"seed\nA\nB\n", "seed\nB\nA\n"}


class TestNoChangeTimeout:
    """Long silent commands (ffmpeg, remotion render) must not hit the 120s default."""

    def test_exec_command_passes_no_change_timeout(self, sandbox):
        calls = []

        def mock_exec(command, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(data=SimpleNamespace(output="ok"))

        sandbox._client.shell.exec_command = mock_exec

        sandbox.execute_command("ffmpeg -i in.mp4 out.mp4")

        # B9：地板 = max(600, ceil(600+5)) = 605（宽限抬升，墙钟 600 先断）
        assert calls[0]["no_change_timeout"] == 605
        assert calls[0]["hard_timeout"] == 600

    def test_retry_also_passes_no_change_timeout(self, sandbox):
        calls = []

        def mock_exec(command, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                return SimpleNamespace(data=SimpleNamespace(output="'ErrorObservation' object has no attribute 'exit_code'"))
            return SimpleNamespace(data=SimpleNamespace(output="ok"))

        sandbox._client.shell.exec_command = mock_exec

        assert sandbox.execute_command("ffmpeg -i in.mp4 out.mp4") == "ok"
        assert len(calls) == 2
        assert calls[1]["no_change_timeout"] == 605
        assert calls[1]["hard_timeout"] == 600  # B9：重试路径同样双闸

    def test_list_dir_passes_no_change_timeout(self, sandbox):
        calls = []

        def mock_exec(command, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(data=SimpleNamespace(output="/a\n/b"))

        sandbox._client.shell.exec_command = mock_exec

        sandbox.list_dir("/test")

        assert calls[0]["no_change_timeout"] == 600


class TestRecoverySessionCleanup:
    """The single-use recovery session must be released, not leaked."""

    def _force_retry(self, sandbox):
        calls = []

        def mock_exec(command, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                return SimpleNamespace(data=SimpleNamespace(output="'ErrorObservation' object has no attribute 'exit_code'"))
            return SimpleNamespace(data=SimpleNamespace(output="ok"))

        sandbox._client.shell.exec_command = mock_exec
        return calls

    def test_recovery_session_is_cleaned_up(self, sandbox):
        calls = self._force_retry(sandbox)
        cleaned = []
        sandbox._client.shell.cleanup_session = lambda session_id: cleaned.append(session_id)

        assert sandbox.execute_command("echo hi") == "ok"
        assert cleaned == [calls[1]["id"]]

    def test_recovery_session_cleaned_up_when_retry_raises(self, sandbox):
        seen = []

        def mock_exec(command, **kwargs):
            seen.append(kwargs)
            if len(seen) == 1:
                return SimpleNamespace(data=SimpleNamespace(output="'ErrorObservation' object has no attribute 'exit_code'"))
            raise RuntimeError("retry blew up")

        sandbox._client.shell.exec_command = mock_exec
        cleaned = []
        sandbox._client.shell.cleanup_session = lambda session_id: cleaned.append(session_id)

        result = sandbox.execute_command("echo hi")

        assert result.startswith("Error:")
        assert cleaned == [seen[1]["id"]], "session must be released even when the retry fails"

    def test_cleanup_failure_does_not_break_command(self, sandbox):
        self._force_retry(sandbox)

        def failing_cleanup(session_id):
            raise RuntimeError("cleanup endpoint down")

        sandbox._client.shell.cleanup_session = failing_cleanup

        assert sandbox.execute_command("echo hi") == "ok"

    def test_no_cleanup_without_retry(self, sandbox):
        sandbox._client.shell.exec_command = lambda command, **kwargs: SimpleNamespace(data=SimpleNamespace(output="fine"))
        cleaned = []
        sandbox._client.shell.cleanup_session = lambda session_id: cleaned.append(session_id)

        assert sandbox.execute_command("echo hi") == "fine"
        assert cleaned == []


class TestCreateSessionBeforeRetry:
    """R-05 ② (2026-08-13): the recovery session must be created explicitly
    before the retry exec_command targets it."""

    def test_retry_creates_session_before_exec(self, sandbox):
        events = []

        def mock_exec(command, **kwargs):
            events.append(("exec", kwargs))
            if len(events) == 1:
                return SimpleNamespace(data=SimpleNamespace(output="'ErrorObservation' object has no attribute 'exit_code'"))
            return SimpleNamespace(data=SimpleNamespace(output="ok"))

        def mock_create_session(**kwargs):
            events.append(("create", kwargs))

        sandbox._client.shell.exec_command = mock_exec
        sandbox._client.shell.create_session = mock_create_session

        result = sandbox.execute_command("test")
        assert result == "ok"
        # create_session must fire after the first (failing) exec and before
        # the retry exec targeting the fresh id.
        assert events[0][0] == "exec"
        assert events[1][0] == "create"
        assert events[2][0] == "exec"
        assert events[2][1].get("id") == events[1][1].get("id")


class TestConsoleAggregation:
    """R-05 ① (2026-08-13): timed-out commands report partial output from
    data.console instead of a bare "(no output)"."""

    def test_partial_console_output_surfaced_with_marker(self, sandbox):
        record = SimpleNamespace(output="partial line 1\npartial line 2")
        sandbox._client.shell.exec_command = lambda command, **kwargs: SimpleNamespace(data=SimpleNamespace(output="", console=[record], status="no_change_timeout"))

        result = sandbox.execute_command("long-running-command")
        assert "partial line 1" in result
        assert "partial line 2" in result
        assert "[command timed out/interrupted" in result

    def test_no_console_records_falls_back_to_no_output(self, sandbox):
        sandbox._client.shell.exec_command = lambda command, **kwargs: SimpleNamespace(data=SimpleNamespace(output="", console=[], status="no_change_timeout"))

        result = sandbox.execute_command("silent-timeout")
        assert result == "(no output)"

    def test_completed_output_skips_console_path(self, sandbox):
        record = SimpleNamespace(output="stale partial")
        sandbox._client.shell.exec_command = lambda command, **kwargs: SimpleNamespace(data=SimpleNamespace(output="full output", console=[record], status="completed"))

        result = sandbox.execute_command("ok")
        assert result == "full output"
        assert "[command timed out" not in result


class TestPerCommandTimeout:
    """T27 (2026-08-13): the bash tool passes a per-command timeout from
    config; aio maps it to the SDK's no_change_timeout."""

    def test_timeout_maps_to_no_change_timeout(self, sandbox):
        calls = []

        def mock_exec(command, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(data=SimpleNamespace(output="ok"))

        sandbox._client.shell.exec_command = mock_exec

        result = sandbox.execute_command("sleep 5", timeout=120)
        assert result == "ok"
        # B9（P1-S4.4，2026-09-30 上游移植）：双闸——no_change_timeout 是地板
        # （≥墙钟+宽限），墙钟 hard_timeout 才是上限。timeout=120 → no_change
        # 取 600 地板、hard=120：持续产出命令不再无限续命。
        assert calls[0]["no_change_timeout"] == 600
        assert calls[0]["hard_timeout"] == 120

    def test_default_falls_back_to_module_constant(self, sandbox):
        calls = []

        def mock_exec(command, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(data=SimpleNamespace(output="ok"))

        sandbox._client.shell.exec_command = mock_exec

        sandbox.execute_command("echo hi")
        assert calls[0]["no_change_timeout"] == 605
        assert calls[0]["hard_timeout"] == 600

    def test_no_change_floor_rises_above_wall_clock_for_long_timeouts(self, sandbox):
        """超地板墙钟（timeout=900 > 600）：no_change 必须抬到 墙钟+宽限 之上，
        否则先撞 no-change 撞不到墙钟，语义又退回单闸。"""
        calls = []

        def mock_exec(command, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(data=SimpleNamespace(output="ok"))

        sandbox._client.shell.exec_command = mock_exec

        sandbox.execute_command("render --long", timeout=900)
        assert calls[0]["hard_timeout"] == 900
        assert calls[0]["no_change_timeout"] >= 905


# ── B10（§4.4，2026-09-30）：read/write/list 死亡打标 ──────────────────────


class TestDeadSandboxTagging:
    def _dead_sandbox(self, sandbox):
        def boom(*args, **kwargs):
            raise RuntimeError("Error response: Container abc123 is not running")

        sandbox._client.shell.exec_command = boom
        sandbox._client.file.read_file = boom
        sandbox._client.file.write_file = boom
        return sandbox

    def test_read_file_tags_dead(self, sandbox):
        sandbox = self._dead_sandbox(sandbox)
        out = sandbox.read_file("/mnt/user-data/workspace/a.txt")
        from deerflow.community.aio_sandbox.aio_sandbox import SANDBOX_DEAD_MARKER

        assert SANDBOX_DEAD_MARKER in out
        assert "is not running" in out

    def test_list_dir_tags_dead_as_single_entry(self, sandbox):
        sandbox = self._dead_sandbox(sandbox)
        entries = sandbox.list_dir("/mnt/user-data/workspace")
        from deerflow.community.aio_sandbox.aio_sandbox import SANDBOX_DEAD_MARKER

        assert len(entries) == 1
        assert SANDBOX_DEAD_MARKER in entries[0]

    def test_write_file_raises_tagged_dead(self, sandbox):
        sandbox = self._dead_sandbox(sandbox)
        import pytest as _pytest

        from deerflow.community.aio_sandbox.aio_sandbox import SANDBOX_DEAD_MARKER

        with _pytest.raises(RuntimeError) as excinfo:
            sandbox.write_file("/mnt/user-data/workspace/a.txt", "x")
        assert SANDBOX_DEAD_MARKER in str(excinfo.value)

    def test_non_dead_errors_keep_legacy_shape(self, sandbox):
        def boom(*args, **kwargs):
            raise RuntimeError("some transient http 500")

        sandbox._client.file.read_file = boom
        out = sandbox.read_file("/mnt/user-data/workspace/a.txt")
        assert out.startswith("Error:")
        assert "[SANDBOX_DEAD]" not in out


# ── 评审二轮修复（2026-09-30）：required-secrets 注入链消费端 ───────────────


class TestEnvInjectionChannel:
    """H1 修复：execute_command(env=...) 经 bash.exec 单命令通道注入（不驻留容器 env）。"""

    def test_env_none_keeps_shell_exec_command_path(self, sandbox):
        calls = []

        def mock_exec(command, **kwargs):
            calls.append(("shell", kwargs))
            return SimpleNamespace(data=SimpleNamespace(output="ok"))

        sandbox._client.shell.exec_command = mock_exec
        out = sandbox.execute_command("echo hi")
        assert out == "ok"
        assert calls[0][0] == "shell"

    def test_env_dict_routes_to_bash_exec_with_env(self, sandbox):
        shell_calls = []
        bash_calls = []

        def mock_shell(command, **kwargs):
            shell_calls.append(kwargs)
            return SimpleNamespace(data=SimpleNamespace(output="shell-path"))

        def mock_bash_exec(command, **kwargs):
            bash_calls.append(kwargs)
            # BashExecResult 真实形状：stdout/stderr/exit_code（无 output 字段，
            # 2026-10-13 事故修正——此前 mock 按 shell 族形状写，与实现同错互证）
            return SimpleNamespace(data=SimpleNamespace(stdout="secret-run\n", stderr=None, exit_code=0))

        sandbox._client.shell.exec_command = mock_shell
        sandbox._client.bash.exec = mock_bash_exec

        out = sandbox.execute_command("python scripts/_doubao_tts.py", timeout=30, env={"VOLCENGINE_VOICE_ACCESS_KEY": "sk-123"})

        assert "secret-run" in out
        assert bash_calls and bash_calls[0]["env"] == {"VOLCENGINE_VOICE_ACCESS_KEY": "sk-123"}
        assert bash_calls[0]["hard_timeout"] == 30
        assert not shell_calls  # 带 env 不走持久 shell 会话路径

    def test_env_bash_exec_dead_container_tagged(self, sandbox):
        def boom(*args, **kwargs):
            raise RuntimeError("Error response: Container abc is not running")

        sandbox._client.bash.exec = boom
        from deerflow.community.aio_sandbox.aio_sandbox import SANDBOX_DEAD_MARKER

        out = sandbox.execute_command("x", env={"K": "V"})
        assert SANDBOX_DEAD_MARKER in out
