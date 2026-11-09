"""Deterministic shutdown helper for BaseTaskService-based test services.

Why this exists (CI-only flake root cause):

Tests that run against ``sqlite+aiosqlite:///:memory:`` engines use a
:class:`~sqlalchemy.pool.StaticPool` — exactly one shared connection. If a
pipeline task spawned by ``submit``/``reap_stalled_tasks``/``start_ppt_pipeline``
is still alive when the fixture runs ``await engine.dispose()``, the dispose
greenlet yields to the event loop inside ``await_only`` (aiosqlite close is
async), the leaked task then hits the pool's ``_do_get``/``memoized_property``
window, and ``StaticPool.dispose()``'s final ``del self.__dict__["connection"]``
can land while the key is gone → ``KeyError: 'connection'`` at fixture teardown
(tests themselves pass; seen only on CI timing).

A bare ``cancel()`` + ``gather()`` is NOT enough: ``_wrap_pipeline``'s
``CancelledError`` branch treats an unmarked cancel as transient and — while
retry budget remains — schedules a brand-new pipeline task *during* the gather,
which then outlives the drain. This helper therefore mirrors the production
``delete_task``/``cancel_task`` contract: mark the ids in ``svc._deleting``
first (explicit cancel → no reschedule), then cancel + await until
``_active_tasks`` converges to no live entries (a concurrent Exception-path
retry can still register one more task before it sees the mark, so loop).
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.gateway._shared.task_framework.service import BaseTaskService


async def drain_service_tasks(svc: BaseTaskService) -> None:
    """Cancel and await all in-flight pipeline tasks so the engine can be disposed safely."""
    svc._deleting.update(svc._active_tasks.keys())
    seen: set[asyncio.Task] = set()
    while True:
        pending = [t for t in svc._active_tasks.values() if t not in seen and not t.done()]
        if not pending:
            return
        for t in pending:
            t.cancel()
            seen.add(t)
        await asyncio.gather(*pending, return_exceptions=True)
