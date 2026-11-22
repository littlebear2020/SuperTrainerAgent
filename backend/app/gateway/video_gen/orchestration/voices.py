"""Legacy voice dictionaries backed by the shared Doubao catalog."""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import edge_tts

from app.gateway._shared.voice_catalog import CURATED_VOICES as _SHARED_CATALOG
from app.gateway._shared.voice_catalog import CatalogVoice

logger = logging.getLogger(__name__)


# Cache: (voices_list, timestamp)
_cache: tuple[list[dict[str, Any]], float] | None = None
_CACHE_TTL = 3600  # 1 hour


async def list_voices_cached(lang: str | None = None) -> list[dict[str, Any]]:
    """List edge-tts voices with in-memory caching.

    Optionally filter by locale prefix (e.g. 'zh' → zh-CN, zh-TW).
    """
    global _cache

    if _cache is None or (time.monotonic() - _cache[1]) > _CACHE_TTL:
        try:
            voices = await edge_tts.list_voices()
            _cache = (voices, time.monotonic())
        except Exception:
            logger.warning("Failed to fetch edge-tts voices", exc_info=True)
            if _cache is not None:
                pass  # use stale cache
            else:
                return []

    voices = _cache[0]

    if lang:
        lang_lower = lang.lower()
        voices = [v for v in voices if v["Locale"].lower().startswith(lang_lower)]

    return voices


def _legacy_voice_dict(v: CatalogVoice) -> dict[str, str]:
    return {
        "id": v.id,
        "name": v.name,
        "locale": v.locale,
        "gender": "Male" if v.gender == "male" else "Female",
        "provider": v.provider,
    }


# 事实源：app.gateway._shared.voice_catalog.CURATED_VOICES（4 条字节直连音色）。
CURATED_VOICES: list[dict[str, str]] = [_legacy_voice_dict(v) for v in _SHARED_CATALOG]

# Map lang code to TTS locale prefix. 角色：受支持语言登记表（test_multilang_tts
# 遍历断言每语言有默认音色路径）；生产代码不读此表——语言→音色走
# resolve_default_voice()/DEFAULT_VOICES。
LANG_LOCALE_MAP: dict[str, str] = {
    "zh": "zh-CN",
    "en": "en-US",
    "ja": "ja-JP",
    "ko": "ko-KR",
    "fr": "fr-FR",
    "de": "de-DE",
    "es": "es-ES",
}

DEFAULT_VOICES: dict[str, str] = {}


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
