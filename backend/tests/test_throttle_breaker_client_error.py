"""T3（2026-08-17 H3 duration 400 修复批）：4xx 不计入熔断（切断放大器）。

事故复盘：确定性 400（时长不合法）被 Task 14「非可重试也计数」计为 provider
failure -> 3 次开闸 -> 后续**时长合法**的帧全部 circuit breaker is open ->
静默占位 12/29 帧。熔断器度量的是 provider 健康度，不是单请求合法性。

不变量：429（限流=健康信号）/ QihuServerError / 裸 RuntimeError 仍照旧计数
（Task 14 防慢性故障跳闸的语义不被过度收窄）；跳过计数时
``_record_provider_call`` 照记 error（可观测性不丢）。
"""

from __future__ import annotations

import pytest

from app.gateway.video_gen.providers import throttle as T
from app.gateway.video_gen.providers.qihu_video import QihuClientError, QihuServerError


def _video_inner(exc_factory):  # noqa: ANN001,ANN202
    class _Inner:
        calls = 0

        async def generate_video(self, prompt: str, *, duration: float = 5.0, **kw):
            _Inner.calls += 1
            raise exc_factory()

    return _Inner()


def _image_inner(exc_factory):  # noqa: ANN001,ANN202
    class _Inner:
        calls = 0

        async def generate_image(self, prompt: str, **kw):
            _Inner.calls += 1
            raise exc_factory()

    return _Inner()


@pytest.mark.asyncio
async def test_client_4xx_never_opens_video_breaker():
    """主判据：QihuClientError(400) 连发 10 次 -> 断路器不开、请求全部到达内层。

    修前：第 5 次即开闸，后续请求抛 ProviderUnavailableError 连坐（事故机制）。
    """
    inner = _video_inner(lambda: QihuClientError("360 video submit failed (400): bad", 400))
    p = T.ThrottledAIVideoProvider(inner, qps=100.0, max_retries=0, circuit_failure_threshold=5)
    for i in range(10):
        with pytest.raises(QihuClientError):
            await p.generate_video("p", duration=3.0)
        assert not p._breaker.is_open, f"第 {i + 1} 次 400 后断路器不得打开"
    assert inner.calls == 10, "所有请求必须真实到达内层（未被断路器拦截）"


@pytest.mark.asyncio
async def test_client_4xx_never_opens_image_breaker():
    """image 侧两处调用点（:327/:339）同款接线--现无生产者，锁住未来防护不回退。"""
    inner = _image_inner(lambda: QihuClientError("forbidden", 403))
    p = T.ThrottledAIImageProvider(inner, qps=100.0, max_retries=0, circuit_failure_threshold=5)
    for _ in range(8):
        with pytest.raises(QihuClientError):
            await p.generate_image("p")
    assert not p._breaker.is_open
    assert inner.calls == 8


@pytest.mark.asyncio
async def test_client_429_still_counts_toward_breaker():
    """429 是 provider 健康信号 -> 照旧计数开闸（实际 429 在 provider 层先抛
    RateLimitError，此用例锁的是分类器的防御纵深分支）。"""
    inner = _video_inner(lambda: QihuClientError("rate limited", 429))
    p = T.ThrottledAIVideoProvider(inner, qps=100.0, max_retries=0, circuit_failure_threshold=3)
    for _ in range(3):
        with pytest.raises(QihuClientError):
            await p.generate_video("p", duration=5.0)
    assert p._breaker.is_open, "429 连发必须开闸（限流=健康信号）"


@pytest.mark.asyncio
async def test_server_error_still_counts_toward_breaker():
    """Task 14 不变量：QihuServerError（5xx）照旧计数。"""
    inner = _video_inner(lambda: QihuServerError("500 boom"))
    p = T.ThrottledAIVideoProvider(inner, qps=100.0, max_retries=0, circuit_failure_threshold=3)
    for _ in range(3):
        with pytest.raises(QihuServerError):
            await p.generate_video("p", duration=5.0)
    assert p._breaker.is_open


@pytest.mark.asyncio
async def test_plain_runtime_error_still_counts_toward_breaker():
    """Task 14 不变量：裸 RuntimeError（无 status_code，如内容审核拒绝）照旧计数。"""
    inner = _video_inner(lambda: RuntimeError("content rejected"))
    p = T.ThrottledAIVideoProvider(inner, qps=100.0, max_retries=0, circuit_failure_threshold=3)
    for _ in range(3):
        with pytest.raises(RuntimeError):
            await p.generate_video("p", duration=5.0)
    assert p._breaker.is_open, "修 Task 14 慢性故障永不跳闸的语义不得被过度收窄"


@pytest.mark.asyncio
async def test_skipped_count_still_records_error_metric(monkeypatch):
    """跳过计数只豁免断路器，不豁免可观测性：_record_provider_call 照记 error。"""
    calls: list[tuple] = []
    monkeypatch.setattr(T, "_record_provider_call", lambda *a, **kw: calls.append((a, kw)))
    inner = _video_inner(lambda: QihuClientError("bad duration", 400))
    p = T.ThrottledAIVideoProvider(inner, qps=100.0, max_retries=0)
    with pytest.raises(QihuClientError):
        await p.generate_video("p", duration=3.0)
    assert calls and calls[0][0][3] == "error", "跳过计数仍须记 error（metrics/sink 不丢）"
