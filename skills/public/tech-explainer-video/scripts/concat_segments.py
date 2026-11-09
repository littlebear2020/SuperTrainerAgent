"""
Concatenate all merged storyboard segments into final video.
Optional: overlay background music (BGM).

Usage:
    python3 concat_segments.py \
        --segments /mnt/user-data/outputs/merged/shot-01.mp4 \
                    /mnt/user-data/outputs/merged/shot-02.mp4 \
                    ... \
        --output /mnt/user-data/outputs/final-video.mp4

    # With BGM (future extension):
    python3 concat_segments.py \
        --segments shot-01.mp4 shot-02.mp4 ... \
        --output final-video.mp4 \
        --bgm background-music.mp3 \
        --bgm-volume 0.15
"""

import argparse
import os
import subprocess
import sys
import tempfile


def concat_segments(
    segments: list[str],
    output: str,
    bgm: str | None = None,
    bgm_volume: float = 0.15,
) -> str:
    # Validate inputs
    missing = [s for s in segments if not os.path.exists(s)]
    if missing:
        return f"Error: missing segment files: {missing}"

    os.makedirs(os.path.dirname(output) or ".", exist_ok=True)

    # Step 1: Concatenate segments
    concat_output = output if not bgm else output.rsplit(".", 1)[0] + "_no_bgm.mp4"

    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        for seg in segments:
            f.write(f"file '{seg}'\n")
        filelist_path = f.name

    concat_cmd = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", filelist_path,
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        concat_output,
    ]
    r = subprocess.run(concat_cmd, capture_output=True, text=True)
    os.remove(filelist_path)

    if r.returncode != 0:
        return f"Error concatenating: {r.stderr[:500]}"

    # Step 2: Add BGM if provided
    if bgm and os.path.exists(bgm):
        # Mix original audio with BGM, looping BGM if shorter
        mix_cmd = [
            "ffmpeg", "-y",
            "-i", concat_output,
            "-stream_loop", "-1",
            "-i", bgm,
            "-filter_complex",
            f"[0:a]volume=1.0[main];[1:a]volume={bgm_volume}[bgm];[main][bgm]amix=inputs=2:duration=first:dropout_transition=2[aout]",
            "-map", "0:v", "-map", "[aout]",
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "128k",
            "-shortest",
            "-movflags", "+faststart",
            output,
        ]
        r = subprocess.run(mix_cmd, capture_output=True, text=True)
        if r.returncode != 0:
            # Fall back: just use concat output without BGM
            os.rename(concat_output, output)
            return f"Warning: BGM mix failed, saved without BGM to {output}"
        os.remove(concat_output)
    elif concat_output != output:
        os.rename(concat_output, output)

    # Get duration
    dur_cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        output,
    ]
    r = subprocess.run(dur_cmd, capture_output=True, text=True)
    duration = float(r.stdout.strip()) if r.returncode == 0 else 0

    size_mb = os.path.getsize(output) / (1024 * 1024)
    mins, secs = divmod(int(duration), 60)
    return (
        f"Final video saved to {output} ({size_mb:.1f} MB)\n"
        f"Duration: {mins}:{secs:02d}"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Concatenate storyboard segments into final video")
    parser.add_argument("--segments", nargs="+", required=True, help="Segment video files (ordered)")
    parser.add_argument("--output", required=True, help="Output path for final video")
    parser.add_argument("--bgm", default=None, help="BGM file path (optional)")
    parser.add_argument("--bgm-volume", type=float, default=0.15, help="BGM volume (0.0-1.0, default: 0.15)")
    args = parser.parse_args()

    result = concat_segments(
        segments=args.segments,
        output=args.output,
        bgm=args.bgm,
        bgm_volume=args.bgm_volume,
    )
    print(result)
