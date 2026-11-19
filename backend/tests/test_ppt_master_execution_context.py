"""Deterministic current-page execution context projection tests."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL_DIR = REPO_ROOT / "skills" / "public" / "ppt-master"
SCRIPTS_DIR = SKILL_DIR / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from project_management.execution_context import (  # noqa: E402
    EXECUTION_CONTEXT_SCHEMA,
    EXECUTION_CONTEXT_TOKEN_BUDGET,
    ExecutionContextError,
    RuleSelector,
    build_execution_context,
    extract_preset_names,
    extract_rule_fragment,
    select_rule_fragments,
)
from project_management.page_context import (  # noqa: E402
    PageContextResult,
    build_page_context,
    render_page_context,
)
from project_management.project_specs import validate_project_artifacts  # noqa: E402


DESIGN_SPEC = """<!-- ppt-master-schema: design-spec/v1 -->
# Synthetic Design Spec

## IX. Content Outline

#### Slide 01 - Cover

- **Audience move**: Understand the cover
- **Relationships**: none
- **Title**: Cover
- **Core message**: P01 only
- **Content**: Use images/cover.png for the P01 cover only
- **Native-ready**: cover-image=no

#### Slide 03 - Industry table

- **Audience move**: Compare industries
- **Relationships**: rows and columns jointly address every cell
- **Title**: Industry table
- **Core message**: P03 only
- **Content**: P03 table cells only
- **Native-ready**: industry-match-table=yes

## X. Speaker Notes Requirements

- **Generation**: disabled
"""

SPEC_LOCK = """<!-- ppt-master-schema: spec-lock/v1 -->
# Execution Lock

## canvas
- viewBox: 0 0 1280 720
- format: PPT 16:9

## communication
- primary_language: zh-CN
- audience: synthetic managers
- objective: synthetic objective
- core_message: synthetic core
- consumption_mode: balanced

## mode
- mode: custom
- mode_references: instructional, briefing
- mode_behavior: synthetic mode behavior

## visual_style
- visual_style: custom
- visual_style_references: swiss-minimal, editorial
- visual_style_behavior: synthetic style behavior

## colors
- background: '#FFFFFF'
- secondary_background: '#FFF1EA'
- primary: '#E9501A'
- accent: '#1C1C1E'
- secondary_accent: '#B5B0A8'
- body_text: '#3A3A3C'
- secondary_text: '#8A8A8E'
- divider: '#C9C4BC'

## typography
- font_family: Microsoft YaHei, SimHei, sans-serif
- title_family: Microsoft YaHei, SimHei, sans-serif
- body_family: Microsoft YaHei, SimHei, sans-serif
- display_family: Times New Roman, SimSun, serif
- annotation_family: Times New Roman, Microsoft YaHei, serif
- body: 18
- title: 32
- subtitle: 22
- hero_title: 52
- section_label: 15
- annotation: 15
- footer: 12
- page_number: 12
- table_body: 16
- table_header: 16
- list_lead: 20

## icons
- library: none
- inventory: none

## images
- P01: images/cover.png | source=user | crop=adaptive

## page_rhythm
- P01: anchor
- P03: dense

## page_visualizations
- P03: table/record_table

## pptx_structure
- mode: flat

## forbidden
- `mask`, `<style>`, `class`, external CSS, `<foreignObject>`, `textPath`, `@font-face`, `<animate*>`, `<set>`, `<script>` / event attributes, `<iframe>`
- HTML named entities in text; write typography as raw Unicode and escape XML reserved characters
"""


def _write_project(root: Path, *, design: str = DESIGN_SPEC, lock: str = SPEC_LOCK) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "design_spec.md").write_text(design, encoding="utf-8")
    (root / "spec_lock.md").write_text(lock, encoding="utf-8")
    (root / "images").mkdir(exist_ok=True)
    (root / "images" / "cover.png").write_bytes(b"synthetic-image")
    (root / "svg_output").mkdir(exist_ok=True)
    errors, _warnings = validate_project_artifacts(root, include_design=False)
    assert errors == []
    return root


def _page_result(project: Path, page: str) -> PageContextResult:
    return build_page_context(project, page)


def test_extract_rule_fragment_returns_exact_source_slice(tmp_path):
    source = tmp_path / "references" / "sample.md"
    source.parent.mkdir()
    source.write_text("before\nSTART\nexact body\nEND\nafter\n", encoding="utf-8")
    selector = RuleSelector("core.sample", "references/sample.md", "START", "END")

    fragment = extract_rule_fragment(selector, skill_dir=tmp_path)

    assert fragment["text"] == "START\nexact body\n"
    assert fragment["rule_id"] == "core.sample"
    assert fragment["source_path"] == "references/sample.md"
    assert fragment["source_anchor"] == "START → END"
    assert len(fragment["source_sha256"]) == 64


@pytest.mark.parametrize(
    ("start", "end", "message"),
    [
        ("MISSING", "END", "start_anchor"),
        ("START", "MISSING", "end_anchor"),
        ("DUP", "END", "start_anchor"),
        ("START", "DUP", "end_anchor"),
        ("END", "START", "before"),
    ],
)
def test_extract_rule_fragment_rejects_invalid_anchors(tmp_path, start, end, message):
    source = tmp_path / "references" / "sample.md"
    source.parent.mkdir()
    source.write_text("START\nbody\nEND\nDUP\nDUP\n", encoding="utf-8")

    with pytest.raises(ExecutionContextError, match=message):
        extract_rule_fragment(RuleSelector("bad", "references/sample.md", start, end), skill_dir=tmp_path)


def test_extract_preset_names_preserves_all_exact_names_in_source_order():
    names = extract_preset_names(skill_dir=SKILL_DIR)

    assert len(names) == 187
    assert len(set(names)) == 187
    assert list(names[:8]) == [
        "line",
        "lineInv",
        "straightConnector1",
        "bentConnector2",
        "bentConnector3",
        "bentConnector4",
        "bentConnector5",
        "curvedConnector2",
    ]
    assert names[-1] == "actionButtonMovie"


def test_p01_projection_contains_image_rules_without_table_modules(tmp_path):
    project = _write_project(tmp_path / "project")
    baseline = render_page_context(_page_result(project, "P01"))[0]

    payload = build_execution_context(_page_result(project, "P01"))

    rule_ids = {item["rule_id"] for item in (*payload["core_rules"], *payload["module_rules"])}
    assert "module.image" in rule_ids
    assert "module.table" not in rule_ids
    assert "module.visualization_reference" not in rule_ids
    assert not any(item["rule_id"].startswith("module.native_table") for item in payload["module_rules"])
    assert "P01 only" in payload["page_context"]["brief_markdown"]
    assert "P03 table cells only" not in payload["page_context"]["brief_markdown"]
    assert render_page_context(_page_result(project, "P01"))[0] == baseline


def test_p03_projection_contains_table_and_native_rules_without_p01_content(tmp_path):
    project = _write_project(tmp_path / "project")

    payload = build_execution_context(_page_result(project, "P03"))

    rule_ids = {item["rule_id"] for item in (*payload["core_rules"], *payload["module_rules"])}
    assert "module.table" in rule_ids
    assert "module.visualization_reference" in rule_ids
    assert any(item["rule_id"].startswith("module.native_table") for item in payload["module_rules"])
    assert "module.image" not in rule_ids
    assert "P03 table cells only" in payload["page_context"]["brief_markdown"]
    assert "P01 only" not in payload["page_context"]["brief_markdown"]


def test_native_table_rules_require_explicit_native_ready_declaration(tmp_path):
    project = _write_project(
        tmp_path / "project",
        design=DESIGN_SPEC.replace("industry-match-table=yes", "industry-match-table=no"),
    )

    payload = build_execution_context(_page_result(project, "P03"))

    assert "module.table" in {item["rule_id"] for item in payload["module_rules"]}
    assert not any(item["rule_id"].startswith("module.native_table") for item in payload["module_rules"])


def test_non_flat_route_is_rejected(tmp_path):
    project = _write_project(tmp_path / "project")
    result = PageContextResult(project_path=project, page="P01", context={"global": {"pptx_structure": {"mode": "structured"}}, "page_context": {}}, inputs=())

    with pytest.raises(ExecutionContextError, match="flat"):
        select_rule_fragments(result)


def test_execution_context_has_exact_tokens_source_hashes_and_stable_context_sha(tmp_path):
    project = _write_project(tmp_path / "project")
    result = _page_result(project, "P01")

    first = build_execution_context(result)
    second = build_execution_context(result)

    assert first == second
    assert first["schema"] == EXECUTION_CONTEXT_SCHEMA
    assert first["token_status"] == "exact"
    assert first["token_budget"] == EXECUTION_CONTEXT_TOKEN_BUDGET == 16000
    assert 0 < first["tokens"]["total"] <= 16000
    assert len(first["context_sha256"]) == 64
    assert len(json.dumps(first, ensure_ascii=False).encode("utf-8")) < 64000
    for fragment in (*first["core_rules"], *first["module_rules"]):
        assert {"rule_id", "source_path", "source_sha256", "source_anchor", "text"} <= fragment.keys()
        assert len(fragment["source_sha256"]) == 64


def test_execution_context_budget_failure_reports_components(tmp_path):
    project = _write_project(tmp_path / "project")
    result = _page_result(project, "P01")

    with pytest.raises(ExecutionContextError) as caught:
        build_execution_context(result, token_budget=1)

    message = str(caught.value)
    assert "8000" not in message
    assert "page_projection" in message
    assert "core_rules" in message
    assert "module_rules" in message
    assert "total" in message
