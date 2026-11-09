"""本地模式子进程 env 清洗（P1-S2，2026-09-30 上游移植）。"""

from __future__ import annotations

from pathlib import Path

from deerflow.sandbox.local.local_sandbox import LocalSandbox


def _make_sandbox(tmp_path: Path) -> LocalSandbox:
    return LocalSandbox("local")


def test_execute_command_scrubs_platform_secrets_from_inherited_env(tmp_path, monkeypatch):
    """本地 bash 子进程不得看到网关进程继承来的平台密钥（B2 第一道=不注入）。"""
    sandbox = _make_sandbox(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "platform-secret-must-vanish")
    monkeypatch.setenv("DEERFLOW_PLAIN", "ok-to-see")

    output = sandbox.execute_command(
        'echo "k=${OPENAI_API_KEY:-unset};p=${DEERFLOW_PLAIN:-unset};path=${PATH:+set}"',
        timeout=10,
    )

    assert "platform-secret-must-vanish" not in output
    assert "k=unset" in output
    assert "p=ok-to-see" in output
    assert "path=set" in output


def test_execute_command_emergency_off_lever_restores_inherit(tmp_path, monkeypatch):
    """DEER_FLOW_SANDBOX_ENV_POLICY=off = 部署事故紧急杆（R-P1-2）。"""
    sandbox = _make_sandbox(tmp_path)
    monkeypatch.setenv("DEER_FLOW_SANDBOX_ENV_POLICY", "off")
    monkeypatch.setenv("SANDBOX_POLICY_PROBE_TOKEN", "legacy-visible")

    output = sandbox.execute_command('echo "v=${SANDBOX_POLICY_PROBE_TOKEN:-unset}"', timeout=10)

    assert "v=legacy-visible" in output


def test_execute_command_env_injection_wins_and_scrubbed_stay_scrubbed(tmp_path, monkeypatch):
    """评审二轮修复（H1 消费端-本地模式）：env= 显式注入覆盖；未注入的密钥仍被清洗。"""
    sandbox = _make_sandbox(tmp_path)
    monkeypatch.setenv("DEERFLOW_PLAIN", "plain-ok")
    monkeypatch.setenv("OPENAI_API_KEY", "platform-must-stay-hidden")

    output = sandbox.execute_command(
        'echo "i=${INJECTED_KEY:-unset};p=${DEERFLOW_PLAIN:-unset};o=${OPENAI_API_KEY:-unset}"',
        timeout=10,
        env={"INJECTED_KEY": "injected-visible"},
    )

    assert "i=injected-visible" in output
    assert "p=plain-ok" in output
    assert "o=unset" in output  # 平台密钥依旧不进子进程
