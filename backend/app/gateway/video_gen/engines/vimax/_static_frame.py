"""Static-frame fallback for failed video shots (B1).

When a single shot's video generation fails (after per-shot retry), this
turns the shot's first_frame.png into a timed static mp4 so the pipeline
can finish the video (static frame + narration audio) instead of aborting
the whole task. User choice: "static frame placeholder" (2026-07-06).
"""

from __future__ import annotations

import logging
import subprocess

logger = logging.getLogger(__name__)


def resolve_target_size(image_generator, default: tuple[int, int] = (1152, 2048)) -> tuple[int, int]:
    """从 ViMax image generator 反查目标宽高（Task 2 / G15）。

    vendored 代码此前硬编码 1600x900 占位与显式 ``size="1600x900"`` kwarg，后者
    会短路桥接层正确的 ``_aspect_to_size``。占位帧尺寸必须跟随用户选的画面比例，
    否则 9:16 任务里混进 16:9 占位 → 拼接出并集画布（letterbox 黑边）。

    默认 (1152, 2048) 与 ``_aspect_to_size(None)`` 一致，即 ViMax 的竖屏缺省。
    """
    aspect = getattr(image_generator, "_aspect_ratio", None)
    if not aspect:
        return default
    try:
        from app.gateway.video_gen.engines.vimax.provider_bridge import _aspect_to_size

        width_str, _, height_str = _aspect_to_size(aspect).partition("x")
        return int(width_str), int(height_str)
    except Exception:  # pragma: no cover - 反查失败不该拖垮占位生成
        logger.warning("resolve_target_size failed for aspect=%r; using %s", aspect, default)
        return default


def make_static_video_from_frame(
    frame_path: str,
    output_path: str,
    duration: float = 5.2,
    *,
    width: int = 1920,
    height: int = 1080,
) -> str:
    """Turn a PNG into a static mp4 of the given duration via ffmpeg.

    5.2s matches happyhorse's per-shot duration. Returns output_path.
    On ffmpeg failure, falls back to a solid-color mp4 (still a valid mp4 that
    concat can consume) — NOT an empty/1-byte file, which would break the
    downstream concat demuxer silently (vg_cda1b579a220 review: a corrupted
    shot is worse than a visible solid-color shot, but a dead concat is worst).
    If even the solid-color fallback fails, raises — let the pipeline fail
    loudly rather than emit a broken file.
    """
    cmd = [
        "ffmpeg",
        "-y",
        "-loop",
        "1",
        "-i",
        frame_path,
        "-t",
        str(duration),
        "-r",
        "24",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-vf",
        "scale=trunc(iw/2)*2:trunc(ih/2)*2",  # libx264 needs even dims
        output_path,
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=60)
        if proc.returncode != 0:
            logger.warning("static-frame ffmpeg failed (%s); trying solid-color fallback", proc.stderr[-200:])
            # 审查发现 6：兜底必须跟随目标宽高。Task 1 修了直接调用点，漏了这里 ——
            # PNG 损坏时产出 1920x1080 占位，9:16 任务的画布又被污染。
            _make_solid_color_video(output_path, duration, width=width, height=height)
    except Exception:
        logger.exception("static-frame generation raised; trying solid-color fallback")
        _make_solid_color_video(output_path, duration, width=width, height=height)
    return output_path


def _make_solid_color_video(output_path: str, duration: float, color: str = "black", *, width: int = 1920, height: int = 1080) -> None:
    """Generate a solid-color mp4 as the last-resort fallback.

    Valid mp4 that the concat demuxer can consume. Used when the PNG→mp4 path
    fails (e.g. corrupt first_frame.png). Raises on ffmpeg failure — better a
    loud pipeline failure than a silently broken concat downstream.

    B2: 接受 width/height（占位帧须匹配目标比例，否则 9:16 任务出 16:9 黑帧→边界跳变抖动）。
    默认 1920x1080 保持 vimax 内部调用（make_static_video_from_frame）兼容。
    """
    cmd = [
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
        output_path,
    ]
    proc = subprocess.run(cmd, capture_output=True, timeout=60)
    if proc.returncode != 0:
        raise RuntimeError(f"solid-color fallback ffmpeg failed: {proc.stderr[-300:]!r}")
