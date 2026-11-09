"""Tests for Remotion rendering pipeline."""

from __future__ import annotations

import base64
from pathlib import Path
from unittest.mock import patch

import pytest

from app.gateway.video_gen.pipelines.remotion_run import run_remotion

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

# Minimal valid mp4 header (ftyp box) for base64 decode test
_FAKE_MP4 = base64.b64encode(b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom").decode()


class _FakeSandbox:
    """Fake sandbox for testing."""

    def __init__(self, files=None, output_exists=True):
        self._files = dict(files or {})
        self._output_exists = output_exists
        self.id = "fake-sandbox"

    def write_file(self, path: str, content: str) -> None:
        self._files[path] = content

    def execute_command(self, cmd: str) -> str:
        if "base64" in cmd:
            return _FAKE_MP4
        return "Rendered 150 frames. Output: /mnt/user-data/outputs/out.mp4"

    def read_file(self, path: str) -> str:
        return self._files.get(path, "")

    def list_dir(self, path: str, max_depth=2) -> list[str]:
        if self._output_exists:
            return ["out.mp4"]
        return []


class _FakeLocalSandbox(_FakeSandbox):
    """Fake local sandbox with resolve_virtual_path."""

    def __init__(self, local_output_path: Path | None = None, **kw):
        super().__init__(**kw)
        self._local_output = local_output_path

    def resolve_virtual_path(self, path: str) -> str:
        return str(self._local_output) if self._local_output else path


class _FakeProgress:
    def __init__(self):
        self.updates = []
        self._result = {}

    async def __call__(self, **kwargs):
        self.updates.append(kwargs)

    async def set_result(self, **kwargs):
        self._result.update(kwargs)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_remotion_validates_composition(tmp_path):
    sandbox = _FakeSandbox()
    progress = _FakeProgress()

    with pytest.raises(ValueError, match="composition"):
        await run_remotion(
            {"props": {}},
            task_dir=tmp_path,
            sandbox=sandbox,
            progress=progress,
        )


@pytest.mark.asyncio
async def test_remotion_writes_props(tmp_path):
    sandbox = _FakeSandbox(output_exists=False)
    progress = _FakeProgress()

    with pytest.raises(RuntimeError, match="did not produce"):
        await run_remotion(
            {"composition": "MyVideo", "props": {"title": "Test"}},
            task_dir=tmp_path,
            sandbox=sandbox,
            progress=progress,
        )

    # Props were written to sandbox
    assert "/mnt/user-data/workspace/props.json" in sandbox._files


@pytest.mark.asyncio
async def test_remotion_missing_output_raises(tmp_path):
    sandbox = _FakeSandbox(output_exists=False)
    progress = _FakeProgress()

    with pytest.raises(RuntimeError, match="did not produce"):
        await run_remotion(
            {"composition": "MyVideo"},
            task_dir=tmp_path,
            sandbox=sandbox,
            progress=progress,
        )


@pytest.mark.asyncio
async def test_remotion_progress_updates(tmp_path):
    sandbox = _FakeSandbox(output_exists=False)
    progress = _FakeProgress()

    with pytest.raises(RuntimeError):
        await run_remotion(
            {"composition": "MyVideo"},
            task_dir=tmp_path,
            sandbox=sandbox,
            progress=progress,
        )

    # Check progress was called with increasing values
    progress_values = [u.get("progress", 0) for u in progress.updates if "progress" in u]
    assert len(progress_values) >= 3
    assert progress_values == sorted(progress_values)


@pytest.mark.asyncio
async def test_remotion_success_nonlocal_sandbox(tmp_path):
    """Success path with non-local sandbox (base64 fetch)."""
    sandbox = _FakeSandbox(output_exists=True)
    progress = _FakeProgress()

    await run_remotion(
        {"composition": "MyVideo", "props": {"title": "Hello"}},
        task_dir=tmp_path,
        sandbox=sandbox,
        progress=progress,
    )

    output = tmp_path / "output.mp4"
    assert output.exists()
    assert output.stat().st_size > 0
    assert progress._result.get("output_path") == str(output)


@pytest.mark.asyncio
async def test_remotion_success_local_sandbox(tmp_path):
    """Success path with local sandbox (direct file copy)."""
    fake_output = tmp_path / "sandbox_output.mp4"
    fake_output.write_bytes(b"\x00\x00\x00\x18ftypmp42")

    sandbox = _FakeLocalSandbox(
        local_output_path=fake_output,
        output_exists=True,
    )
    progress = _FakeProgress()

    with patch(
        "deerflow.sandbox.local.local_sandbox.LocalSandbox",
        _FakeLocalSandbox,
    ):
        await run_remotion(
            {"composition": "MyVideo"},
            task_dir=tmp_path,
            sandbox=sandbox,
            progress=progress,
        )

    output = tmp_path / "output.mp4"
    assert output.exists()
    assert output.read_bytes() == b"\x00\x00\x00\x18ftypmp42"
    assert progress._result.get("file_size", 0) > 0


def test_validate_remotion_id_rejects_shell_metacharacters():
    """composition IDs with shell metacharacters must be rejected."""
    from app.gateway.video_gen.pipelines.remotion_run import _validate_remotion_id

    # Valid IDs
    _validate_remotion_id("DataDrivenVideo", "composition")
    _validate_remotion_id("my-comp_123", "composition")

    # Invalid — shell injection attempts
    for bad in ["; rm -rf /", "$(whoami)", "foo && bar", "x`whoami`", "", "  ", "a;b"]:
        with pytest.raises(ValueError, match="invalid composition"):
            _validate_remotion_id(bad, "composition")
