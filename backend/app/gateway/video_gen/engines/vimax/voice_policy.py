"""ViMax 专用 TTS 音色策略（Plan B，2026-10-19；设计决策 D3/D4/D5）。

范围铁律（D4）：**仅 ViMax 任务**不使用 360 音色——其他 video_gen 类型、
voice-twin、360 LLM/生图/生视频全部保持现状。本模块集中：

- ``VIMAX_TTS_POLICY``：挂到 ViMax 调用点的 :class:`TTSPolicy`（qihu=reject +
  doubao_failure=edge）。**显式传参**，不用 ContextVar 隐式传播。
- ``resolve_vimax_default_voice(lang)``：ViMax 默认音色解析（D5 顺序）——
  ``DOUBAO_TTS_DEFAULT_SPEAKER`` > 授权白名单首项（env speaker 不在白名单时
  告警丢弃）> Edge 语言默认 + 配置缺失告警。zh 无兜底字节默认时回落
  Yunjian（男声，与事故中用户所选男声一致；不在所有环境硬编码云舟 speaker）。
  不改共享 ``resolve_default_voice``（其他调用方 zh 无 env 时仍 preset:146）。
- ``ensure_vimax_voice_allowed(voice_id)``：显式/存量 ``preset:*`` 的付费前可读
  拒绝（统一文案）——不静默映射、不静默换声；None/其他前缀放行。
"""

from __future__ import annotations

import logging
import os

from app.gateway.video_gen.orchestration.tts import TTSPolicy

logger = logging.getLogger(__name__)

#: ViMax 全局唯一策略实例（frozen，无状态可安全共享）。
VIMAX_TTS_POLICY = TTSPolicy(qihu="reject", doubao_failure="edge")


def _whitelisted_speakers() -> list[str]:
    raw = os.environ.get("DOUBAO_TTS_ENABLED_SPEAKERS", "")
    return [s.strip() for s in raw.split(",") if s.strip()]


def resolve_vimax_default_voice(lang: str) -> str:
    """ViMax 默认音色（D5 顺序，env 全部调用时读取——与 resolve_default_voice
    同为 lazy 口径，load_dotenv 后取值）。

    1. ``DOUBAO_TTS_DEFAULT_SPEAKER``（在白名单内或未配置白名单时生效）；
    2. 白名单已配置而 env speaker 不在其中 → 告警 + 白名单首项；
    3. 未设 env → 白名单首项；
    4. 两者皆缺：zh → ``zh-CN-YunjianNeural``（Edge 男声），其他语言沿用共享
       ``DEFAULT_VOICES``——均告警配置缺失（Edge 依赖外网可达）。
    """
    # 边界收口（2026-10-19 P1-1）：无 VOLCENGINE_VOICE_ACCESS_KEY 时跳过全部
    # doubao 候选（env speaker 与白名单）——无 key 的 doubao 音色在试听/合成时
    # 会经默认降级链落 360，与「ViMax 不用 360 音色」冲突；直接给 Edge。
    # 边界热修复（2026-10-20 P2）：strip() 防纯空白值视作已配置（bool("  ") 为真）；
    # 模块级已 import os，去掉函数内冗余 import。
    _has_doubao_key = bool(os.environ.get("VOLCENGINE_VOICE_ACCESS_KEY", "").strip())
    speaker = os.environ.get("DOUBAO_TTS_DEFAULT_SPEAKER", "").strip() if _has_doubao_key else ""
    whitelist = _whitelisted_speakers() if _has_doubao_key else []
    if speaker and whitelist and speaker not in whitelist:
        logger.warning(
            "resolve_vimax_default_voice: DOUBAO_TTS_DEFAULT_SPEAKER=%s 不在授权白名单（%d 项）——按白名单首项 %s 解析",
            speaker,
            len(whitelist),
            whitelist[0],
        )
        speaker = ""
    if not speaker and whitelist:
        speaker = whitelist[0]
    if speaker:
        return f"doubao:{speaker}"
    # 无可用字节默认值 → Edge 语言默认 + 配置缺失告警
    from app.gateway.video_gen.orchestration.voices import DEFAULT_VOICES

    fallback = "zh-CN-YunjianNeural" if lang == "zh" else DEFAULT_VOICES.get(lang, "en-US-ChristopherNeural")
    if not _has_doubao_key:
        logger.warning(
            "resolve_vimax_default_voice: VOLCENGINE_VOICE_ACCESS_KEY 未配置——doubao 候选（env speaker/白名单）全部跳过，回落 Edge %s（依赖外网可达；请配置字节 key 后 doubao 默认音色方可生效）",
            fallback,
        )
    else:
        logger.warning("resolve_vimax_default_voice: 无可用豆包默认 speaker（env/白名单皆缺，lang=%s）——回落 Edge %s（依赖外网可达，请配置 DOUBAO_TTS_DEFAULT_SPEAKER）", lang, fallback)
    return fallback


def ensure_vimax_voice_allowed(voice_id: str | None) -> None:
    """ViMax 音色准入：显式/存量 ``preset:*`` 在付费合成前可读拒绝。

    统一文案给可行动指引（改选），不静默映射到豆包（无权威 preset→豆包对照表，
    静默换声比失败更糟）。shared ``synthesize`` 的 reject 分支是防漏底线，本函数
    是入口级的第一道（更早、文案统一）。
    """
    if voice_id and voice_id.startswith("preset:"):
        raise ValueError(f"ViMax 任务不支持 360 音色（{voice_id}）。请在音频设置中改选豆包或 Edge 音色后重试；存量项目请先更换音色再渲染。")
