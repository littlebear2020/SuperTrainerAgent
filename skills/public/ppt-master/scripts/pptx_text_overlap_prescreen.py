#!/usr/bin/env python3
"""Detect overlapping text lines in PPTX exports using LibreOffice and Poppler."""

import argparse
import json
import math
import re
import subprocess
import sys
import tempfile
from contextlib import ExitStack
from itertools import combinations
from pathlib import Path
from xml.etree import ElementTree as ET

SCHEMA_VERSION = "pptx-text-overlap-prescreen.v1"
DECORATIVE_NUMBER_RE = re.compile(r"[0-9]{1,2}")
COMMAND_TIMEOUT_SECONDS = 120


class PrescreenError(RuntimeError):
    pass


def _local_name(element: ET.Element) -> str:
    return element.tag.rsplit("}", 1)[-1]


def _line_text(line: ET.Element) -> str:
    words = ["".join(word.itertext()).strip() for word in list(line) if _local_name(word) == "word"]
    text = " ".join(word for word in words if word) if words else "".join(line.itertext())
    return re.sub(r"\s+", " ", text).strip()


def _line_record(line: ET.Element, *, block_id: str, line_id: str) -> dict | None:
    text = _line_text(line)
    if not text:
        return None
    try:
        bbox = [float(line.get(name, "")) for name in ("xMin", "yMin", "xMax", "yMax")]
    except ValueError as exc:
        raise PrescreenError(f"Invalid bbox-layout line coordinates for {line_id}") from exc
    if not all(math.isfinite(value) for value in bbox) or bbox[2] < bbox[0] or bbox[3] < bbox[1]:
        raise PrescreenError(f"Invalid bbox-layout line bounds for {line_id}: {bbox}")
    return {"block_id": block_id, "line_id": line_id, "text": text, "bbox": bbox}


def _intersection(first: list[float], second: list[float]) -> tuple[float, float]:
    return (
        max(0.0, min(first[2], second[2]) - max(first[0], second[0])),
        max(0.0, min(first[3], second[3]) - max(first[1], second[1])),
    )


def analyze_bbox_layout(bbox_path: str | Path) -> dict:
    path = Path(bbox_path)
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError) as exc:
        raise PrescreenError(f"Unable to parse pdftotext bbox-layout XML {path}: {exc}") from exc

    page_elements = [element for element in root.iter() if _local_name(element) == "page"]
    if not page_elements:
        raise PrescreenError(f"pdftotext bbox-layout XML has no pages: {path}")

    pages = []
    findings = []
    line_count = 0
    for page_number, page in enumerate(page_elements, 1):
        page_lines = []
        flows = [child for child in list(page) if _local_name(child) == "flow"] or [page]
        for flow_index, flow in enumerate(flows, 1):
            blocks = [child for child in list(flow) if _local_name(child) == "block"]
            for block_index, block in enumerate(blocks, 1):
                block_id = f"p{page_number}-f{flow_index}-b{block_index}"
                lines = [child for child in list(block) if _local_name(child) == "line"]
                for line_index, line in enumerate(lines, 1):
                    record = _line_record(line, block_id=block_id, line_id=f"{block_id}-l{line_index}")
                    if record is not None:
                        page_lines.append(record)
        line_count += len(page_lines)
        page_findings = []
        for first, second in combinations(page_lines, 2):
            width, height = _intersection(first["bbox"], second["bbox"])
            if width <= 0 or height <= 0:
                continue
            decorative = any(DECORATIVE_NUMBER_RE.fullmatch(line["text"].strip()) for line in (first, second))
            finding = {
                "page": page_number,
                "same_block": first["block_id"] == second["block_id"],
                "lines": [first, second],
                "intersection_width": round(width, 3),
                "intersection_height": round(height, 3),
                "classification": "decorative_number_manual_review" if decorative else "blocking_text_overlap",
                "disposition": "manual_page_review" if decorative else "block_delivery",
            }
            page_findings.append(finding)
        findings.extend(page_findings)
        blocking_count = sum(finding["disposition"] == "block_delivery" for finding in page_findings)
        manual_review_count = sum(finding["disposition"] == "manual_page_review" for finding in page_findings)
        pages.append(
            {
                "page": page_number,
                "line_count": len(page_lines),
                "blocking_count": blocking_count,
                "manual_review_count": manual_review_count,
                "requires_manual_review": manual_review_count > 0,
            }
        )

    blocking_count = sum(finding["disposition"] == "block_delivery" for finding in findings)
    manual_review_count = sum(finding["disposition"] == "manual_page_review" for finding in findings)
    status = "failed" if blocking_count else "passed-with-manual-review" if manual_review_count else "passed"
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "passed": blocking_count == 0,
        "requires_manual_review": manual_review_count > 0,
        "page_count": len(pages),
        "line_count": line_count,
        "blocking_count": blocking_count,
        "manual_review_count": manual_review_count,
        "pages": pages,
        "findings": findings,
    }


def _run_command(command: list[str], *, tool: str) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=COMMAND_TIMEOUT_SECONDS, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PrescreenError(f"{tool} could not complete: {exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise PrescreenError(f"{tool} exited {result.returncode}: {detail}")
    return result


def prescreen_pptx(
    pptx_path: str | Path,
    *,
    soffice: str = "soffice",
    pdftotext: str = "pdftotext",
    work_dir: str | Path | None = None,
) -> dict:
    pptx = Path(pptx_path).resolve()
    if not pptx.is_file():
        raise PrescreenError(f"PPTX input is missing: {pptx}")

    with ExitStack() as stack:
        if work_dir is None:
            directory = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="pptx-text-overlap-")))
        else:
            directory = Path(work_dir).resolve()
            directory.mkdir(parents=True, exist_ok=True)
        profile = directory / "libreoffice-profile"
        profile.mkdir(exist_ok=True)
        _run_command(
            [
                soffice,
                f"-env:UserInstallation={profile.as_uri()}",
                "--headless",
                "--convert-to",
                "pdf",
                "--outdir",
                str(directory),
                str(pptx),
            ],
            tool="soffice",
        )
        pdf = directory / f"{pptx.stem}.pdf"
        if not pdf.is_file() or pdf.stat().st_size == 0:
            raise PrescreenError(f"soffice did not produce a non-empty PDF: {pdf}")
        bbox = directory / "bbox-layout.html"
        _run_command([pdftotext, "-bbox-layout", str(pdf), str(bbox)], tool="pdftotext")
        if not bbox.is_file() or bbox.stat().st_size == 0:
            raise PrescreenError(f"pdftotext did not produce non-empty bbox-layout XML: {bbox}")
        report = analyze_bbox_layout(bbox)
        report["source_pptx"] = str(pptx)
        if work_dir is not None:
            report["artifacts"] = {"pdf": str(pdf), "bbox_layout": str(bbox)}
        return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pptx", help="PPTX export to convert and inspect")
    args = parser.parse_args(argv)
    try:
        report = prescreen_pptx(args.pptx)
    except PrescreenError as exc:
        print(json.dumps({"schema_version": SCHEMA_VERSION, "status": "error", "passed": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        print(f"PPTX_TEXT_OVERLAP_PRESCREEN_ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
