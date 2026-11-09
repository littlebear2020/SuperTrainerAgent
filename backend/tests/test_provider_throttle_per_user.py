"""Task 24 / F11：供应商限流必须按用户分桶，断路器保持全局。

修复前 ``throttle.py`` 全文无 user_id：limiter 随 ProviderRegistry 每服务实例
建一次，即进程内**全用户共享一个 QPS 桶**。schema 允许 n_scenes ≤ 200，一个
用户提交 200 帧任务就能长时间占满桶，其余人的生图/生视频全部排在其后。

两条不变量：
1. 限流**分用户**——A 占满不影响 B 取令牌
2. 断路器**不分用户**——供应商挂了是全局事实；按用户分会让每个人各自把它
   打死一遍（10 人 × 5 次失败才跳闸，等于没有断路器）
"""

from __future__ import annotations

import asyncio

import pytest

from app.gateway.video_gen.providers.throttle import (
    ProviderUnavailableError,
    ThrottledAIImageProvider,
)
from deerflow.runtime.user_context import _current_user


class _CountingProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def generate_image(self, prompt: str, **kwargs) -> str:
        self.calls += 1
        return f"img-{prompt}"


class _AlwaysFailingProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def generate_image(self, prompt: str, **kwargs) -> str:
        self.calls += 1
        raise ProviderUnavailableError("upstream 503")


class _StubUser:
    """满足 CurrentUser 协议（结构化类型，只要有 ``.id``）。"""

    def __init__(self, uid: str) -> None:
        self.id = uid


def _as_user(uid: str) -> None:
    _current_user.set(_StubUser(uid))


def _as_anonymous() -> None:
    _current_user.set(None)


def test_limiter_is_per_user_not_shared() -> None:
    """A 与 B 必须拿到不同的 limiter 实例（否则 A 的大任务卡住 B）。"""
    provider = ThrottledAIImageProvider(_CountingProvider(), qps=1.0)

    _as_user("alice")
    limiter_a = provider._limiter_for_current_user()
    _as_user("bob")
    limiter_b = provider._limiter_for_current_user()

    assert limiter_a is not limiter_b, "不同用户必须各自一个 QPS 桶"

    _as_user("alice")
    assert provider._limiter_for_current_user() is limiter_a, "同一用户必须复用同一个桶"


@pytest.mark.asyncio
async def test_user_b_not_blocked_by_user_a_saturation() -> None:
    """A 打满低 QPS 配额后，B 的调用不应被 A 的桶拖住。

    qps=1 → 单桶下第二次 acquire 要等约 1s。分桶后 B 走自己的桶，应当很快。
    """
    provider = ThrottledAIImageProvider(_CountingProvider(), qps=1.0)

    _as_user("alice")
    await provider.generate_image("a1")  # 先把 alice 的桶用掉

    _as_user("bob")
    elapsed = await _timed(provider.generate_image("b1"))

    assert elapsed < 0.5, f"B 不该被 A 的配额拖住，实际等了 {elapsed:.2f}s"


async def _timed(coro) -> float:
    loop = asyncio.get_running_loop()
    start = loop.time()
    await coro
    return loop.time() - start


@pytest.mark.asyncio
async def test_breaker_stays_global_across_users(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 把供应商打到跳闸后，B 必须立即被拒——不能重新打一遍。"""

    async def _noop(*_a, **_k) -> None:
        return None

    monkeypatch.setattr("app.gateway.video_gen.providers.throttle.asyncio.sleep", _noop)

    inner = _AlwaysFailingProvider()
    provider = ThrottledAIImageProvider(inner, qps=100.0, max_retries=0, circuit_failure_threshold=1)

    _as_user("alice")
    with pytest.raises(ProviderUnavailableError):
        await provider.generate_image("a")
    assert provider._breaker.is_open, "阈值 1，一次失败即应跳闸"

    calls_after_alice = inner.calls

    _as_user("bob")
    with pytest.raises(ProviderUnavailableError):
        await provider.generate_image("b")

    assert inner.calls == calls_after_alice, "断路器已开，B 不得再打到上游（断路器必须全局）"


def test_bucket_count_is_bounded() -> None:
    """LRU 上限：大量不同用户不得无界增长（否则是内存泄漏）。"""
    provider = ThrottledAIImageProvider(_CountingProvider(), qps=1.0, max_user_buckets=8)

    for i in range(50):
        _as_user(f"user-{i}")
        provider._limiter_for_current_user()

    assert len(provider._limiters) <= 8, f"桶数应受上限约束，实际 {len(provider._limiters)}"


def test_missing_user_falls_back_to_default_bucket() -> None:
    """无用户上下文（后台任务/worker 回调）不得炸——落默认桶。"""
    provider = ThrottledAIImageProvider(_CountingProvider(), qps=1.0)

    _as_anonymous()
    limiter = provider._limiter_for_current_user()

    assert limiter is not None
