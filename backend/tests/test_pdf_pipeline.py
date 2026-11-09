"""B4-T2: PDF pipeline tests -- task_media_type + filename sanitization + image replacement.

Note: build_pdf_from_doc requires weasyprint (system libs), so the full pipeline
is tested with mocked weasyprint. Pure functions (_safe_pdf_name,
_replace_images_with_file_paths) are tested directly.
"""

from __future__ import annotations

from pathlib import Path

from app.gateway.content.pdf_pipeline import (
    _replace_images_with_file_paths,
    _resolve_local_image_src,
    _safe_pdf_name,
    task_media_type,
)

# ── task_media_type ──────────────────────────────────────────────────────


def test_task_media_type_pdf_gen():
    assert task_media_type("pdf_gen") == "application/pdf"


def test_task_media_type_ppt_gen():
    assert task_media_type("ppt_gen") == "application/vnd.openxmlformats-officedocument.presentationml.presentation"


def test_task_media_type_unknown_falls_back():
    assert task_media_type("unknown") == "application/octet-stream"


# ── _safe_pdf_name (C4 fix: path traversal prevention) ──────────────────


def test_safe_pdf_name_normal_title():
    assert _safe_pdf_name("正常标题") == "正常标题.pdf"


def test_safe_pdf_name_path_traversal_blocked():
    """C4: path separators must be replaced so the filename can't escape out_dir."""
    result = _safe_pdf_name("../../tmp/evil")
    assert "/" not in result
    assert "\\" not in result
    assert result.endswith(".pdf")


def test_safe_pdf_name_none_returns_default():
    assert _safe_pdf_name(None) == "output.pdf"


def test_safe_pdf_name_empty_returns_default():
    assert _safe_pdf_name("") == "output.pdf"


def test_safe_pdf_name_strips_path_chars():
    result = _safe_pdf_name('a\\b/c:d*e?f"g<h>|k')
    assert "/" not in result
    assert "\\" not in result
    assert result.endswith(".pdf")


# ── _resolve_local_image_src ────────────────────────────────────────────


def test_resolve_local_image_path_traversal_blocked():
    """C4: filename with path traversal is rejected."""
    images_dir = Path("/tmp/test-images")
    # Path traversal attempts
    assert _resolve_local_image_src("/api/writer/assets/images/raw/../../../etc/passwd", images_dir) is None
    assert _resolve_local_image_src("/api/writer/assets/images/raw/../secret.png", images_dir) is None


def test_resolve_local_image_non_local_returns_none():
    """Remote URLs should return None (degraded to placeholder)."""
    images_dir = Path("/tmp/test-images")
    assert _resolve_local_image_src("https://example.com/image.png", images_dir) is None


# ── _replace_images_with_file_paths (I8 fix: full tag replacement) ──────


def test_replace_images_remote_produces_clean_html():
    """I8: replacing a remote image should not leave orphaned attributes."""
    html = '<img src="https://example.com/img.png" alt="remote" />'
    result = _replace_images_with_file_paths(html, Path("/tmp"))
    # No orphaned attributes from the original tag
    assert 'alt="remote"' not in result
    assert "/>" not in result
    # Placeholder div should be present
    assert "[图片:" in result


def test_replace_images_no_src_tag_preserved():
    """An img tag without src should be preserved as-is."""
    html = "<img width='100'>"
    result = _replace_images_with_file_paths(html, Path("/tmp"))
    assert "<img" in result


def test_replace_images_multiple_tags():
    """Multiple img tags should all be processed."""
    html = '<p><img src="https://a.com/1.png"> text <img src="https://b.com/2.png"></p>'
    result = _replace_images_with_file_paths(html, Path("/tmp"))
    assert result.count("[图片:") == 2


def test_replace_images_local_preserves_trailing_text(tmp_path):
    """Regression: local image replacement must close the img tag so that
    text after the image is not swallowed as attribute garbage.

    The regex matches the entire <img> tag — the replacement string must
    include a closing /> or subsequent content gets eaten.
    """
    # Create a fake local image file so _resolve_local_image_src finds it
    img_file = tmp_path / "test.png"
    img_file.write_bytes(b"\x89PNG fake")

    html = '<p>前文<img src="/api/writer/assets/images/raw/test.png" />后文</p>'
    result = _replace_images_with_file_paths(html, tmp_path)

    # The img tag must be properly closed
    assert " />" in result or '"/>' in result
    # Trailing text must survive
    assert "后文" in result
    assert "</p>" in result


# ── build_pdf_from_doc (mocked weasyprint) ──────────────────────────────


def test_build_pdf_from_doc_markdown_passthrough():
    """6c: markdown input should produce HTML with the content (mocked weasyprint)."""
    import sys
    import tempfile
    from unittest.mock import MagicMock

    # Pre-populate sys.modules so weasyprint's __init__ (which checks system
    # libs) is never executed. build_pdf_from_doc does `from weasyprint import HTML`
    # which will get our mock instead.
    mock_weasyprint = MagicMock()
    mock_html_instance = MagicMock()
    mock_html_instance.write_pdf.return_value = None
    mock_weasyprint.HTML.return_value = mock_html_instance
    original_module = sys.modules.get("weasyprint")
    sys.modules["weasyprint"] = mock_weasyprint

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            from app.gateway.content.pdf_pipeline import build_pdf_from_doc

            # Create the output file so stat() works
            out_file = Path(tmpdir, "测试文档.pdf")
            out_file.touch()

            result = build_pdf_from_doc("# 标题\n\n正文内容", tmpdir, "测试文档")

            assert "output_path" in result
            # The HTML passed to weasyprint should contain the content
            call_args = mock_weasyprint.HTML.call_args
            html_content = call_args.kwargs.get("string", "")
            assert "标题" in html_content
            assert "正文内容" in html_content
    finally:
        if original_module is not None:
            sys.modules["weasyprint"] = original_module
        else:
            sys.modules.pop("weasyprint", None)


# ── SEC-H2 (批次 2 项 2.4): SSRF 盲点收敛 — parser 级重写 ────────────────


def test_sec_h2_single_quoted_img_src_placeholder(tmp_path):
    """单引号 src 旧正则漏掉 → WeasyPrint 会真的去 fetch；现在占位。"""
    from app.gateway.content.pdf_pipeline import _replace_images_with_file_paths

    out = _replace_images_with_file_paths("<img src='http://internal/x.png'>", tmp_path)
    assert "http://internal/x.png" not in out.replace("[图片: ", "") or "[图片: http://internal/x.png]" in out
    assert "file://" not in out


def test_sec_h2_unquoted_img_src_placeholder(tmp_path):
    from app.gateway.content.pdf_pipeline import _replace_images_with_file_paths

    out = _replace_images_with_file_paths("<img src=http://internal/x.png>", tmp_path)
    assert "[图片: http://internal/x.png]" in out


def test_sec_h2_raw_style_block_dropped(tmp_path):
    """raw <style>（markdown 透传）里的 CSS url() 是主要盲 SSRF 向量。"""
    from app.gateway.content.pdf_pipeline import _replace_images_with_file_paths

    out = _replace_images_with_file_paths(
        '<style>body{background:url("http://169.254.169.254/latest/meta-data")}</style><p>hi</p>',
        tmp_path,
    )
    assert "style" not in out
    assert "169.254" not in out
    assert "<p>hi</p>" in out


def test_sec_h2_link_tag_dropped(tmp_path):
    from app.gateway.content.pdf_pipeline import _replace_images_with_file_paths

    out = _replace_images_with_file_paths('<link rel="stylesheet" href="http://internal/style.css"><p>hi</p>', tmp_path)
    assert "link" not in out
    assert "style.css" not in out
    assert "<p>hi</p>" in out


def test_sec_h2_inline_style_url_stripped(tmp_path):
    from app.gateway.content.pdf_pipeline import _replace_images_with_file_paths

    out = _replace_images_with_file_paths("<div style=\"background:url('http://internal/bg.png');color:red\">t</div>", tmp_path)
    assert "bg.png" not in out
    assert "color:red" in out  # 其余样式保留


def test_sec_h2_media_embed_attrs_dropped(tmp_path):
    from app.gateway.content.pdf_pipeline import _replace_images_with_file_paths

    out = _replace_images_with_file_paths('<video poster="http://internal/p.jpg" src="http://internal/v.mp4" controls></video>', tmp_path)
    assert "<video" in out and "poster" not in out and "v.mp4" not in out


def test_sec_h2_anchor_href_preserved(tmp_path):
    """<a href> 不被 WeasyPrint fetch（仅可点击元数据），保留不动。"""
    from app.gateway.content.pdf_pipeline import _replace_images_with_file_paths

    out = _replace_images_with_file_paths('<a href="https://example.com/page">link</a>', tmp_path)
    assert 'href="https://example.com/page"' in out


def test_sec_h2_text_entities_round_trip(tmp_path):
    """文本里的 < / & 不得经 parser 反转义后重新引入标签。"""
    from app.gateway.content.pdf_pipeline import _replace_images_with_file_paths

    out = _replace_images_with_file_paths("<p>a &lt; b &amp; c</p>", tmp_path)
    assert "a &lt; b &amp; c" in out


# ── SEC-H2 二轮复核（2026-09-04，tinyhtml5 树级实测四向量）──────────────


def test_sec_h2_selfclosing_style_dropped(tmp_path):
    """HTML5 忽略非 void 元素的自闭合斜杠——<style/> 仍开 style 元素。"""
    from app.gateway.content.pdf_pipeline import _replace_images_with_file_paths

    out = _replace_images_with_file_paths('<style/>body{background:url("http://169.254.169.254/x")}</style><p>hi</p>', tmp_path)
    assert "style" not in out
    assert "169.254" not in out
    assert "<p>hi</p>" in out


def test_sec_h2_cdata_smuggling_dropped(tmp_path):
    """CDATA 段（foreign content 内原样生效）整段丢弃，不回吐。"""
    from app.gateway.content.pdf_pipeline import _replace_images_with_file_paths

    out = _replace_images_with_file_paths('<svg><![CDATA[<style>body{background:url("http://internal/c")}</style>]]></svg><p>hi</p>', tmp_path)
    assert "CDATA" not in out
    assert "http://internal" not in out
    assert "url(" not in out


def test_sec_h2_img_without_src_strips_inline_style_url(tmp_path):
    """无 src 的 <img> 不再整签透传——内联 style 的 url() 必须剥除。"""
    from app.gateway.content.pdf_pipeline import _replace_images_with_file_paths

    out = _replace_images_with_file_paths("""<img style="background:url('http://internal/bg.png')" alt="x">""", tmp_path)
    assert "bg.png" not in out
    assert "url(" not in out
    assert "alt" in out  # 其余属性保留


def test_sec_h2_presentational_background_attr_stripped(tmp_path):
    """WeasyPrint 把 presentational background 属性转成 background-image
    发 fetch——全元素剥除（body/table/td）。"""
    from app.gateway.content.pdf_pipeline import _replace_images_with_file_paths

    out = _replace_images_with_file_paths(
        '<body background="http://internal/bgtile.png"><table background="http://internal/t.png"><tr><td background="http://internal/d.png">x</td></tr></table></body>',
        tmp_path,
    )
    assert "background=" not in out
    assert "http://internal" not in out
    assert "<td>x</td>" in out


def test_sec_h2_tree_level_no_fetchables(tmp_path):
    """树级验收（与审查方同款方法）：重写器输出经 tinyhtml5（WeasyPrint 的
    HTML5 解析器）建树，断言无 style 元素/background 属性/内联 url()。"""
    import tinyhtml5

    from app.gateway.content.pdf_pipeline import _replace_images_with_file_paths

    payloads = [
        '<style/>body{background:url("http://169.254.169.254/x")}</style><p>hi</p>',
        '<svg><![CDATA[<style>body{background:url("http://internal/c")}</style>]]></svg><p>hi</p>',
        """<img style="background:url('http://internal/bg.png')" alt="x">""",
        '<body background="http://internal/bgtile.png"><p>hi</p>',
        '<table background="http://internal/t.png"><tr><td background="http://internal/d.png">x</td></tr></table>',
    ]
    for payload in payloads:
        out = _replace_images_with_file_paths(payload, tmp_path)
        doc = tinyhtml5.parse(out)
        for el in doc.iter():
            assert getattr(el, "tag", "") != "style", payload
            assert "background" not in (el.attrib or {}), payload
            assert "url(" not in (el.attrib or {}).get("style", ""), payload
