"""决策 2 / G4：ViMax 拼接在参数一致时走 stream-copy 快路径。

现状是「两个缺点同时存在」的最差组合：每次拼接都用 moviepy 全量**重编码**
（慢 + 掉一档画质），却**没有**换到转场——镜头之间仍是硬切。等于买了票没进场。

产品决策（2026-07-31）：**保持硬切，优先免重编码**。内部培训视频硬切完全可接受
（真人讲课录屏本就是硬切），而渲染速度与画质是每次都能感知的。

实测量化（3 段 640x480@30fps 各 2s）：
    moviepy 重编码 : 1.206s
    stream-copy    : 0.027s   ← 44× 快，且时长精确 6.000s、参数原样保留

安全前提：**只有全部输入的编码参数完全一致**（codec/分辨率/帧率/像素格式/
时基）才能 -c copy，否则产物会花屏或时间戳错乱。参数不一致时必须回落
moviepy——那正是 Task 1 处理过的混合尺寸场景（占位帧 1920x1080 混竖屏真镜）。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from vimax.utils.video import _streams_are_uniform, concatenate_video_files


def _has_ffmpeg() -> bool:
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
        return True
    except (OSError, subprocess.CalledProcessError):
        return False


pytestmark = pytest.mark.skipif(not _has_ffmpeg(), reason="需要 ffmpeg")


def _make(path: Path, *, size="640x480", fps=30, dur=2.0) -> str:
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"testsrc2=s={size}:r={fps}", "-t", str(dur), "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-an", str(path)],
        capture_output=True,
        check=True,
    )
    return str(path)


def _probe(path: str) -> tuple[int, int, str]:
    out = (
        subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height,r_frame_rate", "-of", "csv=p=0", path],
            capture_output=True,
            text=True,
            check=True,
        )
        .stdout.strip()
        .split(",")
    )
    return int(out[0]), int(out[1]), out[2]


def _duration(path: str) -> float:
    return float(
        subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    )


# ── 判据本身 ────────────────────────────────────────────────────────────────


def test_uniform_streams_detected(tmp_path):
    paths = [_make(tmp_path / f"u{i}.mp4") for i in range(3)]
    assert _streams_are_uniform(paths) is True


def test_mixed_resolution_not_uniform(tmp_path):
    """Task 1 的场景：占位帧 1920x1080 混竖屏真镜——绝不能 stream-copy。"""
    paths = [
        _make(tmp_path / "portrait.mp4", size="1152x2048"),
        _make(tmp_path / "landscape.mp4", size="1920x1080"),
    ]
    assert _streams_are_uniform(paths) is False


def test_mixed_fps_not_uniform(tmp_path):
    paths = [
        _make(tmp_path / "f30.mp4", fps=30),
        _make(tmp_path / "f24.mp4", fps=24),
    ]
    assert _streams_are_uniform(paths) is False


def test_single_file_is_uniform(tmp_path):
    assert _streams_are_uniform([_make(tmp_path / "one.mp4")]) is True


def test_unprobeable_input_is_not_uniform(tmp_path):
    """探不到参数时保守返回 False（宁可慢，不可产出坏片）。"""
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"not a video")
    assert _streams_are_uniform([_make(tmp_path / "ok.mp4"), str(bad)]) is False


# ── 端到端 ──────────────────────────────────────────────────────────────────


def test_uniform_concat_preserves_params_and_duration(tmp_path):
    """参数一致 → 走快路径，时长精确、参数原样保留。"""
    paths = [_make(tmp_path / f"s{i}.mp4") for i in range(3)]
    out = tmp_path / "out.mp4"

    concatenate_video_files(paths, str(out))

    assert _duration(str(out)) == pytest.approx(6.0, abs=0.1)
    assert _probe(str(out)) == _probe(paths[0]), "快路径不得改变编码参数"


def test_mixed_size_still_correct(tmp_path):
    """参数不一致 → 回落 moviepy compose，Task 1 的宽高比不变量仍成立。"""
    import numpy as np
    from moviepy import VideoFileClip

    # 等比缩小的尺寸错配（16:9 vs 9:16）：回落与拉伸机制与绝对分辨率无关。
    placeholder = _make(tmp_path / "ph.mp4", size="568x320", dur=1.0)
    real = _make(tmp_path / "real.mp4", size="320x568", dur=1.0)
    out = tmp_path / "mixed.mp4"

    concatenate_video_files([placeholder, real], str(out))

    with VideoFileClip(str(out)) as clip:
        frame = clip.get_frame(clip.duration - 0.5)
    # 真镜（后半段）内容不得被拉伸：非黑区域宽高比应仍接近 320/568
    mask = np.asarray(frame, dtype=float).max(axis=2) > 30
    ys, xs = np.where(mask)
    aspect = (xs.max() - xs.min() + 1) / (ys.max() - ys.min() + 1)
    assert aspect == pytest.approx(320 / 568, rel=0.08), f"回落路径的宽高比不变量被破坏：{aspect:.3f}"
