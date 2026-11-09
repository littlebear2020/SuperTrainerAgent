"""Tests for the unified task framework — BaseTaskService, TaskRepository, MetricsRegistry.

Uses in-memory SQLite + fresh instances to avoid state leakage between tests.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.gateway._shared.task_framework.metrics import MetricsRegistry
from app.gateway._shared.task_framework.models import TaskRow
from app.gateway._shared.task_framework.repository import TaskRepository
from app.gateway._shared.task_framework.schemas import GATEWAY_RECOVERABLE
from app.gateway._shared.task_framework.service import BaseTaskService

# ---------------------------------------------------------------------------
# Helpers: in-memory async SQLite
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="function")
async def async_engine():
    """In-memory async SQLite engine with the TaskRow table.

    Async fixture: a sync ``create`` on ``engine.sync_engine`` triggers
    ``MissingGreenlet`` under aiosqlite, and a sync fixture calling
    ``get_event_loop().run_until_complete`` raises ``RuntimeError: There is no
    current event loop`` when an earlier async-test file in the same process has
    left the thread without a current loop. An async fixture gets its loop
    directly from pytest-asyncio, avoiding both.
    """
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(TaskRow.__table__.create)
    yield engine
    await engine.dispose()


@pytest.fixture()
def session_factory(async_engine):
    return async_sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture()
def repo(session_factory):
    return TaskRepository(session_factory=session_factory)


# ---------------------------------------------------------------------------
# TaskRepository tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_repo_create_and_get(repo):
    task = await repo.create(
        id="vg_abc123",
        user_id="user1",
        domain="video_gen",
        task_type="ai_visual",
        payload='{"script":"hello"}',
    )
    assert task.id == "vg_abc123"
    assert task.domain == "video_gen"

    fetched = await repo.get("vg_abc123")
    assert fetched is not None
    assert fetched.task_type == "ai_visual"


@pytest.mark.asyncio
async def test_repo_get_with_user_filter(repo):
    await repo.create(
        id="ct_001",
        user_id="user1",
        domain="content",
        task_type="ppt_gen",
        payload="{}",
    )
    await repo.create(
        id="ct_002",
        user_id="user2",
        domain="content",
        task_type="ppt_gen",
        payload="{}",
    )

    # user1 sees only their task
    fetched = await repo.get("ct_001", user_id="user1")
    assert fetched is not None
    # user2 cannot see user1's task
    fetched_none = await repo.get("ct_001", user_id="user2")
    assert fetched_none is None


@pytest.mark.asyncio
async def test_repo_list_by_user_with_domain_filter(repo):
    await repo.create(id="t1", user_id="u1", domain="video_gen", task_type="ai_visual", payload="{}")
    await repo.create(id="t2", user_id="u1", domain="content", task_type="ppt_gen", payload="{}")
    await repo.create(id="t3", user_id="u2", domain="video_gen", task_type="ai_visual", payload="{}")

    all_u1 = await repo.list_by_user("u1")
    assert len(all_u1) == 2

    vg_only = await repo.list_by_user("u1", domain="video_gen")
    assert len(vg_only) == 1
    assert vg_only[0].domain == "video_gen"


@pytest.mark.asyncio
async def test_repo_update(repo):
    await repo.create(id="t1", user_id="u1", domain="video_gen", task_type="remotion", payload="{}")
    await repo.update("t1", status="running", progress=50)
    task = await repo.get("t1")
    assert task.status == "running"
    assert task.progress == 50


@pytest.mark.asyncio
async def test_repo_delete(repo):
    await repo.create(id="t1", user_id="u1", domain="video_gen", task_type="remotion", payload="{}")
    await repo.delete("t1")
    assert await repo.get("t1") is None


@pytest.mark.asyncio
async def test_repo_list_stale_for_recovery(repo):
    await repo.create(id="t1", user_id="u1", domain="video_gen", task_type="ai_visual", payload="{}")
    await repo.update("t1", status="running")
    await repo.create(id="t2", user_id="u1", domain="video_gen", task_type="remotion", payload="{}")
    # composing should NOT be recovered by Gateway
    await repo.update("t2", status="composing")

    stale = await repo.list_stale_for_recovery("video_gen", GATEWAY_RECOVERABLE)
    ids = [t.id for t in stale]
    assert "t1" in ids
    assert "t2" not in ids


@pytest.mark.asyncio
async def test_repo_list_stale_for_recovery_stale_after_filters_by_age(repo):
    """stale_after filters to tasks older than the cutoff (the reaper's predicate).

    Without stale_after (startup recovery), every non-terminal task is returned
    regardless of age — that behavior is covered by the test above and must stay
    intact (backward compat). With stale_after, only the aged task is returned."""
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import update as sa_update

    # t1: aged (updated 2h ago) → should be returned by stale_after cutoff
    await repo.create(id="t1", user_id="u1", domain="video_gen", task_type="remotion", payload="{}")
    await repo.update("t1", status="running")
    old = datetime.now(UTC) - timedelta(hours=2)
    async with repo._sf() as session:
        await session.execute(sa_update(TaskRow).where(TaskRow.id == "t1").values(updated_at=old))
        await session.commit()

    # t2: fresh (updated just now) → should NOT be returned by stale_after cutoff
    await repo.create(id="t2", user_id="u1", domain="video_gen", task_type="remotion", payload="{}")
    await repo.update("t2", status="running")

    cutoff = datetime.now(UTC) - timedelta(minutes=30)
    stale = await repo.list_stale_for_recovery("video_gen", GATEWAY_RECOVERABLE, stale_after=cutoff)
    ids = [t.id for t in stale]
    assert "t1" in ids
    assert "t2" not in ids

    # backward compat: omitting stale_after returns BOTH (startup recovery)
    all_stale = await repo.list_stale_for_recovery("video_gen", GATEWAY_RECOVERABLE)
    all_ids = [t.id for t in all_stale]
    assert "t1" in all_ids and "t2" in all_ids


# ---------------------------------------------------------------------------
# BaseTaskService tests
# ---------------------------------------------------------------------------


class _FakeService(BaseTaskService):
    """Minimal concrete service for CRUD/recover tests; pipeline is a no-op.

    Inherits submit/get_task/list_tasks/delete_task/recover_stale_tasks from
    BaseTaskService; only _run_pipeline is implemented (as a no-op), since these
    tests exercise the framework's task plumbing, not pipeline I/O.
    """

    domain = "test"
    id_prefix = "ts"
    max_retries = 0  # recover_stale_tasks must not auto-retry in these tests

    async def submit(self, **kwargs):
        # CRUD tests exercise task management, not pipeline execution. Skip
        # auto-start so no background _wrap_pipeline races the test body on the
        # shared StaticPool connection (which otherwise surfaces as
        # "Cannot operate on a closed database"). This also keeps status=="pending".
        kwargs["auto_start"] = False
        return await super().submit(**kwargs)

    async def _run_pipeline(self, task_id, user_id, task_type, payload, task_dir, progress):
        return None


@pytest.fixture()
def fake_service(repo, tmp_path):
    return _FakeService(repo, storage_dir=str(tmp_path))


@pytest.mark.asyncio
async def test_service_submit_and_get(fake_service, repo):
    task = await fake_service.submit(
        user_id="u1",
        task_type="demo",
        payload={"key": "value"},
    )
    assert task.id.startswith("ts_")
    assert task.status == "pending"

    fetched = await fake_service.get_task(task.id, "u1")
    assert fetched is not None
    assert fetched.task_type == "demo"


@pytest.mark.asyncio
async def test_service_list_tasks(fake_service):
    await fake_service.submit(user_id="u1", task_type="a", payload={})
    await fake_service.submit(user_id="u1", task_type="b", payload={})
    await fake_service.submit(user_id="u2", task_type="c", payload={})

    tasks = await fake_service.list_tasks("u1")
    assert len(tasks) == 2


@pytest.mark.asyncio
async def test_service_delete_task(fake_service, tmp_path):
    task = await fake_service.submit(user_id="u1", task_type="demo", payload={})
    # Create task dir to verify cleanup
    task_dir = tmp_path / "u1" / "tasks" / task.id
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "temp.txt").write_text("data")

    deleted = await fake_service.delete_task(task.id, "u1")
    assert deleted is True
    assert not task_dir.exists()

    # Second delete should return False
    deleted2 = await fake_service.delete_task(task.id, "u1")
    assert deleted2 is False


@pytest.mark.asyncio
async def test_service_cross_user_access_denied(fake_service):
    task = await fake_service.submit(user_id="u1", task_type="demo", payload={})
    fetched = await fake_service.get_task(task.id, "u2")
    assert fetched is None


@pytest.mark.asyncio
async def test_reap_stalled_tasks_flips_old_running_to_failed(repo, tmp_path):
    """The stall watchdog marks a non-terminal task `failed` once its
    updated_at is older than stall_timeout — closing the hole where a wedged
    in-process task (or a failed task the agent never re-checked) stays
    'running'/'rendering' at frozen progress forever (vg_7477487923ef)."""
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import update as sa_update

    svc = _FakeService(repo, storage_dir=str(tmp_path), stall_timeout=1)

    await repo.create(id="stuck", user_id="u1", domain="test", task_type="demo", payload="{}")
    await repo.update("stuck", status="rendering", progress=75)  # looks "in progress"
    # age it past the 1s window
    old = datetime.now(UTC) - timedelta(minutes=10)
    async with repo._sf() as session:
        await session.execute(sa_update(TaskRow).where(TaskRow.id == "stuck").values(updated_at=old))
        await session.commit()

    reaped = await svc.reap_stalled_tasks()
    assert reaped == 1

    task = await repo.get("stuck")
    assert task.status == "failed"
    assert task.progress == 75  # progress untouched — only status flips
    # 2026-08-24 活性重构（W6）：reap 文案改为 "Reaped by watchdog (predicate, ...)"。
    assert "Reaped by watchdog" in (task.error_message or ""), task.error_message


@pytest.mark.asyncio
async def test_reap_stalled_tasks_leaves_fresh_and_terminal_tasks_alone(repo, tmp_path):
    """Fresh in-progress tasks and already-terminal tasks are not reaped."""
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import update as sa_update

    svc = _FakeService(repo, storage_dir=str(tmp_path), stall_timeout=1)

    # fresh running task → NOT reaped
    await repo.create(id="fresh", user_id="u1", domain="test", task_type="demo", payload="{}")
    await repo.update("fresh", status="running", progress=10)

    # old but already FAILED → NOT reaped (terminal; reaper only touches non-terminal)
    await repo.create(id="already_failed", user_id="u1", domain="test", task_type="demo", payload="{}")
    await repo.update("already_failed", status="failed", progress=75)
    old = datetime.now(UTC) - timedelta(hours=2)
    async with repo._sf() as session:
        await session.execute(sa_update(TaskRow).where(TaskRow.id == "already_failed").values(updated_at=old))
        await session.commit()

    reaped = await svc.reap_stalled_tasks()
    assert reaped == 0
    assert (await repo.get("fresh")).status == "running"
    assert (await repo.get("already_failed")).status == "failed"


@pytest.mark.asyncio
async def test_reap_stalled_tasks_disabled_when_timeout_zero(repo, tmp_path):
    """stall_timeout=0 opts out — reaper is a no-op (per-domain config flip)."""
    svc = _FakeService(repo, storage_dir=str(tmp_path), stall_timeout=0)
    await repo.create(id="t", user_id="u1", domain="test", task_type="demo", payload="{}")
    await repo.update("t", status="running")
    # don't even need to age it — disabled means no query
    assert await svc.reap_stalled_tasks() == 0


@pytest.mark.asyncio
async def test_reap_stalled_tasks_catches_fake_busy_progress_unchanged(repo, tmp_path):
    """Regression (vg_f309ae0f32f5): a pipeline that keeps re-emitting the SAME
    progress value (so updated_at keeps refreshing) while actually wedged must
    still be reaped. vimax hung on shot 1 image-gen but kept pushing 30% every
    tick — the old updated_at-only check never fired because updated_at was
    always recent. The progress-no-change predicate closes this blind spot.

    Setup: task at progress=30 with a FRESH updated_at (recent write), but we
    inject a stale baseline so the reaper sees progress 30 as unchanged since
    before the cutoff. A single reap call should flip it to failed."""
    from datetime import UTC, datetime, timedelta

    svc = _FakeService(repo, storage_dir=str(tmp_path), stall_timeout=60)

    await repo.create(id="fakebusy", user_id="u1", domain="test", task_type="demo", payload="{}")
    await repo.update("fakebusy", status="rendering", progress=30)  # FRESH updated_at = recent

    # simulate: reaper already observed progress=30 one stall-window ago, and the
    # task hasn't advanced since. Plant the baseline at (30, now-2h).
    old_baseline = datetime.now(UTC) - timedelta(hours=2)
    svc._progress_baseline["fakebusy"] = (30, old_baseline)

    reaped = await svc.reap_stalled_tasks()
    assert reaped == 1

    task = await repo.get("fakebusy")
    assert task.status == "failed"
    assert task.progress == 30
    assert "Reaped by watchdog" in (task.error_message or ""), task.error_message


@pytest.mark.asyncio
async def test_reap_stalled_tasks_progress_actually_advancing_not_reaped(repo, tmp_path):
    """A task whose progress value genuinely changes resets its baseline clock —
    it must NOT be reaped even if a previous baseline existed. This guards against
    the reaper false-positiving on legitimately-slow-but-progressing tasks."""
    from datetime import UTC, datetime, timedelta

    svc = _FakeService(repo, storage_dir=str(tmp_path), stall_timeout=60)

    await repo.create(id="advancing", user_id="u1", domain="test", task_type="demo", payload="{}")
    # old baseline said progress was 10 two hours ago; but the task has since
    # advanced to 40 — that's real progress, the baseline resets, no reap.
    await repo.update("advancing", status="rendering", progress=40)
    svc._progress_baseline["advancing"] = (10, datetime.now(UTC) - timedelta(hours=2))

    reaped = await svc.reap_stalled_tasks()
    assert reaped == 0
    assert (await repo.get("advancing")).status == "rendering"
    # baseline now tracks the new progress value (40) with a fresh clock
    assert svc._progress_baseline["advancing"][0] == 40


@pytest.mark.asyncio
async def test_start_and_stop_stall_reaper_lifecycle(repo, tmp_path):
    """start_stall_reaper launches a background task; stop cancels it cleanly."""

    svc = _FakeService(repo, storage_dir=str(tmp_path), stall_timeout=1)
    assert svc._reaper_task is None

    svc.start_stall_reaper(interval=3600)  # long interval so the loop sleeps
    assert svc._reaper_task is not None
    assert not svc._reaper_task.done()

    # idempotent: calling again does not spawn a second reaper
    first = svc._reaper_task
    svc.start_stall_reaper(interval=3600)
    assert svc._reaper_task is first

    await svc.stop_stall_reaper()
    assert svc._reaper_task is None
    assert first.cancelled() or first.done()

    # disabled service never starts a reaper
    svc_off = _FakeService(repo, storage_dir=str(tmp_path), stall_timeout=0)
    svc_off.start_stall_reaper()
    assert svc_off._reaper_task is None


# ---------------------------------------------------------------------------
# MetricsRegistry tests
# ---------------------------------------------------------------------------


def test_counter_increment():
    reg = MetricsRegistry()
    reg.inc("test_total", labels={"x": "1"})
    assert reg.counter("test_total").get(labels={"x": "1"}) == 1.0
    reg.inc("test_total", labels={"x": "1"})
    assert reg.counter("test_total").get(labels={"x": "1"}) == 2.0


def test_gauge_set_and_get():
    reg = MetricsRegistry()
    reg.set("pending", 5, labels={"domain": "vg"})
    assert reg.gauge("pending").get(labels={"domain": "vg"}) == 5.0
    reg.set("pending", 3, labels={"domain": "vg"})
    assert reg.gauge("pending").get(labels={"domain": "vg"}) == 3.0


def test_histogram_observe():
    reg = MetricsRegistry()
    reg.observe("duration", 12.5, labels={"type": "ai_image"})
    snap = reg.histogram("duration").snapshot()
    assert len(snap) == 1
    key = list(snap.keys())[0]
    assert snap[key]["count"] == 1
    assert snap[key]["sum"] == 12.5


def test_render_prometheus_format():
    reg = MetricsRegistry()
    reg.inc("tasks_total", labels={"status": "ok"}, help_text="Total tasks")
    reg.observe("duration_seconds", 5.0, labels={"phase": "prepare"}, help_text="Duration")
    output = reg.render()
    assert "# TYPE tasks_total counter" in output
    assert "# TYPE duration_seconds histogram" in output
    # Prefix match: observed labels (e.g. phase="prepare") merge into the same {}
    # so the exact '{le="+Inf"}' suffix never appears; match the opening instead.
    assert 'duration_seconds_bucket{le="+Inf"' in output
    assert "duration_seconds_count" in output


def test_parse_key_roundtrip():
    from app.gateway._shared.task_framework.metrics import _parse_key

    labels = {"domain": "video_gen", "status": "ready"}
    from app.gateway._shared.task_framework.metrics import _label_key

    key = _label_key(labels)
    assert _parse_key(key) == labels


@pytest.mark.asyncio
async def test_progress_updater_is_monotonic_within_run():
    """ProgressUpdater clamps progress to a per-run high-water mark so the bar
    never moves backward within a run (e.g. prep 75% → ViMax extract 5%). The
    high-water is per-instance (NOT lazy-loaded from the stored value — that masked
    renders at a stale 100% from the draft phase)."""
    from unittest.mock import AsyncMock

    from app.gateway._shared.task_framework.service import ProgressUpdater

    repo = AsyncMock()
    repo.update = AsyncMock()

    pu = ProgressUpdater(repo, "t1")
    await pu(progress=75, status="running")  # prep finishes at 75 → high-water 75
    assert repo.update.call_args.kwargs["progress"] == 75
    await pu(progress=5)  # ViMax extract — clamped to 75 (no backward jump)
    assert repo.update.call_args.kwargs["progress"] == 75
    await pu(progress=80)  # forward
    assert repo.update.call_args.kwargs["progress"] == 80
    await pu(progress=50)  # backward — held at 80
    assert repo.update.call_args.kwargs["progress"] == 80
    # status-only calls (no progress) update without touching the clamp
    await pu(status="running")
    assert "progress" not in repo.update.call_args.kwargs


class _ScalingFakeService(_FakeService):
    """_FakeService + per-task stall_timeout override (mirrors VideoGenService.E5b)."""

    def stall_timeout_for_task(self, task) -> int:
        return 5040  # 模拟 54 帧长渲染的缩放 timeout


@pytest.mark.asyncio
async def test_reaper_uses_per_task_scaled_timeout(repo, tmp_path):
    """F1: reaper 须用 per-task 缩放 timeout（stall_timeout_for_task hook），非全局固定。

    54 帧任务 stale 2000s（< scaled 5040）不应 reap；stale 6000s（> 5040）应 reap。
    证明 effective_stall_timeout 真正接入 reaper + per-task 缩放生效（非死代码）。
    """
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import update as sa_update

    from app.gateway._shared.task_framework.models import TaskRow

    svc = _ScalingFakeService(repo, storage_dir=str(tmp_path), stall_timeout=1800)
    await repo.create(id="stuck", user_id="u1", domain="test", task_type="demo", payload="{}")
    await repo.update("stuck", status="rendering", progress=75)

    # stale 2000s（< scaled 5040）→ 不应 reap
    old = datetime.now(UTC) - timedelta(seconds=2000)
    async with repo._sf() as session:
        await session.execute(sa_update(TaskRow).where(TaskRow.id == "stuck").values(updated_at=old))
        await session.commit()
    assert await svc.reap_stalled_tasks() == 0
    assert (await repo.get("stuck")).status == "rendering"

    # stale 6000s（> scaled 5040）→ 应 reap
    older = datetime.now(UTC) - timedelta(seconds=6000)
    async with repo._sf() as session:
        await session.execute(sa_update(TaskRow).where(TaskRow.id == "stuck").values(updated_at=older))
        await session.commit()
    assert await svc.reap_stalled_tasks() == 1
    assert (await repo.get("stuck")).status == "failed"


# ---------------------------------------------------------------------------
# T6（2026-08-14 计划）：progress 冻结固定窗口谓词（20min）。
# 修前 progress-unchanged 谓词（B）的窗口 = 按帧数缩放的 stall_timeout
# （140 帧 = 2.8h）——「有限但巨大」的预算让 20 分钟内无人收尸。新谓词 C 用
# 固定 20min 窗口直接判失败；用户等待态（DRAFT/STORY_READY）豁免——
# 空闲草稿是健康等待不是卡死。
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reaper_frozen_progress_fixed_window_triggers(repo, tmp_path):
    """progress 冻结 20min（B 谓词的缩放窗口 30min 还没到）→ 固定窗口谓词先行收尸。"""
    from datetime import UTC, datetime, timedelta

    svc = _FakeService(repo, storage_dir=str(tmp_path), stall_timeout=1800)
    svc.progress_stall_timeout = 1200  # 20min

    await repo.create(id="frozen", user_id="u1", domain="test", task_type="demo", payload="{}")
    await repo.update("frozen", status="running", progress=10)  # fresh updated_at → A 不触发

    # progress=10 已 21 分钟没变（> 20min 固定窗口；< 30min 缩放窗口）
    old_baseline = datetime.now(UTC) - timedelta(minutes=21)
    svc._progress_baseline["frozen"] = (10, old_baseline)

    reaped = await svc.reap_stalled_tasks()
    assert reaped == 1
    task = await repo.get("frozen")
    assert task.status == "failed"
    assert "1200s" in (task.error_message or ""), f"错误消息应报固定窗口而非缩放窗口：{task.error_message}"


@pytest.mark.asyncio
async def test_reaper_frozen_progress_within_window_not_reaped(repo, tmp_path):
    """progress 冻结 13min（ai_video 含续写单帧合法最坏 ≈13.5min）→ 不误杀（C2 防误杀回归）。"""
    from datetime import UTC, datetime, timedelta

    svc = _FakeService(repo, storage_dir=str(tmp_path), stall_timeout=1800)
    svc.progress_stall_timeout = 1200

    await repo.create(id="slowframe", user_id="u1", domain="test", task_type="demo", payload="{}")
    await repo.update("slowframe", status="running", progress=75)
    svc._progress_baseline["slowframe"] = (75, datetime.now(UTC) - timedelta(minutes=13))

    assert await svc.reap_stalled_tasks() == 0
    assert (await repo.get("slowframe")).status == "running"


@pytest.mark.asyncio
async def test_reaper_fixed_window_respects_advancing_progress(repo, tmp_path):
    """progress 值真实推进 → baseline 重置，固定窗口谓词不触发。"""
    from datetime import UTC, datetime, timedelta

    svc = _FakeService(repo, storage_dir=str(tmp_path), stall_timeout=1800)
    svc.progress_stall_timeout = 1200

    await repo.create(id="adv", user_id="u1", domain="test", task_type="demo", payload="{}")
    await repo.update("adv", status="running", progress=40)
    svc._progress_baseline["adv"] = (10, datetime.now(UTC) - timedelta(hours=2))

    assert await svc.reap_stalled_tasks() == 0
    assert svc._progress_baseline["adv"][0] == 40  # baseline 追踪新值


@pytest.mark.asyncio
async def test_reaper_fixed_window_skips_user_awaiting_statuses(repo, tmp_path):
    """DRAFT/STORY_READY（用户等待确认）冻结多久都不被固定窗口谓词收尸——空闲草稿是健康等待。"""
    from datetime import UTC, datetime, timedelta

    # 缩放窗口 10000s（≈2.8h）> 基线 2h → B 谓词不触发，本测试只检验 C 谓词的豁免
    svc = _FakeService(repo, storage_dir=str(tmp_path), stall_timeout=10000)
    svc.progress_stall_timeout = 60

    for status in ("draft", "story_ready"):
        tid = f"awaiting_{status}"
        await repo.create(id=tid, user_id="u1", domain="test", task_type="demo", payload="{}")
        await repo.update(tid, status=status, progress=100)
        svc._progress_baseline[tid] = (100, datetime.now(UTC) - timedelta(hours=2))

    assert await svc.reap_stalled_tasks() == 0
    assert (await repo.get("awaiting_draft")).status == "draft"
    assert (await repo.get("awaiting_story_ready")).status == "story_ready"


@pytest.mark.asyncio
async def test_reaper_fixed_window_off_by_default_for_other_domains(repo, tmp_path):
    """progress_stall_timeout=None（默认）→ 谓词 C 关闭，其他域行为不变（仍走 A/B）。"""
    from datetime import UTC, datetime, timedelta

    svc = _FakeService(repo, storage_dir=str(tmp_path), stall_timeout=60)
    assert svc.progress_stall_timeout is None

    await repo.create(id="frozen_b", user_id="u1", domain="test", task_type="demo", payload="{}")
    await repo.update("frozen_b", status="running", progress=10)
    svc._progress_baseline["frozen_b"] = (10, datetime.now(UTC) - timedelta(minutes=30))

    # B 谓词（60s 窗口）正常收尸
    assert await svc.reap_stalled_tasks() == 1
    assert (await repo.get("frozen_b")).status == "failed"
