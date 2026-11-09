"""Voice list caching for edge-tts voices + legacy CURATED_VOICES re-export.

2026-08-09 A1：CURATED_VOICES 事实源迁至 ``app.gateway._shared.voice_catalog``
（video-gen 与 voice-twin 共用）。本模块把共享目录 re-export 为 legacy dict
形态——edge 音色剥掉 ``edge:`` 前缀（本模块 tts 路由把无前缀 id 视为 edge）、
gender 首字母大写——``/api/video/voices`` 响应与调用方（dialogue.py 等）
逐字节不变。
"""

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
    """共享目录 → video-gen legacy dict 形态（/api/video/voices 响应不变的关键）。

    - edge 音色剥掉 ``edge:`` 前缀（本模块 tts 路由把无前缀 id 当 edge-tts）；
    - gender 恢复首字母大写（dialogue.py / 前端按 "Male"/"Female" 匹配）；
    - 只暴露 legacy 五键（id/name/locale/gender/provider），scenario/tone 是
      voice-twin 侧的扩展字段，不进 video-gen 响应。
    """
    vid = v.id
    if v.provider == "edge-tts" and vid.startswith("edge:"):
        vid = vid[len("edge:") :]
    return {
        "id": vid,
        "name": v.name,
        "locale": v.locale,
        "gender": "Male" if v.gender == "male" else "Female",
        "provider": v.provider,
    }


# Curated voice list for frontend selector (most popular voices).
# 事实源：app.gateway._shared.voice_catalog.CURATED_VOICES（35 条，两模块共用）。
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

# Default voice per language.
# zh is resolved lazily via resolve_default_voice() (NOT in this dict) so the
# doubao speaker is read from env at call time (after load_dotenv), not at module
# import — see Stage 2a / §1.4 漏点1. zh → doubao:<speaker> when
# DOUBAO_TTS_DEFAULT_SPEAKER is set (字节默认首选, openspeech 国内可达); else preset:146
# (360 知性女声, rollback). The neural voices below are edge-tts (need
# speech.platform.bing.com — blocked in restricted networks); zh no longer relies
# on edge-tts/bing because doubao(openspeech) + 360(api.360.cn) are both domestic.
DEFAULT_VOICES: dict[str, str] = {
    "en": "en-US-ChristopherNeural",
    "ja": "ja-JP-KeitaNeural",
    "ko": "ko-KR-HyunsuNeural",
    "fr": "fr-FR-HenriNeural",
    "de": "de-DE-ConradNeural",
    "es": "es-ES-AlvaroNeural",
}


def resolve_default_voice(lang: str) -> str | None:
    """Default voice_id for a language, or ``None`` if no default.

    zh → ``doubao:<speaker>`` when ``DOUBAO_TTS_DEFAULT_SPEAKER`` is set (字节默认首选),
         else ``preset:146`` (360, rollback). Env is read lazily at call time so the
         speaker is picked up after ``load_dotenv`` regardless of import order.
    other langs → the static edge-tts neural default from ``DEFAULT_VOICES`` (or None).
    """
    if lang == "zh":
        speaker = os.environ.get("DOUBAO_TTS_DEFAULT_SPEAKER", "")
        return f"doubao:{speaker}" if speaker else "preset:146"
    return DEFAULT_VOICES.get(lang)
