"""T5: fixed 模式文档上传 + 智能解析（POST /api/video/script/extract）。"""

from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException


@pytest.mark.asyncio
async def test_extract_from_docx(monkeypatch):
    """上传 .docx → 端点调 _extract_script_text → 返回文本。"""
    from app.gateway.routers import video_gen as router_mod

    upload = MagicMock()
    upload.filename = "script.docx"
    upload.content_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

    async def fake_extract(file):
        return "第一句。第二句。第三句。"

    monkeypatch.setattr(router_mod, "_extract_script_text", fake_extract)

    resp = await router_mod.extract_script_from_upload(upload)
    assert resp["text"] == "第一句。第二句。第三句。"


@pytest.mark.asyncio
async def test_reject_unsupported_type():
    """非允许类型（.exe）→ 400。"""
    from app.gateway.routers import video_gen as router_mod

    upload = MagicMock()
    upload.filename = "x.exe"
    upload.content_type = "application/octet-stream"

    with pytest.raises(HTTPException) as exc:
        await router_mod.extract_script_from_upload(upload)
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_extract_script_text_strips_visual_markers(monkeypatch):
    """_extract_script_text 剥画面/镜头标记（不念画面描述）。

    mock 上游 _extract_text_from_upload 返回含标记原文，让真实 _extract_script_text
    跑 _strip_visual_markers。
    """
    from app.gateway.routers import video_gen as router_mod

    upload = MagicMock()
    upload.filename = "s.txt"

    async def fake_upstream(file):
        return "旁白：第一句。【镜头特写】第二句。", "s.txt"

    monkeypatch.setattr(
        "app.gateway.routers.voice_twin_production._extract_text_from_upload",
        fake_upstream,
    )

    text = await router_mod._extract_script_text(upload)
    assert "镜头特写" not in text  # 画面标记被剥
    assert "第一句" in text


# ---------------------------------------------------------------------------
# P2-13: content_type-only check misfires for .md/.txt sent as octet-stream;
# fall back to filename extension. Also warn when parsed text exceeds the
# 20000-char script cap so the subsequent create doesn't 422 opaquely.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_accept_valid_extension_when_content_type_octet_stream(monkeypatch):
    """浏览器常把 .md/.txt 报成 application/octet-stream；按扩展名兜底接受。"""
    from app.gateway.routers import video_gen as router_mod

    upload = MagicMock()
    upload.filename = "notes.md"
    upload.content_type = "application/octet-stream"

    async def fake_extract(file):
        return "正文"

    monkeypatch.setattr(router_mod, "_extract_script_text", fake_extract)

    resp = await router_mod.extract_script_from_upload(upload)
    assert resp["text"] == "正文"


@pytest.mark.asyncio
async def test_long_script_returns_warning(monkeypatch):
    """解析文本超过 20000 字上限 → 响应附 warning（否则 create 会 422）。"""
    from app.gateway.routers import video_gen as router_mod

    upload = MagicMock()
    upload.filename = "big.txt"
    upload.content_type = "text/plain"

    async def fake_extract(file):
        return "字" * 21000

    monkeypatch.setattr(router_mod, "_extract_script_text", fake_extract)

    resp = await router_mod.extract_script_from_upload(upload)
    assert len(resp["text"]) == 21000
    assert "warning" in resp
    assert "20000" in resp["warning"]


def test_is_acceptable_script_upload_covers_octet_stream_with_valid_ext():
    """纯 helper：content_type 未识别时按扩展名兜底。"""
    from app.gateway.routers import video_gen as router_mod

    assert router_mod._is_acceptable_script_upload("notes.md", "application/octet-stream") is True
    assert router_mod._is_acceptable_script_upload("notes.txt", None) is True
    assert router_mod._is_acceptable_script_upload("doc.docx", "application/octet-stream") is True
    # whitelisted content_type always accepted
    assert router_mod._is_acceptable_script_upload("x", "text/markdown") is True
    # invalid ext + octet-stream → reject
    assert router_mod._is_acceptable_script_upload("photo.jpg", "application/octet-stream") is False
    # nothing to go on
    assert router_mod._is_acceptable_script_upload(None, None) is False


# ---------------------------------------------------------------------------
# M2-1: PDF support in /script/extract (route through convert_file_to_markdown).
# ---------------------------------------------------------------------------


def test_is_acceptable_script_upload_accepts_pdf():
    """M2-1: .pdf / application/pdf 须被白名单接受（修前被 400 拒）。"""
    from app.gateway.routers import video_gen as router_mod

    assert router_mod._is_acceptable_script_upload("doc.pdf", "application/pdf") is True
    # ext fallback when browser reports octet-stream
    assert router_mod._is_acceptable_script_upload("doc.pdf", "application/octet-stream") is True


@pytest.mark.asyncio
async def test_extract_script_text_pdf_routes_to_convert_file_to_markdown(monkeypatch, tmp_path):
    """M2-1: .pdf 走 convert_file_to_markdown（pymupdf4llm→markitdown，已 bundle），
    不走 voice_twin 的 _extract_text_from_upload（后者 DOC_ALLOWED_EXTS 不含 pdf）。"""
    from unittest.mock import AsyncMock

    from app.gateway.routers import video_gen as router_mod

    upload = MagicMock()
    upload.filename = "doc.pdf"
    upload.content_type = "application/pdf"
    upload.read = AsyncMock(return_value=b"%PDF-1.4 fake bytes")

    md_path = tmp_path / "doc.md"
    md_path.write_text("PDF 正文内容", encoding="utf-8")

    convert_calls: list = []

    async def fake_convert(path):
        convert_calls.append(path)
        return md_path

    monkeypatch.setattr("deerflow.utils.file_conversion.convert_file_to_markdown", fake_convert)

    upstream_calls: list = []

    async def fake_upstream(file):
        upstream_calls.append(file)
        return "should not be used", "doc.pdf"

    monkeypatch.setattr(
        "app.gateway.routers.voice_twin_production._extract_text_from_upload",
        fake_upstream,
    )

    text = await router_mod._extract_script_text(upload)
    assert "PDF 正文内容" in text
    assert convert_calls, "pdf 必须走 convert_file_to_markdown"
    assert not upstream_calls, "pdf 不应走 _extract_text_from_upload"
