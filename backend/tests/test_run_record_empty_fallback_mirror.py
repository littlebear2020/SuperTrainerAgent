"""R9（2026-09-24 批 2）：RunRecord ``empty_fallback_count`` 镜像字段.

slow 链（``slow_llm_call_count``，test_slow_llm_call_middleware.py
``TestRunCompletionPlumbing``）的对称形态：列（migration ``p9c0d1e2f3a4``）与
worker 透传（worker.py ``run_ctx.get("empty_fallback_count")`` → completion splat）
在 2026-09-23 批 1 已就绪，本批补齐 RunRecord 内存镜像 + 水合，使运行中的
RunManager（in-memory registry / API run detail）与 SQL 行同口径。
"""

from __future__ import annotations

import pytest
from _db_helpers import sqlite_repo_fixture

from deerflow.persistence.run import RunRepository

# Per-test SQLite engine + RunRepository; engine closed in ``finally`` even
# when an assertion fails (see tests/_db_helpers.py).
repo = sqlite_repo_fixture(RunRepository)


class TestEmptyFallbackRunRecordPlumbing:
    @pytest.mark.anyio
    async def test_sql_store_persists_empty_fallback_count(self, repo) -> None:
        await repo.put("r1", thread_id="t1")
        await repo.update_run_completion("r1", status="success", empty_fallback_count=2)
        row = await repo.get("r1")
        assert row["empty_fallback_count"] == 2

    @pytest.mark.anyio
    async def test_sql_store_leaves_count_alone_when_not_supplied(self, repo) -> None:
        await repo.put("r1", thread_id="t1")
        await repo.update_run_completion("r1", status="running", empty_fallback_count=1)
        await repo.update_run_completion("r1", status="success")
        assert (await repo.get("r1"))["empty_fallback_count"] == 1

    @pytest.mark.anyio
    async def test_run_manager_mirrors_empty_fallback_count(self, repo) -> None:
        """Mirrors the worker call shape: journal completion data plus the
        runtime.context-stamped counter, splatted into update_run_completion."""
        from deerflow.runtime.runs.manager import RunManager

        manager = RunManager(store=repo)
        record = await manager.create("t1")

        completion = {"empty_fallback_count": 2}
        await manager.update_run_completion(record.run_id, status="success", **completion)

        assert record.empty_fallback_count == 2
        assert (await repo.get(record.run_id))["empty_fallback_count"] == 2

    @pytest.mark.anyio
    async def test_run_manager_hydrates_empty_fallback_count(self, repo) -> None:
        """水合链：持久化行 → get_run（_row_to_record）→ RunRecord 字段。"""
        from deerflow.runtime.runs.manager import RunManager

        manager = RunManager(store=repo)
        record = await manager.create("t1")
        await manager.update_run_completion(record.run_id, status="success", empty_fallback_count=3)
        run_id = record.run_id
        del manager  # 丢弃 in-memory registry，强制走 store 水合

        manager2 = RunManager(store=repo)
        hydrated = await manager2.get(run_id)
        assert hydrated is not None
        assert hydrated.empty_fallback_count == 3
        assert hydrated.slow_llm_call_count == 0  # 相邻镜像字段不受影响
