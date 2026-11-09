"""
Merge a single storyboard segment: video + audio + optional subtitle overlay.

The video is automatically extended (freeze last frame) to match audio duration,
so narration is never cut short.

Usage:
    python3 merge_segment.py \
        --video /mnt/user-data/outputs/shot-01.mp4 \
        --audio /mnt/user-data/outputs/shot-01.mp3 \
        --subtitle "这是这句台词的字幕文本" \
        --output /mnt/user-data/outputs/merged/shot-01.mp4 \
        --pause 0.5
"""

import argparse
import json
import os
import subprocess
import sys

# Chinese font search paths (ordered by priority)
_CHINESE_FONT_PATHS = [
    "/usr/share/fonts/truetype/ppt-fallback/NotoSansCJKsc-VF.otf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
]


def _find_chinese_font() -> str | None:
    """Find an available Chinese-capable font on the system."""
    for path in _CHINESE_FONT_PATHS:
        if os.path.exists(path):
            return path
    for root, _dirs, files in os.walk("/usr/share/fonts"):
        for f in files:
            if "NotoSansCJK" in f and f.endswith((".otf", ".ttf", ".ttc")):
                return os.path.join(root, f)
    return None


def _get_duration(path: str) -> float:
    """Get media duration in seconds using ffprobe."""
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True,
    )
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def _get_video_fps(path: str) -> float:
    """Get video FPS using ffprobe."""
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=r_frame_rate",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True,
    )
    try:
        parts = r.stdout.strip().split("/")
        if len(parts) == 2:
            return float(parts[0]) / float(parts[1])
        return float(parts[0])
    except (ValueError, IndexError):
        return 30.0


def merge_segment(
    video: str,
    audio: str,
    subtitle: str | None,
    output: str,
    pause: float = 0.5,
    font_size: int = 26,
    font_color: str = "white",
    border_color: str = "black",
    subtitle_y: int = 80,
) -> str:
    if not os.path.exists(video):
        return f"Error: video file not found: {video}"
    if not os.path.exists(audio):
        return f"Error: audio file not found: {audio}"

    os.makedirs(os.path.dirname(output) or ".", exist_ok=True)

    # Step 1: Measure durations
    audio_dur = _get_duration(audio)
    video_dur = _get_duration(video)
    target_audio_dur = audio_dur + pause

    # Step 2: Create padded audio (add silence at end for transition)
    padded_audio = output.rsplit(".", 1)[0] + "_padded.mp3"
    pad_cmd = [
        "ffmpeg", "-y",
        "-i", audio,
        "-af", f"apad=whole_dur={target_audio_dur}",
        "-c:a", "libmp3lame", "-q:a", "4",
        padded_audio,
    ]
    r = subprocess.run(pad_cmd, capture_output=True, text=True)
    if r.returncode != 0:
        return f"Error padding audio: {r.stderr[:500]}"

    # Step 3: Build video filter chain
    # If video is shorter than audio, freeze last frame to extend
    fps = _get_video_fps(video)
    vf_filters = []

    if video_dur < target_audio_dur and video_dur > 0:
        # Calculate how many extra frames needed for the remaining time
        extra_secs = target_audio_dur - video_dur
        extra_frames = int(extra_secs * fps)
        # tpad=stop=N:stop_mode=clone repeats the last frame N times
        vf_filters.append(f"tpad=stop={extra_frames}:stop_mode=clone")

    # Add subtitle overlay if provided
    if subtitle:
        font_file = _find_chinese_font()
        if not font_file:
            return ("Error: No Chinese font found. Cannot render subtitles. "
                    "Install NotoSansCJK or pass --subtitle '' to skip.")

        # Escape special characters for drawtext
        escaped_sub = (
            subtitle.replace("'", "'")
            .replace("%", "%%")
            .replace(":", "\\:")
            .replace("[", "\\[")
            .replace("]", "\\]")
            .replace("{", "\\{")
            .replace("}", "\\}")
        )

        # Subtitle with semi-transparent background box
        vf_filters.append(
            f"drawtext=text='{escaped_sub}'"
            f":fontfile={font_file}"
            f":fontsize={font_size}"
            f":fontcolor={font_color}"
            f":borderw=1"
            f":bordercolor={border_color}"
            f":box=1"
            f":boxcolor=black@0.5"
            f":boxborderw=8"
            f":x=(w-text_w)/2"
            f":y=h-{subtitle_y}"
        )

    vf_str = ",".join(vf_filters) if vf_filters else None

    # Step 4: Merge video + audio
    merge_cmd = ["ffmpeg", "-y", "-i", video, "-i", padded_audio]
    if vf_str:
        merge_cmd.extend(["-vf", vf_str])
    merge_cmd.extend([
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-shortest",
        "-movflags", "+faststart",
        output,
    ])

    r = subprocess.run(merge_cmd, capture_output=True, text=True)
    if r.returncode != 0:
        return f"Error merging: {r.stderr[:500]}"

    # Cleanup
    if os.path.exists(padded_audio):
        os.remove(padded_audio)

    out_dur = _get_duration(output)
    size_mb = os.path.getsize(output) / (1024 * 1024)
    ext_info = f" (video {video_dur:.1f}s → extended to {out_dur:.1f}s)" if video_dur < target_audio_dur else ""
    return f"Merged: {output} ({size_mb:.1f} MB, {out_dur:.1f}s){ext_info}"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Merge video + audio + subtitle (auto-extends video to match audio)")
    parser.add_argument("--video", required=True, help="Path to video file")
    parser.add_argument("--audio", required=True, help="Path to audio file")
    parser.add_argument("--subtitle", default=None, help="Subtitle text (Chinese supported)")
    parser.add_argument("--output", required=True, help="Output path")
    parser.add_argument("--pause", type=float, default=0.5, help="Transition pause in seconds (default: 0.5)")
    parser.add_argument("--font-size", type=int, default=26, help="Subtitle font size (default: 26)")
    parser.add_argument("--subtitle-y", type=int, default=80, help="Subtitle Y offset from bottom (default: 80)")
    args = parser.parse_args()

    result = merge_segment(
        video=args.video,
        audio=args.audio,
        subtitle=args.subtitle,
        output=args.output,
        pause=args.pause,
        font_size=args.font_size,
        subtitle_y=args.subtitle_y,
    )
    print(result)
