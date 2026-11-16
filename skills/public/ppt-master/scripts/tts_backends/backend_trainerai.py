"""TrainerAI TTS backend — ByteDance doubao seed-tts-2.0 (openspeech SSE).

Policy (SKILL.md override 第 1 条 / TRAINERAI_PATCHES P2): ppt-master narration
MUST go through ByteDance ``seed-tts-2.0``; the upstream bundled edge-tts and the
old 360 ``stream_speech`` / preset-voice path are NOT used in TrainerAI.

Interface follows the v2.10.0 cloud-backend pattern (same shape as
backend_qwen): ``generate(text, output_path, *, api_key, voice_id) -> None``,
plus ``output_extension()`` and ``read_trainerai_api_key()``, so
notes_to_audio.py can dispatch via ``--provider trainerai``.

Speaker: ``voice_id`` is an openSpeech speaker id; empty/None falls back to
``DEFAULT_SPEAKER`` (seed-tts-2.0 console voice). Language detection uses
``additions.enable_language_detector`` without ``explicit_language``.
Long text is split by sentence and re-encoded as one MP3 with ffmpeg after all
chunks succeed; failures never replace an existing output.

The SSE contract mirrors
``skills/public/auto-video-flow/scripts/_doubao_tts.py`` (sandbox skills run as
independent processes and cannot import across skill roots):
  POST https://openspeech.bytedance.com/api/v3/tts/unidirectional/sse
  Headers: X-Api-Key / X-Api-Resource-Id: seed-tts-2.0 / X-Api-Request-Id
  Body: user.uid + req_params{text, speaker, speed_ratio, audio_params}
  Events: 352 audio chunk (base64), 152 finish (code 20000000), 153 failure.

Failure policy: missing key, HTTP error, SSE session failure, or no audio all
raise RuntimeError — the caller reports honestly and stops; never fall back to
edge-tts or 360 stream_speech.
"""

from __future__ import annotations

import base64
import http.client
import json
import os
import subprocess
import tempfile
import time
import urllib.error
import uuid
from pathlib import Path
from urllib.request import Request, urlopen

from tts_backends.backend_common import publish_audio_bytes

ENDPOINT = "https://openspeech.bytedance.com/api/v3/tts/unidirectional/sse"
RESOURCE_ID = "seed-tts-2.0"
DEFAULT_SPEAKER = "zh_male_m191_uranus_bigtts"
DEFAULT_SPEED_RATIO = 1.0
MAX_TEXT_CHARS = 1000
_SENTENCE_ENDINGS = frozenset("。！？!?；;")
_SENTENCE_CLOSERS = frozenset("\"'”’)]}』」")

_TIMEOUT = 120.0
_MAX_ATTEMPTS = 2  # 1 次指数退避重试（网络抖动容忍）
_RETRY_BASE_DELAY = 2.0


def output_extension() -> str:
    return ".mp3"


def read_trainerai_api_key() -> str:
    """Read the only supported TrainerAI TTS credential."""
    value = (os.environ.get("VOLCENGINE_VOICE_ACCESS_KEY") or "").strip()
    if value:
        return value
    raise RuntimeError(
        "No TrainerAI TTS key found. Set VOLCENGINE_VOICE_ACCESS_KEY. "
        "(ZYUN_API_KEY / QIHU_API_KEY are not supported here; ppt-master's "
        "bundled edge-tts is NOT used in TrainerAI.)"
    )


def resolve_speaker(voice_id: str | None) -> str:
    """Empty voice id means the fixed seed-tts-2.0 default speaker."""
    return (voice_id or "").strip() or DEFAULT_SPEAKER


def _sentence_units(text: str) -> list[str]:
    units: list[str] = []
    start = 0
    index = 0
    while index < len(text):
        char = text[index]
        is_ending = char in _SENTENCE_ENDINGS
        if char == ".":
            next_char = text[index + 1] if index + 1 < len(text) else ""
            is_ending = not next_char or next_char.isspace()
        if not is_ending:
            index += 1
            continue
        end = index + 1
        while end < len(text) and text[end] in _SENTENCE_CLOSERS:
            end += 1
        while end < len(text) and text[end].isspace():
            end += 1
        units.append(text[start:end])
        start = end
        index = end
    if start < len(text):
        units.append(text[start:])
    return [unit for unit in units if unit.strip()]


def _split_text(text: str, max_chars: int) -> list[str]:
    if max_chars < 1:
        raise ValueError("max_chars must be at least 1")
    chunks: list[str] = []
    current = ""
    for unit in _sentence_units(text):
        while len(unit) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(unit[:max_chars])
            unit = unit[max_chars:]
        if not unit:
            continue
        if current and len(current) + len(unit) > max_chars:
            chunks.append(current)
            current = ""
        current += unit
    if current:
        chunks.append(current)
    return [chunk for chunk in chunks if chunk.strip()]


def _build_request(text: str, speaker: str, api_key: str) -> Request:
    payload = json.dumps(
        {
            "user": {"uid": "deerflow"},
            "req_params": {
                "text": text,
                "speaker": speaker,
                "speed_ratio": DEFAULT_SPEED_RATIO,
                "audio_params": {"format": "mp3", "sample_rate": 24000},
                "additions": json.dumps({"enable_language_detector": True}),
            },
        }
    ).encode("utf-8")
    headers = {
        "X-Api-Key": api_key,
        "X-Api-Resource-Id": RESOURCE_ID,
        "X-Api-Request-Id": str(uuid.uuid4()),
        "Content-Type": "application/json",
    }
    return Request(ENDPOINT, data=payload, headers=headers, method="POST")


def _request_with_retry(request: Request) -> bytes:
    """Run the SSE request with one exponential-backoff retry on transient errors."""
    delay = _RETRY_BASE_DELAY
    for attempt in range(_MAX_ATTEMPTS):
        try:
            return _synthesize_once(request)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:500]
            if (exc.code == 429 or exc.code >= 500) and attempt < _MAX_ATTEMPTS - 1:
                time.sleep(delay)
                delay *= 2
                continue
            raise RuntimeError(f"doubao TTS HTTP {exc.code}: {detail}") from exc
        except (OSError, http.client.HTTPException) as exc:
            if attempt < _MAX_ATTEMPTS - 1:
                time.sleep(delay)
                delay *= 2
                continue
            raise RuntimeError(
                f"doubao TTS connection error after {_MAX_ATTEMPTS} attempts: {exc}"
            ) from exc
    raise RuntimeError("doubao TTS connection error: unreachable state")


def _synthesize_once(request: Request) -> bytes:
    finished = False
    with urlopen(request, timeout=_TIMEOUT) as response:
        audio_chunks: list[bytes] = []
        current_event: str | None = None
        current_data_lines: list[str] = []

        for raw_line in response:
            line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
            if line.startswith("event:"):
                current_event = line[len("event:") :].strip()
            elif line.startswith("data:"):
                current_data_lines.append(line[len("data:") :].strip())
            elif line == "" and current_data_lines:
                finished = _handle_sse_event(
                    current_event,
                    "\n".join(current_data_lines),
                    audio_chunks,
                )
                current_data_lines.clear()
                current_event = None
                if finished:
                    break
        if current_data_lines and not finished:
            finished = _handle_sse_event(
                current_event,
                "\n".join(current_data_lines),
                audio_chunks,
            )

    if not finished:
        raise RuntimeError("doubao TTS ended before a successful 152 finish event")
    if not audio_chunks:
        raise RuntimeError("doubao TTS returned no audio data")
    return b"".join(audio_chunks)


def _handle_sse_event(
    event_type: str | None,
    data_str: str,
    audio_chunks: list[bytes],
) -> bool:
    try:
        event = json.loads(data_str)
    except json.JSONDecodeError as exc:
        raise RuntimeError("doubao TTS returned invalid SSE event JSON") from exc
    if not isinstance(event, dict):
        raise RuntimeError("doubao TTS returned a non-object SSE event")
    code = event.get("code", -1)
    message = event.get("message", "Unknown error")

    if event_type == "153":
        raise RuntimeError(f"doubao TTS session failed (code={code}): {message}")
    if event_type == "352":
        if code != 0:
            raise RuntimeError(f"doubao TTS error (code={code}): {message}")
        audio_b64 = event.get("data", "")
        if audio_b64:
            audio_chunks.append(base64.b64decode(audio_b64, validate=True))
    elif event_type == "152":
        if str(code) != "20000000":
            raise RuntimeError(f"doubao TTS session error (code={code}): {message}")
        return True
    return False


def _merge_mp3_chunks(chunk_paths: list[Path], output_path: Path) -> None:
    command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
    for path in chunk_paths:
        command.extend(["-i", str(path)])
    inputs = "".join(f"[{index}:a]" for index in range(len(chunk_paths)))
    command.extend([
        "-filter_complex",
        f"{inputs}concat=n={len(chunk_paths)}:v=0:a=1[out]",
        "-map",
        "[out]",
        "-c:a",
        "libmp3lame",
        "-ar",
        "24000",
        "-ac",
        "1",
        "-f",
        "mp3",
        str(output_path),
    ])
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            timeout=300,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"doubao TTS ffmpeg merge failed: {exc}") from exc
    if result.returncode != 0:
        if isinstance(result.stderr, bytes):
            detail = result.stderr.decode("utf-8", errors="replace")
        else:
            detail = str(result.stderr or "")
        raise RuntimeError(f"doubao TTS ffmpeg merge failed: {detail[:1000]}")
    if not output_path.is_file() or output_path.stat().st_size <= 0:
        raise RuntimeError("doubao TTS ffmpeg merge produced no MP3 output")


def generate(text: str, output_path: Path, *, api_key: str, voice_id: str) -> None:
    """Synthesize narration to output_path (MP3). Called by notes_to_audio.py."""
    key = (api_key or "").strip()
    if not key:
        raise RuntimeError("No TrainerAI TTS api_key provided (VOLCENGINE_VOICE_ACCESS_KEY).")
    speaker = resolve_speaker(voice_id)
    text_chunks = _split_text(text, MAX_TEXT_CHARS)
    if not text_chunks:
        raise ValueError("TrainerAI TTS text must contain non-whitespace characters")
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if len(text_chunks) == 1:
        audio = _request_with_retry(_build_request(text_chunks[0], speaker, key))
        publish_audio_bytes(audio, output_path)
        return

    with tempfile.TemporaryDirectory(
        prefix=f".{output_path.name}.",
        dir=output_path.parent,
    ) as temporary_dir:
        work_dir = Path(temporary_dir)
        chunk_paths: list[Path] = []
        for index, text_chunk in enumerate(text_chunks):
            audio = _request_with_retry(_build_request(text_chunk, speaker, key))
            chunk_path = work_dir / f"chunk-{index:04d}.mp3"
            chunk_path.write_bytes(audio)
            chunk_paths.append(chunk_path)
        merged_path = work_dir / "merged.mp3"
        _merge_mp3_chunks(chunk_paths, merged_path)
        os.replace(merged_path, output_path)
