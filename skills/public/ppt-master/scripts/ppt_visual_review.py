"""Per-page visual/quality review wrapper.

Wraps the vendored svg_quality_checker.SVGQualityChecker.check_file across a
project's svg_output/ pages into a structured list + summary that a workspace
visual-review panel can render. Pure + filesystem-bound so it is unit-testable
against fixture SVGs.
"""

from __future__ import annotations

from pathlib import Path


def _load_checker():
    try:
        import sys

        # 技能目录不是 Python 包；按文件位置把 scripts/ 加进 sys.path 再导入
        scripts_dir = str(Path(__file__).resolve().parent)
        if scripts_dir not in sys.path:  # K3（2026-09-11 修复）：防重复路径累积
            sys.path.insert(0, scripts_dir)
        from svg_quality_checker import SVGQualityChecker
    except ImportError as e:
        raise RuntimeError("svg_quality_checker unavailable (deps missing)") from e
    return SVGQualityChecker()


def review_project(project_dir: Path) -> dict:
    """Run check_file on every svg_output/*.svg page; return per-page results."""
    svg_out = Path(project_dir) / "svg_output"
    if not svg_out.is_dir():
        raise ValueError(f"svg_output not found under {project_dir}")
    checker = _load_checker()
    pages: list[dict] = []
    for svg in sorted(svg_out.glob("*.svg")):
        try:
            page_index = int(svg.stem.rsplit("_", 1)[-1])
        except ValueError:
            page_index = len(pages) + 1
        # v2.10.0 实测接口：check_file(svg_file: str, expected_format=None) -> Dict
        # 返回 {"errors": [...], "warnings": [...], "passed": bool, ...}
        report = checker.check_file(str(svg))
        errors = [str(e) for e in report.get("errors", [])]
        warnings = [str(w) for w in report.get("warnings", [])]
        pages.append(
            {
                "page_index": page_index,
                "passed": bool(report.get("passed", not errors)),
                "errors": errors,
                "warnings": warnings,
            }
        )
    return {
        "pages": pages,
        "summary": {
            "total": len(pages),
            "passed": sum(1 for p in pages if p["passed"]),
            "failed": sum(1 for p in pages if not p["passed"]),
        },
    }
