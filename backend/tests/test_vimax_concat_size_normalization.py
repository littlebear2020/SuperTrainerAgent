"""Task 1 (NEW-1): ViMax 占位帧不得毁掉整片画面。

`concatenate_videoclips` 在 moviepy 2.1.2 下默认 `method="chain"`，**不做尺寸
归一**：它把画布定为**首个** clip 的尺寸，并把后续尺寸不同的 clip **拉伸**填满
该画布——不保持宽高比。

当某镜头失败走占位（`_make_solid_color_video` 恒 1920x1080）而真镜是竖屏
（1152x2048）时，若占位排在前面，整片画布被定为 16:9，其后每个真镜都被横向
拉伸 3.16 倍。

实测量化（本机 moviepy 2.1.2 + ffmpeg）：
    chain  : canvas=1920x1080  真镜内容 1920x1080  aspect 1.778  ← 源 0.562，严重拉伸
    compose: canvas=1920x2048  真镜内容 1152x2048  aspect 0.562  ← 与源一致 ✅

注：主审计描述的「彩色扫描线花屏」在 moviepy 2.1.2 上**未能复现**（chain 走的是
resize 而非错位 stride）。真实缺陷是**宽高比失真**——人物被拉扁，同样是「画面
毁掉」，但机制不同。本测试锁定可复现的真实不变量：拼接不得改变各镜宽高比。

另注：compose 产出 1920x2048 **并集**画布，仍不是用户要的 9:16——必须与 Task 2
（消除 vendored 1600x900 硬编码 + 占位按目标宽高生成）同窗口交付。
"""

import subprocess

import numpy as np
import pytest
from vimax.utils.video import concatenate_video_files


def _has_ffmpeg() -> bool:
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
        return True
    except (OSError, subprocess.CalledProcessError):
        return False


pytestmark = pytest.mark.skipif(not _has_ffmpeg(), reason="需要 ffmpeg")


def _make_solid(path, width, height, color, duration=1.0):
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c={color}:s={width}x{height}:r=24",
            "-t",
            str(duration),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        capture_output=True,
        check=True,
    )
    return str(path)


def _content_aspect(frame, channel=2, threshold=100):
    """量出画面中非黑内容的宽高比（letterbox 的黑边不计入）。"""
    mask = np.asarray(frame, dtype=float)[:, :, channel] > threshold
    ys, xs = np.where(mask)
    assert ys.size, "帧中找不到内容像素"
    width = xs.max() - xs.min() + 1
    height = ys.max() - ys.min() + 1
    return width / height


def test_placeholder_first_does_not_stretch_real_shots(tmp_path):
    """占位帧（16:9）排在竖屏真镜之前时，真镜不得被拉伸变形。

    这是 NEW-1 的核心用例：chain 模式下此断言必红（真镜 aspect 被拉成 1.778）。
    夹具用 568x320/320x568 等比缩小（宽高比不变、尺寸错配保留）；chain 的拉伸
    机制是「画布 = 首个 clip」，与绝对分辨率无关，缩小后判别力不变。
    """
    placeholder = _make_solid(tmp_path / "placeholder.mp4", 568, 320, "black")
    real_shot = _make_solid(tmp_path / "real.mp4", 320, 568, "blue")
    out = tmp_path / "out.mp4"

    concatenate_video_files([placeholder, real_shot], str(out))
    assert out.exists() and out.stat().st_size > 0

    from moviepy import VideoFileClip

    with VideoFileClip(str(out)) as clip:
        real_frame = clip.get_frame(clip.duration - 0.5)

    source_aspect = 320 / 568
    actual_aspect = _content_aspect(real_frame)
    assert actual_aspect == pytest.approx(source_aspect, rel=0.05), f"真镜宽高比被改变：{actual_aspect:.3f} != 源 {source_aspect:.3f}（chain 模式把竖屏拉伸填满 16:9 画布）"


def test_mixed_size_concat_preserves_first_shot_aspect(tmp_path):
    """真镜在前、占位在后：占位不得被拉伸成竖屏。"""
    real_shot = _make_solid(tmp_path / "real.mp4", 320, 568, "blue")
    placeholder = _make_solid(tmp_path / "placeholder.mp4", 568, 320, "red")
    out = tmp_path / "out.mp4"

    concatenate_video_files([real_shot, placeholder], str(out))

    from moviepy import VideoFileClip

    with VideoFileClip(str(out)) as clip:
        placeholder_frame = clip.get_frame(clip.duration - 0.5)

    actual_aspect = _content_aspect(placeholder_frame, channel=0)
    assert actual_aspect == pytest.approx(568 / 320, rel=0.05), f"占位宽高比被改变：{actual_aspect:.3f} != 源 {568 / 320:.3f}"


# ── 审查发现 6：make_static_video_from_frame 的 solid-color 兜底仍硬编码 1080p ──
#
# Task 1 修了直接调用 _make_solid_color_video 的那条路（关键字传参），但漏了
# make_static_video_from_frame 内部的兜底：PNG 损坏 → ffmpeg 失败 → 调
# _make_solid_color_video(output_path, duration) **不传宽高** → 默认 1920x1080。
# 9:16 任务里因此混进 16:9 占位，正是 Task 1 要消灭的画布污染。


def test_static_frame_fallback_honors_target_size(tmp_path):
    """PNG 损坏走 solid-color 兜底时，产物必须是目标宽高而非默认 1080p。"""
    from app.gateway.video_gen.engines.vimax._static_frame import (
        make_static_video_from_frame,
    )

    # 空文件 = 截断/写失败的 PNG：ffmpeg 立即 rc!=0 走兜底。勿换成垃圾字节——
    # .png 扩展名让 image2 选中 png 解码器，-loop 1 下解码错误无限循环、永不
    # 产帧（-t 不生效），只能靠生产代码的 timeout=60 强杀，本测试因此耗 60s。
    broken_png = tmp_path / "first_frame.png"
    broken_png.write_bytes(b"")  # 强制 ffmpeg 失败 → 走兜底
    out = tmp_path / "shot.mp4"

    make_static_video_from_frame(str(broken_png), str(out), duration=1.0, width=320, height=568)

    from moviepy import VideoFileClip

    with VideoFileClip(str(out)) as clip:
        assert clip.size == [320, 568], f"兜底占位尺寸为 {clip.size}，未跟随目标宽高 → 9:16 任务会混进 16:9 占位"


def test_static_frame_defaults_stay_backward_compatible(tmp_path):
    """不传宽高时保持既有默认（1920x1080），不破坏其他调用方。"""
    import inspect

    from app.gateway.video_gen.engines.vimax._static_frame import (
        make_static_video_from_frame,
    )

    sig = inspect.signature(make_static_video_from_frame)
    assert sig.parameters["width"].default == 1920
    assert sig.parameters["height"].default == 1080


def test_vimax_caller_passes_target_size_to_static_frame():
    """结构断言：vendored 调用点必须把目标宽高传进去（否则兜底又变 1080p）。"""
    from pathlib import Path as _P

    src = (_P(__file__).resolve().parents[1] / "packages" / "vimax" / "vimax" / "pipelines" / "script2video_pipeline.py").read_text(encoding="utf-8")
    assert "make_static_video_from_frame(ff_path, video_path, duration=5.2)" not in src, "调用点未传 width/height —— PNG 损坏时兜底仍出 1920x1080"
