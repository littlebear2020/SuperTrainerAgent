"""BGM 混音不得用 -shortest 截断比视频流长的旁白音轨。

根因：mix_audio 去 tpad 后（容器末帧定格），音频更长的文件到达 BGM 阶段时
视频流 < 音频流；mix_bgm/mix_bgm_with_ducking 的 -shortest 把输出砍回视频流长
→ 旁白尾巴被切（B1 经 BGM 路径回潮）。真实 ffmpeg 集成测试（无 ffmpeg 时 skip）。
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
skip_no_ffmpeg = pytest.mark.skipif(
    not (FFMPEG and FFPROBE),
    reason="ffmpeg/ffprobe not installed — BGM smoke skipped",
)


def _make_video_with_audio(path, v_seconds, a_seconds):
    """生成 视频流≠音频流时长 的 mp4（不加 -shortest，双流各自完整）。"""
    subprocess.run(
        [FFMPEG, "-y",
         "-f", "lavfi", "-i", f"testsrc=duration={v_seconds}:size=320x240:rate=15",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={a_seconds}",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)],
        check=True, capture_output=True, timeout=60,
    )


def _make_audio(path, seconds=10.0):
    subprocess.run(
        [FFMPEG, "-y", "-f", "lavfi", "-i", f"sine=frequency=220:duration={seconds}",
         "-acodec", "libmp3lame", "-q:a", "9", str(path)],
        check=True, capture_output=True, timeout=60,
    )


def _probe_duration(path):
    out = subprocess.run(
        [FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, timeout=30,
    )
    return float(out.stdout.strip())


@skip_no_ffmpeg
def test_mix_bgm_preserves_narration_tail(tmp_path):
    """v5s/a8s + 10s BGM → 输出 ≥7.7s（修前 -shortest → ~5s，旁白尾巴被切，FAIL）。"""
    from pipelines._ffmpeg_helpers import _ffmpeg_mix_bgm

    video = tmp_path / "v.mp4"
    bgm = tmp_path / "bgm.mp3"
    out = tmp_path / "out.mp4"
    _make_video_with_audio(video, v_seconds=5.0, a_seconds=8.0)
    _make_audio(bgm, seconds=10.0)

    _ffmpeg_mix_bgm(video, bgm, out)

    dur = _probe_duration(out)
    assert dur >= 8.0 - 0.3, f"BGM 混音不得截旁白尾巴：期望 ≥7.7s，实际 {dur:.2f}s"


@skip_no_ffmpeg
def test_mix_bgm_video_longer_keeps_shortest(tmp_path):
    """v8s/a5s + BGM → -shortest 保留：输出 ≈5s（切视频尾，旧行为不变，特征化钉死）。"""
    from pipelines._ffmpeg_helpers import _ffmpeg_mix_bgm

    video = tmp_path / "v.mp4"
    bgm = tmp_path / "bgm.mp3"
    out = tmp_path / "out.mp4"
    _make_video_with_audio(video, v_seconds=8.0, a_seconds=5.0)
    _make_audio(bgm, seconds=10.0)

    _ffmpeg_mix_bgm(video, bgm, out)

    dur = _probe_duration(out)
    assert dur <= 5.5, f"视频更长时仍应切到音频长：期望 ≤5.5s，实际 {dur:.2f}s"


@skip_no_ffmpeg
def test_mix_bgm_with_ducking_preserves_narration_tail(tmp_path):
    """ducking 变体同样不切尾巴（sidechaincompress 失败回退简单混音也应 ≥7.7s）。"""
    from pipelines._ffmpeg_helpers import _ffmpeg_mix_bgm_with_ducking

    video = tmp_path / "v.mp4"
    bgm = tmp_path / "bgm.mp3"
    out = tmp_path / "out.mp4"
    _make_video_with_audio(video, v_seconds=5.0, a_seconds=8.0)
    _make_audio(bgm, seconds=10.0)

    _ffmpeg_mix_bgm_with_ducking(video, bgm, out)

    dur = _probe_duration(out)
    assert dur >= 8.0 - 0.3, f"ducking 混音不得截旁白尾巴：期望 ≥7.7s，实际 {dur:.2f}s"
