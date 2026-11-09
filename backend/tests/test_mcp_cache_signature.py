"""MCP config cache invalidation must not rely on mtime alone.

An mtime-only signature silently misses three real-world cases:
  * two edits within the same filesystem timestamp granularity (same-second),
  * `git checkout` / `git stash pop` restoring a file with an older mtime,
  * `cp -p` / rsync-with-times, which preserves the source mtime.

In all three the config content changed but the cache keeps serving the old
tool list until the process restarts. These tests pin the content-aware
signature (mtime + size + sha256).
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from deerflow.mcp import cache as mcp_cache


def _write_config(path: Path, servers: dict, *, mtime: float | None = None) -> None:
    """Write an extensions config, optionally forcing an exact mtime."""
    path.write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")
    if mtime is not None:
        import os

        os.utime(path, (mtime, mtime))


def _initialized_cache(config_path: Path, tool):
    """Patch resolve_config_path + get_mcp_tools for a primed cache."""
    return (
        patch(
            "deerflow.config.extensions_config.ExtensionsConfig.resolve_config_path",
            return_value=config_path,
        ),
        patch("deerflow.mcp.tools.get_mcp_tools", new_callable=AsyncMock, return_value=[tool]),
    )


@pytest.mark.asyncio
async def test_same_second_content_change_invalidates_cache(tmp_path):
    """Content changed but mtime identical → cache MUST still be stale.

    This is precisely the case an mtime-only signature cannot see, so a test
    that merely bumps mtime would pass against the buggy implementation and
    prove nothing.
    """
    config_path = tmp_path / "extensions_config.json"
    frozen_mtime = 1_700_000_000.0
    _write_config(config_path, {"metaso": {"enabled": True}}, mtime=frozen_mtime)

    mcp_cache.reset_mcp_tools_cache()
    try:
        resolve_patch, tools_patch = _initialized_cache(config_path, MagicMock())
        with resolve_patch, tools_patch:
            await mcp_cache.initialize_mcp_tools()
            assert mcp_cache._is_cache_stale() is False, "freshly primed cache must not be stale"

            # Same mtime, different content — the exact blind spot.
            _write_config(config_path, {"metaso": {"enabled": False}}, mtime=frozen_mtime)
            import os

            assert os.path.getmtime(config_path) == frozen_mtime, "test setup: mtime must be unchanged"

            assert mcp_cache._is_cache_stale() is True, "same-second content change must invalidate the cache"
    finally:
        mcp_cache.reset_mcp_tools_cache()


@pytest.mark.asyncio
async def test_older_mtime_content_change_invalidates_cache(tmp_path):
    """`git checkout` / `cp -p` can move mtime BACKWARDS while content differs."""
    config_path = tmp_path / "extensions_config.json"
    _write_config(config_path, {"metaso": {"enabled": True}}, mtime=1_700_000_500.0)

    mcp_cache.reset_mcp_tools_cache()
    try:
        resolve_patch, tools_patch = _initialized_cache(config_path, MagicMock())
        with resolve_patch, tools_patch:
            await mcp_cache.initialize_mcp_tools()
            assert mcp_cache._is_cache_stale() is False

            # Restored from git: different content, *older* timestamp.
            _write_config(config_path, {"so_360": {"enabled": True}}, mtime=1_700_000_100.0)

            assert mcp_cache._is_cache_stale() is True, "content change with older mtime must invalidate the cache"
    finally:
        mcp_cache.reset_mcp_tools_cache()


@pytest.mark.asyncio
async def test_staleness_short_circuits_on_metadata_change_without_hashing(tmp_path):
    """C3: a differing mtime/size is conclusive — the hot path must NOT hash the
    whole file on every tool load. ``_hash_file`` is reserved for the ambiguous
    same-mtime/size case only. (Previously ``_get_config_signature`` hashed
    unconditionally, defeating the documented short-circuit.)"""
    config_path = tmp_path / "extensions_config.json"
    _write_config(config_path, {"metaso": {"enabled": True}})

    mcp_cache.reset_mcp_tools_cache()
    try:
        resolve_patch, tools_patch = _initialized_cache(config_path, MagicMock())
        with resolve_patch, tools_patch:
            await mcp_cache.initialize_mcp_tools()

            hash_spy = MagicMock()
            with patch("deerflow.mcp.cache._hash_file", hash_spy):
                # Larger content → size differs (mtime-independent), so the
                # metadata check is conclusive on its own.
                _write_config(config_path, {"so_360": {"enabled": True}, "metaso": {"enabled": True}})
                assert mcp_cache._is_cache_stale() is True
            hash_spy.assert_not_called(), "metadata change must short-circuit before hashing the whole file"
    finally:
        mcp_cache.reset_mcp_tools_cache()


@pytest.mark.asyncio
async def test_staleness_tolerates_hash_read_failure(tmp_path):
    """C3: if the digest read fails (file deleted between stat and open, perm
    flip), ``_is_cache_stale`` must degrade to 'not stale' rather than propagate
    and take down tool loading — matching the old mtime-only path's tolerance."""
    config_path = tmp_path / "extensions_config.json"
    _write_config(config_path, {"metaso": {"enabled": True}})

    mcp_cache.reset_mcp_tools_cache()
    try:
        resolve_patch, tools_patch = _initialized_cache(config_path, MagicMock())
        with resolve_patch, tools_patch:
            await mcp_cache.initialize_mcp_tools()
            # File untouched → mtime/size match → code reaches the hash step.
            with patch("deerflow.mcp.cache._hash_file", side_effect=OSError("permission denied")):
                assert mcp_cache._is_cache_stale() is False, "hash failure must degrade to not-stale, not raise"
    finally:
        mcp_cache.reset_mcp_tools_cache()
