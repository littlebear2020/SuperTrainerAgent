"""Memory merge utility for combining global and per-agent memory layers.

Design principle:
- Global memory = user profile (managed by frontend, highest priority)
- Per-agent memory = specialist workspace (accumulated by each agent)

Merge rules:
- User context sections (workContext, personalContext, topOfMind): global wins if non-empty
- History sections: global wins if non-empty
- Facts: merge both lists, deduplicate by content (casefold), global wins on conflict
"""

import copy
import logging
from typing import Any

logger = logging.getLogger(__name__)


def _fact_content_key(content: Any) -> str | None:
    """Normalize fact content for deduplication comparison."""
    if not isinstance(content, str):
        return None
    stripped = content.strip()
    if not stripped:
        return None
    return stripped.casefold()


def _section_has_summary(section_data: dict[str, Any] | None) -> bool:
    """Check if a memory section has meaningful content."""
    if not section_data or not isinstance(section_data, dict):
        return False
    summary = section_data.get("summary", "")
    return bool(summary and summary.strip())


def _merge_user_sections(
    global_user: dict[str, Any],
    agent_user: dict[str, Any],
) -> dict[str, Any]:
    """Merge user context sections. Global wins for non-empty fields."""
    merged = {}

    for key in ("workContext", "personalContext", "topOfMind"):
        g = global_user.get(key, {})
        a = agent_user.get(key, {})

        if _section_has_summary(g):
            merged[key] = copy.deepcopy(g)
        elif _section_has_summary(a):
            merged[key] = copy.deepcopy(a)
        else:
            merged[key] = {"summary": "", "updatedAt": ""}

    return merged


def _merge_history_sections(
    global_history: dict[str, Any],
    agent_history: dict[str, Any],
) -> dict[str, Any]:
    """Merge history sections. Global wins for non-empty fields."""
    merged = {}

    for key in ("recentMonths", "earlierContext", "longTermBackground"):
        g = global_history.get(key, {})
        a = agent_history.get(key, {})

        if _section_has_summary(g):
            merged[key] = copy.deepcopy(g)
        elif _section_has_summary(a):
            merged[key] = copy.deepcopy(a)
        else:
            merged[key] = {"summary": "", "updatedAt": ""}

    return merged


def _merge_facts(
    global_facts: list[dict[str, Any]],
    agent_facts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge fact lists with deduplication. Global facts have higher priority.

    Deduplication is by content (casefold comparison).
    On conflict (same content, different metadata), the global version wins.
    """
    merged: list[dict[str, Any]] = []
    seen_keys: dict[str, str] = {}  # content_key -> source ("global" | "agent")

    # Add global facts first (higher priority)
    for fact in global_facts:
        if not isinstance(fact, dict):
            continue
        key = _fact_content_key(fact.get("content"))
        if key is None:
            continue
        if key not in seen_keys:
            seen_keys[key] = "global"
            merged.append(copy.deepcopy(fact))

    # Add agent facts that don't conflict with global
    for fact in agent_facts:
        if not isinstance(fact, dict):
            continue
        key = _fact_content_key(fact.get("content"))
        if key is None:
            continue
        if key not in seen_keys:
            seen_keys[key] = "agent"
            merged.append(copy.deepcopy(fact))

    return merged


def merge_memories(
    global_memory: dict[str, Any],
    per_agent_memory: dict[str, Any],
) -> dict[str, Any]:
    """Merge global and per-agent memory with global having higher priority.

    Args:
        global_memory: Global user memory (from users/{uid}/memory.json).
        per_agent_memory: Per-agent memory (from users/{uid}/agents/{name}/memory.json).

    Returns:
        Merged memory dictionary with global priority.
    """
    if not global_memory and not per_agent_memory:
        return {}

    if not global_memory:
        return copy.deepcopy(per_agent_memory)

    if not per_agent_memory:
        return copy.deepcopy(global_memory)

    merged: dict[str, Any] = {
        "version": global_memory.get("version", "1.0"),
        "lastUpdated": global_memory.get("lastUpdated", ""),
    }

    # Merge user context — global wins for non-empty
    merged["user"] = _merge_user_sections(
        global_memory.get("user", {}),
        per_agent_memory.get("user", {}),
    )

    # Merge history — global wins for non-empty
    merged["history"] = _merge_history_sections(
        global_memory.get("history", {}),
        per_agent_memory.get("history", {}),
    )

    # Merge facts — deduplicate, global priority
    merged["facts"] = _merge_facts(
        global_memory.get("facts", []),
        per_agent_memory.get("facts", []),
    )

    return merged
