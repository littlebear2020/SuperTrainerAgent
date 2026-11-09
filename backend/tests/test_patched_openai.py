"""Tests for deerflow.models.patched_openai.PatchedChatOpenAI.

These tests verify that _restore_tool_call_signatures correctly re-injects
``thought_signature`` onto tool-call objects stored in
``additional_kwargs["tool_calls"]``, covering id-based matching, positional
fallback, camelCase keys, and several edge-cases.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage

from deerflow.models.patched_openai import _restore_tool_call_signatures

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

RAW_TC_SIGNED = {
    "id": "call_1",
    "type": "function",
    "function": {"name": "web_fetch", "arguments": '{"url":"http://example.com"}'},
    "thought_signature": "SIG_A==",
}

RAW_TC_UNSIGNED = {
    "id": "call_2",
    "type": "function",
    "function": {"name": "bash", "arguments": '{"cmd":"ls"}'},
}

PAYLOAD_TC_1 = {
    "type": "function",
    "id": "call_1",
    "function": {"name": "web_fetch", "arguments": '{"url":"http://example.com"}'},
}

PAYLOAD_TC_2 = {
    "type": "function",
    "id": "call_2",
    "function": {"name": "bash", "arguments": '{"cmd":"ls"}'},
}


def _ai_msg_with_raw_tool_calls(raw_tool_calls: list[dict]) -> AIMessage:
    return AIMessage(content="", additional_kwargs={"tool_calls": raw_tool_calls})


# ---------------------------------------------------------------------------
# Core: signed tool-call restoration
# ---------------------------------------------------------------------------


def test_tool_call_signature_restored_by_id():
    """thought_signature is copied to the payload tool-call matched by id."""
    payload_msg = {"role": "assistant", "content": None, "tool_calls": [PAYLOAD_TC_1.copy()]}
    orig = _ai_msg_with_raw_tool_calls([RAW_TC_SIGNED])

    _restore_tool_call_signatures(payload_msg, orig)

    assert payload_msg["tool_calls"][0]["thought_signature"] == "SIG_A=="


def test_tool_call_signature_for_parallel_calls():
    """For parallel function calls, only the first has a signature (per Gemini spec)."""
    payload_msg = {
        "role": "assistant",
        "content": None,
        "tool_calls": [PAYLOAD_TC_1.copy(), PAYLOAD_TC_2.copy()],
    }
    orig = _ai_msg_with_raw_tool_calls([RAW_TC_SIGNED, RAW_TC_UNSIGNED])

    _restore_tool_call_signatures(payload_msg, orig)

    assert payload_msg["tool_calls"][0]["thought_signature"] == "SIG_A=="
    assert "thought_signature" not in payload_msg["tool_calls"][1]


def test_tool_call_signature_camel_case():
    """thoughtSignature (camelCase) from some gateways is also handled."""
    raw_camel = {
        "id": "call_1",
        "type": "function",
        "function": {"name": "web_fetch", "arguments": "{}"},
        "thoughtSignature": "SIG_CAMEL==",
    }
    payload_msg = {"role": "assistant", "content": None, "tool_calls": [PAYLOAD_TC_1.copy()]}
    orig = _ai_msg_with_raw_tool_calls([raw_camel])

    _restore_tool_call_signatures(payload_msg, orig)

    assert payload_msg["tool_calls"][0]["thought_signature"] == "SIG_CAMEL=="


def test_tool_call_signature_positional_fallback():
    """When ids don't match, falls back to positional matching."""
    raw_no_id = {
        "type": "function",
        "function": {"name": "web_fetch", "arguments": "{}"},
        "thought_signature": "SIG_POS==",
    }
    payload_tc = {
        "type": "function",
        "id": "call_99",
        "function": {"name": "web_fetch", "arguments": "{}"},
    }
    payload_msg = {"role": "assistant", "content": None, "tool_calls": [payload_tc]}
    orig = _ai_msg_with_raw_tool_calls([raw_no_id])

    _restore_tool_call_signatures(payload_msg, orig)

    assert payload_tc["thought_signature"] == "SIG_POS=="


# ---------------------------------------------------------------------------
# Edge cases: no-op scenarios for tool-call signatures
# ---------------------------------------------------------------------------


def test_tool_call_no_raw_tool_calls_is_noop():
    """No change when additional_kwargs has no tool_calls."""
    payload_msg = {"role": "assistant", "content": None, "tool_calls": [PAYLOAD_TC_1.copy()]}
    orig = AIMessage(content="", additional_kwargs={})

    _restore_tool_call_signatures(payload_msg, orig)

    assert "thought_signature" not in payload_msg["tool_calls"][0]


def test_tool_call_no_payload_tool_calls_is_noop():
    """No change when payload has no tool_calls."""
    payload_msg = {"role": "assistant", "content": "just text"}
    orig = _ai_msg_with_raw_tool_calls([RAW_TC_SIGNED])

    _restore_tool_call_signatures(payload_msg, orig)

    assert "tool_calls" not in payload_msg


def test_tool_call_unsigned_raw_entries_is_noop():
    """No signature added when raw tool-calls have no thought_signature."""
    payload_msg = {"role": "assistant", "content": None, "tool_calls": [PAYLOAD_TC_2.copy()]}
    orig = _ai_msg_with_raw_tool_calls([RAW_TC_UNSIGNED])

    _restore_tool_call_signatures(payload_msg, orig)

    assert "thought_signature" not in payload_msg["tool_calls"][0]


def test_tool_call_multiple_sequential_signatures():
    """Sequential tool calls each carry their own signature."""
    raw_tc_a = {
        "id": "call_a",
        "type": "function",
        "function": {"name": "check_flight", "arguments": "{}"},
        "thought_signature": "SIG_STEP1==",
    }
    raw_tc_b = {
        "id": "call_b",
        "type": "function",
        "function": {"name": "book_taxi", "arguments": "{}"},
        "thought_signature": "SIG_STEP2==",
    }
    payload_tc_a = {"type": "function", "id": "call_a", "function": {"name": "check_flight", "arguments": "{}"}}
    payload_tc_b = {"type": "function", "id": "call_b", "function": {"name": "book_taxi", "arguments": "{}"}}
    payload_msg = {"role": "assistant", "content": None, "tool_calls": [payload_tc_a, payload_tc_b]}
    orig = _ai_msg_with_raw_tool_calls([raw_tc_a, raw_tc_b])

    _restore_tool_call_signatures(payload_msg, orig)

    assert payload_tc_a["thought_signature"] == "SIG_STEP1=="
    assert payload_tc_b["thought_signature"] == "SIG_STEP2=="


# ---------------------------------------------------------------------------
# B1b: streaming amount capture via _convert_chunk_to_generation_chunk
# ---------------------------------------------------------------------------

import pytest  # noqa: E402
from langchain_core.messages import AIMessageChunk  # noqa: E402

from deerflow.models.patched_openai import PatchedChatOpenAI  # noqa: E402


class TestStreamingAmountCapture:
    """B1b: the 360 gateway returns a real-bill ``amount`` (string type) in
    the final streaming usage chunk.  The base ``_convert_chunk_to_generation_chunk``
    only normalises token fields into ``usage_metadata`` and drops the raw
    usage dict, so ``amount`` is lost.  Our override copies it into
    ``response_metadata["amount"]`` for the journal to read.
    """

    def _make_patched(self):
        """Construct a PatchedChatOpenAI without network access."""
        return PatchedChatOpenAI(model="test-model", api_key="sk-test")

    def test_amount_extracted_from_usage_chunk(self):
        patched = self._make_patched()

        chunk = {
            "choices": [],
            "id": "chatcmpl-x",
            "model": "test-model",
            "usage": {
                "prompt_tokens": 87,
                "completion_tokens": 10,
                "total_tokens": 97,
                "amount": "0.001174",
            },
        }

        result = patched._convert_chunk_to_generation_chunk(chunk, AIMessageChunk, {"finish_reason": None})
        assert result.message.response_metadata is not None
        assert result.message.response_metadata.get("amount") == pytest.approx(0.001174)

    def test_no_usage_chunk_is_noop(self):
        """A delta chunk without usage should not set amount."""
        patched = self._make_patched()
        chunk = {
            "choices": [{"delta": {"content": "hello"}, "index": 0, "finish_reason": None}],
            "id": "chatcmpl-x",
        }

        result = patched._convert_chunk_to_generation_chunk(chunk, AIMessageChunk, {"finish_reason": None})
        rm = result.message.response_metadata or {}
        assert "amount" not in rm

    def test_none_generation_chunk_does_not_crash(self):
        """The base method returns None for chunks that produce no generation
        (e.g. beta ``content.delta`` frames).  If such a chunk also carries
        ``usage.amount``, the patch must return None instead of crashing with
        AttributeError on ``None.message`` — accounting must never break the
        streaming main loop.
        """
        patched = self._make_patched()
        chunk = {
            "type": "content.delta",
            "choices": [],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2, "amount": "0.001"},
        }

        result = patched._convert_chunk_to_generation_chunk(chunk, AIMessageChunk, {"finish_reason": None})
        assert result is None

    def test_amount_non_numeric_silently_skipped(self):
        """Malformed amount must not crash — debug log only."""
        patched = self._make_patched()
        chunk = {
            "choices": [],
            "usage": {"prompt_tokens": 87, "completion_tokens": 10, "total_tokens": 97, "amount": "not-a-number"},
        }

        result = patched._convert_chunk_to_generation_chunk(chunk, AIMessageChunk, {"finish_reason": None})
        rm = result.message.response_metadata or {}
        assert "amount" not in rm


# Integration behavior for PatchedChatOpenAI is validated indirectly via
# _restore_tool_call_signatures unit coverage above.
