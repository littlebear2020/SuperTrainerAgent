"""Tests for 360 TTS client SSE parsing and error handling."""

from __future__ import annotations

import base64
import json

import pytest

from app.gateway.voice_twin.qihu_tts_client import QihuTTSClient


class TestHandleSseEvent:
    """Unit tests for the _handle_sse_event method."""

    def setup_method(self) -> None:
        self.client = QihuTTSClient(api_key="test-key")
        self.chunks: list[bytes] = []

    def test_valid_audio_chunk(self) -> None:
        audio_data = b"\x00\x01\x02\x03"
        event = json.dumps(
            {
                "code": "0",
                "data": {"content": base64.b64encode(audio_data).decode()},
            }
        )
        self.client._handle_sse_event(event, self.chunks)
        assert self.chunks == [audio_data]

    def test_multiple_chunks_accumulate(self) -> None:
        a1 = b"\x00\x01"
        a2 = b"\x02\x03"
        e1 = json.dumps({"code": "0", "data": {"content": base64.b64encode(a1).decode()}})
        e2 = json.dumps({"code": "0", "data": {"content": base64.b64encode(a2).decode()}})
        self.client._handle_sse_event(e1, self.chunks)
        self.client._handle_sse_event(e2, self.chunks)
        assert self.chunks == [a1, a2]

    def test_error_code_raises(self) -> None:
        event = json.dumps({"code": "rate_limit", "message": "Too many requests"})
        with pytest.raises(RuntimeError, match="rate_limit"):
            self.client._handle_sse_event(event, self.chunks)

    def test_numeric_nonzero_code_raises(self) -> None:
        event = json.dumps({"code": 500, "message": "Internal error"})
        with pytest.raises(RuntimeError, match="500"):
            self.client._handle_sse_event(event, self.chunks)

    def test_code_zero_no_content(self) -> None:
        event = json.dumps({"code": "0", "data": {}})
        self.client._handle_sse_event(event, self.chunks)
        assert self.chunks == []

    def test_no_code_field_ok(self) -> None:
        audio_data = b"\x04\x05"
        event = json.dumps({"data": {"content": base64.b64encode(audio_data).decode()}})
        self.client._handle_sse_event(event, self.chunks)
        assert self.chunks == [audio_data]

    def test_invalid_json_ignored(self) -> None:
        self.client._handle_sse_event("not json", self.chunks)
        assert self.chunks == []

    def test_empty_content_skipped(self) -> None:
        event = json.dumps({"code": "0", "data": {"content": ""}})
        self.client._handle_sse_event(event, self.chunks)
        assert self.chunks == []

    def test_empty_string_code_does_not_raise(self) -> None:
        """360 emits a trailing usage/billing event with code="" (empty string)
        and empty data. This is NOT an error — only a non-empty, non-"0" code is.

        Regression for the preset-voice preview 503: the empty code was
        misclassified as an error, so synthesize_sse raised and discarded the
        audio already collected from earlier code="0" chunks.
        """
        audio_data = b"\x06\x07"
        audio_event = json.dumps({"code": "0", "data": {"content": base64.b64encode(audio_data).decode()}})
        usage_event = json.dumps({"code": "", "message": "", "data": {}, "usage": {"text_words": 2, "amount": "0.0003"}})
        self.client._handle_sse_event(audio_event, self.chunks)
        self.client._handle_sse_event(usage_event, self.chunks)  # must not raise
        assert self.chunks == [audio_data]

    def test_sse_tolerates_null_data(self) -> None:
        """360 若下发 data:null 不得 AttributeError 崩流（K2 同源，两处同修）。"""
        self.client._handle_sse_event('{"code": "", "data": null}', self.chunks)  # 不抛异常即可
        assert self.chunks == []


class TestMergeWavChunks:
    """Tests for _merge_wav_chunks — merging multiple WAV files into one."""

    @staticmethod
    def _make_wav(samples: list[int], sample_rate: int = 24000) -> bytes:
        """Create a minimal valid WAV file with 16-bit mono PCM samples."""
        import io
        import struct
        import wave

        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)  # 16-bit
            wf.setframerate(sample_rate)
            raw = struct.pack(f"<{len(samples)}h", *samples)
            wf.writeframes(raw)
        return buf.getvalue()

    def test_single_chunk_returned_as_is(self) -> None:
        from app.gateway.voice_twin.qihu_tts_client import _merge_wav_chunks

        chunk = self._make_wav([100, 200, 300])
        result = _merge_wav_chunks([chunk])
        assert result == chunk

    def test_multiple_chunks_merged_correctly(self) -> None:
        import io
        import wave

        from app.gateway.voice_twin.qihu_tts_client import _merge_wav_chunks

        chunk1 = self._make_wav([100, 200, 300])
        chunk2 = self._make_wav([400, 500])
        merged = _merge_wav_chunks([chunk1, chunk2])

        # Read merged WAV — should have 5 frames total
        with wave.open(io.BytesIO(merged), "rb") as wf:
            assert wf.getnframes() == 5
            assert wf.getnchannels() == 1
            assert wf.getsampwidth() == 2
            assert wf.getframerate() == 24000

    def test_invalid_chunk_skipped(self) -> None:
        import io
        import wave

        from app.gateway.voice_twin.qihu_tts_client import _merge_wav_chunks

        valid = self._make_wav([100, 200])
        garbage = b"not a wav at all"
        merged = _merge_wav_chunks([valid, garbage])

        with wave.open(io.BytesIO(merged), "rb") as wf:
            assert wf.getnframes() == 2

    def test_all_invalid_returns_concat(self) -> None:
        from app.gateway.voice_twin.qihu_tts_client import _merge_wav_chunks

        garbage = [b"garbage1", b"garbage2"]
        result = _merge_wav_chunks(garbage)
        # Falls back to raw concatenation
        assert result == b"garbage1garbage2"


class TestBuildPayload:
    """_build_payload 的 speed 字段语义（2026-07-27：曾整条 360 通道丢弃语速，P1）。"""

    def test_build_payload_includes_speed_when_not_1(self) -> None:
        """speed≠1.0 必须进 payload。"""
        from app.gateway.voice_twin.qihu_tts_client import _build_payload

        payload = _build_payload("文本", 146, "base64", 1.5)
        assert payload["speed"] == 1.5

    def test_build_payload_omits_speed_at_default(self) -> None:
        """speed=None 或 1.0 时 payload 不带 speed 字段（避免触发 360 端校验）。"""
        from app.gateway.voice_twin.qihu_tts_client import _build_payload

        assert "speed" not in _build_payload("文本", 146, "base64", None)
        assert "speed" not in _build_payload("文本", 146, "base64", 1.0)

    def test_build_payload_speed_clamped(self) -> None:
        """speed 越界钳到 [0.25, 4.0]。"""
        from app.gateway.voice_twin.qihu_tts_client import _build_payload

        assert _build_payload("文本", 146, "base64", 10.0)["speed"] == 4.0
        assert _build_payload("文本", 146, "base64", 0.1)["speed"] == 0.25
