"""Test that messages from superseded runs are hidden from thread history."""

import asyncio

import pytest
from _router_auth_helpers import make_authed_test_app
from fastapi.testclient import TestClient

from app.gateway.routers import thread_runs
from deerflow.runtime.runs import RunManager, RunStatus
from deerflow.runtime.runs.store.memory import MemoryRunStore


@pytest.mark.asyncio
async def test_list_successful_regenerate_sources_returns_source_run_ids():
    """A successful regeneration with regenerate_from_run_id should list the source."""
    store = MemoryRunStore()
    mgr = RunManager(store=store)

    # Source run (the one being regenerated from)
    await store.put("run-old", thread_id="t1", user_id="u1", status="success")
    # Regeneration run (successful, points back to source)
    await store.put("run-new", thread_id="t1", user_id="u1", status="success", metadata={"regenerate_from_run_id": "run-old"})

    sources = await mgr.list_successful_regenerate_sources("t1", user_id="u1")
    assert sources == {"run-old"}


@pytest.mark.asyncio
async def test_failed_regeneration_does_not_hide_source():
    """A failed regeneration should NOT hide the source run."""
    store = MemoryRunStore()
    mgr = RunManager(store=store)

    await store.put("run-old", thread_id="t1", user_id="u1", status="success")
    await store.put("run-new", thread_id="t1", user_id="u1", status="error", metadata={"regenerate_from_run_id": "run-old"})

    sources = await mgr.list_successful_regenerate_sources("t1", user_id="u1")
    assert sources == set()


@pytest.mark.asyncio
async def test_inmemory_record_overrides_store():
    """A latest in-memory failure discards a store-only success for the same source."""
    store = MemoryRunStore()
    mgr = RunManager(store=store)

    # Store has a successful regeneration
    await store.put("run-old", thread_id="t1", user_id="u1", status="success")
    await store.put("run-attempt-1", thread_id="t1", user_id="u1", status="success", metadata={"regenerate_from_run_id": "run-old"})

    # In-memory: a retry that failed (should override the store success)
    from deerflow.runtime.runs import DisconnectMode
    from deerflow.runtime.runs.manager import RunRecord

    mgr._runs["run-attempt-2"] = RunRecord(
        run_id="run-attempt-2",
        thread_id="t1",
        assistant_id=None,
        status=RunStatus.error,
        on_disconnect=DisconnectMode.cancel,
        metadata={"regenerate_from_run_id": "run-old"},
        created_at="2026-01-01T00:00:03",
        updated_at="2026-01-01T00:00:03",
    )

    sources = await mgr.list_successful_regenerate_sources("t1", user_id="u1")
    # Store said success, but in-memory says error -> source should NOT be hidden
    assert sources == set()


@pytest.mark.asyncio
async def test_non_regenerate_runs_are_not_listed():
    """Runs without regenerate_from_run_id should not produce any sources."""
    store = MemoryRunStore()
    mgr = RunManager(store=store)

    await store.put("run-1", thread_id="t1", user_id="u1", status="success")
    await store.put("run-2", thread_id="t1", user_id="u1", status="success")

    sources = await mgr.list_successful_regenerate_sources("t1", user_id="u1")
    assert sources == set()


@pytest.mark.asyncio
async def test_user_isolation():
    """Sources from other users should not be visible."""
    store = MemoryRunStore()
    mgr = RunManager(store=store)

    await store.put("run-old", thread_id="t1", user_id="alice", status="success")
    await store.put("run-new", thread_id="t1", user_id="bob", status="success", metadata={"regenerate_from_run_id": "run-old"})

    # Alice should not see bob's regeneration of alice's run
    sources_alice = await mgr.list_successful_regenerate_sources("t1", user_id="alice")
    assert sources_alice == set()

    # Bob should see his own regeneration source
    sources_bob = await mgr.list_successful_regenerate_sources("t1", user_id="bob")
    assert sources_bob == {"run-old"}


# ---------------------------------------------------------------------------
# Manager-level filter semantics: list_successful_regenerate_sources output
# combined with a list_by_thread listing (the composition the route applies)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_runs_excludes_superseded_runs():
    """Manager-level: a successful regeneration hides its source from a listing."""
    store = MemoryRunStore()
    mgr = RunManager(store=store)

    # Original run (will be superseded)
    await store.put("run-old", thread_id="t1", user_id="u1", status="success")
    # Regeneration that succeeded -> run-old is now superseded
    await store.put("run-new", thread_id="t1", user_id="u1", status="success", metadata={"regenerate_from_run_id": "run-old"})

    sources = await mgr.list_successful_regenerate_sources("t1", user_id="u1")
    assert sources == {"run-old"}

    # The composition the route applies: list all runs, then drop hidden sources
    all_records = await mgr.list_by_thread("t1")
    visible = [r for r in all_records if r.run_id not in sources]
    visible_ids = {r.run_id for r in visible}

    assert "run-new" in visible_ids
    assert "run-old" not in visible_ids


@pytest.mark.asyncio
async def test_list_runs_keeps_runs_when_no_regeneration():
    """Manager-level: without any regeneration, all runs stay visible."""
    store = MemoryRunStore()
    mgr = RunManager(store=store)

    await store.put("run-1", thread_id="t1", user_id="u1", status="success")
    await store.put("run-2", thread_id="t1", user_id="u1", status="success")

    sources = await mgr.list_successful_regenerate_sources("t1", user_id="u1")
    assert sources == set()

    all_records = await mgr.list_by_thread("t1")
    visible = [r for r in all_records if r.run_id not in sources]
    assert len(visible) == 2


@pytest.mark.asyncio
async def test_list_runs_keeps_failed_regeneration_source():
    """Manager-level: a failed regeneration does NOT hide the source run."""
    store = MemoryRunStore()
    mgr = RunManager(store=store)

    await store.put("run-old", thread_id="t1", user_id="u1", status="success")
    await store.put("run-failed", thread_id="t1", user_id="u1", status="error", metadata={"regenerate_from_run_id": "run-old"})

    sources = await mgr.list_successful_regenerate_sources("t1", user_id="u1")
    assert sources == set()

    all_records = await mgr.list_by_thread("t1")
    visible = [r for r in all_records if r.run_id not in sources]
    visible_ids = {r.run_id for r in visible}
    assert visible_ids == {"run-old", "run-failed"}


@pytest.mark.asyncio
async def test_explicit_created_at_sorting_overrides_insertion_order():
    """Even if _runs insertion order differs from created_at order,
    the sorting ensures the latest attempt's status is authoritative."""
    from deerflow.runtime.runs import DisconnectMode, RunStatus
    from deerflow.runtime.runs.manager import RunRecord

    store = MemoryRunStore()
    mgr = RunManager(store=store)

    # Source run
    await store.put("run-source", thread_id="t1", user_id="u1", status="success")

    # Insert attempt-2 (later created_at) BEFORE attempt-1 (earlier) in _runs
    # to simulate insertion order != creation order.
    mgr._runs["attempt-2"] = RunRecord(
        run_id="attempt-2",
        thread_id="t1",
        assistant_id=None,
        status=RunStatus.success,
        on_disconnect=DisconnectMode.cancel,
        metadata={"regenerate_from_run_id": "run-source"},
        created_at="2026-01-02T00:00:00",
        updated_at="2026-01-02T00:00:00",
    )
    mgr._runs["attempt-1"] = RunRecord(
        run_id="attempt-1",
        thread_id="t1",
        assistant_id=None,
        status=RunStatus.error,
        on_disconnect=DisconnectMode.cancel,
        metadata={"regenerate_from_run_id": "run-source"},
        created_at="2026-01-01T00:00:00",
        updated_at="2026-01-01T00:00:00",
    )

    sources = await mgr.list_successful_regenerate_sources("t1", user_id="u1")
    # attempt-1 (error, earlier) is processed first -> discard then no add
    # attempt-2 (success, later) is processed second -> discard then add
    # Result: run-source is hidden (latest attempt succeeded)
    assert sources == {"run-source"}


# ---------------------------------------------------------------------------
# Route-level: GET /api/threads/{thread_id}/runs applies the hiding filter
# (the frontend's actual consumption path — useThreadRuns -> useThreadHistory)
#
# 身份保真（2026-10-05 复查修复）：路由走 get_current_user → access_token
# cookie JWT → deps.get_local_provider 解析 user_id，再传给 store 做按用户
# 过滤。此前只靠 stub 中间件，get_current_user 实际拿到 None → store 的
# user 过滤被整体跳过——生产若把 user_id 传丢（误传 None）这两例照绿。
# 现在用真 cookie + fake local provider 走真实身份解析链。
# ---------------------------------------------------------------------------

_U1 = "11111111-1111-4111-8111-111111111111"
_U2 = "22222222-2222-4222-8222-222222222222"


def _user(uid: str):
    from uuid import UUID

    from app.gateway.auth.models import User

    return User(email=f"{uid}@example.com", password_hash="x", system_role="user", id=UUID(uid))


class _FakeLocalProvider:
    """deps.get_local_provider 替身：JWT sub → 测试 User（token_version 默认 0 对齐）。"""

    def __init__(self, user):
        self._user = user

    async def get_user(self, sub):
        return self._user if str(self._user.id) == sub else None


def _make_runs_client(store, monkeypatch, viewer_uid: str = _U1) -> TestClient:
    monkeypatch.setenv("AUTH_JWT_SECRET", "test-secret-superseded-run-hiding")
    import app.gateway.deps as deps
    from app.gateway.auth import create_access_token

    user = _user(viewer_uid)
    monkeypatch.setattr(deps, "get_local_provider", lambda: _FakeLocalProvider(user))
    app = make_authed_test_app()
    app.include_router(thread_runs.router)
    app.state.run_manager = RunManager(store=store)
    return TestClient(app, cookies={"access_token": create_access_token(viewer_uid)})


def test_list_runs_endpoint_excludes_superseded_runs(monkeypatch):
    """The real route drops a run superseded by a successful regeneration."""
    store = MemoryRunStore()

    async def _seed():
        await store.put("run-old", thread_id="t1", user_id=_U1, status="success")
        await store.put("run-new", thread_id="t1", user_id=_U1, status="success", metadata={"regenerate_from_run_id": "run-old"})

    asyncio.run(_seed())

    with _make_runs_client(store, monkeypatch) as client:
        response = client.get("/api/threads/t1/runs")

    assert response.status_code == 200
    visible_ids = {run["run_id"] for run in response.json()}
    assert "run-new" in visible_ids
    assert "run-old" not in visible_ids


def test_list_runs_endpoint_keeps_source_of_failed_regeneration(monkeypatch):
    """The real route keeps the source run when the regeneration failed."""
    store = MemoryRunStore()

    async def _seed():
        await store.put("run-old", thread_id="t1", user_id=_U1, status="success")
        await store.put("run-failed", thread_id="t1", user_id=_U1, status="error", metadata={"regenerate_from_run_id": "run-old"})

    asyncio.run(_seed())

    with _make_runs_client(store, monkeypatch) as client:
        response = client.get("/api/threads/t1/runs")

    assert response.status_code == 200
    visible_ids = {run["run_id"] for run in response.json()}
    assert visible_ids == {"run-old", "run-failed"}


def test_list_runs_endpoint_does_not_hide_source_for_other_user(monkeypatch):
    """别人的成功重生成不得隐藏我的源 run——user 过滤维度回归。

    生产回归形态：路由把 user_id 传丢（误传 None / 未透传）→
    list_successful_regenerate_sources 全线程合并 → u2 的重生成把 u1 的
    run-old 藏掉。此前身份为 None 时此维度零敏感（复查发现的保真缺口）。"""
    store = MemoryRunStore()

    async def _seed():
        await store.put("run-old", thread_id="t1", user_id=_U1, status="success")
        await store.put("run-new", thread_id="t1", user_id=_U2, status="success", metadata={"regenerate_from_run_id": "run-old"})

    asyncio.run(_seed())

    with _make_runs_client(store, monkeypatch, viewer_uid=_U1) as client:
        response = client.get("/api/threads/t1/runs")

    assert response.status_code == 200
    visible_ids = {run["run_id"] for run in response.json()}
    # u2 的重生成只对 u2 隐藏 run-old；u1 视角两条都在
    assert visible_ids == {"run-old", "run-new"}
