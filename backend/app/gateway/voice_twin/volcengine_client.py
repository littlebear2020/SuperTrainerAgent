"""HTTP client for Volcengine Mega TTS API (V3)."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import uuid
from typing import Any

import httpx

from app.gateway.voice_twin._retry import volcengine_sse_timeout
from app.gateway.voice_twin.schemas import get_resource_id

logger = logging.getLogger(__name__)


class VolcengineTTSHTTPError(RuntimeError):
    """Volcengine TTS 结构化错误（2026-08-09 D5，对齐 DoubaoTTSHTTPError）。

    - HTTP 层错误：``status_code`` 有值（429/5xx 可重试可降级，4xx 不可）；
    - SSE 事件层错误（153 SessionFailed / 352 非零 code / 152 非 20000000）：
      ``status_code=None``，``code`` 携带火山业务码——无 HTTP 语义，默认不重试，
      调用方可按 ``code`` 自行判断（如 55000000 speaker 不匹配是配置错）。
    """

    def __init__(self, status_code: int | None, detail: str, code: object = None) -> None:
        self.status_code = status_code
        self.code = code
        prefix = f"Volcengine TTS HTTP {status_code}" if status_code is not None else f"Volcengine TTS error (code={code})"
        super().__init__(f"{prefix}: {detail}")


class VolcengineTTSClient:
    """Async client for Volcengine voice clone and TTS APIs.

    When ``access_key`` is configured (new console API Key):
        - Training: ``POST /api/v3/tts/voice_clone`` with ``X-Api-Key`` auth
        - Status:   ``POST /api/v3/tts/get_voice``  with ``X-Api-Key`` auth
        - TTS:      ``POST /api/v3/tts/unidirectional/sse`` with V3 auth headers

    When ``access_key`` is NOT configured (V1 legacy fallback):
        - Training: ``POST /api/v1/mega_tts/audio/upload`` with Bearer auth
        - Status:   ``POST /api/v1/mega_tts/status``       with Bearer auth
        - TTS:      V1 body on V3 SSE endpoint

    Supports both pre-paid and post-paid voice clone training:
        - Pre-paid:  speaker_id from console (S_xxx, ICL_xxx, etc.)
        - Post-paid: auto-generated custom_speaker_id (usr_xxx)
    """

    # Reserved prefixes that indicate a pre-paid / console-created speaker_id.
    # Must NOT be used for custom_speaker_id (per V3 regex rules).
    _PREPAID_PREFIXES: tuple[str, ...] = ("S_", "ICL_", "MIX_", "DiT_", "BV_")

    def __init__(
        self,
        appid: str,
        token: str,
        access_key: str = "",
        domain: str = "https://openspeech.bytedance.com",
        status_query_timeout: float = 30.0,
    ) -> None:
        self.appid = appid
        self.token = token
        self.access_key = access_key
        self.domain = domain
        # D8b（2026-08-09）：状态查询超时 10s→30s 默认可配——训练状态接口在高峰
        # 期偶发 >10s，曾把慢响应误判为轮询失败。
        self.status_query_timeout = status_query_timeout

    # ------------------------------------------------------------------
    # Auth helpers
    # ------------------------------------------------------------------

    def _v3_training_headers(self) -> dict[str, str]:
        """Headers for V3 training / status endpoints.

        New console auth: only ``X-Api-Key`` + ``X-Api-Request-Id``.
        Old console would use ``X-Api-App-Key`` + ``X-Api-Access-Key``.
        """
        return {
            "Content-Type": "application/json",
            "X-Api-Key": self.access_key,
            "X-Api-Request-Id": str(uuid.uuid4()),
        }

    def _v3_tts_headers(self, resource_id: str) -> dict[str, str]:
        """Headers for V3 TTS synthesis endpoint.

        New console: ``X-Api-Key`` + ``X-Api-Resource-Id``.
        """
        return {
            "X-Api-Key": self.access_key,
            "X-Api-Resource-Id": resource_id,
            "X-Api-Request-Id": str(uuid.uuid4()),
            "Content-Type": "application/json",
        }

    def _v1_headers(self, resource_id: str) -> dict[str, str]:
        """Headers for V1 endpoints (upload / status)."""
        return {
            "Authorization": f"Bearer;{self.token}",
            "Resource-Id": resource_id,
            "Content-Type": "application/json",
        }

    # ------------------------------------------------------------------
    # Upload / Training
    # ------------------------------------------------------------------

    async def upload_audio(
        self,
        speaker_id: str,
        audio_bytes: bytes,
        audio_format: str = "wav",
        model_type: int = 5,
        language: int = 0,
        extra_params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Upload audio and submit voice clone training task.

        Uses V3 ``/api/v3/tts/voice_clone`` when ``access_key`` is configured,
        otherwise falls back to V1 ``/api/v1/mega_tts/audio/upload``.

        Returns a **normalised** dict so the caller can treat both versions
        the same way::

            {"speaker_id": str, "status": int, "demo_audio": str | None}
        """
        if self.access_key:
            return await self._upload_v3(speaker_id, audio_bytes, audio_format, language, extra_params)
        return await self._upload_v1(speaker_id, audio_bytes, audio_format, model_type, language, extra_params)

    async def _upload_v3(
        self,
        speaker_id: str,
        audio_bytes: bytes,
        audio_format: str,
        language: int,
        extra_params: dict[str, Any] | None,
    ) -> dict[str, Any]:
        # Detect pre-paid vs post-paid mode.
        # Pre-paid:  speaker_id starts with a reserved prefix (S_, ICL_, etc.) —
        #            use speaker_id directly as the console-created voice ID.
        # Post-paid: auto-generated custom_speaker_id (usr_xxx) —
        #            use "custom_speaker_id" placeholder with custom_speaker_id field.
        is_prepaid = speaker_id.startswith(self._PREPAID_PREFIXES)

        payload: dict[str, Any] = {
            "speaker_id": speaker_id if is_prepaid else "custom_speaker_id",
            "audio": {
                "data": base64.b64encode(audio_bytes).decode("utf-8"),
                "format": audio_format,
            },
            "language": language,
        }
        if not is_prepaid:
            payload["custom_speaker_id"] = speaker_id
        if extra_params:
            payload["extra_params"] = extra_params

        _upload_status = "error"
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(
                    f"{self.domain}/api/v3/tts/voice_clone",
                    headers=self._v3_training_headers(),
                    json=payload,
                )
                if resp.is_error:
                    body = resp.text[:1000]
                    logger.error("V3 voice_clone failed: HTTP %s — %s", resp.status_code, body)
                resp.raise_for_status()
                data = resp.json()

            logger.info("V3 voice_clone response: %s", json.dumps(data, ensure_ascii=False)[:500])
            _upload_status = "success"

            # Normalise to common format
            speaker_status = data.get("speaker_status", [])
            demo_audio = speaker_status[0].get("demo_audio", "") if speaker_status else None
            return {
                "speaker_id": data.get("speaker_id", speaker_id),
                "status": data.get("status", 0),
                "demo_audio": demo_audio,
                "_v3_raw": data,
            }
        finally:
            try:
                from app.gateway._shared.llm_usage_sink import record_external_api_call

                # V3 path always uses seed-icl-2.0; V1 path (_upload_v1, model_type 1-5)
                # is not tracked here — pre-existing gap, harmless since voice_clone_upload
                # is in FREE_METHODS (excluded from cost estimation).
                await record_external_api_call(provider="volcengine", method="voice_clone_upload", status=_upload_status, extra={"model": "seed-icl-2.0"})
            except Exception:
                pass

    async def _upload_v1(
        self,
        speaker_id: str,
        audio_bytes: bytes,
        audio_format: str,
        model_type: int,
        language: int,
        extra_params: dict[str, Any] | None,
    ) -> dict[str, Any]:
        resource_id = get_resource_id(model_type)

        default_extras: dict[str, Any] = {
            "enable_audio_denoise": True if model_type < 4 else False,
            "enable_crop_by_asr": True,
            "enable_check_audio_quality": True,
        }
        if extra_params:
            default_extras.update(extra_params)

        payload = {
            "speaker_id": speaker_id,
            "appid": self.appid,
            "audios": [
                {
                    "audio_bytes": base64.b64encode(audio_bytes).decode("utf-8"),
                    "audio_format": audio_format,
                }
            ],
            "source": 2,
            "language": language,
            "model_type": model_type,
            "extra_params": json.dumps(default_extras),
        }

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{self.domain}/api/v1/mega_tts/audio/upload",
                headers=self._v1_headers(resource_id),
                json=payload,
            )
            resp.raise_for_status()
            return resp.json()

    # ------------------------------------------------------------------
    # Status query
    # ------------------------------------------------------------------

    async def get_status(self, speaker_id: str, model_type: int = 5) -> dict[str, Any]:
        """Query voice clone training status.

        Uses V3 ``/api/v3/tts/get_voice`` when ``access_key`` is configured,
        otherwise falls back to V1 ``/api/v1/mega_tts/status``.

        Returns a **normalised** dict::

            {"speaker_id": str, "status": int, "demo_audio": str | None}
        """
        if self.access_key:
            return await self._get_status_v3(speaker_id)
        return await self._get_status_v1(speaker_id, model_type)

    async def _get_status_v3(self, speaker_id: str) -> dict[str, Any]:
        is_prepaid = speaker_id.startswith(self._PREPAID_PREFIXES)
        body: dict[str, Any] = {"speaker_id": speaker_id} if is_prepaid else {"speaker_id": "custom_speaker_id", "custom_speaker_id": speaker_id}
        async with httpx.AsyncClient(timeout=self.status_query_timeout) as client:
            resp = await client.post(
                f"{self.domain}/api/v3/tts/get_voice",
                headers=self._v3_training_headers(),
                json=body,
            )
            resp.raise_for_status()
            data = resp.json()

        speaker_status = data.get("speaker_status", [])
        demo_audio = speaker_status[0].get("demo_audio", "") if speaker_status else None
        return {
            "speaker_id": data.get("speaker_id", speaker_id),
            "status": data.get("status", 0),
            "demo_audio": demo_audio,
            "_v3_raw": data,
        }

    async def _get_status_v1(self, speaker_id: str, model_type: int) -> dict[str, Any]:
        resource_id = get_resource_id(model_type)
        payload = {"appid": self.appid, "speaker_id": speaker_id}

        async with httpx.AsyncClient(timeout=self.status_query_timeout) as client:
            resp = await client.post(
                f"{self.domain}/api/v1/mega_tts/status",
                headers=self._v1_headers(resource_id),
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
        base = data.get("BaseResp") or {}
        status_code = base.get("StatusCode")
        if status_code not in (None, 0):
            # API 级错误（鉴权/参数/speaker 无效）无 status 字段——曾缺省 0 → "uploading" 永卡
            return {"speaker_id": speaker_id, "status": -1, "_error": base.get("StatusMessage") or f"StatusCode={status_code}"}
        return data

    # ------------------------------------------------------------------
    # TTS Synthesis (SSE)
    # ------------------------------------------------------------------

    _LANGUAGE_MAP: dict[int, str] = {0: "zh", 1: "en", 2: "ja", 3: "es", 4: "id", 5: "pt", 6: "de", 7: "fr"}

    async def synthesize_sse(
        self,
        speaker_id: str,
        text: str,
        model_type: int = 5,
        output_format: str = "mp3",
        speed_ratio: float = 1.0,
        language: int = 0,
    ) -> bytes:
        """Stream TTS synthesis via V3 SSE and return concatenated audio bytes.

        V3 SSE event types::

            event: 352  → TTSResponse  (code 0 = success, data = base64 audio)
            event: 351  → TTSSentenceEnd
            event: 152  → SessionFinish (code 20000000 = success)
            event: 153  → SessionFailed

        Falls back to V1 body format when ``access_key`` is not set.
        """
        resource_id = get_resource_id(model_type)

        if self.access_key:
            explicit_lang = self._LANGUAGE_MAP.get(language, "zh")
            payload: dict[str, Any] = {
                "user": {"uid": "deerflow"},
                "req_params": {
                    "text": text,
                    "speaker": speaker_id,
                    "speed_ratio": speed_ratio,
                    "audio_params": {
                        "format": output_format,
                        "sample_rate": 24000,
                    },
                    "additions": json.dumps({"explicit_language": explicit_lang}),
                },
            }
            headers = self._v3_tts_headers(resource_id)
        else:
            payload = {
                "app": {
                    "appid": self.appid,
                    "token": self.token,
                    "cluster": "volcano_icl",
                },
                "user": {"uid": "default"},
                "audio": {
                    "voice_type": speaker_id,
                    "encoding": output_format,
                    "speed_ratio": speed_ratio,
                    "volume_ratio": 1.0,
                    "pitch_ratio": 1.0,
                },
                "request": {
                    "reqid": str(uuid.uuid4()),
                    "text": text,
                    "text_type": "plain",
                    "operation": "query",
                },
            }
            headers = self._v1_headers(resource_id)

        audio_chunks: list[bytes] = []
        _tts_status = "error"  # default to error; set to success on clean return

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                async with client.stream(
                    "POST",
                    f"{self.domain}/api/v3/tts/unidirectional/sse",
                    headers=headers,
                    json=payload,
                ) as response:
                    if response.is_error:
                        # Read the error body before raising so the user sees the
                        # real reason (e.g. "invalid speaker") instead of httpx's
                        # truncated "Client error '400' for url 'https...'".
                        body = await response.aread()
                        detail = body.decode(errors="replace")[:500]
                        logger.error(
                            "Volcengine TTS HTTP %s: %s",
                            response.status_code,
                            detail,
                        )
                        raise VolcengineTTSHTTPError(response.status_code, detail)

                    # T1 (对齐 doubao): SSE 整调用超时——httpx 标量 timeout 的
                    # read 语义是"等下一块数据"的间隔:完全停滞(零字节)会触发
                    # read timeout;慢滴(间隔<阈值、断续有字节)不触发,由 wait_for
                    # 兜底整调用上限。超时/ReadTimeout 均 ∈ TRANSPORT_TTS_ERRORS
                    # → 自动重试 + 降级链。
                    await asyncio.wait_for(
                        self._consume_sse_stream(response, audio_chunks),
                        timeout=volcengine_sse_timeout(),
                    )

            if not audio_chunks:
                raise RuntimeError("Volcengine TTS returned no audio data")

            logger.info(
                "SSE synthesis complete: %d chunks, %d bytes",
                len(audio_chunks),
                sum(len(c) for c in audio_chunks),
            )
            _tts_status = "success"
            return b"".join(audio_chunks)
        finally:
            # F1: track the synthesis call (success AND failure) — same
            # try/finally pattern as doubao/qihu clients.
            try:
                from app.gateway._shared.llm_usage_sink import record_external_api_call

                await record_external_api_call(
                    provider="volcengine",
                    method="synthesize_sse",
                    status=_tts_status,
                    extra={"model": f"volcengine-model-{model_type}", "char_count": len(text)},
                )
            except Exception:
                pass

    async def _consume_sse_stream(self, response: httpx.Response, audio_chunks: list[bytes]) -> None:
        """Consume the V3 SSE stream: parse event/data lines into audio chunks.

        Extracted from ``synthesize_sse`` so the whole consumption loop can be
        wrapped in ``asyncio.wait_for`` (T1 whole-call timeout,对齐 doubao)。
        """
        current_event: str | None = None
        current_data_lines: list[str] = []
        async for raw_line in response.aiter_lines():
            line = raw_line.strip()

            if line.startswith("event:"):
                current_event = line[len("event:") :].strip()
            elif line.startswith("data:"):
                data_content = line[len("data:") :].strip()
                current_data_lines.append(data_content)
            elif line == "" and current_data_lines:
                full_data = "\n".join(current_data_lines)
                current_data_lines.clear()
                try:
                    event_json = json.loads(full_data)
                except json.JSONDecodeError:
                    logger.warning("Failed to parse SSE event JSON: %s", full_data[:200])
                    continue

                self._handle_sse_event(
                    current_event,
                    event_json,
                    audio_chunks,
                )
                current_event = None

        # Handle any remaining buffered data
        if current_data_lines:
            full_data = "\n".join(current_data_lines)
            try:
                event_json = json.loads(full_data)
                self._handle_sse_event(
                    current_event,
                    event_json,
                    audio_chunks,
                )
            except json.JSONDecodeError:
                pass

    def _handle_sse_event(
        self,
        event_type: str | None,
        event_json: dict[str, Any],
        audio_chunks: list[bytes],
    ) -> None:
        """Process a single parsed SSE event and append audio if present."""
        code = event_json.get("code")
        msg = event_json.get("message", "Unknown error")
        # 归一化：字符串 "0"/缺失/空白与数字等价（qihu 空 code 误判同类防御，2026-07-22 审计）
        code_norm = None if code is None else str(code).strip()

        if event_type == "153":
            # SessionFailed — hard error
            raise VolcengineTTSHTTPError(None, f"session failed: {msg}", code=code)

        if event_type == "352":
            # TTSResponse — audio data chunk
            if code_norm not in (None, "", "0"):
                raise VolcengineTTSHTTPError(None, msg, code=code)
            audio_b64 = event_json.get("data") or ""
            if audio_b64:
                audio_chunks.append(base64.b64decode(audio_b64))

        elif event_type == "152":
            # SessionFinish
            if code_norm not in (None, "", "20000000"):
                raise VolcengineTTSHTTPError(None, f"session error: {msg}", code=code)

        elif event_type is None:
            # V1 format — no event line, just data
            if code_norm not in (None, "", "0"):
                raise VolcengineTTSHTTPError(None, msg, code=code)
            audio_b64 = event_json.get("data") or ""
            if audio_b64:
                audio_chunks.append(base64.b64decode(audio_b64))
