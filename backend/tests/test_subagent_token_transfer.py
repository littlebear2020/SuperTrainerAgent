"""Verify subagent token records flow from collector to parent RunJournal.

conftest.py mocks deerflow.subagents.executor globally to break a circular
import chain.  This test module avoids importing SubagentResult / SubagentStatus
directly — we use plain dicts and SimpleNamespace to verify the data-flow
contracts instead.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from deerflow.runtime.journal import RunJournal
from deerflow.subagents.token_collector import SubagentTokenCollector


class _FakeEventStore:
    async def put_batch(self, batch):
        pass


@pytest.fixture
def journal():
    return RunJournal("run-1", "thread-1", _FakeEventStore(), track_token_usage=True)


class TestRecordExternalLlmUsage:
    def test_records_accumulate_in_subagent_bucket(self, journal):
        records = [
            {"source_run_id": "sub-1", "caller": "subagent:general-purpose", "input_tokens": 100, "output_tokens": 50, "total_tokens": 150},
            {"source_run_id": "sub-2", "caller": "subagent:general-purpose", "input_tokens": 80, "output_tokens": 40, "total_tokens": 120},
        ]
        journal.record_external_llm_usage_records(records)
        data = journal.get_completion_data()
        assert data["subagent_tokens"] == 270
        assert data["total_tokens"] == 270
        assert data["total_input_tokens"] == 180
        assert data["total_output_tokens"] == 90

    def test_dedup_by_source_run_id(self, journal):
        records = [
            {"source_run_id": "sub-1", "caller": "subagent:gp", "input_tokens": 100, "output_tokens": 50, "total_tokens": 150},
            # Duplicate — same source_run_id
            {"source_run_id": "sub-1", "caller": "subagent:gp", "input_tokens": 100, "output_tokens": 50, "total_tokens": 150},
        ]
        journal.record_external_llm_usage_records(records)
        data = journal.get_completion_data()
        assert data["subagent_tokens"] == 150
        assert data["total_tokens"] == 150

    def test_zero_total_skipped(self, journal):
        records = [
            {"source_run_id": "sub-x", "caller": "subagent:gp", "input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
        ]
        journal.record_external_llm_usage_records(records)
        data = journal.get_completion_data()
        assert data["total_tokens"] == 0

    def test_middleware_caller_bucketed(self, journal):
        records = [
            {"source_run_id": "mw-1", "caller": "middleware:title", "input_tokens": 30, "output_tokens": 10, "total_tokens": 40},
        ]
        journal.record_external_llm_usage_records(records)
        data = journal.get_completion_data()
        assert data["middleware_tokens"] == 40
        assert data["total_tokens"] == 40

    def test_track_tokens_disabled_skips(self):
        j = RunJournal("r", "t", _FakeEventStore(), track_token_usage=False)
        records = [
            {"source_run_id": "sub-1", "caller": "subagent:gp", "input_tokens": 100, "output_tokens": 50, "total_tokens": 150},
        ]
        j.record_external_llm_usage_records(records)
        assert j.get_completion_data()["total_tokens"] == 0


class TestSubagentResultTokenRecords:
    """SubagentResult is mocked by conftest, so we verify the contract
    via the dataclass definition directly (field name and default)."""

    def test_token_records_field_exists(self):
        # Import the real module source to check the field — this only works
        # because the file is read, not imported.
        import pathlib

        source = pathlib.Path(__file__).resolve().parent.parent / "packages" / "harness" / "deerflow" / "subagents" / "executor.py"
        content = source.read_text()
        assert "token_records" in content
        assert "default_factory=list" in content


class TestSubagentTokenCollectorSnapshot:
    def test_snapshot_returns_copy(self):
        collector = SubagentTokenCollector(caller="subagent:gp")
        # Simulate on_llm_end with usage_metadata
        msg = SimpleNamespace(usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15})
        gen = SimpleNamespace(message=msg)
        response = SimpleNamespace(generations=[[gen]])
        collector.on_llm_end(response, run_id="run-abc")
        snap = collector.snapshot_records()
        assert len(snap) == 1
        assert snap[0]["total_tokens"] == 15
        assert snap[0]["caller"] == "subagent:gp"
        # Snapshot is a copy
        snap.clear()
        assert len(collector.snapshot_records()) == 1


class TestTransferHelper:
    """Test the _transfer_subagent_tokens helper via import."""

    def test_transfer_to_journal(self, journal):
        from deerflow.tools.builtins.task_tool import _transfer_subagent_tokens

        runtime = SimpleNamespace(context={"__run_journal": journal})
        records = [
            {"source_run_id": "sub-1", "caller": "subagent:gp", "input_tokens": 200, "output_tokens": 100, "total_tokens": 300},
        ]
        _transfer_subagent_tokens(runtime, records)
        data = journal.get_completion_data()
        assert data["subagent_tokens"] == 300

    def test_empty_records_noop(self, journal):
        from deerflow.tools.builtins.task_tool import _transfer_subagent_tokens

        runtime = SimpleNamespace(context={"__run_journal": journal})
        _transfer_subagent_tokens(runtime, [])
        assert journal.get_completion_data()["total_tokens"] == 0

    def test_no_runtime_noop(self):
        from deerflow.tools.builtins.task_tool import _transfer_subagent_tokens

        records = [{"source_run_id": "s1", "caller": "subagent:gp", "input_tokens": 100, "output_tokens": 50, "total_tokens": 150}]
        # Should not raise
        _transfer_subagent_tokens(None, records)

    def test_no_journal_in_context_noop(self):
        from deerflow.tools.builtins.task_tool import _transfer_subagent_tokens

        runtime = SimpleNamespace(context={"thread_id": "t1"})
        records = [{"source_run_id": "s1", "caller": "subagent:gp", "input_tokens": 100, "output_tokens": 50, "total_tokens": 150}]
        _transfer_subagent_tokens(runtime, records)

    def test_journal_exception_silenced(self):
        from deerflow.tools.builtins.task_tool import _transfer_subagent_tokens

        bad_journal = MagicMock()
        bad_journal.record_external_llm_usage_records.side_effect = RuntimeError("boom")
        runtime = SimpleNamespace(context={"__run_journal": bad_journal})
        records = [{"source_run_id": "s1", "caller": "subagent:gp", "input_tokens": 100, "output_tokens": 50, "total_tokens": 150}]
        # Should not raise
        _transfer_subagent_tokens(runtime, records)
