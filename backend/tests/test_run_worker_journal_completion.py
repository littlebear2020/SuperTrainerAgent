"""Worker finally-path regression: journal present must not break completion (2026-09-23 review #1).

The finally block used to rebind the local name ``ctx`` (the ``RunContext``
parameter) to the runtime context *dict* inside the ``if journal is not None``
branch, while the D3 token-budget block read ``ctx.app_config`` outside it.
Production always has an event_store → journal is never None → every
successful run raised ``AttributeError`` from the finally, skipping
``publish_end`` (SSE hang), thread-status reset, and both cleanup tasks.
The existing worker tests all pass ``event_store=None``, so only this file
covers the real shape.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from deerflow.runtime.runs.manager import RunManager
from deerflow.runtime.runs.schemas import RunStatus
from deerflow.runtime.runs.worker import run_agent


class _Bridge:
    def __init__(self):
        self.published: list[tuple[str, str, object]] = []

    async def publish(self, run_id, event, data):
        self.published.append((run_id, event, data))

    async def publish_end(self, run_id):
        self.published.append((run_id, "end", None))

    async def cleanup(self, run_id, *, delay=60):
        pass


class _EventStore:
    """Minimal RunJournal event_store: accept batched writes."""

    def __init__(self):
        self.batches: list[list[dict]] = []

    async def put_batch(self, batch):
        self.batches.append(list(batch))


def _ctx(event_store, app_config):
    return SimpleNamespace(
        checkpointer=None,
        store=None,
        event_store=event_store,
        run_events_config=None,
        thread_store=None,
        app_config=app_config,
    )


def _successful_agent_factory(config=None, app_config=None):
    class _Agent:
        checkpointer = None
        store = None

        async def astream(self, *a, **kw):
            yield {"messages": []}

    return _Agent()


# app_config shaped so the disk-admission probe no-ops (local mode, no warn
# ratio) and the D3 branch is entered but its warning disabled (non-positive
# threshold) — exercising both ``app_config`` read sites without a store.
_APP_CONFIG = SimpleNamespace(
    sandbox=None,
    disk_min_free_bytes_block=0,
    disk_warn_free_ratio=0.0,
    token_budget_warn_tokens={"default": 0},
)


@pytest.fixture()
def _fast_run_cleanup(monkeypatch):
    """Keep the finally block's delayed eviction from outliving the test loop."""

    def fake_cleanup(self, run_id, *, delay=300):
        return asyncio.sleep(0)

    monkeypatch.setattr(RunManager, "cleanup", fake_cleanup)


@pytest.mark.asyncio()
async def test_success_path_with_journal_publishes_end(_fast_run_cleanup):
    """journal 创建（event_store 非空）+ run 成功 → finally 不炸、end 事件必达。

    修复前：finally 内 ctx 被重绑为 dict，``ctx.app_config`` 抛 AttributeError
    逃出 finally → publish_end 永不发出（本用例红）。
    """
    mgr = RunManager(store=None)
    record = await mgr.create("t-journal")
    bridge = _Bridge()
    event_store = _EventStore()

    await run_agent(
        bridge,
        mgr,
        record,
        ctx=_ctx(event_store, app_config=_APP_CONFIG),
        agent_factory=_successful_agent_factory,
        graph_input={},
        config={},
    )

    assert record.status == RunStatus.success
    assert record.error is None
    # SSE 流必须正常终止；错误事件不得出现（AttributeError 会先于 end 逃出）。
    assert any(event == "end" for (_rid, event, _data) in bridge.published)
    assert not any(event == "error" for (_rid, event, _data) in bridge.published)


@pytest.mark.asyncio()
async def test_success_path_with_journal_without_app_config(_fast_run_cleanup):
    """app_config=None 的最小 ctx（既有 worker 测试形态）在 journal 在场时同样安全。"""
    mgr = RunManager(store=None)
    record = await mgr.create("t-journal-noconf")
    bridge = _Bridge()

    await run_agent(
        bridge,
        mgr,
        record,
        ctx=_ctx(_EventStore(), app_config=None),
        agent_factory=_successful_agent_factory,
        graph_input={},
        config={},
    )

    assert record.status == RunStatus.success
    assert any(event == "end" for (_rid, event, _data) in bridge.published)
