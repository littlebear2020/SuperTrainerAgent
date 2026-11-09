"""Task 15 (E2): 供应商重试不再重复扣费。

``throttle.py`` 的 ``generate_video`` 把**提交+轮询当作一个整体**重试：

    for attempt in range(self._max_retries + 1):
        try:
            async with self._limiter:
                result = await self._inner.generate_video(prompt, duration=duration, **kwargs)
        except (RateLimitError, TimeoutError, ConnectionError):
            ...  await asyncio.sleep(delay)   # 重试 → 重新走一遍 submit

而 provider 侧 ``seedance_ark.generate_video`` 明确是 (submit + poll) 合体：
``task_id = await self._submit(...)`` 之后才轮询。

**后果**：**轮询阶段**抛 TimeoutError（供应商慢、卡住）→ 外层重试 → **重新 _submit**
→ 供应商侧开出**新任务**，旧任务照跑照计费。``_max_retries`` 默认允许一次逻辑镜头
扣 2-3 次；叠加 ViMax pipeline 级重试（max_retries=2）可达 6 次。**无幂等键**。

修法：提交与轮询解耦——提交成功即记住远端 task_id；重试只重连轮询，不重新提交。
仅当提交本身失败才允许重新提交。
"""

import pytest

from app.gateway.video_gen.providers import throttle as throttle_mod
from app.gateway.video_gen.providers.throttle import (
    ProviderUnavailableError,
    ThrottledAIVideoProvider,
)


def _no_backoff(monkeypatch):
    """throttle 重试退避 2**attempt+rand → 0：重试/提交次数断言不变，只是不真睡。"""
    monkeypatch.setattr(throttle_mod, "_backoff", lambda _attempt: 0.0)


class _SubmitPollProvider:
    """暴露 submit/poll 分离入口的假 provider，记录各阶段调用次数。"""

    def __init__(self, *, poll_failures: int = 0, submit_failures: int = 0):
        self.submits = 0
        self.polls = 0
        self._poll_failures = poll_failures
        self._submit_failures = submit_failures
        self.max_video_duration = 8.0

    async def submit_video(self, prompt, *, duration=5.0, **kwargs) -> str:
        self.submits += 1
        if self.submits <= self._submit_failures:
            raise TimeoutError("submit timed out")
        return f"remote-task-{self.submits}"

    async def poll_video(self, task_id: str, *, duration: float = 5.0):
        self.polls += 1
        if self.polls <= self._poll_failures:
            raise TimeoutError("poll timed out")

        class _Ref:
            url = f"http://cdn/{task_id}.mp4"
            duration = 5.0
            source = "fake"

        return _Ref()

    async def generate_video(self, prompt, *, duration=5.0, **kwargs):
        """合体入口——不应被 throttle 在有 submit/poll 时使用。"""
        task_id = await self.submit_video(prompt, duration=duration, **kwargs)
        return await self.poll_video(task_id, duration=duration)


@pytest.mark.asyncio
async def test_poll_timeout_reuses_task_id_no_resubmit(monkeypatch):
    """提交成功后轮询超时 → 重试**复用原 task_id**，_submit 只被调一次。

    这是 E2 的核心用例：修前每次重试都重新 submit，供应商开新任务并重复计费。
    """
    _no_backoff(monkeypatch)
    inner = _SubmitPollProvider(poll_failures=2)
    p = ThrottledAIVideoProvider(inner, qps=1000, max_retries=3)

    ref = await p.generate_video("a shot", duration=5.0)

    assert inner.submits == 1, f"重试期间重复提交了 {inner.submits} 次 —— 每次都在重复扣费"
    assert inner.polls == 3, f"轮询应重试到成功，实际 {inner.polls} 次"
    assert ref.url.endswith("remote-task-1.mp4"), "未复用最初的远端 task_id"


@pytest.mark.asyncio
async def test_submit_failure_may_resubmit(monkeypatch):
    """提交阶段失败 → 允许重新提交（此时供应商侧没开出任务，不存在重复计费）。"""
    _no_backoff(monkeypatch)
    inner = _SubmitPollProvider(submit_failures=1)
    p = ThrottledAIVideoProvider(inner, qps=1000, max_retries=3)

    ref = await p.generate_video("a shot", duration=5.0)

    assert inner.submits == 2, f"提交失败后应重试提交，实际 {inner.submits} 次"
    assert ref.url.endswith("remote-task-2.mp4")


@pytest.mark.asyncio
async def test_poll_exhausted_raises_without_extra_submits(monkeypatch):
    """轮询重试耗尽仍失败 → 抛错，且提交次数仍为 1。"""
    _no_backoff(monkeypatch)
    inner = _SubmitPollProvider(poll_failures=99)
    p = ThrottledAIVideoProvider(inner, qps=1000, max_retries=2)

    with pytest.raises(TimeoutError):
        await p.generate_video("a shot", duration=5.0)

    assert inner.submits == 1, f"失败路径也不得重复提交（实际 {inner.submits} 次）"


@pytest.mark.asyncio
async def test_legacy_provider_without_submit_poll_still_works(monkeypatch):
    """provider 未暴露 submit/poll 时回落合体入口（向后兼容，不破坏 qihu 等）。"""
    _no_backoff(monkeypatch)

    class _LegacyOnly:
        def __init__(self):
            self.calls = 0
            self.max_video_duration = 8.0

        async def generate_video(self, prompt, *, duration=5.0, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise TimeoutError("transient")

            class _Ref:
                url = "http://cdn/legacy.mp4"
                duration = 5.0

            return _Ref()

    inner = _LegacyOnly()
    p = ThrottledAIVideoProvider(inner, qps=1000, max_retries=2)
    ref = await p.generate_video("x", duration=5.0)

    assert ref.url == "http://cdn/legacy.mp4"
    assert inner.calls == 2, "合体入口应照旧整体重试"


@pytest.mark.asyncio
async def test_seedance_ark_exposes_submit_poll():
    """默认 provider（seedance-ark）必须暴露分离入口，否则本 Task 对它无效。"""
    from app.gateway.video_gen.providers.seedance_ark import SeedanceArkVideoProvider

    assert hasattr(SeedanceArkVideoProvider, "submit_video")
    assert hasattr(SeedanceArkVideoProvider, "poll_video")


@pytest.mark.asyncio
async def test_breaker_still_counts_failures_on_poll_exhaustion():
    """轮询耗尽后断路器仍要计失败（不得因解耦而丢失跳闸能力）。"""
    inner = _SubmitPollProvider(poll_failures=99)
    p = ThrottledAIVideoProvider(inner, qps=1000, max_retries=0, circuit_failure_threshold=2)

    for _ in range(2):
        with pytest.raises(TimeoutError):
            await p.generate_video("x", duration=5.0)

    with pytest.raises(ProviderUnavailableError, match="circuit breaker"):
        await p.generate_video("x", duration=5.0)
