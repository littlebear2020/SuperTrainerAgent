"""E2 (Lane 2, 2026-10-15): ViMax 视频桥 submit/poll 解耦——poll 超时重试不重复扣费。

修前 ``_generate_video_with_retry`` 把 (submit + poll) 合体的 ``generate_video``
当整体重试：**轮询**阶段超时（供应商慢/卡住）→ tenacity 重试 → **重新 submit**
→ 供应商开出新任务，旧任务照跑照计费（双计费）。throttle.py E2（Task 15）已在
包裹层修复同型缺陷，但 ViMax 桥绕过包裹层直连 provider，须同构修复。

修复形态（对齐 throttle.py:444-467）：提交成功即记住远端 task_id（经
``_retry_state`` 穿过 tenacity 重试保留）；后续重试只重连轮询；仅当提交本身
未建单（task_id 未取得）才允许重新提交。

计费口径（plan T1）：submit 计一次；poll 重连不计；submit 失败重试 → 每次重新
建单各计一次（预扣费语义，P1-3 保持）。
"""

import contextlib

import pytest
import tenacity

import app.gateway.video_gen.engines.vimax.provider_bridge as pb
from app.gateway.video_gen.engines.vimax.provider_bridge import SeedanceVideoGeneratorForViMax
from app.gateway.video_gen.providers.base import VideoRef


class _FakeSplitProvider:
    """真类（非 Mock）定义 submit/poll——type 级 split 检测须能看见。"""

    def __init__(self, *, poll_outcomes: list | None = None, submit_error: Exception | None = None):
        self.submit_calls = 0
        self.polled_task_ids: list[str] = []
        self._poll_outcomes = list(poll_outcomes or [])
        self._submit_error = submit_error

    async def submit_video(self, prompt: str, **kwargs) -> str:
        self.submit_calls += 1
        if self._submit_error is not None:
            raise self._submit_error
        return f"task-{self.submit_calls}"

    async def poll_video(self, task_id: str, **kwargs) -> VideoRef:
        self.polled_task_ids.append(task_id)
        outcome = self._poll_outcomes.pop(0) if self._poll_outcomes else None
        if isinstance(outcome, Exception):
            raise outcome
        return VideoRef(url="https://example.com/ok.mp4")


def _make_gen(fake) -> SeedanceVideoGeneratorForViMax:
    gen = SeedanceVideoGeneratorForViMax(api_key="k")
    gen._provider = fake
    return gen


@pytest.fixture(autouse=True)
def _fast_retry(monkeypatch):
    """清零 tenacity 退避等待（2s+4s）与计费单价（DB 单价不可控 → 定死 1.0）。"""
    monkeypatch.setattr(SeedanceVideoGeneratorForViMax._generate_video_with_retry.retry, "wait", tenacity.wait_none())
    monkeypatch.setattr(pb, "_resolve_video_unit_cost", lambda provider: 1.0)

    @contextlib.asynccontextmanager
    async def _fake_sem():
        yield

    monkeypatch.setattr(pb, "_api_semaphore", _fake_sem)


@pytest.mark.asyncio
async def test_poll_timeout_reuses_remote_task_and_charges_once():
    """poll 首超时：submit 只发生一次、task_id 复用、计费恰一次。

    修前行为：poll 超时 → 整体重试 → 第二次 submit（双计费 + 双建单）。"""
    fake = _FakeSplitProvider(poll_outcomes=[TimeoutError("poll stalled"), None])
    gen = _make_gen(fake)

    out = await gen.generate_single_video(prompt="a cat runs")

    assert fake.submit_calls == 1, f"poll 超时重试不得重新 submit（双计费根因），实际 submit {fake.submit_calls} 次"
    assert fake.polled_task_ids == ["task-1", "task-1"], f"重试应复用同一远端任务，实际轮询了 {fake.polled_task_ids}"
    assert gen._call_count == 1, f"计费应恰一次（submit 计、poll 重连不计），实际 {gen._call_count} 次"
    assert gen.cost_cny == 1.0
    assert out.data == "https://example.com/ok.mp4"


@pytest.mark.asyncio
async def test_sustained_timeout_fails_with_single_charge():
    """持续超时 → 重试耗尽后失败，且仍只计一次费（远端只有一个任务在跑）。"""
    fake = _FakeSplitProvider(poll_outcomes=[TimeoutError("t1"), TimeoutError("t2"), TimeoutError("t3")])
    gen = _make_gen(fake)

    with pytest.raises(TimeoutError):
        await gen.generate_single_video(prompt="a cat runs")

    assert fake.submit_calls == 1, f"持续超时不得重复建单，实际 submit {fake.submit_calls} 次"
    assert fake.polled_task_ids == ["task-1"] * 3, f"三次尝试应轮询同一任务，实际 {fake.polled_task_ids}"
    assert gen._call_count == 1, f"失败路径计费应恰一次，实际 {gen._call_count} 次"


@pytest.mark.asyncio
async def test_submit_failure_retries_new_orders_each_charged():
    """submit 阶段失败（未取得 task_id）→ 允许重新 submit，每次建单各计一次。

    submit 失败时远端可能未开出任务（不存在重复计费对象），重试重新提交是
    唯一恢复路径；预扣费语义（P1-3）保持——每次真实提交尝试都过预算闸。"""
    fake = _FakeSplitProvider(submit_error=TimeoutError("submit stalled"))
    gen = _make_gen(fake)

    with pytest.raises(TimeoutError):
        await gen.generate_single_video(prompt="a cat runs")

    assert fake.submit_calls == 3, f"submit 失败应重试满 3 次（1 初始 + 2 重试），实际 {fake.submit_calls} 次"
    assert fake.polled_task_ids == [], "submit 从未成功，不得轮询"
    assert gen._call_count == 3, f"每次重新建单各计一次，实际 {gen._call_count} 次"
