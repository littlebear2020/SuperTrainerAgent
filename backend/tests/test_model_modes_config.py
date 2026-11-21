from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from langchain.chat_models import BaseChatModel

from app.gateway.routers.models import get_model, list_models
from deerflow.config.app_config import AppConfig
from deerflow.config.model_config import ModelConfig
from deerflow.config.sandbox_config import SandboxConfig
from deerflow.models import factory as factory_module

REPO_ROOT = Path(__file__).resolve().parents[2]


def _model(name: str = "test-model", modes: list[str] | None = None) -> ModelConfig:
    values = {
        "name": name,
        "display_name": name,
        "description": None,
        "use": "langchain_openai:ChatOpenAI",
        "model": name,
        "api_key": "test-key",
    }
    if modes is not None:
        values["modes"] = modes
    return ModelConfig(**values)


def _config(models: list[ModelConfig], mode_defaults: dict[str, str] | None = None) -> AppConfig:
    values = {
        "models": models,
        "sandbox": SandboxConfig(use="deerflow.sandbox.local:LocalSandboxProvider"),
    }
    if mode_defaults is not None:
        values["mode_defaults"] = mode_defaults
    return AppConfig(**values)


def test_model_config_rejects_unknown_mode() -> None:
    with pytest.raises(ValueError):
        _model(modes=["thinking"])


def test_mode_defaults_reject_unknown_mode_key() -> None:
    with pytest.raises(ValueError):
        _config([_model(modes=["standard"])], {"thinking": "test-model"})


def test_mode_defaults_reject_missing_model() -> None:
    with pytest.raises(ValueError, match="missing-model"):
        _config([_model(modes=["standard"])], {"standard": "missing-model"})


def test_mode_defaults_reject_model_outside_mode() -> None:
    with pytest.raises(ValueError, match="standard"):
        _config([_model(modes=["flash"])], {"standard": "test-model"})


class _CapturingModel(BaseChatModel):
    captured: dict = {}

    def __init__(self, **kwargs):
        _CapturingModel.captured = dict(kwargs)
        super().__init__(**kwargs)

    @property
    def _llm_type(self) -> str:
        return "mode-test"

    def _generate(self, *args, **kwargs):  # type: ignore[override]
        raise NotImplementedError


def test_factory_does_not_forward_modes(monkeypatch: pytest.MonkeyPatch) -> None:
    config = _config([_model(modes=["flash"])])
    monkeypatch.setattr(factory_module, "resolve_class", lambda path, base: _CapturingModel)

    factory_module.create_chat_model("test-model", app_config=config, attach_tracing=False)

    assert "modes" not in _CapturingModel.captured


@pytest.mark.asyncio
async def test_models_api_returns_modes_and_defaults_without_credentials() -> None:
    config = _config(
        [_model("claude", ["standard", "ultra"])],
        {"standard": "claude"},
    )

    listed = (await list_models(config=config)).model_dump()
    detail = (await get_model("claude", config=config)).model_dump()

    assert listed["mode_defaults"] == {"standard": "claude"}
    assert listed["models"][0]["modes"] == ["standard", "ultra"]
    assert detail["modes"] == ["standard", "ultra"]
    assert "api_key" not in listed["models"][0]
    assert "api_key" not in detail


def test_example_config_version_is_32() -> None:
    with open(REPO_ROOT / "config.example.yaml", encoding="utf-8") as stream:
        config = yaml.safe_load(stream)

    assert config["config_version"] == 32


def test_user_config_version_is_32() -> None:
    config_path = REPO_ROOT / "config.yaml"
    if not config_path.exists():
        pytest.skip("本地 config.yaml 不存在")
    with open(config_path, encoding="utf-8") as stream:
        config = yaml.safe_load(stream)

    assert config["config_version"] == 32
