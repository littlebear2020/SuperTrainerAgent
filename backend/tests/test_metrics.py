"""Tests for the metrics module and task retry mechanism."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.gateway._shared.task_framework.metrics import (
    MetricsRegistry,
    _Counter,
    _format_labels,
    _Gauge,
    _Histogram,
    _label_key,
    _parse_key,
)

# ---------------------------------------------------------------------------
# Metrics registry tests
# ---------------------------------------------------------------------------


class TestCounter:
    def test_inc_default(self):
        c = _Counter("test_counter", "help")
        c.inc()
        assert c.get() == 1.0

    def test_inc_with_value(self):
        c = _Counter("test_counter", "help")
        c.inc(5.0)
        assert c.get() == 5.0

    def test_inc_with_labels(self):
        c = _Counter("test_counter", "help")
        c.inc(labels={"a": "1"})
        c.inc(labels={"a": "1"})
        c.inc(labels={"b": "2"})
        assert c.get(labels={"a": "1"}) == 2.0
        assert c.get(labels={"b": "2"}) == 1.0

    def test_snapshot(self):
        c = _Counter("test_counter", "help")
        c.inc(labels={"x": "1"})
        snap = c.snapshot()
        assert "x=1" in snap


class TestGauge:
    def test_set(self):
        g = _Gauge("test_gauge", "help")
        g.set(42.0)
        assert g.get() == 42.0

    def test_inc_dec(self):
        g = _Gauge("test_gauge", "help")
        g.inc(10.0)
        g.inc(5.0)
        g.dec(3.0)
        assert g.get() == 12.0

    def test_overwrite(self):
        g = _Gauge("test_gauge", "help")
        g.set(10.0)
        g.set(20.0)
        assert g.get() == 20.0


class TestHistogram:
    def test_observe_single(self):
        h = _Histogram("test_hist", "help")
        h.observe(1.5)
        snap = h.snapshot()
        assert "" in snap
        assert snap[""]["count"] == 1
        assert snap[""]["sum"] == 1.5

    def test_observe_with_labels(self):
        h = _Histogram("test_hist", "help")
        h.observe(1.0, labels={"type": "a"})
        h.observe(5.0, labels={"type": "a"})
        snap = h.snapshot()
        assert snap["type=a"]["count"] == 2
        assert snap["type=a"]["sum"] == 6.0

    def test_bucket_counts(self):
        h = _Histogram("test_hist", "help", buckets=(1.0, 5.0, 10.0))
        h.observe(0.5)
        h.observe(3.0)
        h.observe(7.0)
        h.observe(15.0)
        snap = h.snapshot()
        buckets = snap[""]["buckets"]
        assert buckets[1.0] == 1  # 0.5 <= 1.0
        assert buckets[5.0] == 2  # 0.5, 3.0 <= 5.0
        assert buckets[10.0] == 3  # 0.5, 3.0, 7.0 <= 10.0


class TestLabelHelpers:
    def test_label_key_empty(self):
        assert _label_key(None) == ""
        assert _label_key({}) == ""

    def test_label_key_sorted(self):
        assert _label_key({"b": "2", "a": "1"}) == "a=1,b=2"

    def test_parse_key_empty(self):
        assert _parse_key("") == {}

    def test_parse_key_roundtrip(self):
        labels = {"domain": "video_gen", "status": "ok"}
        key = _label_key(labels)
        assert _parse_key(key) == labels

    def test_format_labels_empty(self):
        assert _format_labels(None) == ""
        assert _format_labels({}) == ""

    def test_format_labels_sorted(self):
        result = _format_labels({"b": "2", "a": "1"})
        assert result == '{a="1",b="2"}'


class TestMetricsRegistry:
    def test_counter_create(self):
        r = MetricsRegistry()
        c = r.counter("test", "help text")
        assert c.name == "test"

    def test_counter_idempotent(self):
        r = MetricsRegistry()
        c1 = r.counter("test", "help text")
        c2 = r.counter("test", "other help")
        assert c1 is c2

    def test_convenience_inc(self):
        r = MetricsRegistry()
        r.inc("test_total", labels={"x": "1"})
        assert r.counter("test_total").get(labels={"x": "1"}) == 1.0

    def test_convenience_set(self):
        r = MetricsRegistry()
        r.set("test_gauge", 42.0, labels={"y": "2"})
        assert r.gauge("test_gauge").get(labels={"y": "2"}) == 42.0

    def test_convenience_observe(self):
        r = MetricsRegistry()
        r.observe("test_hist", 1.5, labels={"z": "3"})
        snap = r.histogram("test_hist").snapshot()
        assert "z=3" in snap

    def test_render_counter(self):
        r = MetricsRegistry()
        r.inc("task_total", labels={"status": "ok"}, help_text="Total tasks")
        text = r.render()
        assert "# HELP task_total Total tasks" in text
        assert "# TYPE task_total counter" in text
        assert 'task_total{status="ok"}' in text

    def test_render_gauge(self):
        r = MetricsRegistry()
        r.set("queue_size", 5, help_text="Queue size")
        text = r.render()
        assert "# TYPE queue_size gauge" in text
        assert "queue_size 5" in text

    def test_render_histogram(self):
        r = MetricsRegistry()
        r.observe("duration", 1.5, help_text="Duration")
        text = r.render()
        assert "# TYPE duration histogram" in text
        assert "duration_count" in text
        assert "duration_sum" in text
        assert "duration_bucket" in text

    def test_render_empty(self):
        r = MetricsRegistry()
        text = r.render()
        assert text.strip() == ""


# ---------------------------------------------------------------------------
# Retry mechanism tests
# ---------------------------------------------------------------------------


class _FakeRepo:
    """In-memory fake TaskRepository for retry tests."""

    def __init__(self, task=None):
        self._tasks = {}
        if task:
            self._tasks[task.id] = task
        self.updates = []

    async def get(self, task_id, user_id=None):
        return self._tasks.get(task_id)

    async def update(self, task_id, **kwargs):
        self.updates.append((task_id, kwargs))
        task = self._tasks.get(task_id)
        if task:
            for k, v in kwargs.items():
                setattr(task, k, v)

    async def create(self, **kwargs):
        task = MagicMock()
        task.id = kwargs["id"]
        task.user_id = kwargs["user_id"]
        task.domain = kwargs["domain"]
        task.task_type = kwargs["task_type"]
        task.payload = kwargs["payload"]
        task.status = "pending"
        task.progress = 0
        task.result = "{}"
        task.retry_count = 0
        self._tasks[task.id] = task
        return task

    async def delete(self, task_id):
        self._tasks.pop(task_id, None)

    async def list_by_user(self, user_id, domain=None, limit=100):
        return list(self._tasks.values())

    async def list_stale_for_recovery(self, domain, statuses):
        return [t for t in self._tasks.values() if t.status in statuses and t.domain == domain]


class _TestableService:
    """Concrete BaseTaskService subclass for testing retry logic."""

    from app.gateway._shared.task_framework.service import BaseTaskService as _Base

    domain = "test"
    id_prefix = "ts"
    max_retries = 2
    GATEWAY_STAGES = ("pending", "running")

    def __init__(self, repo, storage_dir):
        self.repo = repo
        self._storage_dir = storage_dir
        self._active_tasks = {}
        self._concurrency = asyncio.Semaphore(5)
        # Per-user concurrency gate — mirror production BaseTaskService (service.py:94-106)
        # so the borrowed _wrap_pipeline (:353 `async with self._get_user_sem(...)`) doesn't
        # AttributeError. The retry tests are single-user/single-task, so cache semantics
        # don't matter; a fresh Semaphore(N) per user is sufficient.
        self._max_tasks_per_user = 3
        self._user_sems: dict[str, asyncio.Semaphore] = {}
        # 2026-08-08 P1-2：recover_stale_tasks 新增 _recovering 标记集
        # （B1 自愈分支据此放弃重排）——stub 须镜像。
        self._deleting: set[str] = set()
        self._recovering: set[str] = set()

    def _get_user_sem(self, user_id: str) -> asyncio.Semaphore:
        # Mirror BaseTaskService._get_user_sem (service.py:94-106): lazy per-user cache.
        sem = self._user_sems.get(user_id)
        if sem is None:
            sem = asyncio.Semaphore(self._max_tasks_per_user)
            self._user_sems[user_id] = sem
        return sem

    async def _run_pipeline(self, task_id, user_id, task_type, payload, task_dir, progress):
        raise NotImplementedError

    # 2026-08-30：_wrap_pipeline 新增 `async with self.with_liveness(task_id)`（活性
    # 滴答/阶段预算机制）。2026-08-31 起不再手写 no-op stub（stub 漂移是本仓已知
    # 失效模式）——别名真方法链：_ensure_liveness_state 本就是为绕过 __init__ 的
    # 手装 stub 设计的懒初始化（service.py:133），with_liveness 进入即调它把全部
    # 活性属性就位；滴答默认 30s 首拍，本文件测试早结束即退出取消，不会触达
    # repo.touch。retry/recovery 测试从此跑的是生产同款活性语义。
    with_liveness = _Base.with_liveness
    _ensure_liveness_state = _Base._ensure_liveness_state
    _liveness_tick = _Base._liveness_tick

    def _should_handoff_to_worker(self, task_type):
        return False

    async def _cleanup_task(self, task):
        pass

    # Inherit _wrap_pipeline and recover_stale_tasks
    _wrap_pipeline = _Base._wrap_pipeline
    _run_gated_pipeline = _Base._run_gated_pipeline
    recover_stale_tasks = _Base.recover_stale_tasks


@pytest.mark.asyncio
async def test_retry_on_first_failure(tmp_path):
    """Task fails once, then succeeds on retry."""
    repo = _FakeRepo()
    task = MagicMock(
        id="ts_abc123",
        user_id="u1",
        domain="test",
        task_type="ppt_gen",
        payload="{}",
        status="pending",
        progress=0,
        result="{}",
        retry_count=0,
    )
    repo._tasks[task.id] = task

    svc = _TestableService(repo, str(tmp_path))
    call_count = 0

    async def pipeline_fails_once(task_id, user_id, task_type, payload, task_dir, progress):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("transient failure")
        return None

    svc._run_pipeline = pipeline_fails_once

    # Run the first attempt (will fail and schedule retry)
    await svc._wrap_pipeline("ts_abc123", "u1", "ppt_gen", {})

    # Wait for all retry tasks to complete
    for _ in range(20):
        await asyncio.sleep(0.05)
        pending = [t for t in svc._active_tasks.values() if not t.done()]
        if not pending:
            break

    assert call_count == 2


@pytest.mark.asyncio
async def test_retry_exhausted(tmp_path):
    """Task fails max_retries + 1 times, ends up failed."""
    repo = _FakeRepo()
    task = MagicMock(
        id="ts_xyz789",
        user_id="u1",
        domain="test",
        task_type="ppt_gen",
        payload="{}",
        status="pending",
        progress=0,
        result="{}",
        retry_count=0,
    )
    repo._tasks[task.id] = task

    svc = _TestableService(repo, str(tmp_path))

    async def always_fail(task_id, user_id, task_type, payload, task_dir, progress):
        raise RuntimeError("permanent failure")

    svc._run_pipeline = always_fail

    # First call fails and schedules retries
    await svc._wrap_pipeline("ts_xyz789", "u1", "ppt_gen", {})

    # Wait for all retries to complete
    for _ in range(5):
        await asyncio.sleep(0.05)
        if not any(not t.done() for t in svc._active_tasks.values()):
            break

    # Should eventually be marked failed
    failed_updates = [u for u in repo.updates if u[1].get("status") == "failed"]
    assert len(failed_updates) >= 1


@pytest.mark.asyncio
async def test_recover_stale_tasks_retries(tmp_path):
    """recover_stale_tasks re-schedules tasks within retry limit."""
    task = MagicMock(
        id="ts_recover1",
        user_id="u1",
        domain="test",
        task_type="ppt_gen",
        payload='{"key": "value"}',
        status="running",
        progress=50,
        result="{}",
        retry_count=0,
    )
    repo = _FakeRepo(task=task)
    svc = _TestableService(repo, str(tmp_path))

    count = await svc.recover_stale_tasks()
    assert count == 1
    updates = [u for u in repo.updates if u[0] == "ts_recover1"]
    assert any(u[1].get("retry_count") == 1 for u in updates)


@pytest.mark.asyncio
async def test_recover_stale_tasks_max_retries_exceeded(tmp_path):
    """recover_stale_tasks marks tasks failed when retry_count >= max_retries."""
    task = MagicMock(
        id="ts_recover2",
        user_id="u1",
        domain="test",
        task_type="ppt_gen",
        payload="{}",
        status="running",
        progress=50,
        result="{}",
        retry_count=2,
    )
    repo = _FakeRepo(task=task)
    svc = _TestableService(repo, str(tmp_path))

    count = await svc.recover_stale_tasks()
    assert count == 1
    failed_updates = [u for u in repo.updates if u[1].get("status") == "failed"]
    assert len(failed_updates) >= 1


# ---------------------------------------------------------------------------
# Worker concurrency tests
# ---------------------------------------------------------------------------


class TestWorkerConcurrency:
    _DOCKER_COMPOSE = Path(__file__).resolve().parents[2] / "docker" / "docker-compose-deploy.yaml"
    _WORKER_PY = Path(__file__).resolve().parents[2] / "docker" / "production-worker" / "worker.py"

    def test_max_concurrent_tasks_default(self):
        """MAX_CONCURRENT_TASKS defaults to 2."""
        content = self._WORKER_PY.read_text()
        assert "MAX_CONCURRENT_TASKS" in content
        assert "int(os.environ.get" in content

    def test_docker_compose_has_replicas(self):
        """docker-compose-deploy.yaml has replicas: 3 for production-worker."""
        content = self._DOCKER_COMPOSE.read_text()
        assert "replicas: 3" in content

    def test_docker_compose_ppt_converter_replicas(self):
        """docker-compose-deploy.yaml has replicas: 4 for ppt-converter (32核档 2026-08-28 调优 3→4)。"""
        content = self._DOCKER_COMPOSE.read_text()
        # ppt-converter section: from ppt-converter: to next service header
        ppt_start = content.index("ppt-converter:")
        # Find next service (after ppt-converter section ends)
        next_service = content.index("nginx:", ppt_start)
        ppt_section = content[ppt_start:next_service]
        assert "replicas: 4" in ppt_section

    def test_docker_compose_video_gen_volume(self):
        """docker-compose-deploy.yaml has video-gen volume for worker."""
        content = self._DOCKER_COMPOSE.read_text()
        assert "video-gen" in content

    def test_worker_claim_gate_is_atomic_acquire(self):
        """R3 v2（2026-08-31）：claim 门控必须用 acquire(blocking=False) 原子占槽。

        v1 的 `_value > 0` 预检是跨线程 check-then-act（真 acquire 在子线程），
        窗口内仍会超领任务回到「阻塞在 acquire 上、租约零续租」病灶，且依赖
        CPython 私有属性——回归到该写法本测试必须红。
        """
        content = self._WORKER_PY.read_text()
        assert "_concurrency.acquire(blocking=False)" in content, "主循环须在 claim 前原子占槽"
        assert "has_slot = _concurrency._value" not in content, "v1 的 _value 预检不得回流（健康检查处的被动读除外）"
        assert 'kwargs={"slot_pre_acquired": True, "slot": slot}' in content, "占得的槽须传给 _run_task（含槽位名，按入口预占配对释放），线程内不得重复阻塞 acquire"

    @staticmethod
    def _worker_service_section(content: str) -> str:
        """提取 production-worker 服务块。不能用裸 split("production-worker:")——
        gateway 块的 WORKER_HEALTH_URL（http://production-worker:8040）先命中，
        切出来的是 gateway 尾段（修前 test_no_static_worker_id 一直在查错段，
        恰因 gateway 段也不含 WORKER_ID 而假绿）。按两空格缩进的服务定义行锚定。"""
        import re

        m = re.search(r"\n  production-worker:\n(.*?)(?=\n  [a-z_-]+:)", content, re.S)
        assert m, "compose 里找不到 production-worker 服务定义"
        return m.group(1)

    def test_docker_compose_no_static_worker_id(self):
        """docker-compose-deploy.yaml does not set static WORKER_ID."""
        content = self._DOCKER_COMPOSE.read_text()
        worker_section = self._worker_service_section(content)
        assert "WORKER_ID" not in worker_section

    def test_docker_compose_grace_covers_drain_budget(self):
        """R2 补全：stop_grace_period ≥ MAX_CONCURRENT_TASKS × 120 + 60（compose 注释自立的公式）。

        drain 对每个在跑线程顺序 join SHUTDOWN_JOIN_TIMEOUT(120s)；grace 小于该预算
        则 SIGKILL 先于 release_my_leases → 任务卡到租约过期（1800s）。调
        MAX_CONCURRENT_TASKS 不同步调 grace 时本测试必须红。
        """
        import re

        worker_section = self._worker_service_section(self._DOCKER_COMPOSE.read_text())
        mct = int(re.search(r"MAX_CONCURRENT_TASKS=(\d+)", worker_section).group(1))
        grace = int(re.search(r"stop_grace_period:\s*(\d+)s", worker_section).group(1))
        assert grace >= mct * 120 + 60, f"grace={grace}s < drain 预算 {mct}×120+60={mct * 120 + 60}s"


# ---------------------------------------------------------------------------
# Metrics endpoint test
# ---------------------------------------------------------------------------


class TestMetricsEndpoint:
    def test_render_prometheus_format(self):
        """MetricsRegistry.render() produces valid Prometheus text."""
        from app.gateway._shared.task_framework.metrics import metrics as reg

        # Reset and add test data
        reg.inc("test_endpoint_total", labels={"status": "ok"}, help_text="Test metric")
        text = reg.render()
        assert "# HELP test_endpoint_total Test metric" in text
        assert "# TYPE test_endpoint_total counter" in text
