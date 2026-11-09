"""Voice Twin Studio API router."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

from app.gateway.voice_twin.schemas import (
    MODELS_INFO,
    ModelTypeInfo,
    SynthesizeRequest,
    VoiceTwinResponse,
    VoiceTwinStatusResponse,
    VoiceTwinUploadRequest,
)
from app.gateway.voice_twin.service import VoiceCloneService
from deerflow.runtime.user_context import get_effective_user_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/voice-twins", tags=["Voice Twin"])


def _get_service(request: Request) -> VoiceCloneService:
    """Get the VoiceCloneService singleton from app.state."""
    svc = getattr(request.app.state, "voice_clone_service", None)
    if svc is None:
        raise HTTPException(status_code=503, detail="Voice clone service not available")
    return svc


@router.get("/models", response_model=list[ModelTypeInfo])
async def list_models():
    """List available voice clone model types."""
    return MODELS_INFO


@router.post("/prescreen")
async def prescreen_audio(request: Request, file: UploadFile, service: VoiceCloneService = Depends(_get_service)):
    """Pre-validate audio file before training submission."""
    from app.gateway.routers.voice_twin_production import _reject_oversized_upload

    _reject_oversized_upload(request, service.config.max_upload_bytes)
    content = await file.read()
    result = await service.prescreen(content, file.filename or "unknown.wav", user_id=get_effective_user_id())
    if result["status"] == "fail":
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.post("/upload", response_model=VoiceTwinResponse)
async def upload_voice_twin(
    request: Request,
    body: VoiceTwinUploadRequest,
    service: VoiceCloneService = Depends(_get_service),
):
    """Submit audio for voice clone training.

    Set ``speaker_id`` to a console-created ID (e.g. S_xxx) for pre-paid mode.
    Leave empty to auto-generate a post-paid custom_speaker_id.

    Parameters are passed in the JSON request body (validated by
    VoiceTwinUploadRequest) — not the URL query string — so that model_type/
    language ranges are enforced and voice_name never leaks into access logs.
    """
    user_id = get_effective_user_id()
    try:
        row = await service.submit_training(
            temp_file_id=body.temp_file_id,
            voice_name=body.voice_name,
            model_type=body.model_type,
            language=body.language,
            extra_params=body.extra_params,
            user_id=user_id,
            speaker_id=body.speaker_id,
        )
    except PermissionError as e:
        logger.warning("SEC-M12: voice training denied: %s", e)
        raise HTTPException(status_code=403, detail="Not your voice twin") from e
    except FileNotFoundError as e:
        logger.warning("SEC-M12: voice training target missing: %s", e)
        raise HTTPException(status_code=404, detail="Voice twin not found") from e
    except RuntimeError as e:
        logger.error("SEC-M12: voice training failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Voice twin training failed") from e

    return row


@router.get("", response_model=list[VoiceTwinResponse])
async def list_voices(request: Request, service: VoiceCloneService = Depends(_get_service)):
    """List all voice twins."""
    user_id = get_effective_user_id()
    rows = await service.repo.list_all(user_id=user_id)
    return [VoiceTwinResponse.model_validate(row).model_copy(update={"demo_available": service.get_demo_path(row.id) is not None}) for row in rows]


@router.get("/preset-voices")
async def list_preset_voices(request: Request):
    """列出共享音色目录（35 条，与 video-gen 同一数据源，2026-08-09 A1）。

    按凭证过滤引擎分组：qihu 组需 qihu_client（QIHU_API_KEY），doubao 组需
    doubao_client（VOLCENGINE_VOICE_ACCESS_KEY），edge-tts 组需 edge_client
    （config.yaml 的 edge_tts.enabled=true）。此前 qihu 未配置时**整个端点**
    静默返回 []——edge/doubao 音色也被连坐隐藏；现在只过滤对应分组。
    服务整体未启用（svc is None）才返回空。
    """
    svc: VoiceCloneService | None = getattr(request.app.state, "voice_clone_service", None)
    if svc is None:
        return []

    from app.gateway._shared.voice_catalog import CURATED_VOICES

    dropped: list[str] = []
    if svc.qihu_client is None:
        dropped.append("qihu")
    if svc.doubao_client is None:
        dropped.append("doubao")
    if getattr(svc, "edge_client", None) is None:
        dropped.append("edge-tts")  # edge_tts.enabled=false 的部署不列 edge 音色
    if dropped:
        logger.info("preset-voices: hiding provider groups without credentials: %s", ",".join(dropped))

    return [v.model_dump() for v in CURATED_VOICES if v.provider not in dropped]


@router.get("/{voice_id}/status", response_model=VoiceTwinStatusResponse)
async def get_status(request: Request, voice_id: str, service: VoiceCloneService = Depends(_get_service)):
    """Query training status (polls Volcengine if still training)."""
    user_id = get_effective_user_id()
    existing = await service.repo.get_by_id(voice_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Voice not found")
    if existing.user_id != user_id:
        raise HTTPException(status_code=403, detail="Not your voice twin")
    row = await service.poll_status(voice_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Voice not found")

    demo_path = service.get_demo_path(voice_id)
    return VoiceTwinStatusResponse(
        id=row.id,
        speaker_id=row.speaker_id,
        status=row.status,
        error_code=row.error_code,
        error_message=row.error_message,
        demo_available=demo_path is not None,
    )


@router.get("/{voice_id}/demo")
async def get_demo_audio(request: Request, voice_id: str, service: VoiceCloneService = Depends(_get_service)):
    """Serve cached demo audio."""
    user_id = get_effective_user_id()
    existing = await service.repo.get_by_id(voice_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Voice not found")
    if existing.user_id != user_id:
        raise HTTPException(status_code=403, detail="Not your voice twin")
    path = service.get_demo_path(voice_id)
    if path is None:
        raise HTTPException(status_code=404, detail="Demo audio not available")
    return FileResponse(path, media_type="audio/wav", filename=f"{voice_id}_demo.wav")


@router.post("/{voice_id}/synthesize")
async def synthesize_audio(
    request: Request,
    voice_id: str,
    body: SynthesizeRequest,
    service: VoiceCloneService = Depends(_get_service),
):
    """Synthesize text with a cloned voice."""
    user_id = get_effective_user_id()
    existing = await service.repo.get_by_id(voice_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Voice not found")
    if existing.user_id != user_id:
        raise HTTPException(status_code=403, detail="Not your voice twin")
    try:
        output_path = await service.synthesize(
            voice_id=voice_id,
            text=body.text,
            output_format=body.output_format,
            speed_ratio=body.speed_ratio,
        )
    except FileNotFoundError as e:
        logger.warning("SEC-M12: synthesize voice missing: %s", e)
        raise HTTPException(status_code=404, detail="Voice not found") from e
    except ValueError as e:
        logger.warning("SEC-M12: synthesize rejected: %s", e)
        raise HTTPException(status_code=400, detail="Invalid synthesize request") from e

    return FileResponse(output_path, media_type=f"audio/{body.output_format}")


@router.delete("/{voice_id}")
async def delete_voice(request: Request, voice_id: str, service: VoiceCloneService = Depends(_get_service)):
    """Delete a voice twin record and its on-disk artifacts."""
    user_id = get_effective_user_id()
    existing = await service.repo.get_by_id(voice_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Voice not found")
    if existing.user_id != user_id:
        raise HTTPException(status_code=403, detail="Not your voice twin")
    deleted = await service.repo.delete(voice_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Voice not found")
    # Remove leaked artifacts (original upload + cached demo). DB row is already
    # gone, so failures here must not affect the response — best-effort cleanup.
    try:
        service.purge_voice_files(voice_id)
    except Exception:
        logger.exception("Failed to purge files for voice %s", voice_id)
    return {"success": True}
