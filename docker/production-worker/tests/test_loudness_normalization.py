"""Task 8 (C3): 全片响度统一。

核实确认全仓**零** ``loudnorm``（grep 无命中）。不同任务、不同 TTS 供应商产出
音量忽大忽小，用户每看一个视频都要手动调音量 —— 非常「半成品」。

修法：终混加 ``loudnorm=I=-16:TP=-1.5:LRA=11``（流媒体常用目标）。

**位置很关键**：必须在 ``amix`` **之后**。Task 5 给四处 amix 加了
``normalize=0`` 以免旁白被按 1/N 衰减；若 loudnorm 放在 amix 之前逐路做，两者
会相互抵消。放在混音完成后对**成品整轨**做一次，两个修复正交。
"""

import re
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from pipelines._ffmpeg_helpers import _normalize_loudness  # noqa: E402


def _has_ffmpeg() -> bool:
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
        return True
    except (OSError, subprocess.CalledProcessError):
        return False


pytestmark = pytest.mark.skipif(not _has_ffmpeg(), reason="需要 ffmpeg")


def _make_video(path: Path, *, volume: float, seconds: float = 4.0):
    """造一个带指定音量正弦音轨的视频。"""
    subprocess.run(
        ["ffmpeg", "-y",
         "-f", "lavfi", "-i", f"testsrc2=s=320x240:r=30",
         "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=44100",
         "-t", str(seconds),
         "-filter:a", f"volume={volume}",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)],
        capture_output=True, check=True,
    )
    return path


def _integrated_loudness(path: Path) -> float:
    """用 loudnorm 的分析模式读出 integrated loudness (LUFS)。"""
    proc = subprocess.run(
        ["ffmpeg", "-i", str(path), "-af", "loudnorm=print_format=summary", "-f", "null", "/dev/null"],
        capture_output=True, text=True,
    )
    m = re.search(r"Input Integrated:\s*(-?[\d.]+) LUFS", proc.stderr)
    assert m, f"读不出 integrated loudness：{proc.stderr[-400:]}"
    return float(m.group(1))


@pytest.mark.parametrize("volume", [0.2, 1.0])
def test_loudness_converges_to_target(tmp_path, volume):
    """两条不同初始响度输入 → 输出 integrated loudness 落在 -16 ±1.5 LUFS。"""
    src = _make_video(tmp_path / f"src_{volume}.mp4", volume=volume)
    out = tmp_path / f"out_{volume}.mp4"

    result = _normalize_loudness(src, out)
    assert Path(result).exists()

    after = _integrated_loudness(Path(result))
    assert after == pytest.approx(-16.0, abs=1.5), (
        f"初始 volume={volume} 的输出响度 {after:.1f} LUFS 未收敛到 -16±1.5"
    )


def test_loudness_normalization_preserves_video_stream(tmp_path):
    """响度归一不得丢画面。"""
    src = _make_video(tmp_path / "src.mp4", volume=0.5)
    out = tmp_path / "out.mp4"

    result = _normalize_loudness(src, out)

    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v",
         "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(result)],
        capture_output=True, text=True, check=True,
    )
    assert "video" in proc.stdout, "响度归一后视频轨丢失"


def test_loudness_normalization_falls_back_on_failure(tmp_path):
    """输入无音轨/损坏时回落原文件，不阻断出片。"""
    src = tmp_path / "novideo.mp4"
    src.write_bytes(b"not a video")
    out = tmp_path / "out.mp4"

    result = _normalize_loudness(src, out)
    assert Path(result) == src, "失败时应回落原文件（出片优先）"


def test_loudnorm_is_wired_into_pipelines():
    """三条管线的终混必须真的调 _normalize_loudness（否则函数只是死代码）。

    位置约束：必须在 BGM 混音之后（amix 全部完成），且在 fade / 格式转换之前。
    """
    import re

    pipelines_dir = Path(__file__).parent.parent / "pipelines"
    for name in ("video_gen_ai_visual.py", "video_gen_mixed.py", "video_gen_segmented.py"):
        text = (pipelines_dir / name).read_text(encoding="utf-8")
        assert "_normalize_loudness" in text, f"{name} 未接通响度归一（C3 全片响度不统一）"

        # 顺序检查：loudnorm 调用点必须出现在 BGM 混音之后
        norm_at = text.index("_normalize_loudness(")
        bgm_matches = [m.start() for m in re.finditer(r"_ffmpeg_mix_bgm\w*\(", text)]
        if bgm_matches:
            assert norm_at > max(bgm_matches), (
                f"{name}: 响度归一在 BGM 混音之前 —— 会与 amix normalize=0 相互抵消"
            )
