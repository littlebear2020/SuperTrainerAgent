"""Task 25：worker 租约三连（H6 + W-2 + W-7）。

审计把这块拆成五条，核实后 **W-2 不成立**（`_run_task` 确有
`_concurrency.acquire()`，真并发只有 MAX_CONCURRENT_TASKS 个），但其余三条
串成一条真链，且每次 `docker compose up -d` 重部署都会触发：

1. **租约从不续期**：全仓只在 claim 时写 `lease_expires_at`、终态时清，
   渲染期零续租。长任务（排队 + 渲染）可超 `effective_lease` 上限。
2. **`recover_stale_tasks` 把过期租约直接判 `failed`**（不是重排队），
   且 3 个 replica 各自都在跑这个回收循环 → 一个**正在正常渲染**的长任务
   会被自己或兄弟 replica 打成「Worker restarted during processing」。
3. **SIGTERM 不 join 在跑线程**：`_sigterm_handler` 立刻
   `release_my_leases()` 把任务重置为可认领的 `composing`，旧线程仍在渲染，
   另一 replica 立刻认领同一任务 → 双处理、产物互相覆盖。

本测试锁三条不变量：
- 渲染期心跳会推进 `lease_expires_at`（使 2 的谓词不再命中健康任务）
- `recover_stale_tasks` 对未超 `max_retries` 的任务**重排队**并计 `retry_count`
- SIGTERM 先置 draining 停认领、join 在跑线程后才释放租约
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import UTC, datetime, timedelta

import pytest

import worker as W


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """建一张最小 unified_tasks 表，并把 worker 的 DB_PATH 指过去。"""
    path = tmp_path / "tasks.db"
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE unified_tasks (
            id TEXT PRIMARY KEY,
            user_id TEXT,
            domain TEXT,
            task_type TEXT,
            status TEXT,
            progress INTEGER DEFAULT 0,
            payload TEXT,
            result TEXT,
            error_message TEXT,
            claimed_by TEXT,
            claimed_at TEXT,
            lease_expires_at TEXT,
            retry_count INTEGER DEFAULT 0,
            priority INTEGER DEFAULT 0,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(W, "DB_PATH", str(path))
    return path


def _insert(db_path, task_id: str, *, status="composing", lease_offset_s: int | None = None, claimed_by=None, retry_count=0):
    now = datetime.now(UTC)
    lease = (now + timedelta(seconds=lease_offset_s)).isoformat() if lease_offset_s is not None else None
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO unified_tasks (id, user_id, domain, task_type, status, payload, claimed_by, lease_expires_at, retry_count, created_at, updated_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (task_id, "u1", "video", "ai_image", status, "{}", claimed_by, lease, retry_count, now.isoformat(), now.isoformat()),
    )
    conn.commit()
    conn.close()


def _row(db_path, task_id: str) -> dict:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return dict(conn.execute("SELECT * FROM unified_tasks WHERE id = ?", (task_id,)).fetchone())
    finally:
        conn.close()


# ---------------------------------------------------------------- 心跳续租


def test_renew_lease_pushes_expiry_forward(db):
    """`renew_lease` 必须把 lease_expires_at 推到未来。"""
    _insert(db, "t1", lease_offset_s=5, claimed_by=W.WORKER_ID)
    before = _row(db, "t1")["lease_expires_at"]

    W.renew_lease("t1")

    after = _row(db, "t1")["lease_expires_at"]
    assert after > before, "续租必须推进过期时间"
    assert datetime.fromisoformat(after) > datetime.now(UTC) + timedelta(seconds=60), "续租后应远离当前时刻，而不是只挪几秒"


def test_heartbeat_keeps_long_task_out_of_recover(db):
    """真链核心：渲染耗时 > 初始租约时，心跳续租使任务**不被**判 failed。"""
    # 租约 1 秒后就过期 —— 模拟「长任务跑过了租约」
    _insert(db, "t2", lease_offset_s=-1, claimed_by=W.WORKER_ID)

    # 渲染中的一次心跳
    W.renew_lease("t2")
    W.recover_stale_tasks()

    row = _row(db, "t2")
    assert row["status"] == "composing", f"心跳续租后不得被回收，实际 status={row['status']}"
    assert row["error_message"] is None, "健康任务不应带 'Worker restarted' 错误"


def test_progress_cb_renews_lease(db, monkeypatch, tmp_path):
    """`progress_cb` 每次被调用都应顺带续租（复用管线已有的逐帧回调）。"""
    _insert(db, "t3", lease_offset_s=5, claimed_by=W.WORKER_ID)
    before = _row(db, "t3")["lease_expires_at"]

    calls: list[str] = []
    real_renew = W.renew_lease

    def _spy(task_id: str) -> None:
        calls.append(task_id)
        real_renew(task_id)

    monkeypatch.setattr(W, "renew_lease", _spy)
    monkeypatch.setattr(W, "is_task_cancelled", lambda _tid: False)

    cb = W._make_progress_cb("t3")
    cb(42)

    assert calls == ["t3"], "progress_cb 必须调用 renew_lease"
    assert _row(db, "t3")["lease_expires_at"] > before
    assert _row(db, "t3")["progress"] == 42, "续租不得破坏既有的进度写入（含高水位钳制）"


# ------------------------------------------------------- recover 改重排队


def test_recover_requeues_instead_of_failing(db):
    """过期租约且未超 max_retries → 重排队（可再被认领），并计 retry_count。"""
    _insert(db, "t4", lease_offset_s=-10, claimed_by="dead-worker", retry_count=0)

    W.recover_stale_tasks()

    row = _row(db, "t4")
    assert row["status"] == "composing", "未超重试上限应重排队，而不是 failed"
    assert row["claimed_by"] is None, "必须放开 claimed_by 才能被别的 replica 认领"
    assert row["lease_expires_at"] is None
    assert row["retry_count"] == 1, "重排队必须计次，否则会无限重排"
    assert row["error_message"] is None


def test_recover_fails_task_after_max_retries(db):
    """重试耗尽 → 才判 failed（防无限重排队）。"""
    _insert(db, "t5", lease_offset_s=-10, claimed_by="dead-worker", retry_count=W.MAX_LEASE_RETRIES)

    W.recover_stale_tasks()

    row = _row(db, "t5")
    assert row["status"] == "failed", "超过 max_retries 必须落 failed"
    assert row["error_message"] and "retr" in row["error_message"].lower()


def test_recover_leaves_live_lease_untouched(db):
    """未过期租约不动（持有者可能仍活着）——保留既有行为。"""
    _insert(db, "t6", lease_offset_s=600, claimed_by="other-worker")

    W.recover_stale_tasks()

    row = _row(db, "t6")
    assert row["claimed_by"] == "other-worker"
    assert row["retry_count"] == 0


# --------------------------------------------------------- SIGTERM 先 join


def test_drain_joins_running_threads_before_releasing(db):
    """W-7 / 审查发现 11：drain 必须先 join 在跑线程，之后才释放租约。

    断言顺序：release_my_leases 被调用时，在跑线程必须已结束。否则旧线程
    继续渲染而任务已可被别的 replica 认领 → 双处理。
    （P2-11 起 join/release 从信号处理器挪到 main() 的 _drain_running_tasks；
    处理器只置标志位，见 test_sigterm_handler_is_flag_only_nonblocking。）
    """
    order: list[str] = []
    started = threading.Event()
    release_seen_alive: list[bool] = []

    def _slow_task() -> None:
        started.set()
        order.append("task-start")
        _shutdown_wait = W._shutdown.wait(2.0)  # noqa: F841  模拟收到停机信号后收尾
        order.append("task-end")

    t = threading.Thread(target=_slow_task, daemon=True)
    W._register_running_thread(t)
    t.start()
    started.wait(1.0)

    def _fake_release() -> None:
        release_seen_alive.append(t.is_alive())
        order.append("release")

    original = W.release_my_leases
    W.release_my_leases = _fake_release  # type: ignore[assignment]
    try:
        W._sigterm_handler(15, None)  # 只置标志位
        W._drain_running_tasks()      # join + release 在这里
    finally:
        W.release_my_leases = original  # type: ignore[assignment]
        W._shutdown.clear()
        W._draining.clear()

    assert release_seen_alive == [False], "释放租约时在跑线程必须已 join 结束，否则会双认领"
    assert order.index("task-end") < order.index("release")


def test_sigterm_handler_is_flag_only_nonblocking(db):
    """P2-11：信号处理器只置标志位、立刻返回——不得在处理器里 join。

    在跑线程未结束时调用处理器，若它在处理器内 join 会阻塞最多
    SHUTDOWN_JOIN_TIMEOUT；Docker 默认 10s grace 一到 SIGKILL，
    release_my_leases 永远不执行。
    """
    import time as _time

    block = threading.Event()

    def _stuck_task() -> None:
        block.wait(5.0)  # 测试期间永不结束

    t = threading.Thread(target=_stuck_task, daemon=True)
    W._register_running_thread(t)
    t.start()

    release_called: list[bool] = []
    original = W.release_my_leases
    W.release_my_leases = lambda: release_called.append(True)  # type: ignore[assignment]
    try:
        t0 = _time.monotonic()
        W._sigterm_handler(15, None)
        elapsed = _time.monotonic() - t0
        assert elapsed < 1.0, f"信号处理器必须立刻返回，实际阻塞 {elapsed:.2f}s"
        assert release_called == [], "处理器内不得释放租约（应留给 main 的 drain）"
        assert W._draining.is_set() and W._shutdown.is_set()
    finally:
        W.release_my_leases = original  # type: ignore[assignment]
        W._shutdown.clear()
        W._draining.clear()
        block.set()


def test_sigterm_sets_draining_to_stop_claiming(db):
    """收到 SIGTERM 后应立刻置 draining，主循环不得再认领新任务。"""
    W._draining.clear()
    original = W.release_my_leases
    W.release_my_leases = lambda: None  # type: ignore[assignment]
    try:
        W._sigterm_handler(15, None)
        assert W._draining.is_set(), "SIGTERM 必须置 draining 停止认领"
        assert W._shutdown.is_set()
    finally:
        W.release_my_leases = original  # type: ignore[assignment]
        W._shutdown.clear()
        W._draining.clear()


# ── 审查发现 4：renew_lease 不校验 claimed_by，长帧结束后仍可双处理 ─────────
#
# 原 docstring 说「刻意不校验 claimed_by」，理由只覆盖 SIGTERM 路径，**没覆盖
# 同一 commit 引入的 recover_stale_tasks 重排队路径**：
#   A 某帧耗时 > 租约 → 过期 → recover 置 claimed_by=NULL → B 认领 →
#   A 帧结束触发 progress_cb → is_task_cancelled 只看 status（仍 composing）→
#   返 False → A 继续渲染且 renew_lease 覆盖 B 的行 → 双处理、产物互相覆盖。


def test_renew_lease_noop_when_task_reassigned(db):
    """任务已被别的 worker 认领 → 本 worker 的续租不得生效。"""
    _insert(db, "r1", lease_offset_s=600, claimed_by="worker-B")
    before = _row(db, "r1")["lease_expires_at"]

    # 归属不符时会抛 TaskCancelled（比静默 no-op 更好：能让本地渲染尽快停）
    with pytest.raises(W.TaskCancelled):
        W.renew_lease("r1")

    assert _row(db, "r1")["lease_expires_at"] == before, "不得覆盖他人持有的租约行"
    assert _row(db, "r1")["claimed_by"] == "worker-B"


def test_renew_lease_raises_cancelled_when_reassigned(db):
    """续租发现自己已不是持有者 → 抛 TaskCancelled 让本地渲染尽快停。"""
    _insert(db, "r2", lease_offset_s=600, claimed_by="worker-B")

    with pytest.raises(W.TaskCancelled):
        W.renew_lease("r2")


def test_cancel_predicate_detects_reassignment(db):
    """is_task_cancelled 须把「任务已被别人认领」也视为该停。"""
    _insert(db, "r3", lease_offset_s=600, claimed_by="worker-B")
    assert W.is_task_cancelled("r3") is True


def test_cancel_predicate_ok_when_still_mine(db):
    """自己仍是持有者 → 不算取消（不得误杀正常渲染）。"""
    _insert(db, "r4", lease_offset_s=600, claimed_by=W.WORKER_ID)
    assert W.is_task_cancelled("r4") is False


def test_cancel_predicate_ok_when_unclaimed(db):
    """claimed_by 为 NULL（回收后尚无人认领）不算取消——避免自己刚被回收就自杀，
    留给 recover/claim 流程决定归属。"""
    _insert(db, "r5", lease_offset_s=600, claimed_by=None)
    assert W.is_task_cancelled("r5") is False


def test_renew_lease_works_when_still_owner(db):
    """自己持有时续租照常生效（不得因加固而失效）。"""
    _insert(db, "r6", lease_offset_s=5, claimed_by=W.WORKER_ID)
    before = _row(db, "r6")["lease_expires_at"]

    W.renew_lease("r6")

    assert _row(db, "r6")["lease_expires_at"] > before


# ---------------------------------------------------------------- P1-8 动态租约

def _insert_with_frames(db_path, task_id: str, n_frames: int, *, claimed_by):
    """_insert 的 payload 带 frames 版本（_insert 固定写 '{}'）。"""
    import json as _json
    now = datetime.now(UTC)
    lease = (now + timedelta(seconds=60)).isoformat()  # 即将过期，靠 renew 续
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO unified_tasks (id, user_id, domain, task_type, status, payload, claimed_by, lease_expires_at, retry_count, created_at, updated_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (task_id, "u1", "video", "ai_image", "composing",
         _json.dumps({"frames": [{"index": i} for i in range(n_frames)]}),
         claimed_by, lease, 0, now.isoformat(), now.isoformat()),
    )
    conn.commit()
    conn.close()


def test_renew_lease_preserves_dynamic_long_lease(db):
    """P1-8：200 帧任务 claim 租约 ≈63min，renew 不得把它塌缩回静态 30min。"""
    _insert_with_frames(db, "long1", 200, claimed_by=W.WORKER_ID)
    W.renew_lease("long1")
    row = _row(db, "long1")
    remaining = (datetime.fromisoformat(row["lease_expires_at"]) - datetime.now(UTC)).total_seconds()
    expected = W.effective_lease(200)  # min(1800+2000, 7200) = 3800
    assert remaining > W.LEASE_DURATION + 600, (
        f"动态租约被塌缩：剩 {remaining:.0f}s，静态上限 {W.LEASE_DURATION}s"
    )
    assert abs(remaining - expected) < 60, f"应与 claim 同公式 ≈{expected}s，实得 {remaining:.0f}s"


def test_renew_lease_short_task_uses_base_lease(db):
    """≤30 帧与 claim 对齐：用静态基础租约，而不是 effective_lease 的微小加成。"""
    _insert_with_frames(db, "short1", 5, claimed_by=W.WORKER_ID)
    W.renew_lease("short1")
    row = _row(db, "short1")
    remaining = (datetime.fromisoformat(row["lease_expires_at"]) - datetime.now(UTC)).total_seconds()
    assert abs(remaining - W.LEASE_DURATION) < 60, f"短任务应 ≈{W.LEASE_DURATION}s，实得 {remaining:.0f}s"


def test_renew_lease_malformed_payload_falls_back_to_base(db):
    """payload 解析失败 → 基础租约兜底（与 claim 的 except 分支语义一致）。"""
    now = datetime.now(UTC)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO unified_tasks (id, user_id, domain, task_type, status, payload, claimed_by, lease_expires_at, retry_count, created_at, updated_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        ("bad1", "u1", "video", "ai_image", "composing", "{not json",
         W.WORKER_ID, (now + timedelta(seconds=60)).isoformat(), 0, now.isoformat(), now.isoformat()),
    )
    conn.commit()
    conn.close()
    W.renew_lease("bad1")
    row = _row(db, "bad1")
    remaining = (datetime.fromisoformat(row["lease_expires_at"]) - datetime.now(UTC)).total_seconds()
    assert abs(remaining - W.LEASE_DURATION) < 60
