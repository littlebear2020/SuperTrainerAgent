"""Tests for run_remotion_storyboard pipeline."""

import base64
from unittest.mock import patch

import pytest

from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame


def _make_storyboard(n_frames=2, fps=30):
    cfg = StoryboardConfig(video_type="remotion", width=1080, height=1920, fps=fps)
    frames = [
        StoryboardFrame(
            index=i,
            narration=f"scene {i}",
            audio_path=f"/tmp/audio/frame_{i:03d}.mp3",
            duration=5.0,
        )
        for i in range(n_frames)
    ]
    sb = Storyboard(title="test", config=cfg, frames=frames)
    sb.recompute_duration()
    return sb


class _FakeSandbox:
    def __init__(self, output_exists=True, is_local=False):
        self.id = "fake"
        self._files = {}
        self._commands = []
        self._output_exists = output_exists
        self._is_local = is_local
        self._local_output = None

    def write_file(self, path, content, **kw):
        self._files[path] = content

    def execute_command(self, cmd, **kw):
        self._commands.append(cmd)
        if "base64" in cmd:
            return base64.b64encode(b"\x00\x00\x00\x18ftypmp42").decode()
        return "Rendered OK"

    def list_dir(self, path, max_depth=1):
        if self._output_exists:
            return ["out.mp4"]
        return []

    def upload_file_stream(self, path, local_path):
        pass


class _FakeLocalSandbox(_FakeSandbox):
    def __init__(self, output_exists=True, local_output_path=None):
        super().__init__(output_exists=output_exists, is_local=True)
        self._local_output = local_output_path

    def resolve_virtual_path(self, path):
        return str(self._local_output) if self._local_output else path


class _FakeProgress:
    def __init__(self):
        self.updates = []
        self._result = {}

    async def __call__(self, **kwargs):
        self.updates.append(kwargs)

    async def set_result(self, **kwargs):
        self._result.update(kwargs)


@pytest.mark.asyncio
async def test_storyboard_render_orchestrates_all_steps(tmp_path):
    from app.gateway.video_gen.pipelines.remotion_run import run_remotion_storyboard

    sb = _make_storyboard()
    sandbox = _FakeSandbox(output_exists=True)

    # Create fake audio files
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    for i in range(2):
        (audio_dir / f"frame_{i:03d}.mp3").write_bytes(b"\x00" * 50)

    progress = _FakeProgress()
    deploy_return = ("DataDrivenVideo", "/mnt/user-data/workspace", "")

    with (
        patch("app.gateway.video_gen.pipelines.remotion_run.deploy_template", return_value=deploy_return) as mock_deploy,
        patch("app.gateway.video_gen.pipelines.remotion_run.upload_audio_files") as mock_upload,
    ):
        await run_remotion_storyboard(
            sb,
            task_dir=tmp_path,
            sandbox=sandbox,
            progress=progress,
        )

    mock_deploy.assert_called_once()
    mock_upload.assert_called_once()
    # Verify render command was executed
    assert any("npx remotion render" in cmd for cmd in sandbox._commands)


@pytest.mark.asyncio
async def test_storyboard_render_reports_progress(tmp_path):
    from app.gateway.video_gen.pipelines.remotion_run import run_remotion_storyboard

    sb = _make_storyboard()
    sandbox = _FakeSandbox(output_exists=True)
    progress = _FakeProgress()

    with patch("app.gateway.video_gen.pipelines.remotion_run.deploy_template", return_value=("DataDrivenVideo", "/mnt/user-data/workspace", "")), patch("app.gateway.video_gen.pipelines.remotion_run.upload_audio_files"):
        await run_remotion_storyboard(
            sb,
            task_dir=tmp_path,
            sandbox=sandbox,
            progress=progress,
        )

    progress_values = [u.get("progress", 0) for u in progress.updates if "progress" in u]
    assert len(progress_values) >= 4  # 10, 20, 30, 85, 95, 100
    assert progress_values == sorted(progress_values)


@pytest.mark.asyncio
async def test_storyboard_render_fails_on_no_output(tmp_path):
    from app.gateway.video_gen.pipelines.remotion_run import run_remotion_storyboard

    sb = _make_storyboard()
    sandbox = _FakeSandbox(output_exists=False)
    progress = _FakeProgress()

    with patch("app.gateway.video_gen.pipelines.remotion_run.deploy_template", return_value=("DataDrivenVideo", "/mnt/user-data/workspace", "")), patch("app.gateway.video_gen.pipelines.remotion_run.upload_audio_files"):
        with pytest.raises(RuntimeError, match="did not produce"):
            await run_remotion_storyboard(
                sb,
                task_dir=tmp_path,
                sandbox=sandbox,
                progress=progress,
            )


@pytest.mark.asyncio
async def test_storyboard_render_local_sandbox_copies_file(tmp_path):
    from app.gateway.video_gen.pipelines.remotion_run import run_remotion_storyboard

    fake_output = tmp_path / "sandbox_out.mp4"
    fake_output.write_bytes(b"\x00\x00\x00\x18ftypmp42")

    sb = _make_storyboard()
    sandbox = _FakeLocalSandbox(output_exists=True, local_output_path=fake_output)
    progress = _FakeProgress()

    with (
        patch("app.gateway.video_gen.pipelines.remotion_run.deploy_template", return_value=("DataDrivenVideo", "/mnt/user-data/workspace", "")),
        patch("app.gateway.video_gen.pipelines.remotion_run.upload_audio_files"),
        patch("deerflow.sandbox.local.local_sandbox.LocalSandbox", _FakeLocalSandbox),
    ):
        await run_remotion_storyboard(
            sb,
            task_dir=tmp_path,
            sandbox=sandbox,
            progress=progress,
        )

    output = tmp_path / "output.mp4"
    assert output.exists()


@pytest.mark.asyncio
async def test_storyboard_render_docker_sandbox_uses_base64(tmp_path):
    from app.gateway.video_gen.pipelines.remotion_run import run_remotion_storyboard

    sb = _make_storyboard()
    # Non-local sandbox (base class)
    sandbox = _FakeSandbox(output_exists=True)
    progress = _FakeProgress()

    with patch("app.gateway.video_gen.pipelines.remotion_run.deploy_template", return_value=("DataDrivenVideo", "/mnt/user-data/workspace", "")), patch("app.gateway.video_gen.pipelines.remotion_run.upload_audio_files"):
        await run_remotion_storyboard(
            sb,
            task_dir=tmp_path,
            sandbox=sandbox,
            progress=progress,
        )

    output = tmp_path / "output.mp4"
    assert output.exists()
    assert output.stat().st_size > 0


def test_run_remotion_storyboard_runs_template_in_thread():
    """deploy_template should run via asyncio.to_thread, not blocking the event loop."""
    import inspect

    from app.gateway.video_gen.pipelines import remotion_run

    source = inspect.getsource(remotion_run.run_remotion_storyboard)
    assert "asyncio.to_thread" in source, "run_remotion_storyboard should wrap sync calls in asyncio.to_thread"
