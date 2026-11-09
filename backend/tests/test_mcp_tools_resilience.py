"""Resilience test: one broken MCP server must not disable the others.

Root cause of the 联网搜索 outage: get_mcp_tools() used one
MultiServerMCPClient.get_tools() over ALL servers, whose internal
asyncio.gather propagates the first server's exception → so_360's 400
killed metaso too → returned [] → cached empty → zero search tools for
every run. This test locks the per-server isolation fix.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from deerflow.mcp.tools import get_mcp_tools


def _patches():
    """so_360 fails (400), metaso succeeds.

    FakeClient reproduces MultiServerMCPClient.get_tools() semantics: it loads
    each configured server and — like the real asyncio.gather — raises if ANY
    configured server fails. So a single client configured with both servers
    raises (the bug); a per-server client configured with only metaso succeeds
    (the fix).
    """

    class FakeClient:
        def __init__(self, config, **_kw):
            self._servers = list(config.keys())

        async def get_tools(self):
            tools = []
            for s in self._servers:
                if s == "so_360_search_mcp":
                    raise RuntimeError("400 Bad Request from api.360.cn")
                t = MagicMock()
                t.name = f"{s}_search"
                tools.append(t)
            return tools

    return (
        patch("langchain_mcp_adapters.client.MultiServerMCPClient", FakeClient),
        patch(
            "deerflow.config.extensions_config.ExtensionsConfig.from_file",
            return_value=MagicMock(
                get_enabled_mcp_servers=MagicMock(return_value={}),
                model_extra={},
            ),
        ),
        patch(
            "deerflow.mcp.tools.build_servers_config",
            return_value={
                "metaso": {"url": "https://metaso.cn/api/mcp"},
                "so_360_search_mcp": {"url": "https://api.360.cn/.../sse"},
            },
        ),
        patch(
            "deerflow.mcp.tools.get_initial_oauth_headers",
            new_callable=AsyncMock,
            return_value={},
        ),
        patch("deerflow.mcp.tools.build_oauth_tool_interceptor", return_value=None),
        # Identity-wrap so the test asserts which tools survive, not the wrapping.
        patch(
            "deerflow.mcp.tools._make_session_pool_tool",
            side_effect=lambda tool, *_a, **_k: tool,
        ),
    )


def test_broken_mcp_server_does_not_kill_working_server():
    p = _patches()
    with p[0], p[1], p[2], p[3], p[4], p[5]:
        tools = asyncio.run(get_mcp_tools())

    names = [t.name for t in tools]
    assert "metaso_search" in names  # metaso survived so_360's failure
    assert len(tools) == 1  # only metaso loaded; so_360's 400 didn't return []


def test_all_servers_failing_returns_empty_without_raising():
    """If every server fails, get_mcp_tools returns [] (not raise)."""
    p = _patches()

    class AllFailClient:
        def __init__(self, config, **_kw):
            pass

        async def get_tools(self):
            raise RuntimeError("connection refused")

    with (
        patch("langchain_mcp_adapters.client.MultiServerMCPClient", AllFailClient),
        p[1],
        p[2],
        p[3],
        p[4],
        p[5],
    ):
        tools = asyncio.run(get_mcp_tools())

    assert tools == []
