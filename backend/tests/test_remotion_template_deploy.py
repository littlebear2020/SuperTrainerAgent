"""Tests for Remotion template deployment and audio upload."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.gateway.video_gen.pipelines.remotion_template import (
    _generate_root_tsx,
    deploy_template,
    get_template_path,
    upload_audio_files,
)

# CI 守卫（2026-09-23 收尾批）：标注用例经 AppConfig 读真实 config.yaml
# （gitignored，CI fresh checkout 不存在）——缺失时跳过，本地在位照常执行。
_REAL_CONFIG_EXISTS = any((Path(__file__).resolve().parents[i] / "config.yaml").exists() for i in (1, 2))
requires_real_config = pytest.mark.skipif(not _REAL_CONFIG_EXISTS, reason="依赖本地 gitignored config.yaml，CI 跳过")

# ---------------------------------------------------------------------------
# _generate_root_tsx
# ---------------------------------------------------------------------------


def test_generate_root_tsx_contains_correct_values():
    result = _generate_root_tsx(fps=30, width=1080, height=1920, total_frames=450)
    assert "durationInFrames={450}" in result
    assert "fps={30}" in result
    assert "width={1080}" in result
    assert "height={1920}" in result


def test_generate_root_tsx_is_valid_tsx():
    result = _generate_root_tsx(fps=30, width=1080, height=1920, total_frames=450)
    assert 'import "./index.css"' in result
    assert "export const RemotionRoot" in result
    assert "<Composition" in result
    assert "DataDrivenVideo" in result


# ---------------------------------------------------------------------------
# get_template_path
# ---------------------------------------------------------------------------


@requires_real_config
def test_get_template_path_default():
    path = get_template_path("default")
    assert path.name == "remotion-base"
    assert path.exists()  # template dir should exist in skills


@requires_real_config
def test_get_template_path_unknown_falls_back():
    path = get_template_path("nonexistent_style")
    assert path.name == "remotion-base"


# ---------------------------------------------------------------------------
# deploy_template
# ---------------------------------------------------------------------------


class _FakeSandbox:
    def __init__(self):
        self.id = "fake"
        self._written = {}
        self._commands = []

    def write_file(self, path: str, content: str, **kw):
        self._written[path] = content

    def execute_command(self, cmd: str, **kw):
        self._commands.append(cmd)
        if "ln -sf" in cmd and "/opt/remotion-template/node_modules" in cmd:
            return ""  # success
        return ""

    def read_file(self, path: str):
        return self._written.get(path, "")


@requires_real_config
def test_deploy_template_writes_files():
    sandbox = _FakeSandbox()
    comp_id, render_dir, env_prefix = deploy_template(sandbox, "default", fps=30, width=1080, height=1920, total_frames=450)
    assert comp_id == "DataDrivenVideo"
    assert render_dir is not None
    assert env_prefix == ""  # local mode, no rspack fix needed
    # Should have written at least Root.tsx, index.ts, package.json
    paths = list(sandbox._written.keys())
    assert any("Root.tsx" in p for p in paths)
    # Composition.tsx may or may not exist depending on whether DataDrivenComposition.tsx
    # is present in the template (Task 4 creates it). Just verify Root.tsx is there.
    assert any("index.ts" in p for p in paths)
    assert any("package.json" in p for p in paths)


@requires_real_config
def test_deploy_template_root_tsx_has_dynamic_values():
    sandbox = _FakeSandbox()
    deploy_template(sandbox, "default", fps=24, width=1280, height=720, total_frames=600)
    root_content = None
    for path, content in sandbox._written.items():
        if "Root.tsx" in path:
            root_content = content
            break
    assert root_content is not None
    assert "durationInFrames={600}" in root_content
    assert "fps={24}" in root_content
    assert "width={1280}" in root_content
    assert "height={720}" in root_content


@requires_real_config
def test_deploy_template_symlinks_node_modules():
    sandbox = _FakeSandbox()
    deploy_template(sandbox, "default", fps=30, width=1080, height=1920, total_frames=300)
    assert any("ln -sf" in cmd and "node_modules" in cmd for cmd in sandbox._commands)


@requires_real_config
def test_deploy_template_creates_public_audio():
    sandbox = _FakeSandbox()
    deploy_template(sandbox, "default", fps=30, width=1080, height=1920, total_frames=300)
    assert any("mkdir" in cmd and "public/audio" in cmd for cmd in sandbox._commands)


@requires_real_config
def test_deploy_template_npm_install_when_node_modules_missing():
    """When node_modules doesn't exist in template source, npm install should run.
    Sandbox.execute_command must not receive timeout kwarg.
    """

    class _KwargTrackingSandbox(_FakeSandbox):
        def execute_command(self, cmd: str, **kw):
            self._commands.append(cmd)
            self._kwargs = getattr(self, "_kwargs", [])
            self._kwargs.append(kw)
            return ""

    sandbox = _KwargTrackingSandbox()
    with patch("app.gateway.video_gen.pipelines.remotion_template._get_base_template_path") as mock_path:
        fake_template = Path("/tmp/nonexistent_template_dir_for_test")
        mock_path.return_value = fake_template
        # npm install will be called because node_modules doesn't exist
        with patch("subprocess.run") as mock_npm:
            mock_npm.return_value = None
            deploy_template(sandbox, "default", fps=30, width=1080, height=1920, total_frames=300)
            # npm install is the first subprocess call (a follow-up local-dev
            # `npx remotion browser ensure` may also run — see deploy_template).
            mock_npm.assert_called()
            assert mock_npm.call_args_list[0].args[0][:2] == ["npm", "install"]
            # Verify no timeout kwarg leaked into execute_command calls
            for kw in sandbox._kwargs:
                assert "timeout" not in kw


# ---------------------------------------------------------------------------
# upload_audio_files
# ---------------------------------------------------------------------------


def test_upload_audio_files_uploads_each_frame(tmp_path):
    # Create fake audio files
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    for i in range(3):
        (audio_dir / f"frame_{i:03d}.mp3").write_bytes(b"\x00" * 100)

    sandbox = _FakeSandbox()
    sandbox.upload_file_stream = MagicMock()

    upload_audio_files(sandbox, audio_dir)
    assert sandbox.upload_file_stream.call_count == 3
    # Verify paths are correct
    calls = sandbox.upload_file_stream.call_args_list
    assert all("public/audio/frame_" in str(c) for c in calls)


def test_upload_audio_files_local_sandbox_uses_copy(tmp_path):
    """LocalSandbox path: resolve_virtual_path available, use shutil.copy."""
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    for i in range(2):
        (audio_dir / f"frame_{i:03d}.mp3").write_bytes(b"\x00" * 50)

    class _FakeLocalSandbox(_FakeSandbox):
        def resolve_virtual_path(self, path: str) -> str:
            # Map sandbox public/audio/ to a local temp dir
            local_dir = tmp_path / "sandbox_public" / "audio"
            local_dir.mkdir(parents=True, exist_ok=True)
            return str(local_dir / Path(path).name)

    sandbox = _FakeLocalSandbox()
    sandbox.upload_file_stream = MagicMock()

    with patch("app.gateway.video_gen.pipelines.remotion_template._is_local_sandbox", return_value=True):
        upload_audio_files(sandbox, audio_dir)

    # Should NOT use upload_file_stream for LocalSandbox
    assert sandbox.upload_file_stream.call_count == 0
    # Files should be copied via resolve_virtual_path
    local_audio = tmp_path / "sandbox_public" / "audio"
    assert (local_audio / "frame_000.mp3").exists()
    assert (local_audio / "frame_001.mp3").exists()
