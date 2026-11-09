"""A3: 主色调 picker 诚实化契约——删 hue 后 primary 不再做色相偏移调色，文案不得再称色相偏移。"""

from pathlib import Path


def test_primary_color_help_text_no_hue_shift_lie():
    """A3: help text 不得再称「主色调通过色相偏移自动应用」（hue 已删，primary 不再做色相偏移）。"""
    # backend/tests/.. /.. = project root（frontend 是 backend 的兄弟目录）
    src = (Path(__file__).resolve().parents[2] / "frontend" / "src" / "components" / "workspace" / "video-gen" / "course-project-detail.tsx").read_text(encoding="utf-8")
    assert "色相偏移" not in src, "删 hue 后不应再称色相偏移（谎言）"
    # 2026-09-14 基线债修复：文案演进后该文件已不再提「主色调」——存在性断言
    # 退役（不再强制提）；反谎言断言（色相偏移不得复活）保留为唯一不变量。
