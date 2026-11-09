"""F8: gateway 侧 _SUBTITLE_STYLE_MAP 内容守卫。改动此 map 须同步 worker
（docker/production-worker/pipelines/_ffmpeg_helpers.py —— 已核实存在，含 _SUBTITLE_STYLE_MAP + _subtitle_filter_vf）。
"""

from app.gateway.video_gen.subtitle_filter import _SUBTITLE_STYLE_MAP


def test_style_map_has_four_keys() -> None:
    assert set(_SUBTITLE_STYLE_MAP) == {"default", "bold", "minimal", "outline"}


def test_style_map_values_match_worker_snapshot() -> None:
    # 与 worker _ffmpeg_helpers._SUBTITLE_STYLE_MAP 的快照一致；改这里须同步 worker
    assert _SUBTITLE_STYLE_MAP["default"] == "FontSize=24,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,Outline=2,Alignment=2,MarginV=30"
    assert _SUBTITLE_STYLE_MAP["bold"] == "FontSize=32,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,Outline=3,BackColour=&H80000000,BorderStyle=4,Alignment=2,MarginV=40"
    assert _SUBTITLE_STYLE_MAP["minimal"] == "FontSize=18,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,Outline=1,Alignment=2,MarginV=20"
    assert _SUBTITLE_STYLE_MAP["outline"] == "FontSize=28,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,Outline=4,Alignment=2,MarginV=35"
