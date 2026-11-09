"""Tests for ModelLengthFinishReasonMiddleware and its detectors (T10).

This middleware is observation-only: it stamps a run stop reason so an
output-length cap is not reported as a clean completion. It must never rewrite
assistant content — that separation is what keeps it safe next to the safety
middleware, which does rewrite.
"""

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from deerflow.agents.middlewares.model_length_finish_reason_middleware import (
    MODEL_LENGTH_CAPPED_STOP_REASON,
    ModelLengthFinishReasonMiddleware,
)
from deerflow.agents.middlewares.model_length_termination_detectors import (
    AnthropicMaxTokensDetector,
    GeminiMaxTokensDetector,
    OpenAICompatibleLengthDetector,
    default_detectors,
)


class _Runtime:
    def __init__(self, context=None):
        self.context = context if context is not None else {}


def _capped(content="A long truncated answer") -> AIMessage:
    return AIMessage(content=content, response_metadata={"finish_reason": "length"})


class TestDetectors:
    def test_default_set(self):
        assert [d.name for d in default_detectors()] == [
            "openai_compatible_length",
            "anthropic_max_tokens",
            "gemini_max_tokens",
        ]

    def test_openai_length(self):
        hit = OpenAICompatibleLengthDetector().detect(_capped())
        assert hit is not None and hit.reason_value == "length"

    def test_openai_stop_ignored(self):
        assert OpenAICompatibleLengthDetector().detect(AIMessage(content="x", response_metadata={"finish_reason": "stop"})) is None

    def test_anthropic_max_tokens(self):
        assert AnthropicMaxTokensDetector().detect(AIMessage(content="x", response_metadata={"stop_reason": "max_tokens"})) is not None

    def test_gemini_max_tokens(self):
        assert GeminiMaxTokensDetector().detect(AIMessage(content="x", response_metadata={"finish_reason": "MAX_TOKENS"})) is not None

    def test_content_filter_not_a_length_cap(self):
        """Safety terminations belong to the other middleware."""
        msg = AIMessage(content="x", response_metadata={"finish_reason": "content_filter"})
        assert all(d.detect(msg) is None for d in default_detectors())


class TestStopReasonStamping:
    def test_stamps_stop_reason(self):
        runtime = _Runtime({"thread_id": "t1", "run_id": "r1"})
        ModelLengthFinishReasonMiddleware().after_model({"messages": [_capped()]}, runtime)
        assert runtime.context["stop_reason"] == MODEL_LENGTH_CAPPED_STOP_REASON

    def test_returns_none_never_rewrites(self):
        """Observation-only: content must be preserved verbatim."""
        msg = _capped()
        assert ModelLengthFinishReasonMiddleware().after_model({"messages": [msg]}, _Runtime()) is None
        assert msg.content == "A long truncated answer"

    def test_preserves_earlier_cap_reason(self):
        """A cap recorded earlier in the run wins over a later length cap."""
        runtime = _Runtime({"stop_reason": "safety_capped"})
        ModelLengthFinishReasonMiddleware().after_model({"messages": [_capped()]}, runtime)
        assert runtime.context["stop_reason"] == "safety_capped"


class TestNonIntervention:
    def test_tool_call_intent_ignored(self):
        """With tool calls pending, the normal tool path decides what happens."""
        runtime = _Runtime()
        msg = AIMessage(content="text", tool_calls=[{"name": "bash", "args": {}, "id": "c1"}], response_metadata={"finish_reason": "length"})
        ModelLengthFinishReasonMiddleware().after_model({"messages": [msg]}, runtime)
        assert "stop_reason" not in runtime.context

    def test_raw_tool_call_payload_ignored(self):
        runtime = _Runtime()
        msg = AIMessage(content="text", additional_kwargs={"tool_calls": [{"id": "c1"}]}, response_metadata={"finish_reason": "length"})
        ModelLengthFinishReasonMiddleware().after_model({"messages": [msg]}, runtime)
        assert "stop_reason" not in runtime.context

    def test_blank_content_ignored(self):
        runtime = _Runtime()
        ModelLengthFinishReasonMiddleware().after_model({"messages": [_capped(content="   ")]}, runtime)
        assert "stop_reason" not in runtime.context

    def test_clean_completion_ignored(self):
        runtime = _Runtime()
        msg = AIMessage(content="done", response_metadata={"finish_reason": "stop"})
        ModelLengthFinishReasonMiddleware().after_model({"messages": [msg]}, runtime)
        assert "stop_reason" not in runtime.context

    def test_list_content_counts_as_visible(self):
        runtime = _Runtime()
        msg = AIMessage(content=[{"type": "text", "text": "partial answer"}], response_metadata={"finish_reason": "length"})
        ModelLengthFinishReasonMiddleware().after_model({"messages": [msg]}, runtime)
        assert runtime.context["stop_reason"] == MODEL_LENGTH_CAPPED_STOP_REASON

    def test_empty_state(self):
        assert ModelLengthFinishReasonMiddleware().after_model({"messages": []}, _Runtime()) is None

    def test_non_ai_last_message(self):
        assert ModelLengthFinishReasonMiddleware().after_model({"messages": [HumanMessage(content="hi")]}, _Runtime()) is None


class TestRobustness:
    def test_buggy_detector_tolerated(self):
        class Boom:
            name = "boom"

            def detect(self, message):
                raise RuntimeError("bug")

        runtime = _Runtime()
        mw = ModelLengthFinishReasonMiddleware(detectors=[Boom()])
        assert mw.after_model({"messages": [_capped()]}, runtime) is None
        assert "stop_reason" not in runtime.context

    def test_missing_context_tolerated(self):
        assert ModelLengthFinishReasonMiddleware().after_model({"messages": [_capped()]}, _Runtime(context=None)) is None

    @pytest.mark.asyncio
    async def test_async_parity(self):
        runtime = _Runtime()
        assert await ModelLengthFinishReasonMiddleware().aafter_model({"messages": [_capped()]}, runtime) is None
        assert runtime.context["stop_reason"] == MODEL_LENGTH_CAPPED_STOP_REASON
