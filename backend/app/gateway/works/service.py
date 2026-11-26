import asyncio
import logging
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from app.gateway.path_utils import resolve_thread_virtual_path
from app.gateway.utils import sanitize_log_param
from app.gateway.works.schemas import ArtifactFileSummary, MediaKind, ThreadArtifactSummary

logger = logging.getLogger(__name__)

_OUTPUTS_PREFIX = "/mnt/user-data/outputs/"
_MEDIA_KIND_BY_EXTENSION: dict[str, MediaKind] = {
    **{extension: "video" for extension in ("mp4", "webm", "mov")},
    **{extension: "image" for extension in ("png", "jpg", "jpeg", "webp", "gif")},
    **{extension: "audio" for extension in ("mp3", "wav", "m4a", "aac", "ogg")},
    **{extension: "document" for extension in ("pptx", "ppt", "pdf", "docx", "doc", "xlsx", "xls", "md", "txt", "csv", "json")},
}


def media_kind_for_path(path: str) -> MediaKind:
    extension = PurePosixPath(path).suffix.lower().removeprefix(".")
    return _MEDIA_KIND_BY_EXTENSION.get(extension, "other")


def _unknown_summary(thread_id: str) -> ThreadArtifactSummary:
    return ThreadArtifactSummary(thread_id=thread_id, delivery_state="unknown", files=[])


def _log_rejection(thread_id: str, reason: str) -> None:
    logger.warning("Artifact summary rejected for thread %s: %s", sanitize_log_param(thread_id), reason)


def build_thread_artifact_summary(thread_id: str, artifact_paths: object) -> ThreadArtifactSummary:
    if not isinstance(artifact_paths, list) or any(not isinstance(path, str) for path in artifact_paths):
        _log_rejection(thread_id, "invalid_artifacts")
        return _unknown_summary(thread_id)
    if not artifact_paths:
        return ThreadArtifactSummary(thread_id=thread_id, delivery_state="not_delivered", files=[])

    virtual_paths = list(dict.fromkeys(artifact_paths))
    for virtual_path in virtual_paths:
        if not virtual_path.startswith(_OUTPUTS_PREFIX):
            _log_rejection(thread_id, "outside_outputs")
            return _unknown_summary(thread_id)

    resolved_paths: list[tuple[str, Path]] = []
    for virtual_path in virtual_paths:
        try:
            actual_path = resolve_thread_virtual_path(thread_id, virtual_path)
        except Exception:
            _log_rejection(thread_id, "resolver_rejected")
            return _unknown_summary(thread_id)
        resolved_paths.append((virtual_path, actual_path))

    files: list[ArtifactFileSummary] = []
    for virtual_path, actual_path in resolved_paths:
        try:
            file_stat = actual_path.stat()
        except OSError:
            files.append(
                ArtifactFileSummary(
                    path=virtual_path,
                    name=PurePosixPath(virtual_path).name,
                    media_kind=media_kind_for_path(virtual_path),
                    size=None,
                    modified_at=None,
                    exists=False,
                )
            )
            continue
        files.append(
            ArtifactFileSummary(
                path=virtual_path,
                name=PurePosixPath(virtual_path).name,
                media_kind=media_kind_for_path(virtual_path),
                size=file_stat.st_size,
                modified_at=datetime.fromtimestamp(file_stat.st_mtime, UTC),
                exists=True,
            )
        )

    files.sort(
        key=lambda file: (
            not file.exists,
            -file.modified_at.timestamp() if file.modified_at is not None else 0,
            file.path,
        )
    )
    existing_count = sum(file.exists for file in files)
    if existing_count == len(files):
        delivery_state = "delivered"
    elif existing_count == 0:
        delivery_state = "missing"
    else:
        delivery_state = "partial"
    return ThreadArtifactSummary(thread_id=thread_id, delivery_state=delivery_state, files=files)


async def load_thread_artifact_summary(thread_id: str, checkpointer: Any) -> ThreadArtifactSummary:
    config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
    checkpoint_tuple = await checkpointer.aget_tuple(config)
    if checkpoint_tuple is None:
        return _unknown_summary(thread_id)
    checkpoint = getattr(checkpoint_tuple, "checkpoint", None) or {}
    channel_values = checkpoint.get("channel_values") or {}
    if not isinstance(channel_values, dict):
        _log_rejection(thread_id, "invalid_channel_values")
        return _unknown_summary(thread_id)
    return await asyncio.to_thread(build_thread_artifact_summary, thread_id, channel_values.get("artifacts", []))
