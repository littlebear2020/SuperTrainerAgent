"""Regression tests for Gateway lifespan shutdown.

These tests guard the invariant that lifespan shutdown is *bounded*: a
misbehaving channel whose ``stop()`` blocks forever must not keep the
uvicorn worker alive. A hung worker is the precondition for the
signal-reentrancy deadlock described in
``app.gateway.app._SHUTDOWN_HOOK_TIMEOUT_SECONDS``.
"""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from unittest.mock import MagicMock, patch

from fastapi import FastAPI


@asynccontextmanager
async def _noop_langgraph_runtime(_app, _startup_config):
    yield


async def _noop_warm_tiktoken(*_a, **_kw):
    """tiktoken BPE warm-up 自带 5s 网络超时（离线/受限网络下不确定性命中），
    与关停有界性契约无关——桩掉以保持测试确定性。"""
    return None


async def _run_lifespan_with_hanging_stop() -> float:
    """Drive the lifespan context with stop_channel_service hanging forever.

    Returns the elapsed wall-clock seconds.
    """
    from app.gateway.app import _SHUTDOWN_HOOK_TIMEOUT_SECONDS, lifespan

    async def hang_forever() -> None:
        await asyncio.sleep(3600)

    app = FastAPI()

    fake_service = MagicMock()
    fake_service.get_status = MagicMock(return_value={})

    async def fake_start():
        return fake_service

    with (
        patch("app.gateway.app.get_app_config"),
        patch("app.gateway.app.get_gateway_config", return_value=MagicMock(host="x", port=0)),
        patch("app.gateway.app.langgraph_runtime", _noop_langgraph_runtime),
        patch("app.gateway.app._warm_tiktoken_encoding_cache", _noop_warm_tiktoken),
        patch("app.channels.service.start_channel_service", side_effect=fake_start),
        patch("app.channels.service.stop_channel_service", side_effect=hang_forever),
    ):
        loop = asyncio.get_event_loop()
        start = loop.time()
        async with lifespan(app):
            pass
        elapsed = loop.time() - start

    assert _SHUTDOWN_HOOK_TIMEOUT_SECONDS < 30.0, "Timeout constant must stay modest"
    return elapsed


# 测试用关停钩超时（秒）：缩小 ``_SHUTDOWN_HOOK_TIMEOUT_SECONDS`` 的等待语义不变
# （仍然真等到 wait_for/executor 超时触发），只缩幅度；上下界断言随其缩放。
_TEST_HOOK_TIMEOUT = 0.4


def _patch_hook_timeout(monkeypatch) -> None:
    """monkeypatch 关停钩超时为测试用小值。

    不能用字符串目标 ``"app.gateway.app._SHUTDOWN_HOOK_TIMEOUT_SECONDS"``：
    ``app.gateway.__init__`` 把 FastAPI 实例 re-export 成 ``app`` 名，遮蔽同名
    子模块，monkeypatch 的字符串解析会拿到 FastAPI 对象。
    """
    import importlib

    gateway_app = importlib.import_module("app.gateway.app")
    monkeypatch.setattr(gateway_app, "_SHUTDOWN_HOOK_TIMEOUT_SECONDS", _TEST_HOOK_TIMEOUT)


def test_shutdown_is_bounded_when_channel_stop_hangs(monkeypatch):
    """Lifespan exit must complete near the configured timeout, not hang."""
    _patch_hook_timeout(monkeypatch)

    elapsed = asyncio.run(_run_lifespan_with_hanging_stop())

    # 上界只需证明「有界」（钩死等 = 3600s），不必紧贴 timeout——xdist 并行
    # 负载下事件循环调度延迟可超 1.5s，slack 给 10s 鉴别力不变。
    assert elapsed < _TEST_HOOK_TIMEOUT + 10.0, f"Lifespan shutdown took {elapsed:.2f}s; expected <= {_TEST_HOOK_TIMEOUT + 10.0:.1f}s"
    # Lower bound: the wait_for should actually have waited (scaled with the timeout).
    assert elapsed >= _TEST_HOOK_TIMEOUT * 0.6, f"Lifespan exited too quickly ({elapsed:.2f}s); wait_for may not have been invoked."


async def _run_lifespan(*, flush_impl=None) -> MagicMock:
    """Drive lifespan start→exit with the memory queue patched out.

    Returns the fake queue so callers can assert on ``flush``.
    """
    from app.gateway.app import lifespan

    fake_queue = MagicMock()
    if flush_impl is not None:
        fake_queue.flush.side_effect = flush_impl

    app = FastAPI()

    fake_service = MagicMock()
    fake_service.get_status = MagicMock(return_value={})

    async def fake_start():
        return fake_service

    async def fake_stop():
        return None

    with (
        patch("app.gateway.app.get_app_config"),
        patch("app.gateway.app.get_gateway_config", return_value=MagicMock(host="x", port=0)),
        patch("app.gateway.app.langgraph_runtime", _noop_langgraph_runtime),
        patch("app.gateway.app._warm_tiktoken_encoding_cache", _noop_warm_tiktoken),
        patch("app.channels.service.start_channel_service", side_effect=fake_start),
        patch("app.channels.service.stop_channel_service", side_effect=fake_stop),
        patch("deerflow.agents.memory.queue.get_memory_queue", return_value=fake_queue),
    ):
        async with lifespan(app):
            pass

    return fake_queue


def test_shutdown_flushes_pending_memory_updates():
    """Queued memory must be written before the process exits.

    ``MemoryUpdateQueue`` debounces for 30s on a ``daemon=True`` timer thread,
    which the interpreter kills outright at exit. Nothing in ``app/`` called
    the existing ``flush()``, so every deploy restart silently discarded up to
    30 seconds of just-stated user preferences.
    """
    fake_queue = _run_lifespan_sync()

    fake_queue.flush.assert_called_once_with()


def _run_lifespan_sync(*, flush_impl=None) -> MagicMock:
    return asyncio.run(_run_lifespan(flush_impl=flush_impl))


def test_memory_flush_is_bounded_when_it_hangs(monkeypatch):
    """A stuck flush (LLM call inside it) must not pin the worker open."""
    _patch_hook_timeout(monkeypatch)

    def hang_forever() -> None:
        time.sleep(3600)

    async def run() -> float:
        loop = asyncio.get_event_loop()
        start = loop.time()
        await _run_lifespan(flush_impl=hang_forever)
        return loop.time() - start

    elapsed = asyncio.run(run())

    assert elapsed < _TEST_HOOK_TIMEOUT + 10.0, f"Memory flush was not bounded: took {elapsed:.2f}s"
    assert elapsed >= _TEST_HOOK_TIMEOUT * 0.6, f"Lifespan exited too quickly ({elapsed:.2f}s); the flush timeout may not have been applied."


def test_memory_flush_failure_does_not_break_shutdown():
    """A raising flush is logged, not propagated — shutdown must still finish."""

    def boom() -> None:
        raise RuntimeError("memory backend unavailable")

    fake_queue = _run_lifespan_sync(flush_impl=boom)

    assert fake_queue.flush.call_count == 1
