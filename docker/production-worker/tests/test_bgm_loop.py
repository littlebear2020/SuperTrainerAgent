"""BGM 循环：短音乐须 aloop 补到视频时长，禁止 apad 静音填充（10s 后音乐消失回归）。"""
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent))

from pipelines import _ffmpeg_helpers as h  # noqa: E402


def _run(func, *args, **kwargs):
    calls = []

    def fake_run(cmd, *a, **kw):
        # 只捕获 ffmpeg 调用——mix_bgm 新增 _shortest_args_for 的 ffprobe 流探测
        # 也走同一 subprocess.run mock，不过滤会让 calls[0] 从 ffmpeg 变成 ffprobe。
        if cmd[0] == "ffmpeg":
            calls.append(cmd)
        r = mock.Mock()
        r.returncode = 0
        r.stderr = b""
        return r

    with mock.patch("pipelines._ffmpeg_helpers._ffprobe_duration", return_value=30.0), \
         mock.patch("pipelines._ffmpeg_helpers.subprocess.run", side_effect=fake_run), \
         mock.patch("pathlib.Path.exists", return_value=True):
        func(*args, **kwargs)
    return calls


def test_simple_mix_loops_bgm():
    """30s 视频 + 10s BGM：filter 须先 aloop 再 atrim（顺序反了等于没循环）。"""
    calls = _run(h._ffmpeg_mix_bgm, Path("/v.mp4"), Path("/b.mp3"), Path("/o.mp4"))
    fc = calls[0][calls[0].index("-filter_complex") + 1]
    assert "aloop=loop=-1" in fc, f"缺 aloop 循环: {fc}"
    assert fc.index("aloop") < fc.index("atrim"), f"aloop 必须在 atrim 之前: {fc}"


def test_ducking_mix_loops_bgm_no_apad():
    """ducking 版：apad（静音填充）换成 aloop；30s 处截断保留。"""
    calls = _run(h._ffmpeg_mix_bgm_with_ducking, Path("/v.mp4"), Path("/b.mp3"), Path("/o.mp4"))
    fc = calls[0][calls[0].index("-filter_complex") + 1]
    assert "aloop=loop=-1" in fc, f"缺 aloop 循环: {fc}"
    assert "apad" not in fc, f"apad 是静音填充（音乐 10s 后消失根因）: {fc}"
    assert "sidechaincompress" in fc  # ducking 能力不回归


def test_ducking_fallback_also_loops():
    """ducking ffmpeg 失败 → fallback simple mix 也走循环版（returncode!=0 触发）。"""
    def fake_fail(cmd, *a, **kw):
        r = mock.Mock()
        r.returncode = 1 if "sidechaincompress" in " ".join(cmd) else 0
        r.stderr = b"boom"
        return r

    calls = []
    def capturing_run(cmd, *a, **kw):
        # 同上：只捕获 ffmpeg（滤掉 _shortest_args_for 的 ffprobe 流探测）。
        if cmd[0] == "ffmpeg":
            calls.append(cmd)
        return fake_fail(cmd, *a, **kw)

    with mock.patch("pipelines._ffmpeg_helpers._ffprobe_duration", return_value=30.0), \
         mock.patch("pipelines._ffmpeg_helpers.subprocess.run", side_effect=capturing_run), \
         mock.patch("pathlib.Path.exists", return_value=True):
        h._ffmpeg_mix_bgm_with_ducking(Path("/v.mp4"), Path("/b.mp3"), Path("/o.mp4"))
    # 第二次调用是 fallback simple mix
    assert len(calls) == 2
    fc = calls[1][calls[1].index("-filter_complex") + 1]
    assert "aloop=loop=-1" in fc
