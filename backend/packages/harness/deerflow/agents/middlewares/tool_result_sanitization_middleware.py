"""Neutralize prompt-injection control tokens in untrusted tool results.

Remote content the agent *fetches* is attacker-influenceable: a page can embed a
forged ``<system_reminder>`` block and have it reach the model as authoritative
framework context. This middleware applies the same structural neutralization
(:func:`neutralize_untrusted_tags`) to those results.

**Scope is decided by tool _source_, not tool name.** Upstream gates on a
hardcoded name allowlist (``{web_fetch, web_search, image_search,
web_capture}``); this repo registers none of those four, so porting that gate
verbatim would compile, pass an import smoke-test, and neutralize exactly
nothing. Our actual remote-content tools come from MCP servers and are named
``{server}_{tool}`` (``mcp/tools.py`` uses ``tool_name_prefix=True``) — e.g.
``metaso_metaso_web_search`` / ``metaso_metaso_web_reader``. Matching those by
name would reintroduce the same silent-failure mode one rename later, so MCP
tools are tagged at registration with ``untrusted_remote_content: True`` in
their metadata (see :func:`mark_tool_as_untrusted_remote_content`) and this
middleware reads that tag.

Deliberately out of scope:

* **bash output.** Local command output is trusted-by-origin and full of text
  that looks like blocked tags — ffmpeg logs, Python tracebacks, and any source
  file the agent reads. Escaping those would corrupt what the model reads back.
* **local file reads.** Same reason.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import replace as dc_replace
from typing import Any, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

logger = logging.getLogger(__name__)

#: Metadata key marking a tool whose results are attacker-influenceable.
UNTRUSTED_REMOTE_CONTENT_KEY = "untrusted_remote_content"


def mark_tool_as_untrusted_remote_content(tool: Any) -> Any:
    """Tag *tool* so its results get neutralized, and return it.

    Called at MCP registration time. Mutates ``tool.metadata`` in place when it
    is a dict (creating it when absent), so the tag survives the
    ``StructuredTool(metadata=tool.metadata)`` hand-off in
    ``_make_session_pool_tool``. Never raises: a tool object that rejects the
    assignment is returned untagged rather than breaking tool loading.
    """
    try:
        metadata = getattr(tool, "metadata", None)
        if isinstance(metadata, dict):
            metadata[UNTRUSTED_REMOTE_CONTENT_KEY] = True
        else:
            tool.metadata = {UNTRUSTED_REMOTE_CONTENT_KEY: True}
    except Exception:  # pragma: no cover - defensive
        logger.warning("Could not tag tool %r as untrusted remote content", getattr(tool, "name", "?"), exc_info=True)
    return tool


def is_untrusted_remote_content_tool(tool: Any) -> bool:
    """Return True when *tool* carries the untrusted-remote-content tag."""
    metadata = getattr(tool, "metadata", None)
    return bool(isinstance(metadata, dict) and metadata.get(UNTRUSTED_REMOTE_CONTENT_KEY))


def _neutralize_content(content: object) -> object:
    """Return *content* with untrusted tags neutralized, preserving its shape.

    Handles both shapes a ToolMessage content can take. MCP results in
    particular arrive as a *list* of content blocks rather than a plain string
    (``_convert_call_tool_result``), so the list branch is the one that actually
    fires for our remote tools — bare ``str`` elements and
    ``{"type": "text", "text": ...}`` blocks are rewritten; non-text blocks
    (images, etc.) pass through untouched.
    """
    from deerflow.agents.middlewares.untrusted_content import neutralize_untrusted_tags

    if isinstance(content, str):
        return neutralize_untrusted_tags(content)
    if isinstance(content, list):
        rebuilt: list[object] = []
        for block in content:
            if isinstance(block, str):
                rebuilt.append(neutralize_untrusted_tags(block))
            elif isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str):
                rebuilt.append({**block, "text": neutralize_untrusted_tags(block["text"])})
            else:
                rebuilt.append(block)
        return rebuilt
    return content


def _sanitize_tool_message(message: ToolMessage) -> ToolMessage:
    """Return a copy of *message* with its content neutralized, or the original."""
    new_content = _neutralize_content(message.content)
    if new_content == message.content:
        return message
    return message.model_copy(update={"content": new_content})


def _sanitize_result(result: ToolMessage | Command) -> ToolMessage | Command:
    """Neutralize a tool-call result (``ToolMessage`` or ``Command``)."""
    if isinstance(result, ToolMessage):
        return _sanitize_tool_message(result)
    update = getattr(result, "update", None)
    if isinstance(update, dict):
        messages = update.get("messages")
        if isinstance(messages, list) and any(isinstance(m, ToolMessage) for m in messages):
            new_messages = [_sanitize_tool_message(m) if isinstance(m, ToolMessage) else m for m in messages]
            if new_messages != messages:
                return dc_replace(result, update={**update, "messages": new_messages})
    return result


class ToolResultSanitizationMiddleware(AgentMiddleware[AgentState]):
    """Escape injection/framework tags in untrusted tool results.

    Gating is by tool *source*: results of tools tagged
    ``untrusted_remote_content`` (MCP-provided, tagged at registration) are
    rewritten; every other tool's output — bash, file reads, local builtins —
    passes through untouched.
    """

    def _should_sanitize(self, request: ToolCallRequest) -> bool:
        """Return True when this call's tool is tagged untrusted remote content.

        ``request.tool`` is the live ``BaseTool`` (``None`` for an unregistered
        tool), so the registration-time tag is readable here without a name lookup.
        """
        return is_untrusted_remote_content_tool(request.tool)

    def _apply(self, request: ToolCallRequest, result: ToolMessage | Command) -> ToolMessage | Command:
        """Sanitize *result* when in scope; fail-open on unexpected errors."""
        try:
            if not self._should_sanitize(request):
                return result
            sanitized = _sanitize_result(result)
            if sanitized is not result:
                logger.debug(
                    "ToolResultSanitizationMiddleware neutralized result of %r",
                    request.tool_call.get("name"),
                )
            return sanitized
        except Exception:
            logger.warning(
                "Tool-result sanitization failed; passing original result through",
                exc_info=True,
            )
            return result

    @override
    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        return self._apply(request, handler(request))

    @override
    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        return self._apply(request, await handler(request))
