"""B6+B8: video-gen-page 文案诚实化——删漫改 stale 文案 + 目标时长 label 体现下限引导。"""

from pathlib import Path


def test_video_gen_page_no_stale_comic_and_honest_duration_label():
    """B6: 漫改是 stale 文案（comic 非合法 video_type）；B8: 目标时长 label 须诚实体现下限引导。"""
    src = (Path(__file__).resolve().parents[2] / "frontend" / "src" / "components" / "workspace" / "video-gen" / "video-gen-page.tsx").read_text(encoding="utf-8")
    assert "漫改" not in src, "漫改是 stale 文案（comic 非合法 video_type），应删"
    assert "引导" in src or "下限" in src, "目标时长 label 须诚实体现镜数下限引导"
