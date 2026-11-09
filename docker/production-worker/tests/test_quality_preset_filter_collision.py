"""Task 3 (NEW-2): preview 画质不得与滤镜链冲突导致转场/淡入淡出被静默丢弃。

``_get_quality_args`` 此前对 preview 预设注入 ``-vf scale=640x360``，与两处调用
方冲突：

1. ``_ffmpeg_concat_with_transitions`` 用 ``-filter_complex`` 做 xfade。ffmpeg 报
   *"Simple and complex filtering cannot be used together for the same stream"* →
   渲染失败 → 但 :218-222 有 fallback 到简单 concat → **净效果是转场全部静默消失**，
   用户以为「我设的转场没生效」。

2. ``_apply_intro_outro_fade`` 先发 ``-vf <fade>`` 再追加 quality args 的
   ``-vf scale`` → ffmpeg 明确警告 *"only the last option -filter:v will be
   used"* → **淡入淡出被静默丢弃**。

实测量化（本机 ffmpeg）：
    末帧平均亮度  现状(-vf scale 覆盖 fade) = 127.42   ← 淡出没生效
                  -s 修法(fade 保留)        =   8.28   ← 淡出真生效

修法：quality args 不再自带 ``-vf``；preview 缩放改用输出尺寸参数 ``-s``，它与
``-vf`` / ``-filter_complex`` 都不冲突。
"""

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from pipelines._ffmpeg_helpers import (  # noqa: E402
    QUALITY_PRESETS,
    _apply_intro_outro_fade,
    _get_quality_args,
)


def _has_ffmpeg() -> bool:
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
        return True
    except (OSError, subprocess.CalledProcessError):
        return False


@pytest.mark.parametrize("quality", sorted(QUALITY_PRESETS))
def test_quality_args_never_contain_vf(quality):
    """任何画质预设都不得在编码参数里塞 -vf —— 那会覆盖调用方的滤镜链。"""
    args = _get_quality_args(quality)
    assert "-vf" not in args, (
        f"画质预设 {quality!r} 仍注入 -vf {args}；"
        "它会与 -filter_complex 冲突（转场静默丢弃）或覆盖 -vf fade（淡出静默丢弃）"
    )


def test_preview_still_downscales():
    """preview 仍须真的降分辨率（用 -s，不是 -vf scale）。"""
    args = _get_quality_args("preview")
    assert "-s" in args, f"preview 不再降分辨率：{args}"
    assert args[args.index("-s") + 1] == "640x360"


def test_non_preview_presets_do_not_force_size():
    """非 preview 预设不得强制输出尺寸。"""
    for quality in ("draft", "standard", "high"):
        assert "-s" not in _get_quality_args(quality)


@pytest.mark.skipif(not _has_ffmpeg(), reason="需要 ffmpeg")
def test_preview_quality_coexists_with_filter_complex(tmp_path):
    """preview 画质 + xfade filter_complex → ffmpeg 不得报 simple/complex 冲突。"""
    clips = []
    for name in ("a", "b"):
        path = tmp_path / f"{name}.mp4"
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc2=s=640x480:r=30",
             "-t", "2", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
            capture_output=True, check=True,
        )
        clips.append(path)

    out = tmp_path / "out.mp4"
    cmd = [
        "ffmpeg", "-y", "-i", str(clips[0]), "-i", str(clips[1]),
        "-filter_complex",
        "[0:v][1:v]xfade=transition=fade:duration=0.5:offset=1.5[v]",
        "-map", "[v]",
        *_get_quality_args("preview"),
        "-an", str(out),
    ]
    proc = subprocess.run(cmd, capture_output=True)
    stderr = proc.stderr.decode(errors="replace")

    assert "Simple and complex filtering cannot be used together" not in stderr, (
        "preview 画质仍与 filter_complex 冲突 → 转场会被 fallback 静默吃掉"
    )
    assert proc.returncode == 0, f"xfade + preview 渲染失败：{stderr[-400:]}"
    assert out.exists() and out.stat().st_size > 0


@pytest.mark.skipif(not _has_ffmpeg(), reason="需要 ffmpeg")
def test_intro_outro_fade_survives_preview_quality(tmp_path):
    """preview 画质下淡出必须真生效（末帧接近黑），不得被 -vf scale 覆盖。"""
    from PIL import Image
    import numpy as np

    src = tmp_path / "src.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=white:s=640x480:r=30",
         "-t", "3", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(src)],
        capture_output=True, check=True,
    )

    faded = _apply_intro_outro_fade(src, tmp_dir=tmp_path, fade_sec=0.5, quality="preview")
    assert faded != src, "fade 未产出新文件（可能整体失败被吞）"

    frame = tmp_path / "last.png"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-sseof", "-0.05", "-i", str(faded),
         "-frames:v", "1", "-update", "1", str(frame)],
        capture_output=True, check=True,
    )
    luma = float(np.asarray(Image.open(frame).convert("L"), dtype=float).mean())

    assert luma < 40, (
        f"末帧亮度 {luma:.1f} 偏高 —— 淡出被静默丢弃"
        "（现状 -vf scale 覆盖 -vf fade，实测 127.4）"
    )
