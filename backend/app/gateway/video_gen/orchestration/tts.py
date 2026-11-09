"""TTS orchestration: synthesize narration → audio file + SRT + duration.

Routes between doubao (zh default) / 360 preset / edge-tts based on voice_id prefix.
"""

from __future__ import annotations

import asyncio
import logging
import re
import subprocess
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import edge_tts
import httpx
import tenacity

from app.gateway._shared.executors import run_ffmpeg

_RETRYABLE_TTS_ERRORS = (
    httpx.RemoteProtocolError,
    httpx.ConnectError,
    httpx.ReadError,
    httpx.ReadTimeout,
    asyncio.TimeoutError,  # C4: edge stream 超时（wait_for 包裹）须被 tenacity 重试
)

logger = logging.getLogger(__name__)


@dataclass
class TTSResult:
    audio_path: str
    srt: str
    duration: float  # seconds
    # P2-14: 降级链末端填实际使用音色（如 doubao 请求→360 preset:146 产出）；
    # None = 与请求一致。CAS 键按此落盘，杜绝"错误音色顶着请求音色键"的永久缓存污染。
    actual_voice_id: str | None = None
    # T7c（2026-10-14 配音修复批）：doubao 字级时间戳 [(word, start_s, end_s)]
    # （chunk/session 局部秒）。vimax narration 用它驱动字幕 cue（替换宽度估算）；
    # 其他调用方忽略（None）。preset/edge 无此通道。
    word_cues: list[tuple[str, float, float]] | None = None


# T5（2026-10-14 配音修复批）：说话人情绪注释 → doubao seed-tts-2.0 emotion 枚举。
# 取值域 = 2026-10-14 live 探针实测被 API 无错接受的值（backend/scripts/
# probe_doubao_tts_emotion_payload.py，4 个在册 doubao 音色全过：英文 happy/sad/
# angry/neutral/auto + 中文 开心/焦急/平静）。注意官方文档口径：emotion 仅部分
# （多情感）音色生效且枚举随音色不同——**只下发探针验证过的值**，新增映射或
# 新音色须先探针复测；未映射注释不下发（回落现状）。声学效果因 TTS 输出非
# 确定性无法离线判定，归部署侧人工试听验收。
_ANNOTATION_EMOTION_MAP: tuple[tuple[str, str], ...] = (
    ("焦急", "焦急"),
    ("急切", "焦急"),
    ("慌张", "焦急"),
    ("紧张", "焦急"),
    ("焦躁", "焦急"),
    ("平静", "平静"),
    ("冷静", "平静"),
    ("沉着", "平静"),
    ("笃定", "平静"),
    ("沉稳", "平静"),
    ("开心", "开心"),
    ("喜悦", "开心"),
    ("高兴", "开心"),
    ("兴奋", "开心"),
    ("欢喜", "开心"),
    ("愉悦", "开心"),
    ("悲伤", "sad"),
    ("难过", "sad"),
    ("伤心", "sad"),
    ("沮丧", "sad"),
    ("低落", "sad"),
    ("哀伤", "sad"),
    ("愤怒", "angry"),
    ("生气", "angry"),
    ("恼怒", "angry"),
    ("气愤", "angry"),
)


# 九轮（bug·low）：否定词子串匹配会把程度副词「特别」误判为否定（特**别**）——
# 用负向后顾排除；标点天然隔断（他说不，要保持平静）；「特不…」这类极罕见
# 构式漏判属已记录取舍（宁漏判不发）。
_NEGATION_BEFORE_KEYWORD = re.compile(r"(?<!特)(不|别|毫无|没有)[^，。,]*$")


def resolve_doubao_emotion(annotation: str) -> str | None:
    """情绪注释（如 ``疑惑且防备``/``焦急``）→ 探针验证过的 emotion 枚举；未映射 None。

    - 复合情绪（九轮 low）：注释命中 ≥2 个**不同**枚举（如「又焦急又开心」）
      → 宁不发不错发，返回 None（与否定式同口径）。
    - 否定式处理（二轮 #2 + 三轮/六轮/九轮修正）：检查**命中关键词前 4 字邻域**
      是否含否定词——覆盖紧邻否定（不开心/别难过/毫无悲伤）与插入式否定
      （不要那么开心/一点也不高兴）；「特别」含「别」但非否定（负向后顾排除），
      「焦急不安/兴奋不已」这类含「不」但属词内成分的合法情绪不被误杀，远邻无关
      否定（他说不，要保持平静）亦不误杀。更长插入（>4 字）的否定漏判属已知取舍。
    """
    hits: set[str] = set()
    for keyword, value in _ANNOTATION_EMOTION_MAP:
        idx = annotation.find(keyword)
        if idx < 0:
            continue
        prefix = annotation[max(0, idx - 4) : idx]
        if _NEGATION_BEFORE_KEYWORD.search(prefix):
            return None  # 否定式情绪——语义颠倒，不下发（回落现状）
        hits.add(value)
    if len(hits) == 1:
        return hits.pop()
    return None


def speed_to_rate(speed: float) -> str:
    """Convert speed multiplier (1.0 = normal) to edge-tts rate string like '+20%'."""
    pct = round((speed - 1.0) * 100)
    sign = "+" if pct >= 0 else "-"
    return f"{sign}{abs(pct)}%"


def _edge_fallback_disabled() -> bool:
    """国内仅可达部署（edge-tts 被墙）应设 TTS_DISABLE_EDGE_FALLBACK=1：
    360/doubao 失败直接 fail-loud，不再走必死的 edge 回退链。"""
    import os

    return os.environ.get("TTS_DISABLE_EDGE_FALLBACK", "").strip().lower() in ("1", "true", "yes")


def _edge_fallback_voice_for(voice_id: str) -> str:
    """按原音色性别选 edge 回退音色（曾硬编码 Yunjian 男声→女声 preset 静默变男声）。

    评审修复（2026-10-19，计划 B）：目录查不到时按 speaker id 前缀判性别——
    ``DOUBAO_TTS_DEFAULT_SPEAKER``/白名单是自由字符串，绝大多数 seed-tts speaker
    不在 4 音色目录里（目录查不到一律落男声 = 历史缺陷类在自由 speaker 源上复发）。
    """
    from app.gateway.video_gen.orchestration.voices import CURATED_VOICES

    gender = next((v.get("gender") for v in CURATED_VOICES if v.get("id") == voice_id), None)
    if gender is None:
        token = voice_id.rsplit(":", 1)[-1].lower()
        if "female" in token or "女" in token:
            gender = "Female"
        elif "male" in token or "男" in token:
            gender = "Male"
    return "zh-CN-XiaoxiaoNeural" if gender == "Female" else "zh-CN-YunjianNeural"


@dataclass(frozen=True)
class TTSPolicy:
    """可选 TTS 策略开关（Plan B，2026-10-19）。``policy=None``（缺省）= 完全旧链路，
    共享 TTS 的所有既有调用方语义零变化。

    - ``qihu``: "allow"（默认）｜"reject"——reject 时 ``preset:*`` 在进入 360 前抛
      可读 ValueError（防漏底线；入口级的统一可读文案见 ViMax voice_policy）。
    - ``doubao_failure``: "qihu_then_edge"（默认，豆包可降级故障 → 360 fallback_voice，
      360 再失败自身回落 edge）｜"edge"——直接同性别 Edge 音色（不经 360）。
      仅作用于**可降级**故障（429/5xx/传输/缺 key/瞬时 RuntimeError）；4xx、
      55000000 speaker mismatch、clone 失败在任何 policy 下都不降级。
    """

    qihu: Literal["allow", "reject"] = "allow"
    doubao_failure: Literal["qihu_then_edge", "edge"] = "qihu_then_edge"


def _parse_preset_id(voice_id: str) -> int:
    """Parse ``preset:N`` → ``int(N)``；非数字/缺 N → ValueError（曾裸 int() 抛 500）。"""
    try:
        return int(voice_id.split(":", 1)[1])
    except (IndexError, ValueError) as exc:
        raise ValueError(f"invalid preset voice_id: {voice_id!r}") from exc


async def synthesize(
    text: str,
    *,
    voice_id: str = "zh-CN-YunjianNeural",
    speed: float = 1.0,
    output_path: str,
    fallback_voice: str = "preset:146",
    section_id: str | None = None,
    emotion: str | None = None,
    policy: TTSPolicy | None = None,
) -> TTSResult:
    """Unified TTS entry point. Routes based on voice_id prefix.

    - "doubao:X" → ByteDance seed-tts-2.0 (openspeech; fallback to 360→edge on infra errors)
    - "clone:X" → voice_twin 克隆音色（seed-icl 资源；T11——同 openspeech 账号体系，
      克隆音色不降级，失败即响亮报错）
    - "preset:XXX" → 360 TTS (srt stays empty, _write_storyboard_srt fallback handles subtitles)
    - other → edge-tts (with WordBoundary word-level SRT)

    ``fallback_voice`` is the 360 preset used when the doubao path degrades
    (wired to ``VideoGenConfig.tts_fallback_voice`` at the service call sites).
    ``section_id``（T11）：同课程任务的关联键（additions.section_id，多轮语气一致）。
    ``emotion``（T5，2026-10-14）：情绪枚举（resolve_doubao_emotion 的产物，如
    ``焦急``/``sad``）——**仅 doubao 路径消费**，其他音色静默忽略；doubao→360/edge
    降级时自动丢弃（下游通道无此参数）。
    ``policy``（Plan B，2026-10-19）：可选 :class:`TTSPolicy`；缺省 None = 完全旧链路。
    reject 策略下 ``preset:*`` 在进入 360 前抛 ValueError（ViMax 等专用调用方的防漏
    底线，非静默换声）。
    """
    if policy is not None and policy.qihu == "reject" and voice_id.startswith("preset:"):
        raise ValueError(f"TTS policy rejects 360 preset voices: {voice_id!r} ——本调用方已禁用 360 音色，请改选豆包（doubao:*）或 Edge（zh-CN-*）音色")
    if voice_id.startswith("doubao:"):
        return await _synthesize_doubao(text, voice_id, speed, output_path, fallback_voice=fallback_voice, section_id=section_id, emotion=emotion, policy=policy)
    if voice_id.startswith("clone:"):
        return await _synthesize_clone(text, voice_id, speed, output_path, section_id=section_id)
    if voice_id.startswith("preset:"):
        return await _synthesize_qihu(text, voice_id, speed, output_path)
    return await _synthesize_edge(text, voice_id, speed, output_path)


async def _synthesize_edge(
    text: str,
    voice_id: str,
    speed: float,
    output_path: str,
) -> TTSResult:
    """edge-tts synthesis with WordBoundary word-level SRT (transport errors retried 3x)."""
    return await _call_edge_with_retry(text=text, voice_id=voice_id, speed=speed, output_path=output_path)


@tenacity.retry(
    retry=tenacity.retry_if_exception_type(_RETRYABLE_TTS_ERRORS),
    wait=tenacity.wait_fixed(2),
    stop=tenacity.stop_after_attempt(3),
    reraise=True,
)
async def _call_edge_with_retry(*, text, voice_id, speed, output_path) -> TTSResult:
    """Call edge-tts, retrying transport errors + timeout (same policy as 360). Non-transport errors propagate.

    C4: edge stream is wrapped in ``asyncio.wait_for`` with a 60s explicit timeout
    (configurable via ``TTS_EDGE_TIMEOUT`` env). Without this, a half-open TCP
    connection (edge server reachable but never sending data) blocks indefinitely —
    tenacity never catches it because it's not a transport error. The 60s default
    is generous because edge is the last-resort fallback; we'd rather wait a bit
    than loud-fail the frame. Worst case: ~3min (3 attempts × 60s + backoff).
    """
    import os

    edge_timeout = float(os.environ.get("TTS_EDGE_TIMEOUT", "60"))
    rate = speed_to_rate(speed)
    communicate = edge_tts.Communicate(text, voice_id, rate=rate)
    submaker = edge_tts.SubMaker()

    audio_bytes = bytearray()
    last_end_100ns = 0

    async def _consume_stream() -> None:
        nonlocal last_end_100ns
        async for chunk in communicate.stream():
            ctype = chunk["type"]
            if ctype == "audio":
                audio_bytes.extend(chunk["data"])
            elif ctype == "WordBoundary":
                submaker.feed(chunk)
                last_end_100ns = chunk["offset"] + chunk["duration"]

    await asyncio.wait_for(_consume_stream(), timeout=edge_timeout)

    with open(output_path, "wb") as f:
        f.write(bytes(audio_bytes))

    duration = last_end_100ns / 10_000_000  # 100ns → seconds
    try:
        srt = submaker.get_srt()
    except Exception:
        srt = ""

    return TTSResult(audio_path=output_path, srt=srt, duration=duration, actual_voice_id=voice_id)


@tenacity.retry(
    retry=tenacity.retry_if_exception_type(_RETRYABLE_TTS_ERRORS),
    wait=tenacity.wait_fixed(2),
    stop=tenacity.stop_after_attempt(3),
    reraise=True,
)
async def _call_360_with_retry(*, client, text, preset_id, output_path, speed=1.0):
    """Call 360 TTS, retrying transport errors. Non-transport errors propagate."""
    import os

    api_level_speed = None if os.environ.get("QIHU_TTS_SPEED_ATEMPO", "") == "1" else speed
    wav_bytes = await client.synthesize_sse(text=text, voice=preset_id, speed=api_level_speed)
    wav_path = output_path.replace(".mp3", ".wav")
    Path(wav_path).write_bytes(wav_bytes)
    if api_level_speed is None and abs(speed - 1.0) > 1e-6:
        # 兜底：360 API 不支持 speed 时用 ffmpeg atempo 后处理（保调变速）。
        # atempo 合法区间 [0.5, 100]；UI 语速范围 0.5–2.0 内无需链式。
        await run_ffmpeg(_wav_atempo, wav_path, min(max(speed, 0.5), 2.0))
    await run_ffmpeg(_wav_to_mp3, wav_path, output_path)
    Path(wav_path).unlink(missing_ok=True)


async def _synthesize_qihu(
    text: str,
    voice_id: str,
    speed: float,
    output_path: str,
) -> TTSResult:
    """360 TTS synthesis. Retries transport errors 3x, then falls back to edge-tts.

    srt stays empty — _write_storyboard_srt() fallback creates sentence-level subtitles.
    """
    import os

    preset_id = _parse_preset_id(voice_id)

    api_key = os.environ.get("QIHU_API_KEY", "")
    if not api_key:
        if _edge_fallback_disabled():
            raise RuntimeError("QIHU_API_KEY is empty and edge fallback disabled (TTS_DISABLE_EDGE_FALLBACK=1)")
        logger.warning("QIHU_API_KEY is empty — falling back to edge-tts (%s)", _edge_fallback_voice_for(voice_id))
        # C3 补接（2026-08-09 评审）：360 key 缺失直落 edge，同样留痕
        record_tts_degradation(
            requested_voice=voice_id,
            actual_voice=_edge_fallback_voice_for(voice_id),
            detail="QIHU_API_KEY 未配置，360 音色降级为 edge-tts 备用音色",
        )
        return await _synthesize_edge(text, _edge_fallback_voice_for(voice_id), speed, output_path)

    try:
        from app.gateway.voice_twin.qihu_tts_client import QihuTTSClient

        client = QihuTTSClient(api_key=api_key)
        await _call_360_with_retry(
            client=client,
            text=text,
            preset_id=preset_id,
            output_path=output_path,
            speed=speed,
        )
    except Exception:
        if _edge_fallback_disabled():
            raise
        logger.exception("360 TTS failed after retries, falling back to edge-tts")
        # C3 补接（2026-08-09 评审）：360→edge 是最严重的降级（全片音色/性别都可能变），
        # 必须留痕——修前此路径零记录，用户拿到变声成片完全无感。
        record_tts_degradation(
            requested_voice=voice_id,
            actual_voice=_edge_fallback_voice_for(voice_id),
            detail="360 TTS 重试后仍失败，降级为 edge-tts 备用音色",
        )
        return await _synthesize_edge(text, _edge_fallback_voice_for(voice_id), speed, output_path)

    duration = await run_ffmpeg(_get_audio_duration, output_path)
    return TTSResult(audio_path=output_path, srt="", duration=duration, actual_voice_id=voice_id)


def record_tts_degradation(**fields) -> None:
    """记录一次 TTS 降级事实（C3 接线）。

    写入 degradations sidecar（如有 task_dir 上下文），使前端能显示
    「本片音色被降级」。无上下文时退化为纯日志（monkeypatch 友好）。
    """
    logger.warning("TTS degradation: %s", fields)
    task_dir = _tts_task_dir.get()
    if task_dir is not None:
        from ..degradations import KIND_VOICE_FALLBACK, record_degradation

        record_degradation(
            task_dir,
            kind=fields.get("kind", KIND_VOICE_FALLBACK),
            detail=str(fields.get("detail", fields.get("requested_voice", ""))),
            # Plan B 评审修复（2026-10-19）：结构化音色字段透传进 sidecar（引擎正式
            # 渲染路径的降级也留结构化痕，不只 detail 文本）。
            requested_voice=fields.get("requested_voice"),
            actual_voice=fields.get("actual_voice"),
        )


#: C3: TTS 降级留痕的 task_dir 上下文。由 service.py 在 TTS 处理前设置。
_tts_task_dir: ContextVar[Path | None] = ContextVar("tts_task_dir", default=None)


def set_tts_degradation_context(task_dir: Path | str | None) -> None:
    """设置当前 TTS 处理的 task_dir，使降级记录能写入 sidecar。

    service.py 在逐帧 TTS 处理前调用：``set_tts_degradation_context(working_dir)``。
    传 None 清除上下文（TTS 处理结束后或无需留痕时）。
    """
    _tts_task_dir.set(Path(task_dir) if task_dir else None)


def _is_retryable_doubao_error(exc: BaseException) -> bool:
    """豆包哪些错误值得重试：传输层错误 + 429/5xx 基础设施错误。

    4xx（鉴权/配置）与音色不匹配不在内——重试不会让它们变好。
    """
    from app.gateway.voice_twin.doubao_tts_client import DoubaoTTSHTTPError

    if isinstance(exc, _RETRYABLE_TTS_ERRORS):
        return True
    if isinstance(exc, DoubaoTTSHTTPError):
        return exc.status_code == 429 or exc.status_code >= 500
    return False


@tenacity.retry(
    retry=tenacity.retry_if_exception(_is_retryable_doubao_error),
    wait=tenacity.wait_fixed(2),
    stop=tenacity.stop_after_attempt(3),
    reraise=True,
)
async def _call_doubao_with_retry(*, client, speaker: str, text: str, speed: float, section_id: str | None = None, collect_words: bool = False, emotion: str | None = None):
    """调豆包 TTS，瞬时错误重试 3 次（策略对齐 360 / edge）。

    C1 根因：此前这里是裸调 —— 豆包**首次** 429/5xx/超时就立刻降级 360，
    导致该帧音色与全片不同，用户听到视频中段突然换人。360 与 edge 两条路径
    早就有 3 次重试，只有豆包没有。
    T10：``collect_words=True`` 走 synthesize_sse_with_timestamps（event 364 字级
    时间戳）；tenacity 重试安全——每次尝试是独立 SSE 会话，words 由返回值携带
    （不共享可变收集器，重试不产生重复词条）。
    T5（2026-10-14）：``emotion`` 透传 seed-tts-2.0 情感参数（audio_params.emotion）。
    """
    if collect_words:
        return await client.synthesize_sse_with_timestamps(speaker=speaker, text=text, speed_ratio=speed, section_id=section_id, emotion=emotion)
    return await client.synthesize_sse(speaker=speaker, text=text, speed_ratio=speed, section_id=section_id, emotion=emotion)


async def _fallback_doubao_to_edge(text: str, original_voice_id: str, speed: float, output_path: str, *, reason: str) -> TTSResult:
    """Plan B（2026-10-19）：``doubao_failure="edge"`` 策略下豆包可降级故障 → 直接同性别 Edge。

    与旧 ``qihu_then_edge`` 链的本质差异：不经 360（该策略的调用方——如 ViMax——
    已整体禁用 360 音色）。**成功返回后**才写「已降级」留痕（actual_voice = 实际
    Edge 音色）；Edge 失败原样上抛、不留任何成功留痕——「开始降级」≠「降级成功」，
    sidecar 不得把失败写成成功。
    """
    edge_voice = _edge_fallback_voice_for(original_voice_id)
    result = await _synthesize_edge(text, edge_voice, speed, output_path)
    record_tts_degradation(
        requested_voice=original_voice_id,
        actual_voice=edge_voice,
        detail=f"豆包 TTS {reason}（请求 {original_voice_id}），已降级为 Edge 备用音色（{edge_voice}）",
    )
    return result


async def _synthesize_doubao(
    text: str,
    voice_id: str,
    speed: float,
    output_path: str,
    *,
    fallback_voice: str = "preset:146",
    section_id: str | None = None,
    emotion: str | None = None,
    policy: TTSPolicy | None = None,
) -> TTSResult:
    """ByteDance seed-tts-2.0 TTS (openspeech V3 SSE).

    Fallback policy (requirement #4: only 429/5xx/timeout/conn degrade):
      - infrastructure errors (HTTP 5xx/429, transport, timeout) → fall back to
        the 360 ``fallback_voice`` (VideoGenConfig.tts_fallback_voice), which
        itself falls back to edge-tts on failure.
      - config/auth errors (HTTP 4xx, SSE non-zero code e.g. 55000000 "resource ID
        mismatched with speaker", session failure, empty audio) → re-raise as a
        user-visible error. No fallback: 360 uses different speakers, and a
        speaker-mismatch is a config error, not a transient infra blip.
      - ``policy.doubao_failure == "edge"``（Plan B，2026-10-19）：四个可降级分支
        改走 :func:`_fallback_doubao_to_edge`（同性别 Edge，成功后留痕）；policy=None
        时四个 360 分支原样保留。

    ``access_key`` reuses ``voice_clone.access_key`` (``$VOLCENGINE_VOICE_ACCESS_KEY``),
    the same openspeech account as voice clone. srt stays empty —
    ``_write_storyboard_srt()`` builds sentence-level subtitles.
    T5（2026-10-14）：``emotion`` = 探针验证过的情感枚举，进 ``audio_params.emotion``；
    降级到 360/edge 的分支不带 emotion（自动丢弃，符合决策 4）。
    """
    import os

    from app.gateway.voice_twin.doubao_tts_client import DoubaoTTSClient, DoubaoTTSHTTPError

    speaker = voice_id.split(":", 1)[1]
    access_key = os.environ.get("VOLCENGINE_VOICE_ACCESS_KEY", "")
    _edge_direct = policy is not None and policy.doubao_failure == "edge"
    if not access_key:
        logger.warning("VOLCENGINE_VOICE_ACCESS_KEY empty — falling back (%s)", "edge" if _edge_direct else "360 TTS")
        # Task 6: 这是**全片**降级（不是单帧抖动）——每一帧都会走 360，成片音色
        # 与用户选的完全不同。此前只有一条 warning，用户界面零提示。
        if _edge_direct:
            return await _fallback_doubao_to_edge(text, voice_id, speed, output_path, reason="访问密钥未配置（VOLCENGINE_VOICE_ACCESS_KEY）")
        record_tts_degradation(
            kind="tts_access_key_missing",
            requested_voice=voice_id,
            actual_voice=fallback_voice,
            detail="VOLCENGINE_VOICE_ACCESS_KEY 未配置，豆包音色全部降级为 360 预设",
        )
        return await _synthesize_qihu(text, fallback_voice, speed, output_path)

    client = DoubaoTTSClient(access_key=access_key)
    words: list[dict] = []
    # T-A3b（D4 保底开关）：DOUBAO_WORD_SRT=0 关闭字级时间戳收集，回落句级
    # _write_storyboard_srt（width-ratio 估算）——三渠道回归若发现豆包词级致
    # 老管线字幕变差，一行开关回滚（默认全开 = D4 拍板）。
    collect = os.environ.get("DOUBAO_WORD_SRT", "1") != "0"
    try:
        # T10：开 enable_subtitle 拿 event 364 字级时间戳（words 为空 = 服务未下发，
        # 如 cache 缓存命中 → 降级句级 _write_storyboard_srt，不回退 edge）
        audio_bytes, words = await _call_doubao_with_retry(
            client=client,
            speaker=speaker,
            text=text,
            speed=speed,
            section_id=section_id,
            collect_words=collect,
            emotion=emotion,
        )
    except DoubaoTTSHTTPError as exc:
        if exc.status_code == 429 or exc.status_code >= 500:
            logger.exception("doubao TTS infra HTTP %s — falling back (%s)", exc.status_code, "edge" if _edge_direct else "360 TTS")
            # C3 补接（2026-08-09 评审）：doubao→360 运行时降级留痕（修前零记录）
            if _edge_direct:
                return await _fallback_doubao_to_edge(text, voice_id, speed, output_path, reason=f"服务错误（HTTP {exc.status_code}）")
            record_tts_degradation(
                requested_voice=voice_id,
                actual_voice=fallback_voice,
                detail=f"豆包 TTS 服务错误（HTTP {exc.status_code}），本帧降级为 360 预设音色",
            )
            return await _synthesize_qihu(text, fallback_voice, speed, output_path)
        raise  # 4xx auth/config → user-visible
    except _RETRYABLE_TTS_ERRORS:
        logger.exception("doubao TTS transport error — falling back (%s)", "edge" if _edge_direct else "360 TTS")
        if _edge_direct:
            return await _fallback_doubao_to_edge(text, voice_id, speed, output_path, reason="传输错误")
        record_tts_degradation(
            requested_voice=voice_id,
            actual_voice=fallback_voice,
            detail="豆包 TTS 传输错误，本帧降级为 360 预设音色",
        )
        return await _synthesize_qihu(text, fallback_voice, speed, output_path)
    except RuntimeError as exc:
        # 55000000 = speaker/resource 不匹配（配置错，不可恢复）→ user-visible，不降级。
        # 其余 RuntimeError（配额/并发 45000292、会话失败、空音频等瞬时）→ 降级 360
        # fallback_voice，避免单帧经 asyncio.gather 拖垮整批 draft audio（P3/A1）。
        msg = str(exc)
        if "55000000" in msg or "mismatched" in msg.lower():
            raise
        logger.exception("doubao TTS transient error — falling back (%s): %s", "edge" if _edge_direct else "360 TTS", msg)
        # C3 补接（2026-08-09 评审）：这是「视频中途换人」最高频路径，必须留痕
        if _edge_direct:
            return await _fallback_doubao_to_edge(text, voice_id, speed, output_path, reason=f"瞬时错误（{msg[:80]}）")
        record_tts_degradation(
            requested_voice=voice_id,
            actual_voice=fallback_voice,
            detail=f"豆包 TTS 瞬时错误（{msg[:80]}），本帧降级为 360 预设音色",
        )
        return await _synthesize_qihu(text, fallback_voice, speed, output_path)

    with open(output_path, "wb") as f:
        f.write(audio_bytes)
    duration = await run_ffmpeg(_get_audio_duration, output_path)
    srt = _words_to_srt(words)
    # T7c：字级时间戳随结果携带（chunk 局部秒）；空表 → None（调用方回落估算）
    word_cues = [(str(w.get("word", "")), float(w.get("startTime", 0.0)), float(w.get("endTime", 0.0))) for w in words if w.get("word")] or None
    return TTSResult(audio_path=output_path, srt=srt, duration=duration, actual_voice_id=voice_id, word_cues=word_cues)


def _words_to_srt(words: list[dict]) -> str:
    """T10：event 364 words（session 相对秒）→ 帧级字级 SRT（逐词一 cue）。

    空 words 返回 ""（调用链回落句级 _write_storyboard_srt）。秒 → SRT
    HH:MM:SS,mmm；非单调/零时长词容错（clamp 到 ≥1ms 且 ≥前一 cue 结束）。
    """
    if not words:
        return ""
    cues: list[tuple[int, int, str]] = []
    prev_end_ms = 0
    for w in words:
        start_ms = max(0, int(round(float(w.get("startTime", 0.0)) * 1000)))
        end_ms = max(start_ms + 1, int(round(float(w.get("endTime", 0.0)) * 1000)))
        start_ms = max(start_ms, prev_end_ms)
        end_ms = max(end_ms, start_ms + 1)
        cues.append((start_ms, end_ms, str(w.get("word", ""))))
        prev_end_ms = end_ms
    lines: list[str] = []
    for i, (sms, ems, word) in enumerate(cues, 1):
        lines.append(str(i))
        lines.append(f"{_srt_ts(sms)} --> {_srt_ts(ems)}")
        lines.append(word)
        lines.append("")
    return "\n".join(lines)


def _srt_ts(ms: int) -> str:
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    sec, msec = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{sec:02d},{msec:03d}"


async def _synthesize_clone(
    text: str,
    voice_id: str,
    speed: float,
    output_path: str,
    *,
    section_id: str | None = None,
) -> TTSResult:
    """T11：clone:<speaker_id> → voice_twin 克隆合成通道（seed-icl 资源）。

    克隆音色**不降级**（静默换音色比失败更糟——与 voice_twin production 同
    口径）：配置缺失/合成失败一律响亮报错。同 openspeech 账号
    （VOLCENGINE_VOICE_ACCESS_KEY），复用 VolcengineTTSClient（model_type=5 →
    seed-icl-2.0 资源）。开 enable_subtitle 拿字级时间戳（T10 同款）。
    """
    import os

    from app.gateway.voice_twin.volcengine_client import VolcengineTTSClient

    speaker = voice_id.split(":", 1)[1]
    access_key = os.environ.get("VOLCENGINE_VOICE_ACCESS_KEY", "")
    if not access_key:
        raise RuntimeError("克隆音色需要 VOLCENGINE_VOICE_ACCESS_KEY（当前未配置）")

    client = VolcengineTTSClient(appid="", token="", access_key=access_key)

    async def _once() -> bytes:
        # VolcengineTTSClient.synthesize_sse 只回 bytes——带时间戳需要复用 doubao
        # 客户端的 364 解析；这里直接用 doubao 客户端的 payload 构造 + 全量客户端
        # 不行（资源 ID 不同），故最小实现：走 volcengine synthesize_sse（无字幕），
        # 字级降级句级。后续若 seed-icl 也支持 364 再升级。
        return await client.synthesize_sse(
            speaker_id=speaker,
            text=text,
            model_type=5,
            speed_ratio=speed,
        )

    audio_bytes = await _once()
    with open(output_path, "wb") as f:
        f.write(audio_bytes)
    duration = await run_ffmpeg(_get_audio_duration, output_path)
    return TTSResult(audio_path=output_path, srt="", duration=duration, actual_voice_id=voice_id)


def _wav_to_mp3(wav_path: str, mp3_path: str) -> None:
    """Convert WAV to MP3 using ffmpeg."""
    r = subprocess.run(
        ["ffmpeg", "-y", "-i", wav_path, "-codec:a", "libmp3lame", "-qscale:a", "2", mp3_path],
        capture_output=True,
        timeout=30,
    )
    if r.returncode != 0:
        # 曾静默返回——mp3 空/缺 + 调用方紧接删掉唯一 WAV 副本 → 既无 WAV 也无有效
        # MP3，后续 concat 才响亮失败（2026-07-27 复核补查）。
        raise RuntimeError(f"ffmpeg wav->mp3 failed: {r.stderr.decode(errors='replace')[:200]}")
    if not Path(mp3_path).exists():
        # rc=0 但产物缺失（异常 ffmpeg/磁盘满）同样响亮化——曾静默 → duration 0.0 → 音画漂移
        raise RuntimeError(f"ffmpeg wav->mp3 output missing: {mp3_path}")


def _wav_atempo(wav_path: str, factor: float) -> None:
    """ffmpeg atempo 就地变速（保调不变 pitch）——360 API 不支持 speed 时的兜底。

    与 _wav_to_mp3 同约定：sync 函数、run_ffmpeg 在线程池执行。输出临时文件再
    os.replace 回原路径。atempo 合法区间 [0.5, 100]，调用方负责钳制。
    """
    import os

    out = wav_path + ".atempo.wav"
    r = subprocess.run(
        ["ffmpeg", "-y", "-i", wav_path, "-filter:a", f"atempo={factor}", out],
        capture_output=True,
        timeout=30,
    )
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg atempo failed: {r.stderr.decode(errors='replace')[:200]}")
    os.replace(out, wav_path)


def _get_audio_duration(path: str) -> float:
    """音频时长（解码口径）。

    必须解码计时而非 ffprobe format=duration：LAME gapless mp3 的格式时长含
    encoder delay/padding（真实 TTS 产物每文件 ~55ms），解码会裁掉；
    _concat_audio 按解码口径拼接。两口径不一致在多帧任务累积成秒级音画错位
    （实测 94 帧漂 5.2s，vg_3300179a11f0）。

    备选实现（同解码口径、结构化输出）：ffprobe -count_samples -show_entries
    stream=nb_read_samples，duration = 样本数/采样率——若 stderr time= 解析在
    某 ffmpeg 版本失效，可换此路径（当前以 ffprobe 格式口径兜底，不更坏）。
    """
    try:
        proc = subprocess.run(
            ["ffmpeg", "-i", path, "-f", "null", "-"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        last = None
        # 取最后一个 time= 行（进度行以 \r 分隔——text=True 的 universal-newlines
        # 与 str.splitlines() 均兼容）；起始行可能是 time=N/A，解析失败跳过即可
        for line in proc.stderr.splitlines():
            if "time=" not in line:
                continue
            try:
                t = line.split("time=")[1].split()[0]
                h, m, s = t.split(":")
                last = int(h) * 3600 + int(m) * 60 + float(s)
            except (ValueError, IndexError):
                continue
        if last is not None and last > 0:
            return last
    except Exception:
        pass
    try:  # 兜底：格式口径总比没有强（回到旧行为，不更坏）
        probe = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0", path],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return float(probe.stdout.strip())
    except Exception:
        return 0.0
