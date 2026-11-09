"""Structural neutralization of framework control tokens in untrusted text.

Content the agent *fetches* (web page bodies, search snippets) is attacker-
influenceable, yet it enters the model context verbatim. A page under an
attacker's control can embed a forged ``<system_reminder>`` block and have it
reach the model looking like authoritative framework context.

This module holds the shared primitive. It deliberately does only two things:

* framework/injection block tags (e.g. ``<system_reminder>``) are HTML-escaped
  to ``&lt;system_reminder&gt;``, losing structural meaning while staying
  human-readable — de-identify rather than reject, so the content the user
  actually wanted is still there to read;
* the plain-text ``--- BEGIN/END UNTRUSTED CONTENT ---`` boundary markers are
  rewritten to look-alike inert forms, so fetched content cannot forge or break
  out of a boundary the framework emits.

Ported from upstream ``input_sanitization_middleware.py``. Upstream couples this
primitive to a user-message guardrail that also *wraps* input in boundary
markers; that half is intentionally NOT ported here. It depends on
``ORIGINAL_USER_CONTENT_KEY`` / ``message_content_to_text`` /
``read_human_input_response``, none of which exist locally, and without the
``ORIGINAL_USER_CONTENT_KEY`` marker it cannot tell our server-injected
``<uploaded_files>`` block (prepended by ``UploadsMiddleware``) from user text —
so it would escape our own trusted block. See
``tool_result_sanitization_middleware`` for the consumer that is ported.
"""

from __future__ import annotations

import re

# Framework block tags that carry authority in model input. Forged in fetched
# content, any one of them mimics trusted framework context.
#
# This list is LOCAL reality, not a copy of upstream's: it was enumerated from
# the closing tags this repo actually emits (``grep -rhoE "</[a-z_-]+>"`` over
# packages/harness/deerflow). Notably local emits ``current_memory``,
# ``global_user_context`` and ``subagent_muscle_memory`` (absent upstream), and
# uses ``uploaded_files`` where upstream moved to ``current_uploads``.
#
# Maintenance: adding a framework block tag means adding it here too. Pinned
# against drift by test_untrusted_content.py::test_denylist_covers_emitted_block_tags,
# which re-derives the emitted set from source and fails on an uncovered tag.
_BLOCKED_TAG_NAMES: frozenset[str] = frozenset(
    {
        # Framework-injected structured/authority blocks
        "system-reminder",
        "system_reminder",
        "memory",
        "current_memory",
        "global_user_context",
        "current_date",
        "think",
        "thinking_style",
        "analysis",
        "role",
        "soul",
        "self_update",
        "clarification_system",
        "critical_reminders",
        "response_style",
        "citations",
        "uploaded_files",
        "current_uploads",
        "subagent_system",
        "subagent_muscle_memory",
        "skill_system",
        "skill_index",
        "available_skills",
        # The <skill> container specifically: the skill_system prompt tells the
        # model to "call read_file on the skill's main file using the path
        # attribute provided in the skill tag below" (lead_agent/prompt.py:605),
        # and <skill> is what wraps that <location> (:597). Forged in fetched
        # content it turns into an arbitrary-file-read primitive, so the
        # container must be neutralized even though its inner <name> /
        # <description> / <location> are inert on their own.
        "skill",
        "disabled_skills",
        "memory_tool_system",
        "todo_list_system",
        "durable_context_data",
        "slash_skill_activation",
        "mcp_routing_hints",
        "available-deferred-tools",
        "goal_continuation",
        "file_editing_workflow",
        "guidelines",
        "output_format",
        "working_directory",
        "tool_restrictions",
        "conversation",
        # 2026-09-22: DeerFlow default summary prompt scaffolding
        # (summarization_middleware.DEERFLOW_DEFAULT_SUMMARY_PROMPT) and the
        # WorkspaceInventoryMiddleware <workspace_files> block. Forged in
        # fetched content, <workspace_files> fakes a system artifact inventory
        # (same authority class as denylisted <uploaded_files>) and the summary
        # meta-tags fake extraction-prompt authority.
        "workspace_files",
        "instructions",
        "messages",
        "objective_information",
        "primary_objective",
        # F3（2026-09-29）: bash 工具资源类失败回执尾部追加的指引块——伪造它可
        # 诱导模型按假"资源不足"信号改道（如放弃重试/降级产出），属携带指令
        # 权威的框架标签，入 denylist。
        "resource_failure_guidance",
        # Tool-protocol tags: forging these can fabricate a tool call or result.
        "tool_call",
        "tool_response",
        # Common prompt-injection tag patterns
        "system",
        "instruction",
        "important",
        "override",
        "ignore",
        "prompt",
    }
)

# Matches a full blocked tag: <tag>, </tag>, <tag attrs>, <tag/>, bare <tag
_BLOCKED_TAG_PATTERN = re.compile(
    r"<\s*/?\s*(?:" + "|".join(re.escape(t) for t in sorted(_BLOCKED_TAG_NAMES)) + r")\b[^>]*>?",
    re.IGNORECASE,
)

# Plain-text boundary markers (OWASP structured-prompt guidance).
UNTRUSTED_CONTENT_BEGIN = "--- BEGIN UNTRUSTED CONTENT ---"
UNTRUSTED_CONTENT_END = "--- END UNTRUSTED CONTENT ---"

# Look-alike inert forms, injected when fetched text already contains a marker.
_NEUTRALIZED_BEGIN = "[BEGIN UNTRUSTED CONTENT]"
_NEUTRALIZED_END = "[END UNTRUSTED CONTENT]"

_BOUNDARY_TOKEN_RE = re.compile(
    re.escape(UNTRUSTED_CONTENT_BEGIN) + r"|" + re.escape(UNTRUSTED_CONTENT_END),
)


def _escape_tag_match(match: re.Match) -> str:
    """Escape < and > in a blocked-tag match so it renders as literal text."""
    return match.group(0).replace("<", "&lt;").replace(">", "&gt;")


def _neutralize_boundary_tokens(text: str) -> str:
    """Replace real boundary markers with look-alike inert forms."""
    return _BOUNDARY_TOKEN_RE.sub(
        lambda m: _NEUTRALIZED_BEGIN if m.group(0) == UNTRUSTED_CONTENT_BEGIN else _NEUTRALIZED_END,
        text,
    )


def neutralize_untrusted_tags(text: str) -> str:
    """Neutralize framework/injection control tokens in untrusted *text*.

    Escapes blocked framework/injection tags and neutralizes boundary markers.
    Does not wrap the text in markers — that framing belongs to the caller.
    Empty/whitespace-only text is returned unchanged so callers emit no noise.
    """
    if not text.strip():
        return text
    text = _BLOCKED_TAG_PATTERN.sub(_escape_tag_match, text)
    return _neutralize_boundary_tokens(text)
