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

from app.gateway._shared.task_cost import evaluate_media_call
from app.gateway.voice_twin.doubao_tts_client import DoubaoTTSClient, DoubaoTTSHTTPError


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


class TestUsageRecording:
    """两个合成入口的 external_api_calls 记录（媒体计量计划 PR 1）。

    字幕版 ``synthesize_sse_with_timestamps`` 修前只记 ``status``，不带
    ``model`` / ``char_count``（线上行 metadata_json=None），千字价目永远估不出。
    两个入口必须带同一份 extra；失败调用同样带字段，但估算器只估 ok/success。
    """

    AUDIO = b"\x00\x01\x02\x03"
    SPEAKER = "zh_male_anqi_moon_bigtts"

    @pytest.fixture
    def captured_sink(self, monkeypatch):  # noqa: ANN201
        calls: list[dict] = []

        async def fake_record(**kwargs):  # noqa: ANN003, ANN202
            calls.append(kwargs)

        monkeypatch.setattr("app.gateway._shared.llm_usage_sink.record_external_api_call", fake_record)
        return calls

    @staticmethod
    def _install_stream(monkeypatch, *, lines: list[str], is_error: bool = False) -> None:  # noqa: ANN001
        class _FakeResponse:
            def __init__(self) -> None:
                self.is_error = is_error
                self.status_code = 500

            async def aiter_lines(self):  # noqa: ANN201
                for line in lines:
                    yield line

            async def aread(self) -> bytes:  # noqa: ANN201
                return b"upstream boom"

            async def __aenter__(self):  # noqa: ANN201
                return self

            async def __aexit__(self, *exc) -> bool:  # noqa: ANN001, ANN202
                return False

        class _FakeClient:
            def __init__(self, *args, **kwargs) -> None:  # noqa: ANN001, ANN002
                pass

            async def __aenter__(self):  # noqa: ANN201
                return self

            async def __aexit__(self, *exc) -> bool:  # noqa: ANN001, ANN202
                return False

            def stream(self, method, url, **kwargs):  # noqa: ANN001, ANN002, ANN201
                return _FakeResponse()

        monkeypatch.setattr("app.gateway.voice_twin.doubao_tts_client.httpx.AsyncClient", _FakeClient)

    def _success_lines(self, *, with_subtitle: bool) -> list[str]:
        audio_b64 = base64.b64encode(self.AUDIO).decode()
        lines = ["event: 352", f"data: {json.dumps({'code': 0, 'data': audio_b64})}", ""]
        if with_subtitle:
            words = [{"word": "你", "startTime": 0.0, "endTime": 0.2, "confidence": 1.0}]
            lines += ["event: 364", f"data: {json.dumps({'sentence': {'words': words}})}", ""]
        lines += ["event: 152", 'data: {"code": 20000000}', ""]
        return lines

    @pytest.mark.asyncio
    async def test_with_timestamps_success_records_model_and_char_count(self, captured_sink, monkeypatch):  # noqa: ANN001
        self._install_stream(monkeypatch, lines=self._success_lines(with_subtitle=True))
        text = "你好，世界！"
        audio, words = await DoubaoTTSClient(access_key="k").synthesize_sse_with_timestamps(self.SPEAKER, text)
        assert audio == self.AUDIO
        assert [w["word"] for w in words] == ["你"]
        assert len(captured_sink) == 1
        call = captured_sink[0]
        assert call["provider"] == "doubao_tts"
        assert call["method"] == "synthesize_sse_with_timestamps"
        assert call["status"] == "success"
        assert call["extra"] == {"model": "seed-tts-2.0", "char_count": len(text)}

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("lines", "is_error", "exc_type", "match"),
        [
            (["event: 153", 'data: {"code": 1, "message": "boom"}', ""], False, RuntimeError, "session failed"),
            ([], True, DoubaoTTSHTTPError, "HTTP 500"),
        ],
        ids=["sse-session-failed", "http-500"],
    )
    async def test_with_timestamps_failure_records_fields_but_is_never_estimated(self, captured_sink, monkeypatch, lines, is_error, exc_type, match):  # noqa: ANN001
        self._install_stream(monkeypatch, lines=lines, is_error=is_error)
        text = "失败样本"
        with pytest.raises(exc_type, match=match):
            await DoubaoTTSClient(access_key="k").synthesize_sse_with_timestamps(self.SPEAKER, text)
        assert len(captured_sink) == 1
        call = captured_sink[0]
        assert call["status"] == "error"
        assert call["extra"] == {"model": "seed-tts-2.0", "char_count": len(text)}
        priced = evaluate_media_call(call["provider"], call["method"], call["status"], call["extra"])
        assert priced.cost is None
        assert priced.source is None

    @pytest.mark.asyncio
    async def test_plain_synthesize_sse_still_records_model_and_char_count(self, captured_sink, monkeypatch):  # noqa: ANN001
        """普通版行为不变（回归守护）。"""
        self._install_stream(monkeypatch, lines=self._success_lines(with_subtitle=False))
        text = "普通版"
        audio = await DoubaoTTSClient(access_key="k").synthesize_sse(self.SPEAKER, text)
        assert audio == self.AUDIO
        assert len(captured_sink) == 1
        call = captured_sink[0]
        assert call["method"] == "synthesize_sse"
        assert call["status"] == "success"
        assert call["extra"] == {"model": "seed-tts-2.0", "char_count": len(text)}
