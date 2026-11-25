from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

DeliveryState = Literal["delivered", "not_delivered", "partial", "missing", "unknown"]
MediaKind = Literal["video", "image", "audio", "document", "other"]


class ArtifactSummariesRequest(BaseModel):
    thread_ids: list[str] = Field(min_length=1, max_length=50)

    @field_validator("thread_ids")
    @classmethod
    def normalize_thread_ids(cls, thread_ids: list[str]) -> list[str]:
        normalized = [thread_id.strip() for thread_id in thread_ids]
        if any(not thread_id or len(thread_id) > 128 for thread_id in normalized):
            raise ValueError("Thread IDs must be non-empty and at most 128 characters")
        return normalized


class ArtifactFileSummary(BaseModel):
    path: str
    name: str
    media_kind: MediaKind
    size: int | None
    modified_at: datetime | None
    exists: bool


class ThreadArtifactSummary(BaseModel):
    thread_id: str
    delivery_state: DeliveryState
    files: list[ArtifactFileSummary]
