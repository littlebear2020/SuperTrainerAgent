"""P1-5: 预算/参数错误不重试（避免成本 3x）。"""

import asyncio
import json
from unittest.mock import MagicMock

import pytest

from app.gateway._shared.task_framework.service import BaseTaskService


class _FakeRepo:
    def __init__(self):
        self._tasks = {}

    async def get(self, task_id, user_id=None):
        return self._tasks.get(task_id)

    async def update(self, task_id, **kwargs):
        # 仿真 unified_tasks.result 非空约束（models.py:37 nullable=False）——
        # 478b13beb 写 result=None 时本 FakeRepo 静默放行，测试绿、生产 503。
        if "result" in kwargs and kwargs["result"] is None:
            raise ValueError("result column is NOT NULL (simulated constraint)")
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


def _make_svc(exc, repo, tmp_path):
    calls = []

    class _Svc(BaseTaskService):
        domain = "test"
        id_prefix = "ts"
        max_retries = 2
        # 含 draft/story_ready：recover_stale_tasks 的等待态语义测试需要
        # （既有用例不走恢复，无影响）
        GATEWAY_STAGES = ("pending", "running", "draft", "story_ready")

        def __init__(self, repo, storage_dir):
            self.repo = repo
            self._storage_dir = storage_dir
            self._active_tasks = {}
            self._concurrency = asyncio.Semaphore(5)
            self._max_tasks_per_user = 3
            self._user_sems: dict[str, asyncio.Semaphore] = {}
            self._deleting: set[str] = set()
            self._recovering: set[str] = set()
            self._reaper_task = None

        def _get_user_sem(self, user_id):
            sem = self._user_sems.get(user_id)
            if sem is None:
                sem = asyncio.Semaphore(self._max_tasks_per_user)
                self._user_sems[user_id] = sem
            return sem

        async def _run_pipeline(self, task_id, user_id, task_type, payload, task_dir, progress):
            calls.append(1)
            if exc is not None:
                raise exc
            return {}  # exc=None：恢复测试用——管线成功，走 ready 分支

        def _should_handoff_to_worker(self, task_type):
            return False

        async def _cleanup_task(self, task):
            pass

    return _Svc(repo, str(tmp_path)), calls


async def _drain(svc, task_id):
    """Wait for all async retries to complete."""
    for _ in range(20):
        t = svc._active_tasks.get(task_id)
        if t is None or t.done():
            break
        try:
            await t
        except Exception:
            pass


@pytest.mark.asyncio
async def test_value_error_not_retried(tmp_path):
    repo = _FakeRepo()
    await repo.create(id="ts_v", user_id="u1", domain="test", task_type="ppt_gen", payload="{}")
    svc, calls = _make_svc(ValueError("bad config"), repo, tmp_path)

    await svc._wrap_pipeline("ts_v", "u1", "ppt_gen", {})
    await _drain(svc, "ts_v")
    assert len(calls) == 1, f"ValueError 不得重试，实际调用 {len(calls)} 次"
    task = await repo.get("ts_v")
    assert task.status == "failed"


@pytest.mark.asyncio
async def test_budget_exceeded_not_retried(tmp_path):
    from app.gateway.video_gen.visual_sources.budget import VisualBudgetExceeded

    repo = _FakeRepo()
    await repo.create(id="ts_b", user_id="u1", domain="test", task_type="ppt_gen", payload="{}")
    svc, calls = _make_svc(VisualBudgetExceeded("over"), repo, tmp_path)

    await svc._wrap_pipeline("ts_b", "u1", "ppt_gen", {})
    await _drain(svc, "ts_b")
    assert len(calls) == 1, f"预算超限不得重试，实际调用 {len(calls)} 次"
    task = await repo.get("ts_b")
    assert task.status == "failed"


@pytest.mark.asyncio
async def test_transient_runtime_error_is_retried(tmp_path):
    repo = _FakeRepo()
    await repo.create(id="ts_r", user_id="u1", domain="test", task_type="ppt_gen", payload="{}")
    svc, calls = _make_svc(RuntimeError("transient 503"), repo, tmp_path)

    await svc._wrap_pipeline("ts_r", "u1", "ppt_gen", {})
    await _drain(svc, "ts_r")
    assert len(calls) == 3, f"瞬态错误应重试 3 次，实际 {len(calls)}"


@pytest.mark.asyncio
async def test_retry_requeue_clears_stale_result(tmp_path):
    """2026-08-19（反馈2-2 P1b）：管线失败自动重排时清空 result——修前上一轮
    残留的 warning 键（如 duration_warning）在 running 态穿透详情接口，
    前端警告卡可点 → 409（vg_91899fe68c3b）。
    2026-08-20：清空口径为置空 JSON "{}"——result 列 nullable=False，
    修前写 NULL 在生产撞约束（FakeRepo 已补约束仿真）。"""
    repo = _FakeRepo()
    await repo.create(id="ts_c", user_id="u1", domain="test", task_type="ppt_gen", payload="{}")
    task = await repo.get("ts_c")
    task.result = json.dumps({"duration_warning": "上一轮残留", "storyboard": {"title": "t"}}, ensure_ascii=False)
    svc, calls = _make_svc(RuntimeError("coverage too low"), repo, tmp_path)

    await svc._wrap_pipeline("ts_c", "u1", "ppt_gen", {})
    await _drain(svc, "ts_c")
    assert len(calls) == 3, "瞬态错误应重试 3 次"
    final = await repo.get("ts_c")
    assert final.result == "{}", "重排必须清空 stale result（置空 JSON，列非空约束不写 NULL）"


@pytest.mark.asyncio
async def test_recover_draft_does_not_consume_retry_budget(tmp_path):
    """2026-08-20：DRAFT=「等用户确认」持久态，重启恢复不烧 retry 预算——
    修前 draft 每次 gateway 重启 retry_count+1，第 3 次重启被自动标 failed。"""
    repo = _FakeRepo()
    await repo.create(id="ts_d", user_id="u1", domain="test", task_type="ppt_gen", payload="{}")
    task = await repo.get("ts_d")
    task.status = "draft"
    task.retry_count = 2  # 已满额——旧逻辑会直接标 failed
    task.error_message = "上一轮重试残留"
    svc, _ = _make_svc(None, repo, tmp_path)

    recovered = await svc.recover_stale_tasks()
    await _drain(svc, "ts_d")

    assert recovered == 1
    final = await repo.get("ts_d")
    assert final.retry_count == 2, "draft 恢复不得消耗 retry 预算"
    assert final.status != "failed", "draft 不得因重启恢复被标 failed"
    assert final.error_message is None, "等待态恢复应清旧 error_message 残留"


@pytest.mark.asyncio
async def test_recover_story_ready_keeps_retry_budget(tmp_path):
    """2026-08-20（审查跟进）：STORY_READY 与 DRAFT 同为「等用户确认」态
    （idea2video story 待确认），孪生豁免——修前 story_ready 每次重启
    retry+1，第 3 次重启同样被自动标 failed。"""
    repo = _FakeRepo()
    await repo.create(id="ts_s", user_id="u1", domain="test", task_type="ppt_gen", payload="{}")
    task = await repo.get("ts_s")
    task.status = "story_ready"
    task.retry_count = 2
    svc, _ = _make_svc(None, repo, tmp_path)

    recovered = await svc.recover_stale_tasks()
    await _drain(svc, "ts_s")

    assert recovered == 1
    final = await repo.get("ts_s")
    assert final.retry_count == 2, "story_ready 恢复不得消耗 retry 预算"
    assert final.status != "failed", "story_ready 不得因重启恢复被标 failed"


@pytest.mark.asyncio
async def test_recover_isolates_single_task_failure(tmp_path):
    """2026-08-20：任务粒度异常隔离——单任务恢复失败不中断其余任务
    （修前一个坏任务抛异常 → lifespan 中断 → video 模块整体 503）。"""
    repo = _FakeRepo()
    await repo.create(id="ts_ok", user_id="u1", domain="test", task_type="ppt_gen", payload="{}")
    await repo.create(id="ts_bad", user_id="u1", domain="test", task_type="ppt_gen", payload="{}")
    ok = await repo.get("ts_ok")
    ok.status = "running"
    bad = await repo.get("ts_bad")
    bad.status = "running"
    bad.payload = "{invalid json"  # 恢复体 json.loads 必炸
    svc, _ = _make_svc(None, repo, tmp_path)

    recovered = await svc.recover_stale_tasks()
    await _drain(svc, "ts_ok")

    assert recovered == 1, "坏任务不得拖垮其余恢复"
    assert (await repo.get("ts_ok")).retry_count == 1
    final_bad = await repo.get("ts_bad")
    assert final_bad.status == "running" and final_bad.retry_count == 0, "失败任务留原状态"


# ── Plan C Task 3（2026-10-19）：本地 deadline 不触发整管线重试 ─────────────


@pytest.mark.asyncio
async def test_local_storyboard_deadline_not_retried(tmp_path):
    """显式 pipeline_retryable=False（ViMax 本地分镜 deadline）→ 直接 failed，
    不消耗重试预算；已完成批次由任务目录缓存复用。"""
    repo = _FakeRepo()
    await repo.create(id="ts_d", user_id="u1", domain="test", task_type="ppt_gen", payload="{}")

    class _LocalDeadline(TimeoutError):
        pipeline_retryable = False

    svc, calls = _make_svc(_LocalDeadline("vimax_storyboard_deadline: local storyboard deadline exceeded"), repo, tmp_path)

    await svc._wrap_pipeline("ts_d", "u1", "ppt_gen", {})
    await _drain(svc, "ts_d")
    assert len(calls) == 1, f"本地 deadline 不得整管线重试，实际调用 {len(calls)} 次"
    task = await repo.get("ts_d")
    assert task.status == "failed"
    assert task.retry_count == 0, "不得消耗重试预算"


@pytest.mark.asyncio
async def test_plain_timeout_still_retried(tmp_path):
    """普通内置 TimeoutError（provider 超时等）保持初次+2 次重试（语义不变）。"""
    repo = _FakeRepo()
    await repo.create(id="ts_t", user_id="u1", domain="test", task_type="ppt_gen", payload="{}")
    svc, calls = _make_svc(TimeoutError("upstream timeout"), repo, tmp_path)

    await svc._wrap_pipeline("ts_t", "u1", "ppt_gen", {})
    await _drain(svc, "ts_t")
    assert len(calls) == 3, f"普通 TimeoutError 应重试 3 次，实际 {len(calls)} 次"


@pytest.mark.asyncio
async def test_task_framework_deadline_does_not_replay_single_batch(tmp_path):
    """2026-10-20 已拍板：单批 deadline 也不再触发任务级整轮重放（2×420 已覆盖
    provider 方差；失败后由用户手动重试）。retry_count 不变、管线只跑 1 次。"""
    repo = _FakeRepo()
    await repo.create(id="ts_sb", user_id="u1", domain="test", task_type="ppt_gen", payload="{}")

    class _SingleBatchDeadline(TimeoutError):
        error_code = "vimax_storyboard_deadline"
        batches_reusable = False
        pipeline_retryable = False

    svc, calls = _make_svc(_SingleBatchDeadline("vimax_storyboard_deadline: single-batch"), repo, tmp_path)

    await svc._wrap_pipeline("ts_sb", "u1", "ppt_gen", {})
    await _drain(svc, "ts_sb")
    assert len(calls) == 1, f"单批 deadline 不得整管线重放，实际 {len(calls)} 次"
    task = await repo.get("ts_sb")
    assert task.status == "failed"
    assert task.retry_count == 0, "不得消耗重试预算"


@pytest.mark.asyncio
async def test_idea2video_deadline_keeps_task_retry(tmp_path):
    """idea2video 显式 task_retryable=True：保持旧任务级初试+2 次重试。"""
    repo = _FakeRepo()
    await repo.create(id="ts_idea", user_id="u1", domain="test", task_type="ppt_gen", payload="{}")

    class _IdeaDeadline(TimeoutError):
        error_code = "vimax_storyboard_deadline"
        batches_reusable = False
        pipeline_retryable = True

    svc, calls = _make_svc(_IdeaDeadline("vimax_storyboard_deadline: idea2video"), repo, tmp_path)
    await svc._wrap_pipeline("ts_idea", "u1", "ppt_gen", {})
    await _drain(svc, "ts_idea")
    assert len(calls) == 3, f"idea2video deadline 应保留 3 次任务级执行，实际 {len(calls)}"
    task = await repo.get("ts_idea")
    assert task.status == "failed"
    assert task.retry_count == 2
