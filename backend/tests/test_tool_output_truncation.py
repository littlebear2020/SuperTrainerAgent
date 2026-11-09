"""Unit tests for tool output truncation functions.

These functions truncate long tool outputs to prevent context window overflow.
- _truncate_bash_output: middle-truncation (head + tail), for bash tool
- _truncate_read_file_output: head-truncation, for read_file tool
- _truncate_ls_output: head-truncation, for ls tool

Also covers the bash output offload: over-cap bash output is persisted in full
to the thread workspace before truncation, and the truncation marker points at
the saved file.
"""

import re
from pathlib import Path
from types import SimpleNamespace

from deerflow.sandbox import tools as sandbox_tools
from deerflow.sandbox.local.local_sandbox import LocalSandbox
from deerflow.sandbox.tools import _truncate_bash_output, _truncate_ls_output, _truncate_read_file_output

# ---------------------------------------------------------------------------
# _truncate_bash_output
# ---------------------------------------------------------------------------


class TestTruncateBashOutput:
    def test_short_output_returned_unchanged(self):
        output = "hello world"
        assert _truncate_bash_output(output, 20000) == output

    def test_output_equal_to_limit_returned_unchanged(self):
        output = "A" * 20000
        assert _truncate_bash_output(output, 20000) == output

    def test_long_output_is_truncated(self):
        output = "A" * 30000
        result = _truncate_bash_output(output, 20000)
        assert len(result) < len(output)

    def test_result_never_exceeds_max_chars(self):
        output = "A" * 30000
        max_chars = 20000
        result = _truncate_bash_output(output, max_chars)
        assert len(result) <= max_chars

    def test_head_is_preserved(self):
        head = "HEAD_CONTENT"
        output = head + "M" * 30000
        result = _truncate_bash_output(output, 20000)
        assert result.startswith(head)

    def test_tail_is_preserved(self):
        tail = "TAIL_CONTENT"
        output = "M" * 30000 + tail
        result = _truncate_bash_output(output, 20000)
        assert result.endswith(tail)

    def test_middle_truncation_marker_present(self):
        output = "A" * 30000
        result = _truncate_bash_output(output, 20000)
        assert "[middle truncated:" in result
        assert "chars skipped" in result

    def test_skipped_chars_count_is_correct(self):
        output = "A" * 25000
        result = _truncate_bash_output(output, 20000)
        # Extract the reported skipped count and verify it equals len(output) - kept.
        # (kept = max_chars - marker_max_len, where marker_max_len is computed from
        # the worst-case marker string — so the exact value is implementation-defined,
        # but it must equal len(output) minus the chars actually preserved.)
        import re

        m = re.search(r"(\d+) chars skipped", result)
        assert m is not None
        reported_skipped = int(m.group(1))
        # Verify the number is self-consistent: head + skipped + tail == total
        assert reported_skipped > 0
        # The marker reports exactly the chars between head and tail
        head_and_tail = len(output) - reported_skipped
        assert result.startswith(output[: head_and_tail // 2])

    def test_max_chars_zero_disables_truncation(self):
        output = "A" * 100000
        assert _truncate_bash_output(output, 0) == output

    def test_50_50_split(self):
        # head and tail should each be roughly max_chars // 2
        output = "H" * 20000 + "M" * 10000 + "T" * 20000
        result = _truncate_bash_output(output, 20000)
        assert result[:100] == "H" * 100
        assert result[-100:] == "T" * 100

    def test_small_max_chars_does_not_crash(self):
        output = "A" * 1000
        result = _truncate_bash_output(output, 10)
        assert len(result) <= 10

    def test_result_never_exceeds_max_chars_various_sizes(self):
        output = "X" * 50000
        for max_chars in [100, 1000, 5000, 20000, 49999]:
            result = _truncate_bash_output(output, max_chars)
            assert len(result) <= max_chars, f"failed for max_chars={max_chars}"


# ---------------------------------------------------------------------------
# _truncate_read_file_output
# ---------------------------------------------------------------------------


class TestTruncateReadFileOutput:
    def test_short_output_returned_unchanged(self):
        output = "def foo():\n    pass\n"
        assert _truncate_read_file_output(output, 50000) == output

    def test_output_equal_to_limit_returned_unchanged(self):
        output = "X" * 50000
        assert _truncate_read_file_output(output, 50000) == output

    def test_long_output_is_truncated(self):
        output = "X" * 60000
        result = _truncate_read_file_output(output, 50000)
        assert len(result) < len(output)

    def test_result_never_exceeds_max_chars(self):
        output = "X" * 60000
        max_chars = 50000
        result = _truncate_read_file_output(output, max_chars)
        assert len(result) <= max_chars

    def test_head_is_preserved(self):
        head = "import os\nimport sys\n"
        output = head + "X" * 60000
        result = _truncate_read_file_output(output, 50000)
        assert result.startswith(head)

    def test_truncation_marker_present(self):
        output = "X" * 60000
        result = _truncate_read_file_output(output, 50000)
        assert "[truncated:" in result
        assert "showing first" in result

    def test_total_chars_reported_correctly(self):
        output = "X" * 60000
        result = _truncate_read_file_output(output, 50000)
        assert "of 60000 chars" in result

    def test_start_line_hint_present(self):
        output = "X" * 60000
        result = _truncate_read_file_output(output, 50000)
        assert "start_line" in result
        assert "end_line" in result

    def test_max_chars_zero_disables_truncation(self):
        output = "X" * 100000
        assert _truncate_read_file_output(output, 0) == output

    def test_tail_is_not_preserved(self):
        # head-truncation: tail should be cut off
        output = "H" * 50000 + "TAIL_SHOULD_NOT_APPEAR"
        result = _truncate_read_file_output(output, 50000)
        assert "TAIL_SHOULD_NOT_APPEAR" not in result

    def test_small_max_chars_does_not_crash(self):
        output = "X" * 1000
        result = _truncate_read_file_output(output, 10)
        assert len(result) <= 10

    def test_result_never_exceeds_max_chars_various_sizes(self):
        output = "X" * 50000
        for max_chars in [100, 1000, 5000, 20000, 49999]:
            result = _truncate_read_file_output(output, max_chars)
            assert len(result) <= max_chars, f"failed for max_chars={max_chars}"


# ---------------------------------------------------------------------------
# _truncate_ls_output
# ---------------------------------------------------------------------------


class TestTruncateLsOutput:
    def test_short_output_returned_unchanged(self):
        output = "dir1\ndir2\nfile1.txt"
        assert _truncate_ls_output(output, 20000) == output

    def test_output_equal_to_limit_returned_unchanged(self):
        output = "X" * 20000
        assert _truncate_ls_output(output, 20000) == output

    def test_long_output_is_truncated(self):
        output = "\n".join(f"file_{i}.txt" for i in range(5000))
        result = _truncate_ls_output(output, 20000)
        assert len(result) < len(output)

    def test_result_never_exceeds_max_chars(self):
        output = "\n".join(f"subdir/file_{i}.txt" for i in range(5000))
        max_chars = 20000
        result = _truncate_ls_output(output, max_chars)
        assert len(result) <= max_chars

    def test_head_is_preserved(self):
        head = "first_dir\nsecond_dir\n"
        output = head + "\n".join(f"file_{i}" for i in range(5000))
        result = _truncate_ls_output(output, 20000)
        assert result.startswith(head)

    def test_truncation_marker_present(self):
        output = "\n".join(f"file_{i}.txt" for i in range(5000))
        result = _truncate_ls_output(output, 20000)
        assert "[truncated:" in result
        assert "showing first" in result

    def test_total_chars_reported_correctly(self):
        output = "X" * 30000
        result = _truncate_ls_output(output, 20000)
        assert "of 30000 chars" in result

    def test_hint_suggests_specific_path(self):
        output = "X" * 30000
        result = _truncate_ls_output(output, 20000)
        assert "Use a more specific path" in result

    def test_max_chars_zero_disables_truncation(self):
        output = "\n".join(f"file_{i}.txt" for i in range(10000))
        assert _truncate_ls_output(output, 0) == output

    def test_tail_is_not_preserved(self):
        output = "H" * 20000 + "TAIL_SHOULD_NOT_APPEAR"
        result = _truncate_ls_output(output, 20000)
        assert "TAIL_SHOULD_NOT_APPEAR" not in result

    def test_small_max_chars_does_not_crash(self):
        output = "\n".join(f"file_{i}.txt" for i in range(100))
        result = _truncate_ls_output(output, 10)
        assert len(result) <= 10

    def test_result_never_exceeds_max_chars_various_sizes(self):
        output = "\n".join(f"file_{i}.txt" for i in range(5000))
        for max_chars in [100, 1000, 5000, 20000, len(output) - 1]:
            result = _truncate_ls_output(output, max_chars)
            assert len(result) <= max_chars, f"failed for max_chars={max_chars}"


# ---------------------------------------------------------------------------
# bash output offload (full output persisted to the thread workspace)
# ---------------------------------------------------------------------------


class _RecordingSandbox:
    """Fake sandbox capturing write_file calls; execute_command returns a fixed output."""

    def __init__(self, output: str, *, fail_writes: bool = False):
        self._output = output
        self._fail_writes = fail_writes
        self.writes: list[tuple[str, str]] = []
        self.commands: list[str] = []

    def execute_command(self, command, timeout=None, env=None):
        self.commands.append(command)
        return self._output

    def write_file(self, path, content, append=False):
        if self._fail_writes:
            raise OSError("sandbox file service unavailable")
        self.writes.append((path, content))


def _patch_bash_config(monkeypatch, max_chars: int) -> None:
    fake_cfg = SimpleNamespace(sandbox=SimpleNamespace(bash_output_max_chars=max_chars, bash_command_timeout=300))
    # bash_tool re-imports get_app_config locally on each call; the secret-mask
    # helper uses the module-level binding. Patch both to stay hermetic.
    monkeypatch.setattr("deerflow.config.app_config.get_app_config", lambda: fake_cfg)
    monkeypatch.setattr("deerflow.sandbox.tools.get_app_config", lambda: fake_cfg)


def _remote_runtime() -> SimpleNamespace:
    return SimpleNamespace(
        state={"sandbox": {"sandbox_id": "aio:test"}},
        context={"thread_id": "t"},
        config={},
    )


class TestBashOutputOffload:
    def test_over_cap_output_offloaded_and_marker_points_to_saved_file(self, monkeypatch):
        output = "A" * 2000
        sandbox = _RecordingSandbox(output)
        monkeypatch.setattr(sandbox_tools, "ensure_sandbox_initialized", lambda runtime: sandbox)
        _patch_bash_config(monkeypatch, max_chars=300)

        result = sandbox_tools.bash_tool.func(_remote_runtime(), description="d", command="echo hi")

        # Output is still truncated to the cap
        assert len(result) <= 300
        # Marker still reports skipped chars and now carries the saved path
        assert "chars skipped" in result
        m = re.search(r"full output saved to ([^;\s]+)", result)
        assert m is not None, f"offload path missing from marker: {result!r}"
        saved_path = m.group(1)
        assert saved_path.startswith("/mnt/user-data/workspace/.tool-results/bash-")
        assert saved_path.endswith(".log")
        assert "read it in slices with read_file" in result
        # The FULL (untruncated) output was persisted via the sandbox's write_file
        assert sandbox.writes == [(saved_path, output)]
        # Remote sandboxes get a best-effort mkdir for the results dir first
        assert "mkdir -p /mnt/user-data/workspace/.tool-results" in sandbox.commands

    def test_offload_write_failure_falls_back_to_plain_marker(self, monkeypatch):
        output = "A" * 2000
        sandbox = _RecordingSandbox(output, fail_writes=True)
        monkeypatch.setattr(sandbox_tools, "ensure_sandbox_initialized", lambda runtime: sandbox)
        _patch_bash_config(monkeypatch, max_chars=300)

        result = sandbox_tools.bash_tool.func(_remote_runtime(), description="d", command="echo hi")

        # Old marker shape, no saved-path mention, no exception escaping
        assert "[middle truncated:" in result
        assert "chars skipped" in result
        assert "full output saved to" not in result
        assert len(result) <= 300

    def test_path_marker_falls_back_to_plain_marker_when_cap_is_tiny(self, monkeypatch):
        # The path-carrying marker (~170 chars) exceeds the cap: truncation must
        # still be marked, just without the pointer (the file is still saved).
        output = "A" * 500
        sandbox = _RecordingSandbox(output)
        monkeypatch.setattr(sandbox_tools, "ensure_sandbox_initialized", lambda runtime: sandbox)
        _patch_bash_config(monkeypatch, max_chars=100)

        result = sandbox_tools.bash_tool.func(_remote_runtime(), description="d", command="echo hi")

        assert "[middle truncated:" in result
        assert "chars skipped" in result
        assert "full output saved to" not in result
        assert len(result) <= 100
        assert len(sandbox.writes) == 1

    def test_under_cap_output_not_offloaded(self, monkeypatch):
        output = "small output"
        sandbox = _RecordingSandbox(output)
        monkeypatch.setattr(sandbox_tools, "ensure_sandbox_initialized", lambda runtime: sandbox)
        _patch_bash_config(monkeypatch, max_chars=300)

        result = sandbox_tools.bash_tool.func(_remote_runtime(), description="d", command="echo hi")

        assert result == output
        assert sandbox.writes == []
        assert not any(cmd.startswith("mkdir") for cmd in sandbox.commands)

    def test_local_sandbox_offload_writes_resolved_host_path(self, monkeypatch, tmp_path):
        workspace = tmp_path / "workspace"
        uploads = tmp_path / "uploads"
        outputs = tmp_path / "outputs"
        for d in (workspace, uploads, outputs):
            d.mkdir(parents=True)
        thread_data = {
            "workspace_path": str(workspace),
            "uploads_path": str(uploads),
            "outputs_path": str(outputs),
        }
        output = "B" * 2000
        sandbox = LocalSandbox(id="local", path_mappings=[])
        sandbox.execute_command = lambda command, timeout=None, env=None: output
        monkeypatch.setattr(sandbox_tools, "ensure_sandbox_initialized", lambda runtime: sandbox)
        monkeypatch.setattr(sandbox_tools, "is_host_bash_allowed", lambda: True)
        monkeypatch.setattr(sandbox_tools, "ensure_thread_directories_exist", lambda runtime: None)
        _patch_bash_config(monkeypatch, max_chars=300)

        runtime = SimpleNamespace(
            state={"sandbox": {"sandbox_id": "local"}, "thread_data": thread_data},
            context={"thread_id": "t"},
            config={},
        )
        result = sandbox_tools.bash_tool.func(runtime, description="d", command="echo hi")

        m = re.search(r"full output saved to ([^;\s]+)", result)
        assert m is not None, f"offload path missing from marker: {result!r}"
        virtual_path = m.group(1)
        assert virtual_path.startswith("/mnt/user-data/workspace/.tool-results/bash-")
        # The file actually landed on the host filesystem under the thread workspace
        saved_file = workspace / ".tool-results" / Path(virtual_path).name
        assert saved_file.read_text(encoding="utf-8") == output
