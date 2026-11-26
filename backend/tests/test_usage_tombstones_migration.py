"""usage_tombstones 迁移：从上一个链头升级、回滚（BUG-01）。"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command as alembic_command
from sqlalchemy.ext.asyncio import create_async_engine

from deerflow.persistence.bootstrap import _get_alembic_config, _get_head_revision, _upgrade

PREVIOUS_HEAD = "p9c0d1e2f3a4"
TOMBSTONES_REVISION = "q0d1e2f3a4b5"

asyncio_test = pytest.mark.asyncio


def _url(tmp_path: Path) -> str:
    return f"sqlite+aiosqlite:///{(tmp_path / 'tombstones_migration.db').as_posix()}"


async def _inspect(engine, fn):
    async with engine.connect() as conn:
        return await conn.run_sync(lambda c: fn(sa.inspect(c)))


@asyncio_test
async def test_usage_tombstones_revision_upgrades_previous_head(tmp_path: Path) -> None:
    engine = create_async_engine(_url(tmp_path))
    try:
        cfg = _get_alembic_config(engine)
        await asyncio.to_thread(_upgrade, cfg, PREVIOUS_HEAD)
        assert "usage_tombstones" not in await _inspect(engine, lambda i: set(i.get_table_names()))

        await asyncio.to_thread(_upgrade, cfg, "head")

        assert "usage_tombstones" in await _inspect(engine, lambda i: set(i.get_table_names()))
        indexes = await _inspect(engine, lambda i: {ix["name"] for ix in i.get_indexes("usage_tombstones")})
        assert "ix_usage_tombstones_user_id" in indexes
        columns = await _inspect(engine, lambda i: {col["name"]: col["nullable"] for col in i.get_columns("usage_tombstones")})
        assert columns == {
            "kind": False,
            "subject_id": False,
            "user_id": False,
            "capability": False,
            "title": True,
            "status": False,
            "subject_created_at": False,
            "deleted_at": False,
        }
        assert _get_head_revision() == TOMBSTONES_REVISION
    finally:
        await engine.dispose()


@asyncio_test
async def test_usage_tombstones_revision_downgrades_cleanly(tmp_path: Path) -> None:
    """回滚路径：先在新镜像里 downgrade 到上一个链头，旧镜像的 upgrade head 才找得到自己的 revision。"""
    engine = create_async_engine(_url(tmp_path))
    try:
        cfg = _get_alembic_config(engine)
        await asyncio.to_thread(_upgrade, cfg, "head")
        assert "usage_tombstones" in await _inspect(engine, lambda i: set(i.get_table_names()))

        await asyncio.to_thread(alembic_command.downgrade, cfg, PREVIOUS_HEAD)

        assert "usage_tombstones" not in await _inspect(engine, lambda i: set(i.get_table_names()))
    finally:
        await engine.dispose()
