"""B7: brightness 字段须暴露 slider（与 saturation/contrast 一致），backend colorgrade 消费（默认 0.0）。"""

from pathlib import Path


def test_color_palette_editor_exposes_brightness_slider():
    """B7: ColorPalette 有 brightness? 字段但无 slider → 须暴露（与饱和度/对比度一致）。"""
    src = (Path(__file__).resolve().parents[2] / "frontend" / "src" / "components" / "workspace" / "video-gen" / "color-palette-editor.tsx").read_text(encoding="utf-8")
    assert "brightness" in src, "ColorPalette 有 brightness? 字段，须暴露 slider"
    assert "亮度" in src, "须有「亮度」label（与饱和度/对比度一致）"
