"""Helpers for persistence tests: per-test SQLite engine with guaranteed cleanup.

Shared-helper precedent: ``_router_auth_helpers.py`` / ``_task_drain.py``.

The pattern this replaces — ``await init_engine(...)`` at test start with a
bare ``await close_engine()`` at test end — leaks a dangling engine whenever
an assertion fails between the two calls (the close line never runs).
Fixtures built by :func:`sqlite_repo_fixture` close the engine in ``finally``
instead, so a red test cannot leak state into later tests in the same worker.

Usage::

    repo = sqlite_repo_fixture(RunRepository)

    async def test_x(repo): ...
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest


def sqlite_repo_fixture(repo_factory: Callable[[Any], Any], *, name: str = "repo"):
    """Build a function-scoped async fixture: fresh per-test SQLite engine + repo.

    ``repo_factory`` is called with ``get_session_factory()``'s return value
    (e.g. ``RunRepository`` / ``ThreadMetaRepository``). Each test gets its own
    ``tmp_path`` database file; the engine is closed in ``finally`` so a
    failing assertion cannot leak it.
    """

    @pytest.fixture(name=name)
    async def _repo(tmp_path):
        from deerflow.persistence.engine import close_engine, get_session_factory, init_engine

        await init_engine("sqlite", url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}", sqlite_dir=str(tmp_path))
        try:
            yield repo_factory(get_session_factory())
        finally:
            await close_engine()

    return _repo
