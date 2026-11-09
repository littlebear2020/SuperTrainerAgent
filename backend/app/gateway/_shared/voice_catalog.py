"""共享音色目录（video-gen 与 voice-twin 配音模块共用单一数据源）。

2026-08-09 配音模块优化 A1：此前 video-gen 有 35 条 CURATED_VOICES 而 voice-twin
只有 6 条 360 白名单，两套目录分叉（交集仅 146/158/340）。本模块是唯一事实源：

- video-gen 侧经 ``video_gen/orchestration/voices.py`` re-export 为 legacy dict
  形态（edge 裸 id、gender 首字母大写），``/api/video/voices`` 响应逐字节不变；
- voice-twin 侧 ``/api/voice-twins/preset-voices`` 直接返回 CatalogVoice 全字段
  （id 带 ``edge:``/``preset:``/``doubao:`` 前缀，gender 小写）。

id 统一为「带前缀完整 voice_id」：``edge:{ShortName}`` / ``preset:{int}`` /
``doubao:{speaker}``。video-gen legacy 形态在 re-export 时剥掉 ``edge:`` 前缀
（其 tts 路由把无前缀 id 视为 edge-tts）。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

VoiceProvider = Literal["edge-tts", "qihu", "doubao"]


class CatalogVoice(BaseModel):
    """目录中的一条音色记录（voice-twin /preset-voices 的响应单元）。"""

    id: str  # 完整 voice_id："preset:146" / "doubao:vv_uranus" / "edge:zh-CN-XiaoxiaoNeural"
    name: str
    gender: Literal["male", "female"]
    locale: str  # "zh-CN" / "en-US" / ...
    provider: VoiceProvider
    description: str
    scenario: list[str] = Field(default_factory=list)  # 适用场景标签（内存常量，不落库）
    tone: list[str] = Field(default_factory=list)  # 音色特质标签
    preview_url: str | None = None


# scenario 枚举建议（后端不强校验，保持扩展性；前端筛选 UI 复用同一组常量）：
#   新闻播报 / 有声书 / 培训讲解 / 儿童故事 / 广告配音 / 悬疑解说 / 会议播报 / 日常对话 / 多语种
# tone 枚举建议：
#   知性 / 温婉 / 儒雅 / 清新 / 磁性 / 亲切 / 活泼 / 沉稳
CURATED_VOICES: list[CatalogVoice] = [
    # ── 中文（edge-tts，免费） ──
    CatalogVoice(
        id="edge:zh-CN-YunjianNeural",
        name="云健（男）",
        gender="male",
        locale="zh-CN",
        provider="edge-tts",
        description="浑厚有力的男声，适合讲解与对话",
        scenario=["培训讲解", "日常对话"],
        tone=["沉稳"],
    ),
    CatalogVoice(
        id="edge:zh-CN-YunxiNeural",
        name="云希（男）",
        gender="male",
        locale="zh-CN",
        provider="edge-tts",
        description="阳光活泼的少年男声",
        scenario=["儿童故事", "日常对话"],
        tone=["活泼"],
    ),
    CatalogVoice(
        id="edge:zh-CN-YunyangNeural",
        name="云扬（男·新闻）",
        gender="male",
        locale="zh-CN",
        provider="edge-tts",
        description="专业新闻播报男声",
        scenario=["新闻播报", "会议播报"],
        tone=["沉稳"],
    ),
    CatalogVoice(
        id="edge:zh-CN-XiaoxiaoNeural",
        name="晓晓（女）",
        gender="female",
        locale="zh-CN",
        provider="edge-tts",
        description="亲切自然的女声，通用场景",
        scenario=["日常对话", "培训讲解"],
        tone=["亲切"],
    ),
    CatalogVoice(
        id="edge:zh-CN-XiaoyiNeural",
        name="晓依（女）",
        gender="female",
        locale="zh-CN",
        provider="edge-tts",
        description="清新柔和的女声，适合故事讲述",
        scenario=["儿童故事", "有声书"],
        tone=["清新"],
    ),
    # ── 360 预设音色（高质量，需 QIHU_API_KEY） ──
    CatalogVoice(
        id="preset:146",
        name="知性女声（360）",
        gender="female",
        locale="zh-CN",
        provider="qihu",
        description="专业知性，正式培训",
        scenario=["培训讲解", "有声书"],
        tone=["知性"],
    ),
    CatalogVoice(
        id="preset:147",
        name="清爽男大（360）",
        gender="male",
        locale="zh-CN",
        provider="qihu",
        description="清爽年轻的男大学生声线",
        scenario=["日常对话", "培训讲解"],
        tone=["清新"],
    ),
    CatalogVoice(
        id="preset:157",
        name="心灵鸡汤（360）",
        gender="female",
        locale="zh-CN",
        provider="qihu",
        description="温暖治愈，情感内容",
        scenario=["有声书", "广告配音"],
        tone=["温婉"],
    ),
    CatalogVoice(
        id="preset:158",
        name="知性温婉（360）",
        gender="female",
        locale="zh-CN",
        provider="qihu",
        description="温婉优雅，知识叙述",
        scenario=["有声书", "培训讲解"],
        tone=["温婉"],
    ),
    CatalogVoice(
        id="preset:160",
        name="温柔文雅（360）",
        gender="female",
        locale="zh-CN",
        provider="qihu",
        description="温柔文雅，轻柔叙述",
        scenario=["有声书", "儿童故事"],
        tone=["温婉"],
    ),
    CatalogVoice(
        id="preset:165",
        name="亲切女声（360）",
        gender="female",
        locale="zh-CN",
        provider="qihu",
        description="亲切随和，播报与对话皆宜",
        scenario=["日常对话", "会议播报"],
        tone=["亲切"],
    ),
    CatalogVoice(
        id="preset:168",
        name="知心姐姐（360）",
        gender="female",
        locale="zh-CN",
        provider="qihu",
        description="知心体贴，亲和力强",
        scenario=["日常对话", "儿童故事"],
        tone=["亲切"],
    ),
    CatalogVoice(
        id="preset:153",
        name="解说小明（360）",
        gender="male",
        locale="zh-CN",
        provider="qihu",
        description="轻快解说，节奏明快",
        scenario=["培训讲解", "广告配音"],
        tone=["活泼"],
    ),
    CatalogVoice(
        id="preset:295",
        name="磁性男嗓（360）",
        gender="male",
        locale="zh-CN",
        provider="qihu",
        description="低沉磁性，广告与叙述",
        scenario=["广告配音", "有声书"],
        tone=["磁性"],
    ),
    CatalogVoice(
        id="preset:332",
        name="磁性解说男声（360）",
        gender="male",
        locale="zh-CN",
        provider="qihu",
        description="磁性解说，记录片质感",
        scenario=["悬疑解说", "培训讲解"],
        tone=["磁性"],
    ),
    CatalogVoice(
        id="preset:341",
        name="悬疑解说（360）",
        gender="male",
        locale="zh-CN",
        provider="qihu",
        description="悬疑氛围，张力十足",
        scenario=["悬疑解说", "有声书"],
        tone=["磁性"],
    ),
    CatalogVoice(
        id="preset:340",
        name="儒雅公子（360）",
        gender="male",
        locale="zh-CN",
        provider="qihu",
        description="翩翩风度，专业场景",
        scenario=["有声书", "培训讲解"],
        tone=["儒雅"],
    ),
    # ── 豆包 seed-tts-2.0（字节 openspeech，需 VOLCENGINE_VOICE_ACCESS_KEY） ──
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
    # ── 英语（edge-tts） ──
    CatalogVoice(
        id="edge:en-US-GuyNeural",
        name="Guy (Male)",
        gender="male",
        locale="en-US",
        provider="edge-tts",
        description="美式英语男声，新闻感",
        scenario=["多语种", "新闻播报"],
        tone=["沉稳"],
    ),
    CatalogVoice(
        id="edge:en-US-ChristopherNeural",
        name="Christopher (Male)",
        gender="male",
        locale="en-US",
        provider="edge-tts",
        description="美式英语男声，低沉权威",
        scenario=["多语种", "新闻播报"],
        tone=["沉稳"],
    ),
    CatalogVoice(
        id="edge:en-US-AriaNeural",
        name="Aria (Female)",
        gender="female",
        locale="en-US",
        provider="edge-tts",
        description="美式英语女声，自然通用",
        scenario=["多语种", "日常对话"],
        tone=["亲切"],
    ),
    CatalogVoice(
        id="edge:en-US-JennyNeural",
        name="Jenny (Female)",
        gender="female",
        locale="en-US",
        provider="edge-tts",
        description="美式英语女声，亲切对话感",
        scenario=["多语种", "日常对话"],
        tone=["亲切"],
    ),
    # ── 日语（edge-tts） ──
    CatalogVoice(
        id="edge:ja-JP-KeitaNeural",
        name="圭太 (Male)",
        gender="male",
        locale="ja-JP",
        provider="edge-tts",
        description="日语男声",
        scenario=["多语种", "日常对话"],
        tone=["沉稳"],
    ),
    CatalogVoice(
        id="edge:ja-JP-NanamiNeural",
        name="七海 (Female)",
        gender="female",
        locale="ja-JP",
        provider="edge-tts",
        description="日语女声，清新自然",
        scenario=["多语种", "日常对话"],
        tone=["清新"],
    ),
    # ── 韩语（edge-tts） ──
    CatalogVoice(
        id="edge:ko-KR-HyunsuNeural",
        name="현수 (Male)",
        gender="male",
        locale="ko-KR",
        provider="edge-tts",
        description="韩语男声",
        scenario=["多语种", "日常对话"],
        tone=["沉稳"],
    ),
    CatalogVoice(
        id="edge:ko-KR-SunHiNeural",
        name="선히 (Female)",
        gender="female",
        locale="ko-KR",
        provider="edge-tts",
        description="韩语女声，亲切自然",
        scenario=["多语种", "日常对话"],
        tone=["亲切"],
    ),
    # ── 法语（edge-tts） ──
    CatalogVoice(
        id="edge:fr-FR-HenriNeural",
        name="Henri (Male)",
        gender="male",
        locale="fr-FR",
        provider="edge-tts",
        description="法语男声",
        scenario=["多语种"],
        tone=["沉稳"],
    ),
    CatalogVoice(
        id="edge:fr-FR-DeniseNeural",
        name="Denise (Female)",
        gender="female",
        locale="fr-FR",
        provider="edge-tts",
        description="法语女声",
        scenario=["多语种"],
        tone=["亲切"],
    ),
    # ── 德语（edge-tts） ──
    CatalogVoice(
        id="edge:de-DE-ConradNeural",
        name="Conrad (Male)",
        gender="male",
        locale="de-DE",
        provider="edge-tts",
        description="德语男声",
        scenario=["多语种"],
        tone=["沉稳"],
    ),
    CatalogVoice(
        id="edge:de-DE-KatjaNeural",
        name="Katja (Female)",
        gender="female",
        locale="de-DE",
        provider="edge-tts",
        description="德语女声",
        scenario=["多语种"],
        tone=["亲切"],
    ),
    # ── 西班牙语（edge-tts） ──
    CatalogVoice(
        id="edge:es-ES-AlvaroNeural",
        name="Álvaro (Male)",
        gender="male",
        locale="es-ES",
        provider="edge-tts",
        description="西班牙语男声",
        scenario=["多语种"],
        tone=["沉稳"],
    ),
    CatalogVoice(
        id="edge:es-ES-ElviraNeural",
        name="Elvira (Female)",
        gender="female",
        locale="es-ES",
        provider="edge-tts",
        description="西班牙语女声",
        scenario=["多语种"],
        tone=["亲切"],
    ),
]

_CATALOG_BY_ID: dict[str, CatalogVoice] = {v.id: v for v in CURATED_VOICES}


def get_catalog_voice(voice_id: str) -> CatalogVoice | None:
    """按完整 voice_id（带前缀）查目录；未收录返回 None。"""
    return _CATALOG_BY_ID.get(voice_id)
