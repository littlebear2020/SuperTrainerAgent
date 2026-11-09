"""Task 3m: ViMax 软字幕兜底。

verify_timing_alignment 的本地副本及其 3 个测试已于 2026-10-05 删除——生产实现
已删（app 内零调用方，Task 17 死代码清理），副本断言的是测试自带代码而非生产行为。
"""

from pathlib import Path

from app.gateway.video_gen.engines.vimax.audio_overlay import ship_soft_subtitle


def test_ship_soft_subtitle_copies_srt(tmp_path: Path):
    (tmp_path / "subtitle.srt").write_text("1\n00:00 --> 00:05\nHello", encoding="utf-8")
    dest = ship_soft_subtitle(tmp_path)
    assert dest is not None and dest.exists()
    assert dest.name == "final_video.srt"


def test_ship_soft_subtitle_no_srt(tmp_path: Path):
    assert ship_soft_subtitle(tmp_path) is None
