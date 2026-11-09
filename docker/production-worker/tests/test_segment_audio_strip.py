"""H5: video segment 剥音轨（-an）+ max_duration 截断。

修复前：normalize_clip_resolution 保留 -c:a aac——上传 mp4 原声随 concat 进 mix，
_ffmpeg_mix_audio 探到 0:a 后 amix 等增益混入（旁白响度减半）；且长 clip 不截断
（_prepare_local_segment/_download_and_prepare 只有 extend 分支无 -t）→ 段时长>帧时长，
后续帧画面相对旁白整体错位。
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import pipelines.video_gen_ai_visual as mod  # noqa: E402
from pipelines import _ffmpeg_helpers as h  # noqa: E402


def _capture_run(monkeypatch, out_path):
    """桩 subprocess.run，记录命令并写出占位输出文件（与 test_normalize_concat_framerate 同款）。"""
    captured = {"cmd": None}

    def _fake(cmd, **k):
        captured["cmd"] = cmd
        out_path.write_bytes(b"y")
        return MagicMock(returncode=0)

    monkeypatch.setattr(h.subprocess, "run", _fake)
    return captured


def test_normalize_strips_audio(tmp_path, monkeypatch):
    clip = tmp_path / "in.mp4"
    clip.write_bytes(b"x")
    out = tmp_path / "out.mp4"
    monkeypatch.setattr(h, "_probe_dimensions", lambda c: (1080, 1920))
    captured = _capture_run(monkeypatch, out)

    h.normalize_clip_resolution(clip, out, 1080, 1920)

    cmd = captured["cmd"]
    assert "-an" in cmd          # 修复前：无 -an，有 -c:a aac
    assert "-c:a" not in cmd


def test_normalize_trims_to_max_duration(tmp_path, monkeypatch):
    clip = tmp_path / "in.mp4"
    clip.write_bytes(b"x")
    out = tmp_path / "out.mp4"
    monkeypatch.setattr(h, "_probe_dimensions", lambda c: (1080, 1920))
    captured = _capture_run(monkeypatch, out)

    h.normalize_clip_resolution(clip, out, 1080, 1920, max_duration=6.0)

    cmd = captured["cmd"]
    assert "-t" in cmd           # 修复前：无 max_duration 参数
    assert cmd[cmd.index("-t") + 1] == "6.000"


def test_normalize_no_trim_without_max_duration(tmp_path, monkeypatch):
    clip = tmp_path / "in.mp4"
    clip.write_bytes(b"x")
    out = tmp_path / "out.mp4"
    monkeypatch.setattr(h, "_probe_dimensions", lambda c: (1080, 1920))
    captured = _capture_run(monkeypatch, out)

    h.normalize_clip_resolution(clip, out, 1080, 1920)

    assert "-t" not in captured["cmd"]


def test_prepare_local_segment_passes_frame_duration(tmp_path, monkeypatch):
    """上传视频（frame_ 前缀）：normalize 收到 max_duration=帧时长——长 clip 被截。"""
    (tmp_path / "visuals").mkdir()
    (tmp_path / "visuals" / "frame_001.mp4").write_bytes(b"v")
    (tmp_path / "tmp").mkdir()
    seen = {}

    def fake_norm(clip, output, w, h, *, max_duration=None):
        seen["max_duration"] = max_duration
        output.write_bytes(b"n")
        return output

    monkeypatch.setattr(mod, "normalize_clip_resolution", fake_norm)
    monkeypatch.setattr(mod, "_probe_duration", lambda p: 10.0)  # 10s 上传 > 6s 帧时长

    asset = {
        "frame_index": 1, "url": None, "local_path": "visuals/frame_001.mp4",
        "type": "video", "duration": 6.0, "placeholder": False, "narration": "",
    }
    res = mod._prepare_local_segment(asset, tmp_path, tmp_path / "tmp", 1080, 1920, "standard")

    assert res is not None
    assert seen["max_duration"] == 6.0  # 修复前：不传 → 10s 段进 concat，后续帧错位
