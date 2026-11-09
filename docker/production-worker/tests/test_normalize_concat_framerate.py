"""Phase G1: normalize_clip_resolution + _ffmpeg_concat 统一帧率到 30fps。"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from pipelines import _ffmpeg_helpers as h  # noqa: E402


def _capture_run(monkeypatch, out_path):
    """桩 subprocess.run，记录命令并写出占位输出文件。"""
    captured = {"cmd": None}

    def _fake(cmd, **k):
        captured["cmd"] = cmd
        out_path.write_bytes(b"y")
        return MagicMock(returncode=0)

    monkeypatch.setattr(h.subprocess, "run", _fake)
    return captured


def test_normalize_reencodes_with_target_fps_when_size_matches(tmp_path, monkeypatch):
    """G1: 尺寸匹配时也 re-encode 到 30fps（不再 copy 保留原帧率）。"""
    clip = tmp_path / "in.mp4"
    clip.write_bytes(b"x")
    out = tmp_path / "out.mp4"
    monkeypatch.setattr(h, "_probe_dimensions", lambda c: (1080, 1920))  # 尺寸匹配
    captured = _capture_run(monkeypatch, out)

    h.normalize_clip_resolution(clip, out, 1080, 1920)
    cmd = captured["cmd"]
    assert cmd is not None, "尺寸匹配也应 re-encode（不再 copy）"
    assert "-r" in cmd and "30" in cmd, "应统一帧率到 30"
    assert "libx264" in cmd
    assert "yuv420p" in cmd


def test_normalize_reencodes_with_scale_when_size_differs(tmp_path, monkeypatch):
    """G1: 尺寸不符时 scale + 统一 30fps + libx264 + yuv420p。"""
    clip = tmp_path / "in.mp4"
    clip.write_bytes(b"x")
    out = tmp_path / "out.mp4"
    monkeypatch.setattr(h, "_probe_dimensions", lambda c: (720, 1280))  # 不符
    captured = _capture_run(monkeypatch, out)

    h.normalize_clip_resolution(clip, out, 1080, 1920)
    cmd_str = " ".join(captured["cmd"])
    assert "scale=1080:1920" in cmd_str
    assert "-r" in captured["cmd"] and "libx264" in cmd_str and "yuv420p" in cmd_str


def test_concat_reencode_fallback_sets_target_fps(tmp_path, monkeypatch):
    """G1: _ffmpeg_concat 的**重编码回落**命令含 -r 30（双保险统一输出帧率）。

    2026-08-03 Task 18 刻意重写：原断言「concat 命令含 -r 30」检查的是首个命令，
    而 concat 现在**先试 stream copy**（段已由 normalize_clip_resolution 逐段归一到
    30fps，全片重编码是 54 镜 354s 超时的根因）。``-r`` 对 ``-c copy`` 本就无效——
    不重新采样，只改容器时基——所以 copy 命令不带它并非丢失双保险。
    G1 的真实意图（回落到重编码时必须统一帧率）由本测试继续锁住。
    """
    seg = tmp_path / "seg_000.mp4"
    seg.write_bytes(b"x")
    out = tmp_path / "concat.mp4"
    cmds: list[list[str]] = []

    def _fake(cmd, **k):
        cmds.append(cmd)
        # 首个命令（stream copy）返回失败，强制走重编码回落
        if "copy" in cmd:
            return MagicMock(returncode=1, stderr=b"forced failure")
        out.write_bytes(b"y")
        return MagicMock(returncode=0)

    monkeypatch.setattr(h.subprocess, "run", _fake)

    h._ffmpeg_concat([seg], out)
    assert len(cmds) == 2, f"copy 失败后应回落重编码，实际执行 {len(cmds)} 条命令"
    reencode = cmds[1]
    assert "-r" in reencode, "重编码回落应设 -r"
    assert str(h.CONCAT_TARGET_FPS) in " ".join(reencode)
    assert "libx264" in " ".join(reencode)


def test_concat_prefers_stream_copy(tmp_path, monkeypatch):
    """Task 18: 同规格段（G1 归一后的常态）→ 首个命令即 stream copy，不重编码。"""
    seg = tmp_path / "seg_000.mp4"
    seg.write_bytes(b"x")
    out = tmp_path / "concat.mp4"
    captured = _capture_run(monkeypatch, out)
    # 段时长探不到（桩文件）→ 时长校验跳过，copy 结果被接受
    monkeypatch.setattr(h, "_probe_duration", lambda p: 0.0)

    h._ffmpeg_concat([seg], out)
    cmd_str = " ".join(captured["cmd"])
    assert "-c copy" in cmd_str, f"应优先 stream copy，实际 {cmd_str}"
    assert "libx264" not in cmd_str, "同规格段不应重编码"
