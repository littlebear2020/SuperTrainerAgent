"""Audio transcription script for the video-dl skill.

Extracts audio via ffmpeg, then transcribes using volcengine/asr-turbo
via the 360 API (https://api.360.cn/v1/audios/generations).
Outputs SRT file and JSON status.

Network required: the sandbox must be able to reach api.360.cn (AI 模型调用，
符合自闭环约束）。2026-09-09 起已移除境外临时文件托管回退（uguu/tmpfiles/
litterbox）——用户音频不出域，base64 直连失败即如实报错。

Uses Python requests instead of curl to avoid nginx proxy interception
in sandbox environments.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import requests

# Bypass any Docker-inherited HTTP proxy (nginx forward proxy) for external API calls.
# Corporate servers often configure Docker daemon with a proxy that intercepts outbound
# requests; skills need direct internet access, not proxy-routed access.
_NO_PROXY = {"http": None, "https": None}

ASR_API_KEY = os.getenv("ASR_360_API_KEY") or os.getenv("QIHU_API_KEY", "")
ASR_API_URL = os.getenv("ASR_360_BASE_URL", "https://api.360.cn/v1/audios/generations")


def extract_audio(video_path: str, audio_path: str) -> None:
    subprocess.run(
        ["ffmpeg", "-i", video_path,
         "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
         "-y", audio_path],
        capture_output=True, check=True,
    )


def _format_timestamp(seconds: float) -> str:
    """Format seconds to SRT timestamp HH:MM:SS,mmm."""
    hrs = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds - int(seconds)) * 1000)
    return f"{hrs:02d}:{mins:02d}:{secs:02d},{millis:03d}"


# ---------------------------------------------------------------------------
# ASR API call
# ---------------------------------------------------------------------------

def _call_asr_api_base64(audio_path: str) -> dict:
    """Call the 360 ASR API with base64-encoded audio data (no file hosting needed)."""
    import base64

    with open(audio_path, "rb") as f:
        audio_b64 = base64.b64encode(f.read()).decode("utf-8")

    payload = {
        "model": "volcengine/asr-turbo",
        "extra_body": {
            "user": {"uid": "1488017566"},
            "audio": {"data": audio_b64},
            "request": {"model_name": "bigmodel"},
        },
    }
    return _call_asr_api(payload)


def _call_asr_api(payload: dict) -> dict:
    """Call the 360 ASR API with volcengine/asr-turbo model.

    Returns the parsed extra_data dict on success, raises on failure.
    """
    r = requests.post(
        ASR_API_URL,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {ASR_API_KEY}",
        },
        json=payload,
        timeout=180,
        proxies=_NO_PROXY,
    )

    response = r.json()
    if "error" in response:
        raise RuntimeError(f"ASR API error: {response['error']}")

    extra_data = response.get("data", {}).get("extra_data", "")
    if not extra_data:
        raise RuntimeError("ASR API returned empty extra_data")

    return json.loads(extra_data)


# ---------------------------------------------------------------------------
# Main transcription pipeline
# ---------------------------------------------------------------------------

def transcribe_api(audio_path: str, output_dir: str, model: str, language: str) -> str:
    """Transcribe using volcengine/asr-turbo via 360 API.

    唯一路径：base64 直连上传（无外部文件托管，受限网络可用）。
    失败即 raise——不允许把用户音频上传到境外临时托管做回退（自闭环拍板项 6）。
    """
    if not ASR_API_KEY:
        raise RuntimeError(
            "No ASR API key configured. "
            "Set ASR_360_API_KEY or QIHU_API_KEY environment variable."
        )

    print(f"[transcribe] base64 direct upload: {audio_path}", file=sys.stderr)
    try:
        extra_data = _call_asr_api_base64(audio_path)
    except Exception as e:
        raise RuntimeError(
            f"ASR base64 直连失败（已无文件托管回退路径，请检查网络/api.360.cn 可达性后重试）: {e}"
        ) from e
    print("[transcribe] ASR success via base64 direct upload!", file=sys.stderr)

    print(f"[transcribe] ASR complete, duration={extra_data.get('audio_info', {}).get('duration', 0)}ms",
          file=sys.stderr)

    utterances = extra_data.get("result", {}).get("utterances", [])
    if not utterances:
        raise RuntimeError("ASR returned no utterances")

    srt_path = Path(output_dir) / f"{Path(audio_path).stem}.srt"
    with open(srt_path, "w", encoding="utf-8") as f:
        for i, utt in enumerate(utterances, 1):
            start_ms = utt.get("start_time", 0)
            end_ms = utt.get("end_time", 0)
            text = utt.get("text", "").strip()
            f.write(f"{i}\n")
            f.write(f"{_format_timestamp(start_ms / 1000)} --> {_format_timestamp(end_ms / 1000)}\n")
            f.write(f"{text}\n\n")

    if not srt_path.exists():
        raise FileNotFoundError(f"No SRT generated in {output_dir}")
    return str(srt_path)


def main():
    parser = argparse.ArgumentParser(description="Transcribe video audio to SRT")
    parser.add_argument("video", help="Video file path")
    parser.add_argument("-o", "--output", default=None,
                        help="Output SRT path (default: same dir as video)")
    parser.add_argument("-m", "--model", default="volcengine/asr-turbo",
                        help="ASR model (default: volcengine/asr-turbo)")
    parser.add_argument("-l", "--language", default="zh", help="Language code")
    args = parser.parse_args()

    video_dir = str(Path(args.video).parent)
    output_srt = args.output or str(Path(args.video).with_suffix(".srt"))

    try:
        audio_path = str(Path(video_dir) / "_audio.wav")
        extract_audio(args.video, audio_path)
        srt_path = transcribe_api(audio_path, video_dir, args.model, args.language)

        if srt_path != output_srt:
            shutil.move(srt_path, output_srt)

        print(json.dumps({"success": True, "subtitle": output_srt}))
    except Exception as e:
        print(json.dumps({"success": False, "error": str(e)}))
        sys.exit(1)
    finally:
        Path(audio_path).unlink(missing_ok=True)


if __name__ == "__main__":
    main()
