"""Line-range behaviour of the ``read_file`` sandbox tool.

The docstring advertises ``start_line`` and ``end_line`` as independently
optional ("Omit to start at the first line" / "Omit to read through the last
line"), but the implementation only sliced when *both* were supplied. Passing
``start_line`` alone silently returned the whole file — the worst failure mode
for an agent reading a 5000-line log, because the output looks plausible and
the agent has no signal it was ignored.

These tests drive ``read_file_tool.func`` against a fake sandbox so the
line-slicing contract is pinned without needing a real container.
"""

from types import SimpleNamespace

import pytest

from deerflow.sandbox.tools import read_file_tool

FIVE_LINES = "line1\nline2\nline3\nline4\nline5"


class FakeSandbox:
    """Minimal sandbox returning a fixed body, recording the paths it saw."""

    id = "fake-sandbox"

    def __init__(self, content: str = FIVE_LINES) -> None:
        self.content = content
        self.reads: list[str] = []

    def read_file(self, path: str) -> str:
        self.reads.append(path)
        return self.content


@pytest.fixture
def sandbox(monkeypatch) -> FakeSandbox:
    fake = FakeSandbox()
    monkeypatch.setattr("deerflow.sandbox.tools.ensure_sandbox_initialized", lambda runtime: fake)
    monkeypatch.setattr("deerflow.sandbox.tools.ensure_thread_directories_exist", lambda runtime: None)
    monkeypatch.setattr("deerflow.sandbox.tools.is_local_sandbox", lambda runtime: False)
    return fake


@pytest.fixture
def runtime() -> SimpleNamespace:
    return SimpleNamespace(state={}, context={"thread_id": "thread-1"}, config={})


def read(runtime, **kwargs) -> str:
    return read_file_tool.func(
        runtime=runtime,
        description="read a file",
        path="/mnt/user-data/workspace/sample.txt",
        **kwargs,
    )


class TestBothBoundsGiven:
    def test_slices_inclusive_range(self, sandbox, runtime):
        assert read(runtime, start_line=2, end_line=4) == "line2\nline3\nline4"

    def test_single_line_range(self, sandbox, runtime):
        assert read(runtime, start_line=3, end_line=3) == "line3"


class TestOneBoundGiven:
    def test_start_line_alone_reads_to_end_of_file(self, sandbox, runtime):
        """The regression: this used to return all five lines."""
        assert read(runtime, start_line=3) == "line3\nline4\nline5"

    def test_end_line_alone_reads_from_first_line(self, sandbox, runtime):
        assert read(runtime, end_line=2) == "line1\nline2"

    def test_start_line_one_is_the_whole_file(self, sandbox, runtime):
        assert read(runtime, start_line=1) == FIVE_LINES


class TestNoBounds:
    def test_returns_whole_file(self, sandbox, runtime):
        assert read(runtime) == FIVE_LINES


class TestInvalidRanges:
    def test_start_line_below_one_is_rejected(self, sandbox, runtime):
        """0 or negative must not silently wrap via Python's negative indexing."""
        result = read(runtime, start_line=0)
        assert "start_line" in result
        assert "line1" not in result

    def test_negative_start_line_is_rejected(self, sandbox, runtime):
        result = read(runtime, start_line=-2)
        assert "start_line" in result
        assert "line4" not in result

    def test_end_line_below_one_is_rejected(self, sandbox, runtime):
        result = read(runtime, end_line=0)
        assert "end_line" in result
        assert "line1" not in result

    def test_start_after_end_is_rejected_rather_than_returning_empty(self, sandbox, runtime):
        result = read(runtime, start_line=4, end_line=2)
        assert "start_line" in result and "end_line" in result

    def test_start_line_past_eof_says_so(self, sandbox, runtime):
        """Distinguishable from an empty file, which is its own message."""
        result = read(runtime, start_line=99)
        assert "exceeds" in result

    def test_end_line_past_eof_clamps_to_last_line(self, sandbox, runtime):
        """Over-reaching the end is a normal, useful request, not an error."""
        assert read(runtime, start_line=4, end_line=99) == "line4\nline5"


class TestEmptyFile:
    def test_empty_file_reports_empty_not_a_range_error(self, monkeypatch, runtime):
        fake = FakeSandbox(content="")
        monkeypatch.setattr("deerflow.sandbox.tools.ensure_sandbox_initialized", lambda runtime: fake)
        monkeypatch.setattr("deerflow.sandbox.tools.ensure_thread_directories_exist", lambda runtime: None)
        monkeypatch.setattr("deerflow.sandbox.tools.is_local_sandbox", lambda runtime: False)

        assert read(runtime, start_line=2) == "(empty)"


@pytest.fixture
def read_limit(monkeypatch):
    """Shrink the configured read_file character budget to a test-friendly size."""

    def _set(max_chars: int) -> None:
        config = SimpleNamespace(sandbox=SimpleNamespace(read_file_output_max_chars=max_chars))
        monkeypatch.setattr("deerflow.config.app_config.get_app_config", lambda: config)

    return _set


class TestTruncationLineBoundary:
    """A truncated read must cut at a line boundary and name the resume line."""

    def test_cut_lands_on_a_line_boundary_and_names_the_next_start_line(self, sandbox, runtime, read_limit):
        lines = [f"{i:03d} " + "x" * 20 for i in range(1, 61)]
        sandbox.content = "\n".join(lines) + "\n"
        read_limit(300)

        result = read(runtime)

        assert len(result) <= 300
        kept = result[: result.index("... [truncated:")]
        assert kept.endswith("\n"), "a cut on a line boundary ends with a complete line"
        shown = kept.count("\n")
        assert kept == "\n".join(lines[:shown]) + "\n"
        assert f"showing first {shown} of 60 lines" in result
        assert f"Continue with start_line={shown + 1}" in result
        # Following the marker resumes at the first unread line: no gap, no overlap.
        read_limit(50000)
        assert read(runtime, start_line=shown + 1) == "\n".join(lines[shown:])

    def test_overlong_line_falls_back_to_a_char_cut_that_names_the_line(self, sandbox, runtime, read_limit):
        """A line with no newline within 4,096 chars of the cut keeps the char cut."""
        sandbox.content = "a\nb\n" + "X" * 8000
        read_limit(6000)

        result = read(runtime)

        assert len(result) <= 6000
        assert result.startswith("a\nb\nXXXX")
        # Snapping back to the line 2 boundary would drop almost the whole budget.
        assert result.count("X") > 4096
        assert "cut inside line 3 of 3 lines" in result
        # Longer than any read can return, so bash is the only honest pointer.
        assert "cut -c" in result
        assert "Continue with start_line" not in result

    def test_ranged_read_marker_uses_absolute_file_line_numbers(self, sandbox, runtime, read_limit):
        lines = [f"{i:03d} " + "x" * 20 for i in range(1, 61)]
        sandbox.content = "\n".join(lines) + "\n"
        read_limit(300)

        result = read(runtime, start_line=21)

        assert len(result) <= 300
        kept = result[: result.index("... [truncated:")]
        shown = kept.count("\n")
        assert kept == "\n".join(lines[20 : 20 + shown]) + "\n"
        # The provider renumbers the slice from 1; the marker must speak in file lines.
        assert f"showing lines 21-{20 + shown} of 21-60 lines" in result
        assert "showing first" not in result
        assert f"Continue with start_line={20 + shown + 1}" in result
        read_limit(50000)
        assert read(runtime, start_line=20 + shown + 1) == "\n".join(lines[20 + shown :])

    def test_tiny_budget_marker_still_says_the_output_was_cut(self, sandbox, runtime, read_limit):
        """A budget too small for text plus a marker must not return a bare prefix."""
        sandbox.content = "".join(f"{i:04d} " + "x" * 55 + "\n" for i in range(1, 101))
        read_limit(60)

        result = read(runtime)

        assert len(result) <= 60
        assert result.startswith("... [truncated:")
        assert "chars exceed" in result
        assert not result.startswith("0001")
