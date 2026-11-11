from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from deerflow.config.app_config import AppConfig
from deerflow.sandbox.security import is_host_bash_allowed
from deerflow.tools.tools import get_available_tools


def _make_config(*, allow_host_bash: bool, sandbox_use: str = "deerflow.sandbox.local:LocalSandboxProvider", extra_tools: list[SimpleNamespace] | None = None):
    return SimpleNamespace(
        tools=[
            SimpleNamespace(name="bash", group="bash", use="deerflow.sandbox.tools:bash_tool"),
            SimpleNamespace(name="ls", group="file:read", use="tests:ls_tool"),
            *(extra_tools or []),
        ],
        models=[],
        sandbox=SimpleNamespace(
            use=sandbox_use,
            allow_host_bash=allow_host_bash,
        ),
        tool_search=SimpleNamespace(enabled=False),
        get_model_config=lambda name: None,
    )


def test_get_available_tools_hides_bash_for_default_local_sandbox(monkeypatch):
    monkeypatch.setattr("deerflow.tools.tools.get_app_config", lambda: _make_config(allow_host_bash=False))
    monkeypatch.setattr(
        "deerflow.tools.tools.resolve_variable",
        lambda use, _: SimpleNamespace(name="bash" if "bash" in use else "ls"),
    )

    names = [tool.name for tool in get_available_tools(include_mcp=False, subagent_enabled=False)]

    assert "bash" not in names
    assert "ls" in names


def test_get_available_tools_keeps_bash_when_explicitly_enabled(monkeypatch):
    monkeypatch.setattr("deerflow.tools.tools.get_app_config", lambda: _make_config(allow_host_bash=True))
    monkeypatch.setattr(
        "deerflow.tools.tools.resolve_variable",
        lambda use, _: SimpleNamespace(name="bash" if "bash" in use else "ls"),
    )

    names = [tool.name for tool in get_available_tools(include_mcp=False, subagent_enabled=False)]

    assert "bash" in names
    assert "ls" in names


def test_get_available_tools_hides_renamed_host_bash_alias(monkeypatch):
    config = _make_config(
        allow_host_bash=False,
        extra_tools=[SimpleNamespace(name="shell", group="bash", use="deerflow.sandbox.tools:bash_tool")],
    )
    monkeypatch.setattr("deerflow.tools.tools.get_app_config", lambda: config)
    monkeypatch.setattr(
        "deerflow.tools.tools.resolve_variable",
        lambda use, _: SimpleNamespace(name="bash" if "bash_tool" in use else "ls"),
    )

    names = [tool.name for tool in get_available_tools(include_mcp=False, subagent_enabled=False)]

    assert "bash" not in names
    assert "shell" not in names
    assert "ls" in names


def test_get_available_tools_keeps_bash_for_aio_sandbox(monkeypatch):
    config = _make_config(
        allow_host_bash=False,
        sandbox_use="deerflow.community.aio_sandbox:AioSandboxProvider",
    )
    monkeypatch.setattr("deerflow.tools.tools.get_app_config", lambda: config)
    monkeypatch.setattr(
        "deerflow.tools.tools.resolve_variable",
        lambda use, _: SimpleNamespace(name="bash" if "bash_tool" in use else "ls"),
    )

    names = [tool.name for tool in get_available_tools(include_mcp=False, subagent_enabled=False)]

    assert "bash" in names
    assert "ls" in names


def test_is_host_bash_allowed_defaults_false_when_sandbox_missing():
    assert is_host_bash_allowed(SimpleNamespace()) is False
    assert is_host_bash_allowed(SimpleNamespace(sandbox=None)) is False


def test_real_config_uses_isolated_aio_with_agent_tools(monkeypatch: pytest.MonkeyPatch):
    repo_root = Path(__file__).resolve().parents[2]
    config_path = repo_root / "config.yaml"
    if not config_path.exists():
        pytest.skip("真实沙箱配置 pin 依赖本地 gitignored config.yaml，CI 跳过")
    monkeypatch.setenv("QIHU_API_KEY", "test-key")
    monkeypatch.setenv("ARK_API_KEY", "test-key")
    config = AppConfig.from_file(str(config_path))

    assert config.sandbox.use == "deerflow.community.aio_sandbox:AioSandboxProvider"
    assert config.sandbox.allow_host_bash is False
    assert config.sandbox.environment == {}
    tool_names = [
        tool.name
        for tool in get_available_tools(
            groups=["bash", "file:read", "file:write"],
            include_mcp=False,
            model_name=config.models[0].name,
            app_config=config,
        )
    ]
    assert {"bash", "ls", "read_file", "write_file"}.issubset(tool_names)


def test_example_keeps_local_default_and_documents_neutral_aio_template():
    repo_root = Path(__file__).resolve().parents[2]
    example_path = repo_root / "config.example.yaml"
    config = yaml.safe_load(example_path.read_text(encoding="utf-8"))
    assert config["sandbox"]["use"] == "deerflow.sandbox.local:LocalSandboxProvider"
    assert config["sandbox"]["allow_host_bash"] is False

    text = example_path.read_text(encoding="utf-8")
    aio_template = text.split("# Option 2: Container-based AIO Sandbox", 1)[1].split("# Option 3:", 1)[0]
    assert "#   image: deerflow-sandbox:local" in aio_template
    assert "#   allow_host_bash: false" in aio_template
    assert "#   environment: {}" in aio_template
    assert "docker build --platform linux/amd64" in aio_template
    assert "littlebear" not in aio_template
