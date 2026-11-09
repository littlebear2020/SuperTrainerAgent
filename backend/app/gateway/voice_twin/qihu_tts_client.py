"""360 TTS client — SSE streaming synthesis via Qihu (360) TTS API."""

from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import wave
from typing import Any

import httpx

from app.gateway.voice_twin._retry import tts_sse_timeout

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.360.cn/v1/audio/stream_speech"


class QihuTTSHTTPError(RuntimeError):
    """360 TTS 结构化错误（2026-08-09 D5，对齐 DoubaoTTSHTTPError）。

    - HTTP 层错误：``status_code`` 有值（429/5xx 可重试可降级，4xx 不可）；
    - SSE 事件层错误（非零 code）：``status_code=None``，``code`` 携带业务码。
    """

    def __init__(self, status_code: int | None, detail: str, code: object = None) -> None:
        self.status_code = status_code
        self.code = code
        prefix = f"360 TTS HTTP {status_code}" if status_code is not None else f"360 TTS error (code={code})"
        super().__init__(f"{prefix}: {detail}")


def _build_payload(text: str, voice: int, response_format: str, speed: float | None) -> dict[str, Any]:
    """360 TTS request payload.

    ``speed``（OpenAI 兼容 0.25–4.0）只在 ≠1.0 时下发——360 是否支持该字段未经实测；
    不支持时部署侧设 QIHU_TTS_SPEED_ATEMPO=1 走 ffmpeg 后处理（见 orchestration/tts.py）。
    """
    payload: dict[str, Any] = {
        "model": "360/tts-stream",
        "input": text,
        "voice": voice,
        "response_format": response_format,
    }
    if speed is not None and abs(speed - 1.0) > 1e-6:
        payload["speed"] = min(max(round(speed, 2), 0.25), 4.0)
    return payload


class QihuTTSClient:
    """Client for 360 TTS streaming API."""

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    async def synthesize_sse(
        self,
        text: str,
        voice: int,
        response_format: str = "base64",
        speed: float | None = None,
    ) -> bytes:
        """Synthesize text via 360 TTS SSE streaming API.

        Returns merged audio bytes (single valid WAV file).
        Each SSE chunk from 360 is a complete WAV file; we merge the PCM data
        into one WAV file with correct headers so ffprobe/ffmpeg read the right duration.
        """
        payload = _build_payload(text, voice, response_format, speed)
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
        }

        audio_chunks: list[bytes] = []
        _tts_status = "error"

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                async with client.stream(
                    "POST",
                    _BASE_URL,
                    json=payload,
                    headers=headers,
                ) as response:
                    if response.is_error:
                        body = await response.aread()
                        detail = body.decode(errors="replace")[:500]
                        logger.error(
                            "360 TTS HTTP %s: %s",
                            response.status_code,
                            detail,
                        )
                        raise QihuTTSHTTPError(response.status_code, detail)

                    # T1: 整调用超时（对齐 edge C4 / doubao）——SSE 停滞/慢滴时
                    # httpx 字节间 read 超时永不触发；wait_for 给调用级上限，
                    # 超时抛 asyncio.TimeoutError（可重试 → tenacity 3 次 → edge 降级链）。
                    await asyncio.wait_for(
                        self._consume_sse_stream(response, audio_chunks),
                        timeout=tts_sse_timeout(),
                    )

            if not audio_chunks:
                raise RuntimeError("360 TTS returned no audio data")

            merged = _merge_wav_chunks(audio_chunks)

            logger.info(
                "360 TTS synthesis complete: voice=%d, %d chunks, %d bytes -> %d bytes merged",
                voice,
                len(audio_chunks),
                sum(len(c) for c in audio_chunks),
                len(merged),
            )
            _tts_status = "success"
            return merged
        finally:
            try:
                from app.gateway._shared.llm_usage_sink import record_external_api_call

                await record_external_api_call(
                    provider="qihu_tts",
                    method="synthesize_sse",
                    status=_tts_status,
                    extra={"model": f"preset:{voice}", "char_count": len(text)},
                )
            except Exception:
                pass

    async def _consume_sse_stream(self, response, audio_chunks: list[bytes]) -> None:
        """Consume the 360 SSE stream: parse data lines into audio chunks.

        Extracted from ``synthesize_sse`` so the whole consumption loop can be
        wrapped in ``asyncio.wait_for`` (T1 whole-call timeout).
        """
        current_data_lines: list[str] = []

        async for raw_line in response.aiter_lines():
            line = raw_line.strip()

            if line.startswith("data:"):
                data_content = line[len("data:") :].strip()
                if data_content == "[DONE]":
                    break
                current_data_lines.append(data_content)
            elif line == "" and current_data_lines:
                full_data = "\n".join(current_data_lines)
                current_data_lines.clear()
                self._handle_sse_event(full_data, audio_chunks)

        # Handle remaining buffered data
        if current_data_lines:
            full_data = "\n".join(current_data_lines)
            self._handle_sse_event(full_data, audio_chunks)

    def _handle_sse_event(self, data_str: str, audio_chunks: list[bytes]) -> None:
        """Parse a single SSE event and append audio data if present."""
        try:
            event_json = json.loads(data_str)
        except json.JSONDecodeError:
            logger.warning("Failed to parse 360 TTS event JSON: %s", data_str[:200])
            return

        code = event_json.get("code")
        # Treat empty/whitespace code the same as absent or "0": 360 emits a
        # trailing usage/billing event with code="" that carries no error and no
        # audio. Only a non-empty, non-"0" code is a real error.
        if code is not None and str(code).strip() not in ("", "0"):
            msg = event_json.get("message", "Unknown error")
            raise QihuTTSHTTPError(None, msg, code=code)

        data = event_json.get("data") or {}
        content = data.get("content", "") if isinstance(data, dict) else ""
        if content:
            audio_chunks.append(base64.b64decode(content))


def _merge_wav_chunks(chunks: list[bytes]) -> bytes:
    """Merge multiple standalone WAV files into a single WAV.

    Each chunk from 360 TTS is a complete WAV file with its own RIFF header.
    Naively concatenating them creates a corrupt file. Instead, extract PCM
    frames from each chunk and write a single WAV with correct headers.
    """
    if len(chunks) == 1:
        return chunks[0]

    all_frames = b""
    params = None

    for chunk in chunks:
        try:
            with wave.open(io.BytesIO(chunk), "rb") as wf:
                if params is None:
                    params = wf.getparams()
                all_frames += wf.readframes(wf.getnframes())
        except Exception:
            # If a chunk isn't valid WAV, skip it
            logger.warning("Skipping invalid WAV chunk (%d bytes)", len(chunk))

    if params is None or not all_frames:
        # Fallback: return raw concatenation (best effort)
        return b"".join(chunks)

    buf = io.BytesIO()
    with wave.open(buf, "wb") as out:
        out.setparams(params)
        out.writeframes(all_frames)

    return buf.getvalue()
