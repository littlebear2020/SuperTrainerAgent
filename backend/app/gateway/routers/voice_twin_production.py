"""Voice production task API router (script-to-audio, doc-extract-text, ppt-to-video)."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse

from app.gateway.voice_twin.production_schemas import (
    ProductionTaskResponse,
    ScriptToAudioRequest,
)
from app.gateway.voice_twin.production_service import ProductionService, strip_markdown
from deerflow.config.paths import get_paths
from deerflow.runtime.user_context import get_effective_user_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/voice-twins/production", tags=["Voice Production"])

MAX_PPT_SIZE = 100 * 1024 * 1024  # 100MB
MAX_DOC_SIZE = 50 * 1024 * 1024  # 50MB
DOC_ALLOWED_EXTS = (".doc", ".docx", ".txt", ".md")

DEFAULT_PREVIEW_TEXT = "你好，这是音色试听示例。您可以选择不同的音色来为内容配音，听听看哪个更合适。"
MAX_PREVIEW_CHARS = 100


def _preview_storage_dir() -> Path:
    """Root dir for cached voice preview clips. Created lazily.

    评审二轮（2026-10-06）：CWD 相对改 get_paths() 基址锚定——容器内 CWD=/app
    时旧写法落未挂载区，预览缓存随容器重建静默丢失（C8 同类第 6 处）。
    """
    p = get_paths().base_dir / "voice-twins" / "voice_previews"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _safe_cache_voice_id(voice_id: str) -> str:
    """缓存键白名单清洗（2026-08-09 D3 纵深防御）：原 `replace(":", "_")` 不洗
    ".." / "/"——当前 Starlette 路由匹配使这类值无法经 HTTP 送达，但缓存键
    直接拼路径，防御层不依赖路由行为。白名单外字符一律压成 "_"。
    """
    return re.sub(r"[^A-Za-z0-9_.-]", "_", voice_id)


def _get_service(request: Request) -> ProductionService:
    svc = getattr(request.app.state, "production_service", None)
    if svc is None:
        raise HTTPException(status_code=503, detail="Production service not available")
    return svc


def _get_voice_twin_ids(request: Request) -> tuple[ProductionService, str]:
    """Return (production_service, user_id) with common validation."""
    svc = _get_service(request)
    user_id = get_effective_user_id()
    return svc, user_id


def _reject_oversized_upload(request: Request, max_bytes: int) -> None:
    """Content-Length 预检：超限直接 413 不读 body（内存 DoS 防线，对齐 ppt-to-video）。

    chunked 传输无 Content-Length 时由读取后的长度检查兜底（既有逻辑）。
    """
    content_length = request.headers.get("content-length")
    if content_length and content_length.isdigit() and int(content_length) > max_bytes:
        raise HTTPException(status_code=413, detail=f"File too large (max {max_bytes // 1024 // 1024}MB)")


class _PseudoVoice:
    """Stand-in for a voice twin DB row, used for preset (360 TTS) voices that
    have no DB record. Field names mirror VoiceTwinRow so callers can treat both
    uniformly. Defined at module level (was created per-call inside _verify_voice).
    """

    voice_name: str = ""
    speaker_id: str = ""
    model_type: int = 0
    language: int = 0
    status: str = "ready"
    user_id: str = ""


@router.get("/voices/{voice_id}/preview")
async def preview_voice(request: Request, voice_id: str, text: str | None = None):
    """Generate (or serve cached) a short preview clip for a voice.

    GET (no CSRF concern). Caches to ``{base_dir}/voice-twins/voice_previews``
    keyed by ``{voice_id}_{md5(text)[:8]}.{ext}``; extension is determined by
    **actual content** (WAV→.wav, mp3→.mp3), not by request voice type — the
    degradation chain may produce a different format than requested. Read path
    tries both extensions. First miss synthesizes via
    ``ProductionService.preview_voice`` and writes atomically (uuid tmp + replace).
    """
    svc = _get_service(request)
    user_id = get_effective_user_id()

    preview_text = (text or DEFAULT_PREVIEW_TEXT)[:MAX_PREVIEW_CHARS]

    from app.gateway.voice_twin.preset_voices import is_doubao_voice_id, is_edge_voice_id, is_preset_voice_id

    is_cloud = is_preset_voice_id(voice_id) or is_doubao_voice_id(voice_id) or is_edge_voice_id(voice_id)

    voice_id_safe = _safe_cache_voice_id(voice_id)
    cache_key = f"{voice_id_safe}_{hashlib.md5(preview_text.encode('utf-8')).hexdigest()[:8]}"
    # 缓存读路径同时试 wav/mp3：preset 正常产 wav，但降级兜底（preset→edge）产 mp3，
    # 缓存键按**实际产物**扩展名落盘（A2），读侧必须两种都认。
    cache_dir = _preview_storage_dir()
    cached_candidates = [(cache_dir / f"{cache_key}.wav", "audio/wav"), (cache_dir / f"{cache_key}.mp3", "audio/mpeg")]

    # st_size > 0 (stricter than video-gen's plain exists()) avoids serving
    # half-written / 0-byte cache files left by past failed writes.
    for cached, cached_media in cached_candidates:
        if cached.exists() and cached.stat().st_size > 0:
            # 命中缓存也要过归属校验——否则知道 voice_id+文本 md5 即可取他人克隆音色试听，
            # 且音色删除后缓存仍外发（P2 安全）。preset/doubao/edge 无归属概念直接放行。
            if not is_cloud:
                repo = svc.voice_service.repo
                row = await repo.get_by_id(voice_id) if repo else None
                if row is None:
                    raise HTTPException(status_code=404, detail="Voice not found")
                if row.user_id != user_id:
                    raise HTTPException(status_code=403, detail="Not your voice twin")
            return FileResponse(str(cached), media_type=cached_media)

    try:
        audio_bytes, returned_media = await svc.preview_voice(voice_id, preview_text, user_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Voice not found")
    except PermissionError:
        raise HTTPException(status_code=403, detail="Not your voice twin")
    except ValueError as e:
        logger.warning("SEC-M12: voice preview rejected (%s): %s", voice_id, e)
        raise HTTPException(status_code=400, detail="Invalid voice preview request")
    except RuntimeError:
        raise HTTPException(status_code=503, detail="TTS service unavailable")

    # 按服务返回的实际媒体类型落盘（降级产物可能是 mp3，与请求音色推定的 wav 不同）。
    # uuid tmp 名 + replace：同 key 并发请求不互相截断（fix M2, 2026-08-10，对齐 D4）。
    actual_ext = "wav" if returned_media == "audio/wav" else "mp3"
    cached = cache_dir / f"{cache_key}.{actual_ext}"
    tmp = cached.with_name(f"{cached.name}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        tmp.write_bytes(audio_bytes)
        tmp.replace(cached)
    finally:
        tmp.unlink(missing_ok=True)
    return FileResponse(str(cached), media_type=returned_media)


@router.post("/script-to-audio", response_model=ProductionTaskResponse)
async def submit_script_to_audio(request: Request, body: ScriptToAudioRequest):
    """Submit a script-to-audio production task."""
    svc, user_id = _get_voice_twin_ids(request)
    voice = await _verify_voice(svc, body.voice_id, user_id)

    task = await svc.submit_script_to_audio(
        user_id=user_id,
        voice_id=body.voice_id,
        voice_name=voice.voice_name,
        text=body.text,
        speed_ratio=body.speed_ratio,
        output_format=body.output_format,
        title=body.title,
    )
    return ProductionTaskResponse.from_task_row(task)


async def _verify_voice(svc: ProductionService, voice_id: str, user_id: str):
    """Common voice validation for both cloned and preset voices."""
    from app.gateway.voice_twin.preset_voices import (
        get_preset_voice_name,
        is_doubao_voice_id,
        is_edge_voice_id,
        is_preset_voice_id,
        parse_doubao_voice_id,
        parse_edge_voice_id,
        parse_preset_voice_id,
    )

    if is_preset_voice_id(voice_id):
        name = get_preset_voice_name(voice_id) or f"360 预设音色 {parse_preset_voice_id(voice_id)}"
        voice = _PseudoVoice()
        voice.voice_name = name
        voice.speaker_id = voice_id
        voice.user_id = user_id
        return voice

    if is_doubao_voice_id(voice_id):
        try:
            speaker = parse_doubao_voice_id(voice_id)
        except ValueError:
            # D3：含路径字符的 speaker id 拒绝（400 而非 500）
            raise HTTPException(status_code=400, detail="Invalid doubao voice id")
        voice = _PseudoVoice()
        voice.voice_name = f"豆包音色 {speaker}"
        voice.speaker_id = voice_id
        voice.user_id = user_id
        return voice

    if is_edge_voice_id(voice_id):
        voice = _PseudoVoice()
        voice.voice_name = f"Edge 音色 {parse_edge_voice_id(voice_id)}"
        voice.speaker_id = voice_id
        voice.user_id = user_id
        return voice

    if svc.voice_service.repo is None:
        raise HTTPException(status_code=503, detail="Voice clone service not available")
    voice = await svc.voice_service.repo.get_by_id(voice_id)
    if voice is None:
        raise HTTPException(status_code=404, detail="Voice not found")
    if voice.user_id != user_id:
        raise HTTPException(status_code=403, detail="Not your voice twin")
    if voice.status != "ready":
        raise HTTPException(status_code=400, detail=f"Voice is not ready (status={voice.status})")
    return voice


async def _extract_text_from_upload(file: UploadFile) -> tuple[str, str]:
    """Extract and clean text from an uploaded document.

    Returns (cleaned_text, filename).
    Raises HTTPException on validation / extraction errors.
    """
    if not file.filename or not file.filename.lower().endswith(DOC_ALLOWED_EXTS):
        raise HTTPException(status_code=400, detail=f"Only {', '.join(DOC_ALLOWED_EXTS)} files are supported")

    content = await file.read()
    if len(content) > MAX_DOC_SIZE:
        raise HTTPException(status_code=413, detail=f"File too large (max {MAX_DOC_SIZE // 1024 // 1024}MB)")

    ext = Path(file.filename).suffix.lower()

    if ext in (".doc", ".docx"):
        from deerflow.utils.file_conversion import _convert_with_markitdown

        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            tmp.write(content)
            tmp_path = Path(tmp.name)
        try:
            text = await asyncio.to_thread(_convert_with_markitdown, tmp_path)
        finally:
            tmp_path.unlink(missing_ok=True)
    else:
        text = content.decode("utf-8", errors="replace")

    text = strip_markdown(text)

    if not text.strip():
        raise HTTPException(status_code=400, detail="Document contains no extractable text")

    return text, file.filename


@router.post("/doc-extract-text")
async def extract_doc_text(request: Request, file: UploadFile):
    """Extract text from a document file (preview only, no synthesis)."""
    _reject_oversized_upload(request, MAX_DOC_SIZE)
    text, filename = await _extract_text_from_upload(file)

    truncated = len(text) > 100_000
    if truncated:
        text = text[:100_000]

    return {
        "text": text,
        "char_count": len(text),
        "filename": filename,
        "truncated": truncated,
    }


# NOTE (2026-08-09 批次C)：POST /doc-to-audio 直传路由已删除——前端零调用，
# 产品决策保留「上传 → /doc-extract-text 服务端提取 → 预览编辑 → /script-to-audio」
# 两步流。_extract_text_from_upload 保留（/doc-extract-text 在用）。


@router.post("/ppt-to-video", response_model=ProductionTaskResponse)
async def submit_ppt_to_video(
    request: Request,
    file: UploadFile,
    voice_id: str = Form(),
    preset: str = Form("none"),
    subtitle: str = Form("false"),
    accept_layout_diff: str = Form("false"),
):
    """Submit a PPT-to-video production task."""
    svc, user_id = _get_voice_twin_ids(request)

    # Validate filename — only .pptx is supported (python-pptx cannot parse the
    # legacy .ppt binary format; accepting it caused silent extract-stage
    # failures). Convert .ppt → .pptx upstream (e.g. LibreOffice) if needed.
    if not file.filename or not file.filename.lower().endswith(".pptx"):
        raise HTTPException(status_code=400, detail="Only .pptx files are supported (legacy .ppt is not parseable; please save as .pptx)")

    # Pre-check size from Content-Length header (non-numeric/missing → 0).
    try:
        declared_size = int(request.headers.get("content-length", "0"))
    except (TypeError, ValueError):
        declared_size = 0
    if declared_size > MAX_PPT_SIZE:
        raise HTTPException(status_code=413, detail=f"File too large (max {MAX_PPT_SIZE // 1024 // 1024}MB)")

    voice = await _verify_voice(svc, voice_id, user_id)

    # Create task first so we have a directory to stream into
    subtitle_bool = subtitle.lower() in ("true", "1", "yes")
    accept_bool = accept_layout_diff.lower() in ("true", "1", "yes")
    ppt_title = Path(file.filename).stem if file.filename else None
    task = await svc.submit_ppt_to_video(
        user_id=user_id,
        voice_id=voice_id,
        voice_name=voice.voice_name,
        pptx_path="",
        title=ppt_title,
        preset=preset,
        subtitle=subtitle_bool,
        accept_layout_diff=accept_bool,
    )
    task_dir = Path(svc._storage_dir) / user_id / "tasks" / task.id
    task_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(file.filename).suffix
    source_path = task_dir / f"source{suffix}"

    async def _rollback_task() -> None:
        """Delete the task (DB row + task dir) so a failed upload does not leave
        a pending orphan that can never complete.
        """
        try:
            await svc.delete_task(task.id, user_id)
        except Exception:
            logger.exception("Failed to roll back orphan ppt-to-video task %s", task.id)

    # Stream upload to disk with size limit
    try:
        with open(source_path, "wb") as f:
            total = 0
            while True:
                chunk = await file.read(1024 * 1024)  # 1MB chunks
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_PPT_SIZE:
                    raise HTTPException(status_code=413, detail=f"File too large (max {MAX_PPT_SIZE // 1024 // 1024}MB)")
                f.write(chunk)
    except HTTPException:
        await _rollback_task()
        raise
    except Exception:
        await _rollback_task()
        raise

    # Update payload with pptx_path
    task_row = await svc.repo.get(task.id)
    if task_row:
        payload = json.loads(task_row.payload or "{}")
        payload["pptx_path"] = str(source_path)
        await svc.repo.update(task.id, payload=json.dumps(payload, ensure_ascii=False))

    # Launch the background pipeline with the saved file path
    svc.start_ppt_pipeline(task.id, user_id, voice_id, str(source_path), preset, subtitle_bool, title=ppt_title, voice_name=voice.voice_name, accept_layout_diff=accept_bool)

    return ProductionTaskResponse.from_task_row(await svc.repo.get(task.id, user_id=user_id) or task)


# ---------------------------------------------------------------------------
# Resume: re-submit a font-gate-failed PPT-to-video task with accept_layout_diff=True
# ---------------------------------------------------------------------------


async def _try_resume_ppt(svc: ProductionService, task_id: str, user_id: str) -> Any:
    """Resume a font-gate-failed ppt-to-video task.

    Returns the new TaskRow, or:
    - None if the task doesn't belong to *user_id* (router maps → 404)
    - Raises ValueError if not a font-gate failure (router maps → 409)
    - Raises FileNotFoundError if source pptx is gone (router maps → 410)
    """
    task = await svc.get_task(task_id, user_id)
    if task is None:
        return None  # 404

    if task.status != "failed" or "missing-font-detected" not in (task.error_message or ""):
        raise ValueError("Task is not eligible for resume (must be a font-gate failure)")

    # Locate source pptx
    task_dir = Path(svc._storage_dir) / user_id / "tasks" / task_id
    source_path = None
    for suffix in (".pptx", ".ppt"):
        candidate = task_dir / f"source{suffix}"
        if candidate.exists():
            source_path = candidate
            break
    if source_path is None:
        raise FileNotFoundError("源文件已清理，请重新上传")

    # Create new task with accept_layout_diff=True
    payload = json.loads(task.payload or "{}")
    new_task = await svc.submit_ppt_to_video(
        user_id=user_id,
        voice_id=payload.get("voice_id", ""),
        voice_name=payload.get("voice_name", ""),
        pptx_path="",
        preset=payload.get("preset", "none"),
        subtitle=payload.get("subtitle", False),
        title=payload.get("title"),
        accept_layout_diff=True,
    )

    # Copy source pptx to new task dir
    new_task_dir = Path(svc._storage_dir) / user_id / "tasks" / new_task.id
    new_task_dir.mkdir(parents=True, exist_ok=True)
    new_source = new_task_dir / f"source{source_path.suffix}"
    shutil.copy2(str(source_path), str(new_source))

    # Update payload with pptx_path
    task_row = await svc.repo.get(new_task.id)
    if task_row:
        new_payload = json.loads(task_row.payload or "{}")
        new_payload["pptx_path"] = str(new_source)
        await svc.repo.update(new_task.id, payload=json.dumps(new_payload, ensure_ascii=False))

    # Launch pipeline
    svc.start_ppt_pipeline(
        new_task.id,
        user_id,
        payload.get("voice_id", ""),
        str(new_source),
        payload.get("preset", "none"),
        payload.get("subtitle", False),
        title=payload.get("title"),
        voice_name=payload.get("voice_name"),
        accept_layout_diff=True,
    )

    return await svc.repo.get(new_task.id, user_id=user_id)


@router.post("/ppt-to-video/{task_id}/resume", response_model=ProductionTaskResponse)
async def resume_ppt_to_video(task_id: str, request: Request):
    """Resume a font-gate-failed PPT-to-video task with accept_layout_diff=True."""
    svc, user_id = _get_voice_twin_ids(request)
    try:
        new_task = await _try_resume_ppt(svc, task_id, user_id)
    except ValueError as e:
        logger.warning("SEC-M12: ppt resume rejected (%s): %s", task_id, e)
        raise HTTPException(status_code=409, detail="Task cannot be resumed in its current state")
    except FileNotFoundError as e:
        logger.warning("SEC-M12: ppt resume target missing (%s): %s", task_id, e)
        raise HTTPException(status_code=410, detail="Task not available for resume")

    if new_task is None:
        raise HTTPException(status_code=404, detail="Task not found")

    return ProductionTaskResponse.from_task_row(new_task)


@router.get("", response_model=list[ProductionTaskResponse])
async def list_production_tasks(request: Request):
    """List all production tasks for the current user."""
    svc, user_id = _get_voice_twin_ids(request)
    tasks = await svc.list_tasks(user_id)
    responses = [ProductionTaskResponse.from_task_row(t) for t in tasks]
    from app.gateway._shared.task_cost import attach_task_costs

    await attach_task_costs(responses)
    return responses


@router.get("/{task_id}", response_model=ProductionTaskResponse)
async def get_production_task(request: Request, task_id: str):
    """Get status of a production task."""
    svc, user_id = _get_voice_twin_ids(request)
    task = await svc.get_task(task_id, user_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    response = ProductionTaskResponse.from_task_row(task)
    from app.gateway._shared.task_cost import attach_task_costs

    await attach_task_costs([response])
    return response


@router.get("/{task_id}/download")
async def download_production_output(request: Request, task_id: str):
    """Download the output file of a completed task."""
    svc, user_id = _get_voice_twin_ids(request)
    task = await svc.get_task(task_id, user_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.status != "ready":
        raise HTTPException(status_code=400, detail=f"Task is not ready (status={task.status})")

    output_path = svc.get_output_path(task_id, user_id)
    if output_path is None or not output_path.exists():
        raise HTTPException(status_code=404, detail="Output file not found")

    payload = json.loads(task.payload or "{}")
    raw_title = payload.get("title") or payload.get("voice_name", "output")
    safe_title = re.sub(r'[\\/*?:"<>|]', "", raw_title).strip() or "output"
    suffix = output_path.suffix.lstrip(".")
    mime_map = {"mp3": "audio/mpeg", "wav": "audio/wav", "ogg": "audio/ogg", "mp4": "video/mp4"}
    media_type = mime_map.get(suffix, "application/octet-stream")
    filename = f"{safe_title}.{suffix}"

    return FileResponse(str(output_path), media_type=media_type, filename=filename)


@router.get("/{task_id}/stream")
async def stream_production_output(request: Request, task_id: str):
    """Stream the output file for inline preview (supports Range requests for video/audio)."""

    svc, user_id = _get_voice_twin_ids(request)
    task = await svc.get_task(task_id, user_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.status != "ready":
        raise HTTPException(status_code=400, detail=f"Task is not ready (status={task.status})")

    output_path = svc.get_output_path(task_id, user_id)
    if output_path is None or not output_path.exists():
        raise HTTPException(status_code=404, detail="Output file not found")

    file_size = output_path.stat().st_size
    suffix = output_path.suffix.lstrip(".")
    mime_map = {"mp3": "audio/mpeg", "wav": "audio/wav", "ogg": "audio/ogg", "mp4": "video/mp4"}
    media_type = mime_map.get(suffix, "application/octet-stream")

    # Parse Range header
    range_header = request.headers.get("range", "")
    if range_header:
        match = re.match(r"bytes=(\d+)-(\d*)", range_header)
        if not match:
            raise HTTPException(status_code=416, detail="Invalid range")
        start = int(match.group(1))
        end = int(match.group(2)) if match.group(2) else file_size - 1
        if start >= file_size:
            raise HTTPException(status_code=416, detail="Range not satisfiable")
        # Clamp end to the last byte rather than 416'ing a satisfiable range —
        # clients commonly send bytes=0-<large> or bytes=N-<past-eof>, which
        # HTTP permits (RFC 7233 §4.2: end >= size → serve to end of rep).
        if end >= file_size:
            end = file_size - 1

        chunk_size = 8192

        async def _stream_range():
            with open(output_path, "rb") as f:
                f.seek(start)
                remaining = end - start + 1
                while remaining > 0:
                    read_size = min(chunk_size, remaining)
                    data = f.read(read_size)
                    if not data:
                        break
                    remaining -= len(data)
                    yield data

        headers = {
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Accept-Ranges": "bytes",
            "Content-Length": str(end - start + 1),
            "Content-Type": media_type,
        }
        return StreamingResponse(_stream_range(), status_code=206, headers=headers)

    # No range: stream full file
    chunk_size = 65536

    async def _stream_full():
        with open(output_path, "rb") as f:
            while True:
                data = f.read(chunk_size)
                if not data:
                    break
                yield data

    return StreamingResponse(
        _stream_full(),
        media_type=media_type,
        headers={
            "Content-Length": str(file_size),
            "Accept-Ranges": "bytes",
            "Content-Type": media_type,
        },
    )


@router.delete("/{task_id}")
async def delete_production_task(request: Request, task_id: str):
    """Cancel (if running) and delete a production task."""
    svc, user_id = _get_voice_twin_ids(request)
    deleted = await svc.delete_task(task_id, user_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"success": True}


@router.post("/maintenance/cleanup")
async def run_storage_cleanup(request: Request):
    """Evict stale voice-preview caches and orphan temp files.

    Intended to be called on a schedule (cron / periodic task). Sweeps the
    shared preview cache (``voice_previews/``, >7 days) and the clone-service
    temp dir (>24h). Requires an authenticated session like the other routes.
    """
    get_effective_user_id()  # auth gate
    voice_svc = getattr(request.app.state, "voice_clone_service", None)
    if voice_svc is None:
        raise HTTPException(status_code=503, detail="Voice clone service not available")
    counts = voice_svc.cleanup_storage(preview_dir=_preview_storage_dir())
    return {"success": True, "evicted": counts}
