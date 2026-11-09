"""批次 C Phase C (2026-08-13)：external_api_calls.task_id 迁移守护。

- upgrade head 连跑两次幂等（部署每次启动走 bootstrap_schema → upgrade head）。
- 升到 head 后列+索引就位。
- downgrade -1 可回（列消失、索引消失）。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command as alembic_command
from sqlalchemy.ext.asyncio import create_async_engine

from deerflow.persistence.bootstrap import _get_alembic_config, _upgrade

REVISION = "b3d4e5f6a7c8"
INDEX_NAME = "ix_external_api_calls_task_id"


def _url(tmp_path: Path) -> str:
    return f"sqlite+aiosqlite:///{(tmp_path / 'task_cost_mig.db').as_posix()}"


async def _external_calls_columns(engine) -> dict[str, dict]:
    async with engine.connect() as conn:
        return await conn.run_sync(lambda c: {col["name"]: col for col in sa.inspect(c).get_columns("external_api_calls")})


async def _external_calls_indexes(engine) -> set[str]:
    async with engine.connect() as conn:
        return await conn.run_sync(lambda c: {ix["name"] for ix in sa.inspect(c).get_indexes("external_api_calls")})


@pytest.mark.anyio
async def test_upgrade_head_twice_is_idempotent(tmp_path: Path) -> None:
    engine = create_async_engine(_url(tmp_path))
    cfg = _get_alembic_config(engine)
    try:
        await asyncio.to_thread(_upgrade, cfg, "head")
        # 第二次升级（部署每次启动的路径）必须是 no-op
        await asyncio.to_thread(_upgrade, cfg, "head")

        cols = await _external_calls_columns(engine)
        assert "task_id" in cols, "upgrade 后 task_id 列必须存在"
        assert cols["task_id"]["nullable"] is True
        indexes = await _external_calls_indexes(engine)
        assert INDEX_NAME in indexes, "upgrade 后 task_id 索引必须存在"
    finally:
        await engine.dispose()


@pytest.mark.anyio
async def test_downgrade_removes_column_and_index(tmp_path: Path) -> None:
    engine = create_async_engine(_url(tmp_path))
    cfg = _get_alembic_config(engine)
    try:
        await asyncio.to_thread(_upgrade, cfg, "head")
        assert "task_id" in await _external_calls_columns(engine)

        # 降到 task_id 迁移之前（i1e5f7a9b4c6）——链上其后有 C0/C2 迁移
        # （j2f6a8b0c1d7/k3d7f9e8a1b5，2026-08-27 成本可信化批次），"-1" 只回
        # 一步到不了本迁移的 downgrade，故用相对目标显式穿越。
        await asyncio.to_thread(alembic_command.downgrade, cfg, "b3d4e5f6a7c8-1")

        cols = await _external_calls_columns(engine)
        assert "task_id" not in cols, "downgrade 后 task_id 列必须消失"
        indexes = await _external_calls_indexes(engine)
        assert INDEX_NAME not in indexes, "downgrade 后 task_id 索引必须消失"
    finally:
        await engine.dispose()


@pytest.mark.anyio
async def test_downgrade_survives_already_dropped_index(tmp_path: Path) -> None:
    """D4：幂等——索引已不在（部分回滚残留）时 downgrade 不炸，列照常删除。

    裸 ``op.drop_index`` 对不存在的索引直接抛错，第二次 downgrade 或部分回滚
    后的 downgrade 会中断整个回滚流程。``safe_drop_index`` 应先判存在。
    """
    engine = create_async_engine(_url(tmp_path))
    cfg = _get_alembic_config(engine)
    try:
        await asyncio.to_thread(_upgrade, cfg, "head")
        assert INDEX_NAME in await _external_calls_indexes(engine)

        # 模拟部分回滚：索引已被手动删掉，列还在
        async with engine.begin() as conn:
            await conn.execute(sa.text(f"DROP INDEX {INDEX_NAME}"))

        await asyncio.to_thread(alembic_command.downgrade, cfg, "b3d4e5f6a7c8-1")

        cols = await _external_calls_columns(engine)
        assert "task_id" not in cols, "downgrade 后 task_id 列必须消失"
    finally:
        await engine.dispose()
