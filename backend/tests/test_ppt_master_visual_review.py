"""PPT Master ppt_visual_review 测试（恢复重写）。"""

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MOD_PATH = REPO_ROOT / "skills/public/ppt-master/scripts/ppt_visual_review.py"
FIX_MOD_PATH = REPO_ROOT / "skills/public/ppt-master/scripts/mock_fixtures.py"


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture()
def mods():
    return _load(MOD_PATH, "ppt_visual_review"), _load(FIX_MOD_PATH, "mock_fixtures")


def test_review_project_missing_svg_output_raises(mods, tmp_path):
    review, _ = mods
    with pytest.raises(ValueError):
        review.review_project(tmp_path / "empty")


def test_review_project_returns_per_page_results(mods, tmp_path):
    review, fixtures = mods
    proj = fixtures.build_project_fixture(tmp_path / "proj", pages=2)
    result = review.review_project(proj)
    assert len(result["pages"]) == 2
    for page in result["pages"]:
        assert {"page_index", "passed", "errors", "warnings"} <= set(page)
    assert "summary" in result
