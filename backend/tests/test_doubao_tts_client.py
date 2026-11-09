"""Tests for the ByteDance doubao seed-tts-2.0 TTS client (Stage 2a).

The client is a slim copy of ``volcengine_client.VolcengineTTSClient.synthesize_sse``
(openspeech V3 SSE path only): same ``X-Api-Key`` + ``X-Api-Resource-Id`` auth, same
``req_params`` body, same SSE event parsing (352=audio / 152=finish / 153=fail) — only
``resource_id`` is fixed to ``seed-tts-2.0`` and the speaker comes from the ``doubao:``
voice-id prefix. See backend/docs/BYTEDANCE_MODELS_INTEGRATION_PLAN.md §阶段 2a.
"""

from __future__ import annotations

import base64
import json

import pytest

from app.gateway.voice_twin.doubao_tts_client import DoubaoTTSClient


class TestHeaders:
    def test_v3_tts_headers_use_access_key_and_seed_tts_resource(self) -> None:
        client = DoubaoTTSClient(access_key="ark-key-xxx")
        headers = client._v3_tts_headers()
        assert headers["X-Api-Key"] == "ark-key-xxx"
        assert headers["X-Api-Resource-Id"] == "seed-tts-2.0"
        assert headers["Content-Type"] == "application/json"
        # X-Api-Request-Id is a per-call uuid (present + unique per call)
        rid = headers["X-Api-Request-Id"]
        assert isinstance(rid, str) and len(rid) > 0
        assert rid != client._v3_tts_headers()["X-Api-Request-Id"]


class TestBuildPayload:
    def test_payload_shape_matches_openspeech_v3_req_params(self) -> None:
        client = DoubaoTTSClient(access_key="k")
        payload = client._build_payload(
            speaker="zh_male_anqi_moon_bigtts",
            text="你好世界",
            speed_ratio=1.2,
            output_format="mp3",
            language="zh",
        )
        assert payload["user"] == {"uid": "deerflow"}
        rp = payload["req_params"]
        assert rp["text"] == "你好世界"
        assert rp["speaker"] == "zh_male_anqi_moon_bigtts"
        assert rp["speed_ratio"] == 1.2
        assert rp["audio_params"] == {"format": "mp3", "sample_rate": 24000}
        # additions is a JSON string carrying explicit_language
        assert json.loads(rp["additions"]) == {"explicit_language": "zh"}


class TestHandleSseEvent:
    """V3 SSE event parsing (352 audio / 152 finish / 153 fail)."""

    def setup_method(self) -> None:
        self.client = DoubaoTTSClient(access_key="k")
        self.chunks: list[bytes] = []

    def test_event_352_code0_appends_audio(self) -> None:
        audio = b"\x00\x01\x02\x03"
        event = {"code": 0, "data": base64.b64encode(audio).decode()}
        self.client._handle_sse_event("352", event, self.chunks)
        assert self.chunks == [audio]

    def test_event_352_code0_empty_data_skipped(self) -> None:
        self.client._handle_sse_event("352", {"code": 0, "data": ""}, self.chunks)
        assert self.chunks == []

    def test_event_352_nonzero_code_raises(self) -> None:
        # e.g. 55000000 "resource ID mismatched with speaker" — a config error, not infra
        with pytest.raises(RuntimeError, match="55000000"):
            self.client._handle_sse_event("352", {"code": 55000000, "message": "resource ID mismatched with speaker"}, self.chunks)

    def test_event_152_code_20000000_ok(self) -> None:
        self.client._handle_sse_event("152", {"code": 20000000}, self.chunks)
        assert self.chunks == []

    def test_event_152_wrong_code_raises(self) -> None:
        with pytest.raises(RuntimeError, match="session error"):
            self.client._handle_sse_event("152", {"code": 20000001, "message": "weird"}, self.chunks)

    def test_event_153_session_failed_raises(self) -> None:
        with pytest.raises(RuntimeError, match="session failed"):
            self.client._handle_sse_event("153", {"code": 1, "message": "boom"}, self.chunks)

    def test_sse_tolerates_string_and_missing_code(self) -> None:
        """code="0"（字符串）或 352 缺 code 不得误抛错丢弃已收音频（qihu 空 code 同类脆弱性）。"""
        self.client._handle_sse_event("352", {"code": "0", "data": "AAAA"}, self.chunks)
        self.client._handle_sse_event("352", {"data": "BBBB"}, self.chunks)  # 缺 code
        self.client._handle_sse_event("152", {}, self.chunks)  # finish 缺 code
        assert len(self.chunks) == 2

        with pytest.raises(RuntimeError, match="55000000"):
            self.client._handle_sse_event("352", {"code": 55000000, "message": "mismatched"}, self.chunks)
