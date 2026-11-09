"""R1: seedance_ark 必须把本地 FS 路径转 data-URI 再塞进 Ark 请求（修 Finding 1）。

修前 raw url → Ark 4xx → 全黑屏静默"成功"。修后经 _to_data_uri 转 base64。
"""

import base64
import os
import tempfile

from app.gateway.video_gen.providers.seedance_ark import _normalize_image_url


def test_local_file_converted_to_data_uri() -> None:
    """本地文件 → data:image/png;base64,..."""
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        path = f.name
    try:
        result = _normalize_image_url(path)
        assert result is not None
        assert result.startswith("data:image/png;base64,")
        b64_part = result.split(",", 1)[1]
        assert base64.b64decode(b64_part) == b"\x89PNG\r\n\x1a\n"
    finally:
        os.unlink(path)


def test_http_url_passes_through() -> None:
    assert _normalize_image_url("https://example.com/img.png") == "https://example.com/img.png"


def test_data_uri_passes_through() -> None:
    assert _normalize_image_url("data:image/png;base64,iVBORw0KGgo=") == "data:image/png;base64,iVBORw0KGgo="


def test_none_returns_none() -> None:
    assert _normalize_image_url(None) is None
