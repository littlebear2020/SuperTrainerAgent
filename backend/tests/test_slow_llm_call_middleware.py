"""Tests for SlowLLMCallMiddleware (W6).

Production showed LLM calls silently taking 1/2/3 x request_timeout because the
openai SDK retries timeouts internally (no run event, no metric, no UI signal).
This middleware is observation-only: past the warn threshold it logs a warning,
emits an ``llm_slow`` stream event, and increments ``slow_llm_call_count`` in
``runtime.context`` (the run worker persists it as run completion data). It
must never alter or break the model call, and implements only wrap hooks so it
adds no graph nodes.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest
from _db_helpers import sqlite_repo_fixture
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, HumanMessage

from deerflow.agents.middlewares.slow_llm_call_middleware import SlowLLMCallMiddleware
from deerflow.config.app_config import AppConfig
from deerflow.config.sandbox_config import SandboxConfig
from deerflow.persistence.run import RunRepository

_MODULE_LOGGER = "deerflow.agents.middlewares.slow_llm_call_middleware"


class _Clock:
    """Deterministic monotonic stand-in; fake handlers advance it."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _make_request(ctx: dict | None, *, model: object = "default", message_count: int = 2) -> SimpleNamespace:
    if model == "default":
        model = SimpleNamespace(model_name="gpt-test")
    return SimpleNamespace(
        model=model,
        messages=[HumanMessage(content=f"m{i}") for i in range(message_count)],
        runtime=SimpleNamespace(context=ctx),
    )


def _handler(clock: _Clock, advance: float):
    def call(_request) -> AIMessage:
        clock.now += advance
        return AIMessage(content="ok")

    return call


def _async_handler(clock: _Clock, advance: float):
    async def call(_request) -> AIMessage:
        clock.now += advance
        return AIMessage(content="ok")

    return call


def _capture_events(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    events: list[dict] = []
    monkeypatch.setattr("langgraph.config.get_stream_writer", lambda: events.append)
    return events


def _warnings(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == _MODULE_LOGGER and r.levelno == logging.WARNING]


class TestSyncPath:
    def test_fast_call_has_no_side_effects(self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
        clock = _Clock()
        events = _capture_events(monkeypatch)
        ctx: dict = {}
        middleware = SlowLLMCallMiddleware(warn_seconds=90.0, clock=clock)

        with caplog.at_level(logging.WARNING, logger=_MODULE_LOGGER):
            result = middleware.wrap_model_call(_make_request(ctx), _handler(clock, 10.0))

        assert isinstance(result, AIMessage) and result.content == "ok"
        assert events == []
        assert ctx == {}
        assert _warnings(caplog) == []

    def test_slow_call_warns_emits_event_and_stamps_counter(self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
        clock = _Clock()
        events = _capture_events(monkeypatch)
        ctx: dict = {"thread_id": "t1"}
        middleware = SlowLLMCallMiddleware(warn_seconds=90.0, clock=clock)

        with caplog.at_level(logging.WARNING, logger=_MODULE_LOGGER):
            result = middleware.wrap_model_call(_make_request(ctx), _handler(clock, 123.4))

        assert result.content == "ok"
        assert events == [{"type": "llm_slow", "duration_s": 123.4, "model": "gpt-test", "threshold": 90.0}]
        assert ctx["slow_llm_call_count"] == 1
        warnings = _warnings(caplog)
        assert len(warnings) == 1
        message = warnings[0].getMessage()
        assert "Slow LLM call: 123.4s" in message
        assert "gpt-test" in message
        assert "messages=2" in message

    def test_threshold_boundary_is_strict(self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
        clock = _Clock()
        events = _capture_events(monkeypatch)
        ctx: dict = {}
        middleware = SlowLLMCallMiddleware(warn_seconds=90.0, clock=clock)

        with caplog.at_level(logging.WARNING, logger=_MODULE_LOGGER):
            middleware.wrap_model_call(_make_request(ctx), _handler(clock, 90.0))
            middleware.wrap_model_call(_make_request(ctx), _handler(clock, 90.1))

        assert [event["duration_s"] for event in events] == [90.1]
        assert ctx["slow_llm_call_count"] == 1
        assert len(_warnings(caplog)) == 1

    def test_counter_accumulates_across_calls(self, monkeypatch: pytest.MonkeyPatch) -> None:
        clock = _Clock()
        _capture_events(monkeypatch)
        ctx: dict = {}
        middleware = SlowLLMCallMiddleware(warn_seconds=90.0, clock=clock)

        middleware.wrap_model_call(_make_request(ctx), _handler(clock, 100.0))
        middleware.wrap_model_call(_make_request(ctx), _handler(clock, 100.0))

        assert ctx["slow_llm_call_count"] == 2


class TestAsyncPath:
    @pytest.mark.asyncio
    async def test_slow_async_call_warns_emits_and_stamps(self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
        clock = _Clock()
        events = _capture_events(monkeypatch)
        ctx: dict = {}
        middleware = SlowLLMCallMiddleware(warn_seconds=90.0, clock=clock)

        with caplog.at_level(logging.WARNING, logger=_MODULE_LOGGER):
            result = await middleware.awrap_model_call(_make_request(ctx), _async_handler(clock, 200.0))

        assert result.content == "ok"
        assert events == [{"type": "llm_slow", "duration_s": 200.0, "model": "gpt-test", "threshold": 90.0}]
        assert ctx["slow_llm_call_count"] == 1
        assert len(_warnings(caplog)) == 1

    @pytest.mark.asyncio
    async def test_fast_async_call_has_no_side_effects(self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
        clock = _Clock()
        events = _capture_events(monkeypatch)
        ctx: dict = {}
        middleware = SlowLLMCallMiddleware(warn_seconds=90.0, clock=clock)

        with caplog.at_level(logging.WARNING, logger=_MODULE_LOGGER):
            result = await middleware.awrap_model_call(_make_request(ctx), _async_handler(clock, 1.0))

        assert result.content == "ok"
        assert events == []
        assert ctx == {}
        assert _warnings(caplog) == []


class TestRobustness:
    def test_event_emission_failure_does_not_break_call(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom_writer():
            raise RuntimeError("no stream context")

        monkeypatch.setattr("langgraph.config.get_stream_writer", boom_writer)
        clock = _Clock()
        ctx: dict = {}
        middleware = SlowLLMCallMiddleware(warn_seconds=90.0, clock=clock)

        result = middleware.wrap_model_call(_make_request(ctx), _handler(clock, 200.0))

        assert result.content == "ok"
        # Stamping is independent of event emission: the counter still lands.
        assert ctx["slow_llm_call_count"] == 1

    def test_handler_exception_propagates_and_slow_failure_is_observed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class FakeProviderError(Exception):
            pass

        clock = _Clock()
        events = _capture_events(monkeypatch)
        ctx: dict = {}
        middleware = SlowLLMCallMiddleware(warn_seconds=90.0, clock=clock)

        def failing_handler(_request) -> AIMessage:
            clock.now += 540.0  # e.g. 3 silent SDK-internal timeout retries
            raise FakeProviderError("timeout")

        with pytest.raises(FakeProviderError):
            middleware.wrap_model_call(_make_request(ctx), failing_handler)

        assert events == [{"type": "llm_slow", "duration_s": 540.0, "model": "gpt-test", "threshold": 90.0}]
        assert ctx["slow_llm_call_count"] == 1

    def test_missing_runtime_context_tolerated(self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
        clock = _Clock()
        events = _capture_events(monkeypatch)
        middleware = SlowLLMCallMiddleware(warn_seconds=90.0, clock=clock)
        request = _make_request(None)
        request.runtime = None

        with caplog.at_level(logging.WARNING, logger=_MODULE_LOGGER):
            result = middleware.wrap_model_call(request, _handler(clock, 100.0))

        assert result.content == "ok"
        assert len(events) == 1
        assert len(_warnings(caplog)) == 1

    def test_model_name_falls_back_to_class_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        clock = _Clock()
        events = _capture_events(monkeypatch)
        middleware = SlowLLMCallMiddleware(warn_seconds=90.0, clock=clock)

        middleware.wrap_model_call(_make_request({}, model=SimpleNamespace()), _handler(clock, 100.0))

        assert events[0]["model"] == "SimpleNamespace"

    def test_missing_model_reports_unknown(self, monkeypatch: pytest.MonkeyPatch) -> None:
        clock = _Clock()
        events = _capture_events(monkeypatch)
        middleware = SlowLLMCallMiddleware(warn_seconds=90.0, clock=clock)

        middleware.wrap_model_call(_make_request({}, model=None), _handler(clock, 100.0))

        assert events[0]["model"] == "unknown"


class TestZeroNodeGuarantee:
    def test_no_before_or_after_model_hooks(self) -> None:
        """Wrap-only middleware: before/after hooks must stay un-overridden so
        LangGraph adds no per-round graph nodes for this middleware."""
        for hook in ("before_model", "after_model", "abefore_model", "aafter_model"):
            assert getattr(SlowLLMCallMiddleware, hook) is getattr(AgentMiddleware, hook)


class TestConfiguration:
    def test_app_config_default_threshold(self) -> None:
        assert AppConfig(sandbox=SandboxConfig(use="test")).slow_llm_warn_seconds == 90.0

    def test_middleware_default_threshold(self) -> None:
        assert SlowLLMCallMiddleware().warn_seconds == 90.0

    def test_custom_threshold_respected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        clock = _Clock()
        events = _capture_events(monkeypatch)
        middleware = SlowLLMCallMiddleware(warn_seconds=5.0, clock=clock)

        middleware.wrap_model_call(_make_request({}), _handler(clock, 6.0))

        assert events[0]["threshold"] == 5.0

    def test_threshold_floored_at_one_second(self) -> None:
        """Direct construction with 0/negative/sub-second values must not
        produce an alarm-on-every-call middleware."""
        assert SlowLLMCallMiddleware(warn_seconds=0.0).warn_seconds == 1.0
        assert SlowLLMCallMiddleware(warn_seconds=-5.0).warn_seconds == 1.0
        assert SlowLLMCallMiddleware(warn_seconds=0.5).warn_seconds == 1.0


class TestOpenAILogVisibility:
    """``apply_logging_level`` pins the ``openai`` logger to INFO so the SDK's
    internal "Retrying request" lines (its silent timeout retries) reach gateway
    logs regardless of the configured ``log_level``."""

    def test_openai_logger_pinned_to_info(self) -> None:
        from deerflow.config.app_config import apply_logging_level

        saved = {name: logging.getLogger(name).level for name in ("openai", "deerflow", "app")}
        handler_levels = {h: h.level for h in logging.root.handlers}
        try:
            apply_logging_level("warning")
            assert logging.getLogger("openai").level == logging.INFO
            apply_logging_level("debug")
            assert logging.getLogger("openai").level == logging.INFO
        finally:
            for name, level in saved.items():
                logging.getLogger(name).setLevel(level)
            for handler, level in handler_levels.items():
                handler.setLevel(level)


# Per-test SQLite engine + RunRepository for TestRunCompletionPlumbing; engine
# closed in ``finally`` even when an assertion fails (see tests/_db_helpers.py).
repo = sqlite_repo_fixture(RunRepository)


class TestRunCompletionPlumbing:
    """The worker splats run completion data into RunManager/RunStore, so the
    ``slow_llm_call_count`` key must be a real, persisted parameter end to end."""

    @pytest.mark.anyio
    async def test_sql_store_persists_slow_llm_call_count(self, repo) -> None:
        await repo.put("r1", thread_id="t1")
        await repo.update_run_completion("r1", status="success", slow_llm_call_count=3)
        row = await repo.get("r1")
        assert row["slow_llm_call_count"] == 3

    @pytest.mark.anyio
    async def test_sql_store_leaves_count_alone_when_not_supplied(self, repo) -> None:
        await repo.put("r1", thread_id="t1")
        await repo.update_run_completion("r1", status="running", slow_llm_call_count=2)
        await repo.update_run_completion("r1", status="success")
        assert (await repo.get("r1"))["slow_llm_call_count"] == 2

    @pytest.mark.anyio
    async def test_run_manager_accepts_slow_llm_call_count(self, repo) -> None:
        """Mirrors the worker call shape: journal completion data plus the
        runtime.context-stamped counter, splatted into update_run_completion."""
        from deerflow.runtime.runs.manager import RunManager

        manager = RunManager(store=repo)
        record = await manager.create("t1")

        completion = {"slow_llm_call_count": 2}
        await manager.update_run_completion(record.run_id, status="success", **completion)

        assert record.slow_llm_call_count == 2
        assert (await repo.get(record.run_id))["slow_llm_call_count"] == 2
