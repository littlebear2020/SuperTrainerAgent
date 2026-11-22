"""Legacy voice dictionaries backed by the shared Doubao catalog."""

from __future__ import annotations

import logging
import os

from app.gateway._shared.voice_catalog import CURATED_VOICES as _SHARED_CATALOG
from app.gateway._shared.voice_catalog import CatalogVoice

logger = logging.getLogger(__name__)


def _legacy_voice_dict(voice: CatalogVoice) -> dict[str, str]:
    return {
        "id": voice.id,
        "name": voice.name,
        "locale": voice.locale,
        "gender": "Male" if voice.gender == "male" else "Female",
        "provider": voice.provider,
    }


CURATED_VOICES: list[dict[str, str]] = [_legacy_voice_dict(voice) for voice in _SHARED_CATALOG]
LANG_LOCALE_MAP = {"zh": "zh-CN"}


def visible_doubao_voice_ids() -> list[str]:
    catalog_ids = {voice.id for voice in _SHARED_CATALOG}
    whitelist = [item.strip() for item in os.environ.get("DOUBAO_TTS_ENABLED_SPEAKERS", "").split(",") if item.strip()]
    if not whitelist:
        return [voice.id for voice in _SHARED_CATALOG]
    return [f"doubao:{speaker}" for speaker in whitelist if f"doubao:{speaker}" in catalog_ids]


def resolve_default_voice(lang: str) -> str | None:
    if lang != "zh":
        return None
    allowed = visible_doubao_voice_ids()
    speaker = os.environ.get("DOUBAO_TTS_DEFAULT_SPEAKER", "").strip()
    candidate = f"doubao:{speaker}" if speaker else ""
    if candidate in allowed:
        return candidate
    if speaker:
        logger.warning("DOUBAO_TTS_DEFAULT_SPEAKER=%s 不在当前可用目录/白名单内，将使用目录默认音色", speaker)
    return allowed[0] if allowed else None
