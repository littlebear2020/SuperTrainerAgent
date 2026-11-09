"""Task 18: concat 优先 stream copy，超时按总时长而非镜数。"""
import inspect
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from pipelines import _ffmpeg_helpers as H  # noqa: E402

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")


def test_concat_tries_stream_copy_first() -> None:
    """段已由 normalize_clip_resolution 归一到同规格（30fps/libx264/yuv420p），
    concat 再套 -preset medium 全片重编码纯属浪费——实测 54 镜超 354s 超时。"""
    src = inspect.getsource(H._ffmpeg_concat)
    assert '"-c", "copy"' in src or "'-c', 'copy'" in src, "concat 应优先 stream copy"


def test_concat_keeps_reencode_fallback() -> None:
    """上传素材归一化失败时会 copy2 原片（规格可能不一致）→ copy 会失败，
    必须保留重编码回落。"""
    src = inspect.getsource(H._ffmpeg_concat)
    assert "libx264" in src or "quality_args" in src, "必须保留重编码回落路径"


def test_timeout_scales_with_total_seconds() -> None:
    """54 镜 8 分钟片 vs 54 镜 1 分钟片，超时不应相同。"""
    short = H._dynamic_concat_timeout(54, False, total_seconds=60)
    long = H._dynamic_concat_timeout(54, False, total_seconds=480)
    assert long > short, "超时必须随总时长增长（修前只看镜数：300+54=354s 恒定）"


def test_timeout_backward_compatible() -> None:
    """不传 total_seconds 时保持旧行为，避免其他调用点被静默改变。"""
    assert H._dynamic_concat_timeout(54, False) == 354


def test_timeout_capped() -> None:
    assert H._dynamic_concat_timeout(500, True, total_seconds=100000) <= 1800


# ---------------------------------------------------------------------------
# BUG-A（2026-08-03）：部分段 probe 失败不得误回落重编码
# ---------------------------------------------------------------------------


def test_partial_probe_failure_does_not_force_reencode(tmp_path, monkeypatch, caplog) -> None:
    """_probe_duration 对探不到的段返回 0.0。若 54 段中 1 段失败，expected 少算该段，
    而 copy 产物 got 含全部段 → |got-expected| 变大 → 误回落重编码 → Task 18 的提速
    静默失效，退回它本要修的慢路径。

    触发条件真实存在：normalize_clip_resolution 归一失败时 shutil.copy2 原片，
    会把不可 probe 的上传素材直接塞进 segments。
    """
    from unittest.mock import MagicMock

    segs = [tmp_path / f"s{i}.mp4" for i in range(3)]
    for s in segs:
        s.write_bytes(b"x" * 64)
    out = tmp_path / "out.mp4"
    cmds: list[list[str]] = []

    def _fake_run(cmd, **kw):  # noqa: ANN001,ANN003
        cmds.append(cmd)
        out.write_bytes(b"y")
        return MagicMock(returncode=0, stderr=b"")

    monkeypatch.setattr(H.subprocess, "run", _fake_run)
    # 第 2 段探测失败（0.0），其余正常；产物时长 = 三段真实之和 6.0
    durations = {str(segs[0]): 2.0, str(segs[1]): 0.0, str(segs[2]): 2.0, str(out): 6.0}
    monkeypatch.setattr(H, "_probe_duration", lambda p: durations.get(str(p), 0.0))

    with caplog.at_level("WARNING"):
        H._ffmpeg_concat(segs, out)

    assert len(cmds) == 1, f"应只执行 copy 一条命令（未回落），实际 {len(cmds)} 条"
    assert "-c copy" in " ".join(cmds[0])
    assert not any("falling back to re-encode" in r.getMessage() for r in caplog.records), "部分段探测失败不得触发回落"


def test_all_probes_ok_still_detects_real_mismatch(tmp_path, monkeypatch) -> None:
    """反向锚：全段都探到时长时，真实的时长不符仍必须回落（别把校验放松成永不触发）。"""
    from unittest.mock import MagicMock

    segs = [tmp_path / f"s{i}.mp4" for i in range(2)]
    for s in segs:
        s.write_bytes(b"x" * 64)
    out = tmp_path / "out.mp4"
    cmds: list[list[str]] = []

    def _fake_run(cmd, **kw):  # noqa: ANN001,ANN003
        cmds.append(cmd)
        out.write_bytes(b"y")
        return MagicMock(returncode=0, stderr=b"")

    monkeypatch.setattr(H.subprocess, "run", _fake_run)
    # 两段各 2.0s（都探到），产物只有 3.2s → 真实压缩，必须回落
    durations = {str(segs[0]): 2.0, str(segs[1]): 2.0, str(out): 3.2}
    monkeypatch.setattr(H, "_probe_duration", lambda p: durations.get(str(p), 0.0))

    H._ffmpeg_concat(segs, out)
    assert len(cmds) == 2, "真实时长不符必须回落重编码"
    assert "libx264" in " ".join(cmds[1])


# ---------------------------------------------------------------------------
# 评审 Minor #1（2026-08-14）：容差非对称 [−0.1, +0.2]——C2 同一 bug 类。
# 短损 = 时长被静默压缩（不可信），必须比长损（下游 -t 截断无损）更严。
# ---------------------------------------------------------------------------


def _concat_with_got(tmp_path, monkeypatch, got: float) -> list[list[str]]:
    """跑一次 _ffmpeg_concat（copy→probe 校验），返回执行的命令列表。"""
    from unittest.mock import MagicMock

    segs = [tmp_path / f"s{i}.mp4" for i in range(2)]
    for s in segs:
        s.write_bytes(b"x" * 64)
    out = tmp_path / "out.mp4"
    cmds: list[list[str]] = []

    def _fake_run(cmd, **kw):  # noqa: ANN001,ANN003
        cmds.append(cmd)
        out.write_bytes(b"y")
        return MagicMock(returncode=0, stderr=b"")

    monkeypatch.setattr(H.subprocess, "run", _fake_run)
    durations = {str(segs[0]): 2.0, str(segs[1]): 2.0, str(out): got}
    monkeypatch.setattr(H, "_probe_duration", lambda p: durations.get(str(p), 0.0))
    H._ffmpeg_concat(segs, out)
    return cmds


def test_copy_short_by_0_15s_falls_back(tmp_path, monkeypatch) -> None:
    """短 0.15s（0.1~0.2 之间）：旧对称 ±0.2 会接受 → 必须回落（C2 同类漏网）。"""
    cmds = _concat_with_got(tmp_path, monkeypatch, got=3.85)
    assert len(cmds) == 2, "短 0.15s 超短侧下限 0.1，必须回落重编码"
    assert "libx264" in " ".join(cmds[1])


def test_copy_short_by_0_05s_accepted(tmp_path, monkeypatch) -> None:
    """短 0.05s 在亚帧取整余量内 → 接受 copy，不误回落（BUG-A 教训）。"""
    cmds = _concat_with_got(tmp_path, monkeypatch, got=3.95)
    assert len(cmds) == 1, "短 0.05s 在下限内应接受 copy"
    assert "copy" in " ".join(cmds[0])


def test_copy_long_by_0_15s_accepted(tmp_path, monkeypatch) -> None:
    """长 0.15s 在上限 0.2 内 → 接受（下游 -t 截断无损）。"""
    cmds = _concat_with_got(tmp_path, monkeypatch, got=4.15)
    assert len(cmds) == 1, "长 0.15s 在上限内应接受 copy"


# ---------------------------------------------------------------------------
# 真实 ffmpeg：stream copy 的正确性边界
#
# 计划假设「规格不一致 → copy 失败 → 回落重编码」。**实测证伪**：混帧率段
# concat -c copy 返回 rc=0，只在 stderr 刷 Non-monotonic DTS，产物时长错误
# （30fps 2s + 24fps 2s → 3.200s，应为 4.0s）。即回落分支靠 returncode 永远
# 不会触发。故必须在 copy 后校验实测时长，偏差过大才回落。
# ---------------------------------------------------------------------------


def _mk(path: Path, *, fps: int, seconds: float = 2.0, size: str = "640x360") -> Path:
    subprocess.run(
        [FFMPEG, "-y", "-f", "lavfi", "-i", f"testsrc=s={size}:d={seconds}:r={fps}",
         "-c:v", "libx264", "-crf", "20", "-pix_fmt", "yuv420p", "-an", str(path)],
        capture_output=True,
    )
    return path


def _dur(path: Path) -> float:
    out = subprocess.run(
        [FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    ).stdout.strip()
    return float(out or 0.0)


@pytest.mark.skipif(not FFMPEG or not FFPROBE, reason="ffmpeg/ffprobe not installed")
def test_real_concat_same_specs_uses_copy_and_keeps_duration(tmp_path) -> None:
    """同规格段（生产常态，G1 已归一）→ copy 成功且时长精确。"""
    segs = [_mk(tmp_path / f"s{i}.mp4", fps=30) for i in range(3)]
    out = tmp_path / "out.mp4"
    H._ffmpeg_concat(segs, out)
    assert abs(_dur(out) - 6.0) < 0.1, f"3×2s 应得 6.0s，实际 {_dur(out)}"


@pytest.mark.skipif(not FFMPEG or not FFPROBE, reason="ffmpeg/ffprobe not installed")
def test_real_concat_mismatched_fps_falls_back_to_reencode(tmp_path, caplog) -> None:
    """混帧率段（上传素材归一化失败时的现实情形）→ copy 判为不可信并回落重编码。

    copy 在此情形下 **rc=0** 但产物时长 3.200s（段和 4.0s），时间轴被静默压缩。
    只看 returncode 的回落分支永不触发，故实现须自校验时长。

    注意：**回落后的重编码路径同样只得 3.267s**——混帧率丢时长是本函数的**既有**
    缺陷（改前的无条件重编码路径实测同为 3.267s），不由本 task 引入、也不在其范围内。
    生产上该情形被 G1 ``normalize_clip_resolution``（逐段 re-encode 到 30fps）挡住，
    只有上传素材归一化失败 ``copy2`` 原片时才可能出现。本测试因此断言本 task 真正
    负责的性质：**不信任错误的 copy 产物**，且不比旧路径更差。
    """
    a = _mk(tmp_path / "a30.mp4", fps=30)
    b = _mk(tmp_path / "b24.mp4", fps=24)
    out = tmp_path / "out.mp4"
    with caplog.at_level("WARNING"):
        H._ffmpeg_concat([a, b], out)
    assert any("falling back to re-encode" in r.getMessage() for r in caplog.records), \
        "混规格段时 copy 必须被判为不可信并回落，实际未回落"

    # 不比旧路径更差：与改前的无条件重编码命令逐字对齐后比时长。
    legacy = tmp_path / "legacy.mp4"
    filelist = tmp_path / "fl.txt"
    filelist.write_text(f"file '{a}'\nfile '{b}'\n")
    subprocess.run(
        [FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", str(filelist),
         "-r", "30", *H._get_quality_args("standard"), "-c:a", "aac", str(legacy)],
        capture_output=True,
    )
    assert _dur(out) >= _dur(legacy) - 0.05, f"回落产物 {_dur(out)}s 短于旧路径 {_dur(legacy)}s"
