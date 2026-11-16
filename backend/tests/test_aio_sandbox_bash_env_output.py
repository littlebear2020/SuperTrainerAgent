"""aio_sandbox bash.exec 通道返回形状回归（2026-10-13 公司服务器事故）。

bash.exec 的 data 是 ``stdout``/``stderr``/``exit_code``（agent_sandbox SDK
BashExecResult），与 shell.exec_command 的 ``output``/``console`` 不同族。
事故形态：包装按 shell 形状读 ``result.data.output`` → AttributeError 被
except 吞掉 → 工具输出 ``'BashExecResult' object has no attribute 'output'``
（命令实际执行成功，仅回传崩坏）。离线验证：stub 客户端注入真实返回形状，
断言 stdout/stderr 正确拼合回传——无需真实沙箱容器。
"""

from types import SimpleNamespace

import pytest

from deerflow.community.aio_sandbox.aio_sandbox import SANDBOX_DEAD_MARKER, AioSandbox


def _sandbox_with_bash_result(data):
    s = AioSandbox(id="t", base_url="http://offline.invalid")
    s._client = SimpleNamespace(bash=SimpleNamespace(exec=lambda **kw: SimpleNamespace(data=data)))
    return s


def test_bash_env_output_combines_stdout_and_stderr():
    s = _sandbox_with_bash_result(SimpleNamespace(stdout="out-line\n", stderr="err-line\n", exit_code=0))
    out = s.execute_command("echo hi", env={"K": "V"})
    assert "out-line" in out
    assert "err-line" in out


def test_bash_env_output_empty_data_returns_empty():
    s = _sandbox_with_bash_result(None)
    assert s.execute_command("x", env={"K": "V"}) == ""


def test_bash_env_output_nonzero_exit_is_explicit_even_without_output():
    s = _sandbox_with_bash_result(SimpleNamespace(stdout=None, stderr=None, exit_code=1))
    out = s.execute_command("x", env={"K": "V"})
    assert "Exit Code: 1" in out


def test_bash_env_reuses_returned_session_id_to_preserve_cwd():
    s = AioSandbox(id="t", base_url="http://offline.invalid")
    sessions: dict[str, str] = {}
    calls = []

    def bash_exec(**kwargs):
        calls.append(kwargs)
        session_id = kwargs.get("session_id") or f"session-{len(calls)}"
        cwd = sessions.get(session_id, "/")
        command = kwargs["command"]
        if command.startswith("cd "):
            cwd = command.removeprefix("cd ")
            sessions[session_id] = cwd
            stdout = ""
        else:
            stdout = f"{cwd}\n"
        return SimpleNamespace(data=SimpleNamespace(session_id=session_id, stdout=stdout, stderr=None, exit_code=0))

    s._client = SimpleNamespace(bash=SimpleNamespace(exec=bash_exec))

    s.execute_command("cd /mnt/user-data/workspace", env={"K": "secret-value"})
    out = s.execute_command("pwd", env={"K": "secret-value"})

    assert out == "/mnt/user-data/workspace\n"
    assert calls[1]["session_id"] == "session-1"
    assert [call["command"] for call in calls] == ["cd /mnt/user-data/workspace", "pwd"]


@pytest.mark.parametrize(
    "error_message",
    [
        "bash session not found",
        "bash session expired",
        "bash session is invalid",
        "bash session is closed",
        "unknown session",
    ],
)
def test_bash_env_invalid_session_retries_without_id_and_reuses_replacement(error_message):
    s = AioSandbox(id="t", base_url="http://offline.invalid")
    s._bash_session_id = "stale-session"
    calls = []

    def bash_exec(**kwargs):
        calls.append(kwargs)
        session_id = kwargs.get("session_id")
        if session_id == "stale-session":
            raise RuntimeError(error_message)
        if session_id is None:
            return SimpleNamespace(data=SimpleNamespace(session_id="fresh-session", stdout="recovered\n", stderr=None, exit_code=0))
        if session_id == "fresh-session":
            return SimpleNamespace(data=SimpleNamespace(session_id="fresh-session", stdout="reused\n", stderr=None, exit_code=0))
        raise AssertionError(f"unexpected session: {session_id}")

    s._client = SimpleNamespace(bash=SimpleNamespace(exec=bash_exec))

    assert s.execute_command("pwd", env={"K": "V"}) == "recovered\n"
    assert s.execute_command("pwd", env={"K": "V"}) == "reused\n"
    assert len(calls) == 3
    assert "session_id" not in calls[1]
    assert calls[2]["session_id"] == "fresh-session"


def test_bash_env_recovery_preserves_output_exit_code_and_env_channel():
    s = AioSandbox(id="t", base_url="http://offline.invalid")
    s._bash_session_id = "stale-session"
    calls = []

    def bash_exec(**kwargs):
        calls.append(kwargs)
        if kwargs.get("session_id") == "stale-session":
            raise RuntimeError("session_id not found")
        return SimpleNamespace(data=SimpleNamespace(session_id="fresh-session", stdout="partial\n", stderr="failure\n", exit_code=7))

    s._client = SimpleNamespace(bash=SimpleNamespace(exec=bash_exec))

    out = s.execute_command("python render.py", env={"K": "secret-value"})

    assert "partial" in out
    assert "failure" in out
    assert "Exit Code: 7" in out
    assert len(calls) == 2
    assert "session_id" not in calls[1]
    assert [call["command"] for call in calls] == ["python render.py", "python render.py"]
    assert all(call["env"] == {"K": "secret-value"} for call in calls)


def test_bash_env_generic_error_does_not_retry_or_clear_session():
    s = AioSandbox(id="t", base_url="http://offline.invalid")
    s._bash_session_id = "current-session"
    calls = []

    def bash_exec(**kwargs):
        calls.append(kwargs)
        raise RuntimeError("invalid argument: malformed command")

    s._client = SimpleNamespace(bash=SimpleNamespace(exec=bash_exec))

    out = s.execute_command("python render.py", env={"K": "V"})

    assert out.startswith("Error:")
    assert len(calls) == 1
    assert s._bash_session_id == "current-session"


def test_bash_env_dead_sandbox_does_not_retry_even_with_session_not_found_message():
    s = AioSandbox(id="t", base_url="http://offline.invalid")
    s._bash_session_id = "stale-session"
    calls = []

    def bash_exec(**kwargs):
        calls.append(kwargs)
        raise RuntimeError("session not found because container is not running")

    s._client = SimpleNamespace(bash=SimpleNamespace(exec=bash_exec))

    out = s.execute_command("pwd", env={"K": "V"})

    assert SANDBOX_DEAD_MARKER in out
    assert len(calls) == 1
