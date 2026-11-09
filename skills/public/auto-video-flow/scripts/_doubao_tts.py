#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Standalone ByteDance doubao seed-tts-2.0 TTS helper (stdlib only).

MIRROR: this file is duplicated across every sandbox skill's scripts/ dir
(glob: skills/**/scripts/_doubao_tts.py — currently 4 copies: auto-video-flow,
corporate-video, tech-explainer-video, course-video(custom)). Keep ALL copies
byte-identical; the guard test backend/tests/test_skill_script_copies.py
asserts md5 equality across the glob and fails the suite on any drift. (Shared
extraction blocked by the skill-sandbox path rules — skills run as independent
processes at /mnt/skills and cannot import app.*; see
tests/test_harness_boundary.py.)

Synchronous stdlib-only mirror of
backend/app/gateway/voice_twin/doubao_tts_client.py (DoubaoTTSClient). Uses
urllib.request + manual SSE line parsing instead of httpx, so it runs in any
sandbox Python without third-party deps.

API contract (verified live in Stage 0 + 2a):
  POST https://openspeech.bytedance.com/api/v3/tts/unidirectional/sse
  Headers:
    X-Api-Key:          <VOLCENGINE_VOICE_ACCESS_KEY>
    X-Api-Resource-Id:  seed-tts-2.0
    X-Api-Request-Id:   <uuid4>
    Content-Type:       application/json
  Body:
    {"user":{"uid":"deerflow"},
     "req_params":{"text":TEXT,"speaker":SPEAKER,"speed_ratio":1.0,
                   "audio_params":{"format":"mp3","sample_rate":24000},
                   "additions":"{\\"explicit_language\\":\\"zh\\"}"}}
  Response: SSE stream.
    event: 352 -> {"code":0,"data":"<base64 mp3>"}  (append decoded audio)
    event: 364 -> TTSSubtitle (one per sub-sentence; only when
                  req_params.audio_params.enable_subtitle=true):
                  {"sentence":{"words":[{"word","startTime","endTime","confidence"}]}}
                  — times are session-relative seconds, aligned to the ORIGINAL text
    event: 152 -> code 20000000 = finish
    event: 153 = fail

Env:
  VOLCENGINE_VOICE_ACCESS_KEY   — required (X-Api-Key).
  DOUBAO_TTS_DEFAULT_SPEAKER    — default speaker id (seed-tts-2.0 console voice).

TTS 红线（沙箱内唯一 TTS 通道）：edge-tts 在沙箱出网隔离下必失败，任何技能
不得回退。synthesize() 失败（env 缺失 / HTTP 错误 / SSE 会话失败 / 无音频 /
重试后仍网络错误）一律抛 RuntimeError——调用方如实报错停止，不要尝试
edge-tts 兜底。

Robustness (2026-09-08 T6): network-level failures (OSError — connection
errors, timeouts, ConnectionResetError — and http.client.HTTPException such as
RemoteDisconnected/IncompleteRead during SSE streaming) and transient
HTTP 429/5xx get ONE retry with exponential backoff; everything surfaces as
RuntimeError per the docstring contract (previously socket.timeout escaped
as-is, breaking callers that only caught RuntimeError).
"""

from __future__ import annotations

import base64
import http.client
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid

_ENDPOINT = "https://openspeech.bytedance.com/api/v3/tts/unidirectional/sse"
_RESOURCE_ID = "seed-tts-2.0"
_TIMEOUT = 120.0
_MAX_ATTEMPTS = 2  # 1 次指数退避重试（网络抖动容忍）
_RETRY_BASE_DELAY = 2.0


def _build_request(text: str, speaker: str, speed_ratio: float, enable_subtitle: bool) -> urllib.request.Request:
    access_key = os.getenv("VOLCENGINE_VOICE_ACCESS_KEY", "").strip()
    headers = {
        "X-Api-Key": access_key,
        "X-Api-Resource-Id": _RESOURCE_ID,
        "X-Api-Request-Id": str(uuid.uuid4()),
        "Content-Type": "application/json",
    }
    audio_params: dict = {"format": "mp3", "sample_rate": 24000}
    if enable_subtitle:
        # 字级时间戳开关（仅 TTS2.0/ICL2.0 生效）——2026-09-08 生产凭证实测：
        # 响应新增 event 364 TTSSubtitle，sentence.words[] 带
        # word/startTime/endTime/confidence（整 session 相对秒、基于原文打轴）。
        # 注意：additions.cache_config 命中缓存的响应不带字幕；ssml/latex 会关闭字幕。
        audio_params["enable_subtitle"] = True
    payload = json.dumps({
        "user": {"uid": "deerflow"},
        "req_params": {
            "text": text,
            "speaker": speaker,
            "speed_ratio": speed_ratio,
            "audio_params": audio_params,
            "additions": json.dumps({"explicit_language": "zh"}),
        },
    }).encode("utf-8")
    return urllib.request.Request(
        _ENDPOINT, data=payload, headers=headers, method="POST"
    )


def _resolve_speaker(speaker: str | None) -> str:
    if not os.getenv("VOLCENGINE_VOICE_ACCESS_KEY", "").strip():
        raise RuntimeError("doubao TTS env VOLCENGINE_VOICE_ACCESS_KEY not set")
    if speaker is None:
        speaker = os.getenv("DOUBAO_TTS_DEFAULT_SPEAKER", "").strip()
    if not speaker:
        raise RuntimeError("doubao TTS env DOUBAO_TTS_DEFAULT_SPEAKER not set")
    return speaker


def synthesize(text: str, speaker: str | None = None, speed_ratio: float = 1.0) -> bytes:
    """Synthesize text via doubao seed-tts-2.0 (SSE streaming). Returns mp3 bytes.

    speaker defaults to DOUBAO_TTS_DEFAULT_SPEAKER env. Raises RuntimeError on
    missing env, HTTP error, SSE session failure, no audio, or network failure
    after retry — the caller must report honestly and stop (edge-tts 必失败，
    任何技能不得回退).
    """
    req = _build_request(text, _resolve_speaker(speaker), speed_ratio, enable_subtitle=False)
    return _request_with_retry(req)


def synthesize_with_timestamps(
    text: str, speaker: str | None = None, speed_ratio: float = 1.0
) -> tuple[bytes, list[dict]]:
    """Like synthesize(), but also returns word-level timestamps.

    Returns (mp3_bytes, words): words is a flat list of
    {"word", "startTime", "endTime", "confidence"} across all event-364
    (TTSSubtitle) messages, in arrival order; times are session-relative
    seconds. Empty list when the service sent no subtitle events (e.g.
    additions.cache_config cache hit, or ssml/latex input) — callers must
    tolerate that and fall back to a coarser timeline (whisper.cpp 对齐或
    ffprobe 句级), NOT to edge-tts.
    """
    words: list[dict] = []
    req = _build_request(text, _resolve_speaker(speaker), speed_ratio, enable_subtitle=True)
    audio = _request_with_retry(req, words=words)
    return audio, words


def _request_with_retry(req: urllib.request.Request, words: list[dict] | None = None) -> bytes:
    """Run the SSE request with 1 exponential-backoff retry.

    Retryable: network-level failures (OSError — URLError / TimeoutError /
    socket.timeout / ConnectionResetError — and http.client.HTTPException such
    as RemoteDisconnected / IncompleteRead during SSE streaming; read-side
    failures surface the same way), and transient HTTP 429/5xx. NOT retryable:
    other HTTP 4xx (request is wrong, retrying won't help) and SSE session
    failures (RuntimeError, app-level).

    words: optional collector for event-364 word timestamps (cleared at each
    attempt start so a retried request never yields duplicated entries).
    """
    delay = _RETRY_BASE_DELAY
    for attempt in range(_MAX_ATTEMPTS):
        try:
            return _synthesize_once(req, words=words)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:500]
            if (exc.code == 429 or exc.code >= 500) and attempt < _MAX_ATTEMPTS - 1:
                time.sleep(delay)
                delay *= 2
                continue
            raise RuntimeError(f"doubao TTS HTTP {exc.code}: {detail}") from exc
        except (OSError, http.client.HTTPException) as exc:
            # OSError 一网打尽 URLError / TimeoutError / socket.timeout /
            # ConnectionResetError；http.client.HTTPException 覆盖读流阶段的
            # RemoteDisconnected / IncompleteRead（非 OSError 子类）。
            if attempt < _MAX_ATTEMPTS - 1:
                time.sleep(delay)
                delay *= 2
                continue
            raise RuntimeError(
                f"doubao TTS connection error after {_MAX_ATTEMPTS} attempts: {exc}"
            ) from exc
    raise RuntimeError("doubao TTS connection error: unreachable state")


def _synthesize_once(req: urllib.request.Request, words: list[dict] | None = None) -> bytes:
    """Single SSE attempt: open stream, collect audio chunks, return mp3 bytes."""
    if words is not None:
        words.clear()  # 重试不产生重复词条
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        audio_chunks: list[bytes] = []
        current_event: str | None = None
        current_data_lines: list[str] = []

        for raw_line in resp:
            line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
            if line.startswith("event:"):
                current_event = line[len("event:"):].strip()
            elif line.startswith("data:"):
                current_data_lines.append(line[len("data:"):].strip())
            elif line == "" and current_data_lines:
                full_data = "\n".join(current_data_lines)
                current_data_lines.clear()
                _handle_sse_event(current_event, full_data, audio_chunks, words)
                current_event = None
        if current_data_lines:
            full_data = "\n".join(current_data_lines)
            _handle_sse_event(current_event, full_data, audio_chunks, words)

    if not audio_chunks:
        raise RuntimeError("doubao TTS returned no audio data")
    return b"".join(audio_chunks)


def _handle_sse_event(
    event_type: str | None,
    data_str: str,
    audio_chunks: list[bytes],
    words: list[dict] | None = None,
) -> None:
    """Process one parsed SSE event; append audio/words or raise on error."""
    try:
        event_json = json.loads(data_str)
    except json.JSONDecodeError:
        return  # skip unparseable event
    code = event_json.get("code", -1)
    msg = event_json.get("message", "Unknown error")

    if event_type == "153":
        raise RuntimeError(f"doubao TTS session failed (code={code}): {msg}")
    if event_type == "352":
        if code != 0:
            raise RuntimeError(f"doubao TTS error (code={code}): {msg}")
        audio_b64 = event_json.get("data", "")
        if audio_b64:
            audio_chunks.append(base64.b64decode(audio_b64))
    elif event_type == "364":
        # TTSSubtitle: {"sentence": {"words": [{word, startTime, endTime, confidence}]}}
        # 字级时间戳（session 相对秒、基于原文）——仅在 enable_subtitle=true 时出现
        if words is not None:
            sentence = event_json.get("sentence") or {}
            for w in sentence.get("words") or []:
                if isinstance(w, dict) and "word" in w:
                    words.append(w)
    elif event_type == "152":
        if code != 20000000:
            raise RuntimeError(f"doubao TTS session error (code={code}): {msg}")


if __name__ == "__main__":
    # Self-check: synthesize a test sentence -> write to tmp mp3 -> print SUCCESS.
    test_text = "你好，这是豆包语音合成测试。"
    try:
        audio = synthesize(test_text)
    except RuntimeError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        sys.exit(1)
    tmp_path = os.path.join(
        os.getenv("TMPDIR", "/tmp"), "_doubao_tts_selfcheck.mp3"
    )
    with open(tmp_path, "wb") as f:
        f.write(audio)
    print(f"SUCCESS: {len(audio)} bytes -> {tmp_path}")
