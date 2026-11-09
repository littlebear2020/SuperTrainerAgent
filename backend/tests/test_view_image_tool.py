import base64
import importlib
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from deerflow.tools.builtins.view_image_tool import view_image_tool

view_image_module = importlib.import_module("deerflow.tools.builtins.view_image_tool")

PNG_BYTES = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")


def _make_thread_data(tmp_path: Path) -> dict[str, str]:
    user_data = tmp_path / "threads" / "thread-1" / "user-data"
    workspace = user_data / "workspace"
    uploads = user_data / "uploads"
    outputs = user_data / "outputs"
    for directory in (workspace, uploads, outputs):
        directory.mkdir(parents=True)

    return {
        "workspace_path": str(workspace),
        "uploads_path": str(uploads),
        "outputs_path": str(outputs),
    }


def _make_runtime(thread_data: dict[str, str]) -> SimpleNamespace:
    return SimpleNamespace(
        state={"thread_data": thread_data},
        context={"thread_id": "thread-1"},
        config={},
    )


def _message_content(result) -> str:
    return result.update["messages"][0].content


def test_view_image_rejects_external_absolute_path(tmp_path: Path) -> None:
    thread_data = _make_thread_data(tmp_path)
    outside_image = tmp_path / "outside.png"
    outside_image.write_bytes(PNG_BYTES)

    result = view_image_tool.func(
        runtime=_make_runtime(thread_data),
        image_path=str(outside_image),
        tool_call_id="tc-external",
    )

    assert "Only image paths under /mnt/user-data" in _message_content(result)
    assert "viewed_images" not in result.update


def test_view_image_reads_virtual_uploads_path(tmp_path: Path) -> None:
    thread_data = _make_thread_data(tmp_path)
    image_path = Path(thread_data["uploads_path"]) / "sample.png"
    image_path.write_bytes(PNG_BYTES)

    result = view_image_tool.func(
        runtime=_make_runtime(thread_data),
        image_path="/mnt/user-data/uploads/sample.png",
        tool_call_id="tc-uploads",
    )

    assert _message_content(result) == "Successfully read image"
    viewed_image = result.update["viewed_images"]["/mnt/user-data/uploads/sample.png"]
    # R-11 (2026-08-13): state carries metadata only — no base64 in checkpoints.
    assert "base64" not in viewed_image
    assert viewed_image["mime_type"] == "image/png"
    assert viewed_image["size"] == len(PNG_BYTES)
    assert viewed_image["actual_path"] == str(image_path)


def test_view_image_rejects_spoofed_extension(tmp_path: Path) -> None:
    thread_data = _make_thread_data(tmp_path)
    image_path = Path(thread_data["uploads_path"]) / "not-really.png"
    image_path.write_bytes(b"not an image")

    result = view_image_tool.func(
        runtime=_make_runtime(thread_data),
        image_path="/mnt/user-data/uploads/not-really.png",
        tool_call_id="tc-spoofed",
    )

    assert "contents do not match" in _message_content(result)
    assert "viewed_images" not in result.update


def test_view_image_rejects_mismatched_magic_bytes(tmp_path: Path) -> None:
    thread_data = _make_thread_data(tmp_path)
    image_path = Path(thread_data["uploads_path"]) / "jpeg-named-png.png"
    image_path.write_bytes(b"\xff\xd8\xff\xe0fake-jpeg")

    result = view_image_tool.func(
        runtime=_make_runtime(thread_data),
        image_path="/mnt/user-data/uploads/jpeg-named-png.png",
        tool_call_id="tc-mismatch",
    )

    assert "file extension indicates image/png" in _message_content(result)
    assert "viewed_images" not in result.update


def test_view_image_rejects_oversized_image(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    thread_data = _make_thread_data(tmp_path)
    image_path = Path(thread_data["uploads_path"]) / "sample.png"
    image_path.write_bytes(PNG_BYTES)
    monkeypatch.setattr(view_image_module, "_MAX_IMAGE_BYTES", len(PNG_BYTES) - 1)

    result = view_image_tool.func(
        runtime=_make_runtime(thread_data),
        image_path="/mnt/user-data/uploads/sample.png",
        tool_call_id="tc-oversized",
    )

    assert "图片太大" in _message_content(result)
    assert "viewed_images" not in result.update


def test_view_image_stores_metadata_for_large_image(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """D1 压缩已移至 middleware 按需读盘路径（R-11）——tool 只存元数据：
    大图入 state 的仍是原始 size + 文件真实 mime，无 base64。"""
    import io as _io

    # 用随机噪声构造大 PNG（纯色 PNG 压缩率极高，无法产生大文件）
    import numpy as np
    from PIL import Image as PILImage

    arr = np.random.randint(0, 256, (1500, 1500, 3), dtype=np.uint8)
    img = PILImage.fromarray(arr)
    buf = _io.BytesIO()
    img.save(buf, format="PNG")
    large_png = buf.getvalue()
    assert len(large_png) > 500_000  # 噪声 PNG 确实大

    thread_data = _make_thread_data(tmp_path)
    image_path = Path(thread_data["uploads_path"]) / "large.png"
    image_path.write_bytes(large_png)

    result = view_image_tool.func(
        runtime=_make_runtime(thread_data),
        image_path="/mnt/user-data/uploads/large.png",
        tool_call_id="tc-compress",
    )

    assert _message_content(result) == "Successfully read image"
    viewed = result.update["viewed_images"]["/mnt/user-data/uploads/large.png"]
    assert "base64" not in viewed
    assert viewed["size"] == len(large_png)
    assert viewed["mime_type"] == "image/png"  # 文件真实 mime（压缩不在此发生）
    assert viewed["actual_path"] == str(image_path)


def test_view_image_small_image_not_compressed(tmp_path: Path) -> None:
    """D1: 小图原样直发（不劣化）。"""
    thread_data = _make_thread_data(tmp_path)
    image_path = Path(thread_data["uploads_path"]) / "small.png"
    image_path.write_bytes(PNG_BYTES)

    result = view_image_tool.func(
        runtime=_make_runtime(thread_data),
        image_path="/mnt/user-data/uploads/small.png",
        tool_call_id="tc-small",
    )

    assert _message_content(result) == "Successfully read image"
    viewed = result.update["viewed_images"]["/mnt/user-data/uploads/small.png"]
    # R-11：只存元数据（mime/size/actual_path），无 base64
    assert "base64" not in viewed
    assert viewed["mime_type"] == "image/png"
    assert viewed["size"] == len(PNG_BYTES)
    assert viewed["actual_path"] == str(image_path)


def test_view_image_sanitizes_read_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    thread_data = _make_thread_data(tmp_path)
    image_path = Path(thread_data["uploads_path"]) / "sample.png"
    image_path.write_bytes(PNG_BYTES)

    def _open(*args, **kwargs):
        raise PermissionError(f"permission denied: {image_path}")

    monkeypatch.setattr("builtins.open", _open)

    result = view_image_tool.func(
        runtime=_make_runtime(thread_data),
        image_path="/mnt/user-data/uploads/sample.png",
        tool_call_id="tc-read-error",
    )

    message = _message_content(result)
    assert "Error reading image file" in message
    assert str(image_path) not in message
    assert str(Path(thread_data["uploads_path"])) not in message
    assert "/mnt/user-data/uploads/sample.png" in message
    assert "viewed_images" not in result.update


@pytest.mark.skipif(os.name == "nt", reason="symlink semantics differ on Windows")
def test_view_image_rejects_uploads_symlink_escape(tmp_path: Path) -> None:
    thread_data = _make_thread_data(tmp_path)
    outside_image = tmp_path / "outside-target.png"
    outside_image.write_bytes(PNG_BYTES)

    link_path = Path(thread_data["uploads_path"]) / "escape.png"
    try:
        link_path.symlink_to(outside_image)
    except OSError as exc:
        pytest.skip(f"symlink creation failed: {exc}")

    result = view_image_tool.func(
        runtime=_make_runtime(thread_data),
        image_path="/mnt/user-data/uploads/escape.png",
        tool_call_id="tc-symlink",
    )

    assert "path traversal" in _message_content(result)
    assert "viewed_images" not in result.update
