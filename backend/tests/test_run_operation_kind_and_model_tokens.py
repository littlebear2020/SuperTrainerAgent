"""Tests for the T21 persistence plumbing.

Two additions the console depends on:

1. ``runs.operation_kind`` carried end to end — RunRecord → RunManager.create
   → RunStore.put → row → ``_record_from_store``. The console filters on this
   column, so a break anywhere in that chain would silently drop every run
   from the dashboard (or, worse, let a non-run row inflate spend).
2. ``token_usage_by_model`` accumulated per model by ``RunJournal`` and
   persisted on completion. Without a per-model split, a run whose subagents
   used a different model gets priced entirely at the lead's rate.
"""

from __future__ import annotations

import pytest
from _db_helpers import sqlite_repo_fixture
from langchain_core.messages import AIMessage

from deerflow.persistence.run import RunRepository
from deerflow.runtime.journal import RunJournal
from deerflow.runtime.runs.manager import RunManager
from deerflow.runtime.runs.schemas import RunStatus
from deerflow.runtime.runs.store.memory import MemoryRunStore


class _FakeEventStore:
    def __init__(self):
        self.batches = []

    async def put_batch(self, batch):
        self.batches.append(batch)


class _FakeResponse:
    """Minimal LLMResult stand-in: RunJournal only walks ``.generations``."""

    def __init__(self, messages):
        self.generations = [[type("Gen", (), {"message": m})() for m in messages]]


def _ai(model: str, input_tokens: int, output_tokens: int, *, cache_read: int | None = None, msg_id: str = "m", amount: float | None = None) -> AIMessage:
    usage = {"input_tokens": input_tokens, "output_tokens": output_tokens, "total_tokens": input_tokens + output_tokens}
    if cache_read is not None:
        usage["input_token_details"] = {"cache_read": cache_read}
    response_metadata: dict = {"model_name": model}
    if amount is not None:
        # Non-streaming path: langchain flattens llm_output["token_usage"]
        # into response_metadata, so amount ends up under token_usage.
        response_metadata["token_usage"] = {"amount": str(amount)}
    return AIMessage(content="x", id=msg_id, usage_metadata=usage, response_metadata=response_metadata)


# ---------------------------------------------------------------------------
# operation_kind
# ---------------------------------------------------------------------------

# Per-test SQLite engine + RunRepository; engine closed in ``finally`` even
# when an assertion fails (see tests/_db_helpers.py).
repo = sqlite_repo_fixture(RunRepository)


class TestOperationKind:
    @pytest.mark.anyio
    async def test_sql_store_defaults_to_run(self, repo):
        await repo.put("r1", thread_id="t1")
        assert (await repo.get("r1"))["operation_kind"] == "run"

    @pytest.mark.anyio
    async def test_sql_store_round_trips_explicit_kind(self, repo):
        await repo.put("r1", thread_id="t1", operation_kind="checkpoint_write")
        assert (await repo.get("r1"))["operation_kind"] == "checkpoint_write"

    @pytest.mark.anyio
    async def test_none_coerced_to_run_never_null(self, repo):
        """The column is NOT NULL; a None from a sloppy caller must not violate it."""
        await repo.put("r1", thread_id="t1", operation_kind=None)
        assert (await repo.get("r1"))["operation_kind"] == "run"

    @pytest.mark.anyio
    async def test_memory_store_records_kind(self):
        store = MemoryRunStore()
        await store.put("r1", thread_id="t1")
        assert (await store.get("r1"))["operation_kind"] == "run"

    @pytest.mark.anyio
    async def test_manager_create_persists_run_kind(self, repo):
        """RunManager.create must write "run" — the console filters on it."""
        manager = RunManager(store=repo)
        record = await manager.create("t1")
        assert record.operation_kind == "run"
        assert (await repo.get(record.run_id))["operation_kind"] == "run"

    @pytest.mark.anyio
    async def test_manager_create_or_reject_persists_run_kind(self, repo):
        manager = RunManager(store=repo)
        record = await manager.create_or_reject("t1")
        assert (await repo.get(record.run_id))["operation_kind"] == "run"

    @pytest.mark.anyio
    async def test_record_from_store_reads_kind(self):
        record = RunManager._record_from_store({"run_id": "r", "thread_id": "t", "operation_kind": "artifact_write"})
        assert record.operation_kind == "artifact_write"

    @pytest.mark.anyio
    async def test_record_from_store_defaults_legacy_rows_to_run(self):
        """Rows written before the column existed hydrate as real runs."""
        assert RunManager._record_from_store({"run_id": "r", "thread_id": "t"}).operation_kind == "run"
        assert RunManager._record_from_store({"run_id": "r", "thread_id": "t", "operation_kind": None}).operation_kind == "run"


# ---------------------------------------------------------------------------
# Per-model token accumulation
# ---------------------------------------------------------------------------


class TestTokensByModel:
    def test_buckets_per_model(self):
        journal = RunJournal("r", "t", _FakeEventStore(), track_token_usage=True)
        journal.on_llm_end(_FakeResponse([_ai("model-a", 100, 50)]), run_id="lc-1")
        journal.on_llm_end(_FakeResponse([_ai("model-b", 200, 20)]), run_id="lc-2")
        journal.on_llm_end(_FakeResponse([_ai("model-a", 10, 5)]), run_id="lc-3")

        by_model = journal.get_completion_data()["token_usage_by_model"]
        assert by_model["model-a"] == {"input_tokens": 110, "output_tokens": 55, "total_tokens": 165}
        assert by_model["model-b"] == {"input_tokens": 200, "output_tokens": 20, "total_tokens": 220}

    def test_totals_stay_consistent_with_buckets(self):
        journal = RunJournal("r", "t", _FakeEventStore(), track_token_usage=True)
        journal.on_llm_end(_FakeResponse([_ai("m1", 100, 50)]), run_id="lc-1")
        journal.on_llm_end(_FakeResponse([_ai("m2", 7, 3)]), run_id="lc-2")
        data = journal.get_completion_data()
        assert data["total_tokens"] == sum(b["total_tokens"] for b in data["token_usage_by_model"].values())

    def test_missing_model_name_lands_in_unknown(self):
        journal = RunJournal("r", "t", _FakeEventStore(), track_token_usage=True)
        message = AIMessage(content="x", id="m", usage_metadata={"input_tokens": 5, "output_tokens": 5, "total_tokens": 10})
        journal.on_llm_end(_FakeResponse([message]), run_id="lc-1")
        assert journal.get_completion_data()["token_usage_by_model"]["unknown"]["total_tokens"] == 10

    def test_cache_read_recorded_sparsely(self):
        """Absent when the provider reports none, so "no hits" stays
        distinguishable from "provider does not report hits"."""
        journal = RunJournal("r", "t", _FakeEventStore(), track_token_usage=True)
        journal.on_llm_end(_FakeResponse([_ai("m1", 100, 10, cache_read=60)]), run_id="lc-1")
        journal.on_llm_end(_FakeResponse([_ai("m2", 100, 10)]), run_id="lc-2")
        by_model = journal.get_completion_data()["token_usage_by_model"]
        assert by_model["m1"]["cache_read_tokens"] == 60
        assert "cache_read_tokens" not in by_model["m2"]

    def test_amount_recorded_sparsely(self):
        """B1a: gateway real-bill amount recorded as a sparse key, absent
        when the provider does not return it (same convention as
        cache_read_tokens)."""
        journal = RunJournal("r", "t", _FakeEventStore(), track_token_usage=True)
        journal.on_llm_end(_FakeResponse([_ai("m1", 100, 10, amount=0.001174)]), run_id="lc-1")
        journal.on_llm_end(_FakeResponse([_ai("m2", 100, 10)]), run_id="lc-2")
        by_model = journal.get_completion_data()["token_usage_by_model"]
        assert by_model["m1"]["amount"] == pytest.approx(0.001174)
        assert "amount" not in by_model["m2"]

    def test_amount_accumulates_across_calls(self):
        """Multiple calls to the same model accumulate amount."""
        journal = RunJournal("r", "t", _FakeEventStore(), track_token_usage=True)
        journal.on_llm_end(_FakeResponse([_ai("m1", 100, 10, amount=0.001)]), run_id="lc-1")
        journal.on_llm_end(_FakeResponse([_ai("m1", 100, 10, amount=0.002)]), run_id="lc-2")
        by_model = journal.get_completion_data()["token_usage_by_model"]
        assert by_model["m1"]["amount"] == pytest.approx(0.003)

    def test_amount_from_streaming_response_metadata(self):
        """B1b: streaming path writes amount into response_metadata['amount']
        (not under token_usage). The journal's two-level read should find it."""
        journal = RunJournal("r", "t", _FakeEventStore(), track_token_usage=True)
        usage = {"input_tokens": 100, "output_tokens": 10, "total_tokens": 110}
        message = AIMessage(content="x", id="m", usage_metadata=usage, response_metadata={"model_name": "m1", "amount": 0.005})
        journal.on_llm_end(_FakeResponse([message]), run_id="lc-1")
        by_model = journal.get_completion_data()["token_usage_by_model"]
        assert by_model["m1"]["amount"] == pytest.approx(0.005)

    def test_dedup_by_langchain_run_id(self):
        """LangChain can fire on_llm_end twice for one response."""
        journal = RunJournal("r", "t", _FakeEventStore(), track_token_usage=True)
        response = _FakeResponse([_ai("m1", 100, 50)])
        journal.on_llm_end(response, run_id="lc-1")
        journal.on_llm_end(response, run_id="lc-1")
        assert journal.get_completion_data()["token_usage_by_model"]["m1"]["total_tokens"] == 150

    def test_tracking_disabled_leaves_map_empty(self):
        journal = RunJournal("r", "t", _FakeEventStore(), track_token_usage=False)
        journal.on_llm_end(_FakeResponse([_ai("m1", 100, 50)]), run_id="lc-1")
        assert journal.get_completion_data()["token_usage_by_model"] == {}

    def test_subagent_records_attributed_to_their_own_model(self):
        """The reason per-model exists: subagents often run elsewhere."""
        journal = RunJournal("r", "t", _FakeEventStore(), track_token_usage=True)
        journal.on_llm_end(_FakeResponse([_ai("lead-model", 100, 100)]), run_id="lc-1")
        journal.record_external_llm_usage_records(
            [
                {
                    "source_run_id": "sub-1",
                    "caller": "subagent:researcher",
                    "input_tokens": 500,
                    "output_tokens": 250,
                    "total_tokens": 750,
                    "model_name": "sub-model",
                }
            ]
        )
        by_model = journal.get_completion_data()["token_usage_by_model"]
        assert by_model["lead-model"]["total_tokens"] == 200
        assert by_model["sub-model"] == {"input_tokens": 500, "output_tokens": 250, "total_tokens": 750}

    def test_subagent_records_without_model_land_in_unknown(self):
        journal = RunJournal("r", "t", _FakeEventStore(), track_token_usage=True)
        journal.record_external_llm_usage_records([{"source_run_id": "s1", "caller": "subagent:x", "input_tokens": 1, "output_tokens": 1, "total_tokens": 2}])
        assert journal.get_completion_data()["token_usage_by_model"]["unknown"]["total_tokens"] == 2


class TestCompletionPersistence:
    @pytest.mark.anyio
    async def test_completion_writes_map_and_stop_reason(self, repo):
        await repo.put("r1", thread_id="t1")
        await repo.update_run_completion(
            "r1",
            status="success",
            total_tokens=150,
            token_usage_by_model={"m1": {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150}},
            stop_reason="safety_capped",
        )
        row = await repo.get("r1")
        assert row["token_usage_by_model"] == {"m1": {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150}}
        assert row["stop_reason"] == "safety_capped"

    @pytest.mark.anyio
    async def test_empty_map_does_not_clobber_a_written_breakdown(self, repo):
        """A completion carrying no breakdown must not erase what a progress
        flush already persisted."""
        await repo.put("r1", thread_id="t1")
        await repo.update_run_completion("r1", status="running", token_usage_by_model={"m1": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}})
        await repo.update_run_completion("r1", status="success", token_usage_by_model={})
        assert (await repo.get("r1"))["token_usage_by_model"] == {"m1": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}}

    @pytest.mark.anyio
    async def test_stop_reason_left_alone_when_not_supplied(self, repo):
        await repo.put("r1", thread_id="t1")
        await repo.update_run_completion("r1", status="success", stop_reason="model_length_capped")
        await repo.update_run_completion("r1", status="success")
        assert (await repo.get("r1"))["stop_reason"] == "model_length_capped"

    @pytest.mark.anyio
    async def test_journal_completion_data_is_accepted_verbatim(self, repo):
        """The worker splats get_completion_data() straight into the store, so
        every key it emits must be a real parameter."""
        manager = RunManager(store=repo)
        record = await manager.create("t1")

        journal = RunJournal(record.run_id, "t1", _FakeEventStore(), track_token_usage=True)
        journal.on_llm_end(_FakeResponse([_ai("m1", 100, 50)]), run_id="lc-1")

        await manager.update_run_completion(record.run_id, status=RunStatus.success.value, **journal.get_completion_data())

        row = await repo.get(record.run_id)
        assert row["status"] == "success"
        assert row["total_tokens"] == 150
        assert row["token_usage_by_model"]["m1"]["total_tokens"] == 150

    @pytest.mark.anyio
    async def test_progress_snapshot_persists_partial_breakdown(self, repo):
        await repo.put("r1", thread_id="t1", status="running")
        await repo.update_run_progress("r1", total_tokens=50, token_usage_by_model={"m1": {"input_tokens": 30, "output_tokens": 20, "total_tokens": 50}})
        row = await repo.get("r1")
        assert row["total_tokens"] == 50
        assert row["token_usage_by_model"]["m1"]["total_tokens"] == 50


class TestPrimaryModelName:
    """`runs.model_name` had no writer at all before this batch.

    All three stores shipped an `update_model_name` method with zero callers,
    so every one of the 869 dev-DB rows has `model_name IS NULL` — the console
    showed a blank model column for all of them and could not use its
    run-level pricing fallback.
    """

    def test_none_when_no_calls_recorded(self):
        journal = RunJournal("r", "t", _FakeEventStore(), track_token_usage=True)
        assert journal.primary_model_name() is None

    def test_picks_the_biggest_consumer_not_the_first_call(self):
        journal = RunJournal("r", "t", _FakeEventStore(), track_token_usage=True)
        journal.on_llm_end(_FakeResponse([_ai("small-model", 10, 10)]), run_id="lc-1")
        journal.on_llm_end(_FakeResponse([_ai("big-model", 5_000, 5_000)]), run_id="lc-2")
        assert journal.primary_model_name() == "big-model"

    def test_prefers_a_known_model_over_the_unknown_bucket(self):
        """A provider that omits model_name must not shadow one that reports it."""
        journal = RunJournal("r", "t", _FakeEventStore(), track_token_usage=True)
        anonymous = AIMessage(content="x", id="a", usage_metadata={"input_tokens": 900_000, "output_tokens": 0, "total_tokens": 900_000})
        journal.on_llm_end(_FakeResponse([anonymous]), run_id="lc-1")
        journal.on_llm_end(_FakeResponse([_ai("named-model", 5, 5)]), run_id="lc-2")
        assert journal.primary_model_name() == "named-model"

    def test_falls_back_to_unknown_when_nothing_is_named(self):
        journal = RunJournal("r", "t", _FakeEventStore(), track_token_usage=True)
        anonymous = AIMessage(content="x", id="a", usage_metadata={"input_tokens": 5, "output_tokens": 5, "total_tokens": 10})
        journal.on_llm_end(_FakeResponse([anonymous]), run_id="lc-1")
        assert journal.primary_model_name() == "unknown"

    @pytest.mark.anyio
    async def test_manager_persists_model_name(self, repo):
        manager = RunManager(store=repo)
        record = await manager.create("t1")

        await manager.update_model_name(record.run_id, "qwen3.8-flash")

        assert record.model_name == "qwen3.8-flash"
        assert (await repo.get(record.run_id))["model_name"] == "qwen3.8-flash"

    @pytest.mark.anyio
    async def test_manager_update_survives_a_failing_store(self, repo):
        """Reporting metadata must never fail an otherwise-finished run."""
        manager = RunManager(store=repo)
        record = await manager.create("t1")

        async def boom(*args, **kwargs):
            raise RuntimeError("db gone")

        repo.update_model_name = boom
        await manager.update_model_name(record.run_id, "m1")  # must not raise
        assert record.model_name == "m1"
