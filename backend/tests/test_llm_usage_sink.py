"""C1: LLM usage sink + contextvar + _invoke_llm_bounded integration tests.

Covers:
- record_llm_usage writes a llm_bookkeeping row with correct tokens/amount
- contextvar propagation (set → get)
- error status (no tokens, just count)
- fire-and-forget doesn't crash when no loop is available
- _invoke_llm_bounded records on success and failure
- llm_bookkeeping rows don't pollute run counts (operation_kind filter)
"""

from __future__ import annotations

import asyncio

import pytest
from langchain_core.messages import AIMessage

from app.gateway._shared.llm_usage_sink import (
    extract_amount_from_metadata,
    get_llm_accounting_context,
    record_llm_usage,
    record_llm_usage_fire_and_forget,
    set_llm_accounting_context,
)

# ---------------------------------------------------------------------------
# DB fixture
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_llm_accounting_ctx():
    """Reset the LLM accounting contextvar around every test.

    ``set_llm_accounting_context`` writes a process-wide ContextVar and none
    of the tests below reset it — without this fixture the last-set value
    leaks into later tests and into other files sharing the xdist worker
    process (test_task_cost.py carries a defensive set(None) for exactly
    this). set(None) on entry + token reset on exit is the idiom from
    ``app/gateway/_shared/llm_usage_sink.py`` itself.
    """
    from app.gateway._shared.llm_usage_sink import _accounting_ctx

    token = _accounting_ctx.set(None)
    try:
        yield
    finally:
        _accounting_ctx.reset(token)


@pytest.fixture()
async def db(tmp_path):
    from deerflow.persistence.base import Base
    from deerflow.persistence.engine import close_engine, get_engine, init_engine

    await init_engine("sqlite", url=f"sqlite+aiosqlite:///{tmp_path / 'sink.db'}", sqlite_dir=str(tmp_path))
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    await close_engine()


async def _count_bookkeeping_rows():
    from sqlalchemy import func, select

    from deerflow.persistence.engine import get_session_factory
    from deerflow.persistence.run.model import RunRow

    sf = get_session_factory()
    async with sf() as session:
        return await session.scalar(select(func.count()).select_from(RunRow).where(RunRow.operation_kind == "llm_bookkeeping")) or 0


async def _get_bookkeeping_rows():
    from sqlalchemy import select

    from deerflow.persistence.engine import get_session_factory
    from deerflow.persistence.run.model import RunRow

    sf = get_session_factory()
    async with sf() as session:
        result = await session.execute(select(RunRow).where(RunRow.operation_kind == "llm_bookkeeping"))
        return result.scalars().all()


# ---------------------------------------------------------------------------
# Contextvar
# ---------------------------------------------------------------------------


class TestContextVar:
    def test_set_and_get(self):
        set_llm_accounting_context("task-1", "user-1", "video_gen")
        ctx = get_llm_accounting_context()
        assert ctx is not None
        assert ctx.task_id == "task-1"
        assert ctx.user_id == "user-1"
        assert ctx.domain == "video_gen"

    def test_get_without_set_returns_none(self):
        # ContextVar default is None; after clearing it should be None
        from app.gateway._shared.llm_usage_sink import _accounting_ctx

        token = _accounting_ctx.set(None)
        try:
            assert get_llm_accounting_context() is None
        finally:
            _accounting_ctx.reset(token)


# ---------------------------------------------------------------------------
# Amount extraction
# ---------------------------------------------------------------------------


class TestExtractAmount:
    def test_reads_from_amount_key(self):
        assert extract_amount_from_metadata({"amount": 0.005}) == pytest.approx(0.005)

    def test_falls_back_to_token_usage(self):
        rm = {"token_usage": {"amount": "0.001174"}}
        assert extract_amount_from_metadata(rm) == pytest.approx(0.001174)

    def test_prefers_amount_over_token_usage(self):
        rm = {"amount": 0.01, "token_usage": {"amount": "0.02"}}
        assert extract_amount_from_metadata(rm) == pytest.approx(0.01)

    def test_returns_none_when_absent(self):
        assert extract_amount_from_metadata({"model": "gpt-4"}) is None

    def test_returns_none_on_invalid(self):
        assert extract_amount_from_metadata({"amount": "not-a-number"}) is None


# ---------------------------------------------------------------------------
# record_llm_usage
# ---------------------------------------------------------------------------


class TestRecordLlmUsage:
    @pytest.mark.anyio
    async def test_success_writes_row_with_tokens(self, db):
        set_llm_accounting_context("task-1", "user-1", "video_gen")

        await record_llm_usage(
            model_name="gemini-3.6-flash",
            usage_metadata={"input_tokens": 100, "output_tokens": 50, "total_tokens": 150},
            response_metadata={"model_name": "gemini-3.6-flash", "token_usage": {"amount": "0.001"}},
            caller_tag="video_gen_bottleneck",
        )

        rows = await _get_bookkeeping_rows()
        assert len(rows) == 1
        row = rows[0]
        assert row.operation_kind == "llm_bookkeeping"
        assert row.status == "success"
        assert row.total_tokens == 150
        assert row.model_name == "gemini-3.6-flash"
        assert row.user_id == "user-1"
        assert row.metadata_json["domain"] == "video_gen"
        assert row.metadata_json["caller_tag"] == "video_gen_bottleneck"
        # Amount in bucket
        bucket = row.token_usage_by_model.get("gemini-3.6-flash", {})
        assert bucket.get("amount") == pytest.approx(0.001)

    @pytest.mark.anyio
    async def test_error_status_no_tokens(self, db):
        set_llm_accounting_context("task-1", "user-1", "video_gen")

        await record_llm_usage(
            model_name=None,
            usage_metadata=None,
            response_metadata=None,
            caller_tag="video_gen_bottleneck",
            status="error",
        )

        rows = await _get_bookkeeping_rows()
        assert len(rows) == 1
        row = rows[0]
        assert row.status == "error"
        assert row.total_tokens == 0
        assert row.token_usage_by_model == {}

    @pytest.mark.anyio
    async def test_does_not_crash_without_context(self, db):
        from app.gateway._shared.llm_usage_sink import _accounting_ctx

        token = _accounting_ctx.set(None)
        try:
            await record_llm_usage(
                model_name="m",
                usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
                response_metadata={},
                caller_tag="test",
            )
        finally:
            _accounting_ctx.reset(token)

        rows = await _get_bookkeeping_rows()
        assert len(rows) == 1
        # Should still write with fallback user/domain
        assert rows[0].metadata_json["domain"] == "unknown"

    @pytest.mark.anyio
    async def test_does_not_crash_without_db(self):
        # With no engine initialized, get_session_factory returns None
        # and the function should silently return.
        await record_llm_usage(
            model_name="m",
            usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
            caller_tag="test",
        )
        # No exception = pass


# ---------------------------------------------------------------------------
# Fire-and-forget
# ---------------------------------------------------------------------------


class TestFireAndForget:
    def test_does_not_crash_without_running_loop(self):
        # Called from a sync context with no event loop
        record_llm_usage_fire_and_forget(
            model_name="m",
            usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
            caller_tag="test",
        )
        # Should not raise

    @pytest.mark.anyio
    async def test_schedules_task_when_loop_running(self, db):
        set_llm_accounting_context("t", "u", "test")
        record_llm_usage_fire_and_forget(
            model_name="m",
            usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
            caller_tag="test",
        )
        # Give the scheduled task time to complete
        await asyncio.sleep(0.1)
        count = await _count_bookkeeping_rows()
        assert count == 1


# ---------------------------------------------------------------------------
# _invoke_llm_bounded integration
# ---------------------------------------------------------------------------


class TestInvokeLlmBounded:
    @pytest.mark.anyio
    async def test_success_records_usage(self, db):
        from app.gateway.video_gen.orchestration import _invoke_llm_bounded

        set_llm_accounting_context("task-1", "user-1", "video_gen")

        class FakeLLM:
            async def ainvoke(self, messages):
                return AIMessage(
                    content="hello",
                    id="m1",
                    usage_metadata={"input_tokens": 100, "output_tokens": 50, "total_tokens": 150},
                    response_metadata={"model_name": "test-model", "token_usage": {"amount": "0.002"}},
                )

        result = await _invoke_llm_bounded(FakeLLM(), [])
        assert result.content == "hello"

        rows = await _get_bookkeeping_rows()
        assert len(rows) == 1
        assert rows[0].status == "success"
        assert rows[0].total_tokens == 150

    @pytest.mark.anyio
    async def test_timeout_records_error(self, db):
        from app.gateway.video_gen.orchestration import _invoke_llm_bounded

        set_llm_accounting_context("task-1", "user-1", "video_gen")

        class SlowLLM:
            async def ainvoke(self, messages):
                await asyncio.sleep(100)

        with pytest.raises(RuntimeError, match="超时"):
            await _invoke_llm_bounded(SlowLLM(), [], timeout=0.01)

        rows = await _get_bookkeeping_rows()
        assert len(rows) == 1
        assert rows[0].status == "error"
        assert rows[0].total_tokens == 0

    @pytest.mark.anyio
    async def test_generic_exception_records_error(self, db):
        from app.gateway.video_gen.orchestration import _invoke_llm_bounded

        set_llm_accounting_context("task-1", "user-1", "video_gen")

        class CrashingLLM:
            async def ainvoke(self, messages):
                raise ValueError("API 500")

        with pytest.raises(ValueError, match="API 500"):
            await _invoke_llm_bounded(CrashingLLM(), [])

        rows = await _get_bookkeeping_rows()
        assert len(rows) == 1
        assert rows[0].status == "error"

    @pytest.mark.anyio
    async def test_bookkeeping_rows_excluded_from_run_counts(self, db):
        """Console queries filter on operation_kind='run' — bookkeeping rows
        must not inflate counts."""
        from sqlalchemy import func, select

        from deerflow.persistence.engine import get_session_factory
        from deerflow.persistence.run.model import RunRow

        set_llm_accounting_context("task-1", "user-1", "video_gen")

        await record_llm_usage(
            model_name="m",
            usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
            caller_tag="test",
        )

        sf = get_session_factory()
        async with sf() as session:
            run_count = await session.scalar(select(func.count()).select_from(RunRow).where(RunRow.operation_kind == "run")) or 0
            bookkeeping_count = await session.scalar(select(func.count()).select_from(RunRow).where(RunRow.operation_kind == "llm_bookkeeping")) or 0

        assert run_count == 0
        assert bookkeeping_count == 1
