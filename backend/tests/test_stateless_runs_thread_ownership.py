"""SEC-C1 (security plan 2026-09-02, batch B1): stateless runs thread ownership.

Two entry points closed by this batch:

1. ``POST /api/runs/stream`` / ``POST /api/runs/wait`` trusted the body's
   ``config.configurable.thread_id`` with no ownership check — any
   authenticated user could take over / read another user's thread.
2. ``services.start_run``'s thread_meta upsert swallowed a create() primary
   key conflict as "non-fatal" and proceeded (F-B08-001) — grafting the run
   onto the other owner's thread.

Expected semantics (mirrors ``@require_permission(owner_check=True)`` and
``ThreadMetaStore.check_access(require_existing=False)``):

- existing row owned by someone else → 404 (no existence leak);
- row missing or owner NULL → allowed (documented auto-create semantics);
- upsert create() failure that is NOT a concurrent create: one retry rules
  out transient jitter; still failing + still missing → run start aborts
  with 503 + error log (M15 fail-closed, requirements-v2 review plan
  2026-09-09 — was warn-and-proceed, which grafted runs onto unowned
  threads readable by anyone via M1's permissive read);
- same-owner / NULL-owner concurrent create race → proceeds.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from _router_auth_helpers import make_authed_test_app
from fastapi.testclient import TestClient

from app.gateway.auth.models import User
from app.gateway.routers import runs as runs_router
from app.gateway.routers.thread_runs import RunCreateRequest
from app.gateway.services import start_run
from deerflow.runtime.runs.schemas import RunStatus
from deerflow.runtime.user_context import AUTO

ALICE = User(email="alice@example.com", password_hash="x", system_role="user", id=uuid4())


def _thread_store(check_access_result: bool) -> MagicMock:
    store = MagicMock()
    store.check_access = AsyncMock(return_value=check_access_result)
    return store


def _make_app(check_access_result: bool) -> TestClient:
    app = make_authed_test_app(user_factory=lambda: ALICE)
    app.include_router(runs_router.router)
    app.state.thread_store = _thread_store(check_access_result)
    app.state.checkpointer = MagicMock()  # stateless_wait reads it unconditionally
    return TestClient(app)


def _body(thread_id: str | None) -> dict:
    body: dict = {"input": {"messages": [{"role": "user", "content": "hi"}]}}
    if thread_id is not None:
        body["config"] = {"configurable": {"thread_id": thread_id}}
    return body


# ---------------------------------------------------------------------------
# stateless_stream / stateless_wait ownership gate
# ---------------------------------------------------------------------------


def test_stateless_stream_other_users_thread_404(monkeypatch):
    """Body thread_id owned by someone else → 404, run never starts."""
    started = AsyncMock()
    monkeypatch.setattr(runs_router, "start_run", started)
    client = _make_app(check_access_result=False)

    response = client.post("/api/runs/stream", json=_body("thread-of-bob"))

    assert response.status_code == 404
    assert "thread-of-bob" in response.json()["detail"]
    started.assert_not_awaited()
    client.app.state.thread_store.check_access.assert_awaited_once_with("thread-of-bob", str(ALICE.id), require_existing=False)


def test_stateless_wait_other_users_thread_404(monkeypatch):
    started = AsyncMock()
    monkeypatch.setattr(runs_router, "start_run", started)
    client = _make_app(check_access_result=False)

    response = client.post("/api/runs/wait", json=_body("thread-of-bob"))

    assert response.status_code == 404
    started.assert_not_awaited()


def test_stateless_stream_own_thread_proceeds(monkeypatch):
    """Body thread_id owned by the caller (check_access True) → run starts."""
    record = SimpleNamespace(run_id="r1", status=RunStatus.error, error="boom", task=None)
    started = AsyncMock(return_value=record)
    monkeypatch.setattr(runs_router, "start_run", started)
    client = _make_app(check_access_result=True)

    response = client.post("/api/runs/wait", json=_body("thread-own"))

    assert response.status_code == 200
    assert response.json()["status"] == "error"
    started.assert_awaited_once()
    args, _kwargs = started.call_args
    assert args[1] == "thread-own"


def test_stateless_missing_thread_id_auto_creates_without_check(monkeypatch):
    """No thread_id in body → fresh uuid, no ownership check (auto-create)."""
    record = SimpleNamespace(run_id="r2", status=RunStatus.error, error="boom", task=None)
    started = AsyncMock(return_value=record)
    monkeypatch.setattr(runs_router, "start_run", started)
    client = _make_app(check_access_result=False)  # even a deny-all store must not be consulted

    response = client.post("/api/runs/wait", json=_body(None))

    assert response.status_code == 200
    started.assert_awaited_once()
    args, _kwargs = started.call_args
    assert args[1]  # generated uuid
    client.app.state.thread_store.check_access.assert_not_awaited()


def test_stateless_stream_404_before_sse(monkeypatch):
    """The 404 is a plain JSON error, not an SSE stream."""
    monkeypatch.setattr(runs_router, "start_run", AsyncMock())
    client = _make_app(check_access_result=False)

    response = client.post("/api/runs/stream", json=_body("thread-of-bob"))

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")


# ---------------------------------------------------------------------------
# services.start_run upsert fail-closed (F-B08-001 second entry)
# ---------------------------------------------------------------------------


class _FakeRunManager:
    def __init__(self, record):
        self.record = record
        self.statuses: list[tuple[str, object, str | None]] = []

    async def create_or_reject(self, *args, **kwargs):
        return self.record

    async def set_status(self, run_id, status, *, error=None):
        self.statuses.append((run_id, status, error))


def _fake_request(thread_store: MagicMock) -> SimpleNamespace:
    state = SimpleNamespace(
        stream_bridge=MagicMock(),
        run_manager=None,  # replaced per-test
        checkpointer=MagicMock(),
        store=None,
        run_event_store=MagicMock(),
        thread_store=thread_store,
        config=SimpleNamespace(),
    )
    return SimpleNamespace(app=SimpleNamespace(state=state))


def _record() -> SimpleNamespace:
    return SimpleNamespace(run_id="run-x", thread_id="thread-x", status=MagicMock(), task=None)


def _thread_store_mock(
    *,
    filtered_get: dict | None,
    create_raises: bool = False,
    create_side_effects: list | None = None,
    unfiltered_row: dict | None = None,
):
    """Store mock whose get() distinguishes the ownership-filtered read from
    the owner-unfiltered triage read (``user_id=None`` bypasses the filter)."""
    store = MagicMock()

    async def get(thread_id, *, user_id=AUTO):
        if user_id is None:
            return unfiltered_row
        return filtered_get

    store.get = AsyncMock(side_effect=get)
    if create_side_effects is not None:
        store.create = AsyncMock(side_effect=create_side_effects)
    elif create_raises:
        store.create = AsyncMock(side_effect=Exception("UNIQUE constraint failed: threads_meta.thread_id"))
    else:
        store.create = AsyncMock()
    store.update_status = AsyncMock()
    return store


def _require_real_config() -> None:
    """CI 守卫（2026-09-23 收尾批）：本组用例经 deps.get_config 读真实 config.yaml
    （gitignored，CI fresh checkout 不存在）——缺失时跳过，本地在位照常执行。"""
    if not any((Path(__file__).resolve().parents[i] / "config.yaml").exists() for i in (1, 2)):
        pytest.skip("依赖本地 gitignored config.yaml，CI 跳过")


def _patch_start_run_internals(monkeypatch):
    _require_real_config()
    monkeypatch.setattr("app.gateway.services.resolve_agent_factory", lambda aid: None)
    monkeypatch.setattr("app.gateway.services.normalize_input", lambda inp: inp)
    monkeypatch.setattr("app.gateway.services.build_run_config", lambda *a, **k: {})
    monkeypatch.setattr("app.gateway.services.run_agent", AsyncMock())


@pytest.mark.asyncio
async def test_upsert_conflict_aborts_run_start(monkeypatch, caplog):
    """create() PK conflict + triage shows another owner → 404, error logged, run marked error."""
    from fastapi import HTTPException

    record = _record()
    mgr = _FakeRunManager(record)
    # filtered get → None; unfiltered triage → row owned by someone else
    store = _thread_store_mock(filtered_get=None, create_raises=True, unfiltered_row={"thread_id": "t", "user_id": "user-bob"})

    request = _fake_request(store)
    request.app.state.run_manager = mgr
    _patch_start_run_internals(monkeypatch)

    body = RunCreateRequest()
    with caplog.at_level("ERROR", logger="app.gateway.services"):
        with pytest.raises(Exception) as excinfo:
            await start_run(body, "thread-owned-by-bob", request)

    assert isinstance(excinfo.value, HTTPException)
    assert excinfo.value.status_code == 404
    # Run record left in a terminal state, not a dangling pending row
    assert mgr.statuses and mgr.statuses[0][2]  # error message recorded
    assert any("thread-owned-by-bob" in r.getMessage() for r in caplog.records if r.levelname == "ERROR")


@pytest.mark.asyncio
async def test_upsert_null_owner_thread_proceeds(monkeypatch, caplog):
    """复核修复（反馈 1 问题 3）：NULL 属主（共享/前鉴权）线程不得被 fail-closed 404。

    check_access(require_existing=False) 允许 NULL 属主；但归属过滤的 get() 对
    NULL 属主行同样返回 None，修前的 fail-closed 把它与"他人占用"混为一谈。
    分诊后：非过滤读显示 user_id=None → 放行。
    """
    record = _record()
    mgr = _FakeRunManager(record)
    store = _thread_store_mock(filtered_get=None, create_raises=True, unfiltered_row={"thread_id": "t", "user_id": None})

    request = _fake_request(store)
    request.app.state.run_manager = mgr
    _patch_start_run_internals(monkeypatch)

    body = RunCreateRequest()
    result = await start_run(body, "thread-null-owner", request)

    assert result is record  # run proceeds on the shared/legacy thread
    assert not mgr.statuses  # no error status recorded


@pytest.mark.asyncio
async def test_upsert_persistent_create_failure_fails_closed_503(monkeypatch, caplog):
    """M15（2026-09-09 窄修）：create() 失败且行确实不存在（非并发建行）→
    重试一次仍失败 = 持续性 infra 故障 → 503 fail-closed，run 标 error。
    （旧行为：warn-and-proceed，把 run 嫁接到无主线程上，叠加 M1 宽容读
    任何登录用户可读。）"""
    from fastapi import HTTPException

    record = _record()
    mgr = _FakeRunManager(record)
    store = _thread_store_mock(filtered_get=None, create_raises=True, unfiltered_row=None)

    request = _fake_request(store)
    request.app.state.run_manager = mgr
    _patch_start_run_internals(monkeypatch)

    body = RunCreateRequest()
    with caplog.at_level("ERROR", logger="app.gateway.services"):
        with pytest.raises(Exception) as excinfo:
            await start_run(body, "thread-vanished", request)

    assert isinstance(excinfo.value, HTTPException)
    assert excinfo.value.status_code == 503
    assert store.create.await_count == 2  # 初次 + 一次重试（区分瞬时抖动）
    assert mgr.statuses and mgr.statuses[0][2]  # run 标 error 留痕
    assert any("thread-vanished" in r.getMessage() for r in caplog.records if r.levelname == "ERROR")


@pytest.mark.asyncio
async def test_upsert_transient_create_failure_recovers_on_retry(monkeypatch, caplog):
    """M15：瞬时抖动（create 首次失败、重试成功、行仍缺失）→ 放行不误杀——
    一次重试就是为区分这种情况，否则 infra 打嗝期间 runs 全 503。"""
    record = _record()
    mgr = _FakeRunManager(record)
    store = _thread_store_mock(
        filtered_get=None,
        create_side_effects=[Exception("transient jitter"), None],
        unfiltered_row=None,
    )

    request = _fake_request(store)
    request.app.state.run_manager = mgr
    _patch_start_run_internals(monkeypatch)

    body = RunCreateRequest()
    result = await start_run(body, "thread-jittery", request)

    assert result is record  # retry created the row → run proceeds
    assert store.create.await_count == 2
    assert not mgr.statuses  # no error status recorded


@pytest.mark.asyncio
async def test_upsert_same_owner_race_proceeds(monkeypatch):
    """create() conflict but triage shows the row owned by the current user (same-owner race) → run proceeds."""
    from deerflow.runtime.user_context import get_effective_user_id

    record = _record()
    mgr = _FakeRunManager(record)
    store = _thread_store_mock(
        filtered_get=None,
        create_raises=True,
        unfiltered_row={"thread_id": "t", "user_id": get_effective_user_id()},
    )

    request = _fake_request(store)
    request.app.state.run_manager = mgr
    _patch_start_run_internals(monkeypatch)

    body = RunCreateRequest()
    result = await start_run(body, "thread-mine", request)

    assert result is record


@pytest.mark.asyncio
async def test_upsert_normal_paths_unaffected(monkeypatch):
    """Existing row → update_status('running'); no exception raised."""
    record = _record()
    mgr = _FakeRunManager(record)

    store = MagicMock()
    store.get = AsyncMock(return_value={"thread_id": "thread-existing"})
    store.update_status = AsyncMock()

    request = _fake_request(store)
    request.app.state.run_manager = mgr
    _patch_start_run_internals(monkeypatch)

    body = RunCreateRequest()
    await start_run(body, "thread-existing", request)

    store.update_status.assert_awaited_once_with("thread-existing", "running")


# ---------------------------------------------------------------------------
# Third entry found by the B1 thread_id cross-check: gallery extract-recipe
# ---------------------------------------------------------------------------


def _gallery_app(check_access_result: bool) -> TestClient:
    _require_real_config()
    from app.gateway.deps import get_current_user_from_request
    from app.gateway.routers import gallery

    app = make_authed_test_app(user_factory=lambda: ALICE)
    app.include_router(gallery.router)
    app.state.thread_store = _thread_store(check_access_result)
    app.dependency_overrides[get_current_user_from_request] = lambda: ALICE

    checkpointer = MagicMock()
    checkpointer.aget_tuple = AsyncMock(return_value=None)
    app.state.checkpointer = checkpointer
    return TestClient(app)


def _recipe_body(thread_id: str) -> dict:
    return {"thread_id": thread_id, "title": "t", "category": "c"}


def test_extract_recipe_other_users_thread_404():
    """Body thread_id owned by someone else → 404 before the checkpoint read."""
    client = _gallery_app(check_access_result=False)

    response = client.post("/api/gallery/extract-recipe", json=_recipe_body("thread-of-bob"))

    assert response.status_code == 404
    assert "thread-of-bob" in response.json()["detail"]
    client.app.state.checkpointer.aget_tuple.assert_not_awaited()


def test_extract_recipe_own_thread_passes_gate():
    """Own thread passes the gate and proceeds to the checkpoint read (empty → 404 'No messages')."""
    client = _gallery_app(check_access_result=True)

    response = client.post("/api/gallery/extract-recipe", json=_recipe_body("thread-own"))

    assert response.status_code == 404
    assert response.json()["detail"] == "No messages found in thread"
    client.app.state.checkpointer.aget_tuple.assert_awaited_once()


# ── 条目 33（2026-09-12 缺陷批）：update_status 单次瞬时重试 ────────────────


@pytest.mark.asyncio
async def test_update_status_transient_failure_recovers_on_retry(monkeypatch):
    """update_status 首败重试成功 → 放行（对齐 create 分支 one-retry 哲学——
    UPDATE 谓词仅 thread_id、幂等，重试一次与首试成功语义等价）。"""
    record = _record()
    mgr = _FakeRunManager(record)
    store = _thread_store_mock(
        filtered_get={"thread_id": "t", "user_id": "test-user-autouse"},
        unfiltered_row=None,
    )
    store.update_status = AsyncMock(side_effect=[Exception("db locked transiently"), None])

    request = _fake_request(store)
    request.app.state.run_manager = mgr
    _patch_start_run_internals(monkeypatch)

    body = RunCreateRequest()
    result = await start_run(body, "thread-exists", request)

    assert result is record  # 重试成功 → run 照常启动
    assert store.update_status.await_count == 2
    assert not mgr.statuses  # 无 error 留痕


@pytest.mark.asyncio
async def test_update_status_persistent_failure_fails_closed_503(monkeypatch, caplog):
    """重试再败 → 外层 catch-all 503 fail-closed + run 标 error（语义不变）。"""
    from fastapi import HTTPException

    record = _record()
    mgr = _FakeRunManager(record)
    store = _thread_store_mock(
        filtered_get={"thread_id": "t", "user_id": "test-user-autouse"},
        unfiltered_row=None,
    )
    store.update_status = AsyncMock(side_effect=Exception("db is gone"))

    request = _fake_request(store)
    request.app.state.run_manager = mgr
    _patch_start_run_internals(monkeypatch)

    body = RunCreateRequest()
    with caplog.at_level("ERROR", logger="app.gateway.services"):
        with pytest.raises(Exception) as excinfo:
            await start_run(body, "thread-exists", request)

    assert isinstance(excinfo.value, HTTPException)
    assert excinfo.value.status_code == 503
    assert store.update_status.await_count == 2  # 初次 + 一次重试
    assert mgr.statuses and mgr.statuses[0][2]  # run 标 error 留痕


# ── P1-S1（2026-09-30 上游移植）：NULL-owner 原子过户 ────────────────────────


@pytest.mark.asyncio
async def test_upsert_null_owner_claimed_atomically(monkeypatch):
    """NULL 属主线程首次访问即经 claim_unowned 原子过户给当前用户（洞收敛）。"""
    record = _record()
    mgr = _FakeRunManager(record)
    store = _thread_store_mock(filtered_get=None, create_raises=True, unfiltered_row={"thread_id": "t", "user_id": None})
    store.claim_unowned = AsyncMock(return_value=True)

    request = _fake_request(store)
    request.app.state.run_manager = mgr
    _patch_start_run_internals(monkeypatch)

    body = RunCreateRequest()
    result = await start_run(body, "thread-null-claim", request)

    assert result is record  # run proceeds
    store.claim_unowned.assert_awaited_once()
    args = store.claim_unowned.await_args.args
    assert args[0] == "thread-null-claim"
    assert args[1]  # claimed for the effective user (default bucket in this harness)


@pytest.mark.asyncio
async def test_upsert_null_owner_claim_lost_aborts_404(monkeypatch, caplog):
    """并发过户输给其他用户 → 分诊改判 foreign → 404（不再嫁接 run）。"""
    from fastapi import HTTPException

    record = _record()
    mgr = _FakeRunManager(record)

    row = {"thread_id": "t", "user_id": None}
    store = MagicMock()

    async def get(thread_id, *, user_id=AUTO):
        if user_id is None:
            return dict(row)  # claim 失败后改为他人属主，模拟真实 UPDATE 竞态
        return None

    store.get = AsyncMock(side_effect=get)
    store.create = AsyncMock(side_effect=Exception("UNIQUE constraint failed"))
    # 首次 claim 失败并把行让给 bob（side_effect 内改变后续 get 的真相）
    store.claim_unowned = AsyncMock(side_effect=lambda tid, owner: row.update({"user_id": "user-bob"}) or False)
    store.update_status = AsyncMock()

    request = _fake_request(store)
    request.app.state.run_manager = mgr
    _patch_start_run_internals(monkeypatch)

    body = RunCreateRequest()
    with pytest.raises(HTTPException) as excinfo:
        await start_run(body, "thread-claim-lost", request)

    assert excinfo.value.status_code == 404
