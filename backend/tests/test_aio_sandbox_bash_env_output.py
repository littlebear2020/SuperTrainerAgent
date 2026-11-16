"""aio_sandbox bash.exec 通道返回形状回归（2026-10-13 公司服务器事故）。

bash.exec 的 data 是 ``stdout``/``stderr``/``exit_code``（agent_sandbox SDK
BashExecResult），与 shell.exec_command 的 ``output``/``console`` 不同族。
事故形态：包装按 shell 形状读 ``result.data.output`` → AttributeError 被
except 吞掉 → 工具输出 ``'BashExecResult' object has no attribute 'output'``
（命令实际执行成功，仅回传崩坏）。离线验证：stub 客户端注入真实返回形状，
断言 stdout/stderr 正确拼合回传——无需真实沙箱容器。
"""

from types import SimpleNamespace

from deerflow.community.aio_sandbox.aio_sandbox import AioSandbox


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
