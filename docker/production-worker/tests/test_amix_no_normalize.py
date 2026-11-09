"""Task 5 (C2): 旁白不得被 BGM 压掉一半。

``amix`` 默认 ``normalize=1``，把每路输入按 1/N 衰减 —— 加一路 BGM 就把旁白
砍掉约一半（-6 dB），用户反馈「旁白听不清、要手动调音量」。

实测量化（本机 ffmpeg，440Hz 旁白 + 200Hz BGM@volume=0.15）：
    纯旁白              mean_volume -21.5 dB
    amix 默认(normalize=1) mean_volume -27.9 dB   ← 掉 6.4 dB
    amix normalize=0      mean_volume -21.9 dB   ← 仅掉 0.4 dB ✅

BGM 音量本就由既有 ``bgm_volume`` 参数单独控制（默认 0.15），不需要 amix 再
额外衰减旁白。四处 amix 全中：mix_audio / mix_bgm / ducking 路径 / ViMax。
"""

import re
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

HELPERS = Path(__file__).parent.parent / "pipelines" / "_ffmpeg_helpers.py"
VIMAX_OVERLAY = (
    Path(__file__).resolve().parents[3]
    / "backend" / "app" / "gateway" / "video_gen" / "engines" / "vimax" / "audio_overlay.py"
)


def _has_ffmpeg() -> bool:
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
        return True
    except (OSError, subprocess.CalledProcessError):
        return False


def _amix_occurrences(path: Path):
    """返回文件里每一处 amix=... 的完整选项串。"""
    text = path.read_text(encoding="utf-8")
    return [(path.name, m.group(0)) for m in re.finditer(r"amix=[^\"'\]\s;]*", text)]


def test_all_amix_sites_disable_normalize():
    """全部 amix 调用点必须显式 normalize=0，否则旁白被按 1/N 衰减。"""
    sites = _amix_occurrences(HELPERS) + _amix_occurrences(VIMAX_OVERLAY)
    assert sites, "没找到任何 amix 调用点——测试锚点失效"

    offenders = [f"{name}: {expr}" for name, expr in sites if "normalize=0" not in expr]
    assert not offenders, (
        "以下 amix 未禁用 normalize（旁白会被压掉约 6 dB）：\n" + "\n".join(offenders)
    )


@pytest.mark.skipif(not _has_ffmpeg(), reason="需要 ffmpeg")
def test_narration_loudness_preserved_after_bgm_mix(tmp_path):
    """真 ffmpeg：混入 BGM 后旁白响度衰减必须 < 1 dB。"""
    from pipelines._ffmpeg_helpers import _ffmpeg_mix_bgm

    def _mean_volume(path):
        proc = subprocess.run(
            ["ffmpeg", "-i", str(path), "-af", "volumedetect", "-f", "null", "/dev/null"],
            capture_output=True, text=True,
        )
        m = re.search(r"mean_volume:\s*(-?[\d.]+) dB", proc.stderr)
        assert m, f"volumedetect 无输出：{proc.stderr[-300:]}"
        return float(m.group(1))

    # 视频（带 440Hz 旁白音轨）
    video = tmp_path / "v.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc2=s=320x240:r=30",
         "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100",
         "-t", "5", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(video)],
        capture_output=True, check=True,
    )
    bgm = tmp_path / "bgm.mp3"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=200:duration=5",
         "-c:a", "libmp3lame", str(bgm)],
        capture_output=True, check=True,
    )

    before = _mean_volume(video)
    out = tmp_path / "out.mp4"
    _ffmpeg_mix_bgm(video, bgm, out, bgm_volume=0.15)
    after = _mean_volume(out)

    drop = before - after
    assert drop < 1.0, (
        f"混入 BGM 后响度掉了 {drop:.1f} dB（{before:.1f} → {after:.1f}）；"
        "amix normalize=1 会把旁白按 1/N 衰减，实测掉 6.4 dB"
    )
