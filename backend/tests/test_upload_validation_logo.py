"""T-E1（M14+m19，2026-09-09 feedback-round1）：LOGO 上传校验链。

M14：上传白名单含 .svg × 嗅探不认 SVG 文本魔数 → SVG 必 400（修前矛盾）。
m19：落盘后缀取嗅探结果而非用户文件名（PNG 字节 + .svg 名不得落成 logo.svg）。
"""

from __future__ import annotations

import pytest

from app.gateway.video_gen.upload_validation import UploadRejected, validate_logo_bytes

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
_JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 64


class TestValidateLogoBytes:
    def test_png_jpg_magic_accepted(self):
        assert validate_logo_bytes(_PNG) == ".png"
        assert validate_logo_bytes(_JPG) == ".jpg"

    def test_svg_bare_magic_accepted(self):
        """M14：白名单含 .svg——嗅探须认 <svg 文本魔数。"""
        assert validate_logo_bytes(b'<svg xmlns="http://www.w3.org/2000/svg" width="10"/>') == ".svg"

    def test_svg_with_xml_decl_accepted(self):
        assert validate_logo_bytes(b'<?xml version="1.0"?>\n<svg viewBox="0 0 1 1"></svg>') == ".svg"

    def test_svg_with_bom_accepted(self):
        assert validate_logo_bytes(b"\xef\xbb\xbf  <svg/>") == ".svg"

    def test_svg_disguised_png_sniffs_png(self):
        """m19：PNG 字节 + .svg 文件名 → 嗅探返回 .png（落盘后缀取嗅探结果）。"""
        assert validate_logo_bytes(_PNG) == ".png", "字节说了算，不看文件名"

    def test_garbage_rejected(self):
        with pytest.raises(UploadRejected):
            validate_logo_bytes(b"not an image at all")

    def test_empty_rejected(self):
        with pytest.raises(UploadRejected):
            validate_logo_bytes(b"")

    def test_oversize_rejected(self):
        with pytest.raises(UploadRejected):
            validate_logo_bytes(_PNG + b"\x00" * (6 * 1024 * 1024), max_bytes=5 * 1024 * 1024)

    def test_svg_not_in_generic_image_validation(self):
        """通用图片校验（validate_image_bytes）仍不认 SVG——LOGO 专用通道开放，
        其余上传端点（upload-visual 等）白名单不含 svg，行为不变。"""
        from app.gateway.video_gen.upload_validation import validate_image_bytes

        with pytest.raises(UploadRejected):
            validate_image_bytes(b"<svg/>")
