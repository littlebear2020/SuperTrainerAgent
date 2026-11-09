import mimetypes
from pathlib import Path
from typing import Annotated

from langchain.tools import InjectedToolCallId, ToolRuntime, tool
from langchain_core.messages import ToolMessage
from langgraph.types import Command
from langgraph.typing import ContextT

from deerflow.agents.thread_state import ThreadDataState, ThreadState
from deerflow.config.paths import VIRTUAL_PATH_PREFIX

_ALLOWED_IMAGE_VIRTUAL_ROOTS = (
    f"{VIRTUAL_PATH_PREFIX}/workspace",
    f"{VIRTUAL_PATH_PREFIX}/uploads",
    f"{VIRTUAL_PATH_PREFIX}/outputs",
)
_ALLOWED_IMAGE_VIRTUAL_ROOTS_TEXT = ", ".join(_ALLOWED_IMAGE_VIRTUAL_ROOTS)
_MAX_IMAGE_BYTES = 20 * 1024 * 1024

# D1: 超过此阈值的图片自动压缩（等比缩小 + JPEG 降质），把 vision 请求耗时
# 从 ~250s（5MB 噪声图实测）压到 <30s 可接受范围。小图（≤ 阈值）原样直发不劣化。
# 2026-08-13（R-11）：tool 只存元数据，压缩改由 ViewImageMiddleware 的按需
# 读盘路径执行（导入本模块的 _compress_image），此阈值语义不变。
_COMPRESSION_THRESHOLD = 2 * 1024 * 1024  # 2MB
_COMPRESS_MAX_DIMENSION = 2048  # 压缩后最长边不超过 2048px（足够看清屏幕截图/文档）
_COMPRESS_TARGET_BYTES = 1_500_000  # 压缩目标 1.5MB（留安全余量）
_EXTENSION_TO_MIME = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


def _is_allowed_image_virtual_path(image_path: str) -> bool:
    return any(image_path == root or image_path.startswith(f"{root}/") for root in _ALLOWED_IMAGE_VIRTUAL_ROOTS)


def _detect_image_mime(image_data: bytes) -> str | None:
    if image_data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if image_data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if len(image_data) >= 12 and image_data.startswith(b"RIFF") and image_data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _sanitize_image_error(error: Exception, thread_data: ThreadDataState | None) -> str:
    from deerflow.sandbox.tools import mask_local_paths_in_output

    return mask_local_paths_in_output(f"{type(error).__name__}: {error}", thread_data)


def _compress_image(image_data: bytes) -> tuple[bytes, str]:
    """D1: 压缩大图——等比缩小 + JPEG 降质迭代，返回 (compressed_bytes, "image/jpeg")。

    策略：先限制最长边到 2048px，再从 quality=85 起逐级降到 30，
    仍超标则继续缩尺寸。目标 < _COMPRESS_TARGET_BYTES。
    """
    import io

    from PIL import Image

    img = Image.open(io.BytesIO(image_data))
    # 转为 RGB（去掉 alpha 通道，JPEG 不支持透明）
    if img.mode != "RGB":
        img = img.convert("RGB")

    # 第一步：限制最长边
    max_dim = _COMPRESS_MAX_DIMENSION
    for _attempt in range(4):  # 最多缩 4 轮（2048→1024→512→256）
        if max(img.width, img.height) > max_dim:
            ratio = max_dim / max(img.width, img.height)
            img = img.resize((int(img.width * ratio), int(img.height * ratio)), Image.LANCZOS)

        # 第二步：逐级降 JPEG 质量
        for quality in (85, 70, 50, 30):
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=quality)
            data = buf.getvalue()
            if len(data) <= _COMPRESS_TARGET_BYTES:
                return data, "image/jpeg"

        # 仍未达标：再缩尺寸
        max_dim = int(max_dim * 0.7)

    # 兜底：最后一轮的 quality=30 结果
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=30)
    return buf.getvalue(), "image/jpeg"


@tool("view_image", parse_docstring=True)
def view_image_tool(
    runtime: ToolRuntime[ContextT, ThreadState],
    image_path: str,
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """Read an image file.

    Use this tool to read an image file and make it available for display.

    When to use the view_image tool:
    - When you need to view an image file.

    When NOT to use the view_image tool:
    - For non-image files (use present_files instead)
    - For multiple files at once (use present_files instead)

    Args:
        image_path: Absolute /mnt/user-data virtual path to the image file. Common formats supported: jpg, jpeg, png, webp.
    """
    from deerflow.sandbox.exceptions import SandboxRuntimeError
    from deerflow.sandbox.tools import (
        get_thread_data,
        resolve_and_validate_user_data_path,
        validate_local_tool_path,
    )

    thread_data = get_thread_data(runtime)

    if not _is_allowed_image_virtual_path(image_path):
        return Command(
            update={
                "messages": [
                    ToolMessage(
                        f"Error: Only image paths under {_ALLOWED_IMAGE_VIRTUAL_ROOTS_TEXT} are allowed",
                        tool_call_id=tool_call_id,
                    )
                ]
            },
        )

    try:
        validate_local_tool_path(image_path, thread_data, read_only=True)
        actual_path = resolve_and_validate_user_data_path(image_path, thread_data)
    except (PermissionError, SandboxRuntimeError) as e:
        return Command(
            update={"messages": [ToolMessage(f"Error: {str(e)}", tool_call_id=tool_call_id)]},
        )

    path = Path(actual_path)

    # Validate that the file exists
    if not path.exists():
        return Command(
            update={"messages": [ToolMessage(f"Error: Image file not found: {image_path}", tool_call_id=tool_call_id)]},
        )

    # Validate that it's a file (not a directory)
    if not path.is_file():
        return Command(
            update={"messages": [ToolMessage(f"Error: Path is not a file: {image_path}", tool_call_id=tool_call_id)]},
        )

    # Validate image extension
    expected_mime_type = _EXTENSION_TO_MIME.get(path.suffix.lower())
    if expected_mime_type is None:
        return Command(
            update={"messages": [ToolMessage(f"Error: Unsupported image format: {path.suffix}. Supported formats: {', '.join(_EXTENSION_TO_MIME)}", tool_call_id=tool_call_id)]},
        )

    # Detect MIME type from file extension
    mime_type, _ = mimetypes.guess_type(actual_path)
    if mime_type is None:
        mime_type = expected_mime_type

    try:
        image_size = path.stat().st_size
    except OSError as e:
        return Command(
            update={"messages": [ToolMessage(f"Error reading image metadata: {_sanitize_image_error(e, thread_data)}", tool_call_id=tool_call_id)]},
        )
    if image_size > _MAX_IMAGE_BYTES:
        size_mb = image_size / 1024 / 1024
        return Command(
            update={"messages": [ToolMessage(f"图片太大（{size_mb:.1f}MB），无法处理。请压缩到 20MB 以内后重试。", tool_call_id=tool_call_id)]},
        )

    # Read image file to validate contents (magic bytes + size)
    try:
        with open(actual_path, "rb") as f:
            image_data = f.read()
    except Exception as e:
        return Command(
            update={"messages": [ToolMessage(f"Error reading image file: {_sanitize_image_error(e, thread_data)}", tool_call_id=tool_call_id)]},
        )

    detected_mime_type = _detect_image_mime(image_data)
    if detected_mime_type is None:
        return Command(
            update={"messages": [ToolMessage("Error: File contents do not match a supported image format", tool_call_id=tool_call_id)]},
        )
    if detected_mime_type != expected_mime_type:
        return Command(
            update={"messages": [ToolMessage(f"Error: Image contents are {detected_mime_type}, but file extension indicates {expected_mime_type}", tool_call_id=tool_call_id)]},
        )
    mime_type = detected_mime_type

    # Store only lightweight metadata in state — NOT base64 — so the image
    # payload never lands in a checkpoint (#4138 / R-11, 2026-08-13). The
    # ViewImageMiddleware reads the file on-demand (with the D1 compression
    # still applied in its read path via _compress_image) right before the
    # model call, and removes the transient message again afterwards.
    new_viewed_images = {
        image_path: {
            "mime_type": mime_type,
            "size": image_size,
            "actual_path": str(actual_path),
        }
    }

    return Command(
        update={"viewed_images": new_viewed_images, "messages": [ToolMessage("Successfully read image", tool_call_id=tool_call_id)]},
    )
