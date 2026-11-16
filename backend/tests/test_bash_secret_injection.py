"""required-secrets 注入链装配端（评审二轮修复 H1，2026-09-30）。

上游链路：skill_activation_middleware（未移植）→ ``__active_skill_secrets`` →
bash 工具 env 注入。``_resolve_injected_secrets`` 先读 run context 的
``__active_skill_secrets`` / ``secrets``，再按 available_skills 走双键受限
宿主注入；没有 available_skills 时才保留 ``DEER_FLOW_SKILL_SECRET_AUTOBIND=1``
的启用技能声明并集 legacy 通道。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import deerflow.sandbox.tools as tools
from deerflow.sandbox.tools import _resolve_injected_secrets, mask_secret_values_in_output
from deerflow.skills.types import SecretRequirement


def _runtime_with_context(ctx: dict | None, config: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(context=ctx, state=None, config=config)


def _runtime_with_skills(skill_names: list[str] | None, ctx: dict | None = None) -> SimpleNamespace:
    metadata = {} if skill_names is None else {"available_skills": skill_names}
    return _runtime_with_context(ctx, config={"metadata": metadata})


def _skill(name: str, declared: tuple[str, ...], *, category: str = "public", enabled: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        category=category,
        enabled=enabled,
        required_secrets=tuple(SecretRequirement(req) for req in declared),
    )


class TestResolveInjectedSecrets:
    def test_none_context_returns_none(self):
        assert _resolve_injected_secrets(None) is None
        assert _resolve_injected_secrets(SimpleNamespace(state=None)) is None  # 无 context 属性

    def test_active_skill_secrets_win(self):
        ctx = {
            "__active_skill_secrets": {"ARK_API_KEY": "active-secret"},
            "secrets": {"QIHU_API_KEY": "request-secret"},
        }
        assert _resolve_injected_secrets(_runtime_with_context(ctx)) == {"ARK_API_KEY": "active-secret"}

    def test_request_secrets_fallback(self):
        ctx = {"secrets": {"QIHU_API_KEY": "request-secret"}}
        assert _resolve_injected_secrets(_runtime_with_context(ctx)) == {"QIHU_API_KEY": "request-secret"}

    def test_empty_context_returns_none(self):
        assert _resolve_injected_secrets(_runtime_with_context({})) is None

    def test_autobind_off_by_default(self, monkeypatch):
        monkeypatch.delenv("DEER_FLOW_SKILL_SECRET_AUTOBIND", raising=False)
        assert _resolve_injected_secrets(_runtime_with_context(None)) is None

    def test_autobind_fills_declared_names_from_gateway_env(self, monkeypatch, tmp_path):
        """AUTOBIND=1：启用技能声明并集 ∩ 网关 env（有值者）注入。"""
        monkeypatch.setenv("DEER_FLOW_SKILL_SECRET_AUTOBIND", "1")
        monkeypatch.setattr(
            tools,
            "_declared_skill_secret_names",
            lambda: {"QIHU_API_KEY", "ARK_API_KEY", "MISSING_KEY"},
        )
        monkeypatch.setenv("QIHU_API_KEY", "qihu-value")
        monkeypatch.setenv("ARK_API_KEY", "ark-value")

        injected = _resolve_injected_secrets(_runtime_with_context(None))
        assert injected == {"QIHU_API_KEY": "qihu-value", "ARK_API_KEY": "ark-value"}

    def test_autobind_blocked_name_logs_audit_warning(self, monkeypatch, caplog):
        """评审 #7：注入名命中封锁名单 → warning 留审计痕。"""
        import logging

        monkeypatch.setenv("DEER_FLOW_SKILL_SECRET_AUTOBIND", "1")
        monkeypatch.setattr(
            tools,
            "_declared_skill_secret_names",
            lambda: {"DATABASE_URL"},
        )
        monkeypatch.setenv("DATABASE_URL", "postgres://user:pw@host/db")

        with caplog.at_level(logging.WARNING, logger="deerflow.sandbox.tools"):
            injected = _resolve_injected_secrets(_runtime_with_context(None))

        assert injected == {"DATABASE_URL": "postgres://user:pw@host/db"}
        assert any("DATABASE_URL" in r.getMessage() and ("封锁" in r.getMessage() or "blocked" in r.getMessage().lower()) for r in caplog.records)

    def test_autobind_union_includes_optional_and_other_skill_declarations(self, monkeypatch):
        """optional 仍入并集；auto 缩小声明不会删除其他技能的同名声明。"""
        from deerflow.skills.types import SecretRequirement

        skills = [
            SimpleNamespace(required_secrets=(SecretRequirement("VOLCENGINE_VOICE_ACCESS_KEY"),)),
            SimpleNamespace(
                required_secrets=(
                    SecretRequirement("DOUBAO_TTS_DEFAULT_SPEAKER", optional=True),
                    SecretRequirement("OTHER_REQUIRED_KEY"),
                )
            ),
        ]
        monkeypatch.setattr("deerflow.skills.load_skills", lambda enabled_only: skills)
        tools._declared_names_cache = None
        try:
            assert tools._declared_skill_secret_names() == {
                "VOLCENGINE_VOICE_ACCESS_KEY",
                "DOUBAO_TTS_DEFAULT_SPEAKER",
                "OTHER_REQUIRED_KEY",
            }
        finally:
            tools._declared_names_cache = None

    def test_declared_names_cached(self, monkeypatch):
        """声明并集按 TTL 缓存——同一进程窗内 compute 只跑一次。"""
        calls = {"n": 0}

        def _heavy() -> set[str]:
            calls["n"] += 1
            return {"A"}

        tools._declared_names_cache = None  # 清缓存隔离前序用例
        first = tools._ttl_cached_declared_names(_heavy)
        second = tools._ttl_cached_declared_names(_heavy)
        assert first == second == {"A"}
        assert calls["n"] == 1
        tools._declared_names_cache = None


class TestRestrictedHostSkillInjection:
    """2026-11-16: 有 available_skills 时，只按白名单公共启用技能声明注入两个允许的宿主键。"""

    @pytest.fixture(autouse=True)
    def _clear_restricted_cache(self):
        tools._restricted_declared_names_cache.clear()
        yield
        tools._restricted_declared_names_cache.clear()

    @staticmethod
    def _patch_skills(monkeypatch, skills):
        calls: dict[str, bool] = {}

        def fake_load_skills(*, enabled_only: bool = False, strict_extensions_config: bool = False):
            calls["enabled_only"] = enabled_only
            calls["strict_extensions_config"] = strict_extensions_config
            return [skill for skill in skills if skill.enabled] if enabled_only else skills

        monkeypatch.setattr("deerflow.skills.load_skills", fake_load_skills)
        return calls

    def test_allowlisted_public_skill_gets_only_the_two_allowed_host_keys(self, monkeypatch):
        skills = [
            _skill(
                "ppt-master",
                ("QIHU_API_KEY", "VOLCENGINE_VOICE_ACCESS_KEY", "DATABASE_URL", "ARK_API_KEY", "MISSING_KEY"),
            ),
            _skill("podcast-studio", ("VOLCENGINE_VOICE_ACCESS_KEY",)),
        ]
        self._patch_skills(monkeypatch, skills)
        monkeypatch.setenv("QIHU_API_KEY", "qihu-host-value")
        monkeypatch.setenv("VOLCENGINE_VOICE_ACCESS_KEY", "voice-host-value")
        monkeypatch.setenv("DATABASE_URL", "postgres://host/db")
        monkeypatch.setenv("ARK_API_KEY", "ark-host-value")
        monkeypatch.delenv("MISSING_KEY", raising=False)

        injected = _resolve_injected_secrets(_runtime_with_skills(["ppt-master"]))

        assert injected == {
            "QIHU_API_KEY": "qihu-host-value",
            "VOLCENGINE_VOICE_ACCESS_KEY": "voice-host-value",
        }

    def test_forbidden_declared_names_are_never_injected(self, monkeypatch):
        self._patch_skills(monkeypatch, [_skill("evil", ("DATABASE_URL", "ARK_API_KEY"))])
        monkeypatch.setenv("DATABASE_URL", "postgres://host/db")
        monkeypatch.setenv("ARK_API_KEY", "ark-host-value")

        assert _resolve_injected_secrets(_runtime_with_skills(["evil"])) is None

    def test_custom_and_legacy_skills_do_not_participate(self, monkeypatch):
        self._patch_skills(
            monkeypatch,
            [
                _skill("custom-ppt", ("QIHU_API_KEY",), category="custom"),
                _skill("legacy-tts", ("VOLCENGINE_VOICE_ACCESS_KEY",), category="legacy"),
            ],
        )
        monkeypatch.setenv("QIHU_API_KEY", "qihu-host-value")
        monkeypatch.setenv("VOLCENGINE_VOICE_ACCESS_KEY", "voice-host-value")

        assert _resolve_injected_secrets(_runtime_with_skills(["custom-ppt", "legacy-tts"])) is None

    def test_disabled_skill_does_not_participate(self, monkeypatch):
        calls = self._patch_skills(monkeypatch, [_skill("ppt-master", ("QIHU_API_KEY",), enabled=False)])
        monkeypatch.setenv("QIHU_API_KEY", "qihu-host-value")

        assert _resolve_injected_secrets(_runtime_with_skills(["ppt-master"])) is None
        assert calls["enabled_only"] is True

    def test_empty_allowlist_injects_nothing(self, monkeypatch):
        self._patch_skills(monkeypatch, [_skill("ppt-master", ("QIHU_API_KEY",))])
        monkeypatch.setenv("QIHU_API_KEY", "qihu-host-value")

        assert _resolve_injected_secrets(_runtime_with_skills([])) is None

    def test_skill_lists_do_not_share_declaration_cache(self, monkeypatch):
        self._patch_skills(
            monkeypatch,
            [
                _skill("skill-a", ("QIHU_API_KEY",)),
                _skill("skill-b", ("VOLCENGINE_VOICE_ACCESS_KEY",)),
            ],
        )
        monkeypatch.setenv("QIHU_API_KEY", "qihu-host-value")
        monkeypatch.setenv("VOLCENGINE_VOICE_ACCESS_KEY", "voice-host-value")

        assert _resolve_injected_secrets(_runtime_with_skills(["skill-a"])) == {"QIHU_API_KEY": "qihu-host-value"}
        assert _resolve_injected_secrets(_runtime_with_skills(["skill-b"])) == {"VOLCENGINE_VOICE_ACCESS_KEY": "voice-host-value"}
        # 回到第一个列表仍命中自己的声明集合，不被第二个列表污染。
        assert _resolve_injected_secrets(_runtime_with_skills(["skill-a"])) == {"QIHU_API_KEY": "qihu-host-value"}

    def test_values_are_read_per_call_not_cached(self, monkeypatch):
        self._patch_skills(monkeypatch, [_skill("skill-a", ("QIHU_API_KEY",))])
        monkeypatch.setenv("QIHU_API_KEY", "first-value")

        assert _resolve_injected_secrets(_runtime_with_skills(["skill-a"])) == {"QIHU_API_KEY": "first-value"}

        monkeypatch.setenv("QIHU_API_KEY", "second-value")
        assert _resolve_injected_secrets(_runtime_with_skills(["skill-a"])) == {"QIHU_API_KEY": "second-value"}

    def test_active_and_request_secrets_keep_priority(self, monkeypatch):
        self._patch_skills(monkeypatch, [_skill("ppt-master", ("QIHU_API_KEY",))])
        monkeypatch.setenv("QIHU_API_KEY", "qihu-host-value")

        active_ctx = {"__active_skill_secrets": {"ARK_API_KEY": "active-secret"}}
        assert _resolve_injected_secrets(_runtime_with_skills(["ppt-master"], active_ctx)) == {"ARK_API_KEY": "active-secret"}

        request_ctx = {"secrets": {"OPENAI_API_KEY": "request-secret"}}
        assert _resolve_injected_secrets(_runtime_with_skills(["ppt-master"], request_ctx)) == {"OPENAI_API_KEY": "request-secret"}

    def test_extensions_config_read_failure_disables_default_host_injection(self, monkeypatch, tmp_path):
        """真实 loader 接线：extensions 配置损坏不能退化为全部启用并注入宿主密钥。"""
        from deerflow.config.extensions_config import ExtensionsConfig

        skill_dir = tmp_path / "skills" / "public" / "ppt-master"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: ppt-master\ndescription: test\nrequired-secrets:\n  - name: QIHU_API_KEY\n---\n\n# ppt-master\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(
            "deerflow.config.get_app_config",
            lambda: SimpleNamespace(skills=SimpleNamespace(get_skills_path=lambda: tmp_path / "skills")),
        )

        def fail_from_file(cls, config_path=None):
            raise RuntimeError("broken extensions config")

        monkeypatch.setattr(ExtensionsConfig, "from_file", classmethod(fail_from_file))
        monkeypatch.setenv("QIHU_API_KEY", "qihu-host-value")

        assert _resolve_injected_secrets(_runtime_with_skills(["ppt-master"])) is None

    def test_missing_or_none_available_skills_keeps_legacy_autobind(self, monkeypatch):
        monkeypatch.setenv("DEER_FLOW_SKILL_SECRET_AUTOBIND", "1")
        monkeypatch.setattr(tools, "_declared_skill_secret_names", lambda: {"QIHU_API_KEY"})
        monkeypatch.setenv("QIHU_API_KEY", "qihu-host-value")

        # 无 metadata.available_skills 字段
        assert _resolve_injected_secrets(_runtime_with_context(None, config={"metadata": {}})) == {"QIHU_API_KEY": "qihu-host-value"}
        # 字段存在但为 None
        assert _resolve_injected_secrets(_runtime_with_context(None, config={"metadata": {"available_skills": None}})) == {"QIHU_API_KEY": "qihu-host-value"}


class TestMaskInjectedValues:
    def test_mask_covers_injected_values(self):
        out = mask_secret_values_in_output("key=sk-live-abcdef123456 tail", injected_values=["sk-live-abcdef123456"])
        assert "sk-live-abcdef123456" not in out
        assert "***" in out

    def test_mask_ignores_short_values(self):
        out = mask_secret_values_in_output("v=ab tail", injected_values=["ab"])
        assert "v=ab" in out  # 短值不 mask（误伤防护，对齐上游 _MIN_MASK_LENGTH 语义）

    def test_mask_without_injected_keeps_legacy_behavior(self, monkeypatch):
        out = mask_secret_values_in_output("plain output")
        assert out == "plain output"


class TestBashToolWiring:
    """bash_tool 接线级：注入 secrets 必须真实抵达 execute_command(env=...)。

    变异验证补钉（2026-09-30）：撤掉接线行时单测仍全绿（BUG-B 形态——
    装配断线但纯函数测试无从发现），故必须有工具级集成测试钉住。
    """

    def test_local_branch_passes_resolved_env_and_masks_echo(self, monkeypatch):
        from types import SimpleNamespace as NS

        import deerflow.sandbox.tools as tools_mod

        captured = {}

        class _FakeSandbox:
            def execute_command(self, command, timeout=None, env=None):
                captured["env"] = env
                captured["timeout"] = timeout
                return "echo back: sk-wired-secret-987654"

        runtime = NS(
            context={"__active_skill_secrets": {"MY_SKILL_KEY": "sk-wired-secret-987654"}},
            state={"thread_data": {"workspace_path": "/tmp/w", "uploads_path": "/tmp/u", "outputs_path": "/tmp/o"}},
        )
        monkeypatch.setattr(tools_mod, "ensure_sandbox_initialized", lambda rt: _FakeSandbox())
        monkeypatch.setattr(tools_mod, "is_local_sandbox", lambda rt: True)
        monkeypatch.setattr(tools_mod, "is_host_bash_allowed", lambda: True)
        monkeypatch.setattr(tools_mod, "ensure_thread_directories_exist", lambda rt: None)
        monkeypatch.setattr("deerflow.config.app_config.get_app_config", lambda: NS(sandbox=NS(bash_output_max_chars=20000, bash_command_timeout=300)))

        out = tools_mod.bash_tool.func(runtime, description="d", command="echo hi")
        assert captured["env"] == {"MY_SKILL_KEY": "sk-wired-secret-987654"}
        assert "sk-wired-secret-987654" not in out  # 回显打码
        assert "***" in out

    def test_local_branch_restricted_host_injection_only_allowlisted_keys(self, monkeypatch):
        """白名单公共技能 → 只注入两个允许的宿主键，且回显打码。"""
        from types import SimpleNamespace as NS

        import deerflow.sandbox.tools as tools_mod
        from deerflow.skills.types import SecretRequirement

        captured = {}

        class _FakeSandbox:
            def execute_command(self, command, timeout=None, env=None):
                captured["env"] = env
                return "echo back: sk-wired-qihu-123456"

        skill = NS(
            name="ppt-master",
            category="public",
            enabled=True,
            required_secrets=(
                SecretRequirement("QIHU_API_KEY"),
                SecretRequirement("DATABASE_URL"),
            ),
        )
        monkeypatch.setattr(
            "deerflow.skills.load_skills",
            lambda *, enabled_only=False, strict_extensions_config=False: [skill],
        )
        monkeypatch.setenv("QIHU_API_KEY", "sk-wired-qihu-123456")
        monkeypatch.setenv("DATABASE_URL", "postgres://user:pw@host/db")
        tools_mod._restricted_declared_names_cache.clear()

        runtime = NS(
            context=None,
            config={"metadata": {"available_skills": ["ppt-master"]}},
            state={"thread_data": {"workspace_path": "/tmp/w", "uploads_path": "/tmp/u", "outputs_path": "/tmp/o"}},
        )
        monkeypatch.setattr(tools_mod, "ensure_sandbox_initialized", lambda rt: _FakeSandbox())
        monkeypatch.setattr(tools_mod, "is_local_sandbox", lambda rt: True)
        monkeypatch.setattr(tools_mod, "is_host_bash_allowed", lambda: True)
        monkeypatch.setattr(tools_mod, "ensure_thread_directories_exist", lambda rt: None)
        monkeypatch.setattr(
            "deerflow.config.app_config.get_app_config",
            lambda: NS(sandbox=NS(bash_output_max_chars=20000, bash_command_timeout=300)),
        )

        try:
            out = tools_mod.bash_tool.func(runtime, description="d", command="echo hi")
        finally:
            tools_mod._restricted_declared_names_cache.clear()

        assert captured["env"] == {"QIHU_API_KEY": "sk-wired-qihu-123456"}
        assert "sk-wired-qihu-123456" not in out
        assert "***" in out
