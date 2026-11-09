"""Remotion props builder — convert Storyboard to Remotion Composition props.

Also defines StoryboardResult (TypedDict) used as the return type when
_prepare_storyboard_video() runs for the remotion data-driven path.

⚠️ 2026-08-03 Task 7：本模块在 engine_arch="multi_engine"（现行）下**不可达**。
remotion 走 chat 交接（前端 remotion-chat-handoff.ts → course-video agent 直接写
Remotion 代码），POST /api/video/create 的 video_type Literal 不含 remotion
（``schemas.CreateVideoRequest``）→ 422。仅 2026-07-15 之前创建的 legacy 草稿
可能触达 ``_render_remotion``。
审查此处前请先确认调用路径真实存在——2026-08-03 的一次审计因误判此为活代码
而浪费了一轮（原始反馈「风格下拉对 remotion 无效」的真实修复点在前端，
不在 ``build_remotion_props``）。
"""

from __future__ import annotations

import re
from typing import TypedDict

from ..storyboard import Storyboard


class StoryboardResult(TypedDict):
    storyboard: Storyboard
    subtitle_path: str | None
    audio_dir: str
    template_id: str | None


def build_remotion_props(
    storyboard: Storyboard,
    subtitle_path: str | None = None,
) -> dict:
    """Convert a Storyboard to a Remotion Composition props dict.

    fps comes from storyboard.config.fps (default 30).
    audioFile is extracted from each frame.audio_path via PurePosixPath
    and prefixed with "audio/" for sandbox staticFile() resolution.
    If frame.audio_path is None, audioFile is set to "".
    """
    fps = storyboard.config.fps
    width = storyboard.config.width
    height = storyboard.config.height

    # Build scenes
    scenes: list[dict] = []
    for frame in storyboard.frames:
        if frame.audio_path:
            # Normalize backslashes for cross-platform compatibility
            normalized = frame.audio_path.replace("\\", "/")
            filename = normalized.rsplit("/", 1)[-1]
            audio_file = f"audio/{filename}"
        else:
            audio_file = ""
        scenes.append(
            {
                "index": frame.index,
                "narration": frame.narration,
                "durationInFrames": round(frame.duration * fps),
                "audioFile": audio_file,
            }
        )

    # Build subtitle segments from SRT
    subtitle_segments: list[dict] = []
    if subtitle_path:
        subtitle_segments = _parse_srt_to_segments(subtitle_path, fps, storyboard)

    total_frames = round(storyboard.total_duration * fps)

    return {
        "title": storyboard.title,
        "fps": fps,
        "width": width,
        "height": height,
        "totalDurationFrames": total_frames,
        "style": storyboard.config.style,
        "transition": storyboard.config.transition,
        "transitionDurationFrames": round(storyboard.config.transition_duration * fps),
        "bgmEnabled": bool(storyboard.config.bgm_path),
        "bgmVolume": storyboard.config.bgm_volume,
        "scenes": scenes,
        "subtitleSegments": subtitle_segments,
    }


def _parse_srt_time(ts: str) -> float:
    """Parse an SRT timestamp 'HH:MM:SS,mmm' to seconds."""
    h, m, s_ms = ts.strip().split(":")
    s, ms = s_ms.split(",")
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


def _parse_srt_to_segments(
    srt_path: str,
    fps: int,
    storyboard: Storyboard,
) -> list[dict]:
    """Parse SRT file into subtitle segments mapped to scene indices."""
    try:
        content = open(srt_path, encoding="utf-8").read()
    except (OSError, FileNotFoundError):
        return []

    # Parse SRT entries: seq, start --> end, text
    pattern = re.compile(
        r"(\d+)\s+"
        r"(\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2},\d{3})\s+"
        r"(.*?)(?=\n\d+\s+\d{2}:\d{2}:\d{2},\d{3}|\Z)",
        re.DOTALL,
    )

    segments: list[dict] = []
    # Build frame boundaries (cumulative frame offsets)
    frame_offsets = []
    acc = 0.0
    for frame in storyboard.frames:
        frame_offsets.append(acc)
        acc += frame.duration

    for match in pattern.finditer(content):
        text = match.group(4).strip()
        if not text:
            continue
        start_sec = _parse_srt_time(match.group(2))
        end_sec = _parse_srt_time(match.group(3))
        start_frame = round(start_sec * fps)
        end_frame = round(end_sec * fps)

        # Map to scene index based on which frame offset contains start_sec
        scene_idx = 0
        for i, offset in enumerate(frame_offsets):
            if start_sec >= offset:
                scene_idx = i

        segments.append(
            {
                "sceneIndex": scene_idx,
                "startFrame": start_frame,
                "endFrame": end_frame,
                "text": text,
            }
        )

    return segments
