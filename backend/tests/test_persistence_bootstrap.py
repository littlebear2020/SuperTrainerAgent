"""Guard tests for deerflow.persistence.bootstrap.

Pins ``_BASELINE_TABLE_NAMES`` and ``_BASELINE_INDEX_NAMES`` against the
baseline revision's actual output, and verifies the three-branch decision
table. Mirrors the upstream test contract so editing the baseline revision
without updating the constants (or vice versa) fires these tests.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

import deerflow.persistence.models  # noqa: F401 — populate Base.metadata
from deerflow.persistence.base import Base
from deerflow.persistence.bootstrap import (
    _BASELINE_INDEX_NAMES,
    _BASELINE_TABLE_NAMES,
    _decide_state,
    _get_alembic_config,
    _get_head_revision,
    _run_baseline_create_all_sync,
    _upgrade,
    bootstrap_schema,
)

asyncio_test = pytest.mark.asyncio


def _url(tmp_path: Path, name: str = "test.db") -> str:
    return f"sqlite+aiosqlite:///{(tmp_path / name).as_posix()}"


async def _table_names(engine) -> set[str]:
    async with engine.connect() as conn:
        return await conn.run_sync(lambda c: set(sa.inspect(c).get_table_names()))


async def _alembic_version(engine) -> str | None:
    async with engine.connect() as conn:
        try:
            row = await conn.execute(sa.text("SELECT version_num FROM alembic_version"))
            return row.scalar()
        except Exception:
            return None


# ---------------------------------------------------------------------------
# _decide_state unit tests (sync, no DB needed)
# ---------------------------------------------------------------------------


class TestDecideState:
    def test_empty(self):
        assert _decide_state({"has_alembic_version": False, "has_deerflow_tables": False}) == "empty"

    def test_legacy(self):
        assert _decide_state({"has_alembic_version": False, "has_deerflow_tables": True}) == "legacy"

    def test_versioned(self):
        assert _decide_state({"has_alembic_version": True, "has_deerflow_tables": True}) == "versioned"

    def test_versioned_no_tables(self):
        # alembic_version present but no DeerFlow tables (edge case) → versioned
        assert _decide_state({"has_alembic_version": True, "has_deerflow_tables": False}) == "versioned"


# ---------------------------------------------------------------------------
# Guard: _BASELINE_TABLE_NAMES must match what the baseline revision creates
# ---------------------------------------------------------------------------


@asyncio_test
async def test_baseline_table_names_constant_matches_baseline(tmp_path: Path) -> None:
    """_BASELINE_TABLE_NAMES must equal the set of tables the baseline revision creates."""
    engine = create_async_engine(_url(tmp_path))
    try:
        cfg = _get_alembic_config(engine)
        await asyncio.to_thread(_upgrade, cfg, "d9e0f1a2b3c4")  # P0-5: baseline revision itself, not stamp point

        async with engine.connect() as conn:
            reflected = await conn.run_sync(lambda c: set(sa.inspect(c).get_table_names()))
        # Remove alembic's own bookkeeping table and the 4 pre-baseline chain tables
        # (unified_tasks, writer_docs, writer_doc_versions, writer_doc_shares) which
        # the baseline revision does NOT create (they were created by earlier revisions).
        reflected.discard("alembic_version")
        pre_chain = {"unified_tasks", "writer_docs", "writer_doc_versions", "writer_doc_shares"}
        reflected -= pre_chain

        assert reflected == _BASELINE_TABLE_NAMES, (
            f"_BASELINE_TABLE_NAMES drifted from baseline revision output:\n  only-in-revision={sorted(reflected - _BASELINE_TABLE_NAMES)}\n  only-in-constant={sorted(_BASELINE_TABLE_NAMES - reflected)}"
        )
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Guard: _BASELINE_INDEX_NAMES must match what the baseline revision creates
# ---------------------------------------------------------------------------


@asyncio_test
async def test_baseline_index_names_constant_matches_baseline(tmp_path: Path) -> None:
    """_BASELINE_INDEX_NAMES must equal the set of indexes the baseline revision creates."""
    engine = create_async_engine(_url(tmp_path))
    try:
        cfg = _get_alembic_config(engine)
        await asyncio.to_thread(_upgrade, cfg, "d9e0f1a2b3c4")  # P0-5: baseline revision itself, not stamp point

        async with engine.connect() as conn:
            all_indexes: set[str] = set()
            for table_name in _BASELINE_TABLE_NAMES:
                indexes = await conn.run_sync(lambda c, t=table_name: {ix["name"] for ix in sa.inspect(c).get_indexes(t)})
                all_indexes.update(indexes)

        all_indexes.discard("ix_alembic_version")

        assert all_indexes == _BASELINE_INDEX_NAMES, (
            f"_BASELINE_INDEX_NAMES drifted from baseline revision output:\n  only-in-revision={sorted(all_indexes - _BASELINE_INDEX_NAMES)}\n  only-in-constant={sorted(_BASELINE_INDEX_NAMES - all_indexes)}"
        )
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Guard: legacy backfill must not create post-baseline tables
# ---------------------------------------------------------------------------


@asyncio_test
async def test_legacy_backfill_skips_non_baseline_tables(tmp_path: Path) -> None:
    """Legacy backfill must not create tables outside _BASELINE_TABLE_NAMES."""
    phantom_name = "phantom_future_table_for_test"
    phantom = sa.Table(
        phantom_name,
        Base.metadata,
        sa.Column("id", sa.Integer, primary_key=True),
    )
    try:
        engine = create_async_engine(_url(tmp_path))
        try:
            async with engine.begin() as conn:
                await conn.run_sync(_run_baseline_create_all_sync)

            async with engine.connect() as conn:
                tables = await conn.run_sync(lambda c: set(sa.inspect(c).get_table_names()))

            assert phantom_name not in tables, f"legacy backfill leaked {phantom_name!r}; a future op.create_table({phantom_name!r}) revision would now collide"
            assert "runs" in tables
        finally:
            await engine.dispose()
    finally:
        Base.metadata.remove(phantom)


# ---------------------------------------------------------------------------
# Three-branch integration tests
# ---------------------------------------------------------------------------


@asyncio_test
async def test_empty_branch(tmp_path: Path) -> None:
    """Empty DB: create_all + stamp head."""
    engine = create_async_engine(_url(tmp_path))
    try:
        await bootstrap_schema(engine, backend="sqlite")
        tables = await _table_names(engine)
        ver = await _alembic_version(engine)
        assert ver == _get_head_revision()
        assert "runs" in tables
        assert "users" in tables
        assert "channel_configs" in tables
        # LangGraph tables must NOT be created
        assert "checkpoints" not in tables
        assert "writes" not in tables
    finally:
        await engine.dispose()


@asyncio_test
async def test_legacy_branch(tmp_path: Path) -> None:
    """Legacy DB (DeerFlow tables, no alembic_version): backfill + stamp baseline + upgrade head."""
    engine = create_async_engine(_url(tmp_path))
    try:
        # Seed with a subset of DeerFlow tables (simulates old deployment)
        async with engine.begin() as conn:
            await conn.run_sync(
                lambda c: Base.metadata.create_all(
                    c,
                    tables=[
                        Base.metadata.tables["runs"],
                        Base.metadata.tables["threads_meta"],
                        Base.metadata.tables["users"],
                    ],
                )
            )
        # No alembic_version yet
        assert await _alembic_version(engine) is None

        await bootstrap_schema(engine, backend="sqlite")

        tables = await _table_names(engine)
        ver = await _alembic_version(engine)
        assert ver == _get_head_revision()
        assert "channel_configs" in tables
        assert "voice_twins" in tables
        # Row counts must survive
        async with engine.connect() as conn:
            count = (await conn.execute(sa.text("SELECT COUNT(*) FROM runs"))).scalar()
        assert count == 0  # seeded empty
    finally:
        await engine.dispose()


@asyncio_test
async def test_versioned_branch_is_noop(tmp_path: Path) -> None:
    """Versioned DB (already at head): upgrade head is a no-op."""
    engine = create_async_engine(_url(tmp_path))
    try:
        await bootstrap_schema(engine, backend="sqlite")
        ver_before = await _alembic_version(engine)
        tables_before = await _table_names(engine)

        # Second call must be a no-op
        await bootstrap_schema(engine, backend="sqlite")
        ver_after = await _alembic_version(engine)
        tables_after = await _table_names(engine)

        assert ver_before == ver_after == _get_head_revision()
        assert tables_before == tables_after
    finally:
        await engine.dispose()


@asyncio_test
async def test_writer_doc_versions_unique_constraint_enforced(tmp_path: Path) -> None:
    """UNIQUE(doc_id, version_number) must be enforced after the full chain.

    Regression guard: migration a1b2c3d4e5f6 created writer_doc_versions with
    only a NON-unique index, and the ensure_writer_versions_unique startup
    patch compensated at runtime. That patch was retired in batch 3, so
    revision a0b1c2d3e4f5 must permanently enforce the constraint.
    """
    engine = create_async_engine(_url(tmp_path))
    try:
        cfg = _get_alembic_config(engine)
        await asyncio.to_thread(_upgrade, cfg, "head")

        # The unique index must exist and be marked unique.
        async with engine.connect() as conn:
            indexes = await conn.run_sync(lambda c: sa.inspect(c).get_indexes("writer_doc_versions"))
        unique_idx = [ix for ix in indexes if ix["name"] == "uq_wdv_doc_version"]
        assert unique_idx, f"uq_wdv_doc_version missing; got {[ix['name'] for ix in indexes]}"
        assert unique_idx[0]["unique"], "uq_wdv_doc_version exists but is not UNIQUE"

        # Inserting a duplicate (doc_id, version_number) must fail.
        insert_version = "INSERT INTO writer_doc_versions (id, doc_id, version_number, title, content_json, snapshot_reason, created_at) VALUES (:id, 'd1', 1, 't', '{}', 'manual', CURRENT_TIMESTAMP)"
        async with engine.begin() as conn:
            await conn.execute(sa.text("INSERT INTO writer_docs (id, user_id, title, content_json, created_at, updated_at) VALUES ('d1', 'u1', 't', '{}', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"))
            await conn.execute(sa.text(insert_version), {"id": "v1"})

        with pytest.raises(IntegrityError):
            async with engine.begin() as conn:
                await conn.execute(sa.text(insert_version), {"id": "v2"})
    finally:
        await engine.dispose()


@asyncio_test
async def test_head_revision_is_reachable(tmp_path: Path) -> None:
    """The full chain from scratch must reach head without errors."""
    engine = create_async_engine(_url(tmp_path))
    try:
        cfg = _get_alembic_config(engine)
        await asyncio.to_thread(_upgrade, cfg, "head")
        ver = await _alembic_version(engine)
        assert ver == _get_head_revision()
    finally:
        await engine.dispose()


@asyncio_test
async def test_operation_kind_backfills_existing_rows(tmp_path: Path) -> None:
    """T21: adding ``runs.operation_kind`` must classify old rows as real runs.

    The column is NOT NULL and the dev DB held 869 rows when it was added, so
    the server default has to supply their value. A nullable-then-backfill
    workaround (what the old ``ensure_*_schema`` patches did) would leave
    exactly the drift this revision was written to avoid.
    """
    engine = create_async_engine(_url(tmp_path))
    try:
        cfg = _get_alembic_config(engine)
        # Stop one revision short, seed a row, then apply the new revision.
        await asyncio.to_thread(_upgrade, cfg, "a0b1c2d3e4f5")
        async with engine.begin() as conn:
            columns = await conn.run_sync(lambda c: {col["name"] for col in sa.inspect(c).get_columns("runs")})
            assert "operation_kind" not in columns, "precondition: column should not exist yet"
            await conn.execute(
                sa.text(
                    "INSERT INTO runs (run_id, thread_id, status, multitask_strategy, metadata_json, kwargs_json,"
                    " message_count, total_input_tokens, total_output_tokens, total_tokens, llm_call_count,"
                    " lead_agent_tokens, subagent_tokens, middleware_tokens, token_usage_by_model, created_at, updated_at)"
                    " VALUES ('legacy-1', 't1', 'success', 'reject', '{}', '{}', 1, 20, 22, 42, 1, 42, 0, 0, '{}',"
                    " CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )

        await asyncio.to_thread(_upgrade, cfg, "b2c3d4e5f6a7")

        async with engine.connect() as conn:
            kind = (await conn.execute(sa.text("SELECT operation_kind FROM runs WHERE run_id = 'legacy-1'"))).scalar()
            column = next(col for col in await conn.run_sync(lambda c: sa.inspect(c).get_columns("runs")) if col["name"] == "operation_kind")
        assert kind == "run", "pre-existing rows must be treated as real runs"
        assert column["nullable"] is False
    finally:
        await engine.dispose()


@asyncio_test
async def test_operation_kind_revision_is_idempotent(tmp_path: Path) -> None:
    """safe_add_column must no-op when a manual ALTER already added the column."""
    engine = create_async_engine(_url(tmp_path))
    try:
        cfg = _get_alembic_config(engine)
        await asyncio.to_thread(_upgrade, cfg, "a0b1c2d3e4f5")
        async with engine.begin() as conn:
            await conn.execute(sa.text("ALTER TABLE runs ADD COLUMN operation_kind VARCHAR(32) NOT NULL DEFAULT 'run'"))

        # Must not raise "duplicate column name".
        await asyncio.to_thread(_upgrade, cfg, "b2c3d4e5f6a7")
        assert await _alembic_version(engine) == "b2c3d4e5f6a7"
    finally:
        await engine.dispose()


@asyncio_test
async def test_llm_error_fallback_column_reaches_existing_rows(tmp_path: Path) -> None:
    """C1 regression: the ``llm_error_fallback`` column (P1-9, 7b709ac4f) shipped
    on RunRow without a revision.存量库 reach schema via ``upgrade head``, so
    they never got the column → ``update_run_completion`` raised
    ``OperationalError("no such column")`` which RunManager/worker swallow,
    silently dropping token/status persistence. Revision c8d9e0f1a2b3 closes it.

    Mirrors test_operation_kind_backfills_existing_rows: stop one revision short,
    seed a legacy run, then upgrade and prove the column is live + writable.
    """
    engine = create_async_engine(_url(tmp_path))
    try:
        cfg = _get_alembic_config(engine)
        # Stop one revision short of c8d9e0f1a2b3 (the llm_error_fallback add).
        await asyncio.to_thread(_upgrade, cfg, "c7d8e9f0a1b2")
        async with engine.begin() as conn:
            columns = await conn.run_sync(lambda c: {col["name"] for col in sa.inspect(c).get_columns("runs")})
            assert "llm_error_fallback" not in columns, "precondition: column should not exist yet"
            # Seed a legacy run that predates the column (operation_kind has a
            # server_default so it need not be listed).
            await conn.execute(
                sa.text(
                    "INSERT INTO runs (run_id, thread_id, status, multitask_strategy, metadata_json, kwargs_json,"
                    " message_count, total_input_tokens, total_output_tokens, total_tokens, llm_call_count,"
                    " lead_agent_tokens, subagent_tokens, middleware_tokens, token_usage_by_model, created_at, updated_at)"
                    " VALUES ('legacy-fb', 't1', 'success', 'reject', '{}', '{}', 1, 20, 22, 42, 1, 42, 0, 0, '{}',"
                    " CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )

        # Apply the revision that adds the column.
        await asyncio.to_thread(_upgrade, cfg, "head")

        async with engine.connect() as conn:
            col = next(col for col in await conn.run_sync(lambda c: sa.inspect(c).get_columns("runs")) if col["name"] == "llm_error_fallback")
            assert col["nullable"] is True, "column must be nullable (legacy rows backfill to NULL ≈ no fallback)"
            # The production failure mode is UPDATE touching this column raising
            # "no such column". Prove it is writable and reads back.
            await conn.execute(sa.text("UPDATE runs SET llm_error_fallback = 1 WHERE run_id = 'legacy-fb'"))
            flagged = (await conn.execute(sa.text("SELECT llm_error_fallback FROM runs WHERE run_id = 'legacy-fb'"))).scalar()
        # SQLite stores BOOLEAN as an integer (1/0); the column being writable
        # and reading back a truthy value is the regression signal — the
        # original failure was UPDATE raising "no such column".
        assert flagged in (1, True), f"llm_error_fallback not persisted: {flagged!r}"
        assert await _alembic_version(engine) == _get_head_revision()
    finally:
        await engine.dispose()


@asyncio_test
async def test_llm_error_fallback_revision_is_idempotent(tmp_path: Path) -> None:
    """safe_add_column must no-op when a manual ALTER already added the column
    (dev DBs / hotfixes that pre-empted the revision)."""
    engine = create_async_engine(_url(tmp_path))
    try:
        cfg = _get_alembic_config(engine)
        await asyncio.to_thread(_upgrade, cfg, "c7d8e9f0a1b2")
        async with engine.begin() as conn:
            await conn.execute(sa.text("ALTER TABLE runs ADD COLUMN llm_error_fallback BOOLEAN"))

        # Must not raise "duplicate column name".
        await asyncio.to_thread(_upgrade, cfg, "head")
        assert await _alembic_version(engine) == _get_head_revision()
    finally:
        await engine.dispose()
