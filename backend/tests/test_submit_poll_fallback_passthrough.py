"""A3 补充：submit/poll 在 Fallback 包裹链上的透传测试。

计划 §五.4 要求：A3/B1 要补 Throttle/Fallback 透传测试（裸 MagicMock 假绿教训）。
A3 已有 Throttle 单层测试（test_qihu_submit_poll_decouple.py），此文件补
Fallback(Throttled(primary), Throttled(fallback)) 完整包裹链上的 submit/poll 行为。

关键场景：primary 的 poll 超时 → Fallback 切到 fallback provider →
fallback 的 submit_video 应被调用（新任务），而不是复用 primary 的 task_id。
"""

import httpx
import pytest
import respx

from app.gateway.video_gen.providers.base import VideoRef
from app.gateway.video_gen.providers.registry import FallbackAIVideoProvider
from app.gateway.video_gen.providers.throttle import ThrottledAIVideoProvider


class _CountingProvider:
    """记录 submit/poll/generate 调用次数的假 provider（暴露 split 入口）。"""

    def __init__(self, name: str, *, poll_failures: int = 0):
        self.name = name
        self.submits = 0
        self.polls = 0
        self._poll_failures = poll_failures
        self.max_video_duration = 8.0

    async def submit_video(self, prompt, *, duration=5.0, **kwargs) -> str:
        self.submits += 1
        return f"{self.name}-task-{self.submits}"

    async def poll_video(self, task_id, *, duration=5.0, **kwargs) -> VideoRef:
        self.polls += 1
        if self.polls <= self._poll_failures:
            raise TimeoutError("poll timed out")
        return VideoRef(url=f"http://cdn/{task_id}.mp4", duration=duration, source=self.name)

    async def generate_video(self, prompt, *, duration=5.0, **kwargs):
        """合体入口——不应在有 split 时被调用。"""
        raise AssertionError("generate_video should not be called when submit/poll exist")


@pytest.mark.asyncio
async def test_fallback_chain_submit_poll_on_primary_success():
    """primary 正常完成 → fallback 不被调用。"""
    primary = _CountingProvider("primary")
    fallback = _CountingProvider("fallback")

    chain = FallbackAIVideoProvider(
        ThrottledAIVideoProvider(primary, qps=1000, max_retries=2),
        ThrottledAIVideoProvider(fallback, qps=1000, max_retries=2),
    )

    ref = await chain.generate_video("test", duration=5.0)

    assert primary.submits == 1
    assert primary.polls >= 1
    assert fallback.submits == 0, "primary 成功时 fallback 不应被调用"
    assert "primary-task-1" in ref.url


@pytest.mark.asyncio
async def test_fallback_chain_poll_timeout_triggers_fallback_resubmit():
    """primary poll 超时耗尽重试 → Fallback 切到 fallback → fallback 重新 submit。

    这验证了 submit/poll 在 Fallback 链上的正确行为：
    - Fallback 调 primary.generate_video → Throttle 调 primary.submit_video + primary.poll_video
    - primary poll 全部失败 → Throttle 耗尽重试抛 TimeoutError
    - TimeoutError 在 _FALLBACK_EXC 中 → Fallback 调 fallback.generate_video
    - fallback 的 Throttle 调 fallback.submit_video + fallback.poll_video
    - fallback 的 submit 是一个**新**任务（不是复用 primary 的 task_id）
    """
    primary = _CountingProvider("primary", poll_failures=99)
    fallback = _CountingProvider("fallback")

    chain = FallbackAIVideoProvider(
        ThrottledAIVideoProvider(primary, qps=1000, max_retries=1),
        ThrottledAIVideoProvider(fallback, qps=1000, max_retries=1),
    )

    ref = await chain.generate_video("test", duration=5.0)

    assert primary.submits == 1, "primary 应只 submit 一次"
    assert primary.polls == 2, "primary 应重试 2 次（1+1）后抛错"
    assert fallback.submits == 1, "fallback 应被调用并 submit 一次（新任务）"
    assert "fallback-task-1" in ref.url, "结果应来自 fallback"


@pytest.mark.asyncio
async def test_fallback_chain_both_fail_raises():
    """primary 和 fallback 都失败 → 最终抛错。"""
    primary = _CountingProvider("primary", poll_failures=99)
    fallback = _CountingProvider("fallback", poll_failures=99)

    chain = FallbackAIVideoProvider(
        ThrottledAIVideoProvider(primary, qps=1000, max_retries=0),
        ThrottledAIVideoProvider(fallback, qps=1000, max_retries=0),
    )

    with pytest.raises(TimeoutError):
        await chain.generate_video("test", duration=5.0)

    assert primary.submits == 1
    assert fallback.submits == 1


@respx.mock
@pytest.mark.asyncio
async def test_real_qihu_through_throttle_and_fallback():
    """端到端：真实 QihuVideoProvider 通过 Throttle 单层（无 fallback 场景）。

    验证 submit_video/poll_video 在真实 provider 上的行为等价于 generate_video。
    """
    from app.gateway.video_gen.providers.qihu_video import QihuVideoProvider

    respx.post("https://api.360.cn/v1/videos/async_generations").mock(
        return_value=httpx.Response(200, json={"data": {"id": "v-e2e", "status": "created"}}),
    )
    respx.get("https://api.360.cn/v1/videos/async_generations/v-e2e").mock(
        return_value=httpx.Response(200, json={"data": {"status": "success", "url": "http://cdn/e2e.mp4"}}),
    )

    inner = QihuVideoProvider(api_key="test-key", poll_interval=0.01)
    throttled = ThrottledAIVideoProvider(inner, qps=1000, max_retries=2)

    ref = await throttled.generate_video("test", duration=8.0)
    assert ref.url == "http://cdn/e2e.mp4"
    assert ref.source == "qihu"
