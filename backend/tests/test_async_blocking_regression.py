"""Regression tests: verify run_ffmpeg does not block the asyncio event loop.

These tests stub the ffmpeg work with a thread-blocking fake held open by a
threading.Event, then assert EVENT ORDER — a lightweight coroutine must complete
before the blocker is released — rather than wall-clock thresholds. If run_ffmpeg
regresses to a direct sync call, the blocker runs on the event-loop thread and the
order flips (the fake times out after 5s so the failure is a slow red, not a hang).
"""

import asyncio
import concurrent.futures
import threading
from unittest.mock import patch

import pytest


def _fake_completed_process(returncode: int = 0):
    """Create a fake subprocess.CompletedProcess-like object."""
    return type("R", (), {"returncode": returncode, "stdout": "", "stderr": b""})()


async def _assert_light_coroutine_wins_over_blocker(*args):
    """Run a thread-blocking fake via run_ffmpeg; a light coroutine must finish
    BEFORE the blocking body returns (event order, no wall clock)."""
    from app.gateway._shared.executors import run_ffmpeg

    order: list[str] = []
    blocker_entered = threading.Event()
    release_blocker = threading.Event()

    def fake_blocking_fn(*fargs, **fkwargs):
        blocker_entered.set()
        if not release_blocker.wait(timeout=5):
            order.append("blocker-timeout")  # only reachable on the regressed path
        order.append("blocker-returned")
        return _fake_completed_process()

    async def light_coroutine():
        order.append("light")

    ffmpeg_task = asyncio.create_task(run_ffmpeg(fake_blocking_fn, *args))

    # Wait until the worker thread is actually inside the blocking fn.
    for _ in range(1000):
        if blocker_entered.is_set():
            break
        await asyncio.sleep(0.001)
    assert blocker_entered.is_set(), "run_ffmpeg never started the blocking fn"

    await light_coroutine()
    release_blocker.set()
    await ffmpeg_task

    assert order == ["light", "blocker-returned"], f"event loop blocked or ordering broken: {order}"


@pytest.mark.asyncio
async def test_run_ffmpeg_does_not_block_event_loop():
    """run_ffmpeg executes in a thread pool — event loop stays responsive."""
    await _assert_light_coroutine_wins_over_blocker()


@pytest.mark.asyncio
async def test_concat_audio_via_run_ffmpeg_does_not_block():
    """Specific regression: _concat_audio path must not block event loop.

    Covers the main path (returncode=0).
    """
    with patch("subprocess.run", side_effect=AssertionError("test must stub via run_ffmpeg's fn, not subprocess.run")):
        await _assert_light_coroutine_wins_over_blocker(["/tmp/a.mp3"], "/tmp/out.mp3")


@pytest.mark.asyncio
async def test_run_ffmpeg_uses_shared_thread_pool_executor():
    """run_ffmpeg must dispatch onto the module-level ThreadPoolExecutor.

    Failing-capable replacement for the old duplicate-import `is` assertion
    (which only exercised the Python module cache): if run_ffmpeg ever switches
    to a per-call/default executor, the captured executor won't be the shared one.
    """
    from app.gateway._shared import executors

    assert isinstance(executors.ffmpeg_executor, concurrent.futures.ThreadPoolExecutor)

    seen: dict[str, object] = {}
    real_run_in_executor = asyncio.BaseEventLoop.run_in_executor

    def spy_run_in_executor(self, executor, func):  # noqa: ANN001, ANN202
        seen["executor"] = executor
        return real_run_in_executor(self, executor, func)

    with patch.object(asyncio.BaseEventLoop, "run_in_executor", spy_run_in_executor):
        await executors.run_ffmpeg(lambda: _fake_completed_process())

    assert seen.get("executor") is executors.ffmpeg_executor, "run_ffmpeg must reuse the module-level ffmpeg_executor"
