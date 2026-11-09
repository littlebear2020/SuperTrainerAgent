"""Tests for ffmpeg quality presets and format conversion helpers."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Load _ffmpeg_helpers with mocked dependencies
# ---------------------------------------------------------------------------

helpers_path = Path(__file__).resolve().parents[2] / "docker/production-worker/pipelines/_ffmpeg_helpers.py"
spec = importlib.util.spec_from_file_location("pipelines._ffmpeg_helpers", helpers_path)
helpers_mod = importlib.util.module_from_spec(spec)
sys.modules.setdefault("pipelines", types.ModuleType("pipelines"))
sys.modules["pipelines._ffmpeg_helpers"] = helpers_mod
spec.loader.exec_module(helpers_mod)

QUALITY_PRESETS = helpers_mod.QUALITY_PRESETS
_get_quality_args = helpers_mod._get_quality_args
_ffmpeg_convert_format = helpers_mod._ffmpeg_convert_format


# ---------------------------------------------------------------------------
# Quality presets
# ---------------------------------------------------------------------------


def test_quality_presets_structure():
    assert "draft" in QUALITY_PRESETS
    assert "standard" in QUALITY_PRESETS
    assert "high" in QUALITY_PRESETS


def test_quality_presets_crf_ordering():
    assert QUALITY_PRESETS["draft"]["crf"] > QUALITY_PRESETS["standard"]["crf"]
    assert QUALITY_PRESETS["standard"]["crf"] > QUALITY_PRESETS["high"]["crf"]


def test_get_quality_args_draft():
    args = _get_quality_args("draft")
    assert "-c:v" in args
    assert "libx264" in args
    idx = args.index("-crf")
    assert args[idx + 1] == "28"


def test_get_quality_args_standard():
    args = _get_quality_args("standard")
    idx = args.index("-crf")
    assert args[idx + 1] == "20"


def test_get_quality_args_high():
    args = _get_quality_args("high")
    idx = args.index("-crf")
    assert args[idx + 1] == "16"


def test_get_quality_args_unknown_defaults_standard():
    args = _get_quality_args("unknown")
    idx = args.index("-crf")
    assert args[idx + 1] == "20"


def test_get_quality_args_includes_pix_fmt():
    args = _get_quality_args("standard")
    assert "yuv420p" in args


# ---------------------------------------------------------------------------
# Format conversion (mocked subprocess)
# ---------------------------------------------------------------------------


@patch.object(helpers_mod, "subprocess")
def test_convert_mp4_to_mp4_relocates_moov(mock_subprocess, tmp_path):
    """MP4 → MP4 做 moov 重定位（stream copy + faststart），非 no-op。

    Bug 1: 旧短路返回 input，但 concat 之后的 burn字幕/colorgrade/mix 等重编码步
    未带 faststart → moov 在尾部 → Web 无法边下边播。mp4 出口须 -c copy -movflags
    +faststart 重定位 moov（不重编码、不损画质）。
    """
    input_file = tmp_path / "output.mp4"
    input_file.write_text("fake video")
    output_file = tmp_path / "output2.mp4"

    mock_subprocess.run.return_value = MagicMock(returncode=0)

    result = _ffmpeg_convert_format(input_file, output_file, output_format="mp4")
    assert result == output_file, "mp4 出口应写出 output（moov 重定位），非短路返回 input"
    cmd = mock_subprocess.run.call_args[0][0]
    assert "-movflags" in cmd and "+faststart" in cmd, f"mp4 出口须 faststart: {cmd}"
    assert "copy" in cmd, "moov 重定位用 stream copy 不重编码"


@patch.object(helpers_mod, "subprocess")
def test_convert_to_webm(mock_subprocess, tmp_path):
    """WebM conversion invokes ffmpeg with libvpx-vp9."""
    input_file = tmp_path / "output.mp4"
    input_file.write_text("fake")
    output_file = tmp_path / "output.webm"

    mock_subprocess.run.return_value = MagicMock(returncode=0)

    result = _ffmpeg_convert_format(input_file, output_file, output_format="webm")
    assert result == output_file
    cmd = mock_subprocess.run.call_args[0][0]
    assert "libvpx-vp9" in cmd


@patch.object(helpers_mod, "_ffprobe_duration", return_value=10.0)
@patch.object(helpers_mod, "subprocess")
def test_convert_to_gif(mock_subprocess, _mock_dur, tmp_path):
    """GIF conversion invokes ffmpeg twice (palette + convert)."""
    input_file = tmp_path / "output.mp4"
    input_file.write_text("fake")
    output_file = tmp_path / "output.gif"

    mock_subprocess.run.return_value = MagicMock(returncode=0)
    result = _ffmpeg_convert_format(input_file, output_file, output_format="gif")
    assert result == output_file
    assert mock_subprocess.run.call_count == 2


@patch.object(helpers_mod, "subprocess")
def test_convert_webm_failure_raises(mock_subprocess, tmp_path):
    """Failed webm conversion raises RuntimeError."""
    input_file = tmp_path / "output.mp4"
    input_file.write_text("fake")
    output_file = tmp_path / "output.webm"

    mock_subprocess.run.return_value = MagicMock(returncode=1, stderr=b"error something failed")
    with pytest.raises(RuntimeError, match="webm conversion failed"):
        _ffmpeg_convert_format(input_file, output_file, output_format="webm")
