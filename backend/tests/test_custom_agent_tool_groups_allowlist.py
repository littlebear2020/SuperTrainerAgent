"""SEC-C2 (security plan 2026-09-02, batch B2): custom-agent tool_groups allowlist.

``POST/PUT /api/agents`` used to persist ``tool_groups`` verbatim, so any
authenticated user could self-serve an agent with the ``bash`` / ``file:write``
/ ``video_engine`` groups (self-service RCE surface). This batch validates the
requested groups against an allowlist whose initial set is ``{web}``
(``file:read`` pending a product decision; execution / heavy-resource groups
stays denied). Pre-existing agents are NOT re-validated on unrelated updates.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
import yaml
from fastapi.testclient import TestClient

from app.gateway.auth.models import User
from app.gateway.authz import _ALL_PERMISSIONS, AuthContext
from deerflow.config.agents_api_config import AgentsApiConfig, get_agents_api_config, set_agents_api_config

# ---------------------------------------------------------------------------
# Fixtures (same shape as tests/test_custom_agent.py::agent_client)
# ---------------------------------------------------------------------------


@pytest.fixture()
def agent_client(tmp_path):
    import app.gateway.routers.agents as agents_router
    from deerflow.config.paths import Paths

    paths_instance = Paths(base_dir=tmp_path)
    previous_config = AgentsApiConfig(**get_agents_api_config().model_dump())
    test_user = User(email="allowlist-test@example.com", password_hash="x", system_role="user", id=uuid4())
    test_auth = AuthContext(user=test_user, permissions=_ALL_PERMISSIONS)

    with (
        patch("deerflow.config.agents_config.get_paths", return_value=paths_instance),
        patch.object(agents_router, "get_paths", return_value=paths_instance),
        patch("app.gateway.routers.agents._authenticate", new_callable=AsyncMock, return_value=test_auth),
    ):
        set_agents_api_config(AgentsApiConfig(enabled=True))
        try:
            from fastapi import FastAPI

            from app.gateway.routers.agents import router

            app = FastAPI()
            app.include_router(router)
            with TestClient(app) as client:
                client._tmp_path = tmp_path  # type: ignore[attr-defined]
                yield client
        finally:
            set_agents_api_config(previous_config)


@pytest.fixture()
def agent_admin_client(tmp_path):
    import app.gateway.routers.agents as agents_router
    from deerflow.config.paths import Paths

    paths_instance = Paths(base_dir=tmp_path)
    previous_config = AgentsApiConfig(**get_agents_api_config().model_dump())
    # SEC-M22（批次 2 项 2.9）：legacy 无主 agent 改动需 admin。
    test_user = User(email="allowlist-admin@example.com", password_hash="x", system_role="admin", id=uuid4())
    test_auth = AuthContext(user=test_user, permissions=_ALL_PERMISSIONS)

    with (
        patch("deerflow.config.agents_config.get_paths", return_value=paths_instance),
        patch.object(agents_router, "get_paths", return_value=paths_instance),
        patch("app.gateway.routers.agents._authenticate", new_callable=AsyncMock, return_value=test_auth),
    ):
        set_agents_api_config(AgentsApiConfig(enabled=True))
        try:
            from fastapi import FastAPI

            from app.gateway.routers.agents import router

            app = FastAPI()
            app.include_router(router)
            with TestClient(app) as client:
                client._tmp_path = tmp_path  # type: ignore[attr-defined]
                yield client
        finally:
            set_agents_api_config(previous_config)


def _create_payload(tool_groups=None) -> dict:
    payload = {"name": "my-agent", "soul": "helpful"}
    if tool_groups is not None:
        payload["tool_groups"] = tool_groups
    return payload


# ---------------------------------------------------------------------------
# Allowlist constant
# ---------------------------------------------------------------------------


def test_allowlist_initial_set_is_web_only():
    """Initial allowlist: {web}. bash/file:write/video_engine denied; file:read pending product decision."""
    from app.gateway.routers.agents import CUSTOM_AGENT_ALLOWED_TOOL_GROUPS

    assert set(CUSTOM_AGENT_ALLOWED_TOOL_GROUPS) == {"web"}


# ---------------------------------------------------------------------------
# POST /api/agents
# ---------------------------------------------------------------------------


class TestPostAllowlist:
    def test_post_bash_rejected(self, agent_client):
        response = agent_client.post("/api/agents", json=_create_payload(["bash"]))
        assert response.status_code == 422
        assert "自定义 agent 不允许挂载该工具组" in response.json()["detail"]
        # Nothing was created
        assert agent_client.get("/api/agents/my-agent").status_code == 404

    def test_post_file_write_rejected(self, agent_client):
        response = agent_client.post("/api/agents", json=_create_payload(["file:write"]))
        assert response.status_code == 422

    def test_post_video_engine_rejected(self, agent_client):
        response = agent_client.post("/api/agents", json=_create_payload(["video_engine"]))
        assert response.status_code == 422

    def test_post_unknown_group_rejected(self, agent_client):
        response = agent_client.post("/api/agents", json=_create_payload(["not-a-group"]))
        assert response.status_code == 422

    def test_post_mixed_with_one_denied_rejected(self, agent_client):
        response = agent_client.post("/api/agents", json=_create_payload(["web", "bash"]))
        assert response.status_code == 422
        assert "bash" in response.json()["detail"]

    def test_post_web_allowed(self, agent_client):
        response = agent_client.post("/api/agents", json=_create_payload(["web"]))
        assert response.status_code == 201
        assert response.json()["tool_groups"] == ["web"]

    def test_post_without_tool_groups_lands_on_allowlist_default(self, agent_client):
        """Plan B2 step 5 (2026-09-02 复核落定): null must not implicitly mount
        every tool — a create without tool_groups lands on ["web"]."""
        response = agent_client.post("/api/agents", json=_create_payload(None))
        assert response.status_code == 201
        assert response.json()["tool_groups"] == ["web"]

    def test_rejection_message_names_the_denied_groups(self, agent_client):
        response = agent_client.post("/api/agents", json=_create_payload(["bash", "video_engine"]))
        detail = response.json()["detail"]
        assert "bash" in detail
        assert "video_engine" in detail


# ---------------------------------------------------------------------------
# PUT /api/agents/{name}
# ---------------------------------------------------------------------------


class TestPutAllowlist:
    def _create(self, client, tool_groups=None):
        payload = _create_payload(tool_groups) if tool_groups else {"name": "my-agent", "soul": "v1"}
        assert client.post("/api/agents", json=payload).status_code == 201

    def test_put_bash_rejected(self, agent_client):
        self._create(agent_client)
        response = agent_client.put("/api/agents/my-agent", json={"tool_groups": ["bash"]})
        assert response.status_code == 422
        # Existing config untouched (create landed on ["web"] — the allowlist default)
        current = agent_client.get("/api/agents/my-agent").json()
        assert current["tool_groups"] == ["web"]

    def test_put_web_allowed(self, agent_client):
        self._create(agent_client)
        response = agent_client.put("/api/agents/my-agent", json={"tool_groups": ["web"]})
        assert response.status_code == 200
        assert response.json()["tool_groups"] == ["web"]

    def test_put_without_tool_groups_leaves_legacy_value_alone(self, agent_admin_client, tmp_path: Path):
        """A pre-existing agent with a now-denied group keeps it on unrelated updates (存量不回溯).

        SEC-M22（批次 2 项 2.9）：legacy 无主 agent 改动需 admin——用 admin 身份。"""
        # Hand-author a legacy agent as if created before the allowlist existed
        agent_dir = tmp_path / "agents" / "legacy-agent"
        agent_dir.mkdir(parents=True)
        with open(agent_dir / "config.yaml", "w") as f:
            yaml.dump({"name": "legacy-agent", "description": "old", "tool_groups": ["bash"]}, f)
        (agent_dir / "SOUL.md").write_text("legacy", encoding="utf-8")

        response = agent_admin_client.put("/api/agents/legacy-agent", json={"description": "updated"})

        assert response.status_code == 200
        assert response.json()["tool_groups"] == ["bash"]

    def test_put_explicit_null_tool_groups_inherits_existing(self, agent_client):
        """复核修复（反馈 1 问题 2）：显式 tool_groups: null 不得剥掉既有显式组。

        修前路径：preserve_non_managed_fields 排除 managed 字段 + is not None
        不回写 → 重写的 yaml 丢失 tool_groups 键 → agent 回落 null=挂全部工具
        （遗留 created_by=null agent 任何认证用户可更新 → 提权链）。
        """
        self._create(agent_client)  # lands ["web"]
        response = agent_client.put("/api/agents/my-agent", json={"tool_groups": None, "description": "x"})
        assert response.status_code == 200
        assert response.json()["tool_groups"] == ["web"]

    def test_put_explicit_null_keeps_legacy_null_agent_converged(self, agent_admin_client, tmp_path: Path):
        """存量 null agent 的显式 null PUT 继承现值——SEC-C3 收敛后现值即 ["web"]。

        批次 2 项 2.1 起 load_agent_config 对缺键/null 收敛为 POST 缺省
        （["web"]/[]），存量 null 不再以 null 形态暴露（null=挂全部工具）。
        """
        agent_dir = tmp_path / "agents" / "legacy-null-agent"
        agent_dir.mkdir(parents=True)
        with open(agent_dir / "config.yaml", "w") as f:
            yaml.dump({"name": "legacy-null-agent", "description": "old"}, f)
        (agent_dir / "SOUL.md").write_text("legacy", encoding="utf-8")

        response = agent_admin_client.put("/api/agents/legacy-null-agent", json={"tool_groups": None})

        assert response.status_code == 200
        assert response.json()["tool_groups"] == ["web"]
