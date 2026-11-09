import builtins
from types import SimpleNamespace

import deerflow.sandbox.local.local_sandbox as local_sandbox
from deerflow.sandbox.local.local_sandbox import LocalSandbox


def _open(base, file, mode="r", *args, **kwargs):
    if "b" in mode:
        return base(file, mode, *args, **kwargs)
    return base(file, mode, *args, encoding=kwargs.pop("encoding", "gbk"), **kwargs)


def test_read_file_uses_utf8_on_windows_locale(tmp_path, monkeypatch):
    path = tmp_path / "utf8.txt"
    text = "\u201cutf8\u201d"
    path.write_text(text, encoding="utf-8")
    base = builtins.open

    monkeypatch.setattr(local_sandbox, "open", lambda file, mode="r", *args, **kwargs: _open(base, file, mode, *args, **kwargs), raising=False)

    assert LocalSandbox("t").read_file(str(path)) == text


def test_write_file_uses_utf8_on_windows_locale(tmp_path, monkeypatch):
    path = tmp_path / "utf8.txt"
    text = "emoji \U0001f600"
    base = builtins.open

    monkeypatch.setattr(local_sandbox, "open", lambda file, mode="r", *args, **kwargs: _open(base, file, mode, *args, **kwargs), raising=False)

    LocalSandbox("t").write_file(str(path), text)

    assert path.read_text(encoding="utf-8") == text


def test_get_shell_prefers_posix_shell_from_path_before_windows_fallback(monkeypatch):
    monkeypatch.setattr(local_sandbox.os, "name", "nt")
    monkeypatch.setattr(LocalSandbox, "_find_first_available_shell", lambda candidates: r"C:\Program Files\Git\bin\sh.exe" if candidates == ("/bin/zsh", "/bin/bash", "/bin/sh", "sh") else None)

    assert LocalSandbox._get_shell() == r"C:\Program Files\Git\bin\sh.exe"


def test_get_shell_uses_powershell_fallback_on_windows(monkeypatch):
    calls: list[tuple[str, ...]] = []

    def fake_find(candidates: tuple[str, ...]) -> str | None:
        calls.append(candidates)
        if candidates == ("/bin/zsh", "/bin/bash", "/bin/sh", "sh"):
            return None
        return r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"

    monkeypatch.setattr(local_sandbox.os, "name", "nt")
    monkeypatch.setattr(local_sandbox.os, "environ", {"SystemRoot": r"C:\Windows"})
    monkeypatch.setattr(LocalSandbox, "_find_first_available_shell", fake_find)

    assert LocalSandbox._get_shell() == r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
    assert calls[1] == (
        "pwsh",
        "pwsh.exe",
        "powershell",
        "powershell.exe",
        r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
        "cmd.exe",
    )


def test_get_shell_uses_cmd_as_last_windows_fallback(monkeypatch):
    def fake_find(candidates: tuple[str, ...]) -> str | None:
        if candidates == ("/bin/zsh", "/bin/bash", "/bin/sh", "sh"):
            return None
        return r"C:\Windows\System32\cmd.exe"

    monkeypatch.setattr(local_sandbox.os, "name", "nt")
    monkeypatch.setattr(local_sandbox.os, "environ", {"SystemRoot": r"C:\Windows"})
    monkeypatch.setattr(LocalSandbox, "_find_first_available_shell", fake_find)

    assert LocalSandbox._get_shell() == r"C:\Windows\System32\cmd.exe"


def test_execute_command_uses_powershell_command_mode_on_windows(monkeypatch):
    calls: list[tuple[object, dict]] = []

    def fake_run(*args, **kwargs):
        calls.append((args[0], kwargs))
        return SimpleNamespace(stdout="ok", stderr="", returncode=0)

    monkeypatch.setattr(local_sandbox.os, "name", "nt")
    monkeypatch.setattr(LocalSandbox, "_get_shell", staticmethod(lambda: r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"))
    monkeypatch.setattr(local_sandbox.subprocess, "run", fake_run)

    output = LocalSandbox("t").execute_command("Write-Output hello")

    assert output == "ok"
    assert len(calls) == 1
    assert calls[0][0] == [
        r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
        "-NoProfile",
        "-Command",
        "Write-Output hello",
    ]
    _kwargs = calls[0][1]
    assert (_kwargs["shell"], _kwargs["capture_output"], _kwargs["text"], _kwargs["timeout"]) == (False, True, True, None)
    # P1-S2（2026-09-30）：子进程 env 经 env_policy 清洗后显式下发，不再裸继承。
    assert isinstance(_kwargs.get("env"), dict)


def test_execute_command_uses_posix_shell_command_mode_on_windows(monkeypatch):
    calls: list[tuple[object, dict]] = []

    def fake_run(*args, **kwargs):
        calls.append((args[0], kwargs))
        return SimpleNamespace(stdout="ok", stderr="", returncode=0)

    monkeypatch.setattr(local_sandbox.os, "name", "nt")
    monkeypatch.setattr(LocalSandbox, "_get_shell", staticmethod(lambda: r"C:\Program Files\Git\bin\sh.exe"))
    monkeypatch.setattr(local_sandbox.subprocess, "run", fake_run)

    output = LocalSandbox("t").execute_command("echo hello")

    assert output == "ok"
    assert len(calls) == 1
    assert calls[0][0] == [r"C:\Program Files\Git\bin\sh.exe", "-c", "echo hello"]
    _kwargs = calls[0][1]
    assert (_kwargs["shell"], _kwargs["capture_output"], _kwargs["text"], _kwargs["timeout"]) == (False, True, True, None)
    # P1-S2（2026-09-30）：子进程 env 经 env_policy 清洗后显式下发，不再裸继承。
    assert isinstance(_kwargs.get("env"), dict)


def test_execute_command_uses_cmd_command_mode_on_windows(monkeypatch):
    calls: list[tuple[object, dict]] = []

    def fake_run(*args, **kwargs):
        calls.append((args[0], kwargs))
        return SimpleNamespace(stdout="ok", stderr="", returncode=0)

    monkeypatch.setattr(local_sandbox.os, "name", "nt")
    monkeypatch.setattr(LocalSandbox, "_get_shell", staticmethod(lambda: r"C:\Windows\System32\cmd.exe"))
    monkeypatch.setattr(local_sandbox.subprocess, "run", fake_run)

    output = LocalSandbox("t").execute_command("echo hello")

    assert output == "ok"
    assert len(calls) == 1
    assert calls[0][0] == [r"C:\Windows\System32\cmd.exe", "/c", "echo hello"]
    _kwargs = calls[0][1]
    assert (_kwargs["shell"], _kwargs["capture_output"], _kwargs["text"], _kwargs["timeout"]) == (False, True, True, None)
    # P1-S2（2026-09-30）：子进程 env 经 env_policy 清洗后显式下发，不再裸继承。
    assert isinstance(_kwargs.get("env"), dict)


def test_execute_command_rescues_partial_output_on_timeout(monkeypatch):
    """R-05 ③ (2026-08-13): TimeoutExpired must not drop captured output —
    the agent gets the partial stdout/stderr plus a timeout marker."""
    import subprocess

    def fake_run(*args, **kwargs):
        exc = subprocess.TimeoutExpired(cmd="sleep 999", timeout=600)
        exc.stdout = "progress 1\nprogress 2\n"
        exc.stderr = "warn"
        raise exc

    monkeypatch.setattr(local_sandbox.os, "name", "posix")
    monkeypatch.setattr(LocalSandbox, "_get_shell", staticmethod(lambda: "/bin/bash"))
    monkeypatch.setattr(local_sandbox.subprocess, "run", fake_run)

    output = LocalSandbox("t").execute_command("sleep 999", timeout=600)
    assert "progress 1" in output
    assert "progress 2" in output
    assert "warn" in output
    assert "[command timed out after 600s" in output
    assert "Exit Code" not in output  # no returncode on timeout


def test_execute_command_forwards_timeout_to_subprocess(monkeypatch):
    """T27 (2026-08-13): an explicit per-command timeout reaches
    subprocess.run."""
    calls: list[dict] = []

    def fake_run(*args, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(stdout="ok", stderr="", returncode=0)

    monkeypatch.setattr(local_sandbox.os, "name", "posix")
    monkeypatch.setattr(LocalSandbox, "_get_shell", staticmethod(lambda: "/bin/bash"))
    monkeypatch.setattr(local_sandbox.subprocess, "run", fake_run)

    LocalSandbox("t").execute_command("echo hi", timeout=123)
    assert calls[0]["timeout"] == 123


def test_sandbox_config_bash_command_timeout_default():
    """D9: default 300s (was hardcoded 600 in both implementations)."""
    from deerflow.config.sandbox_config import SandboxConfig

    assert SandboxConfig(use="x").bash_command_timeout == 300
