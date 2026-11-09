"""Task 13 / Phase 1.3 E2E: overlay apad preserves full video duration.

Real ffmpeg verification that the apad fix works end-to-end: a 10s silent
video + a 3s audio must produce a ~10s output (NOT truncated to 3s, which
was the original -shortest-without-apad bug that cut 222s videos to 22s).

No 360/vimax API needed — just ffmpeg. This is the Phase 1.3 acceptance
check from the vimax plan: "vimax_final.mp4 时长 ≈ final_video.mp4 时长".
"""

from __future__ import annotations

import asyncio
import subprocess
import tempfile
from pathlib import Path

import pytest

from app.gateway.video_gen.engines.vimax.audio_overlay import overlay_audio_track


def _has_ffmpeg() -> bool:
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
        return True
    except (OSError, subprocess.CalledProcessError):
        return False


pytestmark = [
    pytest.mark.skipif(not _has_ffmpeg(), reason="需要 ffmpeg"),
    pytest.mark.e2e,  # 2026-10-05 基建批：真 ffmpeg E2E
]


def _ffprobe_duration(path: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True,
        text=True,
    )
    return float(r.stdout.strip())


def _make_silent_video(path: Path, seconds: float) -> None:
    """Generate a silent color video of the given duration."""
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=black:s=320x240:r=30", "-t", str(seconds), "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
        capture_output=True,
        check=True,
    )


def _make_short_audio(path: Path, seconds: float) -> None:
    """Generate a short sine-wave audio (simulates short TrainerAI TTS)."""
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", "-c:a", "libmp3lame", str(path)],
        capture_output=True,
        check=True,
    )


def test_overlay_apad_preserves_video_duration_not_truncated(tmp_path):
    """10s video + 3s audio → output ≈ 10s (apad pads audio; -shortest picks video)."""
    video = tmp_path / "final_video.mp4"
    _make_silent_video(video, seconds=10.0)
    assert abs(_ffprobe_duration(video) - 10.0) < 0.2, "video must be ~10s"

    # task_dir with a SHORT audio.mp3 (3s) — the pre-apad bug would truncate to 3s
    task_dir = tmp_path
    _make_short_audio(task_dir / "audio.mp3", seconds=3.0)
    assert abs(_ffprobe_duration(task_dir / "audio.mp3") - 3.0) < 0.2

    out = asyncio.run(overlay_audio_track(video, task_dir))
    assert out.exists(), "overlay must produce vimax_final.mp4"

    out_dur = _ffprobe_duration(out)
    print(f"[apad] video=10.0s audio=3.0s output={out_dur:.2f}s")
    # Acceptance: output ≈ video duration, NOT ≈ audio duration.
    assert out_dur > 9.0, f"output must preserve video duration (~10s), got {out_dur:.2f}s — apad fix not working"
    assert out_dur < 11.0, f"output must not exceed video by much, got {out_dur:.2f}s"


def test_overlay_with_bgm_also_preserves_video_duration(tmp_path):
    """BGM path (amix duration=first + apad on voice) must also preserve video length."""
    video = tmp_path / "final_video.mp4"
    _make_silent_video(video, seconds=8.0)
    _make_short_audio(tmp_path / "audio.mp3", seconds=2.0)
    _make_short_audio(tmp_path / "bgm.mp3", seconds=5.0)

    out = asyncio.run(overlay_audio_track(video, tmp_path))
    out_dur = _ffprobe_duration(out)
    print(f"[bgm apad] video=8.0s audio=2.0s bgm=5.0s output={out_dur:.2f}s")
    assert out_dur > 7.0, f"BGM path must also preserve video duration, got {out_dur:.2f}s"


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as td:
        test_overlay_apad_preserves_video_duration_not_truncated(Path(td))
        print("PASS: Phase 1.3 apad — video not truncated to audio length")
