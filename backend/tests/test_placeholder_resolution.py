"""B2: 占位纯色视频分辨率须匹配目标比例（9:16 任务不能出 1920x1080 黑帧边界跳）。

根因：_make_solid_color_video 硬编码 s=1920x1080（16:9）；9:16 任务占位帧被 worker
缩放时边界跳变 → 抖动。修法：接受 width/height，由 _make_placeholder_clip 贯穿目标尺寸。
真实 ffmpeg（无则 skip）。
"""

import shutil
import subprocess

import pytest

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
skip = pytest.mark.skipif(not (FFMPEG and FFPROBE), reason="ffmpeg/ffprobe not installed")


def _probe_resolution(path):
    out = subprocess.run(
        [FFPROBE, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", str(path)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    w, h = out.stdout.strip().split("x")
    return int(w), int(h)


@skip
def test_make_solid_color_video_uses_target_dimensions(tmp_path):
    """9:16 目标（1080x1920）→ 占位输出须是 1080x1920，不是硬编码 1920x1080。"""
    from app.gateway.video_gen.engines.vimax._static_frame import _make_solid_color_video

    out = tmp_path / "p.mp4"
    _make_solid_color_video(str(out), 1.0, "black", width=1080, height=1920)
    assert _probe_resolution(out) == (1080, 1920), "占位分辨率须匹配目标（修前硬编码 1920x1080）"


@skip
def test_make_solid_color_video_defaults_back_compatible(tmp_path):
    """不传尺寸时保持向后兼容（vimax 内部调用不破）。"""
    from app.gateway.video_gen.engines.vimax._static_frame import _make_solid_color_video

    out = tmp_path / "p.mp4"
    _make_solid_color_video(str(out), 1.0, "black")  # 不传 width/height
    w, h = _probe_resolution(out)
    assert (w, h) == (1920, 1080), "默认仍 1920x1080（vimax 内部调用兼容）"
