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


def test_bash_env_output_none_fields_returns_empty():
    s = _sandbox_with_bash_result(SimpleNamespace(stdout=None, stderr=None, exit_code=1))
    assert s.execute_command("x", env={"K": "V"}) == ""
