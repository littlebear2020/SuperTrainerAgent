"""R2 (2026-09-24 批 0, DEF-01'): gpt-6 tools+reasoning_effort 守卫单测.

360 网关上的 gpt-6 在带 function tools 时对任何非 none 的 reasoning_effort
一律 400（effort 各档全灭，flash→minimal 同样命中）。守卫方案（评审 Q1/Q2
定稿）：

* ``ModelConfig.reasoning_effort_none_with_tools: bool`` 显式声明（不靠
  extra="allow"），并进 factory model_dump exclude 清单防上 wire；
* ``PatchedChatOpenAI._get_request_payload`` 落守卫——payload 带非空 tools 时
  顶层与 extra_body **两处同清**为 "none"（openai SDK ``_merge_mappings`` 里
  extra_body 覆盖顶层，只清顶层会被 thinking 档的 extra_body 值盖回）；
* 纯聊天路径（title/suggestions，不带 tools）保留原 effort 语义不受影响。

2026-10-12 花名册批注记：gpt-6-luna/sol 已删册、gpt-6.1-sol spike 证伪不入册
（chat/completions 上 function tools 全形态 400 且 effort 不支持 none 档——
守卫形态对 6.1 无效，上游要求 /v1/responses），**当前花名册无该 flag 的在册
消费者**；本文件保留为守卫机制（PatchedChatOpenAI 路径 + factory 透传）的
回归保护，用例本地构造 ModelConfig、不依赖在册条目。
"""

from __future__ import annotations

import logging

from langchain_core.messages import HumanMessage

from deerflow.config.app_config import AppConfig, SandboxConfig
from deerflow.config.model_config import ModelConfig
from deerflow.models import create_chat_model
from deerflow.models.patched_openai import PatchedChatOpenAI

_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get weather",
            "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
        },
    }
]


def _model(**kwargs) -> PatchedChatOpenAI:
    return PatchedChatOpenAI(
        model="gpt-6-luna",
        api_key="sk-test-not-real",
        base_url="http://localhost:9",
        **kwargs,
    )


class TestGuardClearsBothLevels:
    def test_flag_on_tools_bound_clears_top_level_and_extra_body(self):
        m = _model(
            reasoning_effort_none_with_tools=True,
            reasoning_effort="high",
            extra_body={"reasoning_effort": "high"},
        )
        payload = m._get_request_payload([HumanMessage("hi")], tools=_TOOLS)
        assert payload["reasoning_effort"] == "none"
        assert payload["extra_body"]["reasoning_effort"] == "none"
        # 360 网关实测（spike 2026-09-24）：带 tools 时只认嵌套 extra_body.reasoning_effort，
        # 顶层键被忽略→注册默认档→400。SDK 会展平 extra_body kwarg，故嵌套对象必须
        # 藏在展平键 extra_body 里：payload.extra_body.extra_body.reasoning_effort="none"
        # 展平后即实测通过的 G 形态（顶层 none + 嵌套 extra_body none）。
        assert payload["extra_body"]["extra_body"]["reasoning_effort"] == "none"
        # 原始 high 配置对象不被污染
        assert m.extra_body == {"reasoning_effort": "high"}

    def test_flag_on_tools_bound_extra_body_without_effort_gets_none_too(self):
        """extra_body 原本没有 effort 键也统一补 none（含嵌套形态）——防后续合并把顶层盖回。"""
        m = _model(reasoning_effort_none_with_tools=True, reasoning_effort="minimal", extra_body={"foo": 1})
        payload = m._get_request_payload([HumanMessage("hi")], tools=_TOOLS)
        assert payload["reasoning_effort"] == "none"
        assert payload["extra_body"]["reasoning_effort"] == "none"
        assert payload["extra_body"]["extra_body"]["reasoning_effort"] == "none"
        assert payload["extra_body"]["foo"] == 1
        # 原配置对象不被原地污染
        assert m.extra_body == {"foo": 1}

    def test_flash_tier_minimal_also_forced_to_none(self):
        """前端 flash 档映射 minimal 上 wire——正是 flash 也 400 的根因路径。"""
        m = _model(
            reasoning_effort_none_with_tools=True,
            reasoning_effort="minimal",
            extra_body={"reasoning_effort": "minimal"},
        )
        payload = m._get_request_payload([HumanMessage("hi")], tools=_TOOLS)
        assert payload["reasoning_effort"] == "none"
        assert payload["extra_body"]["reasoning_effort"] == "none"
        assert payload["extra_body"]["extra_body"]["reasoning_effort"] == "none"

    def test_flag_on_without_tools_keeps_effort(self):
        """纯聊天路径（title/suggestions 无 tools）保留原 effort 语义。"""
        m = _model(
            reasoning_effort_none_with_tools=True,
            reasoning_effort="high",
            extra_body={"reasoning_effort": "high"},
        )
        payload = m._get_request_payload([HumanMessage("hi")])
        assert payload["reasoning_effort"] == "high"
        assert payload["extra_body"]["reasoning_effort"] == "high"

    def test_flag_off_default_keeps_effort_with_tools(self):
        m = _model(reasoning_effort="high", extra_body={"reasoning_effort": "high"})
        assert m.reasoning_effort_none_with_tools is False
        payload = m._get_request_payload([HumanMessage("hi")], tools=_TOOLS)
        assert payload["reasoning_effort"] == "high"
        assert payload["extra_body"]["reasoning_effort"] == "high"

    def test_empty_tools_list_treated_as_no_tools(self):
        m = _model(reasoning_effort_none_with_tools=True, reasoning_effort="medium")
        payload = m._get_request_payload([HumanMessage("hi")], tools=[])
        assert payload["reasoning_effort"] == "medium"

    def test_flag_never_leaks_into_default_params(self):
        """守卫字段是声明字段（非 model_kwargs），不得出现在出站参数里。"""
        m = _model(reasoning_effort_none_with_tools=True)
        assert "reasoning_effort_none_with_tools" not in m._default_params
        assert not m.model_kwargs


def _app_config(model: ModelConfig) -> AppConfig:
    return AppConfig(models=[model], sandbox=SandboxConfig(use="deerflow.sandbox.local:LocalSandboxProvider"))


class TestFactoryWiring:
    def _make_model(self, use: str = "deerflow.models.patched_openai:PatchedChatOpenAI", **kwargs) -> ModelConfig:
        return ModelConfig(
            name="gpt-6-test",
            display_name="gpt-6-test",
            description=None,
            use=use,
            model="openai/gpt-6-test",
            api_key="sk-test-not-real",
            base_url="http://localhost:9",
            **kwargs,
        )

    def test_flag_true_reaches_patched_instance(self):
        cfg = self._make_model(reasoning_effort_none_with_tools=True)
        model = create_chat_model("gpt-6-test", app_config=_app_config(cfg), attach_tracing=False)
        assert isinstance(model, PatchedChatOpenAI)
        assert model.reasoning_effort_none_with_tools is True

    def test_flag_default_false_not_forwarded(self):
        cfg = self._make_model()
        model = create_chat_model("gpt-6-test", app_config=_app_config(cfg), attach_tracing=False)
        assert model.reasoning_effort_none_with_tools is False
        assert "reasoning_effort_none_with_tools" not in model._default_params

    def test_flag_on_unsupported_class_warns_and_is_dropped(self, caplog):
        """flag 配在不实现守卫的类上：WARNING 提示 + 不透传（不得报错）。"""
        cfg = self._make_model(use="langchain_openai:ChatOpenAI", reasoning_effort_none_with_tools=True)
        with caplog.at_level(logging.WARNING, logger="deerflow.models.factory"):
            model = create_chat_model("gpt-6-test", app_config=_app_config(cfg), attach_tracing=False)
        assert any("reasoning_effort_none_with_tools" in r.message for r in caplog.records)
        # 未透传：不是构造参数，也不得泄漏进出站参数
        assert "reasoning_effort_none_with_tools" not in model._default_params
        assert not model.model_kwargs

    def test_thinking_disabled_none_mapping_survives_without_tools(self):
        """config 的 disabled→none extra_body 映射在纯聊天路径照常生效（回归保护）。"""
        cfg = self._make_model(
            supports_thinking=True,
            supports_reasoning_effort=True,
            when_thinking_enabled={"extra_body": {"reasoning_effort": "high"}},
            when_thinking_disabled={"extra_body": {"reasoning_effort": "none"}},
            reasoning_effort_none_with_tools=True,
        )
        model = create_chat_model("gpt-6-test", thinking_enabled=False, app_config=_app_config(cfg), attach_tracing=False)
        payload = model._get_request_payload([HumanMessage("hi")])
        assert payload["extra_body"]["reasoning_effort"] == "none"
        # 带 tools 时顶层 minimal 也会被守卫压成 none
        payload_tools = model._get_request_payload([HumanMessage("hi")], tools=_TOOLS)
        assert payload_tools["reasoning_effort"] == "none"
        assert payload_tools["extra_body"]["reasoning_effort"] == "none"
