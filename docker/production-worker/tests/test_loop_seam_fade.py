"""H1: ai_video 长帧补齐——禁止涂黑画面的接缝 fade（黑屏主因回归测试）。

Phase 1 v2：补长机制改为分层降级链（Tier 1 setpts 慢放 / Tier 2 Ken Burns /
Tier 3 tpad 定格兜底）。H1 的核心约束——**不得引入任何 fade**——不变，本文件
继续锁死它。Tier 2/3 行为的正向断言见 ``test_fill_shortfall_tiered.py``。
"""
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent))

from pipelines._ffmpeg_helpers import _ffmpeg_extend_clip_to_duration  # noqa: E402


def _run_extend(actual, target):
    calls = []

    def fake_run(cmd, *a, **kw):
        calls.append(cmd)
        r = mock.Mock()
        r.returncode = 0
        r.stderr = b""
        return r

    with mock.patch("pipelines._ffmpeg_helpers._probe_duration", return_value=actual), \
         mock.patch("pipelines._ffmpeg_helpers.subprocess.run", side_effect=fake_run), \
         mock.patch("pathlib.Path.exists", return_value=True):
        _ffmpeg_extend_clip_to_duration(Path("/in.mp4"), target, Path("/out.mp4"))
    return calls


def test_pad_extend_has_no_blackening_fade():
    """H1: 8s clip 补到 18s——所有 ffmpeg 命令均不得含 fade。

    Phase 1 v2 更新：分层链下 Tier 2 Ken Burns 先试（帧提取 + zoompan + concat），
    全失败才回落 Tier 3 tpad。所有命令都不得引入 fade——``fade=t=in:st=X`` 的
    ffmpeg 语义会把 st 之前整段渲染为纯黑，曾是黑屏主因。
    """
    calls = _run_extend(8.0, 18.0)
    # 所有 ffmpeg 命令均不得含 fade 滤镜
    for cmd in calls:
        if isinstance(cmd, list) and cmd and cmd[0] == "ffmpeg":
            cmd_str = " ".join(str(c) for c in cmd)
            assert "fade=" not in cmd_str, f"接缝 fade 会涂黑 st 之前全部画面: {cmd_str[:100]}"
            assert "-stream_loop" not in cmd_str, "不得循环重播内容"


def test_trim_branch_unchanged():
    """H1: clip ≥ target 时 trim 分支不变（-t + -c copy，无循环）。"""
    calls = _run_extend(10.0, 8.0)
    cmd_str = " ".join(str(c) for c in calls[0])
    assert "-stream_loop" not in cmd_str
    assert "-c copy" in cmd_str
