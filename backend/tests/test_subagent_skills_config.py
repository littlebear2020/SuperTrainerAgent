"""Tests for subagent per-agent skill configuration and custom subagent types.

Covers:
- SubagentConfig.skills field
- SubagentOverrideConfig.skills field
- CustomSubagentConfig model validation
- SubagentsAppConfig.custom_agents and get_skills_for()
- Registry: custom agent lookup, skills override, merged available names
- Executor skills index injection (P1-1, 2026-09-29: index instead of SKILL.md full text)

The skills-filter passthrough in task_tool config assembly is covered
end-to-end by tests/test_task_tool_core_logic.py
(test_task_tool_inherits_parent_skill_allowlist_for_default_subagent,
test_task_tool_intersects_parent_and_subagent_skill_allowlists) against the
real task_tool — a former local test class here only re-implemented the
passthrough expression in the test body and was deleted as a tautology.
"""

import importlib.util
import logging
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import SystemMessage

from deerflow.config.subagents_config import (
    CustomSubagentConfig,
    SubagentOverrideConfig,
    SubagentsAppConfig,
    get_subagents_app_config,
    load_subagents_config_from_dict,
)
from deerflow.subagents.config import SubagentConfig

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _reset_subagents_config(**kwargs) -> None:
    """Reset global subagents config to a known state."""
    load_subagents_config_from_dict(kwargs)


# ---------------------------------------------------------------------------
# SubagentConfig.skills field
# ---------------------------------------------------------------------------


class TestSubagentConfigSkills:
    def test_default_skills_is_none(self):
        config = SubagentConfig(name="test", description="test", system_prompt="test")
        assert config.skills is None

    def test_skills_whitelist(self):
        config = SubagentConfig(
            name="test",
            description="test",
            system_prompt="test",
            skills=["data-analysis", "visualization"],
        )
        assert config.skills == ["data-analysis", "visualization"]

    def test_skills_empty_list_means_no_skills(self):
        config = SubagentConfig(
            name="test",
            description="test",
            system_prompt="test",
            skills=[],
        )
        assert config.skills == []


# ---------------------------------------------------------------------------
# SubagentOverrideConfig.skills field
# ---------------------------------------------------------------------------


class TestSubagentOverrideConfigSkills:
    def test_default_skills_is_none(self):
        override = SubagentOverrideConfig()
        assert override.skills is None

    def test_skills_whitelist(self):
        override = SubagentOverrideConfig(skills=["web-search", "data-analysis"])
        assert override.skills == ["web-search", "data-analysis"]

    def test_skills_empty_list(self):
        override = SubagentOverrideConfig(skills=[])
        assert override.skills == []

    def test_skills_coexists_with_other_fields(self):
        override = SubagentOverrideConfig(
            timeout_seconds=300,
            model="gpt-5",
            skills=["my-skill"],
        )
        assert override.timeout_seconds == 300
        assert override.model == "gpt-5"
        assert override.skills == ["my-skill"]


# ---------------------------------------------------------------------------
# CustomSubagentConfig model
# ---------------------------------------------------------------------------


class TestCustomSubagentConfig:
    def test_minimal_valid(self):
        config = CustomSubagentConfig(
            description="A test agent",
            system_prompt="You are a test agent.",
        )
        assert config.description == "A test agent"
        assert config.system_prompt == "You are a test agent."
        assert config.tools is None
        assert config.disallowed_tools == ["task", "ask_clarification", "present_files"]
        assert config.skills is None
        assert config.model == "inherit"
        assert config.max_turns == 50
        assert config.timeout_seconds == 900

    def test_full_configuration(self):
        config = CustomSubagentConfig(
            description="Data analysis specialist",
            system_prompt="You are a data analysis subagent.",
            tools=["bash", "read_file", "write_file"],
            disallowed_tools=["task"],
            skills=["data-analysis", "visualization"],
            model="qwen3:32b",
            max_turns=80,
            timeout_seconds=600,
        )
        assert config.tools == ["bash", "read_file", "write_file"]
        assert config.skills == ["data-analysis", "visualization"]
        assert config.model == "qwen3:32b"
        assert config.max_turns == 80
        assert config.timeout_seconds == 600

    def test_skills_empty_list_no_skills(self):
        config = CustomSubagentConfig(
            description="test",
            system_prompt="test",
            skills=[],
        )
        assert config.skills == []

    def test_rejects_zero_max_turns(self):
        with pytest.raises(ValueError):
            CustomSubagentConfig(
                description="test",
                system_prompt="test",
                max_turns=0,
            )

    def test_rejects_zero_timeout(self):
        with pytest.raises(ValueError):
            CustomSubagentConfig(
                description="test",
                system_prompt="test",
                timeout_seconds=0,
            )


# ---------------------------------------------------------------------------
# SubagentsAppConfig.custom_agents and get_skills_for()
# ---------------------------------------------------------------------------


class TestSubagentsAppConfigCustomAgents:
    def test_default_custom_agents_empty(self):
        config = SubagentsAppConfig()
        assert config.custom_agents == {}

    def test_custom_agents_loaded(self):
        config = SubagentsAppConfig(
            custom_agents={
                "analysis": CustomSubagentConfig(
                    description="Analysis agent",
                    system_prompt="You analyze data.",
                    skills=["data-analysis"],
                ),
            }
        )
        assert "analysis" in config.custom_agents
        assert config.custom_agents["analysis"].skills == ["data-analysis"]

    def test_multiple_custom_agents(self):
        config = SubagentsAppConfig(
            custom_agents={
                "analysis": CustomSubagentConfig(
                    description="Analysis",
                    system_prompt="analyze",
                    skills=["data-analysis"],
                ),
                "researcher": CustomSubagentConfig(
                    description="Research",
                    system_prompt="research",
                    skills=["web-search"],
                ),
            }
        )
        assert len(config.custom_agents) == 2


class TestGetSkillsFor:
    def test_returns_none_when_no_override(self):
        config = SubagentsAppConfig()
        assert config.get_skills_for("general-purpose") is None
        assert config.get_skills_for("unknown") is None

    def test_returns_skills_whitelist(self):
        config = SubagentsAppConfig(
            agents={
                "general-purpose": SubagentOverrideConfig(skills=["web-search", "coding"]),
            }
        )
        assert config.get_skills_for("general-purpose") == ["web-search", "coding"]

    def test_returns_empty_list_for_no_skills(self):
        config = SubagentsAppConfig(
            agents={
                "bash": SubagentOverrideConfig(skills=[]),
            }
        )
        assert config.get_skills_for("bash") == []

    def test_returns_none_for_unrelated_agent(self):
        config = SubagentsAppConfig(
            agents={
                "bash": SubagentOverrideConfig(skills=["web-search"]),
            }
        )
        assert config.get_skills_for("general-purpose") is None

    def test_returns_none_when_skills_not_set(self):
        config = SubagentsAppConfig(
            agents={
                "bash": SubagentOverrideConfig(timeout_seconds=300),
            }
        )
        assert config.get_skills_for("bash") is None


# ---------------------------------------------------------------------------
# load_subagents_config_from_dict with skills and custom_agents
# ---------------------------------------------------------------------------


class TestLoadSubagentsConfigWithSkills:
    def teardown_method(self):
        _reset_subagents_config()

    def test_load_with_skills_override(self):
        load_subagents_config_from_dict(
            {
                "timeout_seconds": 900,
                "agents": {
                    "general-purpose": {"skills": ["web-search", "data-analysis"]},
                },
            }
        )
        cfg = get_subagents_app_config()
        assert cfg.get_skills_for("general-purpose") == ["web-search", "data-analysis"]

    def test_load_with_empty_skills(self):
        load_subagents_config_from_dict(
            {
                "timeout_seconds": 900,
                "agents": {
                    "bash": {"skills": []},
                },
            }
        )
        cfg = get_subagents_app_config()
        assert cfg.get_skills_for("bash") == []

    def test_load_with_custom_agents(self):
        load_subagents_config_from_dict(
            {
                "timeout_seconds": 900,
                "custom_agents": {
                    "analysis": {
                        "description": "Data analysis specialist",
                        "system_prompt": "You are a data analysis subagent.",
                        "skills": ["data-analysis", "visualization"],
                        "tools": ["bash", "read_file"],
                        "max_turns": 80,
                        "timeout_seconds": 600,
                    },
                },
            }
        )
        cfg = get_subagents_app_config()
        assert "analysis" in cfg.custom_agents
        custom = cfg.custom_agents["analysis"]
        assert custom.skills == ["data-analysis", "visualization"]
        assert custom.tools == ["bash", "read_file"]
        assert custom.max_turns == 80
        assert custom.timeout_seconds == 600

    def test_load_with_both_overrides_and_custom(self):
        load_subagents_config_from_dict(
            {
                "timeout_seconds": 900,
                "agents": {
                    "general-purpose": {"skills": ["web-search"]},
                },
                "custom_agents": {
                    "analysis": {
                        "description": "Analysis",
                        "system_prompt": "Analyze.",
                        "skills": ["data-analysis"],
                    },
                },
            }
        )
        cfg = get_subagents_app_config()
        assert cfg.get_skills_for("general-purpose") == ["web-search"]
        assert cfg.custom_agents["analysis"].skills == ["data-analysis"]


# ---------------------------------------------------------------------------
# Registry: custom agent lookup
# ---------------------------------------------------------------------------


class TestRegistryCustomAgentLookup:
    def teardown_method(self):
        _reset_subagents_config()

    def test_custom_agent_found(self):
        from deerflow.subagents.registry import get_subagent_config

        load_subagents_config_from_dict(
            {
                "custom_agents": {
                    "analysis": {
                        "description": "Data analysis specialist",
                        "system_prompt": "You are a data analysis subagent.",
                        "skills": ["data-analysis"],
                        "tools": ["bash", "read_file"],
                        "max_turns": 80,
                        "timeout_seconds": 600,
                    },
                },
            }
        )
        config = get_subagent_config("analysis")
        assert config is not None
        assert config.name == "analysis"
        assert config.skills == ["data-analysis"]
        assert config.tools == ["bash", "read_file"]
        assert config.max_turns == 80
        assert config.timeout_seconds == 600
        assert config.model == "inherit"

    def test_custom_agent_not_found(self):
        from deerflow.subagents.registry import get_subagent_config

        _reset_subagents_config()
        assert get_subagent_config("nonexistent") is None

    def test_builtin_takes_priority_over_custom(self):
        """If a custom agent has the same name as a builtin, builtin wins."""
        from deerflow.subagents.builtins import BUILTIN_SUBAGENTS
        from deerflow.subagents.registry import get_subagent_config

        load_subagents_config_from_dict(
            {
                "custom_agents": {
                    "general-purpose": {
                        "description": "Custom override attempt",
                        "system_prompt": "Should not be used",
                    },
                },
            }
        )
        config = get_subagent_config("general-purpose")
        # Should get the builtin description, not the custom one
        assert config.description == BUILTIN_SUBAGENTS["general-purpose"].description

    def test_custom_agent_with_override(self):
        """Per-agent overrides also apply to custom agents."""
        from deerflow.subagents.registry import get_subagent_config

        load_subagents_config_from_dict(
            {
                "custom_agents": {
                    "analysis": {
                        "description": "Analysis",
                        "system_prompt": "Analyze.",
                        "timeout_seconds": 600,
                    },
                },
                "agents": {
                    "analysis": {"timeout_seconds": 300, "skills": ["overridden-skill"]},
                },
            }
        )
        config = get_subagent_config("analysis")
        assert config is not None
        assert config.timeout_seconds == 300  # Override applied
        assert config.skills == ["overridden-skill"]  # Override applied


# ---------------------------------------------------------------------------
# Registry: skills override on builtin agents
# ---------------------------------------------------------------------------


class TestRegistrySkillsOverride:
    def teardown_method(self):
        _reset_subagents_config()

    def test_skills_override_applied_to_builtin(self):
        from deerflow.subagents.registry import get_subagent_config

        load_subagents_config_from_dict(
            {
                "agents": {
                    "general-purpose": {"skills": ["web-search", "data-analysis"]},
                },
            }
        )
        config = get_subagent_config("general-purpose")
        assert config.skills == ["web-search", "data-analysis"]

    def test_empty_skills_override(self):
        from deerflow.subagents.registry import get_subagent_config

        load_subagents_config_from_dict(
            {
                "agents": {
                    "bash": {"skills": []},
                },
            }
        )
        config = get_subagent_config("bash")
        assert config.skills == []

    def test_no_skills_override_keeps_default(self):
        from deerflow.subagents.registry import get_subagent_config

        _reset_subagents_config()
        config = get_subagent_config("general-purpose")
        assert config.skills is None  # Default: inherit all

    def test_skills_override_does_not_mutate_builtin(self):
        from deerflow.subagents.builtins import BUILTIN_SUBAGENTS
        from deerflow.subagents.registry import get_subagent_config

        load_subagents_config_from_dict(
            {
                "agents": {
                    "general-purpose": {"skills": ["web-search"]},
                },
            }
        )
        _ = get_subagent_config("general-purpose")
        assert BUILTIN_SUBAGENTS["general-purpose"].skills is None


# ---------------------------------------------------------------------------
# Registry: get_available_subagent_names merges custom types
# ---------------------------------------------------------------------------


class TestRegistryAvailableNames:
    def teardown_method(self):
        _reset_subagents_config()

    def test_includes_builtin_names(self):
        from deerflow.subagents.registry import get_subagent_names

        _reset_subagents_config()
        names = get_subagent_names()
        assert "general-purpose" in names
        assert "bash" in names

    def test_includes_custom_names(self):
        from deerflow.subagents.registry import get_subagent_names

        load_subagents_config_from_dict(
            {
                "custom_agents": {
                    "analysis": {
                        "description": "Analysis",
                        "system_prompt": "Analyze.",
                    },
                    "researcher": {
                        "description": "Research",
                        "system_prompt": "Research.",
                    },
                },
            }
        )
        names = get_subagent_names()
        assert "general-purpose" in names
        assert "bash" in names
        assert "analysis" in names
        assert "researcher" in names

    def test_no_duplicates_when_custom_name_matches_builtin(self):
        from deerflow.subagents.registry import get_subagent_names

        load_subagents_config_from_dict(
            {
                "custom_agents": {
                    "general-purpose": {
                        "description": "Duplicate name",
                        "system_prompt": "test",
                    },
                },
            }
        )
        names = get_subagent_names()
        assert names.count("general-purpose") == 1


# ---------------------------------------------------------------------------
# Registry: list_subagents includes custom agents
# ---------------------------------------------------------------------------


class TestRegistryListSubagentsWithCustom:
    def teardown_method(self):
        _reset_subagents_config()

    def test_list_includes_custom_agents(self):
        from deerflow.subagents.registry import list_subagents

        load_subagents_config_from_dict(
            {
                "custom_agents": {
                    "analysis": {
                        "description": "Analysis",
                        "system_prompt": "Analyze.",
                        "skills": ["data-analysis"],
                    },
                },
            }
        )
        configs = list_subagents()
        names = {c.name for c in configs}
        assert "general-purpose" in names
        assert "bash" in names
        assert "analysis" in names

    def test_list_custom_agent_has_correct_skills(self):
        from deerflow.subagents.registry import list_subagents

        load_subagents_config_from_dict(
            {
                "custom_agents": {
                    "analysis": {
                        "description": "Analysis",
                        "system_prompt": "Analyze.",
                        "skills": ["data-analysis", "visualization"],
                    },
                },
            }
        )
        by_name = {c.name: c for c in list_subagents()}
        assert by_name["analysis"].skills == ["data-analysis", "visualization"]


# ---------------------------------------------------------------------------
# Skills filter passthrough: covered end-to-end in tests/test_task_tool_core_logic.py
# (test_task_tool_inherits_parent_skill_allowlist_for_default_subagent and
# test_task_tool_intersects_parent_and_subagent_skill_allowlists run the real
# task_tool and assert the executor receives the resolved skills). The former
# TestSkillsFilterPassthrough class here re-implemented the passthrough
# expression (`set(config.skills) if ... is not None else None`) in the test
# body — a tautology testing nothing in production — and was deleted.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Executor: skills index injection (P1-1, 2026-09-29 remediation)
# ---------------------------------------------------------------------------

# Same circular-import workaround as tests/test_subagent_executor.py: the real
# executor module must be imported with its heavy dependencies stubbed. The
# real lead prompt module is additionally registered under its canonical name
# so the lazy import inside _load_skill_messages resolves to the real index
# renderer (a plain MagicMock would swallow the assertions below).
_INDEX_MOCKED_MODULES = [
    "deerflow.agents",
    "deerflow.agents.thread_state",
    "deerflow.agents.middlewares",
    "deerflow.agents.middlewares.thread_data_middleware",
    "deerflow.sandbox",
    "deerflow.sandbox.middleware",
    "deerflow.sandbox.security",
    "deerflow.models",
]

_PROMPT_MODULE_NAME = "deerflow.agents.lead_agent.prompt"


def _load_real_prompt_module() -> ModuleType:
    prompt_path = Path(__file__).resolve().parents[1] / "packages" / "harness" / "deerflow" / "agents" / "lead_agent" / "prompt.py"
    spec = importlib.util.spec_from_file_location(_PROMPT_MODULE_NAME, prompt_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestSkillsIndexInjection:
    """Skills are injected as a compact index loaded via read_file, not full text."""

    @pytest.fixture(scope="class", autouse=True)
    def _real_executor_env(self):
        """Import the real executor with circular deps stubbed; undo afterwards."""
        mp = pytest.MonkeyPatch()
        mp.delitem(sys.modules, "deerflow.subagents.executor", raising=False)
        for name in _INDEX_MOCKED_MODULES:
            mp.setitem(sys.modules, name, MagicMock())
        mp.setitem(sys.modules, _PROMPT_MODULE_NAME, _load_real_prompt_module())
        from deerflow.subagents.executor import SubagentExecutor

        self.__class__._RealSubagentExecutor = SubagentExecutor
        yield
        mp.undo()

    @staticmethod
    def _tool(name: str) -> SimpleNamespace:
        return SimpleNamespace(name=name)

    def _make_executor(self, *, skills, tool_names=("read_file", "bash", "write_file"), app_config=None):
        config = SubagentConfig(
            name="test-agent",
            description="Test agent",
            system_prompt="You are a test agent.",
            skills=skills,
        )
        return self._RealSubagentExecutor(
            config=config,
            tools=[self._tool(n) for n in tool_names],
            app_config=app_config,
        )

    @staticmethod
    def _make_skills_app_config(tmp_path: Path, skill_names: tuple[str, ...]):
        """AppConfig backed by a tmp skills tree — no real (gitignored)
        config.yaml needed, so the load_skills() tests run in CI too.

        The executor threads this object into get_skills_prompt_section(
        app_config=...), which bypasses the global enabled-skills cache and
        resolves the scan path from config.skills.path. Enabled state falls
        back to default-True for skill names absent from extensions_config.json
        (these fixture names never appear in a real one).
        """
        from deerflow.config.app_config import AppConfig
        from deerflow.config.sandbox_config import SandboxConfig
        from deerflow.config.skills_config import SkillsConfig

        skills_root = tmp_path / "skills"
        for name in skill_names:
            skill_dir = skills_root / "public" / name
            skill_dir.mkdir(parents=True, exist_ok=True)
            (skill_dir / "SKILL.md").write_text(
                f"---\nname: {name}\ndescription: CI fixture skill {name}\n---\n\n# {name}\n",
                encoding="utf-8",
            )
        return AppConfig(sandbox=SandboxConfig(use="test"), skills=SkillsConfig(path=str(skills_root)))

    @staticmethod
    def _skill_messages(state) -> list[SystemMessage]:
        return [m for m in state["messages"] if isinstance(m, SystemMessage) and isinstance(m.content, str) and m.content.startswith("<skill_system>")]

    @pytest.mark.anyio
    async def test_none_skills_injects_single_index_message(self, tmp_path):
        """skills=None: initial state carries exactly one index SystemMessage."""
        from deerflow.skills.loader import load_skills

        app_config = self._make_skills_app_config(tmp_path, ("ci-idx-skill-a", "ci-idx-skill-b"))
        executor = self._make_executor(skills=None, app_config=app_config)
        state = await executor._build_initial_state("test task")

        skill_msgs = self._skill_messages(state)
        assert len(skill_msgs) == 1
        content = skill_msgs[0].content
        assert "<available_skills>" in content

        enabled_names = [s.name for s in load_skills(enabled_only=True, app_config=app_config)]
        assert enabled_names == ["ci-idx-skill-a", "ci-idx-skill-b"]
        assert any(f"<name>{name}</name>" in content for name in enabled_names)

        # Index only — no SKILL.md full-text markers (old <skill name="...">
        # wrapper or a frontmatter block).
        assert '<skill name="' not in content
        assert "---\nname:" not in content
        assert len(content) < 20_000

    @pytest.mark.anyio
    async def test_skills_whitelist_indexes_only_that_skill(self, tmp_path):
        """skills=[one]: the index lists that skill and nothing else."""
        app_config = self._make_skills_app_config(tmp_path, ("ci-idx-skill-a", "ci-idx-skill-b"))
        executor = self._make_executor(skills=["ci-idx-skill-a"], app_config=app_config)
        messages = await executor._load_skill_messages()

        assert len(messages) == 1
        content = messages[0].content
        assert "<available_skills>" in content
        assert "<name>ci-idx-skill-a</name>" in content
        assert content.count("<name>") == 1

    @pytest.mark.anyio
    async def test_empty_skills_injects_nothing(self):
        """skills=[]: no skill messages at all (existing short-circuit)."""
        executor = self._make_executor(skills=[])
        state = await executor._build_initial_state("test task")
        assert self._skill_messages(state) == []

    @pytest.mark.anyio
    async def test_no_read_file_tool_skips_index_with_warning(self, caplog):
        """Filtered tools without read_file: skip injection and say why."""
        executor = self._make_executor(skills=None, tool_names=("bash", "write_file"))
        with caplog.at_level(logging.WARNING, logger="deerflow.subagents.executor"):
            messages = await executor._load_skill_messages()

        assert messages == []
        assert "no read_file tool" in caplog.text
