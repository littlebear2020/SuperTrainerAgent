"""Size-guard tests for write_file_tool.

Without a ceiling, an oversized single-shot write is handed straight to the
sandbox and the run stalls until the LLM stream chunk-gap timeout fires, which
surfaces as an opaque timeout rather than an actionable message. These tests
pin the guard's behaviour: the rejection message, the append-mode carve-out,
and the env-var override (including 0 = disabled).

They exercise only the tool's internal guard — the sandbox chain is patched to
no-ops — so they stay fast and hermetic.
"""

from types import SimpleNamespace

import pytest

from deerflow.sandbox.tools import write_file_tool


def _call_write_file(monkeypatch, *, content: str, append: bool = False, writes: list | None = None) -> str:
    """Invoke write_file_tool with the sandbox chain stubbed out.

    The guard runs before any sandbox call, so a rejected write never reaches
    the stub; an accepted one lands on the recording sandbox and returns "OK".
    Pass ``writes`` to inspect what actually reached the sandbox.
    """
    recorded = writes if writes is not None else []

    class RecordingSandbox:
        def __init__(self) -> None:
            self.id = "sandbox-size-guard"

        def write_file(self, path: str, content: str, append: bool = False) -> None:
            recorded.append((path, content, append))

    sandbox = RecordingSandbox()
    monkeypatch.setattr("deerflow.sandbox.tools.ensure_sandbox_initialized", lambda runtime: sandbox)
    monkeypatch.setattr("deerflow.sandbox.tools.ensure_thread_directories_exist", lambda runtime: None)
    monkeypatch.setattr("deerflow.sandbox.tools.is_local_sandbox", lambda runtime: False)

    return write_file_tool.func(
        runtime=SimpleNamespace(state={}, context={"thread_id": "thread-1"}, config={}),
        description="write a document",
        path="/mnt/user-data/workspace/doc.txt",
        content=content,
        append=append,
    )


def test_write_below_limit_is_unchanged(monkeypatch) -> None:
    writes: list = []
    payload = "a" * (79 * 1024)
    result = _call_write_file(monkeypatch, content=payload, writes=writes)

    assert result == "OK"
    assert writes == [("/mnt/user-data/workspace/doc.txt", payload, False)]


def test_rejected_write_never_reaches_the_sandbox(monkeypatch) -> None:
    """The point of the guard is to fail fast — an oversized payload must be
    refused before it is handed down to stall on the wire."""
    writes: list = []
    result = _call_write_file(monkeypatch, content="a" * (81 * 1024), writes=writes)

    assert result.startswith("Error: write_file content")
    assert writes == []


def test_write_above_limit_returns_actionable_error(monkeypatch) -> None:
    """The rejection must name the actual size and the cap so the model can
    decide how much to trim, and must point at the two supported ways out."""
    result = _call_write_file(monkeypatch, content="a" * (81 * 1024))

    assert result.startswith("Error: write_file content")
    assert str(81 * 1024) in result
    assert str(80 * 1024) in result
    assert "str_replace" in result
    assert "append=True" in result


def test_multibyte_content_is_measured_in_utf8_bytes(monkeypatch) -> None:
    """A 3-byte-per-char payload trips the cap well before it reaches 80K
    characters — the limit is on the wire size, not the character count."""
    content = "中" * (40 * 1024)  # 40K chars = 120 KB of UTF-8
    result = _call_write_file(monkeypatch, content=content)

    assert result.startswith("Error: write_file content")
    assert str(len(content.encode("utf-8"))) in result


def test_append_bypasses_the_limit(monkeypatch) -> None:
    """append=True is the sanctioned way to write a long document in chunks,
    so the cap must not block it."""
    result = _call_write_file(monkeypatch, content="a" * (200 * 1024), append=True)
    assert result == "OK"


def test_env_override_raises_the_limit(monkeypatch) -> None:
    monkeypatch.setenv("DEERFLOW_WRITE_FILE_MAX_BYTES", str(300 * 1024))
    result = _call_write_file(monkeypatch, content="a" * (150 * 1024))
    assert result == "OK"


def test_env_override_lowers_the_limit(monkeypatch) -> None:
    monkeypatch.setenv("DEERFLOW_WRITE_FILE_MAX_BYTES", str(1024))
    result = _call_write_file(monkeypatch, content="a" * 2048)

    assert result.startswith("Error: write_file content")
    assert "1024-byte" in result


@pytest.mark.parametrize("raw", ["0", "-1"])
def test_non_positive_env_override_disables_the_limit(monkeypatch, raw: str) -> None:
    """0 is the documented opt-out; any non-positive value takes the same path,
    matching upstream so future syncs do not silently flip the semantics."""
    monkeypatch.setenv("DEERFLOW_WRITE_FILE_MAX_BYTES", raw)
    result = _call_write_file(monkeypatch, content="a" * (500 * 1024))
    assert result == "OK"


@pytest.mark.parametrize("raw", ["lots", "", "12.5", "80k"])
def test_malformed_env_override_falls_back_to_default(monkeypatch, raw: str) -> None:
    """A typo in the env var must not disable the guard or crash the tool —
    silently losing the ceiling on every write would be the worse failure."""
    monkeypatch.setenv("DEERFLOW_WRITE_FILE_MAX_BYTES", raw)
    result = _call_write_file(monkeypatch, content="a" * (100 * 1024))
    assert result.startswith("Error: write_file content")
