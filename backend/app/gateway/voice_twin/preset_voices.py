"""Voice ID 语法助手（preset:/doubao:/edge:）+ 展示名查询。

2026-08-09 A1：原 6 条 ``PRESET_VOICES`` 白名单常量已删除，音色目录事实源迁至
``app.gateway._shared.voice_catalog``（35 条，与 video-gen 共用）。本模块只保留
纯函数：voice_id 语法判断/解析（``preset:{int}`` 自 2026-07-22 起是纯语法判断，
任意整数合法）+ 从共享目录查展示名。
"""

from __future__ import annotations


def is_preset_voice_id(voice_id: str) -> bool:
    """'preset:{int}' 语法判断（2026-07-22 起不再是白名单成员判断）。

    video-gen 侧接受任意 360 preset id（tts.py int 直解），白名单漂移曾导致
    preset:332 在 video-gen 可用、在 voice-twin 落克隆分支 404。展示名改由
    共享目录（get_preset_voice_name）提供。
    """
    if not voice_id.startswith("preset:"):
        return False
    try:
        int(voice_id.split(":")[1])
        return True
    except (ValueError, IndexError):
        return False


def parse_preset_voice_id(voice_id: str) -> int:
    """Extract the 360 voice integer ID from 'preset:{int}'."""
    return int(voice_id.split(":")[1])


def is_doubao_voice_id(voice_id: str) -> bool:
    """Check if voice_id is a doubao seed-tts-2.0 voice (format: 'doubao:{speaker}').

    The speaker id is a seed-tts-2.0 console voice id (NOT a preset integer).
    """
    return voice_id.startswith("doubao:") and len(voice_id) > len("doubao:")


def parse_doubao_voice_id(voice_id: str) -> str:
    """Extract the seed-tts-2.0 speaker id from 'doubao:{speaker}'.

    纵深防御（2026-08-09 D3）：speaker id 拒绝路径字符。当前 parse 输出只进
    API JSON payload 不落盘（HTTP 层 Starlette 路由也使含 "/" 的 id 不可达），
    但任何未来落盘/拼路径的调用方都经此函数——在解析点 fail-fast 比在每一处
    消费点防御更可靠。
    """
    speaker = voice_id.split(":", 1)[1]
    if "/" in speaker or "\\" in speaker or ".." in speaker:
        raise ValueError(f"invalid doubao speaker id: {speaker!r}")
    return speaker


def is_edge_voice_id(voice_id: str) -> bool:
    """Check if voice_id is an edge-tts voice (format: 'edge:{ShortName}').

    voice-twin 侧 edge 音色一律带 ``edge:`` 前缀（共享目录统一形态）；
    video-gen 侧的裸 ShortName 形态不经过本模块。
    """
    return voice_id.startswith("edge:") and len(voice_id) > len("edge:")


def parse_edge_voice_id(voice_id: str) -> str:
    """Extract the edge-tts ShortName from 'edge:{ShortName}'."""
    return voice_id.split(":", 1)[1]


def get_preset_voice_name(voice_id: str) -> str | None:
    """Get the display name for a preset voice ID string from the shared catalog.

    未收录的合法 preset id（目录外）返回 None——调用方有兜底文案
    （``360 预设音色 {id}``），不得因此拒绝合成。
    """
    from app.gateway._shared.voice_catalog import get_catalog_voice

    if not is_preset_voice_id(voice_id):
        return None
    voice = get_catalog_voice(voice_id)
    return voice.name if voice else None
