"""Deterministic current-page execution context projection tests."""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL_DIR = REPO_ROOT / "skills" / "public" / "ppt-master"
SCRIPTS_DIR = SKILL_DIR / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from project_management import execution_context as execution_context_module  # noqa: E402
from project_management.execution_context import (  # noqa: E402
    EXECUTION_CONTEXT_SCHEMA,
    EXECUTION_CONTEXT_TOKEN_BUDGET,
    ExecutionContextError,
    RuleSelector,
    _native_ready_status,
    _reference_ids,
    build_execution_context,
    extract_preset_names,
    extract_rule_fragment,
    render_execution_context,
    select_rule_fragments,
    validate_execution_context,
    write_execution_context,
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
        ("END", "START", "overlap|precede"),
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


def test_write_and_validate_execution_context_detects_manual_and_source_changes(tmp_path):
    project = _write_project(tmp_path / "project")
    result = _page_result(project, "P01")

    path, payload = write_execution_context(result)
    validate_execution_context(payload, result)
    assert path == project / "validation" / "execution-context" / "P01.json"
    assert json.loads(path.read_text(encoding="utf-8"))["context_sha256"] == payload["context_sha256"]

    payload["core_rules"][0]["text"] = "manually changed"
    with pytest.raises(ExecutionContextError, match="stale|mismatch|changed"):
        validate_execution_context(payload, result)

    _, fresh = write_execution_context(result)
    (project / "design_spec.md").write_text(DESIGN_SPEC.replace("P01 only", "P01 changed"), encoding="utf-8")
    with pytest.raises(ExecutionContextError, match="stale|mismatch|changed"):
        validate_execution_context(fresh, _page_result(project, "P01"))


def test_validate_execution_context_rejects_tampered_token_measurements(tmp_path):
    project = _write_project(tmp_path / "project")
    result = _page_result(project, "P01")
    _, payload = write_execution_context(result)
    validate_execution_context(payload, result)

    # context_sha256 excludes the measured `tokens`, so the validator must compare them with a fresh measurement.
    payload["tokens"]["total"] = 1

    with pytest.raises(ExecutionContextError, match="tokens"):
        validate_execution_context(payload, result)


def test_write_execution_context_rebuilds_corrupt_artifact(tmp_path):
    project = _write_project(tmp_path / "project")
    result = _page_result(project, "P01")
    path, _ = write_execution_context(result)
    path.write_text("not json", encoding="utf-8")

    _, rebuilt = write_execution_context(result)

    assert json.loads(path.read_text(encoding="utf-8"))["context_sha256"] == rebuilt["context_sha256"]
    validate_execution_context(rebuilt, result)


def test_unavailable_token_counter_prevents_write(tmp_path, monkeypatch):
    project = _write_project(tmp_path / "project")
    result = _page_result(project, "P01")
    monkeypatch.setattr(execution_context_module, "_token_counter", lambda: (None, "unavailable"))

    with pytest.raises(ExecutionContextError, match="unavailable"):
        write_execution_context(result)

    assert not (project / "validation" / "execution-context" / "P01.json").exists()


def test_token_and_byte_budget_failures_prevent_write(tmp_path, monkeypatch):
    project = _write_project(tmp_path / "project")
    result = _page_result(project, "P01")

    oversized = replace(
        result,
        context={
            **result.context,
            "page_context": {
                **result.context["page_context"],
                "brief_markdown": "word " * 16001,
            },
        },
    )
    with pytest.raises(ExecutionContextError, match="token budget"):
        build_execution_context(oversized)

    monkeypatch.setattr(execution_context_module, "EXECUTION_CONTEXT_MAX_BYTES", 1)
    with pytest.raises(ExecutionContextError, match="byte budget"):
        write_execution_context(result)
    assert not (project / "validation" / "execution-context" / "P01.json").exists()


def test_atomic_replace_failure_preserves_previous_complete_pack(tmp_path, monkeypatch):
    project = _write_project(tmp_path / "project")
    result = _page_result(project, "P01")
    path, previous = write_execution_context(result)
    previous_bytes = path.read_bytes()

    def fail_replace(_self, _target):
        raise OSError("synthetic replace failure")

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(OSError, match="synthetic replace failure"):
        write_execution_context(result)

    assert path.read_bytes() == previous_bytes
    assert not list(path.parent.glob("*.tmp"))
    assert previous["context_sha256"]


def test_render_execution_context_produces_valid_json_and_bounded_read_ranges(tmp_path):
    project = _write_project(tmp_path / "project")
    payload = build_execution_context(_page_result(project, "P03"))

    rendered, ranges = render_execution_context(payload)

    assert json.loads(rendered) == payload
    assert len(ranges) == 2
    lines = rendered.splitlines(keepends=True)
    assert ranges[0][0] == 1 and ranges[-1][1] == len(lines)
    for start, end in ranges:
        assert len("".join(lines[start - 1 : end]).encode("utf-8")) < 48000
    for line in lines:
        assert len(line.encode("utf-8")) < 48000


def test_render_execution_context_rejects_single_oversized_field():
    with pytest.raises(ExecutionContextError, match="field|48000|read"):
        render_execution_context({"oversized": "x" * 100}, max_read_bytes=10)


def test_execution_context_cli_writes_short_receipt_without_touching_svg(tmp_path, capsys):
    from project_management.cli import main

    project = _write_project(tmp_path / "project")
    svg = project / "svg_output" / "01_cover.svg"
    svg.write_text("<svg>original</svg>", encoding="utf-8")
    baseline = render_page_context(_page_result(project, "P01"))[0]

    exit_code = main(["execution-context", str(project), "P01"])

    assert exit_code == 0
    receipt = capsys.readouterr().out.strip()
    assert receipt.startswith("EXECUTION_CONTEXT path=")
    assert "page=P01" in receipt
    assert "tokens=" in receipt
    assert "sha256=" in receipt
    assert "read_ranges=" in receipt
    assert len(receipt.splitlines()) == 1
    assert "core_rules" not in receipt
    assert svg.read_text(encoding="utf-8") == "<svg>original</svg>"
    assert render_page_context(_page_result(project, "P01"))[0] == baseline


FLAT_STEP6_RULE = "**Flat Default / Beautify execution pack (P31)**"


def test_flat_workflow_uses_execution_pack_for_initial_and_compacted_contexts():
    workflow = (SKILL_DIR / "workflows" / "generate-pptx.md").read_text(encoding="utf-8")
    start = workflow.index(FLAT_STEP6_RULE)
    end = workflow.index("**Other routes**", start)
    flat = workflow[start:end]

    assert "execution-context <project_path> P<NN>" in flat
    assert "read_ranges" in flat
    assert "start_line" in flat and "end_line" in flat
    assert "compacted" in flat
    assert "first missing page" in flat
    assert "must not be read again for routine bash" in flat
    assert "unregistered trigger" in flat
    assert "Read ${SKILL_DIR}/references/executor-base.md" not in flat
    assert "Read ${SKILL_DIR}/references/shared-standards-core.md" not in flat


def _step6(workflow: str) -> str:
    return workflow[workflow.index("### Step 6: Executor Phase") : workflow.index("### Step 7: Post-processing & Export")]


def test_flat_execution_pack_is_the_first_blocking_rule_of_step6():
    """The first full-core read happened because the legacy batch looked like the Step 6 default.

    The flat rule must come first in Step 6, before the P22 sentence that mentions "the required reads"
    and before any Read of the core, so a flat run meets the execution-pack order before any core load.
    """
    step6 = _step6((SKILL_DIR / "workflows" / "generate-pptx.md").read_text(encoding="utf-8"))
    flat = step6.index(FLAT_STEP6_RULE)

    assert flat < step6.index("**TrainerAI override（P22）**")
    assert flat < step6.index("Read ${SKILL_DIR}/references/executor-base.md")
    assert flat < step6.index("Read ${SKILL_DIR}/references/shared-standards-core.md")
    assert flat < step6.index("**Other routes**")


def test_flat_step6_forbids_core_reads_before_the_execution_pack_is_read():
    step6 = _step6((SKILL_DIR / "workflows" / "generate-pptx.md").read_text(encoding="utf-8"))
    flat = step6[step6.index(FLAT_STEP6_RULE) : step6.index("**Other routes**")]

    assert "Blocking order" in flat
    assert "before the execution pack has been generated and read" in flat
    assert "never read `executor-base.md` or `shared-standards-core.md` in full" in flat
    # SKILL.md's generic "read the role reference before switching roles" would otherwise pull executor-base in first.
    assert "the Executor role-switch read in `SKILL.md` included" in flat
    # The prohibition precedes the command that builds the pack, so it already binds when the model gets there ...
    command = flat.index("execution-context <project_path> P<NN>")
    assert flat.index("Blocking order") < command
    # ... and it is the only place before that command that names either core file (no read-style mention).
    before_command = flat[:command]
    assert before_command.count("executor-base.md") == 1
    assert before_command.count("shared-standards-core.md") == 1


def test_flat_step6_has_no_read_instruction_for_core_references_or_the_complete_spec():
    step6 = _step6((SKILL_DIR / "workflows" / "generate-pptx.md").read_text(encoding="utf-8"))
    flat = step6[step6.index(FLAT_STEP6_RULE) : step6.index("**Other routes**")]

    for forbidden in ("Read ${SKILL_DIR}", "references/executor-base.md", "references/shared-standards-core.md"):
        assert forbidden not in flat
    # The P22 sentence sits outside the flat block but must not presuppose reads that a flat run never does.
    p22 = step6[step6.index("**TrainerAI override（P22）**") :].split("\n", 1)[0]
    assert "the required reads" not in p22


def test_non_flat_workflow_keeps_legacy_full_reference_recovery():
    workflow = (SKILL_DIR / "workflows" / "generate-pptx.md").read_text(encoding="utf-8")
    other = workflow[workflow.index("**Other routes**") :]
    assert "Read ${SKILL_DIR}/references/executor-base.md" in other
    assert "Read ${SKILL_DIR}/references/shared-standards-core.md" in other
    assert "triggered references" in other
    assert "non-flat only" in other[: other.index("```")]
    assert "Read the core as one batch with the exact detail files named by the retained `spec_lock.md`" in other

    recovery = (SKILL_DIR / "workflows" / "governance" / "failure-recovery.md").read_text(encoding="utf-8")
    assert "Execution context is fresh, resumed, compacted, external, or unknown" in recovery
    assert "Read the complete Design Spec, then lock, once; reload triggered inputs" in recovery


def test_resume_execute_has_a_flat_branch_that_builds_the_pack_before_any_core_read():
    resume = (SKILL_DIR / "workflows" / "stages" / "resume-execute.md").read_text(encoding="utf-8")
    flat_start = resume.index("**Flat branch (P31)**")
    flat = resume[flat_start : resume.index("**Non-flat branch**", flat_start)]

    assert "pptx_structure.mode: flat" in flat
    assert "execution-context <project_path> P<NN>" in flat
    assert "first missing page" in flat
    assert "read_ranges" in flat
    assert "never load `executor-base.md`" in flat
    assert "never read the complete Design Spec or complete lock" in flat
    assert "before the execution pack has been generated and read" in flat

    # The unconditional "complete reads happen after the Executor role core loads" sentence must not bind flat runs.
    sanity = resume[resume.index("## Step 1: Sanity check") : resume.index("## Step 2")]
    assert "must both exist; their complete reads happen after" not in sanity
    assert "For non-flat routes their complete reads happen after the Executor role core loads" in sanity
    assert "a flat route reads neither in full" in sanity
    # The conditional-dependency check cannot demand a complete read of the pair on a flat route.
    assert "(flat: from the pack and those bounded fragments)" in resume


def test_resume_execute_non_flat_branch_keeps_the_original_full_read_flow():
    resume = (SKILL_DIR / "workflows" / "stages" / "resume-execute.md").read_text(encoding="utf-8")
    non_flat = resume[resume.index("**Non-flat branch**") :]

    assert "loads `executor-base.md`" in non_flat
    assert "read the complete Design Spec, then the complete lock, once" in non_flat
    assert "resolve the effective Speaker Notes / Custom Animations / Narration Audio outcomes from `design_spec.md §I`" in non_flat
    assert "Source verification" in resume  # the conditional-dependency and verification text stays shared


def test_failure_recovery_flat_row_names_the_pack_order_without_core_reads():
    recovery = (SKILL_DIR / "workflows" / "governance" / "failure-recovery.md").read_text(encoding="utf-8")
    row = next(line for line in recovery.splitlines() if line.startswith("| Flat Default / Beautify execution context is fresh"))

    assert "before any core reference" in row
    assert "never the complete Design Spec, complete core references" in row


def test_p31_patch_row_records_the_read_order_constraint_and_resume_execute():
    patches = (SKILL_DIR / "TRAINERAI_PATCHES.md").read_text(encoding="utf-8")
    row = next(line for line in patches.splitlines() if line.startswith("| P31 |"))

    assert "workflows/stages/resume-execute.md" in row
    assert "读取顺序" in row
    assert "execution-context" in row and "executor-base.md" in row and "shared-standards-core.md" in row
    # Static pins prove wording, not model behavior: the row must say the behavior is still unverified
    # and that only a later paid retest can confirm it.
    assert "尚未验证" in row
    assert "付费复测" in row


def test_execution_pack_ownership_is_distinct_from_page_context_diagnostics():
    ownership = (SKILL_DIR / "references" / "artifact-ownership.md").read_text(encoding="utf-8")
    assert "validation/execution-context/P<NN>.json" in ownership
    assert "Derived per-page execution view" in ownership
    assert "not a second source of truth" in ownership
    assert "atomic rebuild" in ownership

    project_docs = (SKILL_DIR / "scripts" / "docs" / "project.md").read_text(encoding="utf-8")
    assert "execution-context <project_path> P01" in project_docs
    assert "read_ranges" in project_docs
    assert "does not change the diagnostic page-context output" in project_docs


def test_p31_records_execution_pack_and_failed_p30_boundary():
    patches = (SKILL_DIR / "TRAINERAI_PATCHES.md").read_text(encoding="utf-8")
    assert "| P31 |" in patches
    assert "execution-context" in patches
    assert "#28" in patches
    assert "P30" in patches


def test_render_execution_context_rejects_more_than_two_read_ranges():
    payload = {f"field-{index}": "x" * 40 for index in range(4)}

    with pytest.raises(ExecutionContextError, match="at most two|read_ranges|2"):
        render_execution_context(payload, max_read_bytes=100)


def test_extract_rule_fragment_rejects_end_anchor_inside_start_anchor(tmp_path):
    source = tmp_path / "references" / "overlap.md"
    source.parent.mkdir()
    source.write_text("START-END\nbody\n", encoding="utf-8")

    with pytest.raises(ExecutionContextError, match="overlap|precede"):
        extract_rule_fragment(
            RuleSelector("bad.overlap", "references/overlap.md", "START-END", "END"),
            skill_dir=tmp_path,
        )


def test_reference_ids_only_allow_kebab_case_identifiers():
    assert _reference_ids("instructional, swiss-minimal") == ("instructional", "swiss-minimal")
    for unsafe in ("../executor-base", "modes/instructional", "instructional_foo", "hello world"):
        with pytest.raises(ExecutionContextError, match="invalid reference id"):
            _reference_ids(unsafe)


def test_preset_reference_fallback_uses_same_id_validation(tmp_path):
    project = _write_project(tmp_path / "project")
    (project / "spec_lock.md").write_text(
        SPEC_LOCK.replace("mode: custom", "mode: invalid id").replace("- mode_references: instructional, briefing\n", ""),
        encoding="utf-8",
    )
    result = PageContextResult(project_path=project, page="P01", context={}, inputs=())

    with pytest.raises(ExecutionContextError, match="invalid reference id"):
        from project_management.execution_context import _style_fragments

        _style_fragments(result, skill_dir=SKILL_DIR)


def test_execution_context_total_tokens_and_bytes_match_actual_rendered_file(tmp_path):
    from project_management.page_context import _token_counter

    project = _write_project(tmp_path / "project")
    payload = build_execution_context(_page_result(project, "P03"))
    rendered, _ = render_execution_context(payload)
    counter, status = _token_counter()

    assert status == "exact"
    assert payload["tokens"]["total"] == counter(rendered)
    assert len(rendered.encode("utf-8")) < 64000


def test_native_ready_accepts_fullwidth_colon_and_legacy_bare_yes(tmp_path):
    fullwidth = DESIGN_SPEC.replace("Native-ready**: industry-match-table=yes", "Native-ready**：industry-match-table = yes")
    bare = DESIGN_SPEC.replace("Native-ready**: industry-match-table=yes", "Native-ready**: yes")
    for design in (fullwidth, bare):
        project = _write_project(tmp_path / "project", design=design)

        payload = build_execution_context(_page_result(project, "P03"))

        assert payload["native_ready"] is True
        assert any(item["rule_id"].startswith("module.native_table") for item in payload["module_rules"])


def test_execution_context_temp_files_are_unique(tmp_path):
    import tempfile

    project = _write_project(tmp_path / "project")
    result = _page_result(project, "P01")
    names = []
    original = tempfile.NamedTemporaryFile

    def recording_named_temporary_file(*args, **kwargs):
        handle = original(*args, **kwargs)
        names.append(handle.name)
        return handle

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(tempfile, "NamedTemporaryFile", recording_named_temporary_file)
    try:
        write_execution_context(result)
        write_execution_context(result)
    finally:
        monkeypatch.undo()

    assert len(names) == 2
    assert len(set(names)) == 2


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        # Shapes the Strategist really wrote in the v2 spec: backticked entry + fullwidth commentary; 无 = undeclared.
        ("- **Native-ready**: `industry-background-fit-table=yes`（table/record_table 原生表格对象导出）", True),
        ("- **Native-ready**: 无（定性流程结构，不套目录模板）", None),
        ("- **Native-ready**: 无", None),
        ("- **Native-ready**: industry-match-table=yes (single table)", True),
        # Unbolded, indented shape from the paid P01 run's spec.
        ("  - Native-ready: industry-fit-table=yes", True),
        # Colon width, whitespace around "=", case, and the legacy bare yes/no.
        ("- **Native-ready**: industry-match-table=yes", True),
        ("- **Native-ready**：industry-match-table=yes", True),
        ("- **Native-ready**: industry-match-table = yes", True),
        ("- **Native-ready**: industry-match-table=YES", True),
        ("- **Native-ready**: YES", True),
        ("- **Native-ready:** industry-match-table=yes", True),
        ("- **Native-ready**: yes", True),
        ("- **Native-ready**: industry-match-table=no", False),
        ("- **Native-ready**：industry-match-table = no", False),
        ("- **Native-ready**: no", False),
        # One page can key several objects: any native-ready entry loads the native rules; only all-no is False.
        ("- **Native-ready**: table-a=no; table-b=yes", True),
        ("- **Native-ready**: table-a=yes; table-b=no", True),
        ("- **Native-ready**: table-a=no; table-b=no", False),
        ("- **Native-ready**: `table-a=no`；`table-b=yes`（第二张表可原生）", True),
        ("- **Native-ready**: `table-a=no`（含合并单元格；yes 不适用）", False),
        ("- **Native-ready**: table-a=no\n- **Native-ready**: table-b=yes", True),
        # Words that merely start with yes/no, and lines that are not a declaration.
        ("- **Native-ready**: none", None),
        ("- **Native-ready**: not decided", None),
        # A key that merely starts with yes/no must not be read as the decision; its value decides.
        ("- **Native-ready**: yes-table=maybe", None),
        ("- **Native-ready**: yes-table=no", False),
        ("- **Native-ready**: no-merge-table=yes", True),
        ("- **Native-ready**:", None),
        ("- **Core message**: industry-match-table=yes", None),
    ],
)
def test_native_ready_status_grammar(line, expected):
    assert _native_ready_status(line) is expected


def test_native_rules_load_when_any_keyed_table_on_the_page_is_native_ready(tmp_path):
    design = DESIGN_SPEC.replace(
        "Native-ready**: industry-match-table=yes",
        "Native-ready**: summary-table=no; industry-match-table=yes",
    )
    project = _write_project(tmp_path / "project", design=design)

    payload = build_execution_context(_page_result(project, "P03"))

    assert payload["native_ready"] is True
    rule_ids = {item["rule_id"] for item in payload["module_rules"]}
    assert {"module.native_table_selected_object", "module.native_table_schema"} <= rule_ids


def test_execution_context_total_that_cannot_settle_fails_closed(tmp_path, monkeypatch):
    project = _write_project(tmp_path / "project")
    result = _page_result(project, "P01")

    def flip_flop(text: str) -> int:
        # Never reaches a fixed point: the count depends on the very total it is about to be stored as.
        return 1 if '"total":0' in text else 0

    monkeypatch.setattr(execution_context_module, "_token_counter", lambda: (flip_flop, "exact"))

    with pytest.raises(ExecutionContextError, match="settle"):
        build_execution_context(result)


def _with_oversized_brief(result: PageContextResult) -> PageContextResult:
    page_context = {**result.context["page_context"], "brief_markdown": "word " * 16001}
    return replace(result, context={**result.context, "page_context": page_context})


def test_byte_budget_is_reported_before_the_single_read_limit(tmp_path):
    project = _write_project(tmp_path / "project")
    oversized = _with_oversized_brief(_page_result(project, "P01"))

    with pytest.raises(ExecutionContextError, match="byte budget"):
        write_execution_context(oversized, token_budget=10**9)

    assert not (project / "validation" / "execution-context" / "P01.json").exists()


def test_single_read_limit_still_rejects_an_oversized_field_when_total_budgets_allow_it(tmp_path, monkeypatch):
    project = _write_project(tmp_path / "project")
    oversized = _with_oversized_brief(_page_result(project, "P01"))
    monkeypatch.setattr(execution_context_module, "EXECUTION_CONTEXT_MAX_BYTES", 10**9)

    with pytest.raises(ExecutionContextError, match="field 'page_context' exceeds single-read limit 48000"):
        write_execution_context(oversized, token_budget=10**9)

    assert not (project / "validation" / "execution-context" / "P01.json").exists()
