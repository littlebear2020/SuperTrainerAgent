"""B6: ViMax 字幕烧录加 subtitle_style（force_style + fontsdir）。

split 的 bf7b75a2/a99ddf93 已做 (a) narration 写 SRT + (b) 默认开 burn_subtitles + overlay 接线，
但 ``build_overlay_cmd`` 仍是裸 ``subtitles='{path}'``（无 force_style/fontsdir），gateway 镜像也无
CJK 字体 → 部署后中文字幕烧入但缺字/方块。本测 B6c：build_overlay_cmd 接 subtitle_style，
经 subtitle_filter_vf 加 force_style + fontsdir。
"""

from pathlib import Path

from app.gateway.video_gen.engines.vimax.audio_overlay import build_overlay_cmd


def _srt(tmp_path: Path) -> Path:
    s = tmp_path / "subtitle.srt"
    s.write_text("1\n00:00:00,000 --> 00:00:05,000\n测试\n", encoding="utf-8")
    return s


def test_burn_uses_force_style(tmp_path: Path) -> None:
    cmd = build_overlay_cmd(
        Path("/v.mp4"),
        Path("/a.mp3"),
        None,
        Path("/o.mp4"),
        subtitle_path=_srt(tmp_path),
        burn_subtitles=True,
        subtitle_style="default",
    )
    s = " ".join(cmd)
    assert "subtitles=" in s, s
    assert "force_style" in s, f"应含 force_style（subtitle_style 映射），实际 {s}"


def test_no_burn_when_disabled(tmp_path: Path) -> None:
    cmd = build_overlay_cmd(
        Path("/v.mp4"),
        Path("/a.mp3"),
        None,
        Path("/o.mp4"),
        subtitle_path=_srt(tmp_path),
        burn_subtitles=False,
        subtitle_style="default",
    )
    assert "subtitles=" not in " ".join(cmd), "burn 关闭时不应出现 subtitles= 滤镜"
