"""SEC-M7 (批次 2 项 2.6): video_gen 上传端点共享的字节级校验.

5 个上传端点（BGM / upload-visual / 角色三视图 / 风格锚图 / 道具）此前的
校验缺口：扩展名取自用户文件名、upload-visual 先 read 后查大小、三视图与
锚图/道具无大小上限无类型校验（bytes 直写）。本模块统一「大小上限 + 魔数
嗅探（识别不了 = 400 拒绝，不是静默保留原后缀）」语义；图片嗅探复用
`ai_image._sniff_image_ext`（B3），音频/视频魔数 helper 为本批新增。
"""

from __future__ import annotations

from app.gateway.video_gen.visual_sources.ai_image import _sniff_image_ext

# 每类上传的字节上限（三视图/锚图/道具是实拍照片场景，10MB/张；BGM 30MB、
# upload-visual 50MB 沿用端点既有上限）。
IMAGE_MAX_BYTES = 10 * 1024 * 1024
BGM_MAX_BYTES = 30 * 1024 * 1024
VISUAL_MAX_BYTES = 50 * 1024 * 1024


class UploadRejected(ValueError):
    """上传内容未过校验（调用方映射为 HTTP 400）。"""


def sniff_audio_ext(data: bytes) -> str:
    """按魔数识别音频字节格式；识别不了返回 ""（调用方拒绝）。

    覆盖 BGM 白名单族：mp3（ID3 / MPEG 帧同步，排除 layer=reserved 的 ADTS）、
    wav（RIFF/WAVE）、ogg（OggS）、flac（fLaC）、m4a（ftyp 容器）、
    webm 音频（EBML 头）、aac（ADTS 同步字）。
    """
    if data.startswith(b"ID3"):
        return ".mp3"
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return ".wav"
    if data.startswith(b"OggS"):
        return ".ogg"
    if data.startswith(b"fLaC"):
        return ".flac"
    if data[4:8] == b"ftyp":
        return ".m4a"
    if data[:4] == b"\x1a\x45\xdf\xa3":
        return ".webm"
    if data[:2] in (b"\xff\xf1", b"\xff\xf9"):
        # ADTS AAC 同步字（layer 位 = 00；MP3 帧同步 layer 位非零，见下）
        return ".aac"
    if len(data) >= 2 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0 and (data[1] & 0x06) != 0:
        return ".mp3"
    return ""


def sniff_visual_ext(data: bytes) -> str:
    """图片或视频魔数（upload-visual 白名单：jpeg/png/webp/mp4/webm）。"""
    image_ext = _sniff_image_ext(data)
    if image_ext in (".png", ".jpg", ".webp"):
        return image_ext
    if data[4:8] == b"ftyp" and data[8:12] != b"M4A ":
        # mp4 家族；M4A 品牌是纯音频，不属于 visual 白名单
        return ".mp4"
    if data[:4] == b"\x1a\x45\xdf\xa3":
        return ".webm"
    return ""


def validate_image_bytes(data: bytes, *, max_bytes: int = IMAGE_MAX_BYTES, label: str = "image") -> str:
    """大小上限 + 图片魔数校验；返回嗅探后缀（识别不了 = 拒绝）。"""
    if len(data) > max_bytes:
        raise UploadRejected(f"File too large (max {max_bytes // (1024 * 1024)}MB)")
    if not data:
        raise UploadRejected("Empty file")
    ext = _sniff_image_ext(data)
    if not ext:
        raise UploadRejected(f"Unsupported {label} type: not a recognized image (png/jpeg/webp/gif)")
    return ext


def validate_audio_bytes(data: bytes, *, max_bytes: int = BGM_MAX_BYTES, label: str = "audio") -> str:
    if len(data) > max_bytes:
        raise UploadRejected(f"File too large (max {max_bytes // (1024 * 1024)}MB)")
    if not data:
        raise UploadRejected("Empty file")
    ext = sniff_audio_ext(data)
    if not ext:
        raise UploadRejected(f"Unsupported {label} type: not a recognized audio format")
    return ext


def validate_visual_bytes(data: bytes, *, max_bytes: int = VISUAL_MAX_BYTES, label: str = "visual") -> str:
    if len(data) > max_bytes:
        raise UploadRejected(f"File too large (max {max_bytes // (1024 * 1024)}MB)")
    if not data:
        raise UploadRejected("Empty file")
    ext = sniff_visual_ext(data)
    if not ext:
        raise UploadRejected(f"Unsupported {label} type: not a recognized image/video format")
    return ext


def _sniff_svg(data: bytes) -> bool:
    """SVG 文本魔数（M14）：BOM/空白后直接 <svg，或 <?xml 声明 + 2KB 内出现 <svg。"""
    head = data[:512].lstrip(b"\xef\xbb\xbf \t\r\n")
    if head.startswith(b"<svg"):
        return True
    if head.startswith(b"<?xml"):
        return b"<svg" in data[:2048]
    return False


def validate_logo_bytes(data: bytes, *, max_bytes: int = IMAGE_MAX_BYTES, label: str = "logo") -> str:
    """LOGO 专用校验（T-E1/M14/m19）：PNG/JPG/WEBP 魔数 + SVG 文本魔数——
    上传白名单含 .svg 但通用图片嗅探不认 SVG → 修前 SVG 必 400。返回嗅探后缀
    （调用方落盘后缀取嗅探结果，防 PNG 字节+.svg 名落错文件——m19）。"""
    if len(data) > max_bytes:
        raise UploadRejected(f"File too large (max {max_bytes // (1024 * 1024)}MB)")
    if not data:
        raise UploadRejected("Empty file")
    ext = _sniff_image_ext(data)
    if ext in (".png", ".jpg", ".webp"):
        return ext
    if _sniff_svg(data):
        return ".svg"
    raise UploadRejected(f"Unsupported {label} type: not a recognized image (png/jpeg/webp/svg)")
