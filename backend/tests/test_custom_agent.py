"""Tests for custom agent support."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest
import yaml
from fastapi.testclient import TestClient

from app.gateway.auth.models import User
from app.gateway.authz import _ALL_PERMISSIONS, AuthContext
from deerflow.config.agents_api_config import AgentsApiConfig, get_agents_api_config, set_agents_api_config

# Fixed user IDs for deterministic cross-user access-control tests
ALICE_ID = "00000000-0000-0000-0000-000000000001"
BOB_ID = "00000000-0000-0000-0000-000000000002"
ADMIN_ID = "00000000-0000-0000-0000-000000000003"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_paths(base_dir: Path):
    """Return a Paths instance pointing to base_dir."""
    from deerflow.config.paths import Paths

    return Paths(base_dir=base_dir)


def _write_agent(base_dir: Path, name: str, config: dict, soul: str = "You are helpful.") -> None:
    """Write an agent directory with config.yaml and SOUL.md."""
    agent_dir = base_dir / "agents" / name
    agent_dir.mkdir(parents=True, exist_ok=True)

    config_copy = dict(config)
    if "name" not in config_copy:
        config_copy["name"] = name

    with open(agent_dir / "config.yaml", "w") as f:
        yaml.dump(config_copy, f)

    (agent_dir / "SOUL.md").write_text(soul, encoding="utf-8")


# ===========================================================================
# 1. Paths class – agent path methods
# ===========================================================================


class TestPaths:
    def test_agents_dir(self, tmp_path):
        paths = _make_paths(tmp_path)
        assert paths.agents_dir == tmp_path / "agents"

    def test_agent_dir(self, tmp_path):
        paths = _make_paths(tmp_path)
        assert paths.agent_dir("code-reviewer") == tmp_path / "agents" / "code-reviewer"

    def test_agent_memory_file(self, tmp_path):
        paths = _make_paths(tmp_path)
        assert paths.agent_memory_file("code-reviewer") == tmp_path / "agents" / "code-reviewer" / "memory.json"

    def test_user_md_file(self, tmp_path):
        paths = _make_paths(tmp_path)
        assert paths.user_md_file == tmp_path / "USER.md"

    def test_paths_are_different_from_global(self, tmp_path):
        paths = _make_paths(tmp_path)
        assert paths.memory_file != paths.agent_memory_file("my-agent")
        assert paths.memory_file == tmp_path / "memory.json"
        assert paths.agent_memory_file("my-agent") == tmp_path / "agents" / "my-agent" / "memory.json"


# ===========================================================================
# 2. AgentConfig – Pydantic parsing
# ===========================================================================


class TestAgentConfig:
    def test_minimal_config(self):
        from deerflow.config.agents_config import AgentConfig

        cfg = AgentConfig(name="my-agent")
        assert cfg.name == "my-agent"
        assert cfg.description == ""
        assert cfg.model is None
        assert cfg.tool_groups is None

    def test_full_config(self):
        from deerflow.config.agents_config import AgentConfig

        cfg = AgentConfig(
            name="code-reviewer",
            description="Specialized for code review",
            model="deepseek-v3",
            tool_groups=["file:read", "bash"],
        )
        assert cfg.name == "code-reviewer"
        assert cfg.model == "deepseek-v3"
        assert cfg.tool_groups == ["file:read", "bash"]

    def test_config_from_dict(self):
        from deerflow.config.agents_config import AgentConfig

        data = {"name": "test-agent", "description": "A test", "model": "gpt-4"}
        cfg = AgentConfig(**data)
        assert cfg.name == "test-agent"
        assert cfg.model == "gpt-4"
        assert cfg.tool_groups is None


# ===========================================================================
# 3. load_agent_config
# ===========================================================================


class TestLoadAgentConfig:
    def test_load_valid_config(self, tmp_path):
        config_dict = {"name": "code-reviewer", "description": "Code review agent", "model": "deepseek-v3"}
        _write_agent(tmp_path, "code-reviewer", config_dict)

        with patch("deerflow.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from deerflow.config.agents_config import load_agent_config

            cfg = load_agent_config("code-reviewer")

        assert cfg.name == "code-reviewer"
        assert cfg.description == "Code review agent"
        assert cfg.model == "deepseek-v3"

    def test_load_missing_agent_raises(self, tmp_path):
        with patch("deerflow.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from deerflow.config.agents_config import load_agent_config

            with pytest.raises(FileNotFoundError):
                load_agent_config("nonexistent-agent")

    def test_load_missing_config_yaml_raises(self, tmp_path):
        # Create directory without config.yaml
        (tmp_path / "agents" / "broken-agent").mkdir(parents=True)

        with patch("deerflow.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from deerflow.config.agents_config import load_agent_config

            with pytest.raises(FileNotFoundError):
                load_agent_config("broken-agent")

    def test_load_config_infers_name_from_dir(self, tmp_path):
        """Config without 'name' field should use directory name."""
        agent_dir = tmp_path / "agents" / "inferred-name"
        agent_dir.mkdir(parents=True)
        (agent_dir / "config.yaml").write_text("description: My agent\n")
        (agent_dir / "SOUL.md").write_text("Hello")

        with patch("deerflow.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from deerflow.config.agents_config import load_agent_config

            cfg = load_agent_config("inferred-name")

        assert cfg.name == "inferred-name"

    def test_load_config_with_tool_groups(self, tmp_path):
        config_dict = {"name": "restricted", "tool_groups": ["file:read", "file:write"]}
        _write_agent(tmp_path, "restricted", config_dict)

        with patch("deerflow.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from deerflow.config.agents_config import load_agent_config

            cfg = load_agent_config("restricted")

        assert cfg.tool_groups == ["file:read", "file:write"]

    def test_load_config_with_skills_empty_list(self, tmp_path):
        config_dict = {"name": "no-skills-agent", "skills": []}
        _write_agent(tmp_path, "no-skills-agent", config_dict)

        with patch("deerflow.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from deerflow.config.agents_config import load_agent_config

            cfg = load_agent_config("no-skills-agent")

        assert cfg.skills == []

    def test_load_config_with_skills_omitted(self, tmp_path):
        # SEC-C3/M14（批次 2 项 2.1）：缺省 skills 不再回落 None=全部技能，
        # 加载侧收敛为 []（与 POST 缺省一致）——旧行为是跨用户技能注入面。
        config_dict = {"name": "default-skills-agent"}
        _write_agent(tmp_path, "default-skills-agent", config_dict)

        with patch("deerflow.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from deerflow.config.agents_config import load_agent_config

            cfg = load_agent_config("default-skills-agent")

        assert cfg.skills == []

    def test_legacy_prompt_file_field_ignored(self, tmp_path):
        """Unknown fields like the old prompt_file should be silently ignored."""
        agent_dir = tmp_path / "agents" / "legacy-agent"
        agent_dir.mkdir(parents=True)
        (agent_dir / "config.yaml").write_text("name: legacy-agent\nprompt_file: system.md\n")
        (agent_dir / "SOUL.md").write_text("Soul content")

        with patch("deerflow.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from deerflow.config.agents_config import load_agent_config

            cfg = load_agent_config("legacy-agent")

        assert cfg.name == "legacy-agent"


# ===========================================================================
# 4. load_agent_soul
# ===========================================================================


class TestLoadAgentSoul:
    def test_reads_soul_file(self, tmp_path):
        expected_soul = "You are a specialized code review expert."
        _write_agent(tmp_path, "code-reviewer", {"name": "code-reviewer"}, soul=expected_soul)

        with patch("deerflow.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from deerflow.config.agents_config import AgentConfig, load_agent_soul

            cfg = AgentConfig(name="code-reviewer")
            soul = load_agent_soul(cfg.name)

        assert soul == expected_soul

    def test_missing_soul_file_returns_none(self, tmp_path):
        agent_dir = tmp_path / "agents" / "no-soul"
        agent_dir.mkdir(parents=True)
        (agent_dir / "config.yaml").write_text("name: no-soul\n")
        # No SOUL.md created

        with patch("deerflow.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from deerflow.config.agents_config import AgentConfig, load_agent_soul

            cfg = AgentConfig(name="no-soul")
            soul = load_agent_soul(cfg.name)

        assert soul is None

    def test_empty_soul_file_returns_none(self, tmp_path):
        agent_dir = tmp_path / "agents" / "empty-soul"
        agent_dir.mkdir(parents=True)
        (agent_dir / "config.yaml").write_text("name: empty-soul\n")
        (agent_dir / "SOUL.md").write_text("   \n   ")

        with patch("deerflow.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from deerflow.config.agents_config import AgentConfig, load_agent_soul

            cfg = AgentConfig(name="empty-soul")
            soul = load_agent_soul(cfg.name)

        assert soul is None


# ===========================================================================
# 5. list_custom_agents
# ===========================================================================


class TestListCustomAgents:
    def test_empty_when_no_agents_dir(self, tmp_path):
        with patch("deerflow.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from deerflow.config.agents_config import list_custom_agents

            agents = list_custom_agents()

        assert agents == []

    def test_discovers_multiple_agents(self, tmp_path):
        _write_agent(tmp_path, "agent-a", {"name": "agent-a"})
        _write_agent(tmp_path, "agent-b", {"name": "agent-b", "description": "B"})

        with patch("deerflow.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from deerflow.config.agents_config import list_custom_agents

            agents = list_custom_agents()

        names = [a.name for a in agents]
        assert "agent-a" in names
        assert "agent-b" in names

    def test_skips_dirs_without_config_yaml(self, tmp_path):
        # Valid agent
        _write_agent(tmp_path, "valid-agent", {"name": "valid-agent"})
        # Invalid dir (no config.yaml)
        (tmp_path / "agents" / "invalid-dir").mkdir(parents=True)

        with patch("deerflow.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from deerflow.config.agents_config import list_custom_agents

            agents = list_custom_agents()

        assert len(agents) == 1
        assert agents[0].name == "valid-agent"

    def test_skips_non_directory_entries(self, tmp_path):
        # Create the agents dir with a file (not a dir)
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir(parents=True)
        (agents_dir / "not-a-dir.txt").write_text("hello")
        _write_agent(tmp_path, "real-agent", {"name": "real-agent"})

        with patch("deerflow.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from deerflow.config.agents_config import list_custom_agents

            agents = list_custom_agents()

        assert len(agents) == 1
        assert agents[0].name == "real-agent"

    def test_returns_sorted_by_name(self, tmp_path):
        _write_agent(tmp_path, "z-agent", {"name": "z-agent"})
        _write_agent(tmp_path, "a-agent", {"name": "a-agent"})
        _write_agent(tmp_path, "m-agent", {"name": "m-agent"})

        with patch("deerflow.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from deerflow.config.agents_config import list_custom_agents

            agents = list_custom_agents()

        names = [a.name for a in agents]
        assert names == sorted(names)


# ===========================================================================
# 7. Memory isolation: _get_memory_file_path
# ===========================================================================


class TestMemoryFilePath:
    def test_global_memory_path(self, tmp_path):
        """None agent_name should return global memory file."""
        from deerflow.agents.memory.storage import FileMemoryStorage
        from deerflow.config.memory_config import MemoryConfig

        with (
            patch("deerflow.agents.memory.storage.get_paths", return_value=_make_paths(tmp_path)),
            patch("deerflow.agents.memory.storage.get_memory_config", return_value=MemoryConfig(storage_path="")),
        ):
            storage = FileMemoryStorage()
            path = storage._get_memory_file_path(None)
        assert path == tmp_path / "memory.json"

    def test_agent_memory_path(self, tmp_path):
        """Providing agent_name should return per-agent memory file."""
        from deerflow.agents.memory.storage import FileMemoryStorage
        from deerflow.config.memory_config import MemoryConfig

        with (
            patch("deerflow.agents.memory.storage.get_paths", return_value=_make_paths(tmp_path)),
            patch("deerflow.agents.memory.storage.get_memory_config", return_value=MemoryConfig(storage_path="")),
        ):
            storage = FileMemoryStorage()
            path = storage._get_memory_file_path("code-reviewer")
        assert path == tmp_path / "agents" / "code-reviewer" / "memory.json"

    def test_different_paths_for_different_agents(self, tmp_path):
        from deerflow.agents.memory.storage import FileMemoryStorage
        from deerflow.config.memory_config import MemoryConfig

        with (
            patch("deerflow.agents.memory.storage.get_paths", return_value=_make_paths(tmp_path)),
            patch("deerflow.agents.memory.storage.get_memory_config", return_value=MemoryConfig(storage_path="")),
        ):
            storage = FileMemoryStorage()
            path_global = storage._get_memory_file_path(None)
            path_a = storage._get_memory_file_path("agent-a")
            path_b = storage._get_memory_file_path("agent-b")

        assert path_global != path_a
        assert path_global != path_b
        assert path_a != path_b


# ===========================================================================
# 8. Gateway API – Agents endpoints
# ===========================================================================


def _make_test_app(tmp_path: Path):
    """Create a FastAPI app with the agents router, patching paths to tmp_path."""
    from fastapi import FastAPI

    from app.gateway.routers.agents import router

    app = FastAPI()
    app.include_router(router)
    return app


@pytest.fixture()
def agent_client(tmp_path):
    """TestClient with agents router, using tmp_path as base_dir.

    Simulates an authenticated user so that create/update/delete pass auth.
    """
    import app.gateway.routers.agents as agents_router

    paths_instance = _make_paths(tmp_path)
    previous_config = AgentsApiConfig(**get_agents_api_config().model_dump())
    test_user = User(email="test-agent@example.com", system_role="user")
    test_auth = AuthContext(user=test_user, permissions=_ALL_PERMISSIONS)

    with (
        patch("deerflow.config.agents_config.get_paths", return_value=paths_instance),
        patch.object(agents_router, "get_paths", return_value=paths_instance),
        patch("app.gateway.routers.agents._authenticate", new_callable=AsyncMock, return_value=test_auth),
    ):
        set_agents_api_config(AgentsApiConfig(enabled=True))
        try:
            app = _make_test_app(tmp_path)
            with TestClient(app) as client:
                client._tmp_path = tmp_path  # type: ignore[attr-defined]
                yield client
        finally:
            set_agents_api_config(previous_config)


@pytest.fixture()
def agent_admin_client(tmp_path):
    """TestClient with agents router, using tmp_path as base_dir.

    Simulates an authenticated user so that create/update/delete pass auth.
    """
    import app.gateway.routers.agents as agents_router

    paths_instance = _make_paths(tmp_path)
    previous_config = AgentsApiConfig(**get_agents_api_config().model_dump())
    # SEC-M22（批次 2 项 2.9）：legacy 无主 agent 改/删需 admin。
    test_user = User(email="test-agent-admin@example.com", system_role="admin")
    test_auth = AuthContext(user=test_user, permissions=_ALL_PERMISSIONS)

    with (
        patch("deerflow.config.agents_config.get_paths", return_value=paths_instance),
        patch.object(agents_router, "get_paths", return_value=paths_instance),
        patch("app.gateway.routers.agents._authenticate", new_callable=AsyncMock, return_value=test_auth),
    ):
        set_agents_api_config(AgentsApiConfig(enabled=True))
        try:
            app = _make_test_app(tmp_path)
            with TestClient(app) as client:
                client._tmp_path = tmp_path  # type: ignore[attr-defined]
                yield client
        finally:
            set_agents_api_config(previous_config)


@pytest.fixture()
def disabled_agent_client(tmp_path):
    """TestClient with agents router while the management API is disabled."""
    import app.gateway.routers.agents as agents_router

    paths_instance = _make_paths(tmp_path)
    previous_config = AgentsApiConfig(**get_agents_api_config().model_dump())

    with patch("deerflow.config.agents_config.get_paths", return_value=paths_instance), patch.object(agents_router, "get_paths", return_value=paths_instance):
        set_agents_api_config(AgentsApiConfig(enabled=False))
        try:
            app = _make_test_app(tmp_path)
            with TestClient(app) as client:
                yield client
        finally:
            set_agents_api_config(previous_config)


class TestAgentsAPI:
    def test_list_agents_empty(self, agent_client):
        response = agent_client.get("/api/agents")
        assert response.status_code == 200
        data = response.json()
        assert data["agents"] == []

    def test_create_agent(self, agent_client):
        payload = {
            "name": "code-reviewer",
            "description": "Reviews code",
            "soul": "You are a code reviewer.",
        }
        response = agent_client.post("/api/agents", json=payload)
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "code-reviewer"
        assert data["description"] == "Reviews code"
        assert data["soul"] == "You are a code reviewer."

    def test_create_agent_invalid_name(self, agent_client):
        payload = {"name": "Code Reviewer!", "soul": "test"}
        response = agent_client.post("/api/agents", json=payload)
        assert response.status_code == 422

    def test_create_duplicate_agent_409(self, agent_client):
        payload = {"name": "my-agent", "soul": "test"}
        agent_client.post("/api/agents", json=payload)

        # Second create should fail
        response = agent_client.post("/api/agents", json=payload)
        assert response.status_code == 409

    def test_list_agents_after_create(self, agent_client):
        agent_client.post("/api/agents", json={"name": "agent-one", "soul": "p1"})
        agent_client.post("/api/agents", json={"name": "agent-two", "soul": "p2"})

        response = agent_client.get("/api/agents")
        assert response.status_code == 200
        names = [a["name"] for a in response.json()["agents"]]
        assert "agent-one" in names
        assert "agent-two" in names

    def test_list_agents_includes_soul(self, agent_client):
        agent_client.post("/api/agents", json={"name": "soul-agent", "soul": "My soul content"})

        response = agent_client.get("/api/agents")
        assert response.status_code == 200
        agents = response.json()["agents"]
        soul_agent = next(a for a in agents if a["name"] == "soul-agent")
        assert soul_agent["soul"] == "My soul content"

    def test_get_agent(self, agent_client):
        agent_client.post("/api/agents", json={"name": "test-agent", "soul": "Hello world"})

        response = agent_client.get("/api/agents/test-agent")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "test-agent"
        assert data["soul"] == "Hello world"

    def test_get_missing_agent_404(self, agent_client):
        response = agent_client.get("/api/agents/nonexistent")
        assert response.status_code == 404

    def test_update_agent_soul(self, agent_client):
        agent_client.post("/api/agents", json={"name": "update-me", "soul": "original"})

        response = agent_client.put("/api/agents/update-me", json={"soul": "updated"})
        assert response.status_code == 200
        assert response.json()["soul"] == "updated"

    def test_update_agent_description(self, agent_client):
        agent_client.post("/api/agents", json={"name": "desc-agent", "description": "old desc", "soul": "p"})

        response = agent_client.put("/api/agents/desc-agent", json={"description": "new desc"})
        assert response.status_code == 200
        assert response.json()["description"] == "new desc"

    def test_update_missing_agent_404(self, agent_client):
        response = agent_client.put("/api/agents/ghost-agent", json={"soul": "new"})
        assert response.status_code == 404

    def test_delete_agent(self, agent_client):
        agent_client.post("/api/agents", json={"name": "del-me", "soul": "bye"})

        response = agent_client.delete("/api/agents/del-me")
        assert response.status_code == 204

        # Verify it's gone
        response = agent_client.get("/api/agents/del-me")
        assert response.status_code == 404

    def test_delete_missing_agent_404(self, agent_client):
        response = agent_client.delete("/api/agents/does-not-exist")
        assert response.status_code == 404

    def test_create_agent_with_model_and_tool_groups(self, agent_client):
        # SEC-C2 (2026-09-02): tool_groups limited to the custom-agent
        # allowlist — previously ["file:read", "bash"] was accepted verbatim
        # (self-service RCE). Allowed groups still persist.
        payload = {
            "name": "specialized",
            "description": "Specialized agent",
            "model": "deepseek-v3",
            "tool_groups": ["web"],
            "soul": "You are specialized.",
        }
        response = agent_client.post("/api/agents", json=payload)
        assert response.status_code == 201
        data = response.json()
        assert data["model"] == "deepseek-v3"
        assert data["tool_groups"] == ["web"]

    def test_create_persists_files_on_disk(self, agent_client, tmp_path):
        agent_client.post("/api/agents", json={"name": "disk-check", "soul": "disk soul"})

        agent_dir = tmp_path / "agents" / "disk-check"
        assert agent_dir.exists()
        assert (agent_dir / "config.yaml").exists()
        assert (agent_dir / "SOUL.md").exists()
        assert (agent_dir / "SOUL.md").read_text() == "disk soul"

    def test_delete_removes_files_from_disk(self, agent_client, tmp_path):
        agent_client.post("/api/agents", json={"name": "remove-me", "soul": "bye"})
        agent_dir = tmp_path / "agents" / "remove-me"
        assert agent_dir.exists()

        agent_client.delete("/api/agents/remove-me")
        assert not agent_dir.exists()


# ===========================================================================
# 9. Gateway API – User Profile endpoints
# ===========================================================================


class TestUserProfileAPI:
    def test_get_user_profile_empty(self, agent_client):
        response = agent_client.get("/api/user-profile")
        assert response.status_code == 200
        assert response.json()["content"] is None

    def test_put_user_profile(self, agent_client, tmp_path):
        content = "# User Profile\n\nI am a developer."
        response = agent_client.put("/api/user-profile", json={"content": content})
        assert response.status_code == 200
        assert response.json()["content"] == content

        # File should be written to disk
        user_md = tmp_path / "USER.md"
        assert user_md.exists()
        assert user_md.read_text(encoding="utf-8") == content

    def test_get_user_profile_after_put(self, agent_client):
        content = "# Profile\n\nI work on data science."
        agent_client.put("/api/user-profile", json={"content": content})

        response = agent_client.get("/api/user-profile")
        assert response.status_code == 200
        assert response.json()["content"] == content

    def test_put_empty_user_profile_returns_none(self, agent_client):
        response = agent_client.put("/api/user-profile", json={"content": ""})
        assert response.status_code == 200
        assert response.json()["content"] is None


class TestAgentsApiDisabled:
    def test_agents_list_returns_200(self, disabled_agent_client):
        """Read endpoints are available even when agents_api is disabled."""
        response = disabled_agent_client.get("/api/agents")
        assert response.status_code == 200

    def test_agent_get_returns_404(self, disabled_agent_client):
        """Read endpoints are available even when agents_api is disabled,
        but non-existent agents still return 404."""
        response = disabled_agent_client.get("/api/agents/example-agent")
        assert response.status_code == 404

    def test_agent_name_check_returns_403(self, disabled_agent_client):
        response = disabled_agent_client.get("/api/agents/check", params={"name": "example-agent"})
        assert response.status_code == 403

    def test_agent_create_returns_403(self, disabled_agent_client):
        response = disabled_agent_client.post("/api/agents", json={"name": "example-agent", "soul": "blocked"})
        assert response.status_code == 403

    def test_agent_update_returns_403(self, disabled_agent_client):
        response = disabled_agent_client.put("/api/agents/example-agent", json={"description": "blocked"})
        assert response.status_code == 403

    def test_agent_delete_returns_403(self, disabled_agent_client):
        response = disabled_agent_client.delete("/api/agents/example-agent")
        assert response.status_code == 403

    def test_user_profile_routes_return_403(self, disabled_agent_client):
        get_response = disabled_agent_client.get("/api/user-profile")
        put_response = disabled_agent_client.put("/api/user-profile", json={"content": "blocked"})

        assert get_response.status_code == 403
        assert put_response.status_code == 403


# ===========================================================================
# 10. Access control – multi-user visibility tests
# ===========================================================================


@pytest.fixture()
def alice_client(tmp_path):
    """TestClient authenticated as Alice (regular user)."""
    import app.gateway.routers.agents as agents_router

    paths_instance = _make_paths(tmp_path)
    previous_config = AgentsApiConfig(**get_agents_api_config().model_dump())
    alice = User(id=UUID(ALICE_ID), email="alice@example.com", system_role="user")
    alice_auth = AuthContext(user=alice, permissions=_ALL_PERMISSIONS)

    with (
        patch("deerflow.config.agents_config.get_paths", return_value=paths_instance),
        patch.object(agents_router, "get_paths", return_value=paths_instance),
        patch("app.gateway.routers.agents._authenticate", new_callable=AsyncMock, return_value=alice_auth),
    ):
        set_agents_api_config(AgentsApiConfig(enabled=True))
        try:
            app = _make_test_app(tmp_path)
            with TestClient(app) as client:
                client._tmp_path = tmp_path  # type: ignore[attr-defined]
                client._alice = alice  # type: ignore[attr-defined]
                yield client
        finally:
            set_agents_api_config(previous_config)


@pytest.fixture()
def bob_client(tmp_path):
    """TestClient authenticated as Bob (different regular user)."""
    import app.gateway.routers.agents as agents_router

    paths_instance = _make_paths(tmp_path)
    previous_config = AgentsApiConfig(**get_agents_api_config().model_dump())
    bob = User(id=UUID(BOB_ID), email="bob@example.com", system_role="user")
    bob_auth = AuthContext(user=bob, permissions=_ALL_PERMISSIONS)

    with (
        patch("deerflow.config.agents_config.get_paths", return_value=paths_instance),
        patch.object(agents_router, "get_paths", return_value=paths_instance),
        patch("app.gateway.routers.agents._authenticate", new_callable=AsyncMock, return_value=bob_auth),
    ):
        set_agents_api_config(AgentsApiConfig(enabled=True))
        try:
            app = _make_test_app(tmp_path)
            with TestClient(app) as client:
                client._tmp_path = tmp_path  # type: ignore[attr-defined]
                client._bob = bob  # type: ignore[attr-defined]
                yield client
        finally:
            set_agents_api_config(previous_config)


@pytest.fixture()
def admin_client(tmp_path):
    """TestClient authenticated as Admin."""
    import app.gateway.routers.agents as agents_router

    paths_instance = _make_paths(tmp_path)
    previous_config = AgentsApiConfig(**get_agents_api_config().model_dump())
    admin = User(id=UUID(ADMIN_ID), email="admin@example.com", system_role="admin")
    admin_auth = AuthContext(user=admin, permissions=_ALL_PERMISSIONS)

    with (
        patch("deerflow.config.agents_config.get_paths", return_value=paths_instance),
        patch.object(agents_router, "get_paths", return_value=paths_instance),
        patch("app.gateway.routers.agents._authenticate", new_callable=AsyncMock, return_value=admin_auth),
    ):
        set_agents_api_config(AgentsApiConfig(enabled=True))
        try:
            app = _make_test_app(tmp_path)
            with TestClient(app) as client:
                client._tmp_path = tmp_path  # type: ignore[attr-defined]
                client._admin = admin  # type: ignore[attr-defined]
                yield client
        finally:
            set_agents_api_config(previous_config)


@pytest.fixture()
def anon_client(tmp_path):
    """TestClient without authentication."""
    import app.gateway.routers.agents as agents_router

    paths_instance = _make_paths(tmp_path)
    previous_config = AgentsApiConfig(**get_agents_api_config().model_dump())
    anon_auth = AuthContext(user=None, permissions=[])

    with (
        patch("deerflow.config.agents_config.get_paths", return_value=paths_instance),
        patch.object(agents_router, "get_paths", return_value=paths_instance),
        patch("app.gateway.routers.agents._authenticate", new_callable=AsyncMock, return_value=anon_auth),
    ):
        set_agents_api_config(AgentsApiConfig(enabled=True))
        try:
            app = _make_test_app(tmp_path)
            with TestClient(app) as client:
                client._tmp_path = tmp_path  # type: ignore[attr-defined]
                yield client
        finally:
            set_agents_api_config(previous_config)


class TestAgentAccessControl:
    """Tests for multi-user agent visibility and modification permissions."""

    def test_list_hides_other_users_custom_agents(self, bob_client, tmp_path):
        """Bob's list should not include Alice's custom agents."""
        _write_agent(
            tmp_path,
            "alice-bot",
            {"name": "alice-bot", "category": "custom", "created_by": ALICE_ID, "created_at": "2026-01-01T00:00:00+00:00"},
        )

        resp = bob_client.get("/api/agents")
        assert resp.status_code == 200
        names = [a["name"] for a in resp.json()["agents"]]
        assert "alice-bot" not in names

    def test_list_shows_own_custom_agents(self, alice_client, tmp_path):
        """Alice sees her own custom agents in the list."""
        alice_client.post("/api/agents", json={"name": "my-bot", "soul": "My bot"})

        resp = alice_client.get("/api/agents")
        assert resp.status_code == 200
        names = [a["name"] for a in resp.json()["agents"]]
        assert "my-bot" in names

    def test_list_shows_public_agents_to_everyone(self, bob_client, tmp_path):
        """Public agents appear in list for all users."""
        _write_agent(
            tmp_path,
            "public-bot",
            {"name": "public-bot", "category": "public", "created_by": None, "created_at": "2026-01-01T00:00:00+00:00"},
        )

        resp = bob_client.get("/api/agents")
        assert resp.status_code == 200
        names = [a["name"] for a in resp.json()["agents"]]
        assert "public-bot" in names

    def test_list_anon_sees_only_public(self, anon_client, tmp_path):
        """Anonymous (unauthenticated) users only see public agents."""
        _write_agent(
            tmp_path,
            "public-bot",
            {"name": "public-bot", "category": "public", "created_by": None, "created_at": "2026-01-01T00:00:00+00:00"},
        )
        _write_agent(
            tmp_path,
            "some-custom",
            {"name": "some-custom", "category": "custom", "created_by": "other-user-id"},
        )

        resp = anon_client.get("/api/agents")
        assert resp.status_code == 200
        names = [a["name"] for a in resp.json()["agents"]]
        assert "public-bot" in names
        assert "some-custom" not in names

    def test_get_other_users_custom_agent_returns_404(self, bob_client, tmp_path):
        """Bob cannot get details of Alice's custom agent."""
        _write_agent(
            tmp_path,
            "alice-secret",
            {"name": "alice-secret", "category": "custom", "created_by": ALICE_ID, "created_at": "2026-01-01T00:00:00+00:00"},
        )

        resp = bob_client.get("/api/agents/alice-secret")
        assert resp.status_code == 404

    def test_get_own_custom_agent_works(self, alice_client, tmp_path):
        """Alice can get details of her own custom agent."""
        alice_client.post("/api/agents", json={"name": "my-tool", "soul": "My tool"})

        resp = alice_client.get("/api/agents/my-tool")
        assert resp.status_code == 200
        assert resp.json()["name"] == "my-tool"

    def test_get_public_agent_works_for_all(self, bob_client, tmp_path):
        """Public agents are accessible to all authenticated users."""
        _write_agent(
            tmp_path,
            "shared-bot",
            {"name": "shared-bot", "category": "public", "created_by": None, "created_at": "2026-01-01T00:00:00+00:00"},
        )

        resp = bob_client.get("/api/agents/shared-bot")
        assert resp.status_code == 200
        assert resp.json()["name"] == "shared-bot"

    def test_update_other_users_custom_agent_returns_404(self, bob_client, tmp_path):
        """Bob cannot update Alice's custom agent."""
        _write_agent(
            tmp_path,
            "alice-agent",
            {"name": "alice-agent", "category": "custom", "created_by": ALICE_ID, "created_at": "2026-01-01T00:00:00+00:00"},
        )

        resp = bob_client.put("/api/agents/alice-agent", json={"description": "Hacked!"})
        assert resp.status_code == 404

    def test_update_public_agent_returns_403_for_non_admin(self, alice_client, tmp_path):
        """Regular users cannot update public agents."""
        _write_agent(
            tmp_path,
            "public-bot",
            {"name": "public-bot", "category": "public", "created_by": None, "created_at": "2026-01-01T00:00:00+00:00"},
        )

        resp = alice_client.put("/api/agents/public-bot", json={"description": "Changed"})
        assert resp.status_code == 403

    def test_update_own_custom_agent_works(self, alice_client, tmp_path):
        """Alice can update her own custom agent."""
        alice_client.post("/api/agents", json={"name": "my-agent", "soul": "Original"})

        resp = alice_client.put("/api/agents/my-agent", json={"description": "Updated"})
        assert resp.status_code == 200
        assert resp.json()["description"] == "Updated"

    def test_admin_can_update_public_agent(self, admin_client, tmp_path):
        """Admin can update public agents."""
        _write_agent(
            tmp_path,
            "public-bot",
            {"name": "public-bot", "category": "public", "created_by": None, "created_at": "2026-01-01T00:00:00+00:00"},
        )

        resp = admin_client.put("/api/agents/public-bot", json={"description": "Admin update"})
        assert resp.status_code == 200
        assert resp.json()["description"] == "Admin update"

    def test_admin_can_see_all_custom_agents(self, admin_client, tmp_path):
        """Admin sees all agents including other users' custom agents."""
        _write_agent(
            tmp_path,
            "alice-private",
            {"name": "alice-private", "category": "custom", "created_by": ALICE_ID, "created_at": "2026-01-01T00:00:00+00:00"},
        )

        resp = admin_client.get("/api/agents")
        assert resp.status_code == 200
        names = [a["name"] for a in resp.json()["agents"]]
        assert "alice-private" in names

    def test_admin_can_get_other_users_custom_agent(self, admin_client, tmp_path):
        """Admin can get details of any custom agent."""
        _write_agent(
            tmp_path,
            "alice-private",
            {"name": "alice-private", "category": "custom", "created_by": ALICE_ID, "created_at": "2026-01-01T00:00:00+00:00"},
        )

        resp = admin_client.get("/api/agents/alice-private")
        assert resp.status_code == 200
        assert resp.json()["name"] == "alice-private"

    def test_admin_can_update_other_users_custom_agent(self, admin_client, tmp_path):
        """Admin can update any custom agent."""
        _write_agent(
            tmp_path,
            "alice-agent",
            {"name": "alice-agent", "category": "custom", "created_by": ALICE_ID, "created_at": "2026-01-01T00:00:00+00:00"},
        )

        resp = admin_client.put("/api/agents/alice-agent", json={"description": "Admin changed this"})
        assert resp.status_code == 200
        assert resp.json()["description"] == "Admin changed this"

    def test_delete_other_users_custom_agent_returns_404(self, bob_client, tmp_path):
        """Bob cannot delete Alice's custom agent (404, not 403 to avoid leaking existence)."""
        _write_agent(
            tmp_path,
            "alice-agent",
            {"name": "alice-agent", "category": "custom", "created_by": ALICE_ID, "created_at": "2026-01-01T00:00:00+00:00"},
        )

        resp = bob_client.delete("/api/agents/alice-agent")
        assert resp.status_code == 404

    def test_admin_can_delete_other_users_custom_agent(self, admin_client, tmp_path):
        """Admin can delete any custom agent."""
        _write_agent(
            tmp_path,
            "alice-agent",
            {"name": "alice-agent", "category": "custom", "created_by": ALICE_ID, "created_at": "2026-01-01T00:00:00+00:00"},
        )

        resp = admin_client.delete("/api/agents/alice-agent")
        assert resp.status_code == 204

    def test_delete_public_agent_returns_403_for_non_admin(self, alice_client, tmp_path):
        """Regular users cannot delete public agents."""
        _write_agent(
            tmp_path,
            "public-bot",
            {"name": "public-bot", "category": "public", "created_by": None, "created_at": "2026-01-01T00:00:00+00:00"},
        )

        resp = alice_client.delete("/api/agents/public-bot")
        assert resp.status_code == 403

    def test_anon_create_update_delete_returns_401(self, anon_client, tmp_path):
        """Anonymous users get 401 on write endpoints."""
        resp = anon_client.post("/api/agents", json={"name": "test", "soul": "x"})
        assert resp.status_code == 401

        _write_agent(
            tmp_path,
            "some-agent",
            {"name": "some-agent", "category": "custom", "created_by": "other"},
        )
        resp = anon_client.put("/api/agents/some-agent", json={"description": "x"})
        assert resp.status_code == 401

        resp = anon_client.delete("/api/agents/some-agent")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# R-06: update_agent must preserve non-managed fields (2026-08-13)
# ---------------------------------------------------------------------------


class TestUpdateAgentPreservesNonManagedFields:
    def test_put_description_preserves_memory_scope(self, agent_admin_client):
        """The co-writer seed case: a hand-authored `memory_scope: none` must
        survive an unrelated PUT (previously silently stripped, R-06).

        SEC-M22（批次 2 项 2.9）：legacy 无主 agent 改动需 admin——本例改用
        admin 身份（字段保留语义不变）。"""
        import yaml

        base = agent_admin_client._tmp_path
        _write_agent(
            base,
            "scope-agent",
            {"name": "scope-agent", "description": "old", "memory_scope": "none"},
        )

        response = agent_admin_client.put("/api/agents/scope-agent", json={"description": "new desc"})
        assert response.status_code == 200
        assert response.json()["description"] == "new desc"

        config = yaml.safe_load((base / "agents" / "scope-agent" / "config.yaml").read_text(encoding="utf-8"))
        assert config["memory_scope"] == "none", "memory_scope was stripped by PUT"
        assert config["description"] == "new desc"
        assert config["name"] == "scope-agent"

    def test_put_skills_preserves_created_at(self, agent_admin_client):
        """Management metadata must also survive an update (legacy agent —
        admin-only to edit since SEC-M22 批次 2 项 2.9)."""
        import yaml

        base = agent_admin_client._tmp_path
        _write_agent(
            base,
            "meta-agent",
            {"name": "meta-agent", "created_at": "2026-01-01T00:00:00Z"},
        )

        response = agent_admin_client.put("/api/agents/meta-agent", json={"skills": ["ppt-master"]})
        assert response.status_code == 200

        config = yaml.safe_load((base / "agents" / "meta-agent" / "config.yaml").read_text(encoding="utf-8"))
        assert config["created_at"] == "2026-01-01T00:00:00Z"
        assert config["skills"] == ["ppt-master"]

    def test_preserve_non_managed_fields_helper(self):
        from deerflow.config.agents_config import AgentConfig, preserve_non_managed_fields

        cfg = AgentConfig(
            name="x",
            description="d",
            model=None,
            memory_scope="none",
            category="public",
            created_by="admin",
            created_at="2026-01-01T00:00:00Z",
        )
        preserved = preserve_non_managed_fields(cfg)
        assert preserved["memory_scope"] == "none"
        assert preserved["category"] == "public"
        assert "description" not in preserved
        assert "model" not in preserved
        assert "tool_groups" not in preserved
        assert "skills" not in preserved
