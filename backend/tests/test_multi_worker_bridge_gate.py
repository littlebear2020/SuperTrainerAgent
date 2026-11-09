"""Startup gate: multiple Gateway workers require a cross-process stream bridge.

Run state (RunManager's task table, the bridge's SSE event log) lives in the heap
of the worker that created the run. Uvicorn dispatches requests across workers
without affinity, so with >1 worker a reconnect usually lands on a worker that
never hosted the run — intermittent "Run not found" 404s and hanging SSE streams.
This gate converts that silent, load-dependent failure into an explicit startup
error, and keys off the bridge's own ``cross_process`` flag so it retires itself
once a Redis/Postgres bridge lands.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from app.gateway.deps import (
    _enforce_single_worker_for_process_local_bridge,
    resolve_worker_count,
)
from deerflow.runtime import MemoryStreamBridge, StreamBridge


class _CrossProcessBridge(StreamBridge):
    """Stand-in for a future Redis/Postgres bridge that spans processes."""

    cross_process = True

    async def publish(self, run_id: str, event: str, data: Any) -> None:  # pragma: no cover - not exercised
        raise NotImplementedError

    async def publish_end(self, run_id: str) -> None:  # pragma: no cover - not exercised
        raise NotImplementedError

    def subscribe(
        self,
        run_id: str,
        *,
        last_event_id: str | None = None,
        heartbeat_interval: float = 15.0,
    ) -> AsyncIterator[Any]:  # pragma: no cover - not exercised
        raise NotImplementedError

    async def cleanup(self, run_id: str, *, delay: float = 0) -> None:  # pragma: no cover - not exercised
        raise NotImplementedError


class _OtherProcessLocalBridge(_CrossProcessBridge):
    """A *different* process-local bridge — not MemoryStreamBridge, still unsafe."""

    cross_process = False


# ---------------------------------------------------------------------------
# The two branches the guard exists for
# ---------------------------------------------------------------------------


def test_single_worker_boots_with_memory_bridge():
    """One worker + process-local bridge is the supported default: must not raise."""
    _enforce_single_worker_for_process_local_bridge(1, MemoryStreamBridge())


def test_multi_worker_with_memory_bridge_refuses_to_boot():
    """>1 worker + process-local bridge must hard-fail with an actionable message."""
    with pytest.raises(SystemExit) as exc_info:
        _enforce_single_worker_for_process_local_bridge(4, MemoryStreamBridge())

    message = str(exc_info.value)
    # Names the misconfiguration and the component that cannot support it.
    assert "4 worker processes" in message
    assert "MemoryStreamBridge" in message
    assert "cannot deliver run events across processes" in message
    # Names the symptom it prevents, so the operator recognises it.
    assert "Run not found" in message
    # Names the fix, not just the diagnosis.
    assert "GATEWAY_WORKERS=1" in message


# ---------------------------------------------------------------------------
# Capability-keyed, not class-keyed: the guard must retire itself
# ---------------------------------------------------------------------------


def test_multi_worker_allowed_once_bridge_spans_processes():
    """A bridge declaring cross_process=True lifts the restriction with no edit here."""
    _enforce_single_worker_for_process_local_bridge(8, _CrossProcessBridge())


def test_guard_rejects_any_process_local_bridge_not_just_the_memory_one():
    """The check must read the capability flag, never a class name.

    Mutation-tested: replacing the flag check with
    ``type(bridge).__name__ != "MemoryStreamBridge"`` passes every other test in
    this file, yet fails open for any second process-local bridge. This test is
    what kills that mutant.
    """
    with pytest.raises(SystemExit) as exc_info:
        _enforce_single_worker_for_process_local_bridge(2, _OtherProcessLocalBridge())

    assert "_OtherProcessLocalBridge" in str(exc_info.value)


def test_memory_bridge_declares_itself_process_local():
    """The flag the guard reads must actually be False on the in-memory bridge."""
    assert MemoryStreamBridge().cross_process is False


def test_bridge_base_defaults_to_process_local():
    """A new bridge that forgets to declare the flag must fail closed, not open."""
    assert StreamBridge.cross_process is False


# ---------------------------------------------------------------------------
# Worker-count resolution
#
# GATEWAY_WORKERS reaches the process as an argv literal, NOT an env var:
# compose interpolates ${GATEWAY_WORKERS:-1} into the command string at parse
# time and never exports it into the container environment.
# ---------------------------------------------------------------------------


def test_worker_count_defaults_to_one_when_flag_absent():
    assert resolve_worker_count(argv=["uvicorn", "app.gateway.app:app"], environ={}) == 1


def test_worker_count_read_from_separate_argv_value():
    """The form both compose files use: --workers ${GATEWAY_WORKERS:-1}."""
    argv = ["uvicorn", "app.gateway.app:app", "--host", "0.0.0.0", "--workers", "3"]
    assert resolve_worker_count(argv=argv, environ={}) == 3


def test_worker_count_read_from_inline_argv_value():
    assert resolve_worker_count(argv=["uvicorn", "--workers=5"], environ={}) == 5


def test_worker_count_ignores_gateway_workers_env_var():
    """GATEWAY_WORKERS in the environment must not be mistaken for the real count.

    It can be present via ``env_file`` while compose still rendered ``--workers 1``
    (interpolation and env_file are separate mechanisms), so trusting it would
    hard-fail a perfectly safe single-worker gateway.
    """
    argv = ["uvicorn", "app.gateway.app:app", "--workers", "1"]
    assert resolve_worker_count(argv=argv, environ={"GATEWAY_WORKERS": "8"}) == 1


def test_worker_count_falls_back_to_web_concurrency():
    """uvicorn honours WEB_CONCURRENCY when --workers is absent, so we must too."""
    argv = ["uvicorn", "app.gateway.app:app"]
    assert resolve_worker_count(argv=argv, environ={"WEB_CONCURRENCY": "6"}) == 6


def test_explicit_workers_flag_wins_over_web_concurrency():
    argv = ["uvicorn", "app.gateway.app:app", "--workers", "1"]
    assert resolve_worker_count(argv=argv, environ={"WEB_CONCURRENCY": "6"}) == 1


@pytest.mark.parametrize("value", ["", "abc", "2.5"])
def test_malformed_worker_count_does_not_block_startup(value: str):
    """Refusing to boot over a flag we cannot parse is worse than letting uvicorn error."""
    assert resolve_worker_count(argv=["uvicorn", "--workers", value], environ={}) == 1
    assert resolve_worker_count(argv=["uvicorn"], environ={"WEB_CONCURRENCY": value}) == 1


def test_trailing_workers_flag_without_value_is_ignored():
    assert resolve_worker_count(argv=["uvicorn", "app.gateway.app:app", "--workers"], environ={}) == 1


@pytest.mark.parametrize("workers", [0, -1])
def test_non_positive_worker_count_does_not_trip_the_guard(workers: int):
    """uvicorn treats these as 1 (``self.workers = workers or 1``); so must the guard."""
    _enforce_single_worker_for_process_local_bridge(workers, MemoryStreamBridge())
