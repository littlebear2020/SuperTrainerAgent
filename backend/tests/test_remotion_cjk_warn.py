# backend/tests/test_remotion_cjk_warn.py
"""A2: CJK 字体检查改沙箱内 fc-list 真探测（旧扫模板目录=永久误报）。"""


def test_warn_no_cjk_when_fc_list_empty(tmp_path):
    """A2: fc-list 返空（真无 CJK）→ 写 sidecar；返非空→不写（修前恒写=永久误报）。"""
    from app.gateway.video_gen.pipelines import remotion_template as rt

    class FakeSandbox:
        def execute_command(self, cmd, *a, **k):
            class R:
                returncode = 0
                stdout = ""  # fc-list 跑通但无 CJK 字体

            return R

    rt._warn_if_no_cjk_font(tmp_path, sandbox=FakeSandbox())
    assert (tmp_path / "remotion_cjk_warning.json").exists()


def test_warn_silent_when_cjk_present(tmp_path):
    from app.gateway.video_gen.pipelines import remotion_template as rt

    class FakeSandbox:
        def execute_command(self, cmd, *a, **k):
            class R:
                returncode = 0
                stdout = "NotoSansCJK-Regular.ttc: Noto Sans CJK\n"

            return R

    rt._warn_if_no_cjk_font(tmp_path, sandbox=FakeSandbox())
    assert not (tmp_path / "remotion_cjk_warning.json").exists()  # 有 CJK→不误报


def test_warn_no_false_alarm_when_fc_list_unavailable(tmp_path):
    from app.gateway.video_gen.pipelines import remotion_template as rt

    class FakeSandbox:
        def execute_command(self, cmd, *a, **k):
            class R:
                returncode = 127  # fc-list 不存在
                stdout = ""

            return R

    rt._warn_if_no_cjk_font(tmp_path, sandbox=FakeSandbox())
    assert not (tmp_path / "remotion_cjk_warning.json").exists()  # 不确定→不误报


def test_warn_assumes_present_when_no_sandbox(tmp_path):
    """无 sandbox 上下文（local 无沙箱）→ 不误报（assume present）。"""
    from app.gateway.video_gen.pipelines import remotion_template as rt

    rt._warn_if_no_cjk_font(tmp_path, sandbox=None)
    assert not (tmp_path / "remotion_cjk_warning.json").exists()
