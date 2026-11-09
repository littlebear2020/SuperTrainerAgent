from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.messages import AIMessage
from langgraph.errors import GraphBubbleUp

from deerflow.agents.middlewares.llm_error_handling_middleware import (
    FALLBACK_MARKER_KEY,
    LLMErrorHandlingMiddleware,
)
from deerflow.config.app_config import AppConfig
from deerflow.config.sandbox_config import SandboxConfig


def _make_app_config() -> AppConfig:
    """Minimal AppConfig for middleware tests; circuit_breaker uses defaults."""
    return AppConfig(sandbox=SandboxConfig(use="test"))


class FakeError(Exception):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        code: str | None = None,
        headers: dict[str, str] | None = None,
        body: dict | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.body = body
        self.response = SimpleNamespace(status_code=status_code, headers=headers or {}) if status_code is not None or headers else None


def _build_middleware(**attrs: int) -> LLMErrorHandlingMiddleware:
    middleware = LLMErrorHandlingMiddleware(app_config=_make_app_config())
    for key, value in attrs.items():
        setattr(middleware, key, value)
    return middleware


def test_async_model_call_retries_busy_provider_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    middleware = _build_middleware(retry_max_attempts=3, retry_base_delay_ms=25, retry_cap_delay_ms=25)
    attempts = 0
    waits: list[float] = []
    events: list[dict] = []

    async def fake_sleep(delay: float) -> None:
        waits.append(delay)

    def fake_writer():
        return events.append

    async def handler(_request) -> AIMessage:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise FakeError("当前服务集群负载较高，请稍后重试，感谢您的耐心等待。 (2064)")
        return AIMessage(content="ok")

    monkeypatch.setattr("asyncio.sleep", fake_sleep)
    monkeypatch.setattr(
        "langgraph.config.get_stream_writer",
        fake_writer,
    )

    result = asyncio.run(middleware.awrap_model_call(SimpleNamespace(), handler))

    assert isinstance(result, AIMessage)
    assert result.content == "ok"
    assert attempts == 3
    assert waits == [0.025, 0.025]
    assert [event["type"] for event in events] == ["llm_retry", "llm_retry"]


def test_async_model_call_returns_user_message_for_quota_errors() -> None:
    middleware = _build_middleware(retry_max_attempts=3)

    async def handler(_request) -> AIMessage:
        raise FakeError(
            "insufficient_quota: account balance is empty",
            status_code=429,
            code="insufficient_quota",
        )

    result = asyncio.run(middleware.awrap_model_call(SimpleNamespace(), handler))

    assert isinstance(result, AIMessage)
    assert "out of quota" in str(result.content)


def test_sync_model_call_uses_retry_after_header(monkeypatch: pytest.MonkeyPatch) -> None:
    middleware = _build_middleware(retry_max_attempts=2, retry_base_delay_ms=10, retry_cap_delay_ms=10)
    waits: list[float] = []
    attempts = 0

    def fake_sleep(delay: float) -> None:
        waits.append(delay)

    def handler(_request) -> AIMessage:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise FakeError(
                "server busy",
                status_code=503,
                headers={"Retry-After": "2"},
            )
        return AIMessage(content="ok")

    monkeypatch.setattr("time.sleep", fake_sleep)

    result = middleware.wrap_model_call(SimpleNamespace(), handler)

    assert isinstance(result, AIMessage)
    assert result.content == "ok"
    assert waits == [2.0]


def test_sync_model_call_propagates_graph_bubble_up() -> None:
    middleware = _build_middleware()

    def handler(_request) -> AIMessage:
        raise GraphBubbleUp()

    with pytest.raises(GraphBubbleUp):
        middleware.wrap_model_call(SimpleNamespace(), handler)


def test_async_model_call_propagates_graph_bubble_up() -> None:
    middleware = _build_middleware()

    async def handler(_request) -> AIMessage:
        raise GraphBubbleUp()

    with pytest.raises(GraphBubbleUp):
        asyncio.run(middleware.awrap_model_call(SimpleNamespace(), handler))


def test_circuit_half_open_graph_bubble_up_resets_probe() -> None:
    """Verify that GraphBubbleUp in half_open state resets probe_in_flight."""
    middleware = _build_middleware()

    middleware._circuit_state = "half_open"
    middleware._circuit_probe_in_flight = False

    def handler(_request) -> AIMessage:
        raise GraphBubbleUp()

    with pytest.raises(GraphBubbleUp):
        middleware.wrap_model_call(SimpleNamespace(), handler)

    assert middleware._circuit_probe_in_flight is False
    assert middleware._circuit_state == "half_open"


@pytest.mark.anyio
async def test_async_circuit_half_open_graph_bubble_up_resets_probe() -> None:
    """Verify that GraphBubbleUp in half_open state resets probe_in_flight (async version)."""
    middleware = _build_middleware()

    middleware._circuit_state = "half_open"
    middleware._circuit_probe_in_flight = False

    async def handler(_request) -> AIMessage:
        raise GraphBubbleUp()

    with pytest.raises(GraphBubbleUp):
        await middleware.awrap_model_call(SimpleNamespace(), handler)

    assert middleware._circuit_probe_in_flight is False
    assert middleware._circuit_state == "half_open"


# ---------- Circuit Breaker Tests ----------


def transient_failing_handler(request: Any) -> Any:
    raise FakeError("Server Error", status_code=502)  # Used for transient error


def quota_failing_handler(request: Any) -> Any:
    raise FakeError("Quota exceeded", body={"error": {"code": "insufficient_quota"}})  # Used for quota error


def success_handler(request: Any) -> Any:
    return AIMessage(content="Success")


def mock_classify_retriable(exc: BaseException) -> tuple[bool, str]:
    return True, "transient"


def mock_classify_non_retriable(exc: BaseException) -> tuple[bool, str]:
    return False, "quota"


def test_circuit_breaker_trips_and_recovers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify that circuit breaker trips, fast fails, correctly transitions to Half-Open, and recovers or re-opens."""

    # Mock time.sleep to avoid slow tests during retry loops (Speed up from ~4s to 0.1s)
    waits: list[float] = []
    monkeypatch.setattr("time.sleep", lambda d: waits.append(d))

    # Mock time.time to decouple from private implementation details and enable time travel
    current_time = 1000.0
    monkeypatch.setattr("time.time", lambda: current_time)

    middleware = _build_middleware(circuit_failure_threshold=3, circuit_recovery_timeout_sec=10)
    monkeypatch.setattr(middleware, "_classify_error", mock_classify_retriable)

    request: Any = {"messages": []}

    # --- 0. Test initial state & Success ---
    # Success handler does not increase count. If it's already 0, it stays 0.
    middleware.wrap_model_call(request, success_handler)
    assert middleware._circuit_failure_count == 0
    assert middleware._check_circuit() is False

    # --- 1. Trip the circuit ---
    # Fails 3 overall calls. Threshold (3) is reached.
    middleware.wrap_model_call(request, transient_failing_handler)
    assert middleware._circuit_failure_count == 1
    middleware.wrap_model_call(request, transient_failing_handler)
    assert middleware._circuit_failure_count == 2
    middleware.wrap_model_call(request, transient_failing_handler)
    assert middleware._circuit_failure_count == 3
    assert middleware._check_circuit() is True  # Circuit is OPEN

    # --- 2. Fast Fail ---
    # 2nd call: fast fail immediately without calling handler.
    # Count should not increase during OPEN state.
    result = middleware.wrap_model_call(request, success_handler)
    assert result.content == middleware._build_circuit_breaker_message()
    assert middleware._circuit_failure_count == 3

    # --- 3. Half-Open -> Fail -> Re-Open ---
    # Time travel 11 seconds (timeout is 10s). Current time becomes 1011.0
    current_time += 11.0

    # Verify that the timeout was set EXACTLY relative to current_time + timeout_sec
    assert middleware._circuit_open_until == current_time - 11.0 + middleware.circuit_recovery_timeout_sec

    # Fails again! The request will go through the 3-attempt retry loop again.
    middleware.wrap_model_call(request, transient_failing_handler)
    assert middleware._circuit_failure_count == middleware.circuit_failure_threshold
    assert middleware._circuit_state == "open"  # Re-OPENed

    # --- 4. Half-Open -> Success -> Reset ---
    # Time travel another 11 seconds
    current_time += 11.0

    # Succeeds this time! Should completely reset.
    result = middleware.wrap_model_call(request, success_handler)
    assert result.content == "Success"
    assert middleware._circuit_failure_count == 0  # Fully RESET!
    assert middleware._check_circuit() is False


def test_circuit_breaker_does_not_trip_on_non_retriable_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify that circuit breaker ignores business errors like Quota or Auth."""
    waits: list[float] = []
    monkeypatch.setattr("time.sleep", lambda d: waits.append(d))

    middleware = _build_middleware(circuit_failure_threshold=3)
    monkeypatch.setattr(middleware, "_classify_error", mock_classify_non_retriable)

    request: Any = {"messages": []}

    for _ in range(3):
        middleware.wrap_model_call(request, quota_failing_handler)

    assert middleware._circuit_failure_count == 0
    assert middleware._check_circuit() is False


# ---------- ReadError / RemoteProtocolError retriable classification ----------


class _ReadError(Exception):
    """Local stand-in for httpx.ReadError — same class name, no httpx dependency."""


class _RemoteProtocolError(Exception):
    """Local stand-in for httpx.RemoteProtocolError — same class name, no httpx dependency."""


_ReadError.__name__ = "ReadError"
_RemoteProtocolError.__name__ = "RemoteProtocolError"


def test_classify_error_read_error_is_retriable() -> None:
    middleware = _build_middleware()
    exc = _ReadError("Connection dropped mid-stream")
    exc.__class__.__name__ = "ReadError"
    retriable, reason = middleware._classify_error(exc)
    assert retriable is True
    assert reason == "transient"


def test_classify_error_remote_protocol_error_is_retriable() -> None:
    middleware = _build_middleware()
    exc = _RemoteProtocolError("Server closed connection unexpectedly")
    exc.__class__.__name__ = "RemoteProtocolError"
    retriable, reason = middleware._classify_error(exc)
    assert retriable is True
    assert reason == "transient"


def test_sync_read_error_triggers_retry_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    middleware = _build_middleware(retry_max_attempts=3, retry_base_delay_ms=10, retry_cap_delay_ms=10)
    attempts = 0
    waits: list[float] = []
    monkeypatch.setattr("time.sleep", lambda d: waits.append(d))

    def handler(_request) -> AIMessage:
        nonlocal attempts
        attempts += 1
        raise _ReadError("Connection dropped mid-stream")

    result = middleware.wrap_model_call(SimpleNamespace(), handler)

    assert isinstance(result, AIMessage)
    assert "temporarily unavailable" in result.content
    assert attempts == 3  # exhausted all retries
    assert len(waits) == 2  # slept between attempts 1→2 and 2→3


@pytest.mark.anyio
async def test_async_read_error_triggers_retry_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    middleware = _build_middleware(retry_max_attempts=3, retry_base_delay_ms=10, retry_cap_delay_ms=10)
    attempts = 0
    waits: list[float] = []

    async def fake_sleep(d: float) -> None:
        waits.append(d)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    async def handler(_request) -> AIMessage:
        nonlocal attempts
        attempts += 1
        raise _ReadError("Connection dropped mid-stream")

    result = await middleware.awrap_model_call(SimpleNamespace(), handler)

    assert isinstance(result, AIMessage)
    assert "temporarily unavailable" in result.content
    assert attempts == 3  # exhausted all retries
    assert len(waits) == 2  # slept between attempts 1→2 and 2→3


@pytest.mark.anyio
async def test_async_circuit_breaker_trips_and_recovers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify async version of circuit breaker correctly handles state transitions."""
    waits: list[float] = []

    async def fake_sleep(d: float) -> None:
        waits.append(d)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    current_time = 1000.0
    monkeypatch.setattr("time.time", lambda: current_time)

    middleware = _build_middleware(circuit_failure_threshold=3, circuit_recovery_timeout_sec=10)
    monkeypatch.setattr(middleware, "_classify_error", mock_classify_retriable)

    async def async_failing_handler(request: Any) -> Any:
        raise FakeError("Server Error", status_code=502)

    request: Any = {"messages": []}

    # --- 1. Trip the circuit ---
    # Fails 3 overall calls. Threshold (3) is reached.
    await middleware.awrap_model_call(request, async_failing_handler)
    assert middleware._circuit_failure_count == 1
    await middleware.awrap_model_call(request, async_failing_handler)
    assert middleware._circuit_failure_count == 2
    await middleware.awrap_model_call(request, async_failing_handler)
    assert middleware._circuit_failure_count == 3
    assert middleware._check_circuit() is True

    # --- 2. Fast Fail ---
    # 2nd call: fast fail immediately without calling handler
    async def async_success_handler(request: Any) -> Any:
        return AIMessage(content="Success")

    result = await middleware.awrap_model_call(request, async_success_handler)
    assert result.content == middleware._build_circuit_breaker_message()
    assert middleware._circuit_failure_count == 3  # Unchanged

    # --- 3. Half-Open -> Fail -> Re-Open ---
    # Time travel 11 seconds
    current_time += 11.0

    # Verify timeout formula
    assert middleware._circuit_open_until == current_time - 11.0 + middleware.circuit_recovery_timeout_sec

    # Fails again! The request goes through the 3-attempt retry loop.
    await middleware.awrap_model_call(request, async_failing_handler)
    assert middleware._circuit_failure_count == middleware.circuit_failure_threshold
    assert middleware._circuit_state == "open"  # Re-OPENed

    # --- 4. Half-Open -> Success -> Reset ---
    # Time travel another 11 seconds
    current_time += 11.0

    result = await middleware.awrap_model_call(request, async_success_handler)
    assert result.content == "Success"
    assert middleware._circuit_failure_count == 0  # RESET
    assert middleware._check_circuit() is False


# ---------- Circuit generation fencing (upstream #5602 + local hardening) ----------


def test_cancelled_probe_releases_half_open(monkeypatch: pytest.MonkeyPatch) -> None:
    """A cancelled half-open probe must release the probe slot (upstream #5602).

    CancelledError is a BaseException that bypasses every ``except Exception``
    chain — before the fix the cancelled probe left ``_circuit_probe_in_flight``
    stuck True, so ``_check_circuit`` fast-failed all later requests forever.
    """
    current_time = 1000.0
    monkeypatch.setattr("time.time", lambda: current_time)

    async def run() -> None:
        nonlocal current_time
        middleware = _build_middleware(
            retry_max_attempts=1,
            circuit_failure_threshold=1,
            circuit_recovery_timeout_sec=10,
        )

        async def failing(_request) -> AIMessage:
            raise FakeError("Service unavailable", status_code=503)

        # Threshold 1: one retriable failure trips the breaker open.
        await middleware.awrap_model_call(SimpleNamespace(), failing)
        assert middleware._circuit_state == "open"

        current_time += 11.0

        probe_started = asyncio.Event()
        release_probe = asyncio.Event()

        async def probe_handler(_request) -> AIMessage:
            probe_started.set()
            await release_probe.wait()
            return AIMessage(content="probe ok")

        task = asyncio.create_task(middleware.awrap_model_call(SimpleNamespace(), probe_handler))
        await asyncio.wait_for(probe_started.wait(), timeout=1)
        assert middleware._circuit_state == "half_open"
        assert middleware._circuit_probe_in_flight is True

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert middleware._circuit_probe_in_flight is False
        assert middleware._circuit_state == "half_open"

        # The next call must be admitted as a NEW probe, not fast-failed forever.
        second_started = asyncio.Event()
        release_second = asyncio.Event()

        async def second_handler(_request) -> AIMessage:
            second_started.set()
            await release_second.wait()
            return AIMessage(content="second ok")

        task2 = asyncio.create_task(middleware.awrap_model_call(SimpleNamespace(), second_handler))
        await asyncio.wait_for(second_started.wait(), timeout=1)
        assert middleware._circuit_probe_in_flight is True
        release_second.set()
        result = await asyncio.wait_for(task2, timeout=1)
        assert result.content == "second ok"
        assert middleware._circuit_state == "closed"

    asyncio.run(run())


def test_stale_failure_cannot_reopen_while_probe_in_flight(monkeypatch: pytest.MonkeyPatch) -> None:
    """A stale-generation failure vote must not disturb the current probe."""
    from deerflow.agents.middlewares.llm_error_handling_middleware import _CircuitAdmission

    current_time = 1000.0
    monkeypatch.setattr("time.time", lambda: current_time)

    middleware = _build_middleware(circuit_failure_threshold=1, circuit_recovery_timeout_sec=10)

    stale = _CircuitAdmission()
    assert middleware._check_circuit(probe_token=stale) is False  # closed passthrough stamps stale
    middleware._record_failure(admission=stale)  # threshold 1 -> open
    assert middleware._circuit_state == "open"

    current_time += 11.0
    probe = _CircuitAdmission()
    assert middleware._check_circuit(probe_token=probe) is False  # open -> half_open, probe admitted
    assert middleware._circuit_state == "half_open"
    assert middleware._circuit_probe_in_flight is True

    # Stale-generation failure must be rejected: the probe stays in flight.
    middleware._record_failure(admission=stale)
    assert middleware._circuit_state == "half_open"
    assert middleware._circuit_probe_in_flight is True
    assert middleware._circuit_probe_token is probe

    # The probe's own success still closes the circuit.
    middleware._record_success(admission=probe)
    assert middleware._circuit_state == "closed"
    assert middleware._circuit_probe_in_flight is False


def test_stale_success_does_not_close_open_circuit(monkeypatch: pytest.MonkeyPatch) -> None:
    """A stale-generation late success must not close the newly-opened circuit."""
    from deerflow.agents.middlewares.llm_error_handling_middleware import _CircuitAdmission

    current_time = 1000.0
    monkeypatch.setattr("time.time", lambda: current_time)

    middleware = _build_middleware(circuit_failure_threshold=1, circuit_recovery_timeout_sec=10)

    stale = _CircuitAdmission()
    assert middleware._check_circuit(probe_token=stale) is False
    middleware._record_failure(admission=stale)
    assert middleware._circuit_state == "open"
    assert middleware._circuit_open_until == current_time + 10

    # Stale-generation success must be rejected: the circuit stays open.
    middleware._record_success(admission=stale)
    assert middleware._circuit_state == "open"

    # Sanity: token-less callers still settle (admission=None tolerance).
    middleware._record_success()
    assert middleware._circuit_state == "closed"


def test_non_owner_bubble_up_does_not_release_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    """A stale call's GraphBubbleUp release must not free another call's probe."""
    current_time = 1000.0
    monkeypatch.setattr("time.time", lambda: current_time)

    async def run() -> None:
        nonlocal current_time
        middleware = _build_middleware(
            retry_max_attempts=1,
            circuit_failure_threshold=1,
            circuit_recovery_timeout_sec=10,
        )

        a_entered = asyncio.Event()
        release_a = asyncio.Event()
        b_entered = asyncio.Event()
        release_b = asyncio.Event()

        async def handler(request) -> AIMessage:
            name = request.name
            if name == "old":
                a_entered.set()
                await release_a.wait()
                raise GraphBubbleUp()
            if name == "trip":
                raise FakeError("Service unavailable", status_code=503)
            b_entered.set()
            await release_b.wait()
            return AIMessage(content="probe ok")

        # A enters while the circuit is still closed (stale generation).
        old_task = asyncio.create_task(middleware.awrap_model_call(SimpleNamespace(name="old"), handler))
        await asyncio.wait_for(a_entered.wait(), timeout=1)

        # Trip the breaker; A is now a stale-generation call.
        await middleware.awrap_model_call(SimpleNamespace(name="trip"), handler)
        assert middleware._circuit_state == "open"

        current_time += 11.0

        # B becomes the half-open probe.
        probe_task = asyncio.create_task(middleware.awrap_model_call(SimpleNamespace(name="probe"), handler))
        await asyncio.wait_for(b_entered.wait(), timeout=1)
        assert middleware._circuit_state == "half_open"
        assert middleware._circuit_probe_in_flight is True

        # A's GraphBubbleUp must NOT release B's probe.
        release_a.set()
        with pytest.raises(GraphBubbleUp):
            await asyncio.wait_for(old_task, timeout=1)
        assert middleware._circuit_state == "half_open"
        assert middleware._circuit_probe_in_flight is True

        # B still settles normally on its own vote.
        release_b.set()
        result = await asyncio.wait_for(probe_task, timeout=1)
        assert result.content == "probe ok"
        assert middleware._circuit_state == "closed"

    asyncio.run(run())


def test_non_retriable_error_during_probe_releases(monkeypatch: pytest.MonkeyPatch) -> None:
    """Local hardening (v4): a non-retriable error during a half-open probe must
    release the probe — it is not a provider-fault signal, so later requests can
    probe again instead of being fast-failed forever."""
    current_time = 1000.0
    monkeypatch.setattr("time.time", lambda: current_time)

    async def run() -> None:
        nonlocal current_time
        middleware = _build_middleware(
            retry_max_attempts=1,
            circuit_failure_threshold=1,
            circuit_recovery_timeout_sec=10,
        )

        async def failing(_request) -> AIMessage:
            raise FakeError("Service unavailable", status_code=503)

        await middleware.awrap_model_call(SimpleNamespace(), failing)
        assert middleware._circuit_state == "open"

        current_time += 11.0

        probe_calls = 0

        async def auth_failing(_request) -> AIMessage:
            nonlocal probe_calls
            probe_calls += 1
            raise FakeError("401 Unauthorized: invalid api key", status_code=401)

        result = await middleware.awrap_model_call(SimpleNamespace(), auth_failing)
        assert isinstance(result, AIMessage)
        assert result.additional_kwargs.get(FALLBACK_MARKER_KEY) is True
        assert result.additional_kwargs.get("error_reason") == "auth"
        assert probe_calls == 1
        # Released, not stuck: the breaker stays half_open and the probe slot is free.
        assert middleware._circuit_state == "half_open"
        assert middleware._circuit_probe_in_flight is False

        # The next call is admitted as a NEW probe and succeeds.
        async def success(_request) -> AIMessage:
            return AIMessage(content="recovered")

        result2 = await middleware.awrap_model_call(SimpleNamespace(), success)
        assert result2.content == "recovered"
        assert middleware._circuit_state == "closed"

    asyncio.run(run())


def test_cancelled_during_backoff_sleep_releases(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cancellation during the backoff sleep must also release the probe.

    The cancel can land on any await point (admission, provider call, retry
    event, backoff sleep) — this pins the backoff-sleep window specifically.
    """
    current_time = 1000.0
    monkeypatch.setattr("time.time", lambda: current_time)

    async def run() -> None:
        nonlocal current_time
        middleware = _build_middleware(
            retry_max_attempts=1,
            circuit_failure_threshold=1,
            circuit_recovery_timeout_sec=10,
        )

        async def failing(_request) -> AIMessage:
            raise FakeError("Service unavailable", status_code=503)

        await middleware.awrap_model_call(SimpleNamespace(), failing)
        assert middleware._circuit_state == "open"

        current_time += 11.0

        # Give the probe retry budget so its first failure enters the backoff sleep.
        middleware.retry_max_attempts = 3
        sleep_entered = asyncio.Event()

        async def hanging_sleep(_delay: float) -> None:
            sleep_entered.set()
            await asyncio.Event().wait()  # never completes; only cancellation escapes

        monkeypatch.setattr(asyncio, "sleep", hanging_sleep)

        task = asyncio.create_task(middleware.awrap_model_call(SimpleNamespace(), failing))
        await asyncio.wait_for(sleep_entered.wait(), timeout=1)
        assert middleware._circuit_state == "half_open"
        assert middleware._circuit_probe_in_flight is True

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert middleware._circuit_probe_in_flight is False
        assert middleware._circuit_state == "half_open"

    asyncio.run(run())


def _make_request_with_fake_model(model_name: str = "fake-model-p4") -> SimpleNamespace:
    """ModelRequest stand-in whose model exposes a distinctive model_name."""
    return SimpleNamespace(model=SimpleNamespace(model_name=model_name))


def test_sync_retry_warnings_include_model_dimension(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    middleware = _build_middleware(retry_max_attempts=2, retry_base_delay_ms=10, retry_cap_delay_ms=10)

    def fake_sleep(_delay: float) -> None:
        pass

    def handler(_request) -> AIMessage:
        raise FakeError("server unavailable", status_code=503)

    monkeypatch.setattr("time.sleep", fake_sleep)

    with caplog.at_level(logging.WARNING, logger=LLMErrorHandlingMiddleware.__module__):
        result = middleware.wrap_model_call(_make_request_with_fake_model(), handler)

    assert isinstance(result, AIMessage)
    transient = [r for r in caplog.records if "Transient LLM error" in r.getMessage()]
    final = [r for r in caplog.records if "LLM call failed" in r.getMessage()]
    assert len(transient) == 1
    assert len(final) == 1
    assert "(model=fake-model-p4)" in transient[0].getMessage()
    assert "(model=fake-model-p4)" in final[0].getMessage()


@pytest.mark.anyio
async def test_async_retry_warnings_include_model_dimension(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    middleware = _build_middleware(retry_max_attempts=2, retry_base_delay_ms=10, retry_cap_delay_ms=10)

    async def fake_sleep(_delay: float) -> None:
        pass

    async def handler(_request) -> AIMessage:
        raise FakeError("server unavailable", status_code=503)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    with caplog.at_level(logging.WARNING, logger=LLMErrorHandlingMiddleware.__module__):
        result = await middleware.awrap_model_call(_make_request_with_fake_model(), handler)

    assert isinstance(result, AIMessage)
    transient = [r for r in caplog.records if "Transient LLM error" in r.getMessage()]
    final = [r for r in caplog.records if "LLM call failed" in r.getMessage()]
    assert len(transient) == 1
    assert len(final) == 1
    assert "(model=fake-model-p4)" in transient[0].getMessage()
    assert "(model=fake-model-p4)" in final[0].getMessage()
