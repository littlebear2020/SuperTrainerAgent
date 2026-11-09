"""PPT Master mock fixtures 测试（恢复重写）。"""

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MOD_PATH = REPO_ROOT / "skills/public/ppt-master/scripts/mock_fixtures.py"


@pytest.fixture()
def mod():
    spec = importlib.util.spec_from_file_location("mock_fixtures", MOD_PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_build_project_fixture_creates_pages_with_element_ids(mod, tmp_path):
    proj = mod.build_project_fixture(tmp_path / "proj", pages=3)
    svgs = sorted((proj / "svg_output").glob("*.svg"))
    assert len(svgs) == 3
    content = svgs[0].read_text(encoding="utf-8")
    assert 'viewBox="0 0 1280 720"' in content
    assert 'data-element-id="p1-title"' in content
    assert (proj / "spec_lock.md").is_file()


def test_fixture_is_deterministic(mod, tmp_path):
    a = mod.build_project_fixture(tmp_path / "a", pages=2)
    b = mod.build_project_fixture(tmp_path / "b", pages=2)
    assert (a / "svg_output/page_1.svg").read_text() == (b / "svg_output/page_1.svg").read_text()


def test_completed_and_failed_run_records(mod, tmp_path):
    proj = mod.build_project_fixture(tmp_path / "proj", pages=2)
    ok = mod.completed_run_record(proj)
    assert ok["status"] == "completed"
    assert ok["pages"] == 2
    bad = mod.failed_run_record("boom")
    assert bad["status"] == "failed"
    assert "boom" in bad["error"]


def test_k5_title_xml_escaped(tmp_path, mod):
    """K5 修复回归（2026-09-11）：含 & < > 的 title 产出合法 SVG。"""
    import xml.etree.ElementTree as ET

    project = mod.build_project_fixture(tmp_path / "proj", pages=1, title="A & B <C> 'D'")
    svg = (project / "svg_output" / "page_1.svg").read_text(encoding="utf-8")
    root = ET.fromstring(svg)  # 非法 XML 会抛 ParseError
    texts = [t.text for t in root.iter("{http://www.w3.org/2000/svg}text")]
    assert any("A & B <C>" in (t or "") for t in texts)
