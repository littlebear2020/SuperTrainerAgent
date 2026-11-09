"""Production service — async task pipeline for script-to-audio and ppt-to-video.

Refactored to use the shared BaseTaskService framework.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from app.gateway._shared.executors import run_ffmpeg
from app.gateway._shared.task_framework.repository import TaskRepository
from app.gateway._shared.task_framework.service import BaseTaskService, ProgressUpdater

# D2（2026-08-09）：传输错误组与重试策略已抽公共 helper（voice_twin/_retry.py）；
# `_TRANSPORT_TTS_ERRORS` 别名保留兼容既有 import。
from app.gateway.voice_twin._retry import TRANSPORT_TTS_ERRORS as _TRANSPORT_TTS_ERRORS
from app.gateway.voice_twin._retry import is_retryable_tts_error

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Text splitting
# ---------------------------------------------------------------------------


def strip_markdown(text: str) -> str:
    """Strip Markdown formatting for clean TTS input."""
    # Remove fenced code blocks
    text = re.sub(r"```[\s\S]*?```", "", text)
    # Remove inline code
    text = re.sub(r"`([^`]+)`", r"\1", text)
    # Remove images ![alt](url) → alt
    text = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", text)
    # Remove links [text](url) → text
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    # Remove bold **text** or __text__
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"__(.+?)__", r"\1", text)
    # Remove italic *text* or _text_
    text = re.sub(r"\*(.+?)\*", r"\1", text)
    text = re.sub(r"(?<!\w)_(.+?)_(?!\w)", r"\1", text)
    # Remove heading markers (# ## ### etc.)
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    # Remove horizontal rules (---, ***, ___)
    text = re.sub(r"^[-*_]{3,}\s*$", "", text, flags=re.MULTILINE)
    # Remove blockquote markers
    text = re.sub(r"^>\s* ?", "", text, flags=re.MULTILINE)
    # Remove unordered list markers (- or * followed by space)
    text = re.sub(r"^[\-\*]\s+", "", text, flags=re.MULTILINE)
    # Remove ordered list markers (1. 2. etc.)
    text = re.sub(r"^\d+\.\s+", "", text, flags=re.MULTILINE)
    # Remove HTML tags
    text = re.sub(r"<[^>]+>", "", text)
    # Remove table pipes and separator lines
    text = re.sub(r"^\|?[-:]+[-| :]*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"\|", " ", text)
    # Collapse multiple blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# D8e（2026-08-09）：分句前保护常见英文缩写与小数点——"Mr. Smith said e.g. this."
# 曾被在缩写点后误切（影响面：>5000 字符段落的 script-to-audio 线）。
# 做法：分句前把这些 "." 掩码为 \x00，切完再还原。
_SENT_DOT = "\x00"
_PROTECTED_ABBREVS = ("Mr", "Mrs", "Ms", "Dr", "Prof", "Sr", "Jr", "St", "vs", "etc", "Fig", "cf", "al", "No")
_ABBREV_DOT_RE = re.compile(r"\b(?:" + "|".join(_PROTECTED_ABBREVS) + r")\.")
_DECIMAL_DOT_RE = re.compile(r"(\d)\.(\d)")
_EG_IE_RE = re.compile(r"\b([ei])\.(g|e)\.", re.IGNORECASE)


def _mask_sentence_dots(text: str) -> str:
    text = _DECIMAL_DOT_RE.sub(rf"\1{_SENT_DOT}\2", text)
    text = _EG_IE_RE.sub(rf"\1{_SENT_DOT}\2{_SENT_DOT}", text)
    return _ABBREV_DOT_RE.sub(lambda m: m.group(0)[:-1] + _SENT_DOT, text)


def split_text(text: str, max_chars: int = 5000) -> list[str]:
    """Split text into chunks for TTS, respecting paragraph and sentence boundaries."""
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    paragraphs = text.split("\n\n")
    current = ""

    for para in paragraphs:
        if len(current) + len(para) + 2 <= max_chars:
            current = current + "\n\n" + para if current else para
        else:
            if current:
                chunks.append(current)
            if len(para) <= max_chars:
                current = para
            else:
                # Split on sentence-ending punctuation (Chinese 。！？ and
                # English .!?) so long English paragraphs don't get hard-split
                # mid-word. Keep the trailing punctuation with its sentence.
                # 缩写/小数点先掩码（D8e），切分后逐段还原。
                masked = _mask_sentence_dots(para)
                parts = re.split(r"([。！？.!?])", masked)
                current = ""
                for i in range(0, len(parts)):
                    part = parts[i].replace(_SENT_DOT, ".")
                    if i + 1 < len(parts) and parts[i + 1] in ("。", "！", "？", ".", "!", "?"):
                        part = part + parts[i + 1]
                        parts[i + 1] = ""
                    if not part:
                        continue
                    if len(current) + len(part) <= max_chars:
                        current += part
                    else:
                        if current:
                            chunks.append(current)
                        current = part
    if current:
        chunks.append(current)

    final: list[str] = []
    for chunk in chunks:
        if len(chunk) > max_chars:
            logger.warning("Hard-splitting text chunk of %d chars", len(chunk))
            for i in range(0, len(chunk), max_chars):
                final.append(chunk[i : i + max_chars])
        else:
            final.append(chunk)
    return final


# ---------------------------------------------------------------------------
# ProductionService
# ---------------------------------------------------------------------------


def _iter_cause_chain(e: BaseException) -> Any:
    """沿 __cause__ 链展开（重试耗尽的外层包装无分类特征，原因在 cause 上）。"""
    seen: set[int] = set()
    current: BaseException | None = e
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__


def _is_degradable_tts_error(e: Exception) -> bool:
    """该错误是否允许进入降级链（doubao→qihu→edge）。

    仅基础设施类故障可降级：传输层瞬断、结构化 429/5xx、引擎未配置。4xx 鉴权/
    参数错误与 SSE 业务码错误不可降级——降级会掩盖配置问题且换音色也救不了。
    （2026-08-09 D5：正则分类已彻底移除，改 isinstance + status_code。）
    """
    for err in _iter_cause_chain(e):
        if isinstance(err, _TRANSPORT_TTS_ERRORS):
            return True
        if "not configured" in str(err):
            return True
        if is_retryable_tts_error(err):
            return True
    return False


def _looks_like_mp3(audio: bytes) -> bool:
    """按内容嗅探容器：360 给 WAV（RIFF 头）；edge/doubao 给 mp3（ID3 或 0xFF 同步帧）。

    降级链末端产物是 edge mp3——按请求音色推格式会在降级时错位（容器/扩展名不符），
    必须按实际字节判定。
    """
    return audio[:4] != b"RIFF"


def _edge_fallback_voice_for(voice_id: str) -> str:
    """为降级选 edge 音色：同 locale 同性别的目录 edge 音色优先，否则按性别回退中文默认。"""
    from app.gateway._shared.voice_catalog import CURATED_VOICES, get_catalog_voice
    from app.gateway.voice_twin.preset_voices import parse_edge_voice_id

    entry = get_catalog_voice(voice_id)
    gender = entry.gender if entry else "female"
    locale = entry.locale if entry else "zh-CN"
    for c in CURATED_VOICES:
        if c.provider == "edge-tts" and c.locale == locale and c.gender == gender:
            return parse_edge_voice_id(c.id)
    return "zh-CN-XiaoxiaoNeural" if gender == "female" else "zh-CN-YunjianNeural"


class ProductionService(BaseTaskService):
    """Manages async production tasks (script-to-audio, ppt-to-video)."""

    domain = "voice_production"
    id_prefix = "prod"

    # Gateway recoverable statuses (excludes composing — Worker territory)
    GATEWAY_STAGES = (
        "pending",
        "running",
        "extracting",
        "converting",
        "synthesizing",
    )

    def __init__(
        self,
        repo: TaskRepository,
        voice_service: Any,
        storage_dir: str = ".deer-flow/voice-twins",
        max_concurrent_tasks: int = 3,
        tts_concurrency: int = 3,
    ) -> None:
        super().__init__(
            repo=repo,
            storage_dir=storage_dir,
            max_concurrent_tasks=max_concurrent_tasks,
        )
        self.voice_service = voice_service
        self._tts_semaphore = asyncio.Semaphore(tts_concurrency)

    _TTS_MAX_RETRIES = 3
    _TTS_RETRY_BASE_DELAY = 1.0

    async def _do_tts_synthesis(
        self,
        voice_id: str,
        text: str,
        output_format: str = "mp3",
        speed_ratio: float = 1.0,
        row: Any = None,
    ) -> bytes:
        """Route TTS with retry + degradation chain (A2, 2026-08-09).

        链路：doubao → qihu(preset) → edge-tts；preset → edge-tts。edge 兜底
        默认开启（受 voice_service.config.edge_tts.enabled 控制），是付费引擎
        全挂时的安全网（公司服务器 edge 直连可达但不稳定，见
        memory edge-tts-reachable-on-company-server）。
        **克隆音色永不降级**——静默换成别人的声音比任务失败更糟（诚实性）。
        """
        from app.gateway.voice_twin.preset_voices import (
            is_doubao_voice_id,
            is_edge_voice_id,
            is_preset_voice_id,
        )

        is_cloned = not (is_preset_voice_id(voice_id) or is_doubao_voice_id(voice_id) or is_edge_voice_id(voice_id))
        primary_err: Exception | None = None
        try:
            return await self._synthesize_with_retry(voice_id, text, output_format, speed_ratio, row)
        except Exception as e:
            # except-as 名在块尾被解绑，另存一份供链末端 from 引用
            primary_err = e
            if is_cloned or is_edge_voice_id(voice_id) or not _is_degradable_tts_error(e):
                raise
            logger.warning("TTS primary engine failed for %s, entering degradation chain: %s", voice_id, str(e)[:200])

        # doubao → qihu preset（360 key 在时）
        if is_doubao_voice_id(voice_id) and self.voice_service.qihu_client is not None:
            try:
                logger.warning("doubao degraded → 360 preset:146 for %s", voice_id)
                return await self._synthesize_with_retry("preset:146", text, output_format, speed_ratio, row)
            except Exception as qihu_err:
                if not _is_degradable_tts_error(qihu_err):
                    raise
                logger.warning("360 fallback also failed for %s: %s", voice_id, str(qihu_err)[:200])

        # 链末端：edge-tts 兜底（性别/语言匹配的目录音色）
        edge_client = getattr(self.voice_service, "edge_client", None)
        if edge_client is None:
            raise RuntimeError(f"TTS synthesis failed and edge fallback unavailable: {voice_id}") from primary_err
        edge_voice = _edge_fallback_voice_for(voice_id)
        logger.warning("TTS degraded to edge-tts voice %s (requested %s)", edge_voice, voice_id)
        return await edge_client.synthesize(text=text, voice=edge_voice, speed=speed_ratio)

    async def _synthesize_with_retry(
        self,
        voice_id: str,
        text: str,
        output_format: str = "mp3",
        speed_ratio: float = 1.0,
        row: Any = None,
    ) -> bytes:
        """Single-engine TTS with retry (no degradation — that's _do_tts_synthesis).

        For doubao voices (voice_id starts with 'doubao:'), routes to DoubaoTTSClient.
        For preset voices (voice_id starts with 'preset:'), routes to QihuTTSClient.
        For edge voices (voice_id starts with 'edge:'), routes to EdgeTTSClient (mp3).
        For cloned voices, uses the existing Volcengine path via row parameters.

        Edge voices are handled **before** ``call_with_tts_retry`` — the edge client
        has its own tenacity 3× retry internally, so wrapping it again would create
        a 3×3=9 retry nest (fix #4, 2026-08-10).
        """
        from app.gateway.voice_twin._retry import call_with_tts_retry
        from app.gateway.voice_twin.preset_voices import (
            is_doubao_voice_id,
            is_edge_voice_id,
            is_preset_voice_id,
            parse_doubao_voice_id,
            parse_edge_voice_id,
            parse_preset_voice_id,
        )

        # Edge voices: edge_client.synthesize already has tenacity 3× retry.
        # Don't double-wrap with call_with_tts_retry (would be 3×3=9 retries).
        if is_edge_voice_id(voice_id):
            edge_client = getattr(self.voice_service, "edge_client", None)
            if edge_client is None:
                raise RuntimeError("edge TTS service not configured")
            async with self._tts_semaphore:
                return await edge_client.synthesize(
                    text=text,
                    voice=parse_edge_voice_id(voice_id),
                    speed=speed_ratio,
                )

        async def _call() -> bytes:
            async with self._tts_semaphore:
                if is_doubao_voice_id(voice_id):
                    doubao_client = getattr(self.voice_service, "doubao_client", None)
                    if doubao_client is None:
                        raise RuntimeError("doubao TTS service not configured")
                    return await doubao_client.synthesize_sse(
                        speaker=parse_doubao_voice_id(voice_id),
                        text=text,
                        output_format=output_format,
                        speed_ratio=speed_ratio,
                    )
                elif is_preset_voice_id(voice_id):
                    if self.voice_service.qihu_client is None:
                        raise RuntimeError("360 TTS service not configured")
                    return await self.voice_service.qihu_client.synthesize_sse(
                        text=text,
                        voice=parse_preset_voice_id(voice_id),
                    )
                else:
                    if row is None:
                        raise ValueError("Cloned voice requires a DB row")
                    return await self.voice_service.client.synthesize_sse(
                        speaker_id=row.speaker_id,
                        text=text,
                        model_type=row.model_type,
                        output_format=output_format,
                        speed_ratio=speed_ratio,
                        language=row.language,
                    )

        # D2：重试策略走共享 helper（传输瞬断+结构化 429/5xx，指数退避 3 次）；
        # 耗尽包装 "TTS synthesis failed after N retries"（cause 挂原始错误）。
        return await call_with_tts_retry(
            _call,
            max_retries=self._TTS_MAX_RETRIES,
            base_delay=self._TTS_RETRY_BASE_DELAY,
            label="TTS synthesis",
        )

    async def preview_voice(
        self,
        voice_id: str,
        text: str,
        user_id: str,
    ) -> tuple[bytes, str]:
        """Generate a short preview clip for a voice. Returns (audio_bytes, media_type).

        Preset voices (``preset:{int}``) route to the 360/qihu client and yield WAV.
        Cloned voices route to the Volcengine client and yield MP3, after ownership
        and readiness checks (mirrors the production ``_verify_voice`` guarantees
        but local to preview so we never expose another user's voice).
        Reuses ``_do_tts_synthesis`` for synthesis so retry/concurrency are shared.
        """
        from app.gateway.voice_twin.preset_voices import is_doubao_voice_id, is_edge_voice_id, is_preset_voice_id

        if is_preset_voice_id(voice_id) or is_doubao_voice_id(voice_id) or is_edge_voice_id(voice_id):
            audio = await self._do_tts_synthesis(voice_id, text)
            # Preset (360) returns WAV; doubao/edge return mp3。
            # 注意：若走了 edge 降级兜底，实际产物是 mp3——按实际音色判定媒体类型
            # 不能只看请求的 voice_id（曾按请求音色定 ext，降级后容器/扩展名错位）。
            # 按实际字节判定媒体类型（不查请求 voice_id）——降级链 doubao→qihu
            # 会把 mp3 请求变成 WAV 产物，必须内容嗅探（fix #1, 2026-08-10）。
            if not _looks_like_mp3(audio):
                return audio, "audio/wav"
            return audio, "audio/mpeg"

        # Cloned voice: ownership + readiness checks before synthesis.
        if self.voice_service.repo is None:
            raise FileNotFoundError("Voice repository not available")
        row = await self.voice_service.repo.get_by_id(voice_id)
        if row is None:
            raise FileNotFoundError(f"Voice not found: {voice_id}")
        if row.user_id != user_id:
            raise PermissionError("Not your voice twin")
        if row.status != "ready":
            raise ValueError(f"Voice is not ready (status={row.status})")

        audio = await self._do_tts_synthesis(voice_id, text, row=row)
        return audio, "audio/mpeg"

    # --- Public submit helpers (preserve original API for router) ---

    @staticmethod
    def _make_title(text: str, title: str | None = None) -> str:
        """Generate a task title from text or use the provided title."""
        if title:
            return title
        cleaned = text.strip().replace("\n", " ")
        if len(cleaned) <= 20:
            return cleaned
        return cleaned[:20] + "..."

    async def submit_script_to_audio(
        self,
        user_id: str,
        voice_id: str,
        voice_name: str,
        text: str,
        speed_ratio: float = 1.0,
        output_format: str = "mp3",
        title: str | None = None,
    ) -> Any:
        title = self._make_title(text, title)
        payload = {
            "voice_id": voice_id,
            "voice_name": voice_name,
            "title": title,
            "text": text,
            "speed_ratio": speed_ratio,
            "output_format": output_format,
            "task_type": "script_to_audio",
        }
        return await self.submit(
            user_id=user_id,
            task_type="script_to_audio",
            payload=payload,
            priority=70,
        )

    async def submit_ppt_to_video(
        self,
        user_id: str,
        voice_id: str,
        voice_name: str,
        pptx_path: str,
        preset: str = "none",
        subtitle: bool = False,
        title: str | None = None,
        accept_layout_diff: bool = False,
    ) -> Any:
        if not title:
            title = "PPT转视频"
        payload = {
            "voice_id": voice_id,
            "voice_name": voice_name,
            "title": title,
            "pptx_path": pptx_path,
            "preset": preset,
            "subtitle": subtitle,
            "accept_layout_diff": accept_layout_diff,
            "task_type": "ppt_to_video",
        }
        task = await self.submit(
            user_id=user_id,
            task_type="ppt_to_video",
            payload=payload,
            priority=50,
            auto_start=bool(pptx_path),
        )
        return task

    def start_ppt_pipeline(
        self,
        task_id: str,
        user_id: str,
        voice_id: str,
        pptx_path: str,
        preset: str,
        subtitle: bool = False,
        title: str | None = None,
        voice_name: str | None = None,
        accept_layout_diff: bool = False,
    ) -> None:
        """Launch the background PPT-to-video pipeline after file has been saved."""
        payload = {
            "voice_id": voice_id,
            "voice_name": voice_name or "",
            "title": title or "PPT转视频",
            "pptx_path": pptx_path,
            "preset": preset,
            "subtitle": subtitle,
            "accept_layout_diff": accept_layout_diff,
            "task_type": "ppt_to_video",
        }
        async_task = asyncio.create_task(self._wrap_pipeline(task_id, user_id, "ppt_to_video", payload))
        self._active_tasks[task_id] = async_task

    # --- Worker handoff ---

    def _should_handoff_to_worker(self, task_type: str) -> bool:
        return task_type == "ppt_to_video"

    # --- Pipeline implementation ---

    async def _run_pipeline(
        self,
        task_id: str,
        user_id: str,
        task_type: str,
        payload: dict,
        task_dir: Path,
        progress: ProgressUpdater,
    ) -> dict | None:
        if task_type == "script_to_audio":
            return await self._run_script_to_audio(task_id, user_id, payload, task_dir, progress)
        elif task_type == "ppt_to_video":
            return await self._run_ppt_to_video(task_id, user_id, payload, task_dir, progress)
        else:
            raise ValueError(f"Unknown task_type: {task_type}")

    async def _run_script_to_audio(
        self,
        task_id: str,
        user_id: str,
        payload: dict,
        task_dir: Path,
        progress: ProgressUpdater,
    ) -> dict | None:
        """Script-to-audio pipeline. Returns None (Gateway-complete → ready)."""
        voice_id = payload["voice_id"]
        text = payload["text"]
        speed_ratio = payload.get("speed_ratio", 1.0)
        output_format = payload.get("output_format", "mp3")

        audio_dir = task_dir / "audio"
        audio_dir.mkdir(parents=True, exist_ok=True)
        output_path = task_dir / f"output.{output_format}"

        await progress(status="synthesizing", progress=5)

        # Resolve voice: preset/doubao/edge voices skip DB lookup
        from app.gateway.voice_twin.preset_voices import is_doubao_voice_id, is_edge_voice_id, is_preset_voice_id

        row = None
        if not is_preset_voice_id(voice_id) and not is_doubao_voice_id(voice_id) and not is_edge_voice_id(voice_id):
            if self.voice_service.repo is None:
                raise RuntimeError("Voice clone repository not initialized")
            row = await self.voice_service.repo.get_by_id(voice_id)
            if row is None:
                raise FileNotFoundError(f"Voice not found: {voice_id}")
            if row.user_id != user_id:
                raise PermissionError(f"Voice {voice_id} does not belong to user {user_id}")
            if row.status != "ready":
                raise ValueError(f"Voice is not ready (status={row.status})")

        # 360 TTS (preset voices) returns WAV bytes regardless of output_format;
        # Volcengine (cloned voices) and doubao return the requested format;
        # edge-tts returns mp3. 降级链 doubao→qihu 会把 mp3 请求变成 WAV 产物，
        # preset→edge 会把 WAV 请求变成 mp3——按实际字节判定是否转码，不查请求
        # voice_id（fix #1, 2026-08-10）。

        chunks = split_text(text, max_chars=5000)
        total = len(chunks)
        completed = 0
        progress_lock = asyncio.Lock()

        async def _synthesize_and_save(index: int, chunk: str) -> tuple[int, Path | Exception]:
            nonlocal completed
            try:
                audio_bytes = await self._do_tts_synthesis(
                    voice_id=voice_id,
                    text=chunk,
                    output_format=output_format,
                    speed_ratio=speed_ratio,
                    row=row,
                )
                chunk_path = audio_dir / f"chunk-{index:03d}.{output_format}"
                # 按实际字节判定是否需要转码（不查请求 voice_id）——降级链
                # doubao→qihu 会把 mp3 请求变成 WAV 产物（fix #1, 2026-08-10）。
                actual_is_mp3 = _looks_like_mp3(audio_bytes)
                if actual_is_mp3 and output_format == "mp3":
                    chunk_path.write_bytes(audio_bytes)
                else:
                    src_suffix = ".mp3" if actual_is_mp3 else ".wav"
                    wav_tmp = audio_dir / f"_chunk-{index:03d}{src_suffix}"
                    wav_tmp.write_bytes(audio_bytes)
                    try:
                        proc = await run_ffmpeg(_transcode_wav, wav_tmp, chunk_path)
                    finally:
                        # D6：run_ffmpeg 抛异常时也要清临时文件（曾泄漏至任务存活期）
                        wav_tmp.unlink(missing_ok=True)
                    if proc.returncode != 0:
                        raise RuntimeError(_ffmpeg_err(proc.stderr, "TTS transcode failed"))
                async with progress_lock:
                    completed += 1
                    pct = 5 + int(completed / total * 90)
                    await progress(progress=pct)
                return (index, chunk_path)
            except Exception as e:
                return (index, e)

        results = await asyncio.gather(*[_synthesize_and_save(i, chunk) for i, chunk in enumerate(chunks)])

        # Check for failures
        errors = [(idx, r) for idx, r in results if isinstance(r, Exception)]
        if errors:
            # Clean up successful chunk files
            for _, r in results:
                if isinstance(r, Path):
                    r.unlink(missing_ok=True)
            first_err = errors[0][1]
            raise RuntimeError(f"TTS synthesis failed ({len(errors)}/{total} chunks): {first_err}")

        results.sort(key=lambda r: r[0])
        chunk_paths = [r[1] for r in results]

        # Concat or rename
        if len(chunk_paths) > 1:
            await run_ffmpeg(_ffmpeg_concat, chunk_paths, output_path)
            for p in chunk_paths:
                if p.exists():
                    p.unlink()
            if audio_dir.exists() and not any(audio_dir.iterdir()):
                audio_dir.rmdir()
        else:
            chunk_paths[0].rename(output_path)

        file_size = output_path.stat().st_size
        await progress(progress=95)
        await progress.set_result(
            output_path=str(output_path),
            file_size=file_size,
        )
        return None  # Gateway-complete → _wrap_pipeline sets ready

    async def _run_ppt_to_video(
        self,
        task_id: str,
        user_id: str,
        payload: dict,
        task_dir: Path,
        progress: ProgressUpdater,
    ) -> dict | None:
        """PPT-to-video pipeline (Gateway part only). Returns dict for Worker handoff."""
        voice_id = payload["voice_id"]
        pptx_path = payload.get("pptx_path", "")
        preset = payload.get("preset", "none")
        subtitle = payload.get("subtitle", False)

        if not pptx_path:
            raise ValueError("pptx_path is required")

        # ── Step 1: Extract (extracting) ──
        await progress(status="extracting", progress=5)

        from app.gateway.voice_twin.preset_voices import is_doubao_voice_id, is_edge_voice_id, is_preset_voice_id

        row = None
        if not is_preset_voice_id(voice_id) and not is_doubao_voice_id(voice_id) and not is_edge_voice_id(voice_id):
            if self.voice_service.repo is None:
                raise RuntimeError("Voice clone repository not initialized")
            row = await self.voice_service.repo.get_by_id(voice_id)
            if row is None:
                raise FileNotFoundError(f"Voice not found: {voice_id}")
            if row.user_id != user_id:
                raise PermissionError(f"Voice {voice_id} does not belong to user {user_id}")
            if row.status != "ready":
                raise ValueError(f"Voice is not ready (status={row.status})")

        from app.gateway.voice_twin.ppt_extract import (
            analyze_fonts,
            analyze_notes,
            convert_pptx_to_images_auto,
            extract_pptx_content,
        )

        slides = await asyncio.to_thread(extract_pptx_content, pptx_path)
        if not slides:
            raise ValueError("PPT has no slides")

        notes_analysis = analyze_notes(slides)
        if not notes_analysis.is_sufficient:
            raise ValueError(
                f"可朗读内容不充分（{notes_analysis.slides_with_notes}/{notes_analysis.total_slides} 页有备注，{notes_analysis.slides_with_content}/{notes_analysis.total_slides} 页有备注或充足正文）。请为每页添加讲解备注或正文要点。"
            )

        await progress(progress=10)
        await progress.set_result(slide_count=len(slides))

        # ── Step 1b: Font availability gate ──
        # Check for un-embedded fonts that would be substituted by LibreOffice
        # → text wrap errors. Block unless user accepted layout diff.
        accept_layout_diff = bool(payload.get("accept_layout_diff", False))
        font_analysis = await asyncio.to_thread(analyze_fonts, pptx_path)
        if font_analysis.risky and not accept_layout_diff:
            risky_list = "、".join(f"「{n}」" for n, _ in font_analysis.risky[:5])
            raise ValueError(f"missing-font-detected: 检测到未嵌入字体 {risky_list}，转视频可能出现文字换行错乱。请在 PowerPoint「文件→选项→保存→勾选将字体嵌入文件」后重新上传，或在下方点「接受版式差异，继续生成」。")

        # ── Step 2: Convert to images (converting) ──
        await progress(status="converting", progress=15)

        image_paths = await asyncio.to_thread(
            convert_pptx_to_images_auto,
            pptx_path,
            str(task_dir),
            200,
        )
        if len(image_paths) != len(slides):
            raise RuntimeError(f"Image count mismatch: {len(image_paths)} images vs {len(slides)} slides")

        await progress(progress=20)

        # ── Step 3: Synthesize audio per slide (synthesizing) ──
        await progress(status="synthesizing", progress=25)

        audio_dir = task_dir / "audio"
        audio_dir.mkdir(exist_ok=True)

        # Determine narration text per slide (shared by TTS and subtitles)
        slide_narrations: list[str] = []
        for slide in slides:
            narration = slide.notes if slide.has_notes else slide.body
            if not narration.strip():
                narration = slide.title or "(无内容)"
            slide_narrations.append(narration)

        # R5（2026-09-09）：逐页 TTS 串行 for → gather 并发。_do_tts_synthesis 内部
        # 持 _tts_semaphore（默认 3）天然限流，TTS 端 QPS 不随页数上升；文件名按页
        # 唯一（slide-{i:02d}/_slide-{i:02d}）无写冲突。进度按完成计数单调递增
        # （乱序完成不回退，兼容 ProgressUpdater 高水位）；错误对齐 _run_script_to_audio
        # 的收集范式（:602-612）：收齐后清理已成功页文件、raise 第一个异常——任务级
        # 语义与串行版一致（首个失败原样上抛，不吞不换形）。
        total = len(slide_narrations)
        progress_lock = asyncio.Lock()
        completed = 0

        async def _synthesize_slide(i: int, narration: str) -> tuple[int, Path | Exception]:
            nonlocal completed
            try:
                audio_bytes = await self._do_tts_synthesis(
                    voice_id=voice_id,
                    text=narration,
                    output_format="mp3",
                    speed_ratio=1.0,
                    row=row,
                )
                # 按实际字节判定是否需要转码（不查请求 voice_id）——降级链
                # doubao→qihu 会把 mp3 请求变成 WAV 产物（fix #1, 2026-08-10）。
                audio_path = audio_dir / f"slide-{i + 1:02d}.mp3"
                if _looks_like_mp3(audio_bytes):
                    audio_path.write_bytes(audio_bytes)
                else:
                    wav_tmp = audio_dir / f"_slide-{i + 1:02d}.wav"
                    wav_tmp.write_bytes(audio_bytes)
                    try:
                        proc = await run_ffmpeg(_transcode_wav, wav_tmp, audio_path)
                    finally:
                        # D6：run_ffmpeg 抛异常时也要清临时文件（曾泄漏至任务存活期）
                        wav_tmp.unlink(missing_ok=True)
                    if proc.returncode != 0:
                        raise RuntimeError(_ffmpeg_err(proc.stderr, "TTS transcode failed"))
                async with progress_lock:
                    completed += 1
                    pct = 25 + int(completed / total * 50)
                    await progress(progress=pct)
                return (i, audio_path)
            except Exception as e:
                return (i, e)

        results = await asyncio.gather(*[_synthesize_slide(i, narration) for i, narration in enumerate(slide_narrations)])
        results.sort(key=lambda r: r[0])

        errors = [(idx, r) for idx, r in results if isinstance(r, Exception)]
        if errors:
            # 收集范式：清理已成功页音频再上抛第一个错误（对齐 :607-611）
            for _, r in results:
                if isinstance(r, Path):
                    r.unlink(missing_ok=True)
            raise errors[0][1]

        # 后续（字幕/worker handoff）按 audio_dir/slide-{i:02d}.mp3 命名直接取文件，
        # 无需持有列表（串行版的 slide_audios 本就无消费点）。

        # ── Step 3.5: Persist subtitle info for Worker ──
        if subtitle:
            # T9 (2026-08-13): gateway 探时长 + segment() + per-slide ASS（PlayRes=1920×1080）
            from app.gateway.video_gen.orchestration.tts import _get_audio_duration
            from app.gateway.video_gen.subtitle_engine import (
                segment,
                write_ass,
                write_srt,
            )

            subtitles_data = []
            for i, narration in enumerate(slide_narrations):
                audio_path = audio_dir / f"slide-{i + 1:02d}.mp3"
                dur = _get_audio_duration(str(audio_path)) if audio_path.exists() else 0.0
                # P0-5：探时长失败/文件缺失（dur<=0）→ 不写 srt/ass，
                # ass_file=None → worker 自动落回旧路径（旧路径自行 ffprobe）。
                cues = segment(narration, audio_duration=dur, orientation="landscape") if dur > 0 else []
                # Per-slide relative time base (避开 xfade 重叠计算)
                sub_prefix = f"sub-{i + 1:02d}"
                srt_path = str(task_dir / f"{sub_prefix}.srt")
                ass_path = str(task_dir / f"{sub_prefix}.ass")
                if cues:
                    write_srt(cues, out_path=srt_path)
                    try:
                        write_ass(cues, out_path=ass_path, video_w=1920, video_h=1080)
                    except Exception:
                        logger.warning("PPT subtitle ASS generation failed for slide %d", i + 1, exc_info=True)
                subtitles_data.append(
                    {
                        "slide": i + 1,
                        "text": narration,
                        "ass_file": ass_path if Path(ass_path).exists() else None,
                    }
                )
            (task_dir / "subtitles.json").write_text(json.dumps(subtitles_data, ensure_ascii=False), encoding="utf-8")

        # Return result dict for Worker handoff (payload will be merged)
        return {"preset": preset, "subtitle": subtitle}

    # --- File serving ---

    def get_output_path(self, task_id: str, user_id: str) -> Path | None:
        """Resolve the output file path for a task."""
        task_dir = Path(self._storage_dir) / user_id / "tasks" / task_id
        if not task_dir.exists():
            return None
        for ext in ("mp3", "wav", "ogg", "mp4"):
            path = task_dir / f"output.{ext}"
            if path.exists():
                return path
        return None

    # --- Override cleanup for partial intermediates ---

    async def _cleanup_task(self, task: Any) -> None:
        """Clean up task directory on delete/recover."""
        task_dir = Path(self._storage_dir) / task.user_id / "tasks" / task.id
        if task_dir.exists():
            shutil.rmtree(task_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# ffmpeg helpers (module-level, shared with Worker via pipeline.py)
# ---------------------------------------------------------------------------


def _ffmpeg_err(stderr_bytes: bytes, prefix: str) -> str:
    raw = stderr_bytes.decode(errors="replace")
    err_lines = [line.strip() for line in raw.splitlines() if any(kw in line.lower() for kw in ("error", "invalid", "cannot", "no such", "not found", "failed"))]
    if err_lines:
        return f"{prefix}: {'; '.join(err_lines)}"
    return f"{prefix}: ...{raw[-500:]}"


def _transcode_wav(wav_path: Path, out_path: Path) -> subprocess.CompletedProcess:
    """Transcode 360 WAV to the requested container; codec chosen by extension.

    曾固定 libmp3lame：ogg muxer 拒绝 mp3 编码（任务必败）、wav 得到 mp3 帧
    伪装 wav（容器/扩展名不符）——2026-07-22 审计 P1#6。
    """
    codec = {
        ".mp3": ["-c:a", "libmp3lame", "-b:a", "128k"],
        ".ogg": ["-c:a", "libvorbis", "-q:a", "4"],
        ".wav": ["-c:a", "pcm_s16le"],
    }.get(out_path.suffix.lower())
    if codec is None:
        raise ValueError(f"unsupported output format for preset voice: {out_path.suffix}")
    return subprocess.run(
        ["ffmpeg", "-y", "-i", str(wav_path), *codec, str(out_path)],
        capture_output=True,
        timeout=30,
    )


def _ffmpeg_concat(chunk_paths: list[Path], output_path: Path) -> None:
    filelist = output_path.parent / "filelist.txt"
    try:
        with open(filelist, "w") as f:
            for p in chunk_paths:
                f.write(f"file '{p}'\n")

        proc = subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(filelist), "-c", "copy", str(output_path)],
            capture_output=True,
            timeout=60,
        )
        if proc.returncode != 0:
            raise RuntimeError(_ffmpeg_err(proc.stderr, "ffmpeg concat failed"))
    finally:
        if filelist.exists():
            filelist.unlink()
