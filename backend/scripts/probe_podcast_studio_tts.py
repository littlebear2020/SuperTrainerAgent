#!/usr/bin/env python3
"""Opt-in live probe for the two approved podcast stock speakers."""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills/public/podcast-studio/scripts"))

SPEAKERS = [
    ("zh_male_m191_uranus_bigtts", "云舟音色验收。"),
    ("zh_female_vv_uranus_bigtts", "Vivi 音色验收。"),
]


def main() -> int:
    if os.environ.get("RUN_LIVE") != "1":
        print("SKIP: set RUN_LIVE=1 for paid doubao TTS probes")
        return 0
    if not os.environ.get("VOLCENGINE_VOICE_ACCESS_KEY"):
        print("HOST_KEY_MISSING: VOLCENGINE_VOICE_ACCESS_KEY is absent from the process environment")
        return 0

    from _doubao_tts import synthesize

    output_dir = Path(tempfile.mkdtemp(prefix="podcast-studio-tts-probe-"))
    for speaker, text in SPEAKERS:
        try:
            audio = synthesize(text, speaker=speaker, speed_ratio=1.0)
            if not audio:
                raise RuntimeError("empty audio")
            path = output_dir / f"{speaker}.mp3"
            path.write_bytes(audio)
            probe = json.loads(
                subprocess.check_output(
                    ["ffprobe", "-v", "error", "-show_entries", "stream=codec_name,sample_rate", "-of", "json", str(path)],
                    text=True,
                )
            )
            stream = probe["streams"][0]
            if stream["codec_name"] != "mp3" or stream["sample_rate"] != "24000":
                raise RuntimeError(f"unexpected encoding: {stream}")
            subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "null", "-"], check=True)
            print(f"PROBE_OK speaker={speaker} path={path} codec=mp3 sample_rate=24000 bytes={len(audio)}")
        except Exception as exc:
            print(f"PROBE_FAILED speaker={speaker} error={type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
