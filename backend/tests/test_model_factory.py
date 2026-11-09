"""Tests for deerflow.models.factory.create_chat_model."""

from __future__ import annotations

from pathlib import Path

import pytest
from langchain.chat_models import BaseChatModel

from deerflow.config.app_config import AppConfig
from deerflow.config.model_config import ModelConfig
from deerflow.config.sandbox_config import SandboxConfig
from deerflow.models import factory as factory_module
from deerflow.models import openai_codex_provider as codex_provider_module

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_app_config(models: list[ModelConfig]) -> AppConfig:
    return AppConfig(
        models=models,
        sandbox=SandboxConfig(use="deerflow.sandbox.local:LocalSandboxProvider"),
    )


def _make_model(
    name: str = "test-model",
    *,
    use: str = "langchain_openai:ChatOpenAI",
    supports_thinking: bool = False,
    supports_reasoning_effort: bool = False,
    when_thinking_enabled: dict | None = None,
    when_thinking_disabled: dict | None = None,
    thinking: dict | None = None,
    max_tokens: int | None = None,
) -> ModelConfig:
    return ModelConfig(
        name=name,
        display_name=name,
        description=None,
        use=use,
        model=name,
        max_tokens=max_tokens,
        supports_thinking=supports_thinking,
        supports_reasoning_effort=supports_reasoning_effort,
        when_thinking_enabled=when_thinking_enabled,
        when_thinking_disabled=when_thinking_disabled,
        thinking=thinking,
        supports_vision=False,
    )


class FakeChatModel(BaseChatModel):
    """Minimal BaseChatModel stub that records the kwargs it was called with."""

    captured_kwargs: dict = {}

    def __init__(self, **kwargs):
        # Store kwargs before pydantic processes them
        FakeChatModel.captured_kwargs = dict(kwargs)
        super().__init__(**kwargs)

    @property
    def _llm_type(self) -> str:
        return "fake"

    def _generate(self, *args, **kwargs):  # type: ignore[override]
        raise NotImplementedError

    def _stream(self, *args, **kwargs):  # type: ignore[override]
        raise NotImplementedError


@pytest.fixture(autouse=True)
def _reset_captured_kwargs():
    """每个用例起点清零类级捕获态。

    2026-10-05 基建批：captured_kwargs 是类级可变属性（构造即覆盖），此前靠
    各用例手动 ``FakeChatModel.captured_kwargs = {}`` 自觉重置——漏一处即
    顺序耦合（读到上一用例的 kwargs 而非干净失败）。autouse 后手动重置全
    部移除；未构造就读取会拿到 {} 并以断言失败显式暴露。"""
    FakeChatModel.captured_kwargs = {}
    yield


def _patch_factory(monkeypatch, app_config: AppConfig, model_class=FakeChatModel):
    """Patch get_app_config, resolve_class, and tracing for isolated unit tests."""
    monkeypatch.setattr(factory_module, "get_app_config", lambda: app_config)
    monkeypatch.setattr(factory_module, "resolve_class", lambda path, base: model_class)
    monkeypatch.setattr(factory_module, "build_tracing_callbacks", lambda: [])


# ---------------------------------------------------------------------------
# Model selection
# ---------------------------------------------------------------------------


def test_uses_first_model_when_name_is_none(monkeypatch):
    cfg = _make_app_config([_make_model("alpha"), _make_model("beta")])
    _patch_factory(monkeypatch, cfg)

    factory_module.create_chat_model(name=None)

    # resolve_class is called — if we reach here without ValueError, the correct model was used
    assert FakeChatModel.captured_kwargs.get("model") == "alpha"


def test_raises_when_model_not_found(monkeypatch):
    cfg = _make_app_config([_make_model("only-model")])
    monkeypatch.setattr(factory_module, "get_app_config", lambda: cfg)
    monkeypatch.setattr(factory_module, "build_tracing_callbacks", lambda: [])

    with pytest.raises(ValueError, match="ghost-model"):
        factory_module.create_chat_model(name="ghost-model")


def test_appends_all_tracing_callbacks(monkeypatch):
    cfg = _make_app_config([_make_model("alpha")])
    _patch_factory(monkeypatch, cfg)
    monkeypatch.setattr(factory_module, "build_tracing_callbacks", lambda: ["smith-callback", "langfuse-callback"])

    model = factory_module.create_chat_model(name="alpha")

    assert model.callbacks == ["smith-callback", "langfuse-callback"]


# ---------------------------------------------------------------------------
# thinking_enabled=True
# ---------------------------------------------------------------------------


def test_thinking_enabled_raises_when_not_supported_but_when_thinking_enabled_is_set(monkeypatch):
    """supports_thinking guard fires only when when_thinking_enabled is configured —
    the factory uses that as the signal that the caller explicitly expects thinking to work."""
    wte = {"thinking": {"type": "enabled", "budget_tokens": 5000}}
    cfg = _make_app_config([_make_model("no-think", supports_thinking=False, when_thinking_enabled=wte)])
    _patch_factory(monkeypatch, cfg)

    with pytest.raises(ValueError, match="does not support thinking"):
        factory_module.create_chat_model(name="no-think", thinking_enabled=True)


def test_thinking_enabled_raises_for_empty_when_thinking_enabled_explicitly_set(monkeypatch):
    """supports_thinking guard fires when when_thinking_enabled is set to an empty dict —
    the user explicitly provided the section, so the guard must still fire even though
    effective_wte would be falsy."""
    cfg = _make_app_config([_make_model("no-think-empty", supports_thinking=False, when_thinking_enabled={})])
    _patch_factory(monkeypatch, cfg)

    with pytest.raises(ValueError, match="does not support thinking"):
        factory_module.create_chat_model(name="no-think-empty", thinking_enabled=True)


def test_thinking_enabled_merges_when_thinking_enabled_settings(monkeypatch):
    wte = {"temperature": 1.0, "max_tokens": 16000}
    cfg = _make_app_config([_make_model("thinker", supports_thinking=True, when_thinking_enabled=wte)])
    _patch_factory(monkeypatch, cfg)

    factory_module.create_chat_model(name="thinker", thinking_enabled=True)

    assert FakeChatModel.captured_kwargs.get("temperature") == 1.0
    assert FakeChatModel.captured_kwargs.get("max_tokens") == 16000


# ---------------------------------------------------------------------------
# thinking_enabled=False — disable logic
# ---------------------------------------------------------------------------


def test_thinking_disabled_openai_gateway_format(monkeypatch):
    """When thinking is configured via extra_body (OpenAI-compatible gateway),
    disabling must inject extra_body.thinking.type=disabled and reasoning_effort=minimal."""
    wte = {"extra_body": {"thinking": {"type": "enabled", "budget_tokens": 10000}}}
    cfg = _make_app_config(
        [
            _make_model(
                "openai-gw",
                supports_thinking=True,
                supports_reasoning_effort=True,
                when_thinking_enabled=wte,
            )
        ]
    )
    _patch_factory(monkeypatch, cfg)

    captured: dict = {}

    class CapturingModel(FakeChatModel):
        def __init__(self, **kwargs):
            captured.update(kwargs)
            BaseChatModel.__init__(self, **kwargs)

    monkeypatch.setattr(factory_module, "resolve_class", lambda path, base: CapturingModel)

    factory_module.create_chat_model(name="openai-gw", thinking_enabled=False)

    assert captured.get("extra_body") == {"thinking": {"type": "disabled"}}
    assert captured.get("reasoning_effort") == "minimal"
    assert "thinking" not in captured  # must NOT set the direct thinking param


def test_thinking_disabled_langchain_anthropic_format(monkeypatch):
    """When thinking is configured as a direct param (langchain_anthropic),
    disabling must inject thinking.type=disabled WITHOUT touching extra_body or reasoning_effort."""
    wte = {"thinking": {"type": "enabled", "budget_tokens": 8000}}
    cfg = _make_app_config(
        [
            _make_model(
                "anthropic-native",
                use="langchain_anthropic:ChatAnthropic",
                supports_thinking=True,
                supports_reasoning_effort=False,
                when_thinking_enabled=wte,
            )
        ]
    )
    _patch_factory(monkeypatch, cfg)

    captured: dict = {}

    class CapturingModel(FakeChatModel):
        def __init__(self, **kwargs):
            captured.update(kwargs)
            BaseChatModel.__init__(self, **kwargs)

    monkeypatch.setattr(factory_module, "resolve_class", lambda path, base: CapturingModel)

    factory_module.create_chat_model(name="anthropic-native", thinking_enabled=False)

    assert captured.get("thinking") == {"type": "disabled"}
    assert "extra_body" not in captured
    # reasoning_effort must be cleared (supports_reasoning_effort=False)
    assert captured.get("reasoning_effort") is None


def test_thinking_disabled_no_when_thinking_enabled_does_nothing(monkeypatch):
    """If when_thinking_enabled is not set, disabling thinking must not inject any kwargs."""
    cfg = _make_app_config([_make_model("plain", supports_thinking=True, when_thinking_enabled=None)])
    _patch_factory(monkeypatch, cfg)

    captured: dict = {}

    class CapturingModel(FakeChatModel):
        def __init__(self, **kwargs):
            captured.update(kwargs)
            BaseChatModel.__init__(self, **kwargs)

    monkeypatch.setattr(factory_module, "resolve_class", lambda path, base: CapturingModel)

    factory_module.create_chat_model(name="plain", thinking_enabled=False)

    assert "extra_body" not in captured
    assert "thinking" not in captured
    # reasoning_effort not forced (supports_reasoning_effort defaults to False → cleared)
    assert captured.get("reasoning_effort") is None


# ---------------------------------------------------------------------------
# when_thinking_disabled config
# ---------------------------------------------------------------------------


def test_when_thinking_disabled_takes_precedence_over_hardcoded_disable(monkeypatch):
    """When when_thinking_disabled is set, it takes full precedence over the
    hardcoded disable logic (extra_body.thinking.type=disabled etc.)."""
    wte = {"extra_body": {"thinking": {"type": "enabled", "budget_tokens": 10000}}}
    wtd = {"extra_body": {"thinking": {"type": "disabled"}}, "reasoning_effort": "low"}
    cfg = _make_app_config(
        [
            _make_model(
                "custom-disable",
                supports_thinking=True,
                supports_reasoning_effort=True,
                when_thinking_enabled=wte,
                when_thinking_disabled=wtd,
            )
        ]
    )
    _patch_factory(monkeypatch, cfg)

    captured: dict = {}

    class CapturingModel(FakeChatModel):
        def __init__(self, **kwargs):
            captured.update(kwargs)
            BaseChatModel.__init__(self, **kwargs)

    monkeypatch.setattr(factory_module, "resolve_class", lambda path, base: CapturingModel)

    factory_module.create_chat_model(name="custom-disable", thinking_enabled=False)

    assert captured.get("extra_body") == {"thinking": {"type": "disabled"}}
    # User overrode the hardcoded "minimal" with "low"
    assert captured.get("reasoning_effort") == "low"


def test_when_thinking_disabled_not_used_when_thinking_enabled(monkeypatch):
    """when_thinking_disabled must have no effect when thinking_enabled=True."""
    wte = {"extra_body": {"thinking": {"type": "enabled"}}}
    wtd = {"extra_body": {"thinking": {"type": "disabled"}}}
    cfg = _make_app_config(
        [
            _make_model(
                "wtd-ignored",
                supports_thinking=True,
                when_thinking_enabled=wte,
                when_thinking_disabled=wtd,
            )
        ]
    )
    _patch_factory(monkeypatch, cfg)

    captured: dict = {}

    class CapturingModel(FakeChatModel):
        def __init__(self, **kwargs):
            captured.update(kwargs)
            BaseChatModel.__init__(self, **kwargs)

    monkeypatch.setattr(factory_module, "resolve_class", lambda path, base: CapturingModel)

    factory_module.create_chat_model(name="wtd-ignored", thinking_enabled=True)

    # when_thinking_enabled should apply, NOT when_thinking_disabled
    assert captured.get("extra_body") == {"thinking": {"type": "enabled"}}


def test_when_thinking_disabled_without_when_thinking_enabled_still_applies(monkeypatch):
    """when_thinking_disabled alone (no when_thinking_enabled) should still apply its settings."""
    cfg = _make_app_config(
        [
            _make_model(
                "wtd-only",
                supports_thinking=True,
                supports_reasoning_effort=True,
                when_thinking_disabled={"reasoning_effort": "low"},
            )
        ]
    )
    _patch_factory(monkeypatch, cfg)

    captured: dict = {}

    class CapturingModel(FakeChatModel):
        def __init__(self, **kwargs):
            captured.update(kwargs)
            BaseChatModel.__init__(self, **kwargs)

    monkeypatch.setattr(factory_module, "resolve_class", lambda path, base: CapturingModel)

    factory_module.create_chat_model(name="wtd-only", thinking_enabled=False)

    # when_thinking_disabled is now gated independently of has_thinking_settings
    assert captured.get("reasoning_effort") == "low"


def test_when_thinking_disabled_excluded_from_model_dump(monkeypatch):
    """when_thinking_disabled must not leak into the model constructor kwargs."""
    wte = {"extra_body": {"thinking": {"type": "enabled"}}}
    wtd = {"extra_body": {"thinking": {"type": "disabled"}}}
    cfg = _make_app_config(
        [
            _make_model(
                "no-leak-wtd",
                supports_thinking=True,
                when_thinking_enabled=wte,
                when_thinking_disabled=wtd,
            )
        ]
    )
    _patch_factory(monkeypatch, cfg)

    captured: dict = {}

    class CapturingModel(FakeChatModel):
        def __init__(self, **kwargs):
            captured.update(kwargs)
            BaseChatModel.__init__(self, **kwargs)

    monkeypatch.setattr(factory_module, "resolve_class", lambda path, base: CapturingModel)

    factory_module.create_chat_model(name="no-leak-wtd", thinking_enabled=True)

    # when_thinking_disabled value must NOT appear as a raw key
    assert "when_thinking_disabled" not in captured


# ---------------------------------------------------------------------------
# reasoning_effort stripping
# ---------------------------------------------------------------------------


def test_reasoning_effort_cleared_when_not_supported(monkeypatch):
    cfg = _make_app_config([_make_model("no-effort", supports_reasoning_effort=False)])
    _patch_factory(monkeypatch, cfg)

    captured: dict = {}

    class CapturingModel(FakeChatModel):
        def __init__(self, **kwargs):
            captured.update(kwargs)
            BaseChatModel.__init__(self, **kwargs)

    monkeypatch.setattr(factory_module, "resolve_class", lambda path, base: CapturingModel)

    factory_module.create_chat_model(name="no-effort", thinking_enabled=False)

    assert captured.get("reasoning_effort") is None


def test_reasoning_effort_preserved_when_supported(monkeypatch):
    wte = {"extra_body": {"thinking": {"type": "enabled", "budget_tokens": 5000}}}
    cfg = _make_app_config(
        [
            _make_model(
                "effort-model",
                supports_thinking=True,
                supports_reasoning_effort=True,
                when_thinking_enabled=wte,
            )
        ]
    )
    _patch_factory(monkeypatch, cfg)

    captured: dict = {}

    class CapturingModel(FakeChatModel):
        def __init__(self, **kwargs):
            captured.update(kwargs)
            BaseChatModel.__init__(self, **kwargs)

    monkeypatch.setattr(factory_module, "resolve_class", lambda path, base: CapturingModel)

    factory_module.create_chat_model(name="effort-model", thinking_enabled=False)

    # When supports_reasoning_effort=True, it should NOT be cleared to None
    # The disable path sets it to "minimal"; supports_reasoning_effort=True keeps it
    assert captured.get("reasoning_effort") == "minimal"


# ---------------------------------------------------------------------------
# thinking shortcut field
# ---------------------------------------------------------------------------


def test_thinking_shortcut_enables_thinking_when_thinking_enabled(monkeypatch):
    """thinking shortcut alone should act as when_thinking_enabled with a `thinking` key."""
    thinking_settings = {"type": "enabled", "budget_tokens": 8000}
    cfg = _make_app_config(
        [
            _make_model(
                "shortcut-model",
                use="langchain_anthropic:ChatAnthropic",
                supports_thinking=True,
                thinking=thinking_settings,
            )
        ]
    )
    _patch_factory(monkeypatch, cfg)

    captured: dict = {}

    class CapturingModel(FakeChatModel):
        def __init__(self, **kwargs):
            captured.update(kwargs)
            BaseChatModel.__init__(self, **kwargs)

    monkeypatch.setattr(factory_module, "resolve_class", lambda path, base: CapturingModel)

    factory_module.create_chat_model(name="shortcut-model", thinking_enabled=True)

    assert captured.get("thinking") == thinking_settings


def test_thinking_shortcut_disables_thinking_when_thinking_disabled(monkeypatch):
    """thinking shortcut should participate in the disable path (langchain_anthropic format)."""
    thinking_settings = {"type": "enabled", "budget_tokens": 8000}
    cfg = _make_app_config(
        [
            _make_model(
                "shortcut-disable",
                use="langchain_anthropic:ChatAnthropic",
                supports_thinking=True,
                supports_reasoning_effort=False,
                thinking=thinking_settings,
            )
        ]
    )
    _patch_factory(monkeypatch, cfg)

    captured: dict = {}

    class CapturingModel(FakeChatModel):
        def __init__(self, **kwargs):
            captured.update(kwargs)
            BaseChatModel.__init__(self, **kwargs)

    monkeypatch.setattr(factory_module, "resolve_class", lambda path, base: CapturingModel)

    factory_module.create_chat_model(name="shortcut-disable", thinking_enabled=False)

    assert captured.get("thinking") == {"type": "disabled"}
    assert "extra_body" not in captured


def test_thinking_shortcut_merges_with_when_thinking_enabled(monkeypatch):
    """thinking shortcut should be merged into when_thinking_enabled when both are provided."""
    thinking_settings = {"type": "enabled", "budget_tokens": 8000}
    wte = {"max_tokens": 16000}
    cfg = _make_app_config(
        [
            _make_model(
                "merge-model",
                use="langchain_anthropic:ChatAnthropic",
                supports_thinking=True,
                thinking=thinking_settings,
                when_thinking_enabled=wte,
            )
        ]
    )
    _patch_factory(monkeypatch, cfg)

    captured: dict = {}

    class CapturingModel(FakeChatModel):
        def __init__(self, **kwargs):
            captured.update(kwargs)
            BaseChatModel.__init__(self, **kwargs)

    monkeypatch.setattr(factory_module, "resolve_class", lambda path, base: CapturingModel)

    factory_module.create_chat_model(name="merge-model", thinking_enabled=True)

    # Both the thinking shortcut and when_thinking_enabled settings should be applied
    assert captured.get("thinking") == thinking_settings
    assert captured.get("max_tokens") == 16000


def test_thinking_shortcut_not_leaked_into_model_when_disabled(monkeypatch):
    """thinking shortcut must not be passed raw to the model constructor (excluded from model_dump)."""
    thinking_settings = {"type": "enabled", "budget_tokens": 8000}
    cfg = _make_app_config(
        [
            _make_model(
                "no-leak",
                use="langchain_anthropic:ChatAnthropic",
                supports_thinking=True,
                supports_reasoning_effort=False,
                thinking=thinking_settings,
            )
        ]
    )
    _patch_factory(monkeypatch, cfg)

    captured: dict = {}

    class CapturingModel(FakeChatModel):
        def __init__(self, **kwargs):
            captured.update(kwargs)
            BaseChatModel.__init__(self, **kwargs)

    monkeypatch.setattr(factory_module, "resolve_class", lambda path, base: CapturingModel)

    factory_module.create_chat_model(name="no-leak", thinking_enabled=False)

    # The disable path should have set thinking to disabled (not the raw enabled shortcut)
    assert captured.get("thinking") == {"type": "disabled"}


# ---------------------------------------------------------------------------
# OpenAI-compatible providers (Novita, etc.)
# ---------------------------------------------------------------------------


def test_openai_compatible_provider_passes_base_url(monkeypatch):
    """OpenAI-compatible providers should pass base_url through to the model."""
    model = ModelConfig(
        name="novita-deepseek-v3.2",
        display_name="Novita DeepSeek V3.2",
        description=None,
        use="langchain_openai:ChatOpenAI",
        model="deepseek/deepseek-v3.2",
        base_url="https://api.novita.ai/openai",
        api_key="test-key",
        max_tokens=4096,
        temperature=0.7,
        supports_vision=True,
        supports_thinking=False,
    )
    cfg = _make_app_config([model])
    _patch_factory(monkeypatch, cfg)

    captured: dict = {}

    class CapturingModel(FakeChatModel):
        def __init__(self, **kwargs):
            captured.update(kwargs)
            BaseChatModel.__init__(self, **kwargs)

    monkeypatch.setattr(factory_module, "resolve_class", lambda path, base: CapturingModel)

    factory_module.create_chat_model(name="novita-deepseek-v3.2")

    assert captured.get("model") == "deepseek/deepseek-v3.2"
    assert captured.get("base_url") == "https://api.novita.ai/openai"
    assert captured.get("api_key") == "test-key"
    assert captured.get("temperature") == 0.7
    assert captured.get("max_tokens") == 4096
    assert captured.get("stream_usage") is True


def test_openai_compatible_provider_respects_explicit_stream_usage(monkeypatch):
    """Explicit stream_usage should not be overwritten by the factory default."""
    model = ModelConfig(
        name="novita-deepseek-v3.2",
        display_name="Novita DeepSeek V3.2",
        description=None,
        use="langchain_openai:ChatOpenAI",
        model="deepseek/deepseek-v3.2",
        base_url="https://api.novita.ai/openai",
        api_key="test-key",
        stream_usage=False,
        supports_vision=True,
        supports_thinking=False,
    )
    cfg = _make_app_config([model])
    _patch_factory(monkeypatch, cfg)

    captured: dict = {}

    class CapturingModel(FakeChatModel):
        def __init__(self, **kwargs):
            captured.update(kwargs)
            BaseChatModel.__init__(self, **kwargs)

    monkeypatch.setattr(factory_module, "resolve_class", lambda path, base: CapturingModel)

    factory_module.create_chat_model(name="novita-deepseek-v3.2")

    assert captured.get("stream_usage") is False


def test_openai_compatible_provider_enables_stream_usage_for_openai_api_base(monkeypatch):
    """openai_api_base should trigger stream_usage default for ChatOpenAI."""
    model = ModelConfig(
        name="openai-compatible",
        display_name="OpenAI-Compatible",
        description=None,
        use="langchain_openai:ChatOpenAI",
        model="example-model",
        openai_api_base="https://example.com/v1",
        api_key="test-key",
        supports_vision=False,
        supports_thinking=False,
    )
    cfg = _make_app_config([model])
    _patch_factory(monkeypatch, cfg)

    captured: dict = {}

    class CapturingModel(FakeChatModel):
        def __init__(self, **kwargs):
            captured.update(kwargs)
            BaseChatModel.__init__(self, **kwargs)

    monkeypatch.setattr(factory_module, "resolve_class", lambda path, base: CapturingModel)

    factory_module.create_chat_model(name="openai-compatible")

    assert captured.get("openai_api_base") == "https://example.com/v1"
    assert captured.get("stream_usage") is True


def test_non_openai_provider_does_not_receive_stream_usage_default(monkeypatch):
    """Non-OpenAI providers with base_url should not receive stream_usage by default."""
    model = ModelConfig(
        name="ollama-local",
        display_name="Ollama Local",
        description=None,
        use="langchain_ollama:ChatOllama",
        model="qwen2.5",
        base_url="http://127.0.0.1:11434",
        supports_vision=False,
        supports_thinking=False,
    )
    cfg = _make_app_config([model])
    _patch_factory(monkeypatch, cfg)

    captured: dict = {}

    class CapturingModel(FakeChatModel):
        def __init__(self, **kwargs):
            captured.update(kwargs)
            BaseChatModel.__init__(self, **kwargs)

    monkeypatch.setattr(factory_module, "resolve_class", lambda path, base: CapturingModel)

    factory_module.create_chat_model(name="ollama-local")

    assert captured.get("base_url") == "http://127.0.0.1:11434"
    assert "stream_usage" not in captured


def test_openai_compatible_provider_multiple_models(monkeypatch):
    """Multiple models from the same OpenAI-compatible provider should coexist."""
    m1 = ModelConfig(
        name="qwen-3.6-plus",
        display_name="Qwen 3.6 Plus",
        description=None,
        use="langchain_openai:ChatOpenAI",
        model="qwen-3.6-plus",
        base_url="https://api.example.com/v1",
        api_key="test-key",
        temperature=0.7,
        supports_vision=True,
        supports_thinking=False,
    )
    m2 = ModelConfig(
        name="qwen-3.6-turbo",
        display_name="Qwen 3.6 Turbo",
        description=None,
        use="langchain_openai:ChatOpenAI",
        model="qwen-3.6-turbo",
        base_url="https://api.example.com/v1",
        api_key="test-key",
        temperature=0.7,
        supports_vision=True,
        supports_thinking=False,
    )
    cfg = _make_app_config([m1, m2])
    _patch_factory(monkeypatch, cfg)

    captured: dict = {}

    class CapturingModel(FakeChatModel):
        def __init__(self, **kwargs):
            captured.update(kwargs)
            BaseChatModel.__init__(self, **kwargs)

    monkeypatch.setattr(factory_module, "resolve_class", lambda path, base: CapturingModel)

    # Create first model
    factory_module.create_chat_model(name="qwen-3.6-plus")
    assert captured.get("model") == "qwen-3.6-plus"

    # Create second model
    factory_module.create_chat_model(name="qwen-3.6-turbo")
    assert captured.get("model") == "qwen-3.6-turbo"


# ---------------------------------------------------------------------------
# Codex provider reasoning_effort mapping
# ---------------------------------------------------------------------------


class FakeCodexChatModel(FakeChatModel):
    pass


def test_codex_provider_disables_reasoning_when_thinking_disabled(monkeypatch):
    cfg = _make_app_config(
        [
            _make_model(
                "codex",
                use="deerflow.models.openai_codex_provider:CodexChatModel",
                supports_thinking=True,
                supports_reasoning_effort=True,
            )
        ]
    )
    _patch_factory(monkeypatch, cfg, model_class=FakeCodexChatModel)
    monkeypatch.setattr(codex_provider_module, "CodexChatModel", FakeCodexChatModel)

    factory_module.create_chat_model(name="codex", thinking_enabled=False)

    assert FakeChatModel.captured_kwargs.get("reasoning_effort") == "none"


def test_codex_provider_preserves_explicit_reasoning_effort(monkeypatch):
    cfg = _make_app_config(
        [
            _make_model(
                "codex",
                use="deerflow.models.openai_codex_provider:CodexChatModel",
                supports_thinking=True,
                supports_reasoning_effort=True,
            )
        ]
    )
    _patch_factory(monkeypatch, cfg, model_class=FakeCodexChatModel)
    monkeypatch.setattr(codex_provider_module, "CodexChatModel", FakeCodexChatModel)

    factory_module.create_chat_model(name="codex", thinking_enabled=True, reasoning_effort="high")

    assert FakeChatModel.captured_kwargs.get("reasoning_effort") == "high"


def test_codex_provider_defaults_reasoning_effort_to_medium(monkeypatch):
    cfg = _make_app_config(
        [
            _make_model(
                "codex",
                use="deerflow.models.openai_codex_provider:CodexChatModel",
                supports_thinking=True,
                supports_reasoning_effort=True,
            )
        ]
    )
    _patch_factory(monkeypatch, cfg, model_class=FakeCodexChatModel)
    monkeypatch.setattr(codex_provider_module, "CodexChatModel", FakeCodexChatModel)

    factory_module.create_chat_model(name="codex", thinking_enabled=True)

    assert FakeChatModel.captured_kwargs.get("reasoning_effort") == "medium"


def test_codex_provider_strips_unsupported_max_tokens(monkeypatch):
    cfg = _make_app_config(
        [
            _make_model(
                "codex",
                use="deerflow.models.openai_codex_provider:CodexChatModel",
                supports_thinking=True,
                supports_reasoning_effort=True,
                max_tokens=4096,
            )
        ]
    )
    _patch_factory(monkeypatch, cfg, model_class=FakeCodexChatModel)
    monkeypatch.setattr(codex_provider_module, "CodexChatModel", FakeCodexChatModel)

    factory_module.create_chat_model(name="codex", thinking_enabled=True)

    assert "max_tokens" not in FakeChatModel.captured_kwargs


def test_thinking_disabled_vllm_chat_template_format(monkeypatch):
    wte = {"extra_body": {"chat_template_kwargs": {"thinking": True}}}
    model = _make_model(
        "vllm-qwen",
        use="deerflow.models.vllm_provider:VllmChatModel",
        supports_thinking=True,
        when_thinking_enabled=wte,
    )
    model.extra_body = {"top_k": 20}
    cfg = _make_app_config([model])
    _patch_factory(monkeypatch, cfg)

    captured: dict = {}

    class CapturingModel(FakeChatModel):
        def __init__(self, **kwargs):
            captured.update(kwargs)
            BaseChatModel.__init__(self, **kwargs)

    monkeypatch.setattr(factory_module, "resolve_class", lambda path, base: CapturingModel)

    factory_module.create_chat_model(name="vllm-qwen", thinking_enabled=False)

    assert captured.get("extra_body") == {"top_k": 20, "chat_template_kwargs": {"thinking": False}}
    assert captured.get("reasoning_effort") is None


def test_thinking_disabled_vllm_enable_thinking_format(monkeypatch):
    wte = {"extra_body": {"chat_template_kwargs": {"enable_thinking": True}}}
    model = _make_model(
        "vllm-qwen-enable",
        use="deerflow.models.vllm_provider:VllmChatModel",
        supports_thinking=True,
        when_thinking_enabled=wte,
    )
    model.extra_body = {"top_k": 20}
    cfg = _make_app_config([model])
    _patch_factory(monkeypatch, cfg)

    captured: dict = {}

    class CapturingModel(FakeChatModel):
        def __init__(self, **kwargs):
            captured.update(kwargs)
            BaseChatModel.__init__(self, **kwargs)

    monkeypatch.setattr(factory_module, "resolve_class", lambda path, base: CapturingModel)

    factory_module.create_chat_model(name="vllm-qwen-enable", thinking_enabled=False)

    assert captured.get("extra_body") == {
        "top_k": 20,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    assert captured.get("reasoning_effort") is None


# ---------------------------------------------------------------------------
# stream_usage injection
# ---------------------------------------------------------------------------


class _FakeWithStreamUsage(FakeChatModel):
    """Fake model that declares stream_usage in model_fields (like BaseChatOpenAI)."""

    stream_usage: bool | None = None


def test_stream_usage_injected_for_openai_compatible_model(monkeypatch):
    """Factory should set stream_usage=True for models with stream_usage field."""
    cfg = _make_app_config([_make_model("deepseek", use="langchain_deepseek:ChatDeepSeek")])
    _patch_factory(monkeypatch, cfg, model_class=_FakeWithStreamUsage)

    captured: dict = {}

    class CapturingModel(_FakeWithStreamUsage):
        def __init__(self, **kwargs):
            captured.update(kwargs)
            BaseChatModel.__init__(self, **kwargs)

    monkeypatch.setattr(factory_module, "resolve_class", lambda path, base: CapturingModel)

    factory_module.create_chat_model(name="deepseek")

    assert captured.get("stream_usage") is True


def test_stream_usage_not_injected_for_non_openai_model(monkeypatch):
    """Factory should NOT inject stream_usage for models without the field."""
    cfg = _make_app_config([_make_model("claude", use="langchain_anthropic:ChatAnthropic")])
    _patch_factory(monkeypatch, cfg)

    captured: dict = {}

    class CapturingModel(FakeChatModel):
        def __init__(self, **kwargs):
            captured.update(kwargs)
            BaseChatModel.__init__(self, **kwargs)

    monkeypatch.setattr(factory_module, "resolve_class", lambda path, base: CapturingModel)

    factory_module.create_chat_model(name="claude")

    assert "stream_usage" not in captured


def test_stream_usage_not_overridden_when_explicitly_set_in_config(monkeypatch):
    """If config dumps stream_usage=False, factory should respect it."""
    cfg = _make_app_config([_make_model("deepseek", use="langchain_deepseek:ChatDeepSeek")])
    _patch_factory(monkeypatch, cfg, model_class=_FakeWithStreamUsage)

    captured: dict = {}

    class CapturingModel(_FakeWithStreamUsage):
        def __init__(self, **kwargs):
            captured.update(kwargs)
            BaseChatModel.__init__(self, **kwargs)

    monkeypatch.setattr(factory_module, "resolve_class", lambda path, base: CapturingModel)

    # Simulate config having stream_usage explicitly set by patching model_dump
    original_get_model_config = cfg.get_model_config

    def patched_get_model_config(name):
        mc = original_get_model_config(name)
        mc.stream_usage = False  # type: ignore[attr-defined]
        return mc

    monkeypatch.setattr(cfg, "get_model_config", patched_get_model_config)

    factory_module.create_chat_model(name="deepseek")

    assert captured.get("stream_usage") is False


def test_openai_responses_api_settings_are_passed_to_chatopenai(monkeypatch):
    model = ModelConfig(
        name="gpt-5-responses",
        display_name="GPT-5 Responses",
        description=None,
        use="langchain_openai:ChatOpenAI",
        model="gpt-5",
        api_key="test-key",
        use_responses_api=True,
        output_version="responses/v1",
        supports_thinking=False,
        supports_vision=True,
    )
    cfg = _make_app_config([model])
    _patch_factory(monkeypatch, cfg)

    captured: dict = {}

    class CapturingModel(FakeChatModel):
        def __init__(self, **kwargs):
            captured.update(kwargs)
            BaseChatModel.__init__(self, **kwargs)

    monkeypatch.setattr(factory_module, "resolve_class", lambda path, base: CapturingModel)

    factory_module.create_chat_model(name="gpt-5-responses")

    assert captured.get("use_responses_api") is True
    assert captured.get("output_version") == "responses/v1"


# ---------------------------------------------------------------------------
# Duplicate keyword argument collision (issue #1977)
# ---------------------------------------------------------------------------


def test_no_duplicate_kwarg_when_reasoning_effort_in_config_and_thinking_disabled(monkeypatch):
    """When reasoning_effort is set in config.yaml (extra field) AND the thinking-disabled
    path also injects reasoning_effort=minimal into kwargs, the factory must not raise
    TypeError: got multiple values for keyword argument 'reasoning_effort'."""
    wte = {"extra_body": {"thinking": {"type": "enabled", "budget_tokens": 5000}}}
    # ModelConfig.extra="allow" means extra fields from config.yaml land in model_dump()
    model = ModelConfig(
        name="doubao-model",
        display_name="Doubao 1.8",
        description=None,
        use="deerflow.models.patched_deepseek:PatchedChatDeepSeek",
        model="doubao-seed-1-8-250315",
        reasoning_effort="high",  # user-set extra field in config.yaml
        supports_thinking=True,
        supports_reasoning_effort=True,
        when_thinking_enabled=wte,
        supports_vision=False,
    )
    cfg = _make_app_config([model])

    captured: dict = {}

    class CapturingModel(FakeChatModel):
        def __init__(self, **kwargs):
            captured.update(kwargs)
            BaseChatModel.__init__(self, **kwargs)

    _patch_factory(monkeypatch, cfg, model_class=CapturingModel)

    # Must not raise TypeError
    factory_module.create_chat_model(name="doubao-model", thinking_enabled=False)

    # kwargs (runtime) takes precedence: thinking-disabled path sets reasoning_effort=minimal
    assert captured.get("reasoning_effort") == "minimal"


# ---------------------------------------------------------------------------
# 2026-10-12 花名册批：claude 系 360 通道条目构造（PatchedChatOpenAI + base_url
# + anthropic/ 前缀 wire id，spike 留档仓库根 temp/results.json）。Ark 直连聊天
# 构造用例随 doubao-seed-2.1-pro-ark 删册移除（Ark 通道仅剩生图/生视频，无
# 聊天条目）。
# ---------------------------------------------------------------------------

_ARK_BASE = "https://ark.cn-beijing.volces.com/api/v3"
_QIHU_BASE = "https://api.360.cn/v1"


def _claude_sonnet_model() -> ModelConfig:
    """claude-sonnet-5-5 entry: PatchedChatOpenAI + 360 gateway + anthropic wire id."""
    return ModelConfig(
        name="claude-sonnet-5-5",
        display_name="360·Claude Sonnet 5.5",
        description=None,
        use="deerflow.models.patched_openai:PatchedChatOpenAI",
        model="anthropic/claude-sonnet-5-5",  # 360 网关 wire id（anthropic/ 前缀）
        base_url=_QIHU_BASE,
        api_key="test-qihu-key",
        max_tokens=32768,
        supports_thinking=False,  # spike：thinking 参数被接受但无 reasoning 产出 = 无效参数
        supports_vision=True,  # spike：256×256 纯红图答对主色
    )


def test_claude_360_entry_passes_base_url_and_wire_id(monkeypatch):
    """claude 条目（OpenAI 兼容形态）必须透传 base_url + anthropic/ wire id。"""
    cfg = _make_app_config([_claude_sonnet_model()])
    _patch_factory(monkeypatch, cfg)
    captured: dict = {}

    class CapturingModel(FakeChatModel):
        def __init__(self, **kwargs):
            captured.update(kwargs)
            BaseChatModel.__init__(self, **kwargs)

    monkeypatch.setattr(factory_module, "resolve_class", lambda path, base: CapturingModel)

    factory_module.create_chat_model(name="claude-sonnet-5-5")

    # OpenAI 兼容 provider 吃 base_url；wire id 带 anthropic/ 前缀原样上 wire
    assert captured.get("base_url") == _QIHU_BASE
    assert captured.get("model") == "anthropic/claude-sonnet-5-5"
    assert captured.get("api_key") == "test-qihu-key"
    # supports_vision is read by factory/gateway to gate the view_image tool, NOT passed
    # to the provider constructor (it's in the exclude list). Verify on the config itself.
    assert _claude_sonnet_model().supports_vision is True


# ---------------------------------------------------------------------------
# Ark 花名册守卫——读真实 config.yaml（2026-10-05 P0 #9 引入；2026-10-12 花名册
# 批前提反转）
#
# 旧守卫断言「至少存在一条 Ark 直连聊天条目且用原生 id/端点」——豆包聊天删册
# （决策 7）后花名册 **无 Ark 直连聊天条目**（Ark 通道仅剩 seedream/seedance
# 生图生视频，不进 models[]），旧断言按其自述「全部撤下时这里必须红」变红。
# 现守卫反转为新不变量：models[] 内不得出现 Ark 直连聊天条目（聊天一律走
# 360 通道）。若未来重引 Ark 聊天条目，须重写本守卫并恢复原生 id/端点两断言。
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _ark_chat_entries(config: AppConfig) -> list[ModelConfig]:
    """真实花名册中的 Ark 直连聊天条目（api_base 或 base_url 指向 ark.cn-beijing）。

    双字段口径（终审 F5）：DeepSeek 形态条目吃 api_base（已删的 doubao 即此形态），
    OpenAI 形态条目吃 base_url（config.example.yaml 的 glm-5.2-ark 模板即此形态）
    ——只查 api_base 会让 base_url 形态的 Ark 条目绕过「聊天一律走 360」不变量。
    """
    out = []
    for m in config.models:
        if _ARK_BASE in (getattr(m, "api_base", None), getattr(m, "base_url", None)):
            out.append(m)
    return out


@pytest.fixture()
def real_config(monkeypatch) -> AppConfig:
    config_path = _REPO_ROOT / "config.yaml"
    if not config_path.exists():
        pytest.skip("Ark 花名册守卫依赖本地 gitignored config.yaml，CI 跳过")
    monkeypatch.setenv("QIHU_API_KEY", "test-key")
    monkeypatch.setenv("ARK_API_KEY", "test-key")
    return AppConfig.from_file(str(config_path))


def test_no_ark_direct_chat_entries_in_roster(real_config):
    """2026-10-12 起聊天模型一律走 360 通道——models[] 出现 Ark 直连条目即为
    配置漂移（Ark 仅服务生图/生视频，不注册聊天）。"""
    entries = _ark_chat_entries(real_config)
    assert not entries, f"models[] 不应再有 Ark 直连聊天条目（2026-10-12 删册）：{[m.name for m in entries]}"


def test_ark_chat_entries_filter_catches_base_url_form():
    """守卫过滤器双字段口径（终审 F5）：base_url 形态（OpenAI provider）的 Ark
    直连条目（如 example 的 glm-5.2-ark 模板）必须被识别——只查 api_base 会漏。"""
    from types import SimpleNamespace

    fake = SimpleNamespace(
        models=[
            SimpleNamespace(name="glm-5.2-ark", model="glm-5-2-260617", api_base=None, base_url=_ARK_BASE),
            SimpleNamespace(name="qwen3.8-flash", model="qwen/qwen3.8-flash", api_base=None, base_url="https://api.360.cn/v1"),
        ]
    )
    entries = _ark_chat_entries(fake)
    assert [m.name for m in entries] == ["glm-5.2-ark"]


# ---------------------------------------------------------------------------
# Console-only annotations must not reach the provider (A2 fallout)
# ---------------------------------------------------------------------------


def test_pricing_annotation_is_not_forwarded_to_the_provider(monkeypatch):
    """``pricing`` is console bookkeeping, not an LLM parameter.

    ``ModelConfig`` allows extra fields and ``create_chat_model`` dumps the
    whole config into the provider constructor, excluding only a fixed list of
    metadata names. ``pricing`` was not on that list, so annotating a model
    with prices — exactly what the console docs tell operators to do — sent
    ``pricing=...`` into ``ChatOpenAI``, through ``model_kwargs``, and on to
    the wire.

    Observed live: every chat run failed with
    ``AsyncCompletions.create() got an unexpected keyword argument 'pricing'``
    and recorded ``llm.error`` with zero tokens. Configuring cost reporting
    disabled chat entirely.
    """
    model = _make_model("priced")
    model.pricing = {"currency": "CNY", "input_per_million": 8, "output_per_million": 32}
    cfg = _make_app_config([model])
    _patch_factory(monkeypatch, cfg)

    factory_module.create_chat_model(name="priced")

    assert "pricing" not in FakeChatModel.captured_kwargs
    # And it must not sneak through the model_kwargs passthrough either.
    assert "pricing" not in (FakeChatModel.captured_kwargs.get("model_kwargs") or {})


def test_excluding_pricing_leaves_real_parameters_alone(monkeypatch):
    """The exclusion must be surgical — real provider settings still pass."""
    model = _make_model("priced", max_tokens=4096)
    model.pricing = {"currency": "CNY", "input_per_million": 8, "output_per_million": 32}
    cfg = _make_app_config([model])
    _patch_factory(monkeypatch, cfg)

    factory_module.create_chat_model(name="priced")

    assert FakeChatModel.captured_kwargs["max_tokens"] == 4096
    assert FakeChatModel.captured_kwargs["model"] == "priced"


# ---------------------------------------------------------------------------
# stream_chunk_timeout passthrough (T6 / C-02, 2026-08-13)
# ---------------------------------------------------------------------------


def test_stream_chunk_timeout_extra_field_reaches_constructor(monkeypatch):
    """C-02: ``stream_chunk_timeout`` is an extra ModelConfig field — verify it
    survives the model_dump exclusion list and reaches the chat-model
    constructor. langchain-openai 1.2.1 declares the field (base.py:868); the
    default 120s is too short for thinking models whose first token takes
    90-150s. Mutation guard: excluding the field (or dropping it from
    model_dump) turns this red.

    The FakeChatModel is not a BaseChatOpenAI subclass, so the new
    _apply_stream_chunk_timeout_default would drop the key — patch the helper
    to a passthrough here; its own behavior is covered in
    TestStreamChunkTimeoutDefault."""
    model = _make_model("t6-stream-chunk")
    model.stream_chunk_timeout = 240
    cfg = _make_app_config([model])
    _patch_factory(monkeypatch, cfg)
    monkeypatch.setattr(factory_module, "_apply_stream_chunk_timeout_default", lambda cls, settings: None)

    factory_module.create_chat_model(name="t6-stream-chunk")

    assert FakeChatModel.captured_kwargs.get("stream_chunk_timeout") == 240


def test_stream_chunk_timeout_default_injected_for_openai_subclass():
    """T6 root-cause item (2026-08-13): OpenAI-compatible clients get a 240s
    default when no explicit value is configured (langchain's own default is
    120s — too short for thinking first tokens at 90-150s)."""
    from langchain_openai.chat_models.base import BaseChatOpenAI

    settings = {}
    factory_module._apply_stream_chunk_timeout_default(BaseChatOpenAI, settings)
    assert settings["stream_chunk_timeout"] == 240.0


def test_stream_chunk_timeout_explicit_value_wins():
    from langchain_openai.chat_models.base import BaseChatOpenAI

    settings = {"stream_chunk_timeout": 100}
    factory_module._apply_stream_chunk_timeout_default(BaseChatOpenAI, settings)
    assert settings["stream_chunk_timeout"] == 100


def test_stream_chunk_timeout_dropped_for_non_openai_clients():
    """Non-OpenAI clients do not declare the kwarg — the key must be dropped
    rather than forwarded (it would be rejected or silently diverted)."""
    from langchain_openai.chat_models.base import BaseChatOpenAI

    class _NotOpenAI(FakeChatModel):
        pass

    settings = {"stream_chunk_timeout": 240}
    factory_module._apply_stream_chunk_timeout_default(_NotOpenAI, settings)
    assert "stream_chunk_timeout" not in settings
    # sanity: the gate is the subclass check, not the value
    settings2 = {}
    factory_module._apply_stream_chunk_timeout_default(BaseChatOpenAI, settings2)
    assert settings2["stream_chunk_timeout"] == 240.0


# ---------------------------------------------------------------------------
# thinking enable path: deep-merge of when_thinking_enabled (upstream 931d2eeb)
# ---------------------------------------------------------------------------


def test_thinking_enabled_deep_merge_preserves_profile_extra_body_siblings(monkeypatch):
    """A profile-level extra_body (e.g. GLM's tool_stream) must survive when the
    when_thinking_enabled template also sets extra_body — a shallow dict.update
    would replace the whole section, but only while thinking was on."""
    wte = {"extra_body": {"thinking": {"type": "enabled"}}}
    model = _make_model("glm-thinker", supports_thinking=True, when_thinking_enabled=wte)
    model.extra_body = {"tool_stream": True}
    cfg = _make_app_config([model])
    _patch_factory(monkeypatch, cfg)

    captured: dict = {}

    class CapturingModel(FakeChatModel):
        def __init__(self, **kwargs):
            captured.update(kwargs)
            BaseChatModel.__init__(self, **kwargs)

    monkeypatch.setattr(factory_module, "resolve_class", lambda path, base: CapturingModel)

    factory_module.create_chat_model(name="glm-thinker", thinking_enabled=True)

    assert captured.get("extra_body") == {"tool_stream": True, "thinking": {"type": "enabled"}}


def test_thinking_enabled_deep_merge_template_wins_on_conflict(monkeypatch):
    """Deep merge must not change precedence: template values still win over
    profile values for the same key."""
    wte = {"extra_body": {"thinking": {"type": "enabled"}}}
    model = _make_model("conflict-model", supports_thinking=True, when_thinking_enabled=wte)
    model.extra_body = {"thinking": {"type": "disabled"}, "tool_stream": True}
    cfg = _make_app_config([model])
    _patch_factory(monkeypatch, cfg)

    captured: dict = {}

    class CapturingModel(FakeChatModel):
        def __init__(self, **kwargs):
            captured.update(kwargs)
            BaseChatModel.__init__(self, **kwargs)

    monkeypatch.setattr(factory_module, "resolve_class", lambda path, base: CapturingModel)

    factory_module.create_chat_model(name="conflict-model", thinking_enabled=True)

    assert captured.get("extra_body") == {"tool_stream": True, "thinking": {"type": "enabled"}}


def test_thinking_enabled_deep_merge_does_not_alias_model_config_dicts(monkeypatch):
    """Mutating the merged constructor kwargs must not leak back into the
    ModelConfig — the merge used to hand out live references to
    when_thinking_enabled's nested dicts."""
    wte = {"extra_body": {"thinking": {"type": "enabled"}}}
    model = _make_model("alias-guard", supports_thinking=True, when_thinking_enabled=wte)
    cfg = _make_app_config([model])
    _patch_factory(monkeypatch, cfg)

    captured: dict = {}

    class CapturingModel(FakeChatModel):
        def __init__(self, **kwargs):
            captured.update(kwargs)
            BaseChatModel.__init__(self, **kwargs)

    monkeypatch.setattr(factory_module, "resolve_class", lambda path, base: CapturingModel)

    factory_module.create_chat_model(name="alias-guard", thinking_enabled=True)

    captured["extra_body"]["thinking"]["type"] = "mutated"
    model_config = cfg.get_model_config("alias-guard")
    assert model_config.when_thinking_enabled == {"extra_body": {"thinking": {"type": "enabled"}}}
