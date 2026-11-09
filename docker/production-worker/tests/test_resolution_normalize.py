"""Task 3h: 分辨率归一——_probe_dimensions 决策逻辑。"""
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent))

from pipelines._ffmpeg_helpers import _probe_dimensions  # noqa: E402


def test_probe_dimensions_parses_ffprobe():
    """ffprobe csv 输出 W,H → 解析为 (W, H)。"""
    with mock.patch("subprocess.run") as m:
        m.return_value = mock.Mock(stdout="1920,1080\n", returncode=0)
        w, h = _probe_dimensions(Path("/fake.mp4"))
    assert w == 1920 and h == 1080


def test_probe_dimensions_failure_returns_zero():
    """ffprobe 失败 → (0, 0) 不崩。"""
    with mock.patch("subprocess.run", side_effect=Exception("boom")):
        w, h = _probe_dimensions(Path("/fake.mp4"))
    assert w == 0 and h == 0
