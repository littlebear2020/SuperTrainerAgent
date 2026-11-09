"""Tests for subagent thinking configurability (P1-2, 2026-09-29 remediation).

Covers:
- Three-state resolution: config.thinking_enabled (None/True/False) x parent
  run's mode (None/True/False) -> effective value handed to create_chat_model
- Degradation guard: thinking on + model without supports_thinking -> warn and
  fall back to False (mirrors make_lead_agent)
- reasoning_effort forwarding: only when thinking is on, the model declares
  support, and the parent run provided an effort
- config.yaml override chain: agents.<name>.thinking_enabled via registry,
  custom_agents passthrough, builtin non-mutation
- Priority pin: an explicit false must beat an inherited true (the "or" bug
  would make bash impossible to quiet)

Note: mirrors tests/test_subagent_executor.py's sys.modules strategy — the real
executor module is imported with its circular dependencies stubbed.
"""

import logging
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

import pytest

_MOCKED_MODULE_NAMES = [
    "deerflow.agents",
    "deerflow.agents.thread_state",
    "deerflow.agents.middlewares",
    "deerflow.agents.middlewares.thread_data_middleware",
    "deerflow.sandbox",
    "deerflow.sandbox.middleware",
    "deerflow.sandbox.security",
    "deerflow.models",
]


@pytest.fixture(scope="module", autouse=True)
def _setup_executor_classes():
    """Import the real executor with circular deps stubbed; undo afterwards."""
    mp = pytest.MonkeyPatch()
    mp.delitem(sys.modules, "deerflow.subagents.executor", raising=False)
    for name in _MOCKED_MODULE_NAMES:
        mp.setitem(sys.modules, name, MagicMock())

    from deerflow.subagents.config import SubagentConfig
    from deerflow.subagents.executor import SubagentExecutor

    yield {
        "SubagentConfig": SubagentConfig,
        "SubagentExecutor": SubagentExecutor,
    }

    mp.undo()


@pytest.fixture
def classes(_setup_executor_classes):
    return _setup_executor_classes


def _module(name: str, **attrs) -> ModuleType:
    module = ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    return module


def _run_create_agent(
    classes,
    monkeypatch: pytest.MonkeyPatch,
    *,
    config_thinking,
    parent_thinking,
    parent_effort=None,
    supports_thinking=True,
    supports_reasoning_effort=True,
) -> dict:
    """Drive SubagentExecutor._create_agent and return create_chat_model kwargs."""
    from deerflow.subagents import executor as executor_module

    SubagentConfig = classes["SubagentConfig"]
    SubagentExecutor = classes["SubagentExecutor"]

    captured: dict[str, dict] = {}

    def fake_create_chat_model(**kwargs):
        captured["model"] = kwargs
        return object()

    monkeypatch.setattr(executor_module, "create_chat_model", fake_create_chat_model)
    monkeypatch.setattr(executor_module, "create_agent", lambda **kwargs: object())
    monkeypatch.setitem(
        sys.modules,
        "deerflow.agents.middlewares.tool_error_handling_middleware",
        _module(
            "deerflow.agents.middlewares.tool_error_handling_middleware",
            build_subagent_runtime_middlewares=lambda **kwargs: [],
        ),
    )

    app_config = SimpleNamespace(
        get_model_config=lambda name: SimpleNamespace(
            supports_thinking=supports_thinking,
            supports_reasoning_effort=supports_reasoning_effort,
        )
    )
    config = SubagentConfig(
        name="test-agent",
        description="Test agent",
        system_prompt="You are a test agent.",
        thinking_enabled=config_thinking,
    )
    executor = SubagentExecutor(
        config=config,
        tools=[],
        app_config=app_config,
        parent_model="parent-model",
        parent_thinking_enabled=parent_thinking,
        parent_reasoning_effort=parent_effort,
    )
    executor._create_agent()
    return captured["model"]


def _reset_subagents_config() -> None:
    from deerflow.config.subagents_config import load_subagents_config_from_dict

    load_subagents_config_from_dict({})


# ---------------------------------------------------------------------------
# Resolution matrix
# ---------------------------------------------------------------------------


class TestThinkingResolution:
    @pytest.mark.parametrize(
        ("config_value", "parent_value", "expected"),
        [
            (None, None, False),
            (None, True, True),
            (None, False, False),
            (True, None, True),
            (True, True, True),
            (True, False, True),
            (False, None, False),
            (False, True, False),
            (False, False, False),
        ],
    )
    def test_resolution_matrix(self, classes, monkeypatch, config_value, parent_value, expected):
        kwargs = _run_create_agent(
            classes,
            monkeypatch,
            config_thinking=config_value,
            parent_thinking=parent_value,
        )
        assert kwargs["thinking_enabled"] is expected

    def test_explicit_false_beats_inherited_true(self, classes, monkeypatch):
        """Priority pin: the config's explicit false must win over parent True.

        Guards against regressing into an "or" merge — that would make
        ``bash`` (config.yaml thinking_enabled: false) impossible to quiet
        whenever the ultra-tier lead runs with thinking on.
        """
        kwargs = _run_create_agent(classes, monkeypatch, config_thinking=False, parent_thinking=True)
        assert kwargs["thinking_enabled"] is False


# ---------------------------------------------------------------------------
# Degradation guard
# ---------------------------------------------------------------------------


class TestThinkingDegradationGuard:
    def test_unsupported_model_falls_back_with_warning(self, classes, monkeypatch, caplog):
        kwargs = _run_create_agent(
            classes,
            monkeypatch,
            config_thinking=True,
            parent_thinking=None,
            supports_thinking=False,
        )
        assert kwargs["thinking_enabled"] is False
        with caplog.at_level(logging.WARNING, logger="deerflow.subagents.executor"):
            _run_create_agent(
                classes,
                monkeypatch,
                config_thinking=True,
                parent_thinking=None,
                supports_thinking=False,
            )
        assert "does not support it" in caplog.text

    def test_inherited_true_also_degrades(self, classes, monkeypatch):
        kwargs = _run_create_agent(
            classes,
            monkeypatch,
            config_thinking=None,
            parent_thinking=True,
            supports_thinking=False,
        )
        assert kwargs["thinking_enabled"] is False


# ---------------------------------------------------------------------------
# reasoning_effort forwarding
# ---------------------------------------------------------------------------


class TestReasoningEffortForwarding:
    def test_forwarded_when_thinking_on(self, classes, monkeypatch):
        kwargs = _run_create_agent(
            classes,
            monkeypatch,
            config_thinking=True,
            parent_thinking=None,
            parent_effort="high",
        )
        assert kwargs["reasoning_effort"] == "high"

    def test_not_forwarded_when_thinking_off(self, classes, monkeypatch):
        kwargs = _run_create_agent(
            classes,
            monkeypatch,
            config_thinking=False,
            parent_thinking=True,
            parent_effort="high",
        )
        assert "reasoning_effort" not in kwargs

    def test_not_forwarded_when_model_unsupported(self, classes, monkeypatch):
        kwargs = _run_create_agent(
            classes,
            monkeypatch,
            config_thinking=True,
            parent_thinking=None,
            parent_effort="high",
            supports_reasoning_effort=False,
        )
        assert "reasoning_effort" not in kwargs

    def test_not_forwarded_when_parent_has_none(self, classes, monkeypatch):
        kwargs = _run_create_agent(
            classes,
            monkeypatch,
            config_thinking=True,
            parent_thinking=True,
            parent_effort=None,
        )
        assert "reasoning_effort" not in kwargs


# ---------------------------------------------------------------------------
# config.yaml override chain
# ---------------------------------------------------------------------------


class TestThinkingConfigOverride:
    def teardown_method(self):
        _reset_subagents_config()

    def test_subagent_config_default_is_none(self, classes):
        config = classes["SubagentConfig"](name="t", description="t", system_prompt="t")
        assert config.thinking_enabled is None

    def test_agents_override_applies_to_builtin(self):
        from deerflow.config.subagents_config import load_subagents_config_from_dict
        from deerflow.subagents.registry import get_subagent_config

        load_subagents_config_from_dict(
            {"agents": {"general-purpose": {"thinking_enabled": False}}},
        )
        config = get_subagent_config("general-purpose")
        assert config.thinking_enabled is False

    def test_custom_agent_passthrough(self):
        from deerflow.config.subagents_config import load_subagents_config_from_dict
        from deerflow.subagents.registry import get_subagent_config

        load_subagents_config_from_dict(
            {
                "custom_agents": {
                    "analysis": {
                        "description": "Analysis",
                        "system_prompt": "Analyze.",
                        "thinking_enabled": True,
                    },
                },
            },
        )
        config = get_subagent_config("analysis")
        assert config.thinking_enabled is True

    def test_get_thinking_for_none_when_unset(self):
        from deerflow.config.subagents_config import (
            SubagentsAppConfig,
            load_subagents_config_from_dict,
        )

        load_subagents_config_from_dict(
            {"agents": {"general-purpose": {"max_turns": 42}}},
        )
        cfg = SubagentsAppConfig()
        assert cfg.get_thinking_for("general-purpose") is None
        assert cfg.get_thinking_for("unknown-agent") is None

    def test_override_does_not_mutate_builtin(self):
        from deerflow.config.subagents_config import load_subagents_config_from_dict
        from deerflow.subagents.builtins import BUILTIN_SUBAGENTS
        from deerflow.subagents.registry import get_subagent_config

        load_subagents_config_from_dict(
            {"agents": {"general-purpose": {"thinking_enabled": True}}},
        )
        _ = get_subagent_config("general-purpose")
        assert BUILTIN_SUBAGENTS["general-purpose"].thinking_enabled is None
