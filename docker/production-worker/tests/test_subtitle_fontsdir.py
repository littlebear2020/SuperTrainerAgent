"""B3: _subtitle_filter_vf 须带 fontsdir，让 libass 在无/坏 fontconfig 时仍能找 CJK 字体。

根因：worker 镜像缺 fonts-noto-cjk 或 fontconfig 未刷新 → libass 找不到中文字体 →
subtitles 滤镜失败 → "字幕烧录失败"。显式 fontsdir 兜底绕过 fontconfig。
"""


def test_subtitle_filter_includes_explicit_fontsdir(tmp_path):
    from pipelines._ffmpeg_helpers import _subtitle_filter_vf

    vf = _subtitle_filter_vf(tmp_path / "s.srt", None, fontsdir="/usr/share/fonts")
    assert "fontsdir=" in vf, "显式 fontsdir 必须写进滤镜串"
    assert "/usr/share/fonts" in vf


def test_subtitle_filter_auto_detects_fontsdir():
    """无显式 fontsdir 时，自动探测候选字体目录（macOS/Linux 至少一个存在）。"""
    from pipelines._ffmpeg_helpers import _subtitle_filter_vf

    vf = _subtitle_filter_vf(__import__("pathlib").Path("/x/s.srt"))
    assert "fontsdir=" in vf, "应自动探测到系统字体目录并加 fontsdir"


def test_subtitle_filter_force_style_combined_with_fontsdir(tmp_path):
    """force_style + fontsdir 同时存在（顺序无关，两个都在）。"""
    from pipelines._ffmpeg_helpers import _subtitle_filter_vf

    vf = _subtitle_filter_vf(tmp_path / "s.srt", "Bold=1", fontsdir="/usr/share/fonts")
    assert "fontsdir=" in vf
    assert "force_style=" in vf
