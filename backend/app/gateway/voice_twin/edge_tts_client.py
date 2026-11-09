"""edge-tts client for the voice-twin module (A2, 2026-08-09).

模仿 video-gen ``orchestration/tts.py:_call_edge_with_retry`` 的策略（⚠️计划 R6：
真正实现是该函数而非薄壳 ``_synthesize_edge``）：tenacity 3 次传输重试 +
``asyncio.wait_for`` 显式超时（防半开 TCP 永挂）。配音场景无字幕对齐需求，
只取音频流，不喂 SubMaker/WordBoundary。

edge-tts 输出为 mp3（默认 audio-24khz-48kbitrate）。请求其他 output_format 时
由调用方（production_service）走 ffmpeg 转码，与 360 WAV 同一 transcode 通道。
"""

from __future__ import annotations

import asyncio
import logging

import aiohttp
import edge_tts
import httpx
import tenacity

logger = logging.getLogger(__name__)

# edge-tts 底层用 aiohttp（communicate.py:461 aiohttp.ClientSession）——连接失败
# 抛 aiohttp.ClientConnectorError 等，必须列入重试条件（fix #2, 2026-08-10）。
# httpx 异常保留是防御性的（万一未来 edge-tts 换回 httpx），但真正生效的是 aiohttp。
_RETRYABLE_EDGE_ERRORS = (
    aiohttp.ClientError,  # edge-tts 底层是 aiohttp（覆盖 ClientConnectorError 等）
    asyncio.TimeoutError,  # asyncio.wait_for 超时
    httpx.RemoteProtocolError,  # 防御性保留
    httpx.ConnectError,
    httpx.ReadError,
    httpx.ReadTimeout,
)


def speed_to_rate(speed: float) -> str:
    """Convert speed multiplier (1.0 = normal) to edge-tts rate string like '+20%'."""
    pct = round((speed - 1.0) * 100)
    sign = "+" if pct >= 0 else "-"
    return f"{sign}{abs(pct)}%"


class EdgeTTSClient:
    """Async client for edge-tts synthesis (audio only, no word-level SRT)."""

    def __init__(self, timeout: float = 60.0) -> None:
        # 默认 60s 与 video-gen TTS_EDGE_TIMEOUT 一致——edge 是兜底链末端，
        # 宁可多等也不 loud-fail。最坏 ~3min（3 次 × 60s + 退避）。
        self.timeout = timeout

    async def synthesize(
        self,
        text: str,
        voice: str,
        speed: float = 1.0,
    ) -> bytes:
        """Synthesize ``text`` with edge voice ``voice`` (bare ShortName, no 'edge:' prefix).

        Returns mp3 bytes. Transport errors / stream timeouts are retried 3×;
        other errors propagate.
        """
        return await self._call_with_retry(text=text, voice=voice, speed=speed)

    @tenacity.retry(
        retry=tenacity.retry_if_exception_type(_RETRYABLE_EDGE_ERRORS),
        wait=tenacity.wait_fixed(2),
        stop=tenacity.stop_after_attempt(3),
        reraise=True,
    )
    async def _call_with_retry(self, *, text: str, voice: str, speed: float) -> bytes:
        rate = speed_to_rate(speed)
        communicate = edge_tts.Communicate(text, voice, rate=rate)

        audio_bytes = bytearray()

        async def _consume_stream() -> None:
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    audio_bytes.extend(chunk["data"])

        await asyncio.wait_for(_consume_stream(), timeout=self.timeout)

        if not audio_bytes:
            raise RuntimeError("edge TTS returned no audio data")

        logger.info("edge TTS synthesis complete: voice=%s, %d bytes", voice, len(audio_bytes))
        return bytes(audio_bytes)
