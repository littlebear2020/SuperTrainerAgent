"""Bug 2: colorgrade 须在字幕烧录之前——避免 hue/eq 把字幕一起染色（Task 3l 接通
colorgrade 时未考虑与字幕的执行顺序）。

当前顺序 concat→burn字幕→...→colorgrade 会连字幕一起调色（白色字幕描边偏色）。
正确顺序：colorgrade 调画面 → 再烧字幕（字幕保持可读）。
"""
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from pipelines.video_gen_ai_visual import compose_ai_visual  # noqa: E402


def test_colorgrade_runs_before_subtitle_burn(tmp_path):
    """同时有 color_palette + subtitle 时，colorgrade 须先于字幕烧录执行。"""
    # P1-1: local 图片现经 _image_to_video 转换（下方 patch）；payload 用相对路径
    # 对齐生产形态（visuals/...），不再用绝对路径绕过 task_dir 解析。
    seg_src = tmp_path / "0.png"
    seg_src.write_bytes(b"png")
    srt = tmp_path / "sub.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:02,000\nhi\n\n")
    audio = tmp_path / "audio.mp3"
    audio.write_bytes(b"au")

    payload = {
        "frames": [
            {
                "index": 0,
                "local_visual_path": "0.png",
                "visual_type": "image",
                "duration": 2.0,
            }
        ],
        "config": {
            "width": 1080,
            "height": 1920,
            "transition": "none",
            "color_palette": {"primary": "#ff0000", "saturation": 1.2},
        },
        "subtitle_path": str(srt),
        "audio_concat_path": str(audio),
        "quality": "standard",
        "output_format": "mp4",
    }

    log: list[str] = []

    def mock_concat(segments, output, **k):
        output.write_bytes(b"concat")

    def mock_colorgrade_vf(palette):
        # Task 3: colorgrade 顺序不变量已迁入共享 finalizer _apply_colorgrade_and_subtitles。
        # _colorgrade_vf 在其顶部无条件调用（合并/拆分路径都先于字幕烧录），返非 None 触发 colorgrade。
        log.append("colorgrade")
        return "eq=saturation=1.2"

    def mock_mix_audio(video, audio_in, output):
        output.write_bytes(b"mixed")

    def mock_convert(video, output, **k):
        output.write_bytes(b"final")
        return output

    def mock_finalize(result, final_raw):
        return result

    def fake_run(cmd, *a, **k):
        out = Path(cmd[-1])
        out.write_bytes(b"x")
        # 字幕烧录的 cmd 含 subtitles= 过滤器
        if any("subtitles=" in str(c) for c in cmd):
            log.append("burn")
        return type("P", (), {"returncode": 0, "stderr": b""})()

    with (
        # P1-1: local 图片必须经 _image_to_video 转换（修复前直接 copy 改名 mp4）
        patch("pipelines.video_gen_ai_visual._image_to_video") as m_i2v,
        patch("pipelines.video_gen_ai_visual._ffmpeg_concat", side_effect=mock_concat),
        # Task 3: colorgrade+字幕已改调共享 finalizer；spy 跟随 _colorgrade_vf（合并/拆分都先调，先于字幕烧录）
        patch("pipelines._ffmpeg_helpers._colorgrade_vf", side_effect=mock_colorgrade_vf),
        patch("pipelines.video_gen_ai_visual._ffmpeg_mix_audio", side_effect=mock_mix_audio),
        patch("pipelines._ffmpeg_helpers._probe_duration", return_value=0.0),  # 跳过 fade（finalizer 内 _probe_duration）
        patch("pipelines.video_gen_ai_visual._ffmpeg_convert_format", side_effect=mock_convert),
        patch("pipelines.video_gen_ai_visual._finalize_output", side_effect=mock_finalize),
        patch("pipelines.video_gen_ai_visual._ffmpeg_extract_thumbnail"),
        patch("pipelines.video_gen_ai_visual.subprocess.run", side_effect=fake_run),
    ):
        m_i2v.side_effect = lambda src, dur, out, *a, **k: Path(out).write_bytes(b"seg")
        compose_ai_visual(tmp_path, payload, {}, lambda v: None)

    m_i2v.assert_called_once()  # P1-1: local 图片必须经转换，不再直接 copy
    assert "colorgrade" in log and "burn" in log, f"两者都应执行，实际: {log}"
    assert log.index("colorgrade") < log.index("burn"), (
        f"colorgrade 须先于字幕烧录（否则字幕被调色），实际顺序: {log}"
    )
