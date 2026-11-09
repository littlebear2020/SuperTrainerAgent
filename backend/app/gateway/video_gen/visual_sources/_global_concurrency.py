"""Process-wide cap on concurrent upstream AI image/video API calls from ai_image/ai_video.

Mirrors ViMax's ``_API_SEM`` (engines/vimax/provider_bridge.py): without a global
cap, N concurrent tasks each gathering frames at frame_concurrency=3 produce 3N
concurrent upstream calls (字节 Ark seedream/seedance-ark by default, 360 as
fallback) → per-shot slowdown, 429 rate-limiting, cascade timeouts. Lazy
module-level singleton so image + video sources share one cap. Sized from
VideoGenConfig.ai_api_global_concurrency on first build (default 20).
"""

from __future__ import annotations

import asyncio

_SEM: asyncio.Semaphore | None = None
_SEM_LIMIT = 20


def get_ai_api_semaphore(limit: int | None = None) -> asyncio.Semaphore:
    """Return the process-global upstream-AI-API semaphore (lazy singleton).

    The first caller (with a non-None ``limit``) sets the size; subsequent callers
    get the SAME singleton regardless of the limit they pass (a process has one
    upstream budget). ``limit`` is therefore advisory — callers pass the config
    value so the FIRST construction in a fresh process sizes it from config.
    """
    global _SEM, _SEM_LIMIT
    if limit is not None:
        _SEM_LIMIT = max(1, limit)
    if _SEM is None:
        _SEM = asyncio.Semaphore(_SEM_LIMIT)
    return _SEM


def reset_for_test() -> None:
    """Test-only: drop the singleton so tests get a fresh semaphore."""
    global _SEM
    _SEM = None
