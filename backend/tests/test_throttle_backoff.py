"""E10: 限流器持锁退避——重试 sleep 必须在 limiter 退出之后（不占 QPS 窗口）。"""

import asyncio

import pytest


@pytest.mark.asyncio
async def test_image_backoff_sleeps_outside_limiter(monkeypatch):
    """重试退避的 sleep 不得在 async with limiter 锁内（修前在锁内 → 压有效 QPS）。"""
    from app.gateway.video_gen.providers import throttle as T

    order: list[str] = []

    class _RecLimiter:
        async def __aenter__(self):
            order.append("enter")
            return self

        async def __aexit__(self, *exc):
            order.append("exit")
            return False

    class _Flaky:
        calls = 0

        async def generate_image(self, prompt, **kw):
            order.append("request")
            _Flaky.calls += 1
            if _Flaky.calls == 1:
                raise T.RateLimitError("429")
            return T.ImageRef(url="ok")

    async def fake_sleep(_delay):
        order.append("sleep")

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    p = T.ThrottledAIImageProvider(_Flaky(), qps=1.0, max_retries=1)
    # Task 24 起 limiter 按 user_id 分桶。桶 key 取决于当前用户上下文
    # （conftest 的 autouse fixture 会注入 test-user-autouse，无上下文时
    # 回落 "default"），所以用 provider 自己的解析器定位桶，而不是写死字符串。
    from deerflow.runtime.user_context import get_effective_user_id

    p._limiters[get_effective_user_id()] = _RecLimiter()

    result = await p.generate_image("prompt")
    assert result.url == "ok"
    # 关键不变量：第一次 sleep 必须在第一次 limiter exit 之后（锁内不退避）
    first_sleep = order.index("sleep")
    first_exit = order.index("exit")
    assert first_sleep > first_exit, f"退避 sleep 必须在 limiter 退出之后；实际顺序：{order}"


@pytest.mark.asyncio
async def test_video_backoff_sleeps_outside_limiter(monkeypatch):
    """video provider 同理：退避 sleep 不在 limiter 锁内。"""
    from app.gateway.video_gen.providers import throttle as T

    order: list[str] = []

    class _RecLimiter:
        async def __aenter__(self):
            order.append("enter")
            return self

        async def __aexit__(self, *exc):
            order.append("exit")
            return False

    class _Flaky:
        calls = 0

        async def generate_video(self, prompt, *, duration=30.0, **kw):
            order.append("request")
            _Flaky.calls += 1
            if _Flaky.calls == 1:
                raise T.RateLimitError("429")
            return T.VideoRef(url="ok")

    async def fake_sleep(_delay):
        order.append("sleep")

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    p = T.ThrottledAIVideoProvider(_Flaky(), qps=1.0, max_retries=1)
    # 同上：Task 24 起按 user_id 分桶，用解析器定位当前用户的桶。
    from deerflow.runtime.user_context import get_effective_user_id

    p._limiters[get_effective_user_id()] = _RecLimiter()

    result = await p.generate_video("prompt", duration=5.0)
    assert result.url == "ok"
    first_sleep = order.index("sleep")
    first_exit = order.index("exit")
    assert first_sleep > first_exit, f"退避 sleep 必须在 limiter 退出之后；实际顺序：{order}"
