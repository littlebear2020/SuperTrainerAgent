"""PDF generation pipeline - converts Markdown/ProseMirror to PDF via WeasyPrint.

B4-T2: Architecture mirrors ppt_pipeline.py:
- _parse_doc / _is_prosemirror_json are imported from ppt_pipeline (shared)
- markdown -> HTML via python-markdown (tables + fenced_code extensions)
- Print CSS: A4, margins, CJK font stack, code/table styling
- Local images: resolved from /api/writer/assets/images/raw/ -> filesystem path
- Remote/generated images: degraded to placeholder box (non-blocking)
"""

from __future__ import annotations

import html
import logging
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# C4 fix: sanitize document titles used as PDF filenames to prevent path traversal.
# Matches the PPT pipeline's _PPTX_NAME_BAD pattern (ppt_pipeline.py).
_PDF_NAME_BAD = re.compile(r'[\\/:*?"<>|\r\n]+')


def _safe_pdf_name(title: str | None) -> str:
    """Sanitize a document title into a safe PDF filename.

    Strips path separators and other invalid filename characters (CJK preserved).
    Falls back to ``output.pdf`` when the result is empty.
    """
    if not title:
        return "output.pdf"
    base = _PDF_NAME_BAD.sub("_", title).strip().strip(".")
    base = base[:60].strip()
    if not base or not base.strip("_"):
        return "output.pdf"
    return f"{base}.pdf"


# CJK-friendly font stack for print
_FONT_STACK = '"Noto Sans CJK SC", "Noto Sans SC", "PingFang SC", "Microsoft YaHei", sans-serif'

_PRINT_CSS = f"""
@page {{
    size: A4;
    margin: 2cm 2.5cm;
}}
body {{
    font-family: {_FONT_STACK};
    font-size: 11pt;
    line-height: 1.6;
    color: #333;
}}
h1 {{ font-size: 20pt; margin-top: 1em; }}
h2 {{ font-size: 16pt; margin-top: 0.8em; }}
h3 {{ font-size: 14pt; margin-top: 0.6em; }}
h4, h5, h6 {{ font-size: 12pt; margin-top: 0.5em; }}
p {{ margin: 0.5em 0; }}
code {{
    font-family: "Noto Sans Mono CJK SC", "Courier New", monospace;
    background: #f5f5f5;
    padding: 0.1em 0.3em;
    border-radius: 3px;
    font-size: 10pt;
}}
pre {{
    background: #f5f5f5;
    padding: 0.8em;
    border-radius: 5px;
    overflow-x: auto;
    line-height: 1.4;
}}
pre code {{
    background: none;
    padding: 0;
}}
table {{
    border-collapse: collapse;
    width: 100%;
    margin: 0.8em 0;
}}
th, td {{
    border: 1px solid #ddd;
    padding: 0.4em 0.6em;
    text-align: left;
}}
th {{
    background: #f0f0f0;
    font-weight: bold;
}}
img {{
    max-width: 100%;
    height: auto;
}}
blockquote {{
    border-left: 3px solid #ccc;
    margin: 0.5em 0;
    padding: 0.3em 0.8em;
    color: #666;
}}
"""

# Local image URL prefix (matches assets_router.py:170)
_WRITER_IMAGE_URL_PREFIX = "/api/writer/assets/images/raw/"


def _resolve_local_image_src(src: str, images_dir: Path) -> str | None:
    """Resolve a local writer image URL to a filesystem path.

    Returns the absolute path if the image is a local writer asset, else None
    (caller degrades to placeholder for remote/generated images).
    """
    if not src.startswith(_WRITER_IMAGE_URL_PREFIX):
        return None
    filename = src[len(_WRITER_IMAGE_URL_PREFIX) :]
    # Sanitize: only allow filenames, no path traversal
    if "/" in filename or ".." in filename:
        return None
    img_path = images_dir / filename
    if img_path.exists():
        return str(img_path)
    return None


# SEC-H2 (批次 2 项 2.4): attributes WeasyPrint fetches over the network when
# rendering. ``href`` is deliberately NOT in this set for <a> — anchors are
# clickable metadata, never fetched — but IS handled for link/image/use below.
_FETCH_DROP_TAGS = frozenset({"link"})
_FETCH_DROP_ATTR_TAGS = frozenset({"video", "audio", "source", "track", "embed", "object", "iframe", "image", "use"})
_URL_IN_STYLE_RE = re.compile(r"""url\s*\(\s*(?:'[^']*'|"[^"]*"|[^)\s]*)\s*\)""", re.IGNORECASE)


class _SsrfSafeRewriter(HTMLParser):
    """HTML-parser rewriter that removes every network-fetchable reference.

    SEC-H2 (批次 2 项 2.4): the old regex only matched double-quoted
    ``src="..."`` — single-quoted/unquoted src, CSS ``url()`` in raw
    ``<style>`` blocks or inline style attributes, and ``<link>`` stylesheets
    all survived into WeasyPrint, which then fetched them (blind GET SSRF:
    ``write_pdf`` reaching internal hosts). Parser-based rules (decision
    2026-09-03: 不设白名单、只信本地图):

    - ``<img>``: local writer asset -> ``file://``; anything else (remote,
      data:, missing) -> placeholder box (unchanged UX);
    - ``<link>`` tags: dropped entirely;
    - ``<style>`` elements: dropped with their content (our own print CSS is
      injected after this pass, so it is unaffected);
    - fetchable attributes (src/poster/data/href/srcset/…) on media/embed/svg
      elements: attribute dropped, element left inert;
    - inline ``style`` attributes on ANY element: ``url(...)`` tokens stripped;
    - 二轮复核补（2026-09-04，tinyhtml5 树级实测）：``<style/>`` 自闭合斜杠
      在 HTML5 非 void 元素上被忽略（仍开 style 元素）——按开标签处理连内容
      剥除；CDATA（unknown_decl）整段丢弃不回吐；无 src 的 ``<img>`` 不再
      整签透传（内联 style 的 url() 存活）——走通用剥除；presentational
      ``background`` 属性全元素剥除（WeasyPrint 转 background-image 发 fetch）。
    """

    def __init__(self, images_dir: Path) -> None:
        super().__init__(convert_charrefs=True)
        self._images_dir = images_dir
        self._out: list[str] = []
        self._in_style = 0

    # -- tag handling ------------------------------------------------------

    def _attr_map(self, attrs: list[tuple[str | None, str | None]]) -> dict[str, str | None]:
        # First occurrence wins (mirrors browser behavior); None-valued attrs
        # (bare keywords) map to "".
        return {k or "": v or "" for k, v in reversed(attrs)} if attrs else {}

    def _rebuild_tag(self, tag: str, attr_map: dict[str, str | None], self_closing: bool) -> str:
        parts = [tag]
        for name, value in attr_map.items():
            if value is None or value == "" and name not in ("src",):  # bare attr
                parts.append(name)
            else:
                parts.append(f'{name}="{html.escape(value, quote=True)}"')
        return f"<{' '.join(parts)}{'/>' if self_closing else '>'}"

    def _handle_tag(self, tag: str, attrs: list[tuple[str | None, str | None]], self_closing: bool) -> None:
        low = tag.lower()

        if low in _FETCH_DROP_TAGS:
            return  # <link> dropped entirely (SEC-H2)

        if low == "style":
            # HTML5: 非 void 元素的自闭合斜杠被忽略——<style/> 仍然开启一个
            # style 元素，其后内容是 CSS。无论何种形式都按开标签处理，
            # 元素连内容一并剥除（二轮复核向量 1）。
            self._in_style += 1
            return  # element dropped with content

        if low == "img":
            attr_map = self._attr_map(attrs)
            src = attr_map.get("src")
            if src is not None:
                local_path = _resolve_local_image_src(src, self._images_dir)
                if local_path:
                    self._out.append(f'<img src="file://{html.escape(local_path, quote=True)}" />')
                    return
                # Remote / data: / missing -> placeholder (replaces the entire tag)
                self._out.append(f'<div style="border:1px dashed #999;padding:1em;text-align:center;color:#999;">[图片: {html.escape(src, quote=True)}]</div>')
                return
            # No src at all: fall through to the generic strip — the old raw
            # passthrough kept an inline style's url() alive (WeasyPrint
            # fetches it) — 二轮复核向量 3.
            self._out.append(self._emit_generic(low, attr_map, self_closing, force_rebuild=True))
            return

        self._out.append(self._emit_generic(low, self._attr_map(attrs), self_closing))

    def _emit_generic(self, low: str, attr_map: dict, self_closing: bool, *, force_rebuild: bool = False) -> str:
        """Generic tag emission: strip fetch-relevant attributes, rebuild.

        Applies to every non-img/link/style tag (and to src-less <img>):
        - media/embed/svg elements: drop src/poster/data/href/srcset/…;
        - ANY element: drop the presentational ``background`` attribute
          (WeasyPrint turns it into a background-image fetch — 二轮复核
          向量 4) and strip ``url(...)`` from inline style.
        """
        changed = force_rebuild
        if low in _FETCH_DROP_ATTR_TAGS:
            for name in list(attr_map):
                if name.lower() in ("src", "poster", "data", "href", "xlink:href", "srcset", "background", "dynsrc", "lowsrc"):
                    del attr_map[name]
                    changed = True
        if "background" in attr_map:  # presentational attr on ANY element
            del attr_map["background"]
            changed = True
        style_value = attr_map.get("style")
        if style_value is not None and "url(" in style_value.lower():
            attr_map["style"] = _URL_IN_STYLE_RE.sub("", style_value)
            changed = True
        if changed:
            return self._rebuild_tag(low, attr_map, self_closing)
        return self.get_starttag_text() or self._rebuild_tag(low, attr_map, self_closing)

    def handle_starttag(self, tag, attrs):
        self._handle_tag(tag, attrs, self_closing=False)

    def handle_startendtag(self, tag, attrs):
        self._handle_tag(tag, attrs, self_closing=True)

    def handle_endtag(self, tag):
        if tag.lower() == "style" and self._in_style:
            self._in_style -= 1
            return  # dropped with the element
        self._out.append(f"</{tag}>")

    def handle_data(self, data):
        if self._in_style:
            return  # style content dropped
        # convert_charrefs hands us UNescaped text; re-escape so a literal
        # ``<``/``&`` in document text cannot re-open a tag downstream.
        self._out.append(html.escape(data, quote=False))

    # -- verbatim passthrough for everything the parser surfaces -----------

    def handle_comment(self, data):
        self._out.append(f"<!--{data}-->")

    def handle_decl(self, decl):
        self._out.append(f"<!{decl}>")

    def handle_pi(self, data):
        self._out.append(f"<?{data}>")

    def handle_entityref(self, name):  # pragma: no cover (convert_charrefs=True)
        self._out.append(f"&{name};")

    def handle_charref(self, name):  # pragma: no cover (convert_charrefs=True)
        self._out.append(f"&#{name};")

    def unknown_decl(self, data):  # CDATA etc.
        # 二轮复核向量 2：CDATA 段在 foreign content（svg/math）里原样生效，
        # 可走私任意 CSS/标记——整段丢弃，不回吐。
        return

    def result(self) -> str:
        return "".join(self._out)


def _replace_images_with_file_paths(html_str: str, images_dir: Path) -> str:
    """Replace local image URLs with file:// paths; neutralize every fetchable ref.

    Local writer assets embed via ``file://``; remote/generated images degrade to
    a placeholder box. Everything else WeasyPrint could fetch over the network
    (raw ``<style>``/``<link>``, media/embed ``src``/``poster``/``data``,
    inline-style ``url()``) is stripped before the HTML reaches ``write_pdf``
    (SEC-H2: blind-SSRF closure — 只信本地图, no whitelist by decision).
    """
    rewriter = _SsrfSafeRewriter(images_dir)
    rewriter.feed(html_str)
    rewriter.close()
    return rewriter.result()


def build_pdf_from_doc(
    doc_json: str | dict,
    out_dir: str,
    title: str | None = None,
    progress: Any | None = None,
) -> dict:
    """Build a PDF from a Markdown or ProseMirror JSON document.

    Mirrors the signature of build_pptx_from_doc (ppt_pipeline.py:693).
    Returns ``{output_path, file_size}``.
    """
    import markdown as md

    from app.gateway.content.ppt_pipeline import _is_prosemirror_json, _parse_doc

    # Convert content to markdown string
    if _is_prosemirror_json(doc_json):
        # ProseMirror JSON -> parse to slide defs -> extract text as markdown
        # Reuse _parse_doc which returns slide dicts; extract markdown text
        slides = _parse_doc(doc_json)
        md_text = "\n\n".join(slide.get("title", "") + "\n" + slide.get("content", "") for slide in slides)
    elif isinstance(doc_json, str):
        md_text = doc_json
    else:
        md_text = str(doc_json)

    # markdown -> HTML
    html_body = md.markdown(
        md_text,
        extensions=["tables", "fenced_code", "nl2br"],
    )

    # Resolve local images to file paths
    images_dir = Path(out_dir).parent / "writer-images"
    if not images_dir.exists():
        # Fallback: project-level images dir
        from app.gateway.content.assets_router import _WRITER_IMAGES_DIR

        images_dir = _WRITER_IMAGES_DIR

    html_body = _replace_images_with_file_paths(html_body, images_dir)

    # Wrap in full HTML document
    doc_title = title or "Document"
    escaped_title = html.escape(doc_title, quote=True)
    full_html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="utf-8">
    <title>{escaped_title}</title>
    <style>{_PRINT_CSS}</style>
</head>
<body>
{html_body}
</body>
</html>"""

    # Generate PDF
    from weasyprint import HTML

    # C4 fix: sanitize filename to prevent path traversal
    out_path = Path(out_dir) / _safe_pdf_name(doc_title)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    HTML(string=full_html, base_url=str(out_dir)).write_pdf(str(out_path))

    file_size = out_path.stat().st_size

    if progress:
        progress(100, "PDF generated")

    return {
        "output_path": str(out_path),
        "file_size": file_size,
    }


def task_media_type(task_type: str) -> str:
    """Map a content task type to its download media type.

    B4-T2: extracted as a pure function so the media-type mapping is
    unit-testable without driving the download endpoint.
    """
    if task_type == "pdf_gen":
        return "application/pdf"
    if task_type == "ppt_gen":
        return "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    return "application/octet-stream"
