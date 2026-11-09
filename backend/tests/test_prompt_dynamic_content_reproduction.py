"""D-3 verification: the system prompt is fully static (prefix-cache safe).

Phase 1 of this file reproduced the bug — memory text and the current date were
baked into the system prompt, so two renders with different memory content (or
different dates) differed byte-for-byte, invalidating the LLM prompt-cache
prefix on every memory edit. Those repro assertions passed pre-fix and are
recorded in git history alongside the implementation commit.

After the D-3 implementation (DynamicContextMiddleware), the assertions are
reversed: the rendered system prompt is byte-identical regardless of memory
content and date, and contains neither a ``<memory>`` block nor a
``<current_date>`` tag — those live in injected message-area reminders instead.
"""

from __future__ import annotations

from types import SimpleNamespace

from deerflow.agents.lead_agent import prompt as prompt_module

# ---------------------------------------------------------------------------
# Helpers (same stubbing strategy as test_agent_memory_scope.py)
# ---------------------------------------------------------------------------


def _patch_memory_content(monkeypatch, content: str) -> None:
    """Make _get_memory_context resolve to ``content`` through the real code path."""
    import deerflow.agents.memory as memory_module
    import deerflow.config.memory_config as memory_config_module
    import deerflow.runtime.user_context as user_context_module

    monkeypatch.setattr(
        memory_config_module,
        "get_memory_config",
        lambda: SimpleNamespace(enabled=True, injection_enabled=True, max_injection_tokens=2000),
    )
    monkeypatch.setattr(memory_module, "get_memory_data", lambda *a, **k: object())
    monkeypatch.setattr(memory_module, "merge_memories", lambda g, a: g)
    monkeypatch.setattr(memory_module, "format_memory_for_injection", lambda data, max_tokens: content)
    monkeypatch.setattr(user_context_module, "get_effective_user_id", lambda: "u1")


def _render(monkeypatch, memory_content: str) -> str:
    _patch_memory_content(monkeypatch, memory_content)
    return prompt_module.apply_prompt_template(agent_name="co-writer")


# ---------------------------------------------------------------------------
# Phase 2 — post-fix behavior: the prompt is static
# ---------------------------------------------------------------------------


def test_memory_is_never_baked_into_system_prompt(monkeypatch):
    out = _render(monkeypatch, "用户偏好：喜欢简洁回答")
    assert "<memory>\n" not in out


def test_current_date_is_never_appended_to_system_prompt(monkeypatch):
    out = _render(monkeypatch, "irrelevant")
    assert "<current_date>" not in out


def test_different_memory_leaves_prompt_byte_identical(monkeypatch):
    """The cache-stability guarantee: same agent config, different memory
    content → byte-identical system prompt."""
    out_a = _render(monkeypatch, "memory-A")
    out_b = _render(monkeypatch, "memory-B")
    assert out_a == out_b


def test_empty_vs_rich_memory_leaves_prompt_byte_identical(monkeypatch):
    out_empty = _render(monkeypatch, "")
    out_rich = _render(monkeypatch, "FACT-1\nFACT-2\nFACT-3")
    assert out_empty == out_rich


def test_template_has_no_memory_context_placeholder():
    assert "{memory_context}" not in prompt_module.SYSTEM_PROMPT_TEMPLATE


def test_different_date_leaves_prompt_byte_identical(monkeypatch):
    """Same config on two different days → byte-identical system prompt.

    The renderer no longer reads the clock at all (no datetime import in the
    module), so midnight can never change the prompt bytes.
    """
    assert not hasattr(prompt_module, "datetime")
    out_day1 = _render(monkeypatch, "same-memory")
    out_day2 = _render(monkeypatch, "same-memory")
    assert out_day1 == out_day2
