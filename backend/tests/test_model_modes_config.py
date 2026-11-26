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

    assert config["config_version"] == 33


def test_user_config_version_is_32() -> None:
    config_path = REPO_ROOT / "config.yaml"
    if not config_path.exists():
        pytest.skip("本地 config.yaml 不存在")
    with open(config_path, encoding="utf-8") as stream:
        config = yaml.safe_load(stream)

    assert config["config_version"] == 33


EXPECTED_MODES = {
    "qwen3.8-flash": ["flash"],
    "deepseek-v4.1-flash": ["flash", "standard"],
    "claude-sonnet-5-5": ["standard", "ultra"],
    "qwen4-max": ["ultra"],
    "kimi-k3.1-pro": ["ultra"],
    "glm-5.4": ["standard", "ultra"],
}
EXPECTED_MODE_DEFAULTS = {
    "flash": "qwen3.8-flash",
    "standard": "claude-sonnet-5-5",
    "ultra": "qwen4-max",
}
THINKING_MODELS = {"qwen3.8-flash", "deepseek-v4.1-flash", "qwen4-max", "kimi-k3.1-pro", "glm-5.4"}


@pytest.fixture()
def real_config(monkeypatch: pytest.MonkeyPatch) -> AppConfig:
    config_path = REPO_ROOT / "config.yaml"
    if not config_path.exists():
        pytest.skip("花名册测试依赖本地 config.yaml")
    monkeypatch.setenv("QIHU_API_KEY", "test-key")
    monkeypatch.setenv("ARK_API_KEY", "test-key")
    return AppConfig.from_file(str(config_path))


def test_real_roster_modes_and_defaults(real_config: AppConfig) -> None:
    assert real_config.models[0].name == "qwen3.8-flash"
    assert real_config.mode_defaults == EXPECTED_MODE_DEFAULTS
    for model in real_config.models:
        assert model.modes == EXPECTED_MODES.get(model.name, []), model.name


def test_deepseek_latency_roster_contract(real_config: AppConfig) -> None:
    model = real_config.get_model_config("deepseek-v4.1-flash")

    assert model.model == "deepseek/deepseek-v4.1-flash@360-latency"
    assert model.supports_thinking is True
    assert model.supports_reasoning_effort is True
    assert model.supports_vision is True


def test_ultra_roster_contract(real_config: AppConfig) -> None:
    qwen = real_config.get_model_config("qwen4-max")
    kimi = real_config.get_model_config("kimi-k3.1-pro")
    assert qwen is not None
    assert kimi is not None

    for model in (qwen, kimi):
        assert model.use == "deerflow.models.patched_openai:PatchedChatOpenAI"
        assert model.model_extra["base_url"] == "https://api.360.cn/v1"
        assert model.model_extra["max_tokens"] == 32768
        assert model.model_extra["temperature"] == 0.7
        assert model.supports_thinking is True
        assert model.supports_reasoning_effort is True
        assert model.supports_vision is True
    assert qwen.model == "qwen/qwen4-max"
    assert qwen.model_extra["stream_chunk_timeout"] == 240
    assert kimi.model == "moonshotai/kimi-k3.1-pro"
    assert kimi.model_extra["stream_chunk_timeout"] == 300
    assert kimi.model_extra["request_timeout"] == 300


@pytest.mark.parametrize("thinking_enabled", [True, False])
def test_real_roster_factory_mode_leak_and_thinking_shape(real_config: AppConfig, monkeypatch: pytest.MonkeyPatch, thinking_enabled: bool) -> None:
    monkeypatch.setattr(factory_module, "resolve_class", lambda path, base: _CapturingModel)

    for model in real_config.models:
        _CapturingModel.captured = {}
        factory_module.create_chat_model(
            model.name,
            thinking_enabled=thinking_enabled,
            reasoning_effort="high",
            app_config=real_config,
            attach_tracing=False,
        )
        kwargs = _CapturingModel.captured
        assert "modes" not in kwargs, model.name
        if model.name in THINKING_MODELS:
            expected_type = "enabled" if thinking_enabled else "disabled"
            assert kwargs["extra_body"]["thinking"] == {"type": expected_type}, model.name
            assert kwargs["reasoning_effort"] == ("high" if thinking_enabled else "minimal"), model.name
            assert "thinking" not in kwargs, model.name
            assert model.thinking is None, model.name
        elif model.name == "claude-sonnet-5-5":
            assert "thinking" not in kwargs
            assert "reasoning_effort" not in kwargs
            assert "thinking" not in kwargs.get("extra_body", {})


def test_example_contains_mode_model_templates() -> None:
    text = (REPO_ROOT / "config.example.yaml").read_text(encoding="utf-8")

    for name in EXPECTED_MODES:
        assert f"# - name: {name}" in text
    assert "#   model: deepseek/deepseek-v4.1-flash@360-latency" in text
    assert "#   standard: claude-sonnet-5-5" in text


def test_example_glm_5_4_template_pins_pricing() -> None:
    """只读示例，CI 也跑：价目写在示例里，部署侧复制模板时不会漏掉（运行时以库为准，兜底价首次调用即有价）。"""
    text = (REPO_ROOT / "config.example.yaml").read_text(encoding="utf-8")
    start = text.index("# - name: glm-5.4")
    end = text.find("# - name:", start + 1)
    block = text[start : end if end != -1 else len(text)]

    assert "#     currency: CNY\n" in block
    assert "#     input_per_million: 8\n" in block
    assert "#     output_per_million: 28\n" in block
    assert "#     input_cache_hit_per_million: 2\n" in block


def test_glm_5_4_roster_contract(real_config: AppConfig) -> None:
    model = real_config.get_model_config("glm-5.4")

    assert model is not None
    assert model.use == "deerflow.models.patched_openai:PatchedChatOpenAI"
    assert model.model == "z-ai/glm-5.4"
    assert model.model_extra["base_url"] == "https://api.360.cn/v1"
    assert model.model_extra["max_tokens"] == 32768
    # 2026-11-26 探针实测：开思考 + high 正常、有推理 token；视觉与工具调用通过。
    assert model.supports_thinking is True
    assert model.supports_reasoning_effort is True
    assert model.supports_vision is True
    assert "glm-5.4" not in real_config.mode_defaults.values()
    assert model.model_extra["pricing"] == {
        "currency": "CNY",
        "input_per_million": 8,
        "output_per_million": 28,
        "input_cache_hit_per_million": 2,
    }


def test_glm_5_4_pricing_resolves_upstream_id(real_config: AppConfig, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.gateway.routers import console

    monkeypatch.setattr(console, "get_app_config", lambda: real_config)

    price = console._lookup_pricing(console._build_yaml_pricing_map(), "z-ai/glm-5.4")

    assert price is not None
    # 750k 未命中缓存输入 × 8 + 250k 命中缓存 × 2 + 100k 输出 × 28（每百万 token）
    assert console._token_cost(1_000_000, 100_000, price, cache_read_tokens=250_000) == pytest.approx(9.3)


@pytest.mark.asyncio
async def test_models_api_lists_glm_5_4_in_standard_and_ultra(real_config: AppConfig) -> None:
    listed = (await list_models(config=real_config)).model_dump()
    by_name = {item["name"]: item for item in listed["models"]}

    assert by_name["glm-5.4"]["modes"] == ["standard", "ultra"]
    assert listed["mode_defaults"] == EXPECTED_MODE_DEFAULTS
