"""Worker CJK 字体修复（R1: .ttc/.ttf 事实错误修正）+ T6 Bold 校验。

验证：
1. Dockerfile apt-get 安装 fonts-noto-cjk（修 libass 字幕路径）。
2. mixed.py drawtext 不再引用不存在的 NotoSansCJK-Regular.ttf（Debian 该包只提供 .ttc），
   改用 fontconfig 族名 font='Noto Sans CJK SC'。
3. T6（2026-08-13）：Bold 字体路径在 _CJK_FONT_CANDIDATES 中，ASS 样式需要 Bold 变体。
"""

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DOCKERFILE = REPO / "docker/production-worker/Dockerfile"
MIXED = REPO / "docker/production-worker/pipelines/video_gen_mixed.py"


def test_dockerfile_installs_cjk_fonts():
    """Dockerfile apt-get 必须包含 fonts-noto-cjk。"""
    content = DOCKERFILE.read_text(encoding="utf-8")
    assert "fonts-noto-cjk" in content, "Worker Dockerfile 必须安装 fonts-noto-cjk，否则 libass 字幕 fontconfig 找不到 CJK 字体，中文字幕全豆腐块。"
    assert "fc-cache" in content, "装字体后需 fc-cache -f 让 fontconfig 索引新字体"


def test_mixed_drawtext_uses_fontconfig_not_ttf_path():
    """drawtext 不能引用 NotoSansCJK-Regular.ttf（Debian fonts-noto-cjk 只提供 .ttc）。

    应改用 font='Noto Sans CJK SC' 走 fontconfig，正确选 SC 字面。
    """
    content = MIXED.read_text(encoding="utf-8")
    assert "NotoSansCJK-Regular.ttf" not in content, "mixed.py 引用的 NotoSansCJK-Regular.ttf 在 fonts-noto-cjk 包里不存在（该包提供 .ttc）。drawtext 应改用 font='Noto Sans CJK SC' 走 fontconfig。"
    assert "Noto Sans CJK SC" in content, "drawtext 应使用 fontconfig 族名 font='Noto Sans CJK SC'"


def test_cjk_font_candidates_includes_bold():
    """T6: _CJK_FONT_CANDIDATES 必须包含 Bold 路径——ASS 样式需要 Bold 变体。

    Debian fonts-noto-cjk 提供 NotoSansCJK-Bold.ttc。ASS Style 的 Bold=1
    需要系统有对应字体文件，否则 libass 回落到 Regular，样式无粗体效果。
    """
    helpers = REPO / "docker/production-worker/pipelines/_ffmpeg_helpers.py"
    content = helpers.read_text(encoding="utf-8")
    assert "NotoSansCJK-Bold.ttc" in content, "_CJK_FONT_CANDIDATES 必须包含 Bold 路径"


def test_subtitle_filter_vf_ass_no_force_style():
    """T6: _subtitle_filter_vf 对 .ass 文件不拼 force_style（样式已 baked）。"""
    helpers = REPO / "docker/production-worker/pipelines/_ffmpeg_helpers.py"
    content = helpers.read_text(encoding="utf-8")
    assert "is_ass" in content, "_subtitle_filter_vf 应检测 .ass 后缀跳过 force_style"
