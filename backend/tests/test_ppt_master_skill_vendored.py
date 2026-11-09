"""PPT Master 技能 vendored 完整性冒烟测试（恢复计划 Task 1）。

2026-09-11 升级 6.3.0：新增 attribution_guard 冒烟 + P1 覆盖段
P11/P12/P13 硬钉（§八.1——纯文档级软约束 agent 可能无视，测试钉住
marker 字符串，walkthrough 发现不遵守再升级 env kill-switch）。
"""

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL_DIR = REPO_ROOT / "skills" / "public" / "ppt-master"


def test_skill_md_exists_with_correct_name():
    skill_md = SKILL_DIR / "SKILL.md"
    assert skill_md.is_file(), "skills/public/ppt-master/SKILL.md 缺失"
    head = skill_md.read_text(encoding="utf-8")[:2000]
    assert "name: ppt-master" in head


def test_core_scripts_referenced_by_soul_exist():
    for rel in [
        "scripts/project_manager.py",
        "scripts/finalize_svg.py",
        "scripts/svg_to_pptx.py",
        "scripts/svg_quality_checker.py",
        "scripts/source_to_md",
    ]:
        assert (SKILL_DIR / rel).exists(), f"{rel} 缺失"


def test_skill_discovered_and_enabled_by_loader():
    from deerflow.skills.loader import load_skills

    skills = {s.name: s for s in load_skills()}
    assert "ppt-master" in skills, "loader 未发现 ppt-master"
    assert skills["ppt-master"].enabled is True


def test_attribution_guard_passes_on_vendored_tree():
    """6.3.0 fail-closed 完整性门：vendored 补丁后必须仍 pass（exit 0）。"""
    result = subprocess.run(
        [sys.executable, str(SKILL_DIR / "scripts" / "attribution_guard.py")],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, f"attribution_guard fail-closed 触发：{result.stderr.strip()}"


def test_overlay_pins_document_patches():
    """P1 覆盖段硬钉：P11–P13 与 P20–P22 文档级补丁的关键约束必须在 SKILL.md。"""
    overlay = _trainerai_overlay()
    # P11：禁 web 图搜
    assert "禁用 `web`" in overlay
    assert "image-searcher.md" in overlay
    assert "image_search.py" in overlay
    # P12：确认面 chat-only + 禁 confirm_ui
    assert "P12" in overlay
    assert "confirm_ui/server.py" in overlay
    assert "ask_clarification" in overlay
    # P13：禁 live-preview 自启动 + 禁 svg_editor server
    assert "P13" in overlay
    assert "svg_editor/server.py" in overlay
    # P20：chat 确认持久化
    assert "P20" in overlay
    assert "confirm_state.json" in overlay
    assert "confirmed_at" in overlay
    assert "禁止重新发起已确认" in overlay
    # P21：源图转写一次性落盘与复查收敛
    assert "P21" in overlay
    assert "source_image_transcriptions.md" in overlay
    assert "整图重读 ≤1 次" in overlay
    assert "目视为准" in overlay
    # P22：磁盘预检与 0 字节即停
    assert "P22" in overlay
    assert "`df -h`" in overlay
    assert "可用空间" in overlay
    assert "大小 >0" in overlay
    assert "0 字节" in overlay
    assert "每 5 页" in overlay
    # P1 基本面：D3 铁律指向 SOUL.md
    assert "D3 执行铁律" in overlay
    assert "backend/seed/agents/ppt-master/SOUL.md" in overlay


def _trainerai_overlay() -> str:
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    assert "TrainerAI 覆盖" in text, "P1 覆盖段缺失"
    start = text.index("TrainerAI 覆盖")
    end = text.index("# PPT Master Skill", start)
    return text[start:end]
