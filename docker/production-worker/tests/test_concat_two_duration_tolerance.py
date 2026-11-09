"""C2（2026-08-14）：_concat_two_with_duration_check 时长容差非对称 [−0.3, +0.5]。

只信不丢时长的 copy 产物：短 0.4s（超下限 0.3）必须回落重编码；
长 0.4s（在上限 0.5 内）接受 copy。与 backend ai_video._concat_videos_with_check
同一口径（三处对称 ±0.5 全改为非对称）。
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from pipelines import _ffmpeg_helpers as H  # noqa: E402


def _setup(tmp_path, monkeypatch, got: float):
    seg_a = tmp_path / "a.mp4"
    seg_b = tmp_path / "b.mp4"
    out = tmp_path / "out.mp4"
    for p in (seg_a, seg_b):
        p.write_bytes(b"x" * 64)
    cmds: list[list[str]] = []

    def _fake_run(cmd, **kw):  # noqa: ANN001, ANN003
        cmds.append(cmd)
        out.write_bytes(b"y")
        return MagicMock(returncode=0, stderr=b"")

    monkeypatch.setattr(H.subprocess, "run", _fake_run)
    monkeypatch.setattr(
        H,
        "_probe_duration",
        lambda p: {str(seg_a): 2.0, str(seg_b): 2.0, str(out): got}.get(str(p), 0.0),
    )
    monkeypatch.setattr(H, "_probe_dimensions", lambda p: (640, 360))
    return seg_a, seg_b, out, cmds


def test_copy_product_short_by_0_4s_reencodes(tmp_path, monkeypatch) -> None:
    """expected=4.0，copy 产物 3.6（−0.4 超下限 0.3）→ 拒绝 copy，走重编码。"""
    seg_a, seg_b, out, cmds = _setup(tmp_path, monkeypatch, got=3.6)

    result = H._concat_two_with_duration_check(seg_a, seg_b, out)

    assert result == out
    assert len(cmds) == 2, f"短 0.4s 超下限必须回落重编码，实际执行 {len(cmds)} 条命令"
    assert "copy" in " ".join(cmds[0]), "第一条应是 stream copy"
    assert "libx264" in " ".join(cmds[1]), "第二条应是重编码路径"


def test_copy_product_long_by_0_4s_accepted(tmp_path, monkeypatch) -> None:
    """expected=4.0，copy 产物 4.4（+0.4 在上限 0.5 内）→ 接受 copy，不重编码。"""
    seg_a, seg_b, out, cmds = _setup(tmp_path, monkeypatch, got=4.4)

    result = H._concat_two_with_duration_check(seg_a, seg_b, out)

    assert result == out
    assert len(cmds) == 1, "长 0.4s 在上限内应接受 copy，不得重编码"
    assert "copy" in " ".join(cmds[0])
