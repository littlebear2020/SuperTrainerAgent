"""required-secrets 注入链装配端（评审二轮修复 H1，2026-09-30）。

上游链路：skill_activation_middleware（未移植）→ ``__active_skill_secrets`` →
bash 工具 env 注入。fork 简化装配：``_resolve_injected_secrets`` 读两源——
① run context 的 ``__active_skill_secrets`` / ``secrets``（上游原生，P2/P3
middleware 接通后生效）；② ``DEER_FLOW_SKILL_SECRET_AUTOBIND=1`` 时从网关进程
env 按启用技能声明并集代填（部署侧显式开启的受控通道）。
"""

from __future__ import annotations

from types import SimpleNamespace

import deerflow.sandbox.tools as tools
from deerflow.sandbox.tools import _resolve_injected_secrets, mask_secret_values_in_output


def _runtime_with_context(ctx: dict | None) -> SimpleNamespace:
    return SimpleNamespace(context=ctx, state=None)


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
