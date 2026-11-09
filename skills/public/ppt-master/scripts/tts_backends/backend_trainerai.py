"""TrainerAI TTS backend — Voice Twin (360 TTS preset voices).

Policy (SKILL.md override 第 1 条 / TRAINERAI_PATCHES P2): ppt-master narration MUST go through the Voice
Twin voice hub (360 preset / Volcengine clone), NOT ppt-master's bundled
edge-tts. Self-contained port of the 360 TTS SSE + WAV-merge logic from
app/gateway/voice_twin/qihu_tts_client.py (sandbox cannot import app layer).

Interface follows the v2.10.0 cloud-backend pattern (same shape as
backend_qwen): generate(text, output_path, *, api_key, voice_id) -> None,
plus output_extension() and read_trainerai_api_key(), so notes_to_audio.py
can dispatch via --provider trainerai.

360 voice IDs are ints (preset voices, e.g. 146/158/142/284).
Pass voice="146" or "preset:146".

Includes the 2026-07-21 fix: trailing SSE usage events carry code="" which is
NOT an error (same as qihu_tts_client.py fix in commit bde232508).
"""

from __future__ import annotations

import base64
import json
import logging
import os
import urllib.error
import wave
from io import BytesIO
from pathlib import Path
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

STREAM_SPEECH_URL = "https://api.360.cn/v1/audio/stream_speech"

# 360 preset voices (id -> label). Cover the narration default set.
PRESET_VOICES: dict[int, str] = {
    146: "知岚（女声）",
    158: "知悦（女声）",
    142: "知轩（男声）",
    284: "知远（男声）",
}
DEFAULT_VOICE_ID = 146

MAX_CHUNK_CHARS = 500


def output_extension() -> str:
    return ".wav"


def read_trainerai_api_key() -> str:
    for env in ("QIHU_API_KEY", "ZYUN_API_KEY"):
        val = (os.environ.get(env) or "").strip()
        if val:
            return val
    raise RuntimeError(
        "No TrainerAI TTS key found. Set QIHU_API_KEY or ZYUN_API_KEY. "
        "(ppt-master bundled edge-tts is NOT used in TrainerAI.)"
    )


def resolve_voice_id(voice: str) -> int:
    """Resolve a voice spec to a 360 preset int id."""
    spec = (voice or "").strip()
    if spec.lower().startswith("preset:"):
        spec = spec.split(":", 1)[1].strip()
    try:
        vid = int(spec)
        if vid in PRESET_VOICES:
            return vid
    except ValueError:
        pass
    logger.warning("Unrecognized trainerai voice %r, defaulting to %d", voice, DEFAULT_VOICE_ID)
    return DEFAULT_VOICE_ID


def _raise_for_event_code(code, message: str | None = None) -> None:
    """Raise unless the SSE event code means success.

    360 appends a trailing usage event whose code is "" (empty) — that is a
    success terminator, not an error (fix parity with qihu_tts_client.py:113).
    """
    if code is None:
        return
    if str(code).strip() in ("", "0"):
        return
    raise RuntimeError(f"360 TTS error (code={code}): {message or 'Unknown error'}")


def _split_text(text: str, max_chars: int = MAX_CHUNK_CHARS) -> list[str]:
    chunks: list[str] = []
    remaining = text.strip()
    while len(remaining) > max_chars:
        cut = max(remaining.rfind("。", 0, max_chars), remaining.rfind("\n", 0, max_chars), remaining.rfind(" ", 0, max_chars))
        if cut <= 0:
            cut = max_chars
        chunks.append(remaining[: cut + 1].strip())
        remaining = remaining[cut + 1 :].strip()
    if remaining:
        chunks.append(remaining)
    return [c for c in chunks if c]


def _handle_sse_event(data_str: str, audio_chunks: list[bytes]) -> None:
    """Parse one SSE event; append the complete-WAV chunk it carries, if any.

    Mirrors qihu_tts_client._handle_sse_event: audio lives at the NESTED path
    event["data"]["content"] as base64; each audio event is a complete WAV.
    """
    try:
        event = json.loads(data_str)
    except json.JSONDecodeError:
        logger.warning("Failed to parse 360 TTS event JSON: %s", data_str[:200])
        return
    _raise_for_event_code(event.get("code"), event.get("message"))
    data = event.get("data") or {}
    content = data.get("content", "") if isinstance(data, dict) else ""
    if content:
        audio_chunks.append(base64.b64decode(content))


def _synthesize_sse(text: str, voice_id: int, api_key: str) -> list[bytes]:
    """POST one 360 stream_speech SSE stream; return the WAV chunks it emits.

    Payload mirrors qihu_tts_client.py:37-42 EXACTLY — field names are
    model/input/voice/response_format (NOT voice/text).
    """
    payload = {
        "model": "360/tts-stream",
        "input": text,
        "voice": voice_id,
        "response_format": "base64",
    }
    req = Request(
        STREAM_SPEECH_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    audio_chunks: list[bytes] = []
    current_data_lines: list[str] = []
    try:
        resp = urlopen(req, timeout=120)
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:500]
        raise RuntimeError(f"360 TTS HTTP {e.code}: {detail}") from e
    with resp:
        for raw_line in resp:
            line = raw_line.decode("utf-8", "replace").strip()
            if line.startswith("data:"):
                data_content = line[len("data:") :].strip()
                if data_content == "[DONE]":
                    break
                current_data_lines.append(data_content)
            elif line == "" and current_data_lines:
                # blank line = event boundary; supports multi-line data: fields
                _handle_sse_event("\n".join(current_data_lines), audio_chunks)
                current_data_lines.clear()
        if current_data_lines:
            _handle_sse_event("\n".join(current_data_lines), audio_chunks)
    return audio_chunks


def _merge_wav_chunks(chunks: list[bytes]) -> bytes:
    """Merge standalone WAV files into one (mirrors qihu_tts_client._merge_wav_chunks).

    Each 360 SSE audio event is a complete WAV with its own RIFF header; naive
    concat corrupts the file. Extract PCM frames and write one WAV. Invalid
    chunks are skipped; if none parse, fall back to raw concatenation.
    """
    if len(chunks) == 1:
        return chunks[0]
    all_frames = b""
    params = None
    for chunk in chunks:
        try:
            with wave.open(BytesIO(chunk), "rb") as wf:
                if params is None:
                    params = wf.getparams()
                all_frames += wf.readframes(wf.getnframes())
        except Exception:
            logger.warning("Skipping invalid WAV chunk (%d bytes)", len(chunk))
    if params is None or not all_frames:
        return b"".join(chunks)
    buf = BytesIO()
    with wave.open(buf, "wb") as out:
        out.setparams(params)
        out.writeframes(all_frames)
    return buf.getvalue()


def generate(text: str, output_path: Path, *, api_key: str, voice_id: str) -> None:
    """Synthesize narration to output_path (WAV). Called by notes_to_audio.py
    with the AudioBackend fields (api_key + voice_id)."""
    vid = resolve_voice_id(voice_id)
    audio_chunks: list[bytes] = []
    for piece in _split_text(text):
        audio_chunks.extend(_synthesize_sse(piece, vid, api_key))
    if not audio_chunks:
        raise RuntimeError("360 TTS returned no audio data")
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(_merge_wav_chunks(audio_chunks))
