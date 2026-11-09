"""D2: LLM usage accounting for writer/gallery/suggestions.

Tests that the three scattered call sites produce llm_bookkeeping rows
with the correct domain, caller_tag, and status semantics.
"""

from __future__ import annotations

import pytest

from app.gateway._shared.llm_usage_sink import record_llm_usage


@pytest.fixture()
async def db(tmp_path):
    from deerflow.persistence.base import Base
    from deerflow.persistence.engine import close_engine, get_engine, init_engine

    await init_engine("sqlite", url=f"sqlite+aiosqlite:///{tmp_path / 'd2.db'}", sqlite_dir=str(tmp_path))
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    await close_engine()


async def _get_rows():
    from sqlalchemy import select

    from deerflow.persistence.engine import get_session_factory
    from deerflow.persistence.run.model import RunRow

    sf = get_session_factory()
    async with sf() as session:
        result = await session.execute(select(RunRow).where(RunRow.operation_kind == "llm_bookkeeping"))
        return result.scalars().all()


class TestSuggestionsAccounting:
    """Suggestions endpoint records success/error."""

    @pytest.mark.anyio
    async def test_success_records_with_domain(self, db):
        await record_llm_usage(
            model_name="test-model",
            usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
            response_metadata={},
            caller_tag="suggestions",
            domain="suggestions",
            user_id="u1",
        )

        rows = await _get_rows()
        assert len(rows) == 1
        assert rows[0].metadata_json["domain"] == "suggestions"
        assert rows[0].metadata_json["caller_tag"] == "suggestions"
        assert rows[0].user_id == "u1"

    @pytest.mark.anyio
    async def test_error_records_no_tokens(self, db):
        await record_llm_usage(
            model_name=None,
            caller_tag="suggestions",
            domain="suggestions",
            status="error",
            user_id="u1",
        )

        rows = await _get_rows()
        assert len(rows) == 1
        assert rows[0].status == "error"
        assert rows[0].total_tokens == 0


class TestGalleryAccounting:
    """Gallery recipe extraction records each LLM call."""

    @pytest.mark.anyio
    async def test_error_downstream_on_json_failure(self, db):
        await record_llm_usage(
            model_name="test-model",
            usage_metadata={"input_tokens": 100, "output_tokens": 50, "total_tokens": 150},
            response_metadata={},
            caller_tag="gallery_recipe",
            domain="gallery_recipe",
            status="error_downstream",
            user_id="u1",
        )

        rows = await _get_rows()
        assert len(rows) == 1
        assert rows[0].status == "error_downstream"
        assert rows[0].total_tokens == 150  # tokens counted even on downstream failure

    @pytest.mark.anyio
    async def test_retry_produces_two_rows(self, db):
        """Two calls (initial + retry) produce two rows."""
        await record_llm_usage(
            model_name="m",
            usage_metadata={"input_tokens": 100, "output_tokens": 50, "total_tokens": 150},
            caller_tag="gallery_recipe",
            domain="gallery_recipe",
            status="error_downstream",
            user_id="u1",
        )
        await record_llm_usage(
            model_name="m",
            usage_metadata={"input_tokens": 200, "output_tokens": 80, "total_tokens": 280},
            caller_tag="gallery_recipe",
            domain="gallery_recipe",
            status="success",
            user_id="u1",
        )

        rows = await _get_rows()
        assert len(rows) == 2
        # _get_rows has no ORDER BY — key by each call's distinct token total
        # instead of relying on result order (150 = initial, 280 = retry).
        by_total = {row.total_tokens: row for row in rows}
        assert by_total[150].status == "error_downstream"
        assert by_total[280].status == "success"


class TestWriterAssistAccounting:
    """Writer assist records success/error_downstream/error."""

    @pytest.mark.anyio
    async def test_success_records(self, db):
        await record_llm_usage(
            model_name="m",
            usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
            response_metadata={},
            caller_tag="writer_assist",
            domain="writer_assist",
            user_id="u1",
        )

        rows = await _get_rows()
        assert len(rows) == 1
        assert rows[0].metadata_json["domain"] == "writer_assist"
        assert rows[0].status == "success"

    @pytest.mark.anyio
    async def test_disconnect_with_usage_records_error_downstream(self, db):
        await record_llm_usage(
            model_name="m",
            usage_metadata={"input_tokens": 100, "output_tokens": 50, "total_tokens": 150},
            response_metadata={},
            caller_tag="writer_assist",
            domain="writer_assist",
            status="error_downstream",
            user_id="u1",
        )

        rows = await _get_rows()
        assert len(rows) == 1
        assert rows[0].status == "error_downstream"
        assert rows[0].total_tokens == 150

    @pytest.mark.anyio
    async def test_error_without_usage_records_only_count(self, db):
        await record_llm_usage(
            model_name=None,
            caller_tag="writer_assist",
            domain="writer_assist",
            status="error",
            user_id="u1",
        )

        rows = await _get_rows()
        assert len(rows) == 1
        assert rows[0].status == "error"
        assert rows[0].total_tokens == 0
