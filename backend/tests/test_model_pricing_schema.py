"""ORM + migration contract for the ``model_pricing`` table (B1).

Per-model prices live in the DB rather than ``config.yaml`` because the
container mounts the yaml read-only (``:ro``): a config-file-backed pricing
admin would require changing the compose file, whereas a table does not.

The one semantic worth pinning explicitly is
``input_cache_hit_per_million``'s **nullability**. ``null`` and ``0`` mean
different things:

* ``null`` — no hit price configured; cache-hit input tokens bill at the full
  miss price (the conservative reading, already implemented in
  ``console._token_cost`` and pinned by
  ``test_missing_hit_price_bills_cache_at_full_input_price``).
* ``0``    — hits are genuinely free.

Collapsing null into 0 would silently under-report spend for every deployment
that has not configured a hit price, so the column must stay nullable and the
value must round-trip as ``None``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import deerflow.persistence.models  # noqa: F401 — populate Base.metadata
from deerflow.persistence.bootstrap import _get_alembic_config, _upgrade
from deerflow.persistence.model_pricing.model import ModelPricingRow

asyncio_test = pytest.mark.asyncio


def _url(tmp_path: Path, name: str = "pricing.db") -> str:
    return f"sqlite+aiosqlite:///{(tmp_path / name).as_posix()}"


async def _columns(engine, table: str) -> dict[str, dict]:
    async with engine.connect() as conn:
        cols = await conn.run_sync(lambda c: sa.inspect(c).get_columns(table))
    return {c["name"]: c for c in cols}


class TestModelPricingSchema:
    @asyncio_test
    async def test_migration_creates_the_table(self, tmp_path: Path) -> None:
        engine = create_async_engine(_url(tmp_path))
        try:
            cfg = _get_alembic_config(engine)
            await asyncio.to_thread(_upgrade, cfg, "head")

            async with engine.connect() as conn:
                tables = await conn.run_sync(lambda c: set(sa.inspect(c).get_table_names()))
            assert "model_pricing" in tables
        finally:
            await engine.dispose()

    @asyncio_test
    async def test_migration_downgrades_cleanly(self, tmp_path: Path) -> None:
        """The revision must be reversible — deploys get rolled back."""
        from alembic import command as alembic_command

        engine = create_async_engine(_url(tmp_path))
        try:
            cfg = _get_alembic_config(engine)
            await asyncio.to_thread(_upgrade, cfg, "head")

            async with engine.connect() as conn:
                before = await conn.run_sync(lambda c: set(sa.inspect(c).get_table_names()))
            assert "model_pricing" in before, "precondition: upgrade must have created it"

            await asyncio.to_thread(alembic_command.downgrade, cfg, "b2c3d4e5f6a7")

            async with engine.connect() as conn:
                tables = await conn.run_sync(lambda c: set(sa.inspect(c).get_table_names()))
            assert "model_pricing" not in tables
            # The rest of the schema must survive the rollback.
            assert "runs" in tables
        finally:
            await engine.dispose()

    @asyncio_test
    async def test_cache_hit_price_column_is_nullable(self, tmp_path: Path) -> None:
        """null != 0: null means "bill hits at the full miss price"."""
        engine = create_async_engine(_url(tmp_path))
        try:
            cfg = _get_alembic_config(engine)
            await asyncio.to_thread(_upgrade, cfg, "head")

            cols = await _columns(engine, "model_pricing")
            assert cols["input_cache_hit_per_million"]["nullable"] is True
            # The prices that must always be present stay NOT NULL.
            assert cols["input_per_million"]["nullable"] is False
            assert cols["output_per_million"]["nullable"] is False
            assert cols["currency"]["nullable"] is False
        finally:
            await engine.dispose()

    @asyncio_test
    async def test_null_cache_hit_price_round_trips_as_none(self, tmp_path: Path) -> None:
        """Not merely storable: it must come back as ``None``, never ``0.0``."""
        engine = create_async_engine(_url(tmp_path))
        try:
            cfg = _get_alembic_config(engine)
            await asyncio.to_thread(_upgrade, cfg, "head")
            sf = async_sessionmaker(engine, expire_on_commit=False)

            async with sf() as session:
                session.add(
                    ModelPricingRow(
                        model_name="doubao-seed-2-1-pro",
                        currency="CNY",
                        input_per_million=8.0,
                        output_per_million=32.0,
                        input_cache_hit_per_million=None,
                    )
                )
                await session.commit()

            async with sf() as session:
                row = await session.get(ModelPricingRow, "doubao-seed-2-1-pro")

            assert row is not None
            assert row.input_cache_hit_per_million is None
            assert row.input_per_million == 8.0
        finally:
            await engine.dispose()

    @asyncio_test
    async def test_explicit_zero_cache_hit_price_is_preserved(self, tmp_path: Path) -> None:
        """0 means "hits are free" and must not be confused with null."""
        engine = create_async_engine(_url(tmp_path))
        try:
            cfg = _get_alembic_config(engine)
            await asyncio.to_thread(_upgrade, cfg, "head")
            sf = async_sessionmaker(engine, expire_on_commit=False)

            async with sf() as session:
                session.add(
                    ModelPricingRow(
                        model_name="free-cache-model",
                        currency="CNY",
                        input_per_million=8.0,
                        output_per_million=32.0,
                        input_cache_hit_per_million=0.0,
                    )
                )
                await session.commit()

            async with sf() as session:
                row = await session.get(ModelPricingRow, "free-cache-model")

            assert row is not None
            assert row.input_cache_hit_per_million == 0.0
            assert row.input_cache_hit_per_million is not None
        finally:
            await engine.dispose()

    @asyncio_test
    async def test_model_name_is_the_primary_key(self, tmp_path: Path) -> None:
        """One price per model: a second write for the same name replaces it."""
        engine = create_async_engine(_url(tmp_path))
        try:
            cfg = _get_alembic_config(engine)
            await asyncio.to_thread(_upgrade, cfg, "head")

            async with engine.connect() as conn:
                pk = await conn.run_sync(lambda c: sa.inspect(c).get_pk_constraint("model_pricing"))
            assert pk["constrained_columns"] == ["model_name"]
        finally:
            await engine.dispose()

    @asyncio_test
    async def test_audit_columns_exist(self, tmp_path: Path) -> None:
        """Who changed a price and when — pricing is money, so it is audited."""
        engine = create_async_engine(_url(tmp_path))
        try:
            cfg = _get_alembic_config(engine)
            await asyncio.to_thread(_upgrade, cfg, "head")

            cols = await _columns(engine, "model_pricing")
            assert "updated_at" in cols
            assert "updated_by" in cols
        finally:
            await engine.dispose()

    @asyncio_test
    async def test_table_is_not_added_to_the_legacy_baseline_backfill(self) -> None:
        """A post-baseline table must stay out of ``_BASELINE_TABLE_NAMES``.

        ``bootstrap`` restricts its legacy-branch ``create_all`` to the
        baseline set precisely so later ``op.create_table`` revisions do not
        hit "relation already exists". Adding this table there would break
        upgrades of every pre-alembic database.
        """
        from deerflow.persistence.bootstrap import _BASELINE_TABLE_NAMES

        assert "model_pricing" not in _BASELINE_TABLE_NAMES
