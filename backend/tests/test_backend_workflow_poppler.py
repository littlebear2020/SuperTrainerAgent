"""CI environment contract for PDF ingestion tests.

The podcast commit 67968287 added a runtime Poppler dependency. This test only
checks the workflow contract; it never installs packages or invokes providers.
"""

from pathlib import Path

import yaml


def test_poppler_is_installed_and_verified_before_backend_tests():
    workflow_path = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "backend-unit-tests.yml"
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["backend-unit-tests"]["steps"]
    test_index = next(index for index, step in enumerate(steps) if "make test" in step.get("run", ""))
    before_test = steps[:test_index]

    assert any("poppler-utils" in step.get("run", "") for step in before_test), "CI must install Poppler before make test"
    assert any("pdftotext -v" in step.get("run", "") for step in before_test), "CI must verify pdftotext before make test"
