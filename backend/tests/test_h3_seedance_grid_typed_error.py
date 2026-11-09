"""T1/T2/T3（2026-08-17 H3 duration 400 修复批）：seedance-360 栅格声明 + ceil + typed 4xx。

seedance 覆写了 ``_submit``（与 QihuVideoProvider 基类不同体），基类改动它吃不到，
必须独立声明/独立测试（评审 P1-2/P1-3）：
- T1 类级栅格 [4,15] 整数秒（D2 拍板：360 通道与 Ark 同模型按官方规格声明）；
- T2 ``int()`` 同改 ceil（vimax bridge 直调绕过栅格对齐的防御纵深）；
- T3 4xx 抛 QihuClientError 带 status_code（供熔断分类器豁免；[4,15] 是推断，
  推断若错 400 级联会在 seedance 上重演，必须有豁免兜底）。
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock

from app.gateway.video_gen.providers.qihu_video import QihuClientError, QihuServerError
from app.gateway.video_gen.providers.seedance import SeedanceVideoProvider


def _provider() -> SeedanceVideoProvider:
    return SeedanceVideoProvider(api_key="k", base_url="http://b", poll_interval=0)


def test_seedance_360_declares_grid() -> None:
    """T1（D2）：类级声明 [4,15] 整数秒。"""
    p = _provider()
    assert p.min_video_duration == 4.0
    assert p.integer_seconds_only is True
    assert p.max_video_duration == 15.0


def test_seedance_submit_ceils_fractional_duration() -> None:
    """T2 变异靶：3.9 -> 4（int() 截断成 3 -> 400）。"""
    p = _provider()
    bodies: list[dict[str, Any]] = []

    class _C:
        is_closed = False

        async def post(self, url: str, **kw: Any) -> MagicMock:
            bodies.append(kw.get("json") or {})
            return MagicMock(status_code=200, json=lambda: {"data": {"id": "t1"}})

        async def get(self, url: str, **kw: Any) -> MagicMock:
            return MagicMock(
                status_code=200,
                json=lambda: {"data": {"status": "succeeded", "url": "http://v/1.mp4"}},
            )

    p._client = _C()  # type: ignore[assignment]
    asyncio.run(p.generate_video("一只猫", duration=3.9))
    assert bodies[0]["duration"] == 4


def test_seedance_4xx_raises_typed_client_error() -> None:
    """T3：4xx 必须抛 QihuClientError 且带 status_code（熔断豁免的载体）。

    修前是裸 RuntimeError（无 status_code）-> 分类器不豁免 -> 400 级联开闸。
    """
    p = _provider()

    class _C:
        is_closed = False

        async def post(self, url: str, **kw: Any) -> MagicMock:
            return MagicMock(status_code=400, text="bad duration", json=lambda: {})

        async def get(self, url: str, **kw: Any) -> MagicMock:  # pragma: no cover - 不会走到
            raise AssertionError("poll should not be reached")

    p._client = _C()  # type: ignore[assignment]
    try:
        asyncio.run(p.generate_video("一只猫", duration=3.0))
    except QihuClientError as e:
        assert e.status_code == 400
    else:
        raise AssertionError("4xx 必须抛 QihuClientError")


def test_qihu_4xx_raises_typed_client_error() -> None:
    """T3 基类侧同款：qihu_video 4xx -> QihuClientError（带 status_code）。"""
    from app.gateway.video_gen.providers.qihu_video import QihuVideoProvider

    p = QihuVideoProvider(api_key="k", base_url="http://b", model="minimax/MiniMax-H3", poll_interval=0)

    class _C:
        is_closed = False

        async def post(self, url: str, **kw: Any) -> MagicMock:
            return MagicMock(status_code=403, text="forbidden", json=lambda: {})

        async def get(self, url: str, **kw: Any) -> MagicMock:  # pragma: no cover
            raise AssertionError("poll should not be reached")

    p._client = _C()  # type: ignore[assignment]
    try:
        asyncio.run(p.generate_video("一只猫", duration=5.0))
    except QihuClientError as e:
        assert e.status_code == 403
    else:
        raise AssertionError("4xx 必须抛 QihuClientError（不再裸 RuntimeError）")


def test_qihu_5xx_still_server_error() -> None:
    """回归锚：5xx 仍是 QihuServerError（瞬态、可重试语义不变）。"""
    from app.gateway.video_gen.providers.qihu_video import QihuVideoProvider

    p = QihuVideoProvider(api_key="k", base_url="http://b", model="minimax/MiniMax-H3", poll_interval=0)

    class _C:
        is_closed = False

        async def post(self, url: str, **kw: Any) -> MagicMock:
            return MagicMock(status_code=500, text="boom", json=lambda: {})

        async def get(self, url: str, **kw: Any) -> MagicMock:  # pragma: no cover
            raise AssertionError("poll should not be reached")

    p._client = _C()  # type: ignore[assignment]
    try:
        asyncio.run(p.generate_video("一只猫", duration=5.0))
    except QihuServerError:
        pass
    else:
        raise AssertionError("5xx 必须仍抛 QihuServerError")
