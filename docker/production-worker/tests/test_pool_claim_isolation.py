"""T3 双池认领隔离守卫（2026-09-08 remotion-course 计划）。

锁定四条不变量：
1. ffmpeg 池白名单 = registry PIPELINES 全键 task_type 集**扣除** remotion 类型
   （代码派生、禁止手写——手写白名单曾漏 (video_gen, mixed)；跨 domain 含
   ppt_to_video，按 domain 过滤会把 PPT 任务挤出白名单）。
2. remotion 池认领谓词只放行 remotion_course / remotion_still。
3. 两池认领互斥（正反向用例）：同一表里混插两类任务，各自只认领本池的。
4. registry 已注册 (video_gen, remotion_course)——缺此步认领后
   resolve_pipeline 抛 UnsupportedTaskType 直接标 failed（v3 评审 C5 补回项）。
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import UTC, datetime

import pytest

import worker as W
from pipelines.registry import PIPELINES, resolve_pipeline


@pytest.fixture()
def db(tmp_path, monkeypatch):
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


def _insert(db_path, task_id: str, task_type: str, *, priority: int = 0, payload: str = "{}"):
    now = datetime.now(UTC).isoformat()
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO unified_tasks (id, user_id, domain, task_type, status, payload, priority, created_at, updated_at)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        (task_id, "u1", "video_gen", task_type, "composing", payload, priority, now, now),
    )
    conn.commit()
    conn.close()


def _claimed_by(db_path, task_id: str) -> str | None:
    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT claimed_by FROM unified_tasks WHERE id = ?", (task_id,)).fetchone()
    conn.close()
    return row[0]


@pytest.fixture()
def ffmpeg_pool(monkeypatch):
    monkeypatch.setattr(W, "WORKER_POOL", "ffmpeg")
    return W


@pytest.fixture()
def remotion_pool(monkeypatch):
    monkeypatch.setattr(W, "WORKER_POOL", "remotion")
    return W


class TestWhitelistDerivation:
    def test_ffmpeg_whitelist_equals_registry_keys_minus_remotion(self):
        expected = {tt for (_d, tt) in PIPELINES if tt not in W.REMOTION_TASK_TYPES}
        assert set(W._ffmpeg_pool_whitelist()) == expected

    def test_whitelist_cross_domain(self):
        """跨 domain 全键派生：PPT 任务必须在 ffmpeg 白名单内（漏=永久无人认领）。"""
        assert "ppt_to_video" in W._ffmpeg_pool_whitelist()

    def test_whitelist_includes_mixed(self):
        """手写白名单曾漏 (video_gen, mixed)——派生集合必须包含。"""
        assert "mixed" in W._ffmpeg_pool_whitelist()

    def test_whitelist_excludes_remotion_types(self):
        wl = W._ffmpeg_pool_whitelist()
        assert "remotion_course" not in wl
        assert "remotion_still" not in wl

    def test_new_registry_key_derives_into_whitelist_formula(self):
        """派生性质自检：PIPELINES 新增非 remotion 键时白名单公式自动覆盖。"""
        fake = dict(PIPELINES)
        fake[("video_gen", "hypothetical_new")] = fake[("video_gen", "mixed")]
        tt_set = {tt for (_d, tt) in fake if tt not in W.REMOTION_TASK_TYPES}
        assert "hypothetical_new" in tt_set


class TestClaimIsolation:
    def test_ffmpeg_pool_claims_only_ffmpeg_task(self, db, ffmpeg_pool):
        _insert(db, "t-remotion", "remotion_course", priority=100)
        _insert(db, "t-ffmpeg", "ai_image", priority=10)
        task = ffmpeg_pool.db_claim_task()
        assert task is not None
        assert task["id"] == "t-ffmpeg"

    def test_remotion_pool_claims_only_remotion_task(self, db, remotion_pool):
        _insert(db, "t-remotion", "remotion_course", priority=10)
        _insert(db, "t-ffmpeg", "ai_image", priority=100)
        task = remotion_pool.db_claim_task()
        assert task is not None
        assert task["id"] == "t-remotion"

    def test_remotion_pool_claims_still_task(self, db, remotion_pool):
        _insert(db, "t-still", "remotion_still", priority=10)
        task = remotion_pool.db_claim_task()
        assert task is not None
        assert task["id"] == "t-still"

    def test_ffmpeg_pool_skips_remotion_still(self, db, ffmpeg_pool):
        _insert(db, "t-still", "remotion_still", priority=100)
        assert ffmpeg_pool.db_claim_task() is None

    def test_remotion_pool_skips_unknown_type(self, db, remotion_pool):
        """未知 task_type（非 remotion 白名单）remotion 池不认领——留给 ffmpeg 池
        的 UnsupportedTaskType 标 failed 语义由 ffmpeg 池承担。"""
        _insert(db, "t-unknown", "some_future_type", priority=100)
        assert remotion_pool.db_claim_task() is None

    def test_two_pools_claim_disjoint_sets(self, db, monkeypatch):
        """正反向合用例：5 条混插，两池各取各的、互不越界。

        注意不能叠用 ffmpeg_pool/remotion_pool 两个 fixture（monkeypatch 叠加
        时后置者覆盖 WORKER_POOL，先到的池谓词失效）——测试体内顺序切换。
        """
        ids = {
            "t-img": "ai_image",
            "t-vid": "ai_video",
            "t-mixed": "mixed",
            "t-course": "remotion_course",
            "t-still": "remotion_still",
        }
        for tid, tt in ids.items():
            _insert(db, tid, tt)
        ffmpeg_ids = set()
        monkeypatch.setattr(W, "WORKER_POOL", "ffmpeg")
        while True:
            t = W.db_claim_task()
            if t is None:
                break
            ffmpeg_ids.add(t["id"])
        remotion_ids = set()
        monkeypatch.setattr(W, "WORKER_POOL", "remotion")
        while True:
            t = W.db_claim_task()
            if t is None:
                break
            remotion_ids.add(t["id"])
        assert ffmpeg_ids == {"t-img", "t-vid", "t-mixed"}
        assert remotion_ids == {"t-course", "t-still"}


class TestRegistryRegistration:
    def test_remotion_course_registered(self):
        from pipelines.compose_remotion import compose_remotion

        assert resolve_pipeline("video_gen", "remotion_course") is compose_remotion

    def test_remotion_still_registered(self):
        from pipelines.compose_remotion import compose_remotion

        assert resolve_pipeline("video_gen", "remotion_still") is compose_remotion

    def test_registry_has_both_pools_keys(self):
        assert ("video_gen", "remotion_course") in PIPELINES
        assert ("voice_production", "ppt_to_video") in PIPELINES


class TestDualSlotPairing:
    """T-A1（2026-09-09 R1）：渲染槽/still 槽信号量配对 + 认领循环结构。

    三条回归不变量（feedback-round1 计划批 A）：
    (a) only-still 队列 + 渲染槽空闲 → still 仍被认领（防饿死——修前 if/elif
        结构下渲染槽 acquire 成功即整轮跳过 elif，still 空闲期饿死）；
    (b) 配对：渲染槽只领 remotion_course（高优先级 still 不得被渲染槽抢走），
        still 槽只领 remotion_still；_run_task 按入口 slot 释放对应信号量；
    (c) 双槽占满 → 无认领。
    """

    def _remotion(self, monkeypatch):
        monkeypatch.setattr(W, "WORKER_POOL", "remotion")
        monkeypatch.setattr(W, "_still_concurrency", threading.Semaphore(1))
        return W

    def test_only_still_queue_still_claimed_when_render_slot_free(self, db, monkeypatch):
        """(a) 防饿死核心用例：渲染槽空闲、队列里只有 still → 渲染槽 claim 落空
        后必须同轮回落试 still 槽（修前 elif 结构返回 None → still 永久饿死）。"""
        self._remotion(monkeypatch)
        _insert(db, "t-still", "remotion_still", priority=10)
        task, slot = W._claim_one_task()
        assert task is not None and task["id"] == "t-still"
        assert slot == "still"

    def test_render_slot_never_claims_still_even_higher_priority(self, db, monkeypatch):
        """(b) 渲染槽谓词收窄：still 优先级更高也不被渲染槽领走（修前池默认谓词
        会领走 still 并按类型释放 still 槽 → 渲染许可泄漏）。"""
        self._remotion(monkeypatch)
        _insert(db, "t-course", "remotion_course", priority=10)
        _insert(db, "t-still", "remotion_still", priority=100)
        try:
            task, slot = W._claim_one_task()
            assert task["id"] == "t-course" and slot == "render"
            assert _claimed_by(db, "t-still") is None, "渲染槽不得领走 still 任务"
        finally:
            W._concurrency.release()

    def test_still_slot_claims_still_after_render_busy(self, db, monkeypatch):
        """(b) 反向：渲染槽占满（已领 course）时，still 槽独立认领 still。"""
        self._remotion(monkeypatch)
        acquired = 0
        while W._concurrency.acquire(blocking=False):
            acquired += 1
        try:
            _insert(db, "t-course", "remotion_course", priority=10)
            _insert(db, "t-still", "remotion_still", priority=5)
            task, slot = W._claim_one_task()
            assert task["id"] == "t-still" and slot == "still"
            assert _claimed_by(db, "t-course") is None
        finally:
            for _ in range(acquired):
                W._concurrency.release()

    def test_both_slots_full_no_claim(self, db, monkeypatch):
        """(c) 双槽同时占满 → 无第三认领（不超发）。"""
        self._remotion(monkeypatch)
        _insert(db, "t-course", "remotion_course")
        acquired = 0
        while W._concurrency.acquire(blocking=False):
            acquired += 1
        W._still_concurrency.acquire(blocking=False)
        try:
            task, slot = W._claim_one_task()
            assert task is None and slot is None
            assert _claimed_by(db, "t-course") is None
        finally:
            for _ in range(acquired):
                W._concurrency.release()
            W._still_concurrency.release()

    def test_run_task_releases_reserved_slot_not_inferred(self, monkeypatch):
        """(b) 释放配对：入口预占 render 槽跑 still 类型任务（防御性用例——当前
        谓词已隔离，但若未来谓词再漂移，释放错槽不得静默）→ 必须释放渲染槽。"""
        self._remotion(monkeypatch)
        render_sem, still_sem = W._concurrency, W._still_concurrency
        initial_render_slots = render_sem._value
        render_sem.acquire(blocking=False)  # 模拟主循环预占渲染槽
        monkeypatch.setattr(W, "process_task", lambda task: None)
        W._run_task(
            {"id": "t-x", "user_id": "u1", "domain": "video_gen", "task_type": "remotion_still"},
            slot_pre_acquired=True,
            slot="render",
        )
        assert render_sem._value == initial_render_slots and still_sem._value == 1, "释放须按入口 slot，不按 task_type 推断"

    def test_run_task_legacy_inference_still_compatible(self, monkeypatch):
        """slot=None 旧调用方兼容路径：按 task_type 推断仍释放 still 槽。"""
        self._remotion(monkeypatch)
        still_sem = W._still_concurrency
        still_sem.acquire()  # 模拟旧调用方自占槽（slot_pre_acquired=True 语义）
        monkeypatch.setattr(W, "process_task", lambda task: None)
        W._run_task(
            {"id": "t-y", "user_id": "u1", "domain": "video_gen", "task_type": "remotion_still"},
            slot_pre_acquired=True,
        )
        assert still_sem._value == 1

    def test_ffmpeg_pool_render_slot_keeps_default_predicate(self, db, monkeypatch):
        """ffmpeg 池渲染槽维持池默认谓词（None）——存量行为不变的回归锚。"""
        monkeypatch.setattr(W, "WORKER_POOL", "ffmpeg")
        assert W._render_slot_task_types() is None
        _insert(db, "t-img", "ai_image", priority=10)
        try:
            task, slot = W._claim_one_task()
            assert task["id"] == "t-img" and slot == "render"
        finally:
            W._concurrency.release()

    def test_render_slot_types_narrowed_for_remotion_pool(self, monkeypatch):
        monkeypatch.setattr(W, "WORKER_POOL", "remotion")
        assert W._render_slot_task_types() == ["remotion_course"]


class TestRemotionLease:
    def test_lease_reads_total_frames_not_storyboard_count(self):
        """remotion 分支读视频总帧数（fps×时长），不是 payload.frames 数组长度——
        19 场景 × 150 帧 + intro/outro 90×2 = 3030 帧 → 1800+1515=3315s。"""
        payload = (
            '{"pipeline_version": "remotion_course", "frames": [{}, {}, {}],'
            ' "remotion_props": {"fps": 30, "brand": {"intro": {"durationInFrames": 90},'
            ' "outro": {"durationInFrames": 90}},'
            ' "scenes": [{"durationInFrames": 150}]}}'
        )
        # 19 场景写起来太长，用 1 场景断言公式：150+90+90=330 帧 → 1800+165=1965s
        assert W._lease_for_payload(payload) == 1965

    def test_lease_caps_at_7200(self):
        payload = (
            '{"pipeline_version": "remotion_course",'
            ' "remotion_props": {"fps": 30, "scenes": [{"durationInFrames": 999999}]}}'
        )
        assert W._lease_for_payload(payload) == 7200

    def test_lease_falls_back_to_base_on_malformed_props(self):
        payload = '{"pipeline_version": "remotion_course"}'
        assert W._lease_for_payload(payload) == 1800

    def test_ffmpeg_payload_unchanged(self):
        """既有 ffmpeg 路径租约行为不变（30 帧以下走静态 LEASE_DURATION）。"""
        assert W._lease_for_payload('{"frames": [{}, {}]}') == 1800
