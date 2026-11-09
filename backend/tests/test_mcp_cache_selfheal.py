"""Self-heal: initialize_mcp_tools must not permanently cache an empty result
when MCP servers ARE configured (every server failed to connect). Otherwise a
single transient outage disables MCP for the whole session. Empty + no servers
configured is stable and CAN be cached.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from deerflow.mcp import cache as mcp_cache


@pytest.mark.asyncio
async def test_empty_result_with_servers_configured_is_not_cached():
    """Servers configured but all failed → don't mark initialized (retry next run)."""
    mcp_cache.reset_mcp_tools_cache()
    try:
        with (
            patch(
                "deerflow.mcp.tools.get_mcp_tools",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "deerflow.config.extensions_config.ExtensionsConfig.from_file",
                return_value=MagicMock(
                    get_enabled_mcp_servers=MagicMock(return_value={"metaso": {}}),
                ),
            ),
        ):
            result = await mcp_cache.initialize_mcp_tools()
        assert result == []
        # Not cached → next get_cached_mcp_tools() will retry instead of returning [].
        assert mcp_cache._cache_initialized is False
    finally:
        mcp_cache.reset_mcp_tools_cache()


@pytest.mark.asyncio
async def test_empty_result_with_no_servers_configured_is_cached():
    """No servers configured → empty is stable → cache it (no point retrying)."""
    mcp_cache.reset_mcp_tools_cache()
    try:
        with (
            patch(
                "deerflow.mcp.tools.get_mcp_tools",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "deerflow.config.extensions_config.ExtensionsConfig.from_file",
                return_value=MagicMock(
                    get_enabled_mcp_servers=MagicMock(return_value={}),
                ),
            ),
        ):
            result = await mcp_cache.initialize_mcp_tools()
        assert result == []
        assert mcp_cache._cache_initialized is True
    finally:
        mcp_cache.reset_mcp_tools_cache()


@pytest.mark.asyncio
async def test_nonempty_result_is_cached():
    """Normal case: tools loaded → cache + mark initialized."""
    mcp_cache.reset_mcp_tools_cache()
    fake_tool = MagicMock()
    try:
        with (
            patch(
                "deerflow.mcp.tools.get_mcp_tools",
                new_callable=AsyncMock,
                return_value=[fake_tool],
            ),
            patch(
                "deerflow.config.extensions_config.ExtensionsConfig.from_file",
                return_value=MagicMock(
                    get_enabled_mcp_servers=MagicMock(return_value={"metaso": {}}),
                ),
            ),
        ):
            result = await mcp_cache.initialize_mcp_tools()
        assert result == [fake_tool]
        assert mcp_cache._cache_initialized is True
    finally:
        mcp_cache.reset_mcp_tools_cache()
