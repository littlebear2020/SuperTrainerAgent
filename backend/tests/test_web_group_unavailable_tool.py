"""Tests for the C-06 web-group warning + search-unavailable passthrough (2026-08-13, decision D2)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from deerflow.config.app_config import AppConfig
from deerflow.config.sandbox_config import SandboxConfig
from deerflow.tools.tools import get_available_tools


def _make_config(tools: list | None = None) -> AppConfig:
    return AppConfig(
        models=[],
        tools=tools or [],
        sandbox=SandboxConfig(use="deerflow.sandbox.local:LocalSandboxProvider"),
    )


@pytest.fixture()
def no_mcp(monkeypatch):
    """No MCP servers configured and no cached tools."""

    def _no_servers():
        return SimpleNamespace(get_enabled_mcp_servers=lambda: [])

    monkeypatch.setattr("deerflow.config.extensions_config.ExtensionsConfig.from_file", _no_servers)
    monkeypatch.setattr("deerflow.mcp.cache.get_cached_mcp_tools", lambda: [])


def test_web_group_with_zero_tools_gets_unavailable_tool(no_mcp):
    """An agent declaring the web group with zero web tools gets a fallback
    tool whose result states search is unavailable (no silent zero-tool web)."""
    tools = get_available_tools(groups=["web"], include_mcp=True, app_config=_make_config())

    names = [t.name for t in tools]
    assert "web_search_unavailable" in names

    tool = next(t for t in tools if t.name == "web_search_unavailable")
    result = tool.invoke({})
    assert "搜索服务不可用" in result
    assert "config.yaml 未配置 web 组工具" in result


def test_mcp_load_error_reason_surfaces_in_tool_result(no_mcp, monkeypatch):
    """When MCP loading failed, the recorded reason is passed through to the agent."""
    monkeypatch.setattr("deerflow.mcp.cache._last_mcp_load_error", "metaso SSE 握手失败：400 Bad Request")

    tools = get_available_tools(groups=["web"], include_mcp=True, app_config=_make_config())

    tool = next(t for t in tools if t.name == "web_search_unavailable")
    result = tool.invoke({})
    assert "metaso SSE 握手失败" in result


def test_config_web_tool_present_skips_unavailable_tool(no_mcp, monkeypatch):
    """When the web group has real config tools, no fallback is injected."""

    class _FakeWebSearch:
        name = "web_search"

        def invoke(self, *a, **k):
            return "results"

    monkeypatch.setattr("deerflow.tools.tools.resolve_variable", lambda use, base: _FakeWebSearch())
    from deerflow.config.tool_config import ToolConfig

    config = _make_config(tools=[ToolConfig(name="web_search", group="web", use="fake:web_search")])
    tools = get_available_tools(groups=["web"], include_mcp=True, app_config=config)

    assert "web_search" in [t.name for t in tools]
    assert "web_search_unavailable" not in [t.name for t in tools]


def test_mcp_tools_present_skips_unavailable_tool(monkeypatch):
    """MCP tools count as web availability (metaso is the enabled search MCP)."""

    def _no_servers():
        return SimpleNamespace(get_enabled_mcp_servers=lambda: [{"name": "metaso"}])

    monkeypatch.setattr("deerflow.config.extensions_config.ExtensionsConfig.from_file", _no_servers)
    monkeypatch.setattr("deerflow.mcp.cache.get_cached_mcp_tools", lambda: [_FakeMCPTool()])

    tools = get_available_tools(groups=["web"], include_mcp=True, app_config=_make_config())
    assert "web_search_unavailable" not in [t.name for t in tools]


class _FakeMCPTool:
    name = "metaso_web_search"

    def invoke(self, *a, **k):
        return "results"


def test_web_not_requested_skips_check(no_mcp):
    """Agents without the web group never get the fallback tool."""
    tools = get_available_tools(groups=["bash"], include_mcp=True, app_config=_make_config())
    assert "web_search_unavailable" not in [t.name for t in tools]
