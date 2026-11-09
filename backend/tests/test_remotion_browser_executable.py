"""remotion_run._browser_executable_flag returns --browser-executable only when Chrome exists."""

from unittest.mock import MagicMock

from app.gateway.video_gen.pipelines.remotion_run import _browser_executable_flag


class _FakeSandbox:
    def __init__(self, output: str) -> None:
        self._output = output
        self.last_cmd: str | None = None

    def execute_command(self, cmd: str) -> str:
        self.last_cmd = cmd
        return self._output


def test_flag_present_when_chrome_exists() -> None:
    sb = _FakeSandbox("OK")
    flag = _browser_executable_flag(sb)
    assert flag.startswith("--browser-executable=")
    assert flag.endswith("chrome-headless-shell")
    assert "test -x" in sb.last_cmd and "chrome-headless-shell" in sb.last_cmd


def test_flag_absent_when_chrome_missing() -> None:
    sb = _FakeSandbox("MISSING")
    assert _browser_executable_flag(sb) == ""


def test_flag_absent_on_exception() -> None:
    sb = MagicMock()
    sb.execute_command.side_effect = RuntimeError("boom")
    assert _browser_executable_flag(sb) == ""


def test_legacy_render_command_includes_browser_flag() -> None:
    """run_remotion builds a render command containing --browser-executable when Chrome exists."""
    import inspect

    from app.gateway.video_gen.pipelines import remotion_run

    src = inspect.getsource(remotion_run.run_remotion)
    assert "_browser_executable_flag" in src, "run_remotion must call _browser_executable_flag"


def test_storyboard_render_command_includes_browser_flag() -> None:
    import inspect

    from app.gateway.video_gen.pipelines import remotion_run

    src = inspect.getsource(remotion_run.run_remotion_storyboard)
    assert "_browser_executable_flag" in src, "run_remotion_storyboard must call _browser_executable_flag"
