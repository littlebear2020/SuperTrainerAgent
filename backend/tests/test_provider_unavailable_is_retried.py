"""Task 23 / F8：``ProviderUnavailableError``（供应商 5xx）必须重试。

背景（第三轮核实新增，此前误判为「证伪」）：
- `seedance_ark` 提交期 5xx → 抛 `ProviderUnavailableError`
- `throttle.py` 的重试分支只含 `(RateLimitError, TimeoutError, ConnectionError)`
- `ProviderUnavailableError` 被**单独捕获 → record_failure → 立即 raise，零重试**

后果：seedance-ark 是**默认** video provider，供应商一次瞬时 500 就直接失败整帧；
而回滚路径 qihu 的同类错误（`QihuServerError`）反有 3 次重试——**默认路径比回滚
路径更脆弱**。

本测试锁三条不变量：
1. 连抛 2 次 `ProviderUnavailableError` 后成功 → 整体成功（当前代码此用例必红）
2. 重试耗尽后 `record_failure` 仍被调用（断路器计数不能因为改重试而失效）
3. 「断路器已开」那条路径**不进入重试**（否则自旋——它在 for 循环外，本测试
   顺带把这条不变量钉住，防后续重构把它挪进循环）
"""

from __future__ import annotations

import pytest

from app.gateway.video_gen.providers.throttle import (
    ProviderUnavailableError,
    ThrottledAIImageProvider,
)


class _FlakyImageProvider:
    """前 ``fail_times`` 次抛 ProviderUnavailableError，之后成功。"""

    def __init__(self, fail_times: int) -> None:
        self._fail_times = fail_times
        self.calls = 0

    async def generate_image(self, prompt: str, **kwargs):
        self.calls += 1
        if self.calls <= self._fail_times:
            raise ProviderUnavailableError(f"upstream 500 (call {self.calls})")
        return f"image-for-{prompt}"


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch):
    async def _noop(_d: float) -> None:
        return None

    monkeypatch.setattr("app.gateway.video_gen.providers.throttle.asyncio.sleep", _noop)


@pytest.mark.asyncio
async def test_transient_5xx_is_retried_and_succeeds() -> None:
    """两次 5xx 后成功 → 调用方应拿到图，而不是异常。"""
    inner = _FlakyImageProvider(fail_times=2)
    provider = ThrottledAIImageProvider(inner, qps=100.0, max_retries=3)

    result = await provider.generate_image("a cat")

    assert result == "image-for-a cat"
    assert inner.calls == 3, f"应重试到第 3 次才成功，实际调用 {inner.calls} 次"


@pytest.mark.asyncio
async def test_retry_exhausted_still_records_failure() -> None:
    """重试耗尽 → 抛出，且断路器必须计到失败（否则永不跳闸）。"""
    inner = _FlakyImageProvider(fail_times=99)
    provider = ThrottledAIImageProvider(inner, qps=100.0, max_retries=2)

    before = provider._breaker._failure_count
    with pytest.raises(ProviderUnavailableError):
        await provider.generate_image("a dog")

    assert inner.calls == 3, "max_retries=2 → 首次 + 2 次重试 = 3 次调用"
    assert provider._breaker._failure_count > before, "重试耗尽后断路器必须计失败"


@pytest.mark.asyncio
async def test_open_circuit_does_not_spin() -> None:
    """断路器已开 → 立即抛且**不调用** inner（不能进入重试自旋）。"""
    inner = _FlakyImageProvider(fail_times=0)
    provider = ThrottledAIImageProvider(inner, qps=100.0, max_retries=3, circuit_failure_threshold=1)

    # 手动把断路器打开
    provider._breaker.record_failure()
    assert provider._breaker.is_open, "阈值 1，一次失败即应跳闸"

    with pytest.raises(ProviderUnavailableError):
        await provider.generate_image("x")

    assert inner.calls == 0, "断路器开时不应触达 inner，更不应重试自旋"
