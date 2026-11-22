"""TTS orchestration: synthesize narration → audio file + SRT + duration.

Routes only to Doubao or an existing cloned voice.
"""

from __future__ import annotations

import asyncio
import logging
import re
import subprocess
from dataclasses import dataclass

import httpx
import tenacity

from app.gateway._shared.executors import run_ffmpeg

_RETRYABLE_TTS_ERRORS = (
    httpx.RemoteProtocolError,
    httpx.ConnectError,
    httpx.ReadError,
    httpx.ReadTimeout,
    asyncio.TimeoutError,
)

logger = logging.getLogger(__name__)


@dataclass
class TTSResult:
    audio_path: str
    srt: str
    duration: float  # seconds
    # 实际音色随结果显式携带，供 CAS 与调用方核对；None = 与请求一致。
    actual_voice_id: str | None = None
    # T7c（2026-10-14 配音修复批）：doubao 字级时间戳 [(word, start_s, end_s)]
    # （chunk/session 局部秒）。vimax narration 用它驱动字幕 cue（替换宽度估算）；
    # 不支持时间戳的调用方忽略（None）。
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


async def synthesize(
    text: str,
    *,
    voice_id: str = "doubao:zh_female_vv_uranus_bigtts",
    speed: float = 1.0,
    output_path: str,
    section_id: str | None = None,
    emotion: str | None = None,
) -> TTSResult:
    """Synthesize only with the requested Doubao or existing cloned voice."""
    from app.gateway._shared.voice_catalog import ensure_voice_id_supported

    ensure_voice_id_supported(voice_id)
    if voice_id.startswith("doubao:"):
        return await _synthesize_doubao(text, voice_id, speed, output_path, section_id=section_id, emotion=emotion)
    if voice_id.startswith("clone:"):
        return await _synthesize_clone(text, voice_id, speed, output_path, section_id=section_id)
    raise ValueError("不支持的音色 ID；请改选字节直连音色")


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
    """调豆包 TTS；429/5xx、传输与超时最多重试 3 次，耗尽后原样失败。
    T10：``collect_words=True`` 走 synthesize_sse_with_timestamps（event 364 字级
    时间戳）；tenacity 重试安全——每次尝试是独立 SSE 会话，words 由返回值携带
    （不共享可变收集器，重试不产生重复词条）。
    T5（2026-10-14）：``emotion`` 透传 seed-tts-2.0 情感参数（audio_params.emotion）。
    """
    if collect_words:
        return await client.synthesize_sse_with_timestamps(speaker=speaker, text=text, speed_ratio=speed, section_id=section_id, emotion=emotion)
    return await client.synthesize_sse(speaker=speaker, text=text, speed_ratio=speed, section_id=section_id, emotion=emotion)


async def _synthesize_doubao(
    text: str,
    voice_id: str,
    speed: float,
    output_path: str,
    *,
    section_id: str | None = None,
    emotion: str | None = None,
) -> TTSResult:
    """ByteDance seed-tts-2.0 TTS; retry exhaustion and config errors propagate."""
    import os

    from app.gateway.voice_twin.doubao_tts_client import DoubaoTTSClient

    speaker = voice_id.split(":", 1)[1]
    access_key = os.environ.get("VOLCENGINE_VOICE_ACCESS_KEY", "")
    if not access_key:
        raise RuntimeError("doubao TTS requires VOLCENGINE_VOICE_ACCESS_KEY")

    client = DoubaoTTSClient(access_key=access_key)
    collect = os.environ.get("DOUBAO_WORD_SRT", "1") != "0"
    audio_bytes, words = await _call_doubao_with_retry(
        client=client,
        speaker=speaker,
        text=text,
        speed=speed,
        section_id=section_id,
        collect_words=collect,
        emotion=emotion,
    )

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
