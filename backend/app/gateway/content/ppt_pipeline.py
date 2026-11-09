"""PPT generation pipeline — converts Markdown or TipTap doc JSON to .pptx via python-pptx.

Runs in Gateway via ``asyncio.to_thread`` (pure Python, no ffmpeg needed).
Called by ``ContentTaskService._run_ppt_gen()``.

Supports two input formats:
1. Markdown text — parsed by state-machine into slides
2. TipTap ProseMirror JSON — parsed from node structure

Slide types → Built-in Layout mapping:
  - "title"    → Layout 0 Title Slide     (or Layout 1 if has content)
  - "section"  → Layout 2 Section Header   (or Layout 1 if has content)
  - "content"  → Layout 1 Title and Content
  - "code"     → Layout 5 Title Only       (manual code textbox)
  - "table"    → Layout 5 Title Only       (manual table shape)
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from pathlib import Path

from pptx import Presentation
from pptx.util import Inches, Pt

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CODE_FONT = "Consolas"
CODE_MAX_LINES = 30
BODY_LENGTH_THRESHOLD = 300
BODY_FONT_SMALL = Pt(14)
BODY_FONT_NORMAL = Pt(18)

# Theme configurations (replaces old TEMPLATES)
THEMES: dict[str, dict] = {
    "default": {
        "accent_color": "1F4E79",
        "heading_color": "1F4E79",
        "body_color": "333333",
        "bg_color": None,
        "color_override": True,
    },
    "dark": {
        "accent_color": "5BA3E6",
        "heading_color": "FFFFFF",
        "body_color": "CCCCCC",
        "bg_color": "1A1A2E",
        "color_override": True,
    },
    "minimal": {
        "accent_color": "000000",
        "heading_color": "000000",
        "body_color": "555555",
        "bg_color": None,
        "color_override": True,
    },
    "training": {
        "accent_color": "2E86AB",
        "heading_color": "2E86AB",
        "body_color": "333333",
        "bg_color": None,
        "color_override": True,
    },
}

# Legacy alias for backward compatibility
TEMPLATES = THEMES

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")
_BULLET_RE = re.compile(r"^[-*+]\s+(.+)$")
_NUMBERED_RE = re.compile(r"^\d+\.\s+(.+)$")

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _make_slide(
    slide_type: str,
    title: str = "",
    *,
    bullets: list | None = None,
    body: str = "",
    quote: str = "",
    code: str = "",
    lang: str = "",
    table_headers: list | None = None,
    table_rows: list | None = None,
) -> dict:
    """Factory function to create slide definitions with all fields."""
    return {
        "type": slide_type,
        "title": title,
        "bullets": bullets or [],
        "body": body,
        "quote": quote,
        "code": code,
        "lang": lang,
        "table_headers": table_headers or [],
        "table_rows": table_rows or [],
    }


def _get_ph(slide, idx: int):
    """Safely find a placeholder by placeholder_format.idx."""
    for ph in slide.placeholders:
        if ph.placeholder_format.idx == idx:
            return ph
    return None


def _set_title(slide, text: str) -> None:
    """Set slide title — prefer shapes.title, fall back to idx=0."""
    if not text:
        return
    if slide.shapes.title:
        slide.shapes.title.text = text
    else:
        ph = _get_ph(slide, 0)
        if ph:
            ph.text = text


def _extract_text(node: dict) -> str:
    """Extract plain text from a ProseMirror node (recursive)."""
    content = node.get("content", [])
    if not content:
        return node.get("text", "")
    parts: list[str] = []
    for child in content:
        if child.get("type") == "text":
            parts.append(child.get("text", ""))
        else:
            parts.append(_extract_text(child))
    return "".join(parts)


def _is_prosemirror_json(doc_json: str | dict) -> bool:
    """Check if doc_json is a ProseMirror doc structure."""
    if isinstance(doc_json, dict):
        return doc_json.get("type") == "doc" and "content" in doc_json
    if isinstance(doc_json, str):
        s = doc_json.strip()
        if not s.startswith("{"):
            return False
        try:
            parsed = json.loads(s)
            return isinstance(parsed, dict) and parsed.get("type") == "doc" and "content" in parsed
        except (json.JSONDecodeError, ValueError):
            return False
    return False


def _parse_color(hex_str: str | None):
    """Parse hex color string to RGBColor."""
    from pptx.dml.color import RGBColor

    if not hex_str:
        return RGBColor(0, 0, 0)
    hex_str = hex_str.lstrip("#")
    return RGBColor(int(hex_str[0:2], 16), int(hex_str[2:4], 16), int(hex_str[4:6], 16))


def _body_font_size(text: str):
    """Return appropriate font size based on text length."""
    if len(text) > BODY_LENGTH_THRESHOLD:
        return BODY_FONT_SMALL
    return BODY_FONT_NORMAL


def _calc_col_widths(headers: list, rows: list, total_width):
    """Calculate proportional column widths based on content length."""
    min_w = Inches(1.5)

    def _char_width(ch: str) -> int:
        return 2 if ord(ch) > 0x2000 else 1

    col_max = [sum(_char_width(c) for c in h) for h in headers]
    for row in rows:
        for j, cell in enumerate(row):
            if j < len(col_max):
                col_max[j] = max(col_max[j], sum(_char_width(c) for c in cell))

    weighted = [max(c, 1) for c in col_max]
    total_weight = sum(weighted)
    widths = [max(min_w, int(total_width * w / total_weight)) for w in weighted]
    diff = total_width - sum(widths)
    widths[-1] += diff
    return widths


def _parse_prosemirror_table(node: dict) -> tuple[list[str], list[list[str]]]:
    """Extract table headers and rows from ProseMirror table node."""
    headers: list[str] = []
    rows: list[list[str]] = []

    for row_node in node.get("content", []):
        if row_node.get("type") != "tableRow":
            continue
        cells: list[str] = []
        for cell_node in row_node.get("content", []):
            if cell_node.get("type") in ("tableCell", "tableHeader"):
                cells.append(_extract_text(cell_node))
        if not headers:
            headers = cells
        else:
            rows.append(cells)

    return headers, rows


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------


# Inline markdown stripping for PPT text (P1-15): paired-marker parsing, NOT
# char-level deletion — "C# 入门" / "3*4=12" / "5~10人" must survive.
_INLINE_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_INLINE_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_INLINE_CODE_RE = re.compile(r"`([^`]*)`")
_INLINE_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*|__([^_]+)__")
_INLINE_STRIKE_RE = re.compile(r"~~([^~]+)~~")
_INLINE_ITALIC_RE = re.compile(r"(?<![*\w])\*([^*]+)\*(?![*\w])|(?<![_\w])_([^_]+)_(?![_\w])")


def _strip_inline_md(text: str) -> str:
    """Strip inline markdown for PPT display text (P1-15/P1-16 minimal).

    Images degrade to a ``[配图：alt]`` placeholder (full image embedding is
    D2 scope). Links keep their text. Only PAIRED emphasis markers are
    removed, so literal ``#``/``*``/``~`` in normal text survive.
    """
    text = _INLINE_IMAGE_RE.sub(lambda m: f"[配图：{m.group(1)}]" if m.group(1) else "", text)
    text = _INLINE_LINK_RE.sub(r"\1", text)
    text = _INLINE_CODE_RE.sub(r"\1", text)
    text = _INLINE_BOLD_RE.sub(lambda m: m.group(1) or m.group(2), text)
    text = _INLINE_STRIKE_RE.sub(r"\1", text)
    text = _INLINE_ITALIC_RE.sub(lambda m: m.group(1) or m.group(2), text)
    return text.strip()


_PPTX_NAME_BAD = re.compile(r'[\\/:*?"<>|\r\n]+')


def _safe_pptx_name(title: str | None) -> str:
    """Download filename from the doc title (P2-42), CJK kept, path chars stripped."""
    if not title:
        return "output.pptx"
    base = _PPTX_NAME_BAD.sub("_", title).strip().strip(".")
    base = base[:60].strip()
    # "///" → "___"：纯占位下划线也视为无效名，回退默认
    if not base or not base.strip("_"):
        return "output.pptx"
    return f"{base}.pptx"


def _parse_markdown(text: str) -> list[dict]:
    """Parse Markdown text into slide definitions with state machine."""
    slides: list[dict] = []
    in_code_block = False
    code_buffer: list[str] = []
    code_lang = ""
    in_table = False
    table_headers: list[str] = []
    table_rows: list[list[str]] = []

    for line in text.split("\n"):
        stripped = line.strip()

        # --- State: inside code block ---
        if in_code_block:
            if stripped.startswith("```"):
                slides.append(_make_slide("code", code="\n".join(code_buffer), lang=code_lang))
                in_code_block = False
                code_buffer = []
            else:
                code_buffer.append(line)
            continue

        # --- Code block open ---
        if stripped.startswith("```"):
            in_code_block = True
            code_lang = stripped[3:].strip()
            code_buffer = []
            continue

        # --- State: inside table ---
        if in_table:
            if not stripped.startswith("|"):
                # Table ended — flush
                slides.append(_make_slide("table", table_headers=table_headers, table_rows=table_rows))
                in_table = False
                table_headers = []
                table_rows = []
                # Fall through to process this line normally
            elif re.match(r"^\|[\s\-:|]+\|$", stripped):
                # Separator line — skip
                continue
            else:
                # Data row
                cells = [c.strip() for c in stripped.strip("|").split("|")]
                table_rows.append(cells)
                continue

        # --- Table open (header line) ---
        if stripped.startswith("|") and stripped.endswith("|"):
            in_table = True
            table_headers = [c.strip() for c in stripped.strip("|").split("|")]
            table_rows = []
            continue

        # --- Blockquote ---
        if stripped.startswith("> "):
            quote_text = stripped[2:].strip()
            if slides:
                slides[-1]["quote"] = (slides[-1]["quote"] + "\n" + quote_text).strip() if slides[-1]["quote"] else quote_text
            else:
                slides.append(_make_slide("content", quote=quote_text))
            continue

        if not stripped:
            continue

        # --- Heading ---
        m = _HEADING_RE.match(stripped)
        if m:
            hashes, title_text = m.group(1), _strip_inline_md(m.group(2))
            level = len(hashes)
            if level <= 2:
                slide_type = "title" if level == 1 else "section"
                slides.append(_make_slide(slide_type, title=title_text))
            else:
                # H3-H6 become subtitle on current slide
                if slides:
                    slides[-1]["title"] = (slides[-1]["title"] + "\n" + title_text) if slides[-1]["title"] else title_text
                else:
                    slides.append(_make_slide("content", title=title_text))
            continue

        # --- Bullet list ---
        m = _BULLET_RE.match(stripped)
        if m:
            bullet_text = _strip_inline_md(m.group(1))
            if not bullet_text:
                continue
            if not slides:
                slides.append(_make_slide("content"))
            slides[-1]["bullets"].append(bullet_text)
            continue

        # --- Numbered list ---
        m = _NUMBERED_RE.match(stripped)
        if m:
            numbered_text = _strip_inline_md(m.group(1))
            if not numbered_text:
                continue
            if not slides:
                slides.append(_make_slide("content"))
            slides[-1]["bullets"].append(numbered_text)
            continue

        # --- Regular paragraph text — strip inline formatting (P1-15: paired-
        # marker parsing replaces the old char-level `re.sub(r"[*_~`#]")` which
        # ate literal characters like "C# 入门" / "3*4=12").
        clean = _strip_inline_md(stripped)
        if not clean:
            continue

        if not slides:
            slides.append(_make_slide("content", body=clean))
        else:
            current = slides[-1]
            if current["bullets"] or current["body"]:
                current["body"] = (current["body"] + "\n" + clean).strip()
            else:
                current["body"] = clean

    # --- EOF flush: table/code block at end of document ---
    if in_table:
        slides.append(_make_slide("table", table_headers=table_headers, table_rows=table_rows))
    if in_code_block:
        slides.append(_make_slide("code", code="\n".join(code_buffer), lang=code_lang))

    if not slides:
        slides.append(_make_slide("title", title="Untitled"))

    return slides


def _parse_prosemirror(doc_json: str | dict) -> list[dict]:
    """Parse TipTap ProseMirror doc JSON into slide definitions."""
    if isinstance(doc_json, str):
        doc = json.loads(doc_json)
    else:
        doc = doc_json

    nodes = doc.get("content", [])
    slides: list[dict] = []

    for node in nodes:
        node_type = node.get("type", "")

        if node_type == "heading":
            level = node.get("attrs", {}).get("level", 1)
            text = _extract_text(node)
            if level <= 2:
                slide_type = "title" if level == 1 else "section"
                slides.append(_make_slide(slide_type, title=text))
            else:
                if slides:
                    slides[-1]["title"] = slides[-1]["title"] + "\n" + text if slides[-1]["title"] else text
                else:
                    slides.append(_make_slide("content", title=text))

        elif node_type == "paragraph":
            text = _extract_text(node)
            if not text.strip():
                continue
            if not slides:
                slides.append(_make_slide("content", body=text))
            else:
                current = slides[-1]
                if current["bullets"] or current["body"]:
                    current["body"] = (current["body"] + "\n" + text).strip()
                else:
                    current["body"] = text

        elif node_type == "bulletList":
            items = node.get("content", [])
            if not slides:
                slides.append(_make_slide("content"))
            for item in items:
                if item.get("type") == "listItem":
                    bullet_text = _extract_text(item)
                    slides[-1]["bullets"].append(bullet_text)

        elif node_type == "codeBlock":
            code = _extract_text(node)
            lang = node.get("attrs", {}).get("language", "")
            slides.append(_make_slide("code", title=lang or "代码", code=code, lang=lang))

        elif node_type == "table":
            headers, rows = _parse_prosemirror_table(node)
            slides.append(_make_slide("table", table_headers=headers, table_rows=rows))

        elif node_type == "blockquote":
            text = _extract_text(node)
            if text.strip():
                if slides:
                    slides[-1]["quote"] = (slides[-1]["quote"] + "\n" + text).strip() if slides[-1]["quote"] else text
                else:
                    slides.append(_make_slide("content", quote=text))

        elif node_type in ("aiBlock", "aiBlockResult"):
            text = _extract_text(node)
            if text.strip():
                if not slides:
                    slides.append(_make_slide("content", body=text))
                else:
                    slides[-1]["body"] = (slides[-1]["body"] + "\n" + text).strip()

    if not slides:
        slides.append(_make_slide("title", title="Untitled"))

    return slides


def _parse_doc(doc_json: str | dict) -> list[dict]:
    """Parse doc content into slide definitions. Auto-detects format."""
    if _is_prosemirror_json(doc_json):
        return _parse_prosemirror(doc_json)
    text = doc_json if isinstance(doc_json, str) else json.dumps(doc_json)
    return _parse_markdown(text)


# ---------------------------------------------------------------------------
# PPTX Building
# ---------------------------------------------------------------------------


def _apply_bg(slide, bg_color: str | None) -> None:
    """Apply solid background color to a slide."""
    if not bg_color:
        return
    fill = slide.background.fill
    fill.solid()
    fill.fore_color.rgb = _parse_color(bg_color)


def _add_code_slide(slide, sd: dict, config: dict, margin, content_width) -> None:
    """Add code content to a Title Only slide."""
    code = sd["code"]
    lines = code.split("\n")
    if len(lines) > CODE_MAX_LINES:
        code = "\n".join(lines[:CODE_MAX_LINES]) + "\n... (已截断)"

    top = Inches(1.8)
    height = Inches(5.0)
    box = slide.shapes.add_textbox(margin, top, content_width, height)
    tf = box.text_frame
    tf.word_wrap = True

    p = tf.paragraphs[0]
    p.text = code
    p.font.size = Pt(12)
    p.font.name = CODE_FONT
    p.font.color.rgb = _parse_color(config.get("body_color"))

    # Code box background
    from pptx.dml.color import RGBColor

    fill = box.fill
    fill.solid()
    if config.get("bg_color"):
        fill.fore_color.rgb = RGBColor(0x2D, 0x2D, 0x3D)
    else:
        fill.fore_color.rgb = RGBColor(0xF5, 0xF5, 0xF5)


def _add_table_slide(slide, sd: dict, config: dict, margin, content_width) -> None:
    """Add table to a Title Only slide."""
    headers = sd["table_headers"]
    rows = sd["table_rows"]
    if not headers:
        return

    num_rows = len(rows) + 1
    num_cols = len(headers)
    col_widths = _calc_col_widths(headers, rows, content_width)

    top = Inches(2.0)
    table_shape = slide.shapes.add_table(num_rows, num_cols, margin, top, content_width, Inches(4.5))
    table = table_shape.table

    # Set column widths
    for j, w in enumerate(col_widths):
        if j < len(table.columns):
            table.columns[j].width = w

    # Header row — bold + accent background
    for j, h in enumerate(headers):
        cell = table.cell(0, j)
        cell.text = h
        for p in cell.text_frame.paragraphs:
            p.font.bold = True
            p.font.size = Pt(14)
            p.font.color.rgb = _parse_color("FFFFFF")
        cell.fill.solid()
        cell.fill.fore_color.rgb = _parse_color(config.get("accent_color"))

    # Data rows
    for i, row in enumerate(rows):
        for j, cell_text in enumerate(row):
            if j >= num_cols:
                break
            cell = table.cell(i + 1, j)
            cell.text = cell_text
            for p in cell.text_frame.paragraphs:
                p.font.size = Pt(12)
                p.font.color.rgb = _parse_color(config.get("body_color"))


def _fill_content(slide, sd: dict, config: dict) -> None:
    """Fill body placeholder (Layout 1) with quote/bullets/body text."""
    ph = _get_ph(slide, 1)
    if not ph:
        return

    tf = ph.text_frame
    tf.word_wrap = True

    paragraphs_to_add: list[tuple[str, str]] = []

    if sd["quote"]:
        paragraphs_to_add.append(("quote", sd["quote"]))

    if sd["bullets"]:
        for bullet in sd["bullets"]:
            paragraphs_to_add.append(("bullet", bullet))
    if sd["body"]:
        paragraphs_to_add.append(("body", sd["body"]))

    if not paragraphs_to_add:
        return

    for idx, (ptype, text) in enumerate(paragraphs_to_add):
        if idx == 0:
            p = tf.paragraphs[0]
        else:
            p = tf.add_paragraph()

        p.text = text
        p.space_after = Pt(6)

        if ptype == "quote":
            p.font.italic = True
            p.font.size = BODY_FONT_SMALL
        elif ptype == "bullet":
            p.level = 0
            p.font.size = BODY_FONT_NORMAL
        else:
            p.font.size = _body_font_size(text)

        if config.get("color_override"):
            p.font.color.rgb = _parse_color(config["body_color"])


def _build_pptx(
    slides: list[dict],
    template_name: str,
    output_path: Path,
    progress: Callable[[int, str], None],
) -> dict:
    """Build a .pptx file from slide definitions using built-in layouts."""
    config = THEMES.get(template_name, THEMES["default"])
    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)

    MARGIN = Inches(0.8)
    CONTENT_WIDTH = prs.slide_width - MARGIN * 2

    total = len(slides)
    for i, sd in enumerate(slides):
        slide_type = sd["type"]
        has_content = bool(sd["bullets"] or sd["body"] or sd["quote"])

        # --- Select layout based on type + content ---
        if slide_type == "title" and not has_content:
            # Pure cover page
            slide = prs.slides.add_slide(prs.slide_layouts[0])
            _set_title(slide, sd["title"])

        elif slide_type == "section" and not has_content:
            # Pure section divider
            slide = prs.slides.add_slide(prs.slide_layouts[2])
            _set_title(slide, sd["title"])

        elif slide_type == "code":
            slide = prs.slides.add_slide(prs.slide_layouts[5])
            _set_title(slide, sd["title"] or sd.get("lang") or "代码")
            _add_code_slide(slide, sd, config, MARGIN, CONTENT_WIDTH)

        elif slide_type == "table":
            slide = prs.slides.add_slide(prs.slide_layouts[5])
            _set_title(slide, sd["title"])
            _add_table_slide(slide, sd, config, MARGIN, CONTENT_WIDTH)

        else:
            # Content-bearing slides (title/section/content with body/bullets/quote)
            slide = prs.slides.add_slide(prs.slide_layouts[1])
            _set_title(slide, sd["title"])
            _fill_content(slide, sd, config)

        # --- Override title color ---
        if config.get("color_override") and sd["title"]:
            title_shape = slide.shapes.title
            if title_shape:
                for p in title_shape.text_frame.paragraphs:
                    p.font.color.rgb = _parse_color(config["heading_color"])

        # --- Dark / custom background ---
        if config.get("bg_color"):
            _apply_bg(slide, config["bg_color"])

        pct = int((i + 1) / total * 80)
        progress(pct, f"Slide {i + 1}/{total}")

    prs.save(str(output_path))
    file_size = output_path.stat().st_size
    progress(100, "Done")

    return {
        "output_path": str(output_path),
        "file_size": file_size,
        "slide_count": total,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_pptx_from_doc(
    *,
    doc_json: str | dict,
    template: str = "default",
    out_dir: str,
    progress: Callable[[int, str], None] | None = None,
    title: str | None = None,
) -> dict:
    """Main entry point: Markdown or TipTap doc JSON → .pptx file.

    Args:
        doc_json: Markdown text, or legacy TipTap ProseMirror doc JSON (string or dict).
        template: Template name (default/dark/minimal/training).
        out_dir: Output directory for the .pptx file.
        progress: Optional callback(pct: int, msg: str) for progress.
        title: Optional doc title — drives the .pptx filename (P2-42).

    Returns:
        Dict with output_path, file_size, slide_count.
    """
    if progress is None:

        def progress(pct, msg=""):
            pass

    progress(5, "Parsing document")
    slides = _parse_doc(doc_json)

    progress(10, "Building presentation")
    output_path = Path(out_dir) / _safe_pptx_name(title)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    return _build_pptx(slides, template, output_path, progress)
