"""Task 9: live 验证 runbook 须存在并列出 live-verify 项（无 ARK_API_KEY → 标 PENDING）。"""

from pathlib import Path


def test_live_verify_runbook_exists_and_lists_items():
    """runbook 须存在、列 4 项 live-verify、标 PENDING、记录 C=N/A。"""
    runbook = Path(__file__).resolve().parents[2] / "docs" / "plans" / "archive" / "2026-07-13-video-gen-halfbaked-fix-live-verify-runbook.md"
    assert runbook.exists(), "live-verify runbook 须存在"
    src = runbook.read_text(encoding="utf-8")
    # 4 项 live-verify（Task 1/14 绝对路径、Task 6 seedream i2i、Task 13 Step0 size、Task 14 TOS）
    assert "绝对路径" in src, "Task 1/14 绝对路径 i2i 项须列出"
    assert "seedream i2i" in src, "Task 6 seedream i2i 项须列出"
    assert "2048x1152" in src, "Task 13 Step0 size 集合项须列出"
    assert "TOS" in src, "Task 14 TOS→local 项须列出"
    assert "PENDING" in src, "无 ARK_API_KEY 须标 PENDING"
    assert "N/A" in src, "C 诚实化须记录 N/A 结论（无 provider 选择器 UI）"
