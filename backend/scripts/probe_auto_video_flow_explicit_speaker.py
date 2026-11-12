#!/usr/bin/env python3
"""Opt-in, single-call live probe for auto-video-flow explicit-speaker TTS."""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills" / "public" / "auto-video-flow" / "scripts"))

from generate_audio_template import SPEAKER, generate_tts


def _audio_duration(path: Path) -> float:
    report = json.loads(
        subprocess.check_output(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration:stream=codec_type",
                "-of",
                "json",
                str(path),
            ],
            text=True,
        )
    )
    duration = float(report["format"]["duration"])
    if duration <= 0 or not any(stream.get("codec_type") == "audio" for stream in report.get("streams", [])):
        raise RuntimeError("invalid audio probe")
    return duration


def main() -> int:
    if os.environ.get("RUN_LIVE") != "1":
        print("SKIP: set RUN_LIVE=1 for one paid doubao TTS call")
        return 0
    if not os.environ.get("VOLCENGINE_VOICE_ACCESS_KEY", "").strip():
        print("VOLCENGINE_VOICE_ACCESS_KEY MISSING")
        return 2
    if os.environ.get("DOUBAO_TTS_DEFAULT_SPEAKER", "").strip():
        print("DOUBAO_TTS_DEFAULT_SPEAKER PRESENT; probe requires it MISSING")
        return 2

    output_dir = Path(os.environ.get("PROBE_OUTPUT_DIR") or tempfile.mkdtemp(prefix="auto-video-flow-tts-"))
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "explicit-speaker.mp3"
    try:
        generate_tts("文字动效验收。", str(output_path), speaker=SPEAKER)
        duration = _audio_duration(output_path)
    except Exception as exc:
        print(f"PROBE_FAILED error={type(exc).__name__}", file=sys.stderr)
        return 1

    print(f"PROBE_OK speaker={SPEAKER} path={output_path} duration={duration:.3f} bytes={output_path.stat().st_size}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
