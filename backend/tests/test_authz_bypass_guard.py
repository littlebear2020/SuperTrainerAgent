"""C8（2026-10-05 审计 §1-8）：`_deerflow_test_bypass_auth` 旁路收编。

旁路属性只能在 pytest 环境下生效（PYTEST_CURRENT_TEST 存在）；生产进程即使
收到带该属性的请求对象也走正常鉴权路径。
"""

import pytest

from app.gateway.authz import require_auth


def _bypass_request():
    """带旁路属性的假请求（模拟生产进程收到被污染的请求对象）。"""
    from types import SimpleNamespace

    return SimpleNamespace(state=SimpleNamespace(), cookies={}, _deerflow_test_bypass_auth=True)


async def _handler(request):
    return "executed"


def test_bypass_ineffective_outside_pytest(monkeypatch):
    """PYTEST_CURRENT_TEST 缺失时旁路属性必须失效（走鉴权 → 拒绝）。"""
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    decorated = require_auth(_handler)

    from fastapi import HTTPException

    with pytest.raises((HTTPException, PermissionError, ValueError)):
        # 假请求无有效 cookie → 鉴权失败路径必须被走到（而非旁路直接执行）
        import asyncio

        asyncio.get_event_loop_policy().new_event_loop().run_until_complete(decorated(request=_bypass_request()))


def test_bypass_effective_under_pytest(monkeypatch):
    """pytest 环境下旁路保持可用（既有直接调用型测试依赖此行为）。"""
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "tests/test_x.py::test_y")
    decorated = require_auth(_handler)

    import asyncio

    result = asyncio.get_event_loop_policy().new_event_loop().run_until_complete(decorated(request=_bypass_request()))
    assert result == "executed"
