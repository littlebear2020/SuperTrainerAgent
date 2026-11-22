"""video-gen 与 voice-twin 共用的字节直连音色目录。"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field

VoiceProvider = Literal["doubao"]
RETIRED_VOICE_ERROR = "原音色已停用，请改选字节直连音色"
_BARE_EDGE_VOICE_RE = re.compile(r"^[a-z]{2}-[A-Z]{2}-.+Neural$")


class CatalogVoice(BaseModel):
    id: str
    name: str
    gender: Literal["male", "female"]
    locale: str
    provider: VoiceProvider
    description: str
    scenario: list[str] = Field(default_factory=list)
    tone: list[str] = Field(default_factory=list)
    preview_url: str | None = None


CURATED_VOICES: list[CatalogVoice] = [
    CatalogVoice(
        id="doubao:zh_female_vv_uranus_bigtts",
        name="Vivi2.0（女·字节）",
        gender="female",
        locale="zh-CN",
        provider="doubao",
        description="高自然度女声，多场景通用",
        scenario=["培训讲解", "日常对话"],
        tone=["知性"],
    ),
    CatalogVoice(
        id="doubao:zh_female_yingyujiaoxue_uranus_bigtts",
        name="Tina2.0（女·字节·英语教学）",
        gender="female",
        locale="zh-CN",
        provider="doubao",
        description="英语教学专用，发音标准",
        scenario=["培训讲解", "多语种"],
        tone=["亲切"],
    ),
    CatalogVoice(
        id="doubao:zh_male_m191_uranus_bigtts",
        name="云舟2.0（男·字节）",
        gender="male",
        locale="zh-CN",
        provider="doubao",
        description="稳重大气男声，播报级",
        scenario=["新闻播报", "培训讲解"],
        tone=["沉稳"],
    ),
    CatalogVoice(
        id="doubao:zh_male_liufei_uranus_bigtts",
        name="刘飞2.0（男·字节）",
        gender="male",
        locale="zh-CN",
        provider="doubao",
        description="富有表现力的男声",
        scenario=["广告配音", "日常对话"],
        tone=["磁性"],
    ),
]

_CATALOG_BY_ID: dict[str, CatalogVoice] = {voice.id: voice for voice in CURATED_VOICES}


def get_catalog_voice(voice_id: str) -> CatalogVoice | None:
    return _CATALOG_BY_ID.get(voice_id)


def is_retired_voice_id(voice_id: str | None) -> bool:
    return bool(voice_id) and (voice_id.startswith(("preset:", "edge:")) or _BARE_EDGE_VOICE_RE.fullmatch(voice_id) is not None)


def ensure_voice_id_supported(voice_id: str | None) -> None:
    if not voice_id:
        raise ValueError("音色 ID 不能为空，请选择目录内字节直连音色或既有克隆")
    if is_retired_voice_id(voice_id):
        raise ValueError(RETIRED_VOICE_ERROR)
    if voice_id.startswith("doubao:"):
        from app.gateway.video_gen.orchestration.voices import visible_doubao_voice_ids

        if voice_id not in visible_doubao_voice_ids():
            raise ValueError("该字节音色不在当前目录或白名单内，请改选可用音色")
