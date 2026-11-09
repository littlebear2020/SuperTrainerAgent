"""Centralized accessors for singleton objects stored on ``app.state``.

**Getters** (used by routers): raise 503 when a required dependency is
missing, except ``get_store`` which returns ``None``.

Initialization is handled directly in ``app.py`` via :class:`AsyncExitStack`.
"""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import AsyncGenerator, Callable, Mapping, Sequence
from contextlib import AsyncExitStack, asynccontextmanager
from typing import TYPE_CHECKING, TypeVar, cast

from fastapi import FastAPI, HTTPException, Request
from langgraph.types import Checkpointer

from deerflow.config.app_config import AppConfig
from deerflow.persistence.feedback import FeedbackRepository
from deerflow.runtime import RunContext, RunManager, StreamBridge
from deerflow.runtime.events.store.base import RunEventStore
from deerflow.runtime.runs.store.base import RunStore

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from app.gateway.auth.local_provider import LocalAuthProvider
    from app.gateway.auth.repositories.sqlite import SQLiteUserRepository
    from deerflow.persistence.thread_meta.base import ThreadMetaStore
    from deerflow.runtime import RunRecord


T = TypeVar("T")


async def _mark_latest_recovered_threads_error(
    run_manager: RunManager,
    thread_store: ThreadMetaStore,
    recovered_runs: list[RunRecord],
) -> None:
    """Mark thread status as error only when its newest run was recovered."""
    recovered_by_thread: dict[str, set[str]] = {}
    for record in recovered_runs:
        recovered_by_thread.setdefault(record.thread_id, set()).add(record.run_id)

    for thread_id, recovered_run_ids in recovered_by_thread.items():
        try:
            latest_runs = await run_manager.list_by_thread(thread_id)
        except Exception:
            logger.warning("Failed to find latest run for thread %s during run reconciliation", thread_id, exc_info=True)
            continue
        if not latest_runs or latest_runs[0].run_id not in recovered_run_ids:
            continue
        try:
            await thread_store.update_status(thread_id, "error", user_id=None)
        except Exception:
            logger.warning("Failed to mark thread %s as error during run reconciliation", thread_id, exc_info=True)


def get_config(request: Request) -> AppConfig:
    """Return the freshest ``AppConfig`` for the current request.

    Routes through :func:`deerflow.config.app_config.get_app_config`, which
    reloads ``config.yaml`` from disk when its mtime changes. This closes the
    R-07 split-brain (2026-08-13): the previous implementation returned the
    startup snapshot stored on ``app.state``, so all 21 ``Depends(get_config)``
    sites (models / skills / gallery / uploads / suggestions) required a
    process restart to see config.yaml edits.

    Hot-reload boundary: fields backed by startup-time singletons (database
    engine, checkpointer binding, sandbox provider, IM channels, logging
    level) remain restart-required by design — see ``backend/CLAUDE.md``
    "Config Hot-Reload Boundary".

    Any failure to materialise the config (missing file, permission denied,
    YAML parse error, validation error) is reported as 503 — semantically
    "the gateway cannot serve requests without a usable configuration" — and
    logged with the original exception so operators have something to debug.
    """
    try:
        from deerflow.config import get_app_config

        return get_app_config()
    except Exception as exc:
        logger.exception("Failed to load AppConfig at request time")
        raise HTTPException(status_code=503, detail="Configuration not available") from exc


def resolve_worker_count(argv: Sequence[str] | None = None, environ: Mapping[str, str] | None = None) -> int:
    """Return the number of Gateway worker processes uvicorn was asked to start.

    Read from ``sys.argv`` first, because that is where the real value lives:
    Docker Compose interpolates ``--workers ${GATEWAY_WORKERS:-1}`` into the
    gateway ``command`` string at *parse* time and does **not** export
    ``GATEWAY_WORKERS`` into the container environment. Reading only the env var
    would therefore miss ``GATEWAY_WORKERS=4 docker compose up`` entirely, and
    would falsely trip when the var is set in an ``env_file`` that compose never
    used for interpolation. ``sys.argv`` is inherited by uvicorn's spawned worker
    subprocesses, so this resolves identically in parent and children.

    Falls back to ``WEB_CONCURRENCY`` (uvicorn honours it when ``--workers`` is
    absent, see ``uvicorn.config.Config.__init__``) and finally to 1.

    Unparseable values return 1: the guard exists to catch a deliberate scale-up,
    and refusing to boot over a malformed flag we cannot interpret would be worse
    than letting uvicorn report its own error.
    """
    args = list(sys.argv if argv is None else argv)
    env = os.environ if environ is None else environ

    for index, arg in enumerate(args):
        value: str | None = None
        if arg == "--workers" and index + 1 < len(args):
            value = args[index + 1]
        elif arg.startswith("--workers="):
            value = arg.split("=", 1)[1]
        if value is not None:
            try:
                return int(value)
            except (TypeError, ValueError):
                return 1

    raw = env.get("WEB_CONCURRENCY")
    if raw:
        try:
            return int(raw)
        except (TypeError, ValueError):
            return 1
    return 1


def _multi_worker_bridge_error(workers: int, bridge: StreamBridge) -> str:
    """Explain why *workers* processes cannot share *bridge*, and how to fix it."""
    return (
        f"Refusing to start: the Gateway was launched with {workers} worker processes, but the active "
        f"stream bridge ({type(bridge).__name__}) cannot deliver run events across processes.\n"
        "\n"
        "Run state — the RunManager task table and this bridge's SSE event log — lives in the heap of "
        "whichever worker created the run. Uvicorn spreads requests across workers with no affinity, so a "
        "client that reconnects to its own run usually lands on a worker that has never heard of it. The "
        "symptom is intermittent, load-dependent 'Run not found' 404s and SSE streams that hang or drop "
        "mid-answer, with nothing in the logs to explain it. Cancellation and request de-duplication break "
        "the same way.\n"
        "\n"
        "Fix, either:\n"
        "  * Run a single worker — set GATEWAY_WORKERS=1 (or drop --workers). This is the supported "
        "configuration and the default in docker/docker-compose.yaml and docker-compose-deploy.yaml.\n"
        "  * Or wait for a cross-process stream bridge. 'stream_bridge.type: redis' is declared in config "
        "but not implemented yet (make_stream_bridge raises NotImplementedError), so no multi-worker "
        "configuration currently works. Once a bridge sets cross_process = True, this check stops firing "
        "on its own.\n"
        "\n"
        "Scaling in the meantime: run more single-worker Gateway containers behind the nginx upstream, "
        "each owning its own runs."
    )


def _enforce_single_worker_for_process_local_bridge(workers: int, bridge: StreamBridge) -> None:
    """Refuse to boot when multiple workers would silently lose run state.

    Keyed off the bridge's own ``cross_process`` capability flag rather than a
    hardcoded class name, so landing a Redis/Postgres bridge retires this guard
    without editing it.
    """
    if workers <= 1:
        return
    if getattr(bridge, "cross_process", False):
        return
    raise SystemExit(_multi_worker_bridge_error(workers, bridge))


@asynccontextmanager
async def langgraph_runtime(app: FastAPI, startup_config: AppConfig) -> AsyncGenerator[None, None]:
    """Bootstrap and tear down all LangGraph runtime singletons.

    ``startup_config`` is the snapshot taken in ``lifespan()`` at process
    start: the engines built here (database engine, checkpointer binding,
    run-event store, stream bridge) are restart-required by design, so they
    deliberately do NOT follow per-request hot reloads (R-07, 2026-08-13).

    Usage in ``app.py``::

        async with langgraph_runtime(app, startup_config):
            yield
    """
    from deerflow.persistence.engine import close_engine, get_session_factory, init_engine_from_config
    from deerflow.runtime import make_store, make_stream_bridge
    from deerflow.runtime.checkpointer.async_provider import make_checkpointer
    from deerflow.runtime.events.store import make_run_event_store

    async with AsyncExitStack() as stack:
        config = startup_config
        if config is None:
            raise RuntimeError("langgraph_runtime() requires app.state.config to be initialized")

        app.state.stream_bridge = await stack.enter_async_context(make_stream_bridge(config))

        # Multi-worker safety gate: a process-local bridge cannot serve runs
        # created in a sibling worker. Checked right after construction so the
        # error names the bridge that was actually selected, and before any
        # persistence engine opens connections.
        _enforce_single_worker_for_process_local_bridge(resolve_worker_count(), app.state.stream_bridge)

        # Import app-level ORM models so Base.metadata.create_all discovers
        # their tables.  These are outside the harness import chain.
        # Use importlib to avoid binding "app" as a local name which would
        # shadow the FastAPI app parameter.
        import importlib

        importlib.import_module("app.gateway.voice_twin.models")
        importlib.import_module("app.gateway._shared.task_framework.models")
        importlib.import_module("app.gateway.content.models")

        # Initialize persistence engine BEFORE checkpointer so that
        # auto-create-database logic runs first (postgres backend).
        await init_engine_from_config(config.database)

        app.state.checkpointer = await stack.enter_async_context(make_checkpointer(config))
        app.state.store = await stack.enter_async_context(make_store(config))

        # Initialize repositories — one get_session_factory() call for all.
        sf = get_session_factory()
        if sf is not None:
            from deerflow.persistence.feedback import FeedbackRepository
            from deerflow.persistence.run import RunRepository

            app.state.run_store = RunRepository(sf)
            app.state.feedback_repo = FeedbackRepository(sf)
        else:
            from deerflow.runtime.runs.store.memory import MemoryRunStore

            app.state.run_store = MemoryRunStore()
            app.state.feedback_repo = None

        from deerflow.persistence.thread_meta import make_thread_store

        app.state.thread_store = make_thread_store(sf, app.state.store)

        # Run event store
        run_events_config = getattr(config, "run_events", None)
        app.state.run_event_store = make_run_event_store(run_events_config)

        # RunManager with store backing for persistence
        app.state.run_manager = RunManager(store=app.state.run_store)
        if getattr(config.database, "backend", None) == "sqlite":
            from datetime import UTC, datetime

            # Startup-only recovery: clean shutdowns return no active rows and
            # the thread-status update below becomes a no-op.
            recovered_runs = await app.state.run_manager.reconcile_orphaned_inflight_runs(
                error="Gateway restarted before this run reached a durable final state.",
                before=datetime.now(UTC).isoformat(),
            )
            await _mark_latest_recovered_threads_error(app.state.run_manager, app.state.thread_store, recovered_runs)

        try:
            yield
        finally:
            await close_engine()


# ---------------------------------------------------------------------------
# Getters – called by routers per-request
# ---------------------------------------------------------------------------


def _require(attr: str, label: str) -> Callable[[Request], T]:
    """Create a FastAPI dependency that returns ``app.state.<attr>`` or 503."""

    def dep(request: Request) -> T:
        val = getattr(request.app.state, attr, None)
        if val is None:
            raise HTTPException(status_code=503, detail=f"{label} not available")
        return cast(T, val)

    dep.__name__ = dep.__qualname__ = f"get_{attr}"
    return dep


get_stream_bridge: Callable[[Request], StreamBridge] = _require("stream_bridge", "Stream bridge")
get_run_manager: Callable[[Request], RunManager] = _require("run_manager", "Run manager")
get_checkpointer: Callable[[Request], Checkpointer] = _require("checkpointer", "Checkpointer")
get_run_event_store: Callable[[Request], RunEventStore] = _require("run_event_store", "Run event store")
get_feedback_repo: Callable[[Request], FeedbackRepository] = _require("feedback_repo", "Feedback")
get_run_store: Callable[[Request], RunStore] = _require("run_store", "Run store")


def get_store(request: Request):
    """Return the global store (may be ``None`` if not configured)."""
    return getattr(request.app.state, "store", None)


def get_thread_store(request: Request) -> ThreadMetaStore:
    """Return the thread metadata store (SQL or memory-backed)."""
    val = getattr(request.app.state, "thread_store", None)
    if val is None:
        raise HTTPException(status_code=503, detail="Thread metadata store not available")
    return val


def get_run_context(request: Request) -> RunContext:
    """Build a :class:`RunContext` from ``app.state`` singletons."""
    return RunContext(
        checkpointer=get_checkpointer(request),
        store=get_store(request),
        event_store=get_run_event_store(request),
        run_events_config=getattr(request.app.state, "run_events_config", None),
        thread_store=get_thread_store(request),
        app_config=get_config(request),
    )


# ---------------------------------------------------------------------------
# Auth helpers (used by authz.py and auth middleware)
# ---------------------------------------------------------------------------

# Cached singletons to avoid repeated instantiation per request
_cached_local_provider: LocalAuthProvider | None = None
_cached_repo: SQLiteUserRepository | None = None


def get_local_provider() -> LocalAuthProvider:
    """Get or create the cached LocalAuthProvider singleton.

    Must be called after ``init_engine_from_config()`` — the shared
    session factory is required to construct the user repository.
    """
    global _cached_local_provider, _cached_repo
    if _cached_repo is None:
        from app.gateway.auth.repositories.sqlite import SQLiteUserRepository
        from deerflow.persistence.engine import get_session_factory

        sf = get_session_factory()
        if sf is None:
            raise RuntimeError("get_local_provider() called before init_engine_from_config(); cannot access users table")
        _cached_repo = SQLiteUserRepository(sf)
    if _cached_local_provider is None:
        from app.gateway.auth.local_provider import LocalAuthProvider

        _cached_local_provider = LocalAuthProvider(repository=_cached_repo)
    return _cached_local_provider


async def get_current_user_from_request(request: Request):
    """Get the current authenticated user from the request cookie.

    Raises HTTPException 401 if not authenticated.
    """
    from app.gateway.auth import decode_token
    from app.gateway.auth.errors import AuthErrorCode, AuthErrorResponse, TokenError, token_error_to_code

    access_token = request.cookies.get("access_token")
    if not access_token:
        raise HTTPException(
            status_code=401,
            detail=AuthErrorResponse(code=AuthErrorCode.NOT_AUTHENTICATED, message="Not authenticated").model_dump(),
        )

    payload = decode_token(access_token)
    if isinstance(payload, TokenError):
        raise HTTPException(
            status_code=401,
            detail=AuthErrorResponse(code=token_error_to_code(payload), message=f"Token error: {payload.value}").model_dump(),
        )

    provider = get_local_provider()
    user = await provider.get_user(payload.sub)
    if user is None:
        raise HTTPException(
            status_code=401,
            detail=AuthErrorResponse(code=AuthErrorCode.USER_NOT_FOUND, message="User not found").model_dump(),
        )

    # Token version mismatch → password was changed, token is stale
    if user.token_version != payload.ver:
        raise HTTPException(
            status_code=401,
            detail=AuthErrorResponse(code=AuthErrorCode.TOKEN_INVALID, message="Token revoked (password changed)").model_dump(),
        )

    return user


async def get_optional_user_from_request(request: Request):
    """Get optional authenticated user from request.

    Returns None if not authenticated.
    """
    try:
        return await get_current_user_from_request(request)
    except HTTPException:
        return None


async def get_current_user(request: Request) -> str | None:
    """Extract user_id from request cookie, or None if not authenticated.

    Thin adapter that returns the string id for callers that only need
    identification (e.g., ``feedback.py``). Full-user callers should use
    ``get_current_user_from_request`` or ``get_optional_user_from_request``.
    """
    user = await get_optional_user_from_request(request)
    return str(user.id) if user else None
