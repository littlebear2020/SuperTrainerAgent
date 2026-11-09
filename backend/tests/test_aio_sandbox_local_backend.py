import logging
import os
from types import SimpleNamespace

import pytest

from deerflow.community.aio_sandbox.local_backend import (
    LocalContainerBackend,
    _format_container_command_for_log,
    _format_container_mount,
    _redact_container_command_for_log,
    _resolve_docker_bind_host,
)


def test_format_container_mount_uses_mount_syntax_for_docker_windows_paths():
    args = _format_container_mount("docker", "D:/deer-flow/backend/.deer-flow/threads", "/mnt/threads", False)

    assert args == [
        "--mount",
        "type=bind,src=D:/deer-flow/backend/.deer-flow/threads,dst=/mnt/threads",
    ]


def test_format_container_mount_marks_docker_readonly_mounts():
    args = _format_container_mount("docker", "/host/path", "/mnt/path", True)

    assert args == [
        "--mount",
        "type=bind,src=/host/path,dst=/mnt/path,readonly",
    ]


def test_format_container_mount_keeps_volume_syntax_for_apple_container():
    args = _format_container_mount("container", "/host/path", "/mnt/path", True)

    assert args == [
        "-v",
        "/host/path:/mnt/path:ro",
    ]


def test_redact_container_command_for_log_redacts_env_values():
    redacted = _redact_container_command_for_log(
        [
            "docker",
            "run",
            "-e",
            "API_KEY=secret-value",
            "--env=TOKEN=token-value",
            "--name",
            "sandbox",
            "image",
        ]
    )

    assert "API_KEY=<redacted>" in redacted
    assert "--env=TOKEN=<redacted>" in redacted
    assert "secret-value" not in " ".join(redacted)
    assert "token-value" not in " ".join(redacted)


def test_redact_container_command_for_log_keeps_inherited_env_names():
    redacted = _redact_container_command_for_log(
        [
            "docker",
            "run",
            "-e",
            "API_KEY",
            "--env=TOKEN",
            "--name",
            "sandbox",
            "image",
        ]
    )

    assert redacted == [
        "docker",
        "run",
        "-e",
        "API_KEY",
        "--env=TOKEN",
        "--name",
        "sandbox",
        "image",
    ]


def test_format_container_command_for_log_uses_windows_quoting(monkeypatch):
    monkeypatch.setattr(os, "name", "nt")

    command = _format_container_command_for_log(["docker", "run", "--name", "sandbox one", "image"])

    assert command == 'docker run --name "sandbox one" image'


def test_start_container_logs_redacted_env_values(monkeypatch, caplog):
    backend = LocalContainerBackend(
        image="sandbox:latest",
        base_port=8080,
        container_prefix="sandbox",
        config_mounts=[],
        environment={"API_KEY": "secret-value", "NORMAL": "visible-value"},
    )
    monkeypatch.setattr(backend, "_runtime", "docker")

    captured_cmd: list[str] = []

    def fake_run(cmd, **kwargs):
        captured_cmd.extend(cmd)
        return SimpleNamespace(stdout="container-id\n", stderr="", returncode=0)

    monkeypatch.setattr("subprocess.run", fake_run)

    with caplog.at_level(logging.INFO, logger="deerflow.community.aio_sandbox.local_backend"):
        backend._start_container("sandbox-test", 18080)

    joined_cmd = " ".join(captured_cmd)
    assert "API_KEY=secret-value" in joined_cmd
    assert "NORMAL=visible-value" in joined_cmd

    log_output = "\n".join(record.getMessage() for record in caplog.records)
    assert "API_KEY=<redacted>" in log_output
    assert "NORMAL=<redacted>" in log_output
    assert "secret-value" not in log_output
    assert "visible-value" not in log_output


def _capture_start_container_command(monkeypatch, backend: LocalContainerBackend, runtime: str = "docker") -> list[str]:
    monkeypatch.setattr(backend, "_runtime", runtime)
    captured_cmd: list[str] = []

    def fake_run(cmd, **kwargs):
        captured_cmd.extend(cmd)
        return SimpleNamespace(stdout="container-id\n", stderr="", returncode=0)

    monkeypatch.setattr("subprocess.run", fake_run)
    backend._start_container("sandbox-test", 18080)
    return captured_cmd


def test_resolve_docker_bind_host_defaults_loopback_for_localhost(monkeypatch):
    monkeypatch.delenv("DEER_FLOW_SANDBOX_BIND_HOST", raising=False)
    monkeypatch.delenv("DEER_FLOW_SANDBOX_HOST", raising=False)

    assert _resolve_docker_bind_host() == "127.0.0.1"


def test_resolve_docker_bind_host_keeps_dood_compatibility(monkeypatch):
    """B3（P1-S2，2026-09-30 上游移植）：非回环 sandbox host 不再绑 0.0.0.0——
    解析到 daemon host-gateway 映射的实际地址，发布端口与网关实际连接的地址一致。"""
    monkeypatch.delenv("DEER_FLOW_SANDBOX_BIND_HOST", raising=False)
    monkeypatch.setenv("DEER_FLOW_SANDBOX_HOST", "host.docker.internal")
    # 钉死非 Desktop：本机 Docker Desktop 在跑时进程级缓存探测会翻转分支
    # 走宿主回环（那由专门用例覆盖），这里测的是解析路径。
    monkeypatch.setattr(
        "deerflow.community.aio_sandbox.local_backend._docker_server_is_desktop",
        lambda: False,
    )
    monkeypatch.setattr(
        "deerflow.community.aio_sandbox.local_backend._resolve_sandbox_host_address",
        lambda host: "172.17.0.1",
    )

    assert _resolve_docker_bind_host() == "172.17.0.1"


def test_resolve_docker_bind_host_unresolvable_host_falls_back_to_bridge_gateway(monkeypatch, caplog):
    """解析失败（异常 DNS/自定义主机名未注册）→ 默认桥网关兜底 + 警告，绝不 0.0.0.0。"""
    import logging as _logging

    monkeypatch.delenv("DEER_FLOW_SANDBOX_BIND_HOST", raising=False)
    monkeypatch.setenv("DEER_FLOW_SANDBOX_HOST", "unresolvable.invalid")
    monkeypatch.setattr(
        "deerflow.community.aio_sandbox.local_backend._resolve_sandbox_host_address",
        lambda host: None,
    )
    monkeypatch.setattr(
        "deerflow.community.aio_sandbox.local_backend._docker_bridge_gateway_ip",
        lambda: None,
    )

    with caplog.at_level(_logging.WARNING, logger="deerflow.community.aio_sandbox.local_backend"):
        assert _resolve_docker_bind_host() == "172.17.0.1"
    assert any("falling back to the default bridge gateway" in r.getMessage() for r in caplog.records)


def test_resolve_docker_bind_host_desktop_daemon_binds_host_loopback(monkeypatch):
    """Docker Desktop（含 Linux DooD 网关探测到 Desktop daemon）对
    host.docker.internal 走宿主回环。"""
    monkeypatch.delenv("DEER_FLOW_SANDBOX_BIND_HOST", raising=False)
    monkeypatch.setenv("DEER_FLOW_SANDBOX_HOST", "host.docker.internal")
    monkeypatch.setattr(
        "deerflow.community.aio_sandbox.local_backend._docker_server_is_desktop",
        lambda: True,
    )

    assert _resolve_docker_bind_host() == "127.0.0.1"


def test_resolve_docker_bind_host_broad_bind_only_via_explicit_override(monkeypatch):
    """0.0.0.0 只能经显式覆盖拿到（运维回滚杆），任何推导路径都不给。"""
    monkeypatch.setenv("DEER_FLOW_SANDBOX_HOST", "host.docker.internal")
    monkeypatch.setenv("DEER_FLOW_SANDBOX_BIND_HOST", "0.0.0.0")

    assert _resolve_docker_bind_host() == "0.0.0.0"


def test_resolve_docker_bind_host_uses_ipv6_loopback_for_ipv6_sandbox_host(monkeypatch):
    monkeypatch.delenv("DEER_FLOW_SANDBOX_BIND_HOST", raising=False)
    monkeypatch.setenv("DEER_FLOW_SANDBOX_HOST", "[::1]")

    assert _resolve_docker_bind_host() == "[::1]"


def test_resolve_docker_bind_host_logs_selected_bind_reason(caplog):
    with caplog.at_level(logging.DEBUG, logger="deerflow.community.aio_sandbox.local_backend"):
        assert _resolve_docker_bind_host(sandbox_host="localhost", bind_host="") == "127.0.0.1"

    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "Docker sandbox bind: 127.0.0.1 (loopback default)" in messages


def test_resolve_docker_bind_host_allows_explicit_override(monkeypatch):
    monkeypatch.setenv("DEER_FLOW_SANDBOX_HOST", "localhost")
    monkeypatch.setenv("DEER_FLOW_SANDBOX_BIND_HOST", "192.0.2.10")

    assert _resolve_docker_bind_host() == "192.0.2.10"


def test_start_container_binds_local_docker_port_to_loopback_by_default(monkeypatch):
    backend = LocalContainerBackend(
        image="sandbox:latest",
        base_port=8080,
        container_prefix="sandbox",
        config_mounts=[],
        environment={},
    )
    monkeypatch.delenv("DEER_FLOW_SANDBOX_HOST", raising=False)
    monkeypatch.delenv("DEER_FLOW_SANDBOX_BIND_HOST", raising=False)

    captured_cmd = _capture_start_container_command(monkeypatch, backend)

    assert captured_cmd[captured_cmd.index("-p") + 1] == "127.0.0.1:18080:8080"


def test_start_container_keeps_broad_bind_for_dood_sandbox_host(monkeypatch):
    """B3：DooD host 经解析地址发布（不再是 0.0.0.0 全接口暴露）。"""
    backend = LocalContainerBackend(
        image="sandbox:latest",
        base_port=8080,
        container_prefix="sandbox",
        config_mounts=[],
        environment={},
    )
    monkeypatch.setenv("DEER_FLOW_SANDBOX_HOST", "host.docker.internal")
    monkeypatch.delenv("DEER_FLOW_SANDBOX_BIND_HOST", raising=False)
    monkeypatch.setattr(
        "deerflow.community.aio_sandbox.local_backend._docker_server_is_desktop",
        lambda: False,
    )
    monkeypatch.setattr(
        "deerflow.community.aio_sandbox.local_backend._resolve_sandbox_host_address",
        lambda host: "172.17.0.1",
    )

    captured_cmd = _capture_start_container_command(monkeypatch, backend)

    assert captured_cmd[captured_cmd.index("-p") + 1] == "172.17.0.1:18080:8080"


def test_start_container_binds_ipv6_sandbox_host_to_ipv6_loopback(monkeypatch):
    backend = LocalContainerBackend(
        image="sandbox:latest",
        base_port=8080,
        container_prefix="sandbox",
        config_mounts=[],
        environment={},
    )
    monkeypatch.setenv("DEER_FLOW_SANDBOX_HOST", "[::1]")
    monkeypatch.delenv("DEER_FLOW_SANDBOX_BIND_HOST", raising=False)

    captured_cmd = _capture_start_container_command(monkeypatch, backend)

    assert captured_cmd[captured_cmd.index("-p") + 1] == "[::1]:18080:8080"


def test_start_container_keeps_apple_container_port_format(monkeypatch):
    backend = LocalContainerBackend(
        image="sandbox:latest",
        base_port=8080,
        container_prefix="sandbox",
        config_mounts=[],
        environment={},
    )
    monkeypatch.setenv("DEER_FLOW_SANDBOX_BIND_HOST", "127.0.0.1")

    captured_cmd = _capture_start_container_command(monkeypatch, backend, runtime="container")

    assert captured_cmd[captured_cmd.index("-p") + 1] == "18080:8080"


def test_start_container_attaches_to_sandbox_network_when_env_set(monkeypatch):
    backend = LocalContainerBackend(
        image="sandbox:latest",
        base_port=8080,
        container_prefix="sandbox",
        config_mounts=[],
        environment={},
    )
    monkeypatch.setenv("DEER_FLOW_SANDBOX_NETWORK", "deerflow-deploy_deer-flow")
    monkeypatch.delenv("DEER_FLOW_SANDBOX_HOST", raising=False)
    monkeypatch.delenv("DEER_FLOW_SANDBOX_BIND_HOST", raising=False)

    captured_cmd = _capture_start_container_command(monkeypatch, backend)

    assert "--network" in captured_cmd
    assert captured_cmd[captured_cmd.index("--network") + 1] == "deerflow-deploy_deer-flow"


def test_start_container_omits_network_flag_when_env_unset(monkeypatch):
    backend = LocalContainerBackend(
        image="sandbox:latest",
        base_port=8080,
        container_prefix="sandbox",
        config_mounts=[],
        environment={},
    )
    monkeypatch.delenv("DEER_FLOW_SANDBOX_NETWORK", raising=False)
    monkeypatch.delenv("DEER_FLOW_SANDBOX_HOST", raising=False)
    monkeypatch.delenv("DEER_FLOW_SANDBOX_BIND_HOST", raising=False)

    captured_cmd = _capture_start_container_command(monkeypatch, backend)

    assert "--network" not in captured_cmd


# ── P1-S2（2026-09-30 上游移植）：容器加固 ──────────────────────────────────


def _hardening_backend() -> LocalContainerBackend:
    return LocalContainerBackend(
        image="sandbox:latest",
        base_port=8080,
        container_prefix="sandbox",
        config_mounts=[],
        environment={},
    )


def test_start_container_hardening_default_flags(monkeypatch):
    """默认档：cap-drop=ALL+最小回加、no-new-privileges、pids-limit 512、
    seccomp=unconfined（Chromium 兼容，上游默认同）。"""
    for lever in (
        "DEER_FLOW_SANDBOX_IMAGE_STARTUP_CAPS",
        "DEER_FLOW_SANDBOX_SECCOMP_PROFILE",
        "DEER_FLOW_SANDBOX_SECCOMP_UNCONFINED",
        "DEER_FLOW_SANDBOX_PIDS_LIMIT",
        "DEER_FLOW_SANDBOX_NETWORK",
        "DEER_FLOW_SANDBOX_HOST",
        "DEER_FLOW_SANDBOX_BIND_HOST",
        "DEER_FLOW_SANDBOX_CONTAINER_USER",
    ):
        monkeypatch.delenv(lever, raising=False)

    captured_cmd = _capture_start_container_command(monkeypatch, _hardening_backend())

    assert "--cap-drop=ALL" in captured_cmd
    for cap in ("--cap-add=CHOWN", "--cap-add=FOWNER", "--cap-add=SETUID", "--cap-add=SETGID", "--cap-add=DAC_OVERRIDE"):
        assert cap in captured_cmd
    security_opts = [captured_cmd[i + 1] for i, arg in enumerate(captured_cmd) if arg == "--security-opt"]
    assert "no-new-privileges" in security_opts
    assert "seccomp=unconfined" in security_opts
    pids_idx = captured_cmd.index("--pids-limit")
    assert captured_cmd[pids_idx + 1] == "512"


def test_start_container_caps_minimal_set_when_startup_caps_disabled(monkeypatch):
    """DEER_FLOW_SANDBOX_IMAGE_STARTUP_CAPS=0：预初始化非 root 镜像可整组撤销
    兼容能力，只留 cap-drop=ALL + no-new-privileges。"""
    monkeypatch.setenv("DEER_FLOW_SANDBOX_IMAGE_STARTUP_CAPS", "0")
    monkeypatch.delenv("DEER_FLOW_SANDBOX_SECCOMP_PROFILE", raising=False)
    monkeypatch.delenv("DEER_FLOW_SANDBOX_SECCOMP_UNCONFINED", raising=False)
    monkeypatch.delenv("DEER_FLOW_SANDBOX_HOST", raising=False)
    monkeypatch.delenv("DEER_FLOW_SANDBOX_BIND_HOST", raising=False)
    monkeypatch.delenv("DEER_FLOW_SANDBOX_NETWORK", raising=False)

    captured_cmd = _capture_start_container_command(monkeypatch, _hardening_backend())

    assert "--cap-drop=ALL" in captured_cmd
    assert "--cap-add=CHOWN" not in captured_cmd
    security_opts = [captured_cmd[i + 1] for i, arg in enumerate(captured_cmd) if arg == "--security-opt"]
    assert "no-new-privileges" in security_opts


def test_start_container_seccomp_profile_override(monkeypatch):
    monkeypatch.setenv("DEER_FLOW_SANDBOX_SECCOMP_PROFILE", "/etc/docker/seccomp-chromium.json")
    monkeypatch.delenv("DEER_FLOW_SANDBOX_SECCOMP_UNCONFINED", raising=False)
    monkeypatch.delenv("DEER_FLOW_SANDBOX_IMAGE_STARTUP_CAPS", raising=False)
    monkeypatch.delenv("DEER_FLOW_SANDBOX_HOST", raising=False)
    monkeypatch.delenv("DEER_FLOW_SANDBOX_BIND_HOST", raising=False)
    monkeypatch.delenv("DEER_FLOW_SANDBOX_NETWORK", raising=False)

    captured_cmd = _capture_start_container_command(monkeypatch, _hardening_backend())

    security_opts = [captured_cmd[i + 1] for i, arg in enumerate(captured_cmd) if arg == "--security-opt"]
    assert "seccomp=/etc/docker/seccomp-chromium.json" in security_opts
    assert "seccomp=unconfined" not in security_opts


def test_start_container_seccomp_builtin_opt_out(monkeypatch):
    monkeypatch.delenv("DEER_FLOW_SANDBOX_SECCOMP_PROFILE", raising=False)
    monkeypatch.setenv("DEER_FLOW_SANDBOX_SECCOMP_UNCONFINED", "0")
    monkeypatch.delenv("DEER_FLOW_SANDBOX_IMAGE_STARTUP_CAPS", raising=False)
    monkeypatch.delenv("DEER_FLOW_SANDBOX_HOST", raising=False)
    monkeypatch.delenv("DEER_FLOW_SANDBOX_BIND_HOST", raising=False)
    monkeypatch.delenv("DEER_FLOW_SANDBOX_NETWORK", raising=False)

    captured_cmd = _capture_start_container_command(monkeypatch, _hardening_backend())

    security_opts = [captured_cmd[i + 1] for i, arg in enumerate(captured_cmd) if arg == "--security-opt"]
    assert "seccomp=builtin" in security_opts


def test_start_container_pids_limit_env_override_and_disable(monkeypatch):
    monkeypatch.setenv("DEER_FLOW_SANDBOX_PIDS_LIMIT", "1024")
    monkeypatch.delenv("DEER_FLOW_SANDBOX_SECCOMP_PROFILE", raising=False)
    monkeypatch.delenv("DEER_FLOW_SANDBOX_SECCOMP_UNCONFINED", raising=False)
    monkeypatch.delenv("DEER_FLOW_SANDBOX_IMAGE_STARTUP_CAPS", raising=False)
    monkeypatch.delenv("DEER_FLOW_SANDBOX_HOST", raising=False)
    monkeypatch.delenv("DEER_FLOW_SANDBOX_BIND_HOST", raising=False)
    monkeypatch.delenv("DEER_FLOW_SANDBOX_NETWORK", raising=False)

    captured = _capture_start_container_command(monkeypatch, _hardening_backend())
    assert captured[captured.index("--pids-limit") + 1] == "1024"

    monkeypatch.setenv("DEER_FLOW_SANDBOX_PIDS_LIMIT", "none")
    captured = _capture_start_container_command(monkeypatch, _hardening_backend())
    assert "--pids-limit" not in captured


@pytest.mark.parametrize(
    "network",
    ["host", "name=host", "gw-priority=0,name=host", "container:some-container", "none", "name=none"],
)
def test_start_container_rejects_bind_voiding_networks(monkeypatch, network):
    """host/container:<name>/none 会作废端口发布（Docker 丢弃 -p 或共享网络
    命名空间/只剩回环）——拒绝而非静默丢失加固 bind（含 name= 长语法规避）。"""
    monkeypatch.setenv("DEER_FLOW_SANDBOX_NETWORK", network)
    monkeypatch.delenv("DEER_FLOW_SANDBOX_HOST", raising=False)
    monkeypatch.delenv("DEER_FLOW_SANDBOX_BIND_HOST", raising=False)

    with pytest.raises(RuntimeError, match="DEER_FLOW_SANDBOX_NETWORK"):
        _capture_start_container_command(monkeypatch, _hardening_backend())


def test_start_container_accepts_egress_controlled_bridge_network(monkeypatch):
    monkeypatch.setenv("DEER_FLOW_SANDBOX_NETWORK", "deerflow-deploy_deer-flow")
    monkeypatch.delenv("DEER_FLOW_SANDBOX_HOST", raising=False)
    monkeypatch.delenv("DEER_FLOW_SANDBOX_BIND_HOST", raising=False)

    captured_cmd = _capture_start_container_command(monkeypatch, _hardening_backend())

    assert captured_cmd[captured_cmd.index("--network") + 1] == "deerflow-deploy_deer-flow"


def test_start_container_apple_container_skips_docker_only_hardening(monkeypatch):
    monkeypatch.delenv("DEER_FLOW_SANDBOX_NETWORK", raising=False)

    captured_cmd = _capture_start_container_command(monkeypatch, _hardening_backend(), runtime="apple_container")

    assert "--cap-drop=ALL" not in captured_cmd
    assert "--security-opt" not in captured_cmd
    assert "--pids-limit" not in captured_cmd


# ── 评审修复（2026-09-30）：environment 非空 fail-safe 告警 ──────────────────


def test_nonempty_environment_warns_without_leaking_values(caplog):
    """P1-S2 收尾 fail-safe：config.yaml 未同步清空时，构造即告警（键名可见、值绝不落日志）。"""
    import logging as _logging

    backend = LocalContainerBackend(
        image="sandbox:latest",
        base_port=8080,
        container_prefix="sandbox",
        config_mounts=[],
        environment={"QIHU_API_KEY": "super-secret-value", "NODE_ENV": "production"},
    )

    with caplog.at_level(_logging.WARNING, logger="deerflow.community.aio_sandbox.local_backend"):
        backend._warn_if_environment_nonempty()

    messages = "\n".join(r.getMessage() for r in caplog.records)
    assert "QIHU_API_KEY" in messages  # 键名可见（可操作）
    assert "NODE_ENV" in messages
    assert "secret" not in messages.replace("super-secret", "")  # 值不落日志
    assert any("P1-S2" in r.getMessage() or "密钥" in r.getMessage() for r in caplog.records)


def test_empty_environment_does_not_warn(caplog):
    import logging as _logging

    backend = LocalContainerBackend(
        image="sandbox:latest",
        base_port=8080,
        container_prefix="sandbox",
        config_mounts=[],
        environment={},
    )

    with caplog.at_level(_logging.WARNING, logger="deerflow.community.aio_sandbox.local_backend"):
        backend._warn_if_environment_nonempty()

    assert not caplog.records
