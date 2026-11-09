"""B1: _ffmpeg_mix_audio 不得用 -shortest 截断比视频长的旁白音频。

根因：xfade 转场让视频总时长 < 音频（Σdur−Σtd），-shortest 把较长的旁白音频裁短
→ 尾巴被切（内容没讲完）。修法：视频短于音频时用 tpad freeze-frame 补齐到音频长度。
真实 ffmpeg 集成测试（无 ffmpeg 时 skip）。
"""

import shutil
import subprocess

import pytest

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
skip_no_ffmpeg = pytest.mark.skipif(
    not (FFMPEG and FFPROBE),
    reason="ffmpeg/ffprobe not installed — B1 smoke skipped",
)


def _make_video_no_audio(path, seconds=5.0):
    subprocess.run(
        [FFMPEG, "-y", "-f", "lavfi", "-i", f"color=c=blue:s=320x240:r=15:d={seconds}",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", str(path)],
        check=True, capture_output=True, timeout=60,
    )


def _make_audio(path, seconds=8.0):
    subprocess.run(
        [FFMPEG, "-y", "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
         "-acodec", "libmp3lame", "-q:a", "9", str(path)],
        check=True, capture_output=True, timeout=60,
    )


def _probe_duration(path):
    out = subprocess.run(
        [FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, timeout=30,
    )
    return float(out.stdout.strip())


def _probe_stream_duration(path, selector):
    """probe 单条流时长（selector 如 'v:0'）；无该流 → None。"""
    out = subprocess.run(
        [FFPROBE, "-v", "error", "-select_streams", selector,
         "-show_entries", "stream=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, timeout=30,
    )
    s = out.stdout.strip()
    return float(s) if s else None


def _make_video_with_audio(path, v_seconds=5.0, a_seconds=5.0):
    """生成 视频流≠音频流时长 的 mp4（不加 -shortest，双流各自完整）。"""
    subprocess.run(
        [FFMPEG, "-y",
         "-f", "lavfi", "-i", f"testsrc=duration={v_seconds}:size=320x240:rate=15",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={a_seconds}",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)],
        check=True, capture_output=True, timeout=60,
    )


@skip_no_ffmpeg
def test_mix_audio_does_not_truncate_long_audio(tmp_path):
    """视频 5s + 音频 8s → 混音输出 ≥8s（freeze-frame 补齐，不截断音频）。"""
    from pipelines._ffmpeg_helpers import _ffmpeg_mix_audio

    video = tmp_path / "v.mp4"
    audio = tmp_path / "a.mp3"
    out = tmp_path / "out.mp4"
    _make_video_no_audio(video, seconds=5.0)
    _make_audio(audio, seconds=8.0)

    _ffmpeg_mix_audio(video, audio, out)

    dur = _probe_duration(out)
    assert dur >= 8.0 - 0.3, f"混音不得截断音频尾巴：期望 ≥7.7s，实际 {dur:.2f}s（修前 -shortest → ~5s）"


@skip_no_ffmpeg
def test_mix_audio_no_reencode_when_video_shorter(tmp_path):
    """5s 无声视频 + 8s 音频 → 容器 ≥7.7s 且**视频流仍 ≈5s**。

    钉死新机制：视频流 stream copy（不 tpad 烘焙定格、零重编码）。
    修前 tpad 分支会把视频流拉到 8s（本断言修前 FAIL）。
    """
    from pipelines._ffmpeg_helpers import _ffmpeg_mix_audio

    video = tmp_path / "v.mp4"
    audio = tmp_path / "a.mp3"
    out = tmp_path / "out.mp4"
    _make_video_no_audio(video, seconds=5.0)
    _make_audio(audio, seconds=8.0)

    _ffmpeg_mix_audio(video, audio, out)

    fmt_dur = _probe_duration(out)
    v_dur = _probe_stream_duration(out, "v:0")
    assert fmt_dur >= 8.0 - 0.3, f"容器时长须保住音频全长：{fmt_dur:.2f}s"
    assert v_dur is not None and v_dur <= 5.5, (
        f"视频流必须原样 copy（≈5s），实际 {v_dur:.2f}s——tpad 重编码已删，不得烘焙定格"
    )


@skip_no_ffmpeg
def test_mix_audio_video_longer_keeps_shortest(tmp_path):
    """8s 视频 + 5s 音频 → 保留 -shortest 切视频尾，输出 ≈5s（旧行为不变，特征化钉死）。"""
    from pipelines._ffmpeg_helpers import _ffmpeg_mix_audio

    video = tmp_path / "v.mp4"
    audio = tmp_path / "a.mp3"
    out = tmp_path / "out.mp4"
    _make_video_no_audio(video, seconds=8.0)
    _make_audio(audio, seconds=5.0)

    _ffmpeg_mix_audio(video, audio, out)

    dur = _probe_duration(out)
    assert dur <= 5.5, f"视频更长时仍应切到音频长：期望 ≤5.5s，实际 {dur:.2f}s"


@skip_no_ffmpeg
def test_mix_audio_embedded_audio_branch_no_reencode(tmp_path):
    """视频自带 5s 音轨 + 外部 8s 音频（amix 分支）→ 容器 ≥7.7s 且视频流 ≈5s。

    has_audio 分支同样去 tpad：amix duration=longest 保音频全长，视频流 copy。
    修前 tpad+amix 分支会把视频流拉到 8s（本断言修前 FAIL）。
    """
    from pipelines._ffmpeg_helpers import _ffmpeg_mix_audio

    video = tmp_path / "v.mp4"
    audio = tmp_path / "a.mp3"
    out = tmp_path / "out.mp4"
    _make_video_with_audio(video, v_seconds=5.0, a_seconds=5.0)
    _make_audio(audio, seconds=8.0)

    _ffmpeg_mix_audio(video, audio, out)

    fmt_dur = _probe_duration(out)
    v_dur = _probe_stream_duration(out, "v:0")
    assert fmt_dur >= 8.0 - 0.3, f"amix 分支容器时长须保住外部音频全长：{fmt_dur:.2f}s"
    assert v_dur is not None and v_dur <= 5.5, f"amix 分支视频流必须 copy（≈5s），实际 {v_dur:.2f}s"


@skip_no_ffmpeg
def test_prepare_segment_extends_short_clip(tmp_path):
    """B1 part2: _prepare_segment_from_file 源短于目标时长 → 先补长再截，输出 ≈ 目标时长。

    ai_video 8s 上限但单镜常更短；修前 -t duration 只截不补 → 段短于旁白 → 讲一半结束。
    """
    from pipelines.video_gen_mixed import _prepare_segment_from_file

    src = tmp_path / "src.mp4"
    subprocess.run(
        [FFMPEG, "-y", "-f", "lavfi", "-i", f"color=c=red:s=320x240:r=15:d=3.0",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", str(src)],
        check=True, capture_output=True, timeout=60,
    )
    out = tmp_path / "seg.mp4"
    _prepare_segment_from_file(src, out, duration=8.0, width=320, height=240)
    dur = _probe_duration(out)
    assert dur >= 8.0 - 0.3, f"短源须补长到目标时长：期望 ≥7.7s，实际 {dur:.2f}s（修前 ~3s）"
