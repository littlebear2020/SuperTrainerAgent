"""ViMax voice selection uses the same Doubao-only catalog as other tasks."""

from __future__ import annotations

from app.gateway._shared.voice_catalog import ensure_voice_id_supported
from app.gateway.video_gen.orchestration.voices import resolve_default_voice


def resolve_vimax_default_voice(lang: str) -> str | None:
    return resolve_default_voice(lang)


def ensure_vimax_voice_allowed(voice_id: str | None) -> None:
    if voice_id:
        ensure_voice_id_supported(voice_id)
