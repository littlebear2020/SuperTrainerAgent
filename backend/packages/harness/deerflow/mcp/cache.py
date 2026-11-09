"""Cache for MCP tools to avoid repeated loading."""

import asyncio
import hashlib
import logging
import os
from pathlib import Path
from typing import NamedTuple

from langchain_core.tools import BaseTool

logger = logging.getLogger(__name__)

_mcp_tools_cache: list[BaseTool] | None = None
_cache_initialized = False
# Last MCP load failure reason (C-06, 2026-08-13): surfaced to the agent by
# the tool-assembly layer when the web group is requested but zero tools load.
_last_mcp_load_error: str | None = None
_initialization_lock = asyncio.Lock()
_config_signature: "_ConfigSignature | None" = None  # Track config file identity


class _ConfigSignature(NamedTuple):
    """Content-aware identity of the extensions config file.

    mtime alone is not enough to detect a change: two edits inside one
    filesystem timestamp tick share an mtime, and `git checkout` / `cp -p`
    can even move mtime *backwards* while the content differs. Any of those
    left the cache serving stale tools until the process restarted.
    """

    mtime: float
    size: int
    digest: str


def _hash_file(path: Path) -> str:
    """Return the sha256 hex digest of a file, streamed in chunks."""
    hasher = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _get_config_signature() -> _ConfigSignature | None:
    """Get the current signature of the extensions config file.

    Returns:
        The signature, or None if the file doesn't exist.
    """
    from deerflow.config.extensions_config import ExtensionsConfig

    config_path = ExtensionsConfig.resolve_config_path()
    if not config_path or not config_path.exists():
        return None

    stat = os.stat(config_path)
    return _ConfigSignature(mtime=stat.st_mtime, size=stat.st_size, digest=_hash_file(Path(config_path)))


def _is_cache_stale() -> bool:
    """Check if the cache is stale due to config file changes.

    Ordering rationale: `os.stat` is a single syscall, so mtime+size are
    compared first and short-circuit the common "nothing changed" path —
    `get_cached_mcp_tools()` runs on every tool load, and hashing there would
    put a full file read in the hot path. The digest is only consulted when
    mtime+size are *identical*, i.e. exactly the ambiguous case where cheap
    metadata cannot distinguish "untouched" from "edited within the same
    timestamp tick" (or restored with preserved times). Any mtime or size
    difference is already conclusive and needs no hash at all.

    A read failure (file deleted between exists/stat/open, permission flip)
    degrades to "not stale" — we cannot prove a change, and this matches the
    tolerance of the old mtime-only path. It never propagates out of
    ``get_cached_mcp_tools``.

    Returns:
        True if the cache should be invalidated, False otherwise.
    """
    if not _cache_initialized:
        return False  # Not initialized yet, not stale
    if _config_signature is None:
        return False

    from deerflow.config.extensions_config import ExtensionsConfig

    config_path = ExtensionsConfig.resolve_config_path()
    if not config_path or not config_path.exists():
        return False  # unreadable now → can't prove a change → not stale

    try:
        stat = os.stat(config_path)
    except OSError:
        logger.warning("MCP config stat failed during staleness check; assuming unchanged", exc_info=True)
        return False

    # Cheap check first: a differing mtime or size is conclusive on its own —
    # no full-file hash in the hot path (the previous implementation hashed on
    # every call via _get_config_signature, defeating this short-circuit).
    if stat.st_mtime != _config_signature.mtime or stat.st_size != _config_signature.size:
        logger.info(f"MCP config file metadata changed (mtime: {_config_signature.mtime} -> {stat.st_mtime}, size: {_config_signature.size} -> {stat.st_size}), cache is stale")
        return True

    # Metadata identical — only a content hash can disambiguate the ambiguous
    # same-second edit / `cp -p` / `git checkout` case.
    try:
        current_digest = _hash_file(Path(config_path))
    except OSError:
        logger.warning("MCP config file became unreadable during staleness hash; assuming unchanged", exc_info=True)
        return False
    if current_digest != _config_signature.digest:
        logger.info("MCP config file content changed while mtime/size stayed identical (same-second edit or restored copy), cache is stale")
        return True

    return False


async def initialize_mcp_tools() -> list[BaseTool]:
    """Initialize and cache MCP tools.

    This should be called once at application startup.

    Returns:
        List of LangChain tools from all enabled MCP servers.
    """
    global _mcp_tools_cache, _cache_initialized, _config_signature

    async with _initialization_lock:
        if _cache_initialized:
            logger.info("MCP tools already initialized")
            return _mcp_tools_cache or []

        from deerflow.mcp.tools import get_mcp_tools

        logger.info("Initializing MCP tools...")
        _mcp_tools_cache = await get_mcp_tools()
        if not _mcp_tools_cache:
            # Empty result. If servers ARE configured, every server failed to
            # connect — DON'T cache it: a transient outage shouldn't disable
            # MCP for the whole session, the next run should retry. (No servers
            # configured → empty is stable, safe to cache below.)
            from deerflow.config.extensions_config import ExtensionsConfig

            try:
                has_servers = bool(ExtensionsConfig.from_file().get_enabled_mcp_servers())
            except Exception:
                has_servers = False
            if has_servers:
                _last_mcp_load_error = "MCP 服务器已配置但 0 个工具加载成功（检查连通性/认证）"
                logger.warning("MCP loaded 0 tools despite configured servers; will retry on next run (not cached). Check MCP server connectivity / auth.")
                return []
        _cache_initialized = True
        _config_signature = _get_config_signature()  # Record config file identity
        _last_mcp_load_error = None
        logger.info(f"MCP tools initialized: {len(_mcp_tools_cache or [])} tool(s) loaded (config signature: {_config_signature})")

        return _mcp_tools_cache or []


def get_cached_mcp_tools() -> list[BaseTool]:
    """Get cached MCP tools with lazy initialization.

    If tools are not initialized, automatically initializes them.
    This ensures MCP tools work in both FastAPI and LangGraph Studio contexts.

    Also checks if the config file has been modified since last initialization,
    and re-initializes if needed. This ensures that changes made through the
    Gateway API (which runs in a separate process) are reflected in the
    LangGraph Server.

    Returns:
        List of cached MCP tools.
    """
    global _cache_initialized, _last_mcp_load_error

    # Check if cache is stale due to config file changes
    if _is_cache_stale():
        logger.info("MCP cache is stale, resetting for re-initialization...")
        reset_mcp_tools_cache()

    if not _cache_initialized:
        logger.info("MCP tools not initialized, performing lazy initialization...")
        try:
            # Try to initialize in the current event loop
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # If loop is already running (e.g., in LangGraph Studio),
                # we need to create a new loop in a thread
                import concurrent.futures

                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(asyncio.run, initialize_mcp_tools())
                    future.result()
            else:
                # If no loop is running, we can use the current loop
                loop.run_until_complete(initialize_mcp_tools())
        except RuntimeError:
            # No event loop exists, create one
            try:
                asyncio.run(initialize_mcp_tools())
            except Exception as exc:
                # C-06 (2026-08-13): the failure reason is recorded so the
                # tool-assembly layer can surface "search unavailable" to the
                # agent instead of silently binding zero tools.
                _last_mcp_load_error = str(exc)
                logger.exception("Failed to lazy-initialize MCP tools")
                return []
        except Exception as exc:
            _last_mcp_load_error = str(exc)
            logger.exception("Failed to lazy-initialize MCP tools")
            return []

    return _mcp_tools_cache or []


def reset_mcp_tools_cache() -> None:
    """Reset the MCP tools cache.

    This is useful for testing or when you want to reload MCP tools.
    Also closes all persistent MCP sessions so they are recreated on
    the next tool load.
    """
    global _mcp_tools_cache, _cache_initialized, _config_signature
    _mcp_tools_cache = None
    _cache_initialized = False
    _config_signature = None

    # Close persistent sessions – they will be recreated by the next
    # get_mcp_tools() call with the (possibly updated) connection config.
    try:
        from deerflow.mcp.session_pool import get_session_pool

        pool = get_session_pool()
        pool.close_all_sync()
    except Exception:
        logger.debug("Could not close MCP session pool on cache reset", exc_info=True)

    from deerflow.mcp.session_pool import reset_session_pool

    reset_session_pool()
    logger.info("MCP tools cache reset")


def get_last_mcp_load_error() -> str | None:
    """Return the most recent MCP load failure reason, or None on success."""
    return _last_mcp_load_error


def _set_last_mcp_load_error(reason: str) -> None:
    """Record an MCP load failure reason (used by mcp.tools at load time)."""
    global _last_mcp_load_error
    _last_mcp_load_error = reason
