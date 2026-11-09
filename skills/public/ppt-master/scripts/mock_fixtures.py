"""Deterministic mock fixtures for ppt-master frontend dev / CI.

Frontend workspace development and regression suites must NOT depend on real
agent generation (slow / expensive / non-deterministic). This module produces
deterministic "generation-completed" and "generation-failed" fixtures: a
sample project (spec_lock + N hand-written-style SVG pages with
data-element-id) + run-record-shaped dicts.

Not used in production runs.
"""

from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape

_CANVAS = "0 0 1280 720"
_ACCENT = "#0D9488"

_PAGE_TEMPLATE = """<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="{canvas}">
  <rect data-element-id="p{n}-bg" width="1280" height="720" fill="#FFFFFF"/>
  <rect data-element-id="p{n}-bar" width="16" height="720" fill="{accent}"/>
  <text data-element-id="p{n}-title" x="120" y="360" font-size="56" font-family="PingFang SC, Arial, sans-serif" fill="#0F172A">{title}</text>
  <text data-element-id="p{n}-sub" x="124" y="420" font-size="22" fill="#475569">{subtitle} page {n}</text>
</svg>
"""

_SPEC_LOCK = """# spec_lock (mock fixture)
canvas: 1280x720
accent: {accent}
font: PingFang SC, Arial, sans-serif
"""


def build_project_fixture(project_dir: Path, pages: int = 3, title: str = "mock fixture") -> Path:
    project_dir = Path(project_dir)
    svg_dir = project_dir / "svg_output"
    svg_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "spec_lock.md").write_text(_SPEC_LOCK.format(accent=_ACCENT), encoding="utf-8")
    # K5（2026-09-11 修复）：title/subtitle 经 XML 转义，含 & < > 产出合法 SVG
    for n in range(1, pages + 1):
        (svg_dir / f"page_{n}.svg").write_text(
            _PAGE_TEMPLATE.format(canvas=_CANVAS, n=n, title=escape(title), subtitle=escape("mock fixture"), accent=_ACCENT),
            encoding="utf-8",
        )
    return project_dir


def completed_run_record(project_dir: Path) -> dict:
    project_dir = Path(project_dir)
    pages = len(list((project_dir / "svg_output").glob("*.svg")))
    return {
        "status": "completed",
        "project_dir": str(project_dir),
        "pages": pages,
        "output": str(project_dir / "output.pptx"),
    }


def failed_run_record(error: str) -> dict:
    return {"status": "failed", "error": error, "pages": 0}
