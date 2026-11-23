import html
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "skills/public/ppt-master/scripts/pptx_text_overlap_prescreen.py"


def load_prescreen():
    if not MODULE_PATH.is_file():
        pytest.fail(f"pptx text overlap prescreen is missing: {MODULE_PATH}", pytrace=False)
    spec = importlib.util.spec_from_file_location("pptx_text_overlap_prescreen", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def write_bbox(tmp_path, lines, *, page=1):
    pages = []
    for page_number in range(1, page + 1):
        blocks = {}
        if page_number == page:
            for text, left, top, right, bottom, block in lines:
                blocks.setdefault(block, []).append((text, left, top, right, bottom))
        block_xml = []
        for block_number, block_lines in enumerate(blocks.values(), 1):
            line_xml = []
            for text, left, top, right, bottom in block_lines:
                line_xml.append(f'<line xMin="{left}" yMin="{top}" xMax="{right}" yMax="{bottom}"><word>{html.escape(text)}</word></line>')
            block_xml.append(f'<block xMin="0" yMin="0" xMax="500" yMax="500">{"".join(line_xml)}</block>')
        pages.append(f'<page width="500" height="500"><flow id="f1">{"".join(block_xml)}</flow></page>')
    path = tmp_path / "bbox-layout.html"
    path.write_text(f'<?xml version="1.0"?><doc>{"".join(pages)}</doc>', encoding="utf-8")
    return path


def test_same_block_coffee_overlap_blocks_delivery(tmp_path):
    module = load_prescreen()
    bbox = write_bbox(
        tmp_path,
        [
            ("再分段注水", 100, 100, 180, 120, 1),
            ("偏差信号", 100, 113, 180, 133, 1),
        ],
        page=2,
    )

    report = module.analyze_bbox_layout(bbox)

    assert report["passed"] is False
    assert report["blocking_count"] == 1
    assert report["manual_review_count"] == 0
    finding = report["findings"][0]
    assert finding["page"] == 2
    assert finding["same_block"] is True
    assert finding["intersection_width"] == 80.0
    assert finding["intersection_height"] == 7.0
    assert finding["disposition"] == "block_delivery"
    assert finding["lines"][0]["block_id"] == finding["lines"][1]["block_id"]
    assert finding["lines"][0]["line_id"] != finding["lines"][1]["line_id"]
    assert report["pages"][0]["page"] == 1
    assert report["pages"][1]["blocking_count"] == 1


@pytest.mark.parametrize("number", ["7", "42"])
def test_one_or_two_digit_overlap_requires_page_review_without_hard_failure(tmp_path, number):
    module = load_prescreen()
    bbox = write_bbox(tmp_path, [(f"  {number}  ", 100, 100, 180, 130, 1), ("背景", 100, 120, 180, 150, 1)])

    report = module.analyze_bbox_layout(bbox)

    assert report["passed"] is True
    assert report["status"] == "passed-with-manual-review"
    assert report["blocking_count"] == 0
    assert report["manual_review_count"] == 1
    assert report["pages"][0]["requires_manual_review"] is True
    assert report["findings"][0]["classification"] == "decorative_number_manual_review"
    assert report["findings"][0]["disposition"] == "manual_page_review"


def test_three_digits_and_short_labels_do_not_use_decorative_exception(tmp_path):
    module = load_prescreen()
    bbox = write_bbox(
        tmp_path,
        [
            ("123", 100, 100, 180, 130, 1),
            ("再分段注水", 100, 110, 180, 140, 1),
            ("偏差信号", 100, 120, 180, 150, 1),
        ],
    )

    report = module.analyze_bbox_layout(bbox)

    assert report["passed"] is False
    assert report["blocking_count"] == 3
    assert report["manual_review_count"] == 0
    assert {finding["classification"] for finding in report["findings"]} == {"blocking_text_overlap"}


def test_separated_lines_pass(tmp_path):
    module = load_prescreen()
    bbox = write_bbox(tmp_path, [("第一行", 100, 100, 180, 120, 1), ("Second line", 100, 140, 220, 160, 1)])

    report = module.analyze_bbox_layout(bbox)

    assert report["passed"] is True
    assert report["status"] == "passed"
    assert report["findings"] == []
    assert report["line_count"] == 2


def test_poppler_xhtml_bbox_document_is_supported(tmp_path):
    module = load_prescreen()
    bbox = write_bbox(
        tmp_path,
        [
            ("再分段注水", 100, 100, 180, 120, 1),
            ("偏差信号", 100, 113, 180, 133, 1),
        ],
    )
    document = bbox.read_text(encoding="utf-8").replace('<?xml version="1.0"?>', "")
    bbox.write_text(f'<html xmlns="http://www.w3.org/1999/xhtml"><head><title></title></head><body>{document}</body></html>', encoding="utf-8")

    report = module.analyze_bbox_layout(bbox)

    assert report["blocking_count"] == 1
    assert report["findings"][0]["same_block"] is True


def test_malformed_bbox_fails_loudly(tmp_path):
    module = load_prescreen()
    bbox = tmp_path / "broken.html"
    bbox.write_text("<doc><page>", encoding="utf-8")

    with pytest.raises(module.PrescreenError, match="bbox-layout|XML"):
        module.analyze_bbox_layout(bbox)


def test_missing_soffice_fails_loudly(tmp_path):
    module = load_prescreen()
    pptx = tmp_path / "sample.pptx"
    pptx.write_bytes(b"controlled fixture")

    with pytest.raises(module.PrescreenError, match="soffice"):
        module.prescreen_pptx(pptx, soffice=str(tmp_path / "missing-soffice"), work_dir=tmp_path / "conversion")
