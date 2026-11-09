"""Task 3g: 片头淡入 + 片尾淡出 vf 构造。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from pipelines._ffmpeg_helpers import _build_fade_vf  # noqa: E402  # Task 3: 已迁共享 helpers


def test_fade_vf_has_in_and_out():
    vf = _build_fade_vf(total_duration=60.0)
    assert "fade=t=in" in vf, "缺片头淡入"
    assert "fade=t=out" in vf, "缺片尾淡出"


def test_fade_out_starts_near_end():
    """淡出起始时间 = 总时长 - fade_sec。"""
    vf = _build_fade_vf(total_duration=60.0, fade_sec=0.5)
    assert "st=59.50" in vf, f"60s 视频淡出应在 59.5s 开始: {vf}"


def test_short_video_clamps_fade_out():
    """短视频淡出起始不负数。"""
    vf = _build_fade_vf(total_duration=0.3, fade_sec=0.5)
    assert "st=0.00" in vf, "短视频淡出起始应 clamp 到 0"
