"""Writer assets API — AI image generation and upload for document illustration.

No separate service class: the router reads config from app.state and reuses
the existing throttled provider singleton from video_gen_service.

Key design decisions:
- AI image generation goes through video_gen_service.providers.ai_image,
  which is wrapped by ThrottledAIImageProvider (QPS 1.0, max_retries=3,
  CircuitBreaker: 5 failures → 30s open). This protects the shared 360 API
  account from being rate-limited/banned by concurrent Writer requests.
- Per-user rate limiting: max 5 AI image requests per minute per user.
"""

import logging
import random
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.gateway.video_gen.pricing import record_image_cost

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/writer/assets", tags=["Writer Assets"])

# ── Per-user AI image rate limiting ────────────────────────────────────────
_USER_RATE: dict[str, list[float]] = {}
_RATE_MAX_REQUESTS = 5  # per window
_RATE_WINDOW = 60  # seconds


def _check_user_rate(user_id: str) -> bool:
    """Returns True if request allowed, False if rate limited."""
    now = time.time()
    timestamps = _USER_RATE.get(user_id, [])
    timestamps = [t for t in timestamps if now - t < _RATE_WINDOW]
    if len(timestamps) >= _RATE_MAX_REQUESTS:
        _USER_RATE[user_id] = timestamps
        return False
    timestamps.append(now)
    _USER_RATE[user_id] = timestamps
    return True


def _cleanup_rate_table() -> None:
    """Remove users with no active timestamps. Prevents memory leak."""
    now = time.time()
    stale_keys = [uid for uid, ts in _USER_RATE.items() if not ts or all(now - t >= _RATE_WINDOW for t in ts)]
    for k in stale_keys:
        del _USER_RATE[k]


def _get_user_id() -> str:
    """Get current user ID via the project-standard contextvar."""
    from deerflow.runtime.user_context import get_effective_user_id

    return get_effective_user_id()


# ── L2: AI image generation ───────────────────────────────────────────────


class GenerateImageRequest(BaseModel):
    prompt: str
    size: str = "1024x1024"


class GeneratedImage(BaseModel):
    url: str
    width: int
    height: int
    source: str


@router.post("/images/generate")
async def generate_image(
    request: Request,
    body: GenerateImageRequest,
) -> GeneratedImage:
    """AI text-to-image (360-Gemini) via throttled provider singleton.

    Reuses video_gen_service.providers.ai_image which is wrapped by
    ThrottledAIImageProvider (QPS 1.0, retry, circuit breaker).
    Per-user rate limit: 5 requests/minute.

    Error contract (W19): when video_gen is unconfigured or qihu_api_key is
    missing, returns 503 with a structured Chinese actionable ``detail``:
    ``{"message": str, "action": str, "reason": "video_gen_unconfigured" |
    "qihu_key_missing"}``. The frontend renders ``message`` + ``action`` +
    a retry button (not the raw HTTP text). The provider-reuse success path
    is unchanged.
    """
    user_id = _get_user_id()
    if not _check_user_rate(user_id):
        raise HTTPException(
            status_code=429,
            detail="AI 生图请求过于频繁（每分钟 5 次），请稍后再试",
        )

    # Probabilistic cleanup of stale rate-limit entries
    if random.random() < 0.01:
        _cleanup_rate_table()

    # Decouple the video_gen config-failure path (W19): read app.state directly
    # (no shared helper) so the AI 生图 tab gets a structured Chinese
    # actionable error.
    vg_svc = getattr(request.app.state, "video_gen_service", None)
    if vg_svc is None:
        raise HTTPException(
            status_code=503,
            detail={
                "message": "AI 生图未配置：需要视频生成模块支持",
                "action": "请联系管理员配置 video_gen 模块（含图像模型）后重启服务",
                "reason": "video_gen_unconfigured",
            },
        )

    config = vg_svc.config
    if not config.qihu_api_key:
        raise HTTPException(
            status_code=503,
            detail={
                "message": "AI 生图未配置：缺少图像模型 API 密钥",
                "action": "请联系管理员在配置中设置 qihu_api_key 后重启服务",
                "reason": "qihu_key_missing",
            },
        )

    provider = vg_svc.providers.ai_image
    try:
        result = await provider.generate_image(body.prompt, size=body.size)
        record_image_cost("writer", provider=getattr(result, "source", None))  # P1-8: 成本可见
        logger.info(
            "writer_ai_image user=%s prompt_len=%d source=%s",
            user_id,
            len(body.prompt),
            result.source,
        )
        return GeneratedImage(
            url=result.url,
            width=result.width,
            height=result.height,
            source=result.source,
        )
    except Exception as e:
        logger.warning("writer_ai_image failed user=%s error=%s", user_id, e)
        raise HTTPException(status_code=502, detail="图片生成失败（原因见服务端日志）") from e


# ── L3: Image upload (paste / drop — issue ①) ──────────────────────────────

_PROJECT_ROOT = Path(__file__).resolve().parents[4]
_WRITER_IMAGES_DIR = _PROJECT_ROOT / "backend" / ".deer-flow" / "writer-images"
_WRITER_IMAGE_URL_PREFIX = "/api/writer/assets/images/raw/"
_WRITER_IMAGE_MAX_BYTES = 10 * 1024 * 1024  # 10 MB


def _writer_image_dir() -> Path:
    """Writer uploaded-image storage dir.

    Wrapped in a function (rather than reading the module constant directly)
    so tests can monkeypatch it to a tmp dir without littering the repo.
    """
    return _WRITER_IMAGES_DIR


def _detect_writer_image_ext(head: bytes) -> str | None:
    """Detect an allowed image type from magic bytes. SVG is never allowed.

    Mirrors gallery's thumbnail sniffing — the type is derived from the file
    content, never the (forgeable) uploaded filename or Content-Type.
    """
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if head.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if head.startswith(b"GIF87a") or head.startswith(b"GIF89a"):
        return ".gif"
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return ".webp"
    return None


class UploadedImage(BaseModel):
    url: str
    width: int | None = None
    height: int | None = None
    source: str = "upload"


@router.post("/images/upload")
async def upload_image(
    file: UploadFile = File(...),
) -> UploadedImage:
    """Store a pasted/dropped image and return a servable URL.

    Accepts PNG/JPEG/GIF/WebP (type sniffed from magic bytes; SVG rejected),
    caps size at 10 MB, and applies the same per-user rate limit as AI image
    generation. Dimensions aren't read server-side (no Pillow) — the browser
    renders the natural size and the editor's resize handle captures the
    aspect ratio on the first drag.
    """
    user_id = _get_user_id()
    if not _check_user_rate(user_id):
        raise HTTPException(
            status_code=429,
            detail="Image upload rate limit reached (5/min). Please try again shortly.",
        )

    raw = await file.read()
    if len(raw) > _WRITER_IMAGE_MAX_BYTES:
        raise HTTPException(status_code=413, detail="Image too large (max 10MB)")
    ext = _detect_writer_image_ext(raw[:16])
    if ext is None:
        raise HTTPException(
            status_code=400,
            detail="Unsupported image type (allowed: PNG/JPEG/GIF/WebP; SVG disallowed)",
        )

    filename = f"{uuid.uuid4().hex}{ext}"
    storage = _writer_image_dir()
    storage.mkdir(parents=True, exist_ok=True)
    (storage / filename).write_bytes(raw)
    logger.info("writer_image_upload user=%s bytes=%d ext=%s", user_id, len(raw), ext)
    return UploadedImage(url=f"{_WRITER_IMAGE_URL_PREFIX}{filename}")


@router.get("/images/raw/{filename}")
async def serve_writer_image(filename: str):
    """Serve a previously uploaded writer image (referenced by markdown ``<img src>``)."""
    # Guard against path traversal: only a bare filename is allowed.
    if not filename or "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    path = _writer_image_dir() / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Image not found")
    return FileResponse(path)
