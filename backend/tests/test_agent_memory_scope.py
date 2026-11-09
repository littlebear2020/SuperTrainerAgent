"""Tests for per-agent memory opt-out via memory_scope: none (D-5).

co-writer runs are one-shot full-draft edit sessions (up to 20k chars): the
MemoryMiddleware fired an extraction LLM call after every run, and the merged
global+per-agent memory was injected into subsequent runs — pure pollution.
memory_scope: none skips BOTH the extraction middleware and the injection.
"""

from __future__ import annotations

from types import SimpleNamespace

import yaml

from deerflow.agents.lead_agent import agent as lead_agent_module
from deerflow.agents.lead_agent import prompt as prompt_module
from deerflow.config.app_config import AppConfig
from deerflow.config.model_config import ModelConfig
from deerflow.config.sandbox_config import SandboxConfig


def _make_app_config(models: list[ModelConfig]) -> AppConfig:
    return AppConfig(
        models=models,
        sandbox=SandboxConfig(use="deerflow.sandbox.local:LocalSandboxProvider"),
    )


def _make_model(name: str) -> ModelConfig:
    return ModelConfig(
        name=name,
        display_name=name,
        description=None,
        use="langchain_openai:ChatOpenAI",
        model=name,
        supports_thinking=False,
        supports_vision=False,
    )


def _stub_heavy_middlewares(monkeypatch):
    """Same stubs as test_lead_agent_model_resolution.py — summarization/todo
    constructors spin up LLM clients that need API keys."""
    monkeypatch.setattr(lead_agent_module, "_create_summarization_middleware", lambda **kwargs: None)
    monkeypatch.setattr(lead_agent_module, "_create_todo_list_middleware", lambda is_plan_mode: None)


def test_build_middlewares_skips_memory_middleware_when_scope_none(monkeypatch):
    app_config = _make_app_config([_make_model("m")])
    monkeypatch.setattr(lead_agent_module, "get_app_config", lambda: app_config)
    _stub_heavy_middlewares(monkeypatch)

    middlewares = lead_agent_module._build_middlewares(
        {"configurable": {}},
        model_name="m",
        app_config=app_config,
        memory_scope="none",
    )

    assert not any(isinstance(m, lead_agent_module.MemoryMiddleware) for m in middlewares)


def test_build_middlewares_includes_memory_middleware_by_default(monkeypatch):
    app_config = _make_app_config([_make_model("m")])
    monkeypatch.setattr(lead_agent_module, "get_app_config", lambda: app_config)
    _stub_heavy_middlewares(monkeypatch)

    middlewares = lead_agent_module._build_middlewares(
        {"configurable": {}},
        model_name="m",
        app_config=app_config,
    )

    assert any(isinstance(m, lead_agent_module.MemoryMiddleware) for m in middlewares)


def _patch_memory_available(monkeypatch):
    """Make memory data loadable so the scope gate is the ONLY thing that can
    produce an empty context."""
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
    monkeypatch.setattr(memory_module, "format_memory_for_injection", lambda data, max_tokens: "FACTS")
    monkeypatch.setattr(user_context_module, "get_effective_user_id", lambda: "u1")


def test_get_memory_context_returns_empty_when_scope_none(monkeypatch):
    _patch_memory_available(monkeypatch)
    assert prompt_module._get_memory_context("co-writer", memory_scope="none") == ""


def test_get_memory_context_loads_when_scope_not_none(monkeypatch):
    _patch_memory_available(monkeypatch)
    assert "<memory>" in prompt_module._get_memory_context("co-writer")


def test_apply_prompt_template_omits_memory_section_when_scope_none(monkeypatch):
    """D-3: the system prompt is static — memory never appears in it for ANY
    scope. The scope gate itself moved to DynamicContextMiddleware (verified in
    test_dynamic_context_middleware.py)."""
    _patch_memory_available(monkeypatch)
    # apply_prompt_template no longer takes memory_scope (D-3).
    out = prompt_module.apply_prompt_template(agent_name="co-writer")
    # The injected memory block is `<memory>\n{content}\n</memory>` — assert on
    # the block form, not the bare tag: the System-Context Confidentiality
    # section (2026-08-13 T2) mentions the tag name as prose.
    assert "<memory>\n" not in out


def test_co_writer_seed_config_disables_memory():
    from pathlib import Path

    cfg = yaml.safe_load((Path(__file__).parent.parent / "seed" / "agents" / "co-writer" / "config.yaml").read_text())
    assert cfg["memory_scope"] == "none"


# ---------------------------------------------------------------------------
# D-3 B9/B10: direct tests for _get_memory_context's config gate and the
# per-agent merge routing (local customizations that D-3 must preserve).
# ---------------------------------------------------------------------------


def test_get_memory_context_returns_empty_when_memory_disabled(monkeypatch):
    """B9: enabled=False short-circuits before any memory data is loaded."""
    import deerflow.agents.memory as memory_module
    import deerflow.config.memory_config as memory_config_module

    monkeypatch.setattr(
        memory_config_module,
        "get_memory_config",
        lambda: SimpleNamespace(enabled=False, injection_enabled=True, max_injection_tokens=2000),
    )
    calls = []
    monkeypatch.setattr(memory_module, "get_memory_data", lambda *a, **k: calls.append((a, k)) or object())

    assert prompt_module._get_memory_context("co-writer") == ""
    assert calls == []


def test_get_memory_context_returns_empty_when_injection_disabled(monkeypatch):
    """B9: injection_enabled=False also short-circuits before any data load."""
    import deerflow.agents.memory as memory_module
    import deerflow.config.memory_config as memory_config_module

    monkeypatch.setattr(
        memory_config_module,
        "get_memory_config",
        lambda: SimpleNamespace(enabled=True, injection_enabled=False, max_injection_tokens=2000),
    )
    calls = []
    monkeypatch.setattr(memory_module, "get_memory_data", lambda *a, **k: calls.append((a, k)) or object())

    assert prompt_module._get_memory_context("co-writer") == ""
    assert calls == []


def test_get_memory_context_merges_per_agent_memory_global_first(monkeypatch):
    """B10: agent_name routes to per-agent memory and merges with global —
    global is passed first (the front-end panel wins; merge priority itself is
    implemented and tested inside merge_memories)."""
    import deerflow.agents.memory as memory_module

    _patch_memory_available(monkeypatch)
    loaded = []
    monkeypatch.setattr(memory_module, "get_memory_data", lambda name, *, user_id: loaded.append(name) or f"mem:{name}")
    merged = []
    monkeypatch.setattr(memory_module, "merge_memories", lambda g, a: merged.append((g, a)) or g)

    out = prompt_module._get_memory_context("co-writer")

    assert loaded == [None, "co-writer"]
    assert merged == [("mem:None", "mem:co-writer")]
    assert "<memory>" in out


def test_get_memory_context_skips_per_agent_merge_without_agent_name(monkeypatch):
    """B10: agent_name=None loads only global memory and never merges."""
    import deerflow.agents.memory as memory_module

    _patch_memory_available(monkeypatch)
    loaded = []
    monkeypatch.setattr(memory_module, "get_memory_data", lambda name, *, user_id: loaded.append(name) or object())
    merge_calls = []
    monkeypatch.setattr(memory_module, "merge_memories", lambda g, a: merge_calls.append(1) or g)

    out = prompt_module._get_memory_context(None)

    assert loaded == [None]
    assert merge_calls == []
    assert "<memory>" in out
