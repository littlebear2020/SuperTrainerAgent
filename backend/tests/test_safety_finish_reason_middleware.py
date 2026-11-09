"""Tests for SafetyFinishReasonMiddleware and its provider detectors (T10).

Covers the failure mode that motivated the port: doubao/360 gateway safety
truncation leaves a half-formed tool call that LangChain would dispatch, so a
``write_file`` stopping mid-sentence corrupts a real file.
"""

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from deerflow.agents.middlewares.safety_finish_reason_middleware import (
    SAFETY_CAPPED_STOP_REASON,
    SafetyFinishReasonMiddleware,
)
from deerflow.agents.middlewares.safety_termination_detectors import (
    AnthropicRefusalDetector,
    GeminiSafetyDetector,
    OpenAICompatibleContentFilterDetector,
    SafetyTermination,
    default_detectors,
)


class _Runtime:
    def __init__(self, context=None):
        self.context = context if context is not None else {}


def _truncated_write_file() -> AIMessage:
    """A content_filter response carrying a half-written write_file call."""
    return AIMessage(
        content="",
        tool_calls=[{"name": "write_file", "args": {"path": "/mnt/user-data/outputs/report.md", "content": "The findings are incomp"}, "id": "call_1"}],
        additional_kwargs={"tool_calls": [{"id": "call_1", "function": {"name": "write_file", "arguments": '{"path":"report.md","content":"The findings are incomp'}}]},
        response_metadata={"finish_reason": "content_filter"},
    )


class TestDetectors:
    def test_default_detector_set(self):
        assert [d.name for d in default_detectors()] == [
            "openai_compatible_content_filter",
            "anthropic_refusal",
            "gemini_safety",
        ]

    def test_openai_content_filter(self):
        hit = OpenAICompatibleContentFilterDetector().detect(AIMessage(content="x", response_metadata={"finish_reason": "content_filter"}))
        assert hit is not None
        assert hit.reason_field == "finish_reason"
        assert hit.reason_value == "content_filter"

    def test_openai_normal_stop_ignored(self):
        assert OpenAICompatibleContentFilterDetector().detect(AIMessage(content="x", response_metadata={"finish_reason": "stop"})) is None

    def test_gateway_custom_reason_via_config(self):
        """360-style gateways use alternative tokens; config extends the set."""
        msg = AIMessage(content="x", response_metadata={"finish_reason": "sensitive"})
        assert OpenAICompatibleContentFilterDetector().detect(msg) is None
        assert OpenAICompatibleContentFilterDetector(finish_reasons=["content_filter", "sensitive"]).detect(msg) is not None

    def test_reads_additional_kwargs_fallback(self):
        """Some passthrough adapters stash the signal in additional_kwargs."""
        msg = AIMessage(content="x", additional_kwargs={"finish_reason": "content_filter"})
        assert OpenAICompatibleContentFilterDetector().detect(msg) is not None

    def test_anthropic_refusal(self):
        hit = AnthropicRefusalDetector().detect(AIMessage(content="x", response_metadata={"stop_reason": "refusal"}))
        assert hit is not None and hit.reason_field == "stop_reason"

    def test_gemini_safety_reasons(self):
        for reason in ("SAFETY", "BLOCKLIST", "PROHIBITED_CONTENT", "IMAGE_SAFETY"):
            assert GeminiSafetyDetector().detect(AIMessage(content="x", response_metadata={"finish_reason": reason})) is not None

    def test_gemini_max_tokens_not_safety(self):
        """MAX_TOKENS is a length cap, not safety — handled by the other middleware."""
        assert GeminiSafetyDetector().detect(AIMessage(content="x", response_metadata={"finish_reason": "MAX_TOKENS"})) is None

    def test_non_string_metadata_does_not_raise(self):
        msg = AIMessage(content="x", response_metadata={"finish_reason": {"weird": "dict"}})
        assert OpenAICompatibleContentFilterDetector().detect(msg) is None


class TestToolCallSuppression:
    def test_truncated_tool_calls_are_stripped(self):
        out = SafetyFinishReasonMiddleware().after_model({"messages": [_truncated_write_file()]}, _Runtime())
        patched = out["messages"][0]
        assert patched.tool_calls == []

    def test_raw_provider_payload_also_cleared(self):
        """Clearing only structured tool_calls would let the raw payload be replayed."""
        out = SafetyFinishReasonMiddleware().after_model({"messages": [_truncated_write_file()]}, _Runtime())
        kwargs = out["messages"][0].additional_kwargs
        assert "tool_calls" not in kwargs
        assert "function_call" not in kwargs

    def test_provider_reason_preserved(self):
        out = SafetyFinishReasonMiddleware().after_model({"messages": [_truncated_write_file()]}, _Runtime())
        assert out["messages"][0].response_metadata["finish_reason"] == "content_filter"

    def test_explanation_appended(self):
        out = SafetyFinishReasonMiddleware().after_model({"messages": [_truncated_write_file()]}, _Runtime())
        assert "safety-related signal" in out["messages"][0].content

    def test_observability_record(self):
        out = SafetyFinishReasonMiddleware().after_model({"messages": [_truncated_write_file()]}, _Runtime())
        record = out["messages"][0].additional_kwargs["safety_termination"]
        assert record["detector"] == "openai_compatible_content_filter"
        assert record["suppressed_tool_call_names"] == ["write_file"]
        assert record["suppressed_tool_call_count"] == 1

    def test_stop_reason_stamped(self):
        runtime = _Runtime({"thread_id": "t1"})
        SafetyFinishReasonMiddleware().after_model({"messages": [_truncated_write_file()]}, runtime)
        assert runtime.context["stop_reason"] == SAFETY_CAPPED_STOP_REASON

    def test_list_content_keeps_structure(self):
        """Anthropic thinking blocks must not be string-coerced into TypeError."""
        msg = AIMessage(
            content=[{"type": "text", "text": "partial"}],
            tool_calls=[{"name": "bash", "args": {}, "id": "c1"}],
            response_metadata={"finish_reason": "content_filter"},
        )
        patched = SafetyFinishReasonMiddleware().after_model({"messages": [msg]}, _Runtime())["messages"][0]
        assert isinstance(patched.content, list)
        assert any("safety-related signal" in b.get("text", "") for b in patched.content if isinstance(b, dict))


class TestEmptyResponseBackfill:
    def test_blank_safety_response_backfilled(self):
        """An empty assistant message poisons the thread on strict providers."""
        msg = AIMessage(content="", response_metadata={"stop_reason": "refusal"})
        patched = SafetyFinishReasonMiddleware().after_model({"messages": [msg]}, _Runtime())["messages"][0]
        assert patched.content.strip()

    def test_backfill_does_not_claim_suppression(self):
        msg = AIMessage(content="", response_metadata={"stop_reason": "refusal"})
        patched = SafetyFinishReasonMiddleware().after_model({"messages": [msg]}, _Runtime())["messages"][0]
        assert "were suppressed" not in patched.content

    def test_none_content_backfilled(self):
        """content=None is reachable via model_copy, which skips validation."""
        msg = AIMessage(content="", response_metadata={"finish_reason": "content_filter"}).model_copy(update={"content": None})
        patched = SafetyFinishReasonMiddleware().after_model({"messages": [msg]}, _Runtime())["messages"][0]
        assert patched.content and "None" != patched.content.strip()

    def test_whitespace_content_backfilled(self):
        msg = AIMessage(content="   \n ", response_metadata={"finish_reason": "content_filter"})
        assert SafetyFinishReasonMiddleware().after_model({"messages": [msg]}, _Runtime()) is not None


class TestNonIntervention:
    def test_partial_answer_left_untouched(self):
        """Text with no tool calls still reaches the user unmodified."""
        msg = AIMessage(content="Here is what I could say", response_metadata={"finish_reason": "content_filter"})
        assert SafetyFinishReasonMiddleware().after_model({"messages": [msg]}, _Runtime()) is None

    def test_clean_response_untouched(self):
        msg = AIMessage(content="all good", tool_calls=[{"name": "bash", "args": {}, "id": "c1"}], response_metadata={"finish_reason": "tool_calls"})
        assert SafetyFinishReasonMiddleware().after_model({"messages": [msg]}, _Runtime()) is None

    def test_empty_state(self):
        assert SafetyFinishReasonMiddleware().after_model({"messages": []}, _Runtime()) is None

    def test_non_ai_last_message(self):
        assert SafetyFinishReasonMiddleware().after_model({"messages": [HumanMessage(content="hi")]}, _Runtime()) is None

    def test_missing_runtime_context_tolerated(self):
        assert SafetyFinishReasonMiddleware().after_model({"messages": [_truncated_write_file()]}, _Runtime(context=None)) is not None


class TestDetectorRobustness:
    def test_buggy_detector_does_not_break_run(self):
        class Boom:
            name = "boom"

            def detect(self, message):
                raise RuntimeError("detector bug")

        class Works:
            name = "works"

            def detect(self, message):
                return SafetyTermination(detector="works", reason_field="finish_reason", reason_value="content_filter")

        mw = SafetyFinishReasonMiddleware(detectors=[Boom(), Works()])
        out = mw.after_model({"messages": [_truncated_write_file()]}, _Runtime())
        assert out["messages"][0].tool_calls == []

    def test_custom_detectors_replace_defaults(self):
        class Never:
            name = "never"

            def detect(self, message):
                return None

        assert SafetyFinishReasonMiddleware(detectors=[Never()]).after_model({"messages": [_truncated_write_file()]}, _Runtime()) is None


class TestAsyncParity:
    @pytest.mark.asyncio
    async def test_async_matches_sync(self):
        mw = SafetyFinishReasonMiddleware()
        out = await mw.aafter_model({"messages": [_truncated_write_file()]}, _Runtime())
        assert out["messages"][0].tool_calls == []
        assert "safety_termination" in out["messages"][0].additional_kwargs

    @pytest.mark.asyncio
    async def test_async_no_intervention(self):
        mw = SafetyFinishReasonMiddleware()
        msg = AIMessage(content="fine", response_metadata={"finish_reason": "stop"})
        assert await mw.aafter_model({"messages": [msg]}, _Runtime()) is None
