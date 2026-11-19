#!/usr/bin/env python3
"""Deterministic per-page execution-context projection."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .page_context import (
    PageContextResult,
    _compact_json,
    _file_sha256,
    _input_location,
    _section_fields,
    _section_index,
    _token_counter,
)
from .paths import SKILL_DIR
from .project_specs import parse_spec_lock_artifact

EXECUTION_CONTEXT_SCHEMA = "ppt-master.execution-context.v1"
EXECUTION_CONTEXT_TOKEN_BUDGET = 16000
EXECUTION_CONTEXT_MAX_BYTES = 64000


class ExecutionContextError(RuntimeError):
    """Reject an invalid, unsupported, stale, or over-budget execution view."""


@dataclass(frozen=True)
class RuleSelector:
    rule_id: str
    source_path: str
    start_anchor: str
    end_anchor: str | None = None


def _selector(rule_id: str, source_path: str, start: str, end: str | None = None) -> RuleSelector:
    return RuleSelector(rule_id, source_path, start, end)


_CORE_SELECTORS = (
    _selector("core.blueprint_binding", "references/executor-base.md", "**Hard rule — binding selection vs realization**", "**Reference — planning sketches are adjusted freely**"),
    _selector("core.content_expression", "references/executor-base.md", "**Hard rule — content vs expression**", "**Execution anchors and contextual values**"),
    _selector("core.per_page_decisions", "references/executor-base.md", "### 2.2 Per-page decision chain", "## 3. Execution Guidelines"),
    _selector("core.flat_structure", "references/executor-base.md", "**Hard rule — Shape-first page authority**", "**Style workspace under flat structure**"),
    _selector("core.file_naming", "references/executor-base.md", "### SVG File Naming Convention", "## 4. Icon Usage"),
    _selector("core.xml_foundation", "references/shared-standards-core.md", "### 1.0 Text characters: must be well-formed XML", "### 1.1 Line-end Markers (Conditional Contract)"),
    _selector("core.page_closure", "references/shared-standards-core.md", "### 4.0 Complete Page-Design Contract", "### 4.1 Semantic SVG Marker Contract"),
    _selector("core.canvas_flat", "references/shared-standards-core.md", "### 4.1 Semantic SVG Marker Contract", "### 4.2 Editability, Package Promotion, and Text Leading"),
    _selector("core.text_frames", "references/shared-standards-core.md", "| One editable PPT text frame with mixed formatting or multiline prose |", "| Stable object grouping or object-level animation anchor |"),
    _selector("core.text_leading", "references/shared-standards-core.md", "**Default — leading by role and density", "**Hard rule — supported shape conversion**"),
    _selector("core.root_grouping", "references/shared-standards-core.md", "**Hard rule — root groups protect body-text layout**", "Wrap each logical Slide-local body unit"),
    _selector("core.grouping_units", "references/shared-standards-core.md", "Wrap each logical Slide-local body unit", "**Structural atoms and slots are excluded automatically.**"),
    _selector("core.grouping_forbidden", "references/shared-standards-core.md", "**Forbidden**:\n\n- One giant", '```xml\n<g id="card-benefits-1"'),
    _selector("core.preset_boundaries", "references/preset-shape-vocabulary.md", "**Hard rule — registered identities and behavior boundaries**", "**When to run**"),
)


def _source_path(selector: RuleSelector, skill_dir: Path) -> Path:
    root = skill_dir.resolve()
    path = (root / selector.source_path).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ExecutionContextError(f"{selector.rule_id}: source_path escapes Skill") from exc
    if not path.is_file():
        raise ExecutionContextError(f"{selector.rule_id}: source not found: {selector.source_path}")
    return path


def _unique_position(text: str, anchor: str, *, selector: RuleSelector, kind: str) -> int:
    count = text.count(anchor)
    if count != 1:
        raise ExecutionContextError(f"{selector.rule_id}: {kind} must occur exactly once, found {count}: {anchor!r}")
    return text.index(anchor)


def extract_rule_fragment(selector: RuleSelector, *, skill_dir: Path = SKILL_DIR) -> dict[str, str]:
    """Extract one exact source fragment between unique anchors."""
    path = _source_path(selector, skill_dir)
    source = path.read_text(encoding="utf-8")
    start = _unique_position(source, selector.start_anchor, selector=selector, kind="start_anchor")
    if selector.end_anchor is None:
        end = len(source)
    else:
        end = _unique_position(source, selector.end_anchor, selector=selector, kind="end_anchor")
    if end < start + len(selector.start_anchor):
        raise ExecutionContextError(f"{selector.rule_id}: end_anchor must not overlap or precede start_anchor")
    anchor = selector.start_anchor if selector.end_anchor is None else f"{selector.start_anchor} → {selector.end_anchor}"
    return {
        "rule_id": selector.rule_id,
        "source_path": selector.source_path,
        "source_sha256": _file_sha256(path),
        "source_anchor": anchor,
        "text": source[start:end],
    }


def extract_preset_names(*, skill_dir: Path = SKILL_DIR) -> tuple[str, ...]:
    """Project exact backtick-quoted names from the preset table rows."""
    path = _source_path(RuleSelector("core.preset_names", "references/preset-shape-vocabulary.md", ""), skill_dir)
    names: list[str] = []
    seen: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.lstrip().startswith("|"):
            continue
        for name in re.findall(r"`([^`]+)`", line):
            if name not in seen:
                seen.add(name)
                names.append(name)
    if len(names) != 187:
        raise ExecutionContextError(f"core.preset_names: expected 187 names, found {len(names)}")
    return tuple(names)


def _whole_file_fragment(rule_id: str, source_path: str, *, skill_dir: Path) -> dict[str, str]:
    selector = RuleSelector(rule_id, source_path, "")
    path = _source_path(selector, skill_dir)
    return {
        "rule_id": rule_id,
        "source_path": source_path,
        "source_sha256": _file_sha256(path),
        "source_anchor": "whole-file",
        "text": path.read_text(encoding="utf-8"),
    }


def _preset_name_fragment(*, skill_dir: Path) -> dict[str, str]:
    path = _source_path(RuleSelector("core.preset_names", "references/preset-shape-vocabulary.md", ""), skill_dir)
    return {
        "rule_id": "core.preset_names",
        "source_path": "references/preset-shape-vocabulary.md",
        "source_sha256": _file_sha256(path),
        "source_anchor": "exact-preset-name-cells",
        "text": "\n".join(extract_preset_names(skill_dir=skill_dir)) + "\n",
    }


def _lock_fields(result: PageContextResult, heading: str) -> dict[str, str]:
    sections = _section_index(parse_spec_lock_artifact(result.project_path / "spec_lock.md", report_duplicate_fields=True))
    return _section_fields(sections, heading)


def _reference_ids(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    ids = tuple(item.strip() for item in value.split(",") if item.strip())
    for ref_id in ids:
        if re.fullmatch(r"[a-z0-9-]+", ref_id) is None:
            raise ExecutionContextError(f"invalid reference id: {ref_id!r}")
    if len(ids) != len(set(ids)):
        raise ExecutionContextError(f"duplicate reference ids: {value}")
    return ids


def _style_fragments(result: PageContextResult, *, skill_dir: Path) -> list[dict[str, str]]:
    mode_fields = _lock_fields(result, "mode")
    style_fields = _lock_fields(result, "visual_style")
    mode_ids = _reference_ids(mode_fields.get("mode_references"))
    if not mode_ids and mode_fields.get("mode") not in (None, "", "custom"):
        mode_ids = _reference_ids(mode_fields.get("mode"))
    style_ids = _reference_ids(style_fields.get("visual_style_references"))
    if not style_ids and style_fields.get("visual_style") not in (None, "", "custom"):
        style_ids = _reference_ids(style_fields.get("visual_style"))
    fragments: list[dict[str, str]] = []
    for mode_id in mode_ids:
        fragments.append(
            extract_rule_fragment(
                RuleSelector(
                    f"module.mode.{mode_id}",
                    f"references/modes/{mode_id}.md",
                    "## 1. Narrative skeleton",
                    "## 2. Page-structure tendencies",
                ),
                skill_dir=skill_dir,
            )
        )
    for style_id in style_ids:
        fragments.append(
            extract_rule_fragment(
                RuleSelector(
                    f"module.style.{style_id}",
                    f"references/visual-styles/{style_id}.md",
                    "## 1. Shape & decoration",
                    "## 2. Typography character",
                ),
                skill_dir=skill_dir,
            )
        )
    return fragments


def _native_table_fragments(*, skill_dir: Path) -> list[dict[str, str]]:
    source = "references/native-data-interface.md"
    selectors = (
        _selector("module.native_table_selected_object", source, "**Hard rule — selected-object authoring**", "**Hard rule — activation is the opt-in**"),
        _selector("module.native_table_activation", source, "**Hard rule — activation is the opt-in**", "**Metadata placement and bounds**"),
        _selector("module.native_table_bounds", source, "**Metadata placement and bounds**", '```xml\n<g id="p03-revenue-chart"'),
        _selector("module.native_table_projection", source, "**Hard rule — project by the selected authority**", "### Table schema"),
        _selector("module.native_table_schema", source, "### Table schema", "### Chart schemas"),
    )
    return [extract_rule_fragment(selector, skill_dir=skill_dir) for selector in selectors]


def _select_core_and_modules(result: PageContextResult, *, skill_dir: Path) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    route = dict(result.context.get("global", {}).get("pptx_structure", {}))
    if route.get("mode") != "flat":
        raise ExecutionContextError(f"execution-context supports only flat routes: {route}")
    core = [extract_rule_fragment(selector, skill_dir=skill_dir) for selector in _CORE_SELECTORS]
    core.append(_preset_name_fragment(skill_dir=skill_dir))
    page = dict(result.context.get("page_context", {}))
    modules = _style_fragments(result, skill_dir=skill_dir)
    if page.get("images"):
        modules.append(_whole_file_fragment("module.image", "references/executor-image.md", skill_dir=skill_dir))
    visualization = page.get("visualization")
    if visualization:
        modules.append(
            extract_rule_fragment(
                RuleSelector(
                    "module.visualization_reference",
                    "references/executor-visualization.md",
                    "**Hard rule — reference, not lock**",
                ),
                skill_dir=skill_dir,
            )
        )
    if isinstance(visualization, str) and visualization.startswith("table/"):
        modules.append(_whole_file_fragment("module.table", "references/executor-table.md", skill_dir=skill_dir))
        brief = str(page.get("brief_markdown", ""))
        if re.search(r"Native-ready\*{0,2}\s*:\s*[^\n;=]+=yes\b", brief, flags=re.IGNORECASE):
            modules.extend(_native_table_fragments(skill_dir=skill_dir))
    return core, modules


def select_rule_fragments(result: PageContextResult) -> tuple[dict[str, str], ...]:
    """Select exact core and current-page module fragments for a flat page."""
    core, modules = _select_core_and_modules(result, skill_dir=SKILL_DIR)
    return (*core, *modules)


def _token_count(counter, payload: object) -> int:
    return counter(_compact_json(payload))


def build_execution_context(result: PageContextResult, *, token_budget: int = EXECUTION_CONTEXT_TOKEN_BUDGET) -> dict[str, object]:
    """Build one measured, hashed execution-context payload without disk writes."""
    counter, status = _token_counter()
    if counter is None or status != "exact":
        raise ExecutionContextError(f"token_status={status}: exact o200k_base counting is required")
    core, modules = _select_core_and_modules(result, skill_dir=SKILL_DIR)
    route = dict(result.context["global"]["pptx_structure"])
    payload: dict[str, object] = {
        "schema": EXECUTION_CONTEXT_SCHEMA,
        "page": result.page,
        "route": route,
        "global": result.context["global"],
        "page_context": result.context["page_context"],
        "reference_set": result.context.get("reference_set", []),
        "core_rules": core,
        "module_rules": modules,
        "token_status": "exact",
        "token_budget": token_budget,
    }
    input_paths = list(result.inputs)
    for fragment in (*core, *modules):
        input_paths.append((SKILL_DIR / fragment["source_path"]).resolve())
    inputs = []
    for path in dict.fromkeys(input_paths):
        scope, relative = _input_location(result.project_path, path)
        inputs.append({"scope": scope, "path": relative, "sha256": _file_sha256(path)})
    payload["inputs"] = inputs
    tokens = {
        "page_projection": _token_count(counter, {"global": payload["global"], "page_context": payload["page_context"]}),
        "core_rules": _token_count(counter, core),
        "module_rules": _token_count(counter, modules),
    }
    payload["tokens"] = tokens
    tokens["total"] = _token_count(counter, payload)
    if tokens["total"] > token_budget:
        raise ExecutionContextError(f"execution-context token budget exceeded: {tokens} budget={token_budget}")
    payload["context_sha256"] = _file_sha256_from_text(_compact_json(payload))
    byte_count = len(_compact_json(payload).encode("utf-8"))
    if byte_count >= EXECUTION_CONTEXT_MAX_BYTES:
        raise ExecutionContextError(f"execution-context byte budget exceeded: {byte_count} >= {EXECUTION_CONTEXT_MAX_BYTES}")
    return payload


def _file_sha256_from_text(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def render_execution_context(payload: dict[str, object], *, max_read_bytes: int = 48000) -> tuple[str, tuple[tuple[int, int], ...]]:
    """Render valid JSON with bounded top-level lines and read ranges."""
    if max_read_bytes <= 0:
        raise ExecutionContextError("max_read_bytes must be positive")
    lines = ["{\n"]
    items = list(payload.items())
    for index, (key, value) in enumerate(items):
        suffix = ",\n" if index + 1 < len(items) else "\n"
        line = f"{json.dumps(key, ensure_ascii=False)}:{_compact_json(value).rstrip()}{suffix}"
        if len(line.encode("utf-8")) >= max_read_bytes:
            raise ExecutionContextError(f"field {key!r} exceeds single-read limit {max_read_bytes}")
        lines.append(line)
    lines.append("}\n")

    ranges: list[tuple[int, int]] = []
    start = 0
    current_bytes = 0
    for index, line in enumerate(lines):
        line_bytes = len(line.encode("utf-8"))
        if current_bytes and current_bytes + line_bytes >= max_read_bytes:
            ranges.append((start + 1, index))
            start = index
            current_bytes = 0
        current_bytes += line_bytes
    ranges.append((start + 1, len(lines)))
    if len(ranges) > 2:
        raise ExecutionContextError("execution-context requires at most two read_ranges")
    return "".join(lines), tuple(ranges)


def write_execution_context(result: PageContextResult, *, token_budget: int = EXECUTION_CONTEXT_TOKEN_BUDGET) -> tuple[Path, dict[str, object]]:
    """Atomically write one fresh execution-context artifact."""
    payload = build_execution_context(result, token_budget=token_budget)
    rendered, _ranges = render_execution_context(payload)
    destination = result.project_path / "validation" / "execution-context" / f"{result.page}.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".json.tmp")
    try:
        temporary.write_text(rendered, encoding="utf-8")
        temporary.replace(destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return destination, payload


def validate_execution_context(payload: dict[str, object], result: PageContextResult) -> None:
    """Reject a manually changed or stale execution-context payload."""
    unsigned = {key: value for key, value in payload.items() if key != "context_sha256"}
    actual = _file_sha256_from_text(_compact_json(unsigned))
    if actual != payload.get("context_sha256"):
        raise ExecutionContextError("execution-context changed: context_sha256 mismatch")
    expected = build_execution_context(result)
    if actual != expected["context_sha256"]:
        raise ExecutionContextError("execution-context stale: context_sha256 mismatch")
