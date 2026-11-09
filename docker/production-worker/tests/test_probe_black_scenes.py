"""Phase G2: _probe_black_scenes 解析 ffmpeg blackdetect 输出。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from pipelines import _ffmpeg_helpers as h  # noqa: E402


def test_probe_parses_blackdetect_lines(monkeypatch):
    """G2: 解析 blackdetect 行为 [{start, duration}]。"""

    class _Proc:
        stderr = (
            "frame=100 fps=30 q=-1.0\n"
            "[blackdetect @ 0x1] black_start:5.200 black_end:6.000 black_duration:0.800\n"
            "[blackdetect @ 0x2] black_start:12.000 black_end:12.500 black_duration:0.500\n"
        )

    monkeypatch.setattr(h.subprocess, "run", lambda *a, **k: _Proc())
    scenes = h._probe_black_scenes(Path("v.mp4"))
    assert len(scenes) == 2
    assert scenes[0] == {"start": 5.2, "duration": 0.8}
    assert scenes[1] == {"start": 12.0, "duration": 0.5}


def test_probe_returns_empty_when_no_black(monkeypatch):
    """G2: 无黑场 → 空列表。"""

    class _Proc:
        stderr = "frame=100 fps=30 q=-1.0\n"

    monkeypatch.setattr(h.subprocess, "run", lambda *a, **k: _Proc())
    assert h._probe_black_scenes(Path("v.mp4")) == []


def test_probe_returns_empty_on_ffmpeg_failure(monkeypatch):
    """G2: ffmpeg 异常 → 空列表（不抛，诊断工具不阻断）。"""

    def _raise(*a, **k):
        raise RuntimeError("ffmpeg gone")

    monkeypatch.setattr(h.subprocess, "run", _raise)
    assert h._probe_black_scenes(Path("v.mp4")) == []
