"""In-flight token/cost persistence for a running run (P2-1).

The operations console lists active runs correctly (no default status filter,
live elapsed time), but its metering columns read 0 for the entire duration of
a run and only jump at completion: ``RunJournal`` accumulated tokens purely in
memory and the single writer was the worker's ``finally`` block. If the process
died mid-run — the exact case an operator is watching the console for — that
usage was lost permanently.

The store plumbing (``update_run_progress`` on base/memory/SQL + RunManager)
already existed with zero production callers. These tests pin the missing half:
the journal deciding *when* to flush, and the invariant that a mid-run flush
plus the final write must not double-count.
"""

from __future__ import annotations

import pytest
from _db_helpers import sqlite_repo_fixture
from langchain_core.messages import AIMessage

from deerflow.persistence.run import RunRepository
from deerflow.runtime.journal import RunJournal
from deerflow.runtime.runs.manager import RunManager
from deerflow.runtime.runs.schemas import RunStatus


class _FakeEventStore:
    async def put_batch(self, batch):
        pass


class _FakeResponse:
    """Minimal LLMResult stand-in: RunJournal only walks ``.generations``."""

    def __init__(self, messages):
        self.generations = [[type("Gen", (), {"message": m})() for m in messages]]


def _ai(model: str, input_tokens: int, output_tokens: int, *, msg_id: str = "m") -> AIMessage:
    usage = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
    }
    return AIMessage(content="answer", id=msg_id, usage_metadata=usage, response_metadata={"model_name": model})


# Per-test SQLite engine + RunRepository; engine closed in ``finally`` even
# when an assertion fails (see tests/_db_helpers.py).
repo = sqlite_repo_fixture(RunRepository)


class TestProgressFlushCadence:
    """The journal must offer a bounded-cadence in-flight snapshot."""

    def test_journal_exposes_progress_snapshot(self):
        journal = RunJournal("r", "t", _FakeEventStore(), track_token_usage=True)
        journal.on_llm_end(_FakeResponse([_ai("m1", 100, 50)]), run_id="lc-1")

        snapshot = journal.get_progress_data()

        assert snapshot["total_tokens"] == 150
        assert snapshot["token_usage_by_model"]["m1"]["total_tokens"] == 150

    def test_first_llm_call_is_due_for_flush(self):
        """An operator must not wait a full interval to see a run's first cost."""
        journal = RunJournal("r", "t", _FakeEventStore(), track_token_usage=True)
        journal.on_llm_end(_FakeResponse([_ai("m1", 10, 5)]), run_id="lc-1")

        assert journal.progress_flush_due() is True

    def test_not_due_again_immediately_after_a_flush(self):
        """Cadence gate: consecutive calls must not hammer SQLite per token."""
        journal = RunJournal("r", "t", _FakeEventStore(), track_token_usage=True)
        journal.on_llm_end(_FakeResponse([_ai("m1", 10, 5)]), run_id="lc-1")
        journal.mark_progress_flushed()

        journal.on_llm_end(_FakeResponse([_ai("m1", 10, 5)]), run_id="lc-2")

        assert journal.progress_flush_due() is False

    def test_due_again_once_the_call_threshold_is_crossed(self):
        """Token-count growth alone should not trigger a write; call count does."""
        journal = RunJournal("r", "t", _FakeEventStore(), track_token_usage=True, progress_flush_every=3)
        journal.on_llm_end(_FakeResponse([_ai("m1", 10, 5)]), run_id="lc-1")
        journal.mark_progress_flushed()

        for i in range(2, 5):
            journal.on_llm_end(_FakeResponse([_ai("m1", 10, 5)]), run_id=f"lc-{i}")

        assert journal.progress_flush_due() is True

    def test_no_flush_when_nothing_was_recorded(self):
        journal = RunJournal("r", "t", _FakeEventStore(), track_token_usage=True)
        assert journal.progress_flush_due() is False

    def test_no_flush_when_token_tracking_is_disabled(self):
        journal = RunJournal("r", "t", _FakeEventStore(), track_token_usage=False)
        journal.on_llm_end(_FakeResponse([_ai("m1", 10, 5)]), run_id="lc-1")
        assert journal.progress_flush_due() is False


class TestNoDoubleCounting:
    """Mid-run writes are absolute snapshots, never increments."""

    @pytest.mark.anyio
    async def test_inflight_flush_plus_final_write_is_not_inflated(self, repo):
        """Three progress flushes then a completion must total the real usage.

        This repo already shipped a token double-count bug, so additivity is
        pinned explicitly: each write sets the accumulated total rather than
        adding a delta, so the final row equals the journal's own total.
        """
        manager = RunManager(store=repo)
        record = await manager.create("t1")
        await manager.set_status(record.run_id, RunStatus.running)

        journal = RunJournal(record.run_id, "t1", _FakeEventStore(), track_token_usage=True)

        for i in range(1, 4):
            journal.on_llm_end(_FakeResponse([_ai("m1", 100, 50)]), run_id=f"lc-{i}")
            await manager.update_run_progress(record.run_id, **journal.get_progress_data())
            journal.mark_progress_flushed()

        await manager.update_run_completion(record.run_id, status=RunStatus.success.value, **journal.get_completion_data())

        row = await repo.get(record.run_id)
        assert row["total_tokens"] == 450
        assert row["total_input_tokens"] == 300
        assert row["total_output_tokens"] == 150
        assert row["llm_call_count"] == 3
        assert row["token_usage_by_model"]["m1"]["total_tokens"] == 450

    @pytest.mark.anyio
    async def test_usage_survives_a_crash_before_completion(self, repo):
        """No completion write ever lands — the flushed snapshot must persist."""
        manager = RunManager(store=repo)
        record = await manager.create("t1")
        await manager.set_status(record.run_id, RunStatus.running)

        journal = RunJournal(record.run_id, "t1", _FakeEventStore(), track_token_usage=True)
        journal.on_llm_end(_FakeResponse([_ai("m1", 200, 100)]), run_id="lc-1")
        await manager.update_run_progress(record.run_id, **journal.get_progress_data())

        row = await repo.get(record.run_id)
        assert row["status"] == "running"
        assert row["total_tokens"] == 300
        assert row["token_usage_by_model"]["m1"]["total_tokens"] == 300

    @pytest.mark.anyio
    async def test_progress_snapshot_is_accepted_verbatim_by_the_store(self, repo):
        """The worker splats ``get_progress_data()`` straight in, so every key
        it emits must be a real ``update_run_progress`` parameter."""
        manager = RunManager(store=repo)
        record = await manager.create("t1")
        await manager.set_status(record.run_id, RunStatus.running)

        journal = RunJournal(record.run_id, "t1", _FakeEventStore(), track_token_usage=True)
        journal.on_llm_end(_FakeResponse([_ai("m1", 10, 5)]), run_id="lc-1")

        await manager.update_run_progress(record.run_id, **journal.get_progress_data())

        assert (await repo.get(record.run_id))["total_tokens"] == 15

    @pytest.mark.anyio
    async def test_progress_write_cannot_resurrect_a_finished_run(self, repo):
        """A late flush from a straggling task must not overwrite a terminal row."""
        manager = RunManager(store=repo)
        record = await manager.create("t1")
        await manager.set_status(record.run_id, RunStatus.running)

        journal = RunJournal(record.run_id, "t1", _FakeEventStore(), track_token_usage=True)
        journal.on_llm_end(_FakeResponse([_ai("m1", 100, 50)]), run_id="lc-1")
        await manager.update_run_completion(record.run_id, status=RunStatus.success.value, **journal.get_completion_data())

        await manager.update_run_progress(record.run_id, total_tokens=999999)

        row = await repo.get(record.run_id)
        assert row["status"] == "success"
        assert row["total_tokens"] == 150
