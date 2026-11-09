"""Task 4 (B4 + B3 + N9): 短素材不再循环播放 / 不再音画错位。

三条审计条目是**同一个根因**——「segment 比目标时长短」缺统一处理：

| 审计 ID | 表现 |
|---|---|
| B4 | 短片被 ``-stream_loop`` **循环播放**（1.2s 片在 6s 槽里放 5 遍，跳帧呆滞） |
| B3 | **用户上传**被显式排除在补长之外 → 短了就错位，且误差累积 |
| N9 | probe 失败时 ``src_dur = duration`` → 补长判据恒 False，最该补的坏片恰好不补 |

修法：统一用 ``tpad=stop_mode=clone`` **定格末帧**而非 ``-stream_loop`` 循环。

实测量化（本机 ffmpeg，1.2s 源补到 6s）：
    stream_loop : 时长 6.067s（溢出）  尾部两帧差异 9.30（内容重播，跳变）
    tpad clone  : 时长 6.000s（精确）  尾部两帧差异 0.00（定格，静止）

推翻旧权衡：``_ffmpeg_extend_clip_to_duration`` 的 docstring 曾把 tpad 列为
「需重编码且改变段内音频时长语义，列后续评估」。其中**音频顾虑在当前调用序下
已不成立**——该函数的产物随后一律经 ``normalize_clip_resolution``，后者带
``-an`` 剥掉段音轨（H5）。本文件用 test_extend_output_has_no_audio_stream 锁死
这个前提：一旦有人去掉 -an，该测试立刻红。

保留 H1 教训：新实现不得引入任何 fade（``fade=t=in:st=X`` 会把 st 之前整段渲成
纯黑，曾是黑屏主因，由真实任务 vg_e766f5d640f7 blackdetect 坐实）。
"""

import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent.parent))

from pipelines._ffmpeg_helpers import (  # noqa: E402
    _ffmpeg_extend_clip_to_duration,
    _probe_duration,
    normalize_clip_resolution,
)


def _has_ffmpeg() -> bool:
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
        return True
    except (OSError, subprocess.CalledProcessError):
        return False


pytestmark = pytest.mark.skipif(not _has_ffmpeg(), reason="需要 ffmpeg")


def _make_clip(path, duration, *, size="320x240", with_audio=False):
    cmd = ["ffmpeg", "-y", "-f", "lavfi", "-i", f"testsrc2=s={size}:r=30"]
    if with_audio:
        cmd += ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100"]
    cmd += ["-t", str(duration), "-c:v", "libx264", "-pix_fmt", "yuv420p"]
    if with_audio:
        cmd += ["-c:a", "aac", "-shortest"]
    cmd.append(str(path))
    subprocess.run(cmd, capture_output=True, check=True)
    return path


def _frame_at(video, t, tmp_path):
    out = tmp_path / f"f_{abs(hash((str(video), t))) % 99999}.png"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-ss", str(t), "-i", str(video),
         "-frames:v", "1", "-update", "1", str(out)],
        capture_output=True, check=True,
    )
    return np.asarray(Image.open(out).convert("L"), dtype=float)


def test_short_clip_extends_without_content_replay(tmp_path):
    """1.2s 片 → 6s 槽：输出 6s 且末段为 Ken Burns 缓推（不重播内容）。

    Phase 1 v2 更新：旧 tpad 定格已被分层降级链替换。
    gap_ratio=80% → Tier 2 Ken Burns（末段缓推，有微运动但非内容重播）。
    保留原始测试意图：禁止 stream_loop 式内容重播（实测 diff=9.30）。
    """
    src = _make_clip(tmp_path / "short.mp4", 1.2)
    out = tmp_path / "ext.mp4"

    _ffmpeg_extend_clip_to_duration(src, 6.0, out)

    actual = _probe_duration(out)
    assert actual == pytest.approx(6.0, abs=0.1), f"补长后时长 {actual}s != 6.0s"

    # 原片只有 1.2s，3.0s 与 5.0s 都落在补出来的尾段
    a = _frame_at(out, 3.0, tmp_path)
    b = _frame_at(out, 5.0, tmp_path)
    diff = float(np.abs(a - b).mean())
    # Ken Burns 缓推有微运动但远小于 stream_loop 内容重播（实测 9.30）
    assert diff < 8.0, (
        f"尾段两帧差异 {diff:.2f} —— 画面在重播内容而非缓推"
        "（stream_loop 实测 9.30，Ken Burns 缓推应远低于此）"
    )


def test_extend_introduces_no_fade(tmp_path):
    """H1 回归：补长不得引入 fade（会把 st 之前整段涂黑，曾是黑屏主因）。"""
    src = _make_clip(tmp_path / "s.mp4", 1.0)
    out = tmp_path / "ext.mp4"
    _ffmpeg_extend_clip_to_duration(src, 4.0, out)

    # 原片首帧不得被涂黑：与源首帧比对应基本一致
    src_first = _frame_at(src, 0.1, tmp_path)
    out_first = _frame_at(out, 0.1, tmp_path)
    assert float(np.abs(src_first - out_first).mean()) < 12.0, "首帧被涂黑，疑似引入 fade"


def test_long_clip_still_trimmed(tmp_path):
    """clip ≥ target 时仍是截断（不补长）。"""
    src = _make_clip(tmp_path / "long.mp4", 5.0)
    out = tmp_path / "trim.mp4"
    _ffmpeg_extend_clip_to_duration(src, 2.0, out)
    assert _probe_duration(out) == pytest.approx(2.0, abs=0.1)


def test_extend_output_has_no_audio_stream(tmp_path):
    """锁死 tpad 的前提：段音轨随后由 normalize_clip_resolution 的 -an 剥掉。

    一旦有人去掉 -an，本测试转红 —— 提醒「段内音频时长语义」顾虑重新成立。
    """
    src = _make_clip(tmp_path / "with_audio.mp4", 1.0, with_audio=True)
    normalized = tmp_path / "norm.mp4"
    normalize_clip_resolution(src, normalized, 320, 240)

    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a",
         "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(normalized)],
        capture_output=True, text=True, check=True,
    )
    assert proc.stdout.strip() == "", (
        "normalize_clip_resolution 不再剥音轨 —— tpad 定格会改变段内音频时长语义"
    )


def test_probe_failure_pads_conservatively(tmp_path, monkeypatch):
    """N9: probe 失败时必须保守补长，不得假设「时长等于目标」而跳过补长。"""
    from pipelines import video_gen_mixed as vm

    calls = []
    monkeypatch.setattr(vm, "_ffprobe_duration", lambda *a, **k: (_ for _ in ()).throw(ValueError("probe failed")))
    monkeypatch.setattr(
        vm, "_ffmpeg_extend_clip_to_duration",
        lambda src, dur, out, **kw: (calls.append((src, dur)), out.write_bytes(b"x"), out)[2],
    )

    src = _make_clip(tmp_path / "s.mp4", 1.0)
    out = tmp_path / "seg.mp4"
    try:
        vm._prepare_segment_from_file(src, out, 6.0, 320, 240)
    except RuntimeError:
        pass  # 真 ffmpeg 段编码可能因 stub 产物无效而失败，本测试只关心是否尝试补长

    assert calls, "probe 失败时没有尝试补长 —— 最该补的坏片恰好不补（N9）"
