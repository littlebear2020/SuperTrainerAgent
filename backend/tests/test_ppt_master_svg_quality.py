import importlib
import importlib.util
import sys
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = ROOT / "skills/public/ppt-master/scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
importlib.import_module("svg_quality")
SPEC = importlib.util.spec_from_file_location("svg_quality.checker", SCRIPTS_DIR / "svg_quality/checker.py")
checker_module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = checker_module
SPEC.loader.exec_module(checker_module)
SVGQualityChecker = checker_module.SVGQualityChecker
SVG_NS = "http://www.w3.org/2000/svg"


def parse_svg(body: str, *, view_box: str = "0 0 1280 720") -> ET.Element:
    return ET.fromstring(f'<svg xmlns="{SVG_NS}" viewBox="{view_box}">{body}</svg>')


def empty_result() -> dict:
    return {"errors": [], "warnings": [], "info": {}}


def sibling_findings(result: dict) -> list[str]:
    return [finding for finding in result["errors"] + result["warnings"] if "sibling text overlap" in finding]


def check_text_bounds(root: ET.Element) -> dict:
    result = empty_result()
    SVGQualityChecker()._check_text_bounds(root, result)
    return result


def measured_bounds(text: ET.Element, root: ET.Element) -> tuple[float, float, float, float]:
    parents = {id(child): parent for parent in root.iter() for child in list(parent)}
    sizes = checker_module._resolve_project_font_sizes(root)
    spacings = checker_module._resolve_project_letter_spacings(root, sizes)
    bounds = SVGQualityChecker._estimated_text_bounds(text, parents, sizes, spacings, include_headroom=False)
    assert bounds is not None
    return bounds


def coffee_svg(signal_y: float = 160) -> ET.Element:
    return parse_svg(
        f"""<g id="coffee-card" data-pptx-bounds="80 80 400 260">
        <text id="brew" x="100" y="120" font-size="24" data-pptx-frame="100 90 200 55">
          <tspan x="100" y="120">再分段</tspan><tspan x="100" y="144">注水</tspan>
        </text>
        <text id="signal" x="100" y="{signal_y}" font-size="20" data-pptx-frame="100 150 200 40">偏差信号</text>
        </g>"""
    )


def test_coffee_sibling_text_overlap_is_detected_and_fixed_layout_is_clear():
    root = coffee_svg()
    texts = list(root.iter(f"{{{SVG_NS}}}text"))
    first_bounds, second_bounds = (measured_bounds(text, root) for text in texts)
    assert SVGQualityChecker._bounds_overlap_dimensions(first_bounds, second_bounds)[1] > 1
    declared_frames = [tuple(float(value) for value in text.get("data-pptx-frame").split()) for text in texts]
    first_frame, second_frame = declared_frames
    assert first_frame[1] + first_frame[3] <= second_frame[1]

    result = check_text_bounds(root)
    findings = sibling_findings(result)
    assert len(findings) == 1
    assert findings[0] in result["warnings"]
    assert "brew" in findings[0] and "signal" in findings[0]
    assert "9.4px" in findings[0]

    assert sibling_findings(check_text_bounds(coffee_svg(signal_y=190))) == []


def test_sibling_overlap_ignores_internal_tspans_normal_cards_and_other_modules():
    root = parse_svg(
        """<g id="paragraph" data-pptx-bounds="80 80 300 120">
        <text x="100" y="120" font-size="24"><tspan x="100" y="120">同段第一行</tspan><tspan x="100" y="122">同段第二行</tspan></text>
        </g>
        <g id="normal-card" data-pptx-bounds="420 80 300 180">
        <text x="440" y="120" font-size="24">正常卡片 Step 7</text>
        <text x="440" y="170" font-size="20">Adjacent line 42</text>
        </g>
        <g id="other-card-a" data-pptx-bounds="760 80 200 100"><text x="780" y="150" font-size="24">跨模块甲</text></g>
        <g id="other-card-b" data-pptx-bounds="1000 80 200 100"><text x="780" y="140" font-size="24">跨模块乙</text></g>"""
    )

    assert sibling_findings(check_text_bounds(root)) == []


@pytest.mark.parametrize("overlap", [0.0, 0.5, 1.0], ids=["edge-contact", "within-tolerance", "at-tolerance"])
def test_sibling_overlap_allows_edge_contact_and_existing_tolerance(overlap):
    root = parse_svg(
        """<g id="tolerance-card" data-pptx-bounds="80 80 300 180">
        <text x="100" y="120" font-size="24">第一行</text>
        <text x="100" y="170" font-size="20">Second line</text>
        </g>"""
    )
    first, second = list(root.iter(f"{{{SVG_NS}}}text"))
    first_bounds = measured_bounds(first, root)
    second_bounds = measured_bounds(second, root)
    adjusted_y = float(second.get("y")) + first_bounds[3] - overlap - second_bounds[1]
    second.set("y", str(adjusted_y))

    assert sibling_findings(check_text_bounds(root)) == []


@pytest.mark.parametrize("kind", ["hidden", "zero-opacity", "non-visual", "off-canvas-morph"])
def test_sibling_overlap_preserves_visibility_and_morph_exclusions(kind):
    if kind == "off-canvas-morph":
        body = """<g id="staged" data-pptx-bounds="1400 80 400 260" data-pptx-morph-staging="true">
        <text x="1420" y="120" font-size="24"><tspan x="1420" y="120">再分段</tspan><tspan x="1420" y="144">注水</tspan></text>
        <text x="1420" y="160" font-size="20">偏差信号</text></g>"""
    else:
        attribute = {"hidden": 'display="none"', "zero-opacity": 'opacity="0"', "non-visual": ""}[kind]
        wrapper_start = "<defs>" if kind == "non-visual" else ""
        wrapper_end = "</defs>" if kind == "non-visual" else ""
        body = f"""<g id="excluded" data-pptx-bounds="80 80 400 260" {attribute}>{wrapper_start}
        <text x="100" y="120" font-size="24"><tspan x="100" y="120">再分段</tspan><tspan x="100" y="144">注水</tspan></text>
        <text x="100" y="160" font-size="20">偏差信号</text>{wrapper_end}</g>"""

    assert sibling_findings(check_text_bounds(parse_svg(body))) == []
