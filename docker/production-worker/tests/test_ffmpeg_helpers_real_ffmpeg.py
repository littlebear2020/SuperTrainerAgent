"""B5: 真实 ffmpeg 集成 smoke——关掉 mock-only 覆盖盲区。

worker finalizer 全 mock subprocess.run/inspect.getsource 是上轮漏掉 Task14 bug 的根因
（mock 掩盖真实行为）。本测试不 mock——真跑 ffmpeg 验证合并 ``eq=...,subtitles=...``
filtergraph + ``-af afade`` fade。CI/本地无 ffmpeg 时 skip（不 fail）。
"""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
pytestmark = pytest.mark.skipif(
    not FFMPEG or not FFPROBE,
    reason="ffmpeg/ffprobe not installed — real-ffmpeg smoke skipped",
)


def _make_tiny_mp4(path: Path, seconds: float = 2.0) -> None:
    """lavfi 生成 2s 带音轨 tiny mp4（testsrc + sine → libx264/aac）。"""
    subprocess.run(
        [
            FFMPEG, "-y",
            "-f", "lavfi", "-i", f"testsrc=duration={seconds}:size=320x240:rate=15",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(path),
        ],
        capture_output=True, timeout=60, check=True,
    )


def _make_srt(path: Path) -> None:
    path.write_text("1\n00:00:00,000 --> 00:00:02,000\nhello subtitle smoke\n", encoding="utf-8")


def _probe_duration(path: Path) -> float:
    out = subprocess.run(
        [FFPROBE, "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, timeout=30, check=True,
    )
    return float(out.stdout.decode().strip())


def _has_audio(path: Path) -> bool:
    out = subprocess.run(
        [FFPROBE, "-v", "error", "-select_streams", "a",
         "-show_entries", "stream=codec_type", "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, timeout=30, check=True,
    )
    return "audio" in out.stdout.decode()


def test_real_ffmpeg_colorgrade_subtitles_and_fade(tmp_path):
    """B5: 真实 ffmpeg 跑合并 colorgrade+subtitles filtergraph + intro/outro fade。

    断言输出存在、非 0 字节、duration≈2s、有音轨。这关掉 mock 掩盖——若合并
    filtergraph 或 afade 在真实 ffmpeg 下坏，本测会红（mock 测不出）。
    """
    from pipelines._ffmpeg_helpers import _apply_colorgrade_and_subtitles, _apply_intro_outro_fade

    src = tmp_path / "src.mp4"
    _make_tiny_mp4(src, 2.0)
    assert src.exists() and src.stat().st_size > 0, "tiny mp4 须生成成功（ffmpeg 可用）"

    srt = tmp_path / "subs.srt"
    _make_srt(srt)

    graded = _apply_colorgrade_and_subtitles(
        src,
        task_dir=tmp_path,
        tmp_dir=tmp_path,
        color_palette={"primary": "#2C3E50", "saturation": 1.2, "contrast": 1.1, "brightness": 0.0},
        subtitle_path=srt,
        subtitle_style="FontSize=24",
    )
    assert graded.exists() and graded.stat().st_size > 0, "colorgrade+subtitles 须产出非空视频"

    faded = _apply_intro_outro_fade(graded, tmp_dir=tmp_path, quality="standard")
    assert faded.exists() and faded.stat().st_size > 0, "fade 须产出非空视频"
    dur = _probe_duration(faded)
    assert 1.5 <= dur <= 2.5, f"fade 后时长应≈2s，got {dur}"
    assert _has_audio(faded), "fade 后须保留音轨（-af afade 不应丢音轨）"
