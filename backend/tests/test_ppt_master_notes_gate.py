"""ppt-master 备注导出门禁两路测试（DEF-05 / F，2026-09-23 批 2）。

- 路 1（缺失→WARN）：notes 启用但 notes/ 缺失 / 覆盖不足 → 导出器
  ``find_notes_files`` 打 stderr WARN 且不 hard fail（给 agent 自修复机会）；
  工作流硬拦截在 generate-pptx.md Step 7 质量门（文本断言钉住，防回退成
  "声称检查却 0 errors 放行"）。
- 路 2（齐全→通过）：notes/ 齐备且逐页匹配 → 零 WARN，返回全部内容。

discovery 模块位于 skills 目录（运行时挂载进沙箱），按 vendored 测试的
做法直接以路径导入，不动 14GB 沙箱镜像。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL_DIR = REPO_ROOT / "skills" / "public" / "ppt-master"
DISCOVERY_PATH = SKILL_DIR / "scripts" / "svg_to_pptx" / "pptx_package" / "discovery.py"
WORKFLOW_PATH = SKILL_DIR / "workflows" / "generate-pptx.md"


def _load_discovery():
    spec = importlib.util.spec_from_file_location("pptx_package_discovery", DISCOVERY_PATH)
    module = importlib.util.module_from_spec(spec)
    # discovery imports `slide_roster` from the scripts/ dir (sandbox runtime layout).
    scripts_dir = str(SKILL_DIR / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec.loader.exec_module(module)
    return module


def test_missing_notes_dir_warns_not_fails(tmp_path, capsys):
    discovery = _load_discovery()
    (tmp_path / "svg_output").mkdir()
    (tmp_path / "svg_output" / "01_cover.svg").write_text("<svg/>", encoding="utf-8")

    result = discovery.find_notes_files(tmp_path, [tmp_path / "svg_output" / "01_cover.svg"])
    assert result == {}  # 不 hard fail（agent 自修复机会）
    err = capsys.readouterr().err
    assert "notes/ directory is missing" in err
    assert "no speaker notes" in err


def test_partial_coverage_warns_with_missing_stems(tmp_path, capsys):
    discovery = _load_discovery()
    svg_dir = tmp_path / "svg_output"
    svg_dir.mkdir()
    for stem in ("01_cover", "02_agenda", "03_body"):
        (svg_dir / f"{stem}.svg").write_text("<svg/>", encoding="utf-8")
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()
    (notes_dir / "01_cover.md").write_text("开场备注", encoding="utf-8")
    (notes_dir / "02_agenda.md").write_text("议程备注", encoding="utf-8")

    result = discovery.find_notes_files(tmp_path, sorted(svg_dir.glob("*.svg")))
    assert set(result) == {"01_cover", "02_agenda"}
    err = capsys.readouterr().err
    assert "1/3 slide(s) have no matching notes file" in err
    assert "03_body" in err


def test_complete_coverage_is_silent(tmp_path, capsys):
    discovery = _load_discovery()
    svg_dir = tmp_path / "svg_output"
    svg_dir.mkdir()
    stems = ("01_cover", "02_agenda", "03_body")
    for stem in stems:
        (svg_dir / f"{stem}.svg").write_text("<svg/>", encoding="utf-8")
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()
    for stem in stems:
        (notes_dir / f"{stem}.md").write_text(f"{stem} 备注", encoding="utf-8")

    result = discovery.find_notes_files(tmp_path, sorted(svg_dir.glob("*.svg")))
    assert set(result) == set(stems)
    captured = capsys.readouterr()
    assert captured.err == ""  # 齐全 → 零 WARN


def test_workflow_step7_gate_hard_checks_notes_coverage():
    """工作流硬门禁文本钉住：声明开启时逐页核对，不通过则拦截。"""
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "Speaker-Notes coverage is a hard gate" in text, "Step 7 硬门禁段落缺失"
    assert "must equal the published slide count" in text
    assert "blocks this gate" in text
    # 7.1 成功判据同样要求按计数与 stem 核对
    assert "verified by count and stem, not assumption" in text
