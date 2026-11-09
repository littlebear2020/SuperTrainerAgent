"""ByteDance doubao seed-tts-2.0 TTS client (Stage 2a).

Slim copy of ``volcengine_client.VolcengineTTSClient.synthesize_sse`` — the openspeech
V3 SSE path only (no V1 fallback, no voice-clone training). Same ``X-Api-Key`` +
``X-Api-Resource-Id`` auth, same ``req_params`` body, same SSE event parsing
(352=audio / 152=finish / 153=fail) as voice_clone; only differences:

- ``resource_id`` is fixed to ``seed-tts-2.0`` (voice_clone uses ``seed-icl-2.0``).
- The speaker is a seed-tts-2.0 console voice id (passed in from the ``doubao:``
  voice-id prefix), not a cloned speaker id.

Auth reuses ``VoiceCloneConfig.access_key`` (single ``X-Api-Key`` header, same openspeech
account as voice clone). See backend/docs/BYTEDANCE_MODELS_INTEGRATION_PLAN.md §阶段 2a
+ §0.2.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import uuid
from typing import Any

import httpx

from app.gateway.voice_twin._retry import tts_sse_timeout

logger = logging.getLogger(__name__)


class DoubaoTTSHTTPError(RuntimeError):
    """HTTP error from doubao TTS, carrying the status code so the caller can
    classify infra vs auth/config (requirement #4: only 429/5xx/timeout/conn degrade).

    - 5xx / 429 → infrastructure → caller falls back to 360/edge.
    - 4xx (auth/config) → non-infra → caller surfaces to the user (no fallback).
    """

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        super().__init__(f"doubao TTS HTTP {status_code}: {detail}")


class DoubaoTTSClient:
    """Async client for ByteDance openspeech seed-tts-2.0 synthesis (V3 SSE)."""

    RESOURCE_ID = "seed-tts-2.0"
    DOMAIN = "https://openspeech.bytedance.com"
    ENDPOINT = "/api/v3/tts/unidirectional/sse"

    def __init__(self, access_key: str, domain: str = DOMAIN) -> None:
        self.access_key = access_key
        self.domain = domain

    # ------------------------------------------------------------------
    # Request construction (unit-tested without httpx)
    # ------------------------------------------------------------------

    def _v3_tts_headers(self) -> dict[str, str]:
        """V3 TTS headers: ``X-Api-Key`` + ``X-Api-Resource-Id`` + request id."""
        return {
            "X-Api-Key": self.access_key,
            "X-Api-Resource-Id": self.RESOURCE_ID,
            "X-Api-Request-Id": str(uuid.uuid4()),
            "Content-Type": "application/json",
        }

    def _build_payload(
        self,
        speaker: str,
        text: str,
        speed_ratio: float = 1.0,
        output_format: str = "mp3",
        language: str = "zh",
        *,
        enable_subtitle: bool = False,
        section_id: str | None = None,
        emotion: str | None = None,
    ) -> dict[str, Any]:
        """Openspeech V3 ``req_params`` body (identical shape to voice_clone).

        T10（2026-09-08 remotion-course 计划）：``enable_subtitle=True`` 开启 event 364
        TTSSubtitle（sentence.words[] 字级时间戳，session 相对秒）——注意
        additions.cache_config 命中缓存的响应**不带字幕事件**，调用方须容忍空表。
        T11：``section_id`` 进 additions（同课程任务多轮上下文关联，语气一致）。
        T5（2026-10-14 配音修复批）：``emotion`` 进 ``audio_params.emotion``
        （seed-tts-2.0 情感参数，官方口径仅部分多情感音色生效且枚举随音色不同；
        只下发 2026-10-14 探针验证过的值，见 orchestration/tts.py 的映射表与
        scripts/probe_doubao_tts_emotion_payload.py）。
        """
        audio_params: dict[str, Any] = {
            "format": output_format,
            "sample_rate": 24000,
        }
        if enable_subtitle:
            audio_params["enable_subtitle"] = True
        if emotion:
            audio_params["emotion"] = emotion
        additions: dict[str, Any] = {"explicit_language": language}
        if section_id:
            additions["section_id"] = section_id
        return {
            "user": {"uid": "deerflow"},
            "req_params": {
                "text": text,
                "speaker": speaker,
                "speed_ratio": speed_ratio,
                "audio_params": audio_params,
                "additions": json.dumps(additions),
            },
        }

    # ------------------------------------------------------------------
    # SSE event parsing (352 audio / 152 finish / 153 fail)
    # ------------------------------------------------------------------

    def _handle_sse_event(
        self,
        event_type: str | None,
        event_json: dict[str, Any],
        audio_chunks: list[bytes],
        words: list[dict] | None = None,
    ) -> None:
        """Process one parsed SSE event; append audio/words or raise on error.

        - 352 TTSResponse: code 0 → ``data`` is base64 audio; non-zero → error.
        - 364 TTSSubtitle: ``sentence.words[]`` 字级时间戳（enable_subtitle=true 时
          逐句下发；T10）。``words`` 收集器为 None 时忽略（既有调用零变化）。
        - 152 SessionFinish: code 20000000 → ok; otherwise → error.
        - 153 SessionFailed: hard error.
        """
        if event_type == "364" and words is not None:
            sentence = event_json.get("sentence") or event_json.get("data") or {}
            for w in sentence.get("words") or []:
                if isinstance(w, dict) and w.get("word"):
                    words.append(
                        {
                            "word": str(w.get("word")),
                            "startTime": float(w.get("startTime", 0.0)),
                            "endTime": float(w.get("endTime", 0.0)),
                            "confidence": float(w.get("confidence", 0.0)),
                        }
                    )
            return
        code = event_json.get("code")
        msg = event_json.get("message", "Unknown error")
        # 归一化：字符串 "0"/缺失/空白与数字等价（qihu 空 code 误判同类防御，2026-07-22 审计）
        code_norm = None if code is None else str(code).strip()

        if event_type == "153":
            raise RuntimeError(f"doubao TTS session failed (code={code}): {msg}")

        if event_type == "352":
            if code_norm not in (None, "", "0"):
                raise RuntimeError(f"doubao TTS error (code={code}): {msg}")
            audio_b64 = event_json.get("data") or ""
            if audio_b64:
                audio_chunks.append(base64.b64decode(audio_b64))

        elif event_type == "152":
            if code_norm not in (None, "", "20000000"):
                raise RuntimeError(f"doubao TTS session error (code={code}): {msg}")

    async def _consume_sse_stream(self, response, audio_chunks: list[bytes], words: list[dict] | None = None) -> None:
        """Consume the V3 SSE stream: parse event/data lines into audio chunks.

        Extracted from ``synthesize_sse`` so the whole consumption loop can be
        wrapped in ``asyncio.wait_for`` (T1 whole-call timeout). T10: ``words``
        收集器透传给 event 364 解析（None = 不收集，既有行为不变）。
        """
        current_event: str | None = None
        current_data_lines: list[str] = []
        async for raw_line in response.aiter_lines():
            line = raw_line.strip()

            if line.startswith("event:"):
                current_event = line[len("event:") :].strip()
            elif line.startswith("data:"):
                current_data_lines.append(line[len("data:") :].strip())
            elif line == "" and current_data_lines:
                full_data = "\n".join(current_data_lines)
                current_data_lines.clear()
                try:
                    event_json = json.loads(full_data)
                except json.JSONDecodeError:
                    logger.warning("Failed to parse doubao SSE event JSON: %s", full_data[:200])
                    continue
                self._handle_sse_event(current_event, event_json, audio_chunks, words)
                current_event = None

        if current_data_lines:
            full_data = "\n".join(current_data_lines)
            try:
                event_json = json.loads(full_data)
                self._handle_sse_event(current_event, event_json, audio_chunks, words)
            except json.JSONDecodeError:
                pass

    async def synthesize_sse_with_timestamps(
        self,
        speaker: str,
        text: str,
        output_format: str = "mp3",
        speed_ratio: float = 1.0,
        language: str = "zh",
        *,
        section_id: str | None = None,
        emotion: str | None = None,
    ) -> tuple[bytes, list[dict]]:
        """T10：开 enable_subtitle 的合成，返回 (audio_bytes, words)。

        words = 全部 event 364 的 sentence.words[] 拍平（到达序，
        {"word","startTime","endTime","confidence"}，session 相对秒）。
        空表 = 服务未下发字幕事件（cache_config 缓存命中 / ssml 输入等）——
        调用方须容忍并降级句级，不得回退 edge。
        T5（2026-10-14）：``emotion`` 透传 audio_params（仅探针验证过的枚举）。
        """
        headers = self._v3_tts_headers()
        payload = self._build_payload(speaker, text, speed_ratio, output_format, language, enable_subtitle=True, section_id=section_id, emotion=emotion)
        audio_chunks: list[bytes] = []
        words: list[dict] = []
        _tts_status = "error"
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                async with client.stream(
                    "POST",
                    f"{self.domain}{self.ENDPOINT}",
                    headers=headers,
                    json=payload,
                ) as response:
                    if response.is_error:
                        body = await response.aread()
                        detail = body.decode(errors="replace")[:500]
                        logger.error("doubao TTS HTTP %s: %s", response.status_code, detail)
                        raise DoubaoTTSHTTPError(response.status_code, detail)
                    await asyncio.wait_for(
                        self._consume_sse_stream(response, audio_chunks, words),
                        timeout=tts_sse_timeout(),
                    )
            if not audio_chunks:
                raise RuntimeError("doubao TTS returned no audio data")
            logger.info(
                "doubao SSE synthesis complete: %d chunks, %d bytes, %d subtitle words",
                len(audio_chunks),
                sum(len(c) for c in audio_chunks),
                len(words),
            )
            _tts_status = "success"
            return b"".join(audio_chunks), words
        finally:
            try:
                from app.gateway._shared.llm_usage_sink import record_external_api_call

                await record_external_api_call(
                    provider="doubao_tts",
                    method="synthesize_sse_with_timestamps",
                    status=_tts_status,
                )
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Synthesis
    # ------------------------------------------------------------------

    async def synthesize_sse(
        self,
        speaker: str,
        text: str,
        output_format: str = "mp3",
        speed_ratio: float = 1.0,
        language: str = "zh",
        *,
        section_id: str | None = None,
        emotion: str | None = None,
    ) -> bytes:
        """Stream seed-tts-2.0 synthesis via V3 SSE; return concatenated audio bytes.

        Raises ``RuntimeError`` on HTTP error, SSE session failure, or a non-zero
        event code (e.g. 55000000 "resource ID mismatched with speaker" — a config
        error, not an infrastructure blip). The caller decides whether to retry or
        surface the error to the user.
        T5（2026-10-14）：``emotion`` 透传 audio_params（仅探针验证过的枚举）。
        """
        headers = self._v3_tts_headers()
        payload = self._build_payload(speaker, text, speed_ratio, output_format, language, section_id=section_id, emotion=emotion)

        audio_chunks: list[bytes] = []
        _tts_status = "error"  # default to error; set to success on clean return

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                async with client.stream(
                    "POST",
                    f"{self.domain}{self.ENDPOINT}",
                    headers=headers,
                    json=payload,
                ) as response:
                    if response.is_error:
                        body = await response.aread()
                        detail = body.decode(errors="replace")[:500]
                        logger.error("doubao TTS HTTP %s: %s", response.status_code, detail)
                        raise DoubaoTTSHTTPError(response.status_code, detail)

                    # T1: 整调用超时（对齐 edge C4）——httpx 标量 timeout 的 read 语义是
                    # 字节间间隔，SSE 停滞/慢滴永不触发；wait_for 给调用级上限，
                    # 超时抛 asyncio.TimeoutError（可重试 → tenacity 3 次 → 360 降级链）。
                    await asyncio.wait_for(
                        self._consume_sse_stream(response, audio_chunks),
                        timeout=tts_sse_timeout(),
                    )

            if not audio_chunks:
                raise RuntimeError("doubao TTS returned no audio data")

            logger.info(
                "doubao SSE synthesis complete: %d chunks, %d bytes",
                len(audio_chunks),
                sum(len(c) for c in audio_chunks),
            )
            _tts_status = "success"
            return b"".join(audio_chunks)
        finally:
            try:
                from app.gateway._shared.llm_usage_sink import record_external_api_call

                await record_external_api_call(
                    provider="doubao_tts",
                    method="synthesize_sse",
                    status=_tts_status,
                    extra={"model": "seed-tts-2.0", "char_count": len(text)},
                )
            except Exception:
                pass
