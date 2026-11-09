"""P1-9: LLM fallback replies must not be recorded as run success.

``LLMErrorHandlingMiddleware`` converts provider exceptions into a plain
``AIMessage`` so the graph terminates cleanly. Clean graph termination is not
task success. Without a structured marker on that message there is no signal
any downstream consumer could detect, so the run is journaled as ``success``
and the user is handed a provider-failure sentence dressed as an answer.

These tests pin both halves:
1. all four middleware fallback return sites stamp the marker, and
2. the journal maps a marked fallback onto the run-error path.

Detection point note: the marker is observed in ``on_chain_end`` (the run's
final state), NOT ``on_llm_end``. A synthesized fallback never went through the
model, so ``on_llm_end`` does not fire for it at all — verified empirically
against a real ``create_agent`` graph before choosing the hook.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from deerflow.agents.middlewares.llm_error_handling_middleware import (
    FALLBACK_MARKER_KEY,
    LLMErrorHandlingMiddleware,
)
from deerflow.config.app_config import AppConfig
from deerflow.config.sandbox_config import SandboxConfig
from deerflow.runtime.events.store.memory import MemoryRunEventStore
from deerflow.runtime.journal import RunJournal


def _make_middleware(**attrs: int) -> LLMErrorHandlingMiddleware:
    middleware = LLMErrorHandlingMiddleware(app_config=AppConfig(sandbox=SandboxConfig(use="test")))
    for key, value in attrs.items():
        setattr(middleware, key, value)
    return middleware


class _Boom(Exception):
    """Non-retriable provider error (classified "generic")."""


def _trip_circuit(middleware: LLMErrorHandlingMiddleware) -> None:
    """Force the breaker OPEN so the fast-fail return site is reachable."""
    for _ in range(middleware.circuit_failure_threshold):
        middleware._record_failure()
    assert middleware._check_circuit() is True


# --- middleware half: all four return sites ---------------------------------


def test_sync_final_failure_stamps_marker() -> None:
    middleware = _make_middleware(retry_max_attempts=1)

    def handler(_request):
        raise _Boom("provider exploded")

    result = middleware.wrap_model_call(SimpleNamespace(), handler)

    assert isinstance(result, AIMessage)
    assert result.additional_kwargs.get(FALLBACK_MARKER_KEY) is True
    assert result.additional_kwargs.get("error_detail") == "provider exploded"
    assert result.additional_kwargs.get("error_type") == "_Boom"


def test_sync_circuit_breaker_stamps_marker() -> None:
    middleware = _make_middleware()
    _trip_circuit(middleware)

    def handler(_request):  # pragma: no cover - breaker must short-circuit
        raise AssertionError("handler must not be called while circuit is open")

    result = middleware.wrap_model_call(SimpleNamespace(), handler)

    assert isinstance(result, AIMessage)
    assert result.additional_kwargs.get(FALLBACK_MARKER_KEY) is True
    assert result.additional_kwargs.get("error_reason") == "circuit_open"


def test_async_final_failure_stamps_marker() -> None:
    middleware = _make_middleware(retry_max_attempts=1)

    async def handler(_request):
        raise _Boom("async provider exploded")

    result = asyncio.run(middleware.awrap_model_call(SimpleNamespace(), handler))

    assert isinstance(result, AIMessage)
    assert result.additional_kwargs.get(FALLBACK_MARKER_KEY) is True
    assert result.additional_kwargs.get("error_detail") == "async provider exploded"


def test_async_circuit_breaker_stamps_marker() -> None:
    middleware = _make_middleware()
    _trip_circuit(middleware)

    async def handler(_request):  # pragma: no cover - breaker must short-circuit
        raise AssertionError("handler must not be called while circuit is open")

    result = asyncio.run(middleware.awrap_model_call(SimpleNamespace(), handler))

    assert isinstance(result, AIMessage)
    assert result.additional_kwargs.get(FALLBACK_MARKER_KEY) is True
    assert result.additional_kwargs.get("error_reason") == "circuit_open"


def test_successful_response_is_not_marked() -> None:
    """Guard against over-marking: a real answer must stay unmarked."""
    middleware = _make_middleware()

    def handler(_request):
        return AIMessage(content="a genuine answer")

    result = middleware.wrap_model_call(SimpleNamespace(), handler)
    assert result.additional_kwargs.get(FALLBACK_MARKER_KEY) is None


# --- journal half -----------------------------------------------------------


@pytest.fixture
def journal_setup():
    store = MemoryRunEventStore()
    # flush_threshold=1 so on_chain_end's buffered events land in the store
    # immediately; the assertions below read them back synchronously.
    return RunJournal("r1", "t1", store, flush_threshold=1), store


def _run_end_events(store: MemoryRunEventStore) -> list[dict]:
    return [e for e in store._events.get("t1", []) if e["event_type"] == "run.end"]


def _fallback_state(detail: str = "provider exploded") -> dict:
    return {
        "messages": [
            HumanMessage(content="do the thing"),
            AIMessage(
                content="The configured LLM provider is temporarily unavailable.",
                additional_kwargs={
                    FALLBACK_MARKER_KEY: True,
                    "error_type": "APITimeoutError",
                    "error_reason": "transient",
                    "error_detail": detail,
                },
            ),
        ]
    }


def test_journal_flags_fallback_as_error(journal_setup) -> None:
    journal, _store = journal_setup
    journal.on_chain_end(_fallback_state(), run_id="run-1", parent_run_id=None)

    assert journal.had_llm_error_fallback is True
    assert journal.llm_error_fallback_message == "provider exploded"
    assert journal.get_completion_data()["llm_error_fallback"] is True


@pytest.mark.anyio
async def test_journal_run_end_event_is_not_marked_success(journal_setup) -> None:
    journal, store = journal_setup
    journal.on_chain_end(_fallback_state(), run_id="run-1", parent_run_id=None)
    # on_chain_end only buffers (sync callback can't safely drive an async store);
    # the async flush drains the run.end event into the store where it's asserted.
    await journal.flush()

    run_end = _run_end_events(store)
    assert run_end, "expected a run.end event"
    assert run_end[-1]["metadata"]["status"] == "error"


@pytest.mark.anyio
async def test_journal_clean_run_still_success(journal_setup) -> None:
    """A genuine answer must remain a success — no false positives."""
    journal, store = journal_setup
    journal.on_chain_end(
        {"messages": [HumanMessage(content="hi"), AIMessage(content="real answer")]},
        run_id="run-1",
        parent_run_id=None,
    )
    await journal.flush()

    assert journal.had_llm_error_fallback is False
    assert journal.get_completion_data()["llm_error_fallback"] is False
    run_end = _run_end_events(store)
    assert run_end[-1]["metadata"]["status"] == "success"


def test_journal_ignores_unmarked_error_looking_prose(journal_setup) -> None:
    """Only the marker is authoritative; display text is not a status protocol."""
    journal, _store = journal_setup
    journal.on_chain_end(
        {"messages": [AIMessage(content="LLM request failed: something broke")]},
        run_id="run-1",
        parent_run_id=None,
    )

    assert journal.had_llm_error_fallback is False


def test_journal_only_inspects_final_assistant_message(journal_setup) -> None:
    """A stale marker followed by a real answer is a recovered run, not a failure."""
    journal, _store = journal_setup
    journal.on_chain_end(
        {
            "messages": [
                AIMessage(content="transient blip", additional_kwargs={FALLBACK_MARKER_KEY: True}),
                AIMessage(content="the retry produced a real answer"),
            ]
        },
        run_id="run-1",
        parent_run_id=None,
    )

    assert journal.had_llm_error_fallback is False
