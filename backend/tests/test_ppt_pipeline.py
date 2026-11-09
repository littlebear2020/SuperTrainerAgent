"""Tests for PPT generation pipeline."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pptx import Presentation
from pptx.util import Pt

from app.gateway.content.ppt_pipeline import (
    BODY_LENGTH_THRESHOLD,
    _body_font_size,
    _extract_text,
    _make_slide,
    _parse_doc,
    _parse_markdown,
    _parse_prosemirror,
    build_pptx_from_doc,
)

# ---------------------------------------------------------------------------
# _extract_text
# ---------------------------------------------------------------------------


def test_extract_text_simple():
    node = {"type": "text", "text": "Hello world"}
    assert _extract_text(node) == "Hello world"


def test_extract_text_nested():
    node = {
        "type": "paragraph",
        "content": [
            {"type": "text", "text": "Hello "},
            {"type": "text", "text": "world"},
        ],
    }
    assert _extract_text(node) == "Hello world"


def test_extract_text_empty():
    assert _extract_text({}) == ""
    assert _extract_text({"type": "text"}) == ""


# ---------------------------------------------------------------------------
# _make_slide factory
# ---------------------------------------------------------------------------


def test_make_slide_defaults():
    slide = _make_slide("content")
    assert slide["type"] == "content"
    assert slide["title"] == ""
    assert slide["bullets"] == []
    assert slide["body"] == ""
    assert slide["quote"] == ""
    assert slide["code"] == ""
    assert slide["lang"] == ""
    assert slide["table_headers"] == []
    assert slide["table_rows"] == []


def test_make_slide_with_all_fields():
    slide = _make_slide(
        "code",
        "Title",
        bullets=["a"],
        body="text",
        quote="q",
        code="print()",
        lang="python",
        table_headers=["H"],
        table_rows=[["v"]],
    )
    assert slide["type"] == "code"
    assert slide["title"] == "Title"
    assert slide["bullets"] == ["a"]
    assert slide["body"] == "text"
    assert slide["quote"] == "q"
    assert slide["code"] == "print()"
    assert slide["lang"] == "python"
    assert slide["table_headers"] == ["H"]
    assert slide["table_rows"] == [["v"]]


# ---------------------------------------------------------------------------
# _parse_doc (ProseMirror path — existing tests)
# ---------------------------------------------------------------------------


def test_parse_doc_h1_creates_slide():
    doc = {
        "type": "doc",
        "content": [
            {"type": "heading", "attrs": {"level": 1}, "content": [{"type": "text", "text": "Title"}]},
        ],
    }
    slides = _parse_doc(doc)
    assert len(slides) == 1
    assert slides[0]["type"] == "title"
    assert slides[0]["title"] == "Title"


def test_parse_doc_h2_creates_section():
    doc = {
        "type": "doc",
        "content": [
            {"type": "heading", "attrs": {"level": 2}, "content": [{"type": "text", "text": "Section"}]},
        ],
    }
    slides = _parse_doc(doc)
    assert len(slides) == 1
    assert slides[0]["type"] == "section"


def test_parse_doc_multiple_headings():
    doc = {
        "type": "doc",
        "content": [
            {"type": "heading", "attrs": {"level": 1}, "content": [{"type": "text", "text": "Slide 1"}]},
            {"type": "heading", "attrs": {"level": 1}, "content": [{"type": "text", "text": "Slide 2"}]},
        ],
    }
    slides = _parse_doc(doc)
    assert len(slides) == 2


def test_parse_doc_paragraph():
    doc = {
        "type": "doc",
        "content": [
            {"type": "heading", "attrs": {"level": 1}, "content": [{"type": "text", "text": "Title"}]},
            {"type": "paragraph", "content": [{"type": "text", "text": "Body text"}]},
        ],
    }
    slides = _parse_doc(doc)
    assert len(slides) == 1
    assert slides[0]["body"] == "Body text"


def test_parse_doc_bullet_list():
    doc = {
        "type": "doc",
        "content": [
            {"type": "heading", "attrs": {"level": 1}, "content": [{"type": "text", "text": "Title"}]},
            {
                "type": "bulletList",
                "content": [
                    {"type": "listItem", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Item 1"}]}]},
                    {"type": "listItem", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Item 2"}]}]},
                ],
            },
        ],
    }
    slides = _parse_doc(doc)
    assert len(slides) == 1
    assert slides[0]["bullets"] == ["Item 1", "Item 2"]


def test_parse_doc_empty_creates_default():
    slides = _parse_doc({"type": "doc", "content": []})
    assert len(slides) == 1
    assert slides[0]["title"] == "Untitled"


def test_parse_doc_from_string():
    doc_str = json.dumps(
        {
            "type": "doc",
            "content": [
                {"type": "heading", "attrs": {"level": 1}, "content": [{"type": "text", "text": "Test"}]},
            ],
        }
    )
    slides = _parse_doc(doc_str)
    assert len(slides) == 1
    assert slides[0]["title"] == "Test"


# ---------------------------------------------------------------------------
# _parse_markdown — code blocks, tables, blockquotes
# ---------------------------------------------------------------------------


def test_parse_markdown_code_block():
    md = "# Title\n```python\nprint('hello')\nprint('world')\n```"
    slides = _parse_markdown(md)
    assert len(slides) == 2  # title, code
    assert slides[1]["type"] == "code"
    assert slides[1]["lang"] == "python"
    assert "print('hello')" in slides[1]["code"]
    assert "print('world')" in slides[1]["code"]


def test_parse_markdown_code_block_at_eof():
    """Code block ending at EOF should still be flushed."""
    md = "```js\nconst x = 1;\n```"
    slides = _parse_markdown(md)
    assert len(slides) == 1
    assert slides[0]["type"] == "code"
    assert slides[0]["lang"] == "js"


def test_parse_markdown_table():
    md = "# Title\n| Name | Age |\n|---|---|\n| Alice | 30 |\n| Bob | 25 |"
    slides = _parse_markdown(md)
    assert len(slides) == 2
    assert slides[1]["type"] == "table"
    assert slides[1]["table_headers"] == ["Name", "Age"]
    assert slides[1]["table_rows"] == [["Alice", "30"], ["Bob", "25"]]


def test_parse_markdown_table_at_eof():
    """Table at end of document should be flushed (DeepSeek correction)."""
    md = "| A | B |\n|---|---|\n| 1 | 2 |"
    slides = _parse_markdown(md)
    assert len(slides) == 1
    assert slides[0]["type"] == "table"
    assert slides[0]["table_headers"] == ["A", "B"]
    assert slides[0]["table_rows"] == [["1", "2"]]


def test_parse_markdown_blockquote():
    md = "# Title\n> This is a quote\nBody text"
    slides = _parse_markdown(md)
    assert len(slides) == 1
    assert slides[0]["quote"] == "This is a quote"
    assert slides[0]["body"] == "Body text"


def test_parse_markdown_blockquote_prefix_stripped():
    """> prefix should be removed, not appear in output."""
    md = "> Hello world"
    slides = _parse_markdown(md)
    assert len(slides) == 1
    assert slides[0]["quote"] == "Hello world"
    assert ">" not in slides[0]["quote"]


# ---------------------------------------------------------------------------
# _parse_prosemirror — codeBlock, table, blockquote
# ---------------------------------------------------------------------------


def test_parse_prosemirror_code_block():
    doc = {
        "type": "doc",
        "content": [
            {
                "type": "codeBlock",
                "attrs": {"language": "python"},
                "content": [{"type": "text", "text": "print('hello')"}],
            },
        ],
    }
    slides = _parse_prosemirror(doc)
    assert len(slides) == 1
    assert slides[0]["type"] == "code"
    assert slides[0]["lang"] == "python"
    assert "print('hello')" in slides[0]["code"]


def test_parse_prosemirror_table():
    doc = {
        "type": "doc",
        "content": [
            {
                "type": "table",
                "content": [
                    {
                        "type": "tableRow",
                        "content": [
                            {"type": "tableCell", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Name"}]}]},
                            {"type": "tableCell", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Age"}]}]},
                        ],
                    },
                    {
                        "type": "tableRow",
                        "content": [
                            {"type": "tableCell", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Alice"}]}]},
                            {"type": "tableCell", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "30"}]}]},
                        ],
                    },
                ],
            },
        ],
    }
    slides = _parse_prosemirror(doc)
    assert len(slides) == 1
    assert slides[0]["type"] == "table"
    assert slides[0]["table_headers"] == ["Name", "Age"]
    assert slides[0]["table_rows"] == [["Alice", "30"]]


def test_parse_prosemirror_blockquote():
    doc = {
        "type": "doc",
        "content": [
            {"type": "heading", "attrs": {"level": 1}, "content": [{"type": "text", "text": "Title"}]},
            {
                "type": "blockquote",
                "content": [
                    {"type": "paragraph", "content": [{"type": "text", "text": "Important quote"}]},
                ],
            },
        ],
    }
    slides = _parse_prosemirror(doc)
    assert len(slides) == 1
    assert slides[0]["quote"] == "Important quote"


# ---------------------------------------------------------------------------
# _body_font_size
# ---------------------------------------------------------------------------


def test_long_body_auto_shrink():
    short_text = "a" * 100
    assert _body_font_size(short_text) == Pt(18)
    long_text = "a" * (BODY_LENGTH_THRESHOLD + 1)
    assert _body_font_size(long_text) == Pt(14)


# ---------------------------------------------------------------------------
# build_pptx_from_doc (integration)
# ---------------------------------------------------------------------------


def test_build_pptx_creates_file(tmp_path):
    doc = {
        "type": "doc",
        "content": [
            {"type": "heading", "attrs": {"level": 1}, "content": [{"type": "text", "text": "My Presentation"}]},
            {"type": "heading", "attrs": {"level": 2}, "content": [{"type": "text", "text": "Section 1"}]},
            {"type": "paragraph", "content": [{"type": "text", "text": "Some content here"}]},
            {"type": "heading", "attrs": {"level": 1}, "content": [{"type": "text", "text": "Slide 2"}]},
        ],
    }

    progress_log = []

    def on_progress(pct, msg=""):
        progress_log.append((pct, msg))

    result = build_pptx_from_doc(
        doc_json=doc,
        template="default",
        out_dir=str(tmp_path),
        progress=on_progress,
    )

    output = Path(result["output_path"])
    assert output.exists()
    assert result["file_size"] > 0
    assert result["slide_count"] == 3  # H1, H2(+paragraph), H1

    # Verify it's a valid PPTX
    prs = Presentation(str(output))
    assert len(prs.slides) == 3

    # Progress was reported
    assert len(progress_log) > 0
    assert progress_log[-1][0] == 100


def test_build_pptx_dark_template(tmp_path):
    doc = {
        "type": "doc",
        "content": [
            {"type": "heading", "attrs": {"level": 1}, "content": [{"type": "text", "text": "Dark Theme"}]},
        ],
    }
    result = build_pptx_from_doc(
        doc_json=doc,
        template="dark",
        out_dir=str(tmp_path),
    )
    assert Path(result["output_path"]).exists()
    assert result["slide_count"] == 1


def test_build_pptx_bullets(tmp_path):
    doc = {
        "type": "doc",
        "content": [
            {"type": "heading", "attrs": {"level": 1}, "content": [{"type": "text", "text": "Key Points"}]},
            {
                "type": "bulletList",
                "content": [
                    {"type": "listItem", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "First"}]}]},
                    {"type": "listItem", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Second"}]}]},
                    {"type": "listItem", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Third"}]}]},
                ],
            },
        ],
    }
    result = build_pptx_from_doc(doc_json=doc, template="training", out_dir=str(tmp_path))
    prs = Presentation(str(Path(result["output_path"])))
    slide = prs.slides[0]
    assert len(slide.shapes) >= 2


def test_build_pptx_no_progress_callback(tmp_path):
    """Should work without progress callback."""
    doc = {
        "type": "doc",
        "content": [
            {"type": "heading", "attrs": {"level": 1}, "content": [{"type": "text", "text": "No Callback"}]},
        ],
    }
    result = build_pptx_from_doc(doc_json=doc, out_dir=str(tmp_path))
    assert Path(result["output_path"]).exists()


def test_build_pptx_uses_layout_not_blank(tmp_path):
    """Slides should use built-in layouts, never Blank."""
    doc = {
        "type": "doc",
        "content": [
            {"type": "heading", "attrs": {"level": 1}, "content": [{"type": "text", "text": "Title"}]},
            {"type": "paragraph", "content": [{"type": "text", "text": "Content"}]},
        ],
    }
    result = build_pptx_from_doc(doc_json=doc, out_dir=str(tmp_path))
    prs = Presentation(str(Path(result["output_path"])))
    for slide in prs.slides:
        assert slide.slide_layout.name != "Blank"


def test_build_pptx_code_slide(tmp_path):
    """Code block should produce a slide with code textbox."""
    doc = {
        "type": "doc",
        "content": [
            {
                "type": "codeBlock",
                "attrs": {"language": "python"},
                "content": [{"type": "text", "text": "print('hello')"}],
            },
        ],
    }
    result = build_pptx_from_doc(doc_json=doc, out_dir=str(tmp_path))
    prs = Presentation(str(Path(result["output_path"])))
    assert len(prs.slides) == 1
    assert len(prs.slides[0].shapes) >= 2  # title + code textbox


def test_build_pptx_table_slide(tmp_path):
    """Table should produce a slide with table shape."""
    doc = {
        "type": "doc",
        "content": [
            {
                "type": "table",
                "content": [
                    {
                        "type": "tableRow",
                        "content": [
                            {"type": "tableCell", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Name"}]}]},
                            {"type": "tableCell", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Age"}]}]},
                        ],
                    },
                    {
                        "type": "tableRow",
                        "content": [
                            {"type": "tableCell", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Alice"}]}]},
                            {"type": "tableCell", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "30"}]}]},
                        ],
                    },
                ],
            },
        ],
    }
    result = build_pptx_from_doc(doc_json=doc, out_dir=str(tmp_path))
    prs = Presentation(str(Path(result["output_path"])))
    assert len(prs.slides) == 1
    has_table = any(shape.has_table for shape in prs.slides[0].shapes)
    assert has_table


# ── P1-15: inline markdown stripping — markers only, never literal chars ──


class TestStripInlineMd:
    def test_bold_italic_code_markers_removed(self):
        from app.gateway.content.ppt_pipeline import _strip_inline_md

        assert _strip_inline_md("**重要** 事项") == "重要 事项"
        assert _strip_inline_md("用 `pip install` 安装") == "用 pip install 安装"
        assert _strip_inline_md("*斜体* 与 _斜体_") == "斜体 与 斜体"

    def test_literal_special_chars_preserved(self):
        from app.gateway.content.ppt_pipeline import _strip_inline_md

        assert _strip_inline_md("C# 入门") == "C# 入门"
        assert _strip_inline_md("3*4=12") == "3*4=12"
        assert _strip_inline_md("5~10人") == "5~10人"

    def test_unpaired_markers_preserved(self):
        from app.gateway.content.ppt_pipeline import _strip_inline_md

        # 未配对标记按字面保留（评审问题 5）——若「修复」成删单星号会误伤 "3*4"
        assert _strip_inline_md("未配对 **星号") == "未配对 **星号"

    def test_cjk_underscore_not_treated_as_italic(self):
        from app.gateway.content.ppt_pipeline import _strip_inline_md

        # Python re 的 \w 含中文：lookbehind 使 CJK 之间的 _ 不判斜体（有意行为，
        # CJK 场景下 _ 常作普通字符）——固化语义防回归
        assert _strip_inline_md("中文_下划线_中文") == "中文_下划线_中文"

    def test_links_become_text_images_become_placeholder(self):
        from app.gateway.content.ppt_pipeline import _strip_inline_md

        assert _strip_inline_md("详见[官方文档](https://example.com)") == "详见官方文档"
        assert _strip_inline_md("![架构图](https://x.com/a.png)") == "[配图：架构图]"


class TestBulletsCleaned:
    def test_bullet_with_bold_is_clean(self):
        from app.gateway.content.ppt_pipeline import _parse_doc

        slides = _parse_doc("# 主题\n\n- **关键** 结论\n")
        flat = [b for s in slides for b in s.get("bullets", [])]
        assert flat == ["关键 结论"]


class TestSafePptxName:
    def test_title_drives_filename(self):
        from app.gateway.content.ppt_pipeline import _safe_pptx_name

        assert _safe_pptx_name("2026 培训方案") == "2026 培训方案.pptx"

    def test_path_chars_stripped_and_fallback(self):
        from app.gateway.content.ppt_pipeline import _safe_pptx_name

        assert "/" not in _safe_pptx_name("a/b\\c:d")
        assert _safe_pptx_name("///") == "output.pptx"
        assert _safe_pptx_name(None) == "output.pptx"


@pytest.mark.asyncio
async def test_ppt_gen_rejects_unknown_template(tmp_path):
    from app.gateway.content.ppt_service import ContentTaskService

    svc = ContentTaskService.__new__(ContentTaskService)

    async def progress(**kwargs):
        return None

    progress.set_result = lambda **kw: None  # type: ignore[attr-defined]
    with pytest.raises(ValueError, match="Unknown template"):
        await svc._run_ppt_gen("ct_x", {"doc_json": "{}", "template": "nope"}, tmp_path, progress)
