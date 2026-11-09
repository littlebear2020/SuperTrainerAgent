"""Slow LLM call observability middleware.

Production showed single LLM calls silently taking 1/2/3 x ``request_timeout``
because the openai SDK retries timeouts *internally* — the only traces were
``openai`` logger DEBUG/INFO lines, with no run event, no metric, and no UI
signal. This middleware closes that blind spot without changing any
timeout/retry behaviour: it times every model call and, once a call exceeds
``warn_seconds``, it

* logs a ``WARNING`` with duration, model name and message count;
* emits an ``llm_slow`` stream event (same ``get_stream_writer`` channel as
  ``LLMErrorHandlingMiddleware``'s ``llm_retry``); and
* increments ``slow_llm_call_count`` in ``runtime.context``, which the run
  worker (``runtime/runs/worker.py``) carries into run completion data —
  the same stamping path the safety/model-length middlewares use for
  ``stop_reason``.

Observation-only by contract: it implements *only* ``wrap_model_call`` /
``awrap_model_call`` (no ``before_model``/``after_model``, so LangGraph adds no
per-round graph nodes), never retries or rewrites the request/response, and
every side effect is individually guarded so an observability failure can never
break the model call. A call that fails after exceeding the threshold is still
observed (timing lives in ``finally``) — a slow-then-failed attempt is exactly
the production symptom this exists to surface.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from typing import override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import (
    ModelCallResult,
    ModelRequest,
    ModelResponse,
)

logger = logging.getLogger(__name__)

DEFAULT_WARN_SECONDS = 90.0


class SlowLLMCallMiddleware(AgentMiddleware[AgentState]):
    """Time model calls; warn + emit ``llm_slow`` + count when over threshold."""

    def __init__(self, *, warn_seconds: float = DEFAULT_WARN_SECONDS, clock: Callable[[], float] = time.monotonic) -> None:
        super().__init__()
        # Floor at 1s: AppConfig validates gt=0, but direct construction (tests,
        # factory chains) can bypass it — 0/negative would alarm on every call,
        # and a sub-second threshold is never meaningful for an LLM call.
        self.warn_seconds = max(1.0, float(warn_seconds))
        self._clock = clock

    def _model_name(self, request: ModelRequest) -> str:
        model = getattr(request, "model", None)
        for attr in ("model_name", "model"):
            value = getattr(model, attr, None)
            if isinstance(value, str) and value:
                return value
        return type(model).__name__ if model is not None else "unknown"

    def _message_count(self, request: ModelRequest) -> int:
        messages = getattr(request, "messages", None)
        return len(messages) if isinstance(messages, list) else 0

    def _emit_slow_event(self, duration_s: float, model: str) -> None:
        try:
            from langgraph.config import get_stream_writer

            writer = get_stream_writer()
            writer(
                {
                    "type": "llm_slow",
                    "duration_s": round(duration_s, 1),
                    "model": model,
                    "threshold": self.warn_seconds,
                }
            )
        except Exception:
            logger.debug("Failed to emit llm_slow event", exc_info=True)

    def _stamp_slow_call_count(self, request: ModelRequest) -> None:
        try:
            ctx = getattr(getattr(request, "runtime", None), "context", None)
            if isinstance(ctx, dict):
                ctx["slow_llm_call_count"] = int(ctx.get("slow_llm_call_count") or 0) + 1
        except Exception:
            logger.debug("Failed to stamp slow_llm_call_count", exc_info=True)

    def _observe(self, request: ModelRequest, elapsed: float) -> None:
        """Record one finished model call. Never raises."""
        try:
            if elapsed <= self.warn_seconds:
                return
            model = self._model_name(request)
            logger.warning(
                "Slow LLM call: %.1fs (model=%s, threshold=%.0fs, messages=%d)",
                elapsed,
                model,
                self.warn_seconds,
                self._message_count(request),
            )
            self._emit_slow_event(elapsed, model)
            self._stamp_slow_call_count(request)
        except Exception:
            logger.debug("Slow LLM call observation failed", exc_info=True)

    @override
    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelCallResult:
        start = self._clock()
        try:
            return handler(request)
        finally:
            self._observe(request, self._clock() - start)

    @override
    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelCallResult:
        start = self._clock()
        try:
            return await handler(request)
        finally:
            self._observe(request, self._clock() - start)
