"""Pydantic schemas for voice twin API endpoints."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class PrescreenResponse(BaseModel):
    """Response from audio pre-screening."""

    temp_file_id: str
    duration: float
    format: str
    needs_transcode: bool
    status: str  # "pass" or "fail"
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None


class VoiceTwinUploadRequest(BaseModel):
    """Request body for submitting a voice clone training task."""

    temp_file_id: str
    voice_name: str = Field(..., min_length=1, max_length=100)
    model_type: int = Field(default=5, ge=1, le=5)
    language: int = Field(default=0, ge=0, le=7)
    extra_params: dict | None = None
    speaker_id: str | None = None


class VoiceTwinResponse(BaseModel):
    """Voice twin record returned to frontend."""

    model_config = {"from_attributes": True}

    id: str
    user_id: str
    voice_name: str
    speaker_id: str
    model_type: int
    language: int
    status: str
    error_code: int | None = None
    error_message: str | None = None
    source_audio_format: str | None = None
    source_audio_duration: float | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    # ORM 无此列——list_voices 按磁盘 demos/{id}.wav 显式填充（防前端盲渲染 404 播放器）。
    demo_available: bool = False


class VoiceTwinStatusResponse(BaseModel):
    """Training status response."""

    id: str
    speaker_id: str
    status: str
    error_code: int | None = None
    error_message: str | None = None
    demo_available: bool = False


class SynthesizeRequest(BaseModel):
    """Request to synthesize text with a cloned voice."""

    text: str = Field(..., min_length=1, max_length=5000)
    output_format: str = Field(default="mp3", pattern="^(mp3|wav|ogg)$")
    speed_ratio: float = Field(default=1.0, ge=0.5, le=2.0)


class ModelTypeInfo(BaseModel):
    """Information about an available voice clone model type."""

    model_type: int
    name: str
    description: str
    languages: list[int]
    resource_id: str


MODELS_INFO: list[ModelTypeInfo] = [
    ModelTypeInfo(model_type=1, name="ICL 1.0", description="标准音色克隆，适合正式课件朗读", languages=[0, 1, 2, 3, 4, 5], resource_id="seed-icl-1.0"),
    ModelTypeInfo(model_type=2, name="DiT 标准版", description="音色复刻，不还原风格", languages=[0, 1, 2, 3, 4, 5, 6, 7], resource_id="seed-icl-1.0"),
    ModelTypeInfo(model_type=3, name="DiT 还原版", description="音色+口音+语速风格还原", languages=[0, 1], resource_id="seed-icl-1.0"),
    ModelTypeInfo(model_type=4, name="ICL 2.0", description="第二代音色克隆", languages=[0, 1], resource_id="seed-icl-2.0"),
    ModelTypeInfo(model_type=5, name="ICL 3.0", description="最新音色克隆，高逼真度高自然度", languages=[0, 1], resource_id="seed-icl-2.0"),
]


def get_resource_id(model_type: int) -> str:
    """Return the Volcengine Resource-Id for a given model_type."""
    return "seed-icl-2.0" if model_type >= 4 else "seed-icl-1.0"
