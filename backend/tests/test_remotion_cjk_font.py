"""Task 3n: Remotion CJK 字体检查 + 模板评审基础设施。"""

from pathlib import Path

import pytest

from app.gateway.video_gen.pipelines.remotion_template import check_cjk_font_in_template

# CI 守卫（2026-09-23 收尾批）：本文件用例经 AppConfig 读真实 config.yaml
# （gitignored，CI fresh checkout 不存在）——缺失时跳过，本地在位照常执行。
pytestmark = pytest.mark.skipif(
    not any((Path(__file__).resolve().parents[i] / "config.yaml").exists() for i in (1, 2)),
    reason="依赖本地 gitignored config.yaml，CI 跳过",
)


def test_cjk_font_check_returns_report():
    """返回结构化报告（has_cjk_font/font_count/note），不崩。"""
    r = check_cjk_font_in_template()
    assert "has_cjk_font" in r
    assert "font_count" in r
    assert "note" in r
    assert isinstance(r["has_cjk_font"], bool)
    assert isinstance(r["font_count"], int)


def test_cjk_font_check_note_actionable():
    """无论有无，note 都给出可操作指引。"""
    r = check_cjk_font_in_template()
    assert len(r["note"]) > 10  # 有实质内容
