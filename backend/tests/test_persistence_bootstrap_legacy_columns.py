"""P0-5: legacy 老库经 bootstrap 后必须补齐 baseline 之前的加列 migration。"""

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

# 关键：注册 unified_tasks 进 Base.metadata，否则 bootstrap 走 empty 分支
import app.gateway._shared.task_framework.models  # noqa: F401


def _legacy_columns(db_file) -> set[str]:
    eng = sa.create_engine(f"sqlite:///{db_file}")
    with eng.connect() as c:
        cols = {r[1] for r in c.exec_driver_sql("PRAGMA table_info(unified_tasks)")}
    eng.dispose()
    return cols


@pytest.mark.asyncio
async def test_legacy_db_gets_dirty_frames_column(tmp_path):
    """老库：有 unified_tasks（无 dirty_frames）、无 alembic_version。"""
    db_file = tmp_path / "legacy.db"
    eng = sa.create_engine(f"sqlite:///{db_file}")
    with eng.begin() as c:
        c.exec_driver_sql(
            "CREATE TABLE unified_tasks ("
            " id TEXT PRIMARY KEY, user_id TEXT, domain TEXT, task_type TEXT,"
            " status TEXT, progress INTEGER, payload TEXT, result TEXT,"
            " error_message TEXT, claimed_by TEXT, claimed_at TEXT,"
            " lease_expires_at TEXT, retry_count INTEGER, priority INTEGER,"
            " created_at TEXT, updated_at TEXT)"
        )
    eng.dispose()

    assert "dirty_frames" not in _legacy_columns(db_file), "前置条件：老库不该有该列"

    from deerflow.persistence.bootstrap import bootstrap_schema

    aeng = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    try:
        await bootstrap_schema(aeng, backend="sqlite")  # 不得抛（含撞表）
    finally:
        await aeng.dispose()

    assert "dirty_frames" in _legacy_columns(db_file), "legacy 分支 stamp 在 baseline 会跳过 baseline 之前的加列 migration"


@pytest.mark.asyncio
async def test_empty_db_still_bootstraps(tmp_path):
    """空库回归：不得被本次改动影响。"""
    from deerflow.persistence.bootstrap import bootstrap_schema

    db_file = tmp_path / "empty.db"
    aeng = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    try:
        await bootstrap_schema(aeng, backend="sqlite")
    finally:
        await aeng.dispose()
    assert "dirty_frames" in _legacy_columns(db_file)
