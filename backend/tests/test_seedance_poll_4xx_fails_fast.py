"""Task 23 / F7：seedance-ark 轮询遇永久性 4xx 必须立刻失败，不能烧满超时窗口。

修前 `_poll` 对非 200/429/5xx 的响应只 `logger.warning` + `continue`，于是
401 鉴权失效 / 403 / 404 任务不存在这类**永不自愈**的错误会一直轮到
`max_wait`（默认 300s）耗尽，最后抛的还是误导性的 `TimeoutError`——用户等满
五分钟，拿到的诊断信息是「超时」而不是「你的 key 过期了」。

本测试锁两条不变量：
1. 永久性 4xx（401/403/404）→ 立即抛错，且错误信息带状态码
2. 可重试语义的 4xx（408 请求超时 / 425 太早 / 409 冲突）→ 仍然 continue

注意：这里断言的是「调用次数」而非墙钟耗时——`asyncio.sleep` 被 patch 掉后
真实耗时无意义，轮询次数才是「有没有白等」的可靠代理。
"""

from __future__ import annotations

from typing import Any

import pytest

from app.gateway.video_gen.providers.seedance_ark import SeedanceArkVideoProvider


class _FakeResp:
    def __init__(self, status_code: int, payload: dict[str, Any] | None = None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text or str(self._payload)

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeClient:
    """记录每次 GET 的假 client；按预设脚本逐个返回响应。"""

    def __init__(self, script: list[_FakeResp]) -> None:
        self._script = script
        self.get_calls = 0

    async def get(self, url: str, **kwargs) -> _FakeResp:
        self.get_calls += 1
        idx = min(self.get_calls - 1, len(self._script) - 1)
        return self._script[idx]


def _make_provider(client: _FakeClient) -> SeedanceArkVideoProvider:
    p = SeedanceArkVideoProvider(api_key="k", base_url="https://ark.example/api/v3", model="m")
    p._get_client = lambda: client  # type: ignore[method-assign]
    return p


@pytest.mark.parametrize("status", [401, 403, 404, 400])
@pytest.mark.asyncio
async def test_permanent_4xx_raises_immediately(status: int, monkeypatch: pytest.MonkeyPatch) -> None:
    """401/403/404/400 → 第一次就抛，且不是 TimeoutError。"""
    monkeypatch.setattr("app.gateway.video_gen.providers.seedance_ark.asyncio.sleep", _noop_sleep)
    client = _FakeClient([_FakeResp(status, text=f"denied-{status}")])
    provider = _make_provider(client)

    with pytest.raises(RuntimeError) as exc:
        await provider._poll("task-1")

    assert not isinstance(exc.value, TimeoutError), "永久错误不应伪装成超时"
    assert str(status) in str(exc.value), f"错误信息必须带状态码，实际: {exc.value}"
    assert client.get_calls == 1, f"永久性 4xx 应只轮一次，实际轮了 {client.get_calls} 次"


@pytest.mark.parametrize("status", [408, 425, 409])
@pytest.mark.asyncio
async def test_retryable_4xx_keeps_polling(status: int, monkeypatch: pytest.MonkeyPatch) -> None:
    """408/425/409 有「稍后再试」语义 → 仍需继续轮询，最终拿到成功结果。"""
    monkeypatch.setattr("app.gateway.video_gen.providers.seedance_ark.asyncio.sleep", _noop_sleep)
    script = [
        _FakeResp(status, text="try later"),
        _FakeResp(status, text="try later"),
        _FakeResp(200, {"status": "succeeded", "content": {"video_url": "https://cdn/x.mp4"}}),
    ]
    client = _FakeClient(script)
    provider = _make_provider(client)

    url, _last_frame, _body = await provider._poll("task-2")

    assert url == "https://cdn/x.mp4"
    assert client.get_calls == 3, "可重试 4xx 应继续轮询直到成功"


async def _noop_sleep(_delay: float) -> None:
    return None
