"""2026-10-12 360 通道花名册调整批：LLM 花名册增删 + 默认模型易主 pin。

计划 docs/plans/2026-10-12-model-config-roster-adjustment-plan.md（§1.6 十项决策拍板）。
spike 结论（仓库根 temp/results.json，2026-10-12）：
- claude 三条经 360 网关 OpenAI 兼容封装可用（PatchedChatOpenAI 路径成立）；
  vision 全答对主色（256×256 纯红图）；thinking 顶层/budget/effort 形态被网关接受
  但 reasoning_tokens=0 且无 reasoning 字段 = 无效参数 → supports_thinking: false；
  usage 回执无 amount → pricing TBD（不写 pricing 块）。
- gpt-6.1-sol spike 证伪**不入册**：拒 thinking 块 + reasoning_effort 不支持 none
  档（仅 low/medium/high/xhigh）+ chat/completions 上 function tools 全形态 400
  （含无 effort 形态，上游明示须 /v1/responses）——注册即带病入册（agent 平台
  模型必须可带工具），待上游放开后重议。
- 下架探针 7/7 网关侧仍在服务（§6.3 回滚前提成立，留档 changelog）。
- 默认模型易主：qwen3.8-flash 压测胜出提为 models[0]（650 样本，
  docs/2026-10-12-flash-trio-benchmark.md）；glm-5.3-flash 降为 models[1]。
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from langchain.chat_models import BaseChatModel

from deerflow.config.app_config import AppConfig
from deerflow.models import factory as factory_module

REPO_ROOT = Path(__file__).resolve().parents[2]

NEW_LLM_NAMES = ["claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5"]
NEW_LLM_MODEL_IDS = {
    "claude-haiku-5-5": "anthropic/claude-haiku-5-5",
    "claude-sonnet-5-5": "anthropic/claude-sonnet-5-5",
    "claude-opus-5-5": "anthropic/claude-opus-5-5",
}
REMOVED_NAMES = [
    "mimo-v2.6-flash",
    "mimo-v2.6-pro",
    "gpt-6-luna",
    "gpt-6-sol",
    "gemini-3.8-flash",
    "deepseek-v4-flash-vision-exp",
    "doubao-seed-2.1-pro-ark",
]
# spike 证伪（tools 全形态 400）不入册——防止"照抄旧计划 4 条"式回归。
FALSIFIED_NAME = "gpt-6.1-sol"


@pytest.fixture()
def real_config(monkeypatch: pytest.MonkeyPatch) -> AppConfig:
    """加载仓库根 config.yaml（$VAR 经 monkeypatch 补齐，不发网络请求）。

    config.yaml 被 .gitignore 忽略（/config.yaml，CI fresh checkout 不存在），
    AppConfig.from_file 对显式缺失路径抛 FileNotFoundError——缺失时 skip，
    依赖本 fixture 的全部用例（含 stubbed_factory 链路）在 CI 自动跳过、
    本地照常执行；example_yaml 用例不依赖 config.yaml，不受波及。
    """
    config_path = REPO_ROOT / "config.yaml"
    if not config_path.exists():
        pytest.skip("花名册 pin 依赖本地 gitignored config.yaml，CI 跳过")
    monkeypatch.setenv("QIHU_API_KEY", "test-key")
    monkeypatch.setenv("ARK_API_KEY", "test-key")
    return AppConfig.from_file(str(config_path))


@pytest.fixture()
def example_yaml() -> dict:
    with open(REPO_ROOT / "config.example.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


class _RosterStubModel(BaseChatModel):
    """记录构造 kwargs 的桩（镜像 test_model_factory.FakeChatModel 模式）。"""

    captured: dict = {}

    def __init__(self, **kwargs):
        _RosterStubModel.captured = dict(kwargs)
        super().__init__(**kwargs)

    @property
    def _llm_type(self) -> str:
        return "roster-stub"

    def _generate(self, *args, **kwargs):  # type: ignore[override]
        raise NotImplementedError


@pytest.fixture()
def stubbed_factory(real_config: AppConfig, monkeypatch: pytest.MonkeyPatch):
    """factory 打桩：真实 config + 假模型类，create_chat_model 全链可跑。"""
    monkeypatch.setattr(factory_module, "resolve_class", lambda path, base: _RosterStubModel)
    monkeypatch.setattr(factory_module, "build_tracing_callbacks", lambda: [])
    return real_config


# ── 花名册内容 ────────────────────────────────────────────────────────────


def test_roster_contains_three_new_claude_models(real_config: AppConfig) -> None:
    names = [m.name for m in real_config.models]
    for n in NEW_LLM_NAMES:
        assert n in names, f"{n} 未入册"
        assert NEW_LLM_MODEL_IDS[n] == real_config.get_model_config(n).model


def test_removed_and_falsified_models_absent(real_config: AppConfig) -> None:
    """删册 7 条 + spike 证伪的 gpt-6.1-sol 均不得在册。"""
    names = [m.name for m in real_config.models]
    for n in [*REMOVED_NAMES, FALSIFIED_NAME]:
        assert n not in names, f"{n} 应已删册/不入册（2026-10-12 花名册批）"


def test_models0_is_qwen38_flash(real_config: AppConfig) -> None:
    """默认位易主（§1.4 压测裁决）：qwen3.8-flash 胜出，glm-5.3-flash 顺延 models[1]。"""
    assert real_config.models[0].name == "qwen3.8-flash"
    assert real_config.models[1].name == "glm-5.3-flash"
    assert real_config.models[0].display_name == "360·Qwen 3.8 Flash（默认）"
    assert real_config.models[1].display_name == "360·GLM 5.3 Flash"


def test_video_gen_defaults_migrated(real_config: AppConfig) -> None:
    """video_gen 三字段迁移 + vimax 切回 360 网关（决策 5）。"""
    vg = real_config.video_gen
    assert vg["script_model"] == "qwen3.8-flash"
    assert vg["subtitle_llm_model"] == "qwen3.8-flash"
    assert vg["vimax_llm_model"] == "qwen/qwen3.8-max"
    assert vg["vimax_llm_base_url"] == "https://api.360.cn/v1"
    assert vg["ai_image_provider"] == "seedream-360"
    assert vg["ai_video_provider"] == "seedance"


def test_video_gen_code_defaults_pinned() -> None:
    """config.py 代码默认值 = 新值（决策 6：example/新部署兜底，删册后按旧默认加载即崩）。"""
    from app.gateway.video_gen.config import VideoGenConfig

    assert VideoGenConfig().subtitle_llm_model == "qwen3.8-flash"
    assert VideoGenConfig().vimax_llm_model == "qwen/qwen3.8-max"


# ── claude 三条字段形态（spike 定稿）───────────────────────────────────────


def test_claude_vision_true_thinking_false(real_config: AppConfig) -> None:
    """spike：vision 答对主色；thinking 参数被接受但无 reasoning 产出 = 无效参数。"""
    for n in NEW_LLM_NAMES:
        cfg = real_config.get_model_config(n)
        assert cfg.supports_vision is True, f"{n} supports_vision 应为 true（spike 实证）"
        assert cfg.supports_thinking is False, f"{n} supports_thinking 应为 false（spike 实证）"


def test_claude_no_thinking_toggle_blocks(real_config: AppConfig) -> None:
    """supports_thinking=false → 不写 when_thinking_* 块（无效参数不上 wire）。"""
    for n in NEW_LLM_NAMES:
        cfg = real_config.get_model_config(n)
        assert cfg.when_thinking_enabled is None
        assert cfg.when_thinking_disabled is None
        assert cfg.supports_reasoning_effort is False


def test_claude_use_patched_openai_and_360_gateway(real_config: AppConfig) -> None:
    for n in NEW_LLM_NAMES:
        cfg = real_config.get_model_config(n)
        assert cfg.use == "deerflow.models.patched_openai:PatchedChatOpenAI"
        assert "api.360.cn" in (cfg.model_extra or {}).get("base_url", "")


# ── 币种口径 ─────────────────────────────────────────────────────────────


def test_pricing_single_currency_cny_or_absent(real_config: AppConfig) -> None:
    """单部署单币种：全册 pricing 要么缺省（spike 回执缺失=TBD）要么 CNY。"""
    for m in real_config.models:
        pricing = (m.model_extra or {}).get("pricing")
        if pricing is not None:
            assert pricing.get("currency") == "CNY", f"{m.name} 币种违规：{pricing}"


def test_new_models_pricing_tbd_pending_receipt(real_config: AppConfig) -> None:
    """spike 无 amount 回执 → claude 三条不写 pricing（待 /api/admin/model-pricing 补录）。"""
    for n in NEW_LLM_NAMES:
        pricing = (real_config.get_model_config(n).model_extra or {}).get("pricing")
        assert pricing is None, f"{n} 应为 TBD（无 pricing 块），实际：{pricing}"


# ── 两态实例化（mock 层） ────────────────────────────────────────────────


@pytest.mark.parametrize("thinking_enabled", [True, False])
def test_create_chat_model_both_states(stubbed_factory, thinking_enabled: bool) -> None:
    """claude 三条无 thinking 开关：thinking_enabled 两态均不注入思考参数。"""
    for n in NEW_LLM_NAMES:
        model = factory_module.create_chat_model(n, thinking_enabled=thinking_enabled, app_config=stubbed_factory)
        assert isinstance(model, _RosterStubModel)
        kwargs = _RosterStubModel.captured
        assert not kwargs.get("when_thinking_enabled")
        assert "reasoning_effort" not in kwargs or kwargs.get("reasoning_effort") is None, f"{n} 未声明 supports_reasoning_effort，factory 应 pop 该参数"


def test_create_chat_model_reasoning_effort_popped_for_claude(stubbed_factory) -> None:
    """未声明 supports_reasoning_effort：factory 静默 pop（防 wire 上报 Unknown parameter）。"""
    factory_module.create_chat_model("claude-sonnet-5-5", thinking_enabled=False, app_config=stubbed_factory, reasoning_effort="low")
    assert "reasoning_effort" not in _RosterStubModel.captured


# ── 2026-11-11 Terra 上游替换与 DeepSeek 新增 ─────────────────────────────

TARGET_MODEL_IDS = {
    "gpt-5.6-terra": "openai/gpt-6.1-terra",
    "deepseek-v4.1-flash": "deepseek/deepseek-v4.1-flash",
}


def test_terra_keeps_internal_alias_and_updates_upstream(real_config: AppConfig) -> None:
    matches = [m for m in real_config.models if m.name == "gpt-5.6-terra"]
    assert len(matches) == 1
    terra = matches[0]
    assert terra.model == "openai/gpt-6.1-terra"
    assert terra.display_name == "360·GPT 6.1 Terra"
    assert "gpt-6.1-terra" not in [m.name for m in real_config.models]
    assert terra.supports_vision is False
    assert terra.supports_thinking is False
    assert terra.supports_reasoning_effort is False
    assert terra.when_thinking_enabled is None
    assert terra.when_thinking_disabled is None


def test_deepseek_v41_flash_roster_contract(real_config: AppConfig) -> None:
    deepseek = real_config.get_model_config("deepseek-v4.1-flash")
    assert deepseek.model == "deepseek/deepseek-v4.1-flash"
    assert deepseek.display_name == "360·DeepSeek V4.1 Flash"
    assert deepseek.use == "deerflow.models.patched_deepseek:PatchedChatDeepSeek"
    assert (deepseek.model_extra or {}).get("api_base") == "https://api.360.cn/v1"
    assert deepseek.supports_vision is False
    assert deepseek.supports_thinking is False
    assert deepseek.supports_reasoning_effort is False
    assert deepseek.when_thinking_enabled is None
    assert deepseek.when_thinking_disabled is None


@pytest.mark.asyncio
async def test_models_api_maps_new_roster_without_exposing_vision(real_config: AppConfig) -> None:
    from app.gateway.routers.models import list_models

    response = await list_models(config=real_config)
    mapped = {m.name: m for m in response.models if m.name in TARGET_MODEL_IDS}
    assert set(mapped) == set(TARGET_MODEL_IDS)
    for name, upstream_id in TARGET_MODEL_IDS.items():
        model = mapped[name]
        assert model.model == upstream_id
        assert model.supports_thinking is False
        assert model.supports_reasoning_effort is False
        assert not hasattr(model, "supports_vision")
    assert mapped["gpt-5.6-terra"].display_name == "360·GPT 6.1 Terra"
    assert mapped["deepseek-v4.1-flash"].display_name == "360·DeepSeek V4.1 Flash"


@pytest.mark.parametrize("name,upstream_id", TARGET_MODEL_IDS.items())
def test_new_models_factory_preserves_upstream_and_pops_reasoning(stubbed_factory, name: str, upstream_id: str) -> None:
    factory_module.create_chat_model(name, thinking_enabled=False, attach_tracing=False, app_config=stubbed_factory, reasoning_effort="low")
    kwargs = _RosterStubModel.captured
    assert kwargs["model"] == upstream_id
    assert "reasoning_effort" not in kwargs


# ── config.example.yaml 同步 ─────────────────────────────────────────────
# example 的 models 段是注释模板（真实条目在 gitignored config.yaml），按文本匹配。


def test_example_yaml_contains_new_models() -> None:
    text = (REPO_ROOT / "config.example.yaml").read_text(encoding="utf-8")
    for n in NEW_LLM_NAMES:
        assert f"# - name: {n}" in text, f"config.example.yaml 缺 {n} 模板"
    assert "# - name: gpt-5.6-terra" in text
    assert "#   model: openai/gpt-6.1-terra" in text
    assert "# - name: deepseek-v4.1-flash" in text
    assert "#   model: deepseek/deepseek-v4.1-flash" in text
    assert "旧线程兼容别名" in text
    # 删册条目的模板不得残留
    for n in REMOVED_NAMES:
        assert f"# - name: {n}" not in text, f"config.example.yaml 应删 {n} 模板"


def test_example_config_version_bumped(example_yaml: dict) -> None:
    # 31=本地模型与 Aio 执行就绪批（2026-11-11）；30 保留给 pending W2
    # （declarative-reasoning-profile），本批不占用该预留版本。
    # 29=vimax 遗留收口批（2026-10-16，删 vimax_longform_*×3 + remotion_agent_assistant 四死字段）；
    # 28=vimax 切 360 + 配音音色加固批（2026-10-12，vimax_image_backend 新字段 + vimax_video_backend 默认翻转）；
    # 27=花名册调整批（2026-10-12）；26=OPS-2 删全局 channels.feishu（D6，2026-10-07）；
    # 25=上传白名单（R2-1）；24=skill 机制优化批 A；23=审计整改批 C4；22=中断修复批 P1；
    # 21=长任务稳定性批。
    assert example_yaml.get("config_version") == 31, "config_version 应为 31（2026-11-11 本地模型与 Aio 执行就绪批）"


def test_user_config_version_bumped(example_yaml: dict) -> None:
    """两文件版本一致（=bump 后的最新值），避免启动 outdated 告警。

    读本地 gitignored config.yaml——CI 缺失时 skip（同 real_config 守卫口径）。
    """
    config_path = REPO_ROOT / "config.yaml"
    if not config_path.exists():
        pytest.skip("花名册 pin 依赖本地 gitignored config.yaml，CI 跳过")
    import yaml as _yaml

    with open(config_path, encoding="utf-8") as f:
        user_version = _yaml.safe_load(f).get("config_version")
    assert user_version == example_yaml.get("config_version") == 31
