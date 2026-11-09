"""Shared thread pool for isolating ffmpeg/ffprobe subprocess operations.

All ffmpeg/ffprobe-related sync functions should be called via run_ffmpeg()
instead of direct asyncio.to_thread(), ensuring:
1. Uses a dedicated thread pool (does not exhaust the default pool)
2. Concurrency is bounded (configurable, default 8)
3. Single call site reduces risk of missing await
"""

import asyncio
import concurrent.futures
import functools
import os

_max_workers = int(os.environ.get("FFMPEG_MAX_WORKERS", "0")) or min(8, os.cpu_count() or 4)

ffmpeg_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=_max_workers,
    thread_name_prefix="ffmpeg",
)


async def run_ffmpeg(fn, *args, **kwargs):
    """Execute a sync function in the dedicated ffmpeg thread pool.

    Usage:
        # Before (blocks event loop):
        _concat_audio(paths, output)

        # After:
        result = await run_ffmpeg(_concat_audio, paths, output)

    kwargs 透传（2026-08-20）：run_in_executor 本身不收 kwargs，经 partial 绑定——
    调用方可用关键字传可选参数（如 _concat_audio 的 expected_seconds），
    不占用 *args 位置（测试桩常按位置取参数，如 output=args[-1]）。
    """
    global ffmpeg_executor
    # Auto-recover if the executor was shut down (e.g. by app lifespan shutdown
    # during tests where multiple TestClient instances are created).
    if ffmpeg_executor._shutdown:
        ffmpeg_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=_max_workers,
            thread_name_prefix="ffmpeg",
        )
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(ffmpeg_executor, functools.partial(fn, *args, **kwargs))
