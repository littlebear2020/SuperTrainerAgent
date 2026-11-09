"""Task 3b: Ken Burns 运镜按帧时长归一化——长帧不再撞 1.1 上限静止。

方案甲（2026-07-20）：保留 zoompan，过采样 2x→4x 把整数取整抖动压到 ≤0.25 输出像素；
fps 25→30 对齐 CONCAT_TARGET_FPS 消除 25→30 重采样跳帧。
（crop 替换路线经 ffmpeg 8.1.2 实测证伪：on 非 crop 变量、crop w/h 仅初始化求值一次，
详见 docs/plan-video-gen-subtitle-and-kenburns-fix.md §2.4.0。）
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from pipelines.video_gen_ai_visual import _ZOOM_OVERSAMPLE, _build_zoompan_vf  # noqa: E402


def test_zoom_rate_normalized_by_duration():
    """运镜速率随 duration 归一：3s 帧和 12s 帧都走完 1.0→1.1 完整行程。"""
    vf_short = _build_zoompan_vf(width=720, height=1280, duration=3.0)
    vf_long = _build_zoompan_vf(width=720, height=1280, duration=12.0)
    rate_short = float(re.search(r"zoom\+([\d.]+)", vf_short).group(1))
    rate_long = float(re.search(r"zoom\+([\d.]+)", vf_long).group(1))
    # 短帧速率大（快推完）、长帧速率小（慢推完）
    assert rate_short > rate_long, f"短帧速率应更大，{rate_short} vs {rate_long}"
    # 两者总行程都≈0.1（rate × total_frames ≈ 0.1，30fps）
    assert abs(rate_short * 3.0 * 30 - 0.1) < 0.02
    assert abs(rate_long * 12.0 * 30 - 0.1) < 0.02


def test_long_frame_does_not_hit_cap_early():
    """12s 帧 rate 远小于旧 0.0008，不会提前撞上限静止。"""
    vf_long = _build_zoompan_vf(width=720, height=1280, duration=12.0)
    rate_long = float(re.search(r"zoom\+([\d.]+)", vf_long).group(1))
    # 12s = 360 帧，归一 rate = 0.1/360 ≈ 0.00028 < 旧 0.0008 → 不会撞上限静止
    assert rate_long < 0.0008, f"12s 帧 rate 应 <0.0008，实际 {rate_long}"


def test_zoom_direction_alternates_by_frame_index():
    """Task 3i: 偶数帧 zoom in，奇数帧 zoom out（去单调）。"""
    vf_in = _build_zoompan_vf(width=720, height=1280, duration=5.0, frame_index=0)
    vf_out = _build_zoompan_vf(width=720, height=1280, duration=5.0, frame_index=1)
    assert "zoom+0." in vf_in, "偶数帧应 zoom in"
    assert "max(zoom-" in vf_out, "奇数帧应 zoom out"


def test_segment_fps_is_30_and_frame_count_matches():
    """段输出原生 30fps == CONCAT_TARGET_FPS（消除 25→30 重采样跳帧），d 按 30fps 计。"""
    vf = _build_zoompan_vf(width=720, height=1280, duration=3.0)
    assert "fps=30" in vf, "zoompan 输出须为 30fps"
    assert ":d=90:" in vf, "3s × 30fps = 90 帧"
    vf_long = _build_zoompan_vf(width=720, height=1280, duration=12.0)
    assert ":d=360:" in vf_long, "12s × 30fps = 360 帧"


def test_oversample_is_4x():
    """4x 过采样：zoompan x/y 整数取整 ±1px ÷ 4 → ≤0.25 输出像素（修前 2x → 0.5px 仍可见）。"""
    vf = _build_zoompan_vf(width=720, height=1280, duration=3.0)
    assert _ZOOM_OVERSAMPLE == 4
    assert f"scale={720 * 4}:{1280 * 4}" in vf
    assert f"crop={720 * 4}:{1280 * 4}" in vf
