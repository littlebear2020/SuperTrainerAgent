"""Task 8 (C7 真因): mp3 拼接不得累积时长漂移。

审计的 C7 说 ``-c copy`` 拼接导致「每两个场景衔接处有咔哒爆音」。**爆音已被实跑
证伪**（边界样本跳变仅为中段 1.4 倍，听不出）。但同一行代码藏着更严重的真问题：
**MP3 padding 累积漂移**。

实测量化（本机 ffmpeg，50 个 1.000s mp3 拼接）：
    -c copy          → 52.200s（漂 +2.200s，约 44ms/帧）+ 49 条 DTS 非单调警告
    重编码 libmp3lame → 50.000s（漂 0.000s）✅

**这是「字幕和语音不同步」（C8）的独立真因之一**：字幕时间轴按各帧**标称**时长
累加，而实际音频每帧多出约 44ms → 视频越长，后半段字幕与语音偏离越大。50 帧就
偏 2.2 秒。

每个 mp3 帧固定 1152 samples，文件时长几乎不可能是帧长整数倍 → 每个文件尾部有
padding；``-c copy`` 保留所有 padding 并顺序堆叠，重编码则重新按样本对齐。
"""

import subprocess
from pathlib import Path

import pytest

from app.gateway.video_gen import service as svc


def _has_ffmpeg() -> bool:
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
        return True
    except (OSError, subprocess.CalledProcessError):
        return False


pytestmark = pytest.mark.skipif(not _has_ffmpeg(), reason="需要 ffmpeg")


def _make_mp3(path: Path, seconds: float = 1.0):
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", "-acodec", "libmp3lame", "-q:a", "9", str(path)],
        capture_output=True,
        check=True,
    )
    return str(path)


def test_fifty_frame_concat_has_no_cumulative_drift(tmp_path):
    """50 个 1.000s 片拼接 → 总时长 50.000s ± 0.05s（当前代码此用例必红，实测 52.2s）。"""
    parts = [_make_mp3(tmp_path / f"p{i}.mp3") for i in range(50)]
    out = str(tmp_path / "joined.mp3")

    svc._concat_audio(parts, out)

    total = svc._get_audio_duration(out)
    assert total == pytest.approx(50.0, abs=0.05), f"50 帧拼接后总时长 {total:.3f}s，漂移 {total - 50.0:+.3f}s；mp3 padding 累积会让后半段字幕与语音越走越偏（C8 真因）"


def test_short_concat_stays_exact(tmp_path):
    """2 个 1.000s 片拼接 → 2.000s（修前实测 2.045s）。"""
    parts = [_make_mp3(tmp_path / f"s{i}.mp3") for i in range(2)]
    out = str(tmp_path / "joined2.mp3")

    svc._concat_audio(parts, out)

    total = svc._get_audio_duration(out)
    assert total == pytest.approx(2.0, abs=0.03), f"2 帧拼接 {total:.3f}s"


def test_concat_emits_no_dts_warnings(tmp_path):
    """拼接不得产生 DTS 非单调警告（修前 50 帧产 49 条）。"""
    parts = [_make_mp3(tmp_path / f"d{i}.mp3") for i in range(10)]
    out = tmp_path / "joined10.mp3"

    svc._concat_audio(parts, str(out))

    # 重新解码产物，确认时间戳连续（有 padding 堆叠时 ffmpeg 会抱怨）
    proc = subprocess.run(
        ["ffmpeg", "-v", "warning", "-i", str(out), "-f", "null", "/dev/null"],
        capture_output=True,
        text=True,
    )
    assert "monoton" not in proc.stderr.lower(), f"产物时间戳异常：{proc.stderr[-300:]}"


def test_concat_preserves_audio_content(tmp_path):
    """重编码不得把音频弄没（响度仍在合理范围，不是静音）。"""
    import re

    parts = [_make_mp3(tmp_path / f"c{i}.mp3") for i in range(3)]
    out = tmp_path / "joined3.mp3"

    svc._concat_audio(parts, str(out))

    proc = subprocess.run(
        ["ffmpeg", "-i", str(out), "-af", "volumedetect", "-f", "null", "/dev/null"],
        capture_output=True,
        text=True,
    )
    m = re.search(r"mean_volume:\s*(-?[\d.]+) dB", proc.stderr)
    assert m, "volumedetect 无输出"
    mean = float(m.group(1))
    assert mean > -50, f"拼接产物近乎静音（{mean} dB），重编码可能出错"
