from datetime import datetime
from typing import Literal

from pydantic import BaseModel

DeliveryState = Literal["delivered", "not_delivered", "partial", "missing", "unknown"]
MediaKind = Literal["video", "image", "audio", "document", "other"]


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
