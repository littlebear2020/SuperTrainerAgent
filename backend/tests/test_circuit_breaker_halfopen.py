"""Task 14: 断路器真正生效（半开互斥 + 非可重试错误也计失败）。

审计自己订正过一次（断路器**存在**），但订正后留下的两条都属实：

**1. 半开无互斥**：``is_open`` 是**纯读属性**——超时后对**每个**并发协程都返回
   False。docstring 承诺「half-open: allow one probe」，代码**没有任何 gating** →
   供应商刚缓过来就被 N 路并发再打死。

**2. 非可重试错误不计数**：只对
   ``RateLimitError|TimeoutError|ConnectionError|ProviderUnavailableError`` 调
   ``record_failure``。``ValueError`` / ``RuntimeError`` / 裸 5xx 逃逸**不计** →
   慢性故障的供应商**永不跳闸**。
"""

import asyncio

import pytest

from app.gateway.video_gen.providers.throttle import CircuitBreaker, ProviderUnavailableError

# ── 半开互斥 ──────────────────────────────────────────────────────────────


def test_breaker_opens_after_threshold():
    b = CircuitBreaker(failure_threshold=3, reset_timeout=30.0)
    for _ in range(3):
        b.record_failure()
    assert b.is_open


def test_success_closes_breaker():
    b = CircuitBreaker(failure_threshold=2, reset_timeout=30.0)
    b.record_failure()
    b.record_failure()
    assert b.is_open
    b.record_success()
    assert not b.is_open


@pytest.mark.asyncio
async def test_half_open_admits_only_one_probe():
    """半开窗口内并发 10 路 → 只有 1 路拿到探测许可。

    这是缺陷 1 的核心用例：修前 is_open 是纯读属性，10 路全部通过。
    """
    b = CircuitBreaker(failure_threshold=1, reset_timeout=0.01)
    b.record_failure()
    assert b.is_open

    await asyncio.sleep(0.05)  # 进入半开

    admitted = []

    async def try_probe(i):
        if b.try_acquire_probe():
            admitted.append(i)

    await asyncio.gather(*(try_probe(i) for i in range(10)))

    assert len(admitted) == 1, f"半开窗口放行了 {len(admitted)} 路探测（应为 1）"


@pytest.mark.asyncio
async def test_failed_probe_reopens_and_blocks_others():
    """探测失败 → 重新闭合，其余协程继续被拦。"""
    b = CircuitBreaker(failure_threshold=1, reset_timeout=0.01)
    b.record_failure()
    await asyncio.sleep(0.05)

    assert b.try_acquire_probe(), "首个探测应被放行"
    b.record_failure()  # 探测失败

    assert b.is_open, "探测失败后应重新打开"
    assert not b.try_acquire_probe(), "刚失败就又放行探测 → 供应商被连续打"


@pytest.mark.asyncio
async def test_successful_probe_closes_breaker():
    """探测成功 → 断路器闭合，放行全部流量。"""
    b = CircuitBreaker(failure_threshold=1, reset_timeout=0.01)
    b.record_failure()
    await asyncio.sleep(0.05)

    assert b.try_acquire_probe()
    b.record_success()

    assert not b.is_open
    # 闭合后不需要探测许可
    assert b.try_acquire_probe()


# ── 非可重试错误也要计失败 ──────────────────────────────────────────────────


class _BoomProvider:
    """每次都抛非「可重试」类异常的 provider。"""

    def __init__(self, exc):
        self._exc = exc
        self.calls = 0
        self.max_video_duration = 8.0

    async def generate_image(self, prompt, **kw):
        self.calls += 1
        raise self._exc

    async def generate_video(self, prompt, *, duration=30.0, **kw):
        self.calls += 1
        raise self._exc


@pytest.mark.asyncio
@pytest.mark.parametrize("exc", [RuntimeError("bad gateway 502"), ValueError("garbage response")])
async def test_non_retryable_errors_trip_breaker_image(exc):
    """连续 N 次 RuntimeError/ValueError → 必须跳闸（修前永不跳闸）。"""
    from app.gateway.video_gen.providers.throttle import ThrottledAIImageProvider

    inner = _BoomProvider(exc)
    p = ThrottledAIImageProvider(inner, qps=1000, max_retries=0, circuit_failure_threshold=3)

    for _ in range(3):
        with pytest.raises(type(exc)):
            await p.generate_image("x")

    with pytest.raises(ProviderUnavailableError, match="circuit breaker"):
        await p.generate_image("x")


@pytest.mark.asyncio
async def test_non_retryable_errors_trip_breaker_video():
    from app.gateway.video_gen.providers.throttle import ThrottledAIVideoProvider

    inner = _BoomProvider(RuntimeError("500 internal"))
    p = ThrottledAIVideoProvider(inner, qps=1000, max_retries=0, circuit_failure_threshold=2)

    for _ in range(2):
        with pytest.raises(RuntimeError):
            await p.generate_video("x", duration=5.0)

    with pytest.raises(ProviderUnavailableError, match="circuit breaker"):
        await p.generate_video("x", duration=5.0)


@pytest.mark.asyncio
async def test_success_still_resets_failure_count():
    """成功仍清零失败计数（不得因本 Task 变成累积到死）。"""
    from app.gateway.video_gen.providers.throttle import ThrottledAIImageProvider

    class _Flaky:
        def __init__(self):
            self.n = 0

        async def generate_image(self, prompt, **kw):
            self.n += 1
            if self.n <= 2:
                raise RuntimeError("hiccup")

            class _R:
                url = "http://x/a.png"
                b64_json = None

            return _R()

    p = ThrottledAIImageProvider(_Flaky(), qps=1000, max_retries=0, circuit_failure_threshold=3)
    for _ in range(2):
        with pytest.raises(RuntimeError):
            await p.generate_image("x")
    await p.generate_image("x")  # 成功清零

    # 再连续 2 次失败不应立刻跳闸（计数已清零）
    inner2 = _BoomProvider(RuntimeError("again"))
    p._inner = inner2
    for _ in range(2):
        with pytest.raises(RuntimeError):
            await p.generate_image("x")
    assert not p._breaker.is_open, "成功未清零失败计数"
