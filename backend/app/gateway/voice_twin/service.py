"""Voice clone service — business logic, transcoding, demo caching."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.gateway._shared.executors import run_ffmpeg
from app.gateway.voice_twin._retry import call_with_tts_retry
from app.gateway.voice_twin.config import VoiceCloneConfig
from app.gateway.voice_twin.errors import get_friendly_error
from app.gateway.voice_twin.models import VoiceTwinRow
from app.gateway.voice_twin.repository import VoiceTwinRepository
from app.gateway.voice_twin.volcengine_client import VolcengineTTSClient, VolcengineTTSHTTPError

logger = logging.getLogger(__name__)

SUPPORTED_FORMATS = {"wav", "mp3", "ogg", "m4a", "aac", "pcm"}
NEEDS_TRANSCODE_FORMATS = {"webm"}

_VOLC_STATUS_MAP = {
    0: "uploading",
    1: "training",
    2: "ready",  # 训练完成
    3: "failed",
    # 4 = 训练完成（V3 新版终态语义无官方文档，实测与 2 等价——V3 voice_clone
    # 响应里训练成功返回 4；保守映射 ready，未知值在调用方落 "training" 继续轮询）
    4: "ready",
}


def _check_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


def _probe_duration(path: Path, ext: str) -> float:
    """Best-effort audio duration in seconds.

    Tries ffprobe first (handles all formats); falls back to the stdlib wave
    module for WAV (header parse, no deps). Returns 0.0 if it cannot determine
    the duration so callers can proceed without blocking on metadata.
    """
    if shutil.which("ffprobe") is not None:
        import subprocess

        try:
            proc = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if proc.returncode == 0:
                return float(proc.stdout.strip())
        except (ValueError, subprocess.SubprocessError):
            pass

    if ext == "wav":
        import wave

        try:
            with wave.open(str(path), "rb") as wf:
                return wf.getnframes() / float(wf.getframerate() or 1)
        except Exception:
            pass

    return 0.0


def _ensure_dirs(config: VoiceCloneConfig) -> Path:
    base = Path(config.storage_dir)
    for sub in ("temp", "originals", "demos", "outputs"):
        (base / sub).mkdir(parents=True, exist_ok=True)
    return base


class VoiceCloneService:
    def __init__(
        self,
        config: VoiceCloneConfig,
        session_factory: async_sessionmaker | None = None,
    ) -> None:
        self.config = config
        self.repo: VoiceTwinRepository | None = None
        if session_factory is not None:
            self.repo = VoiceTwinRepository(session_factory)
        self.client = VolcengineTTSClient(
            appid=config.appid,
            token=config.token,
            access_key=config.access_key,
            domain=config.domain,
            status_query_timeout=config.status_query_timeout,
        )
        self.qihu_client = None
        qihu_key = config.qihu_tts.api_key
        if not qihu_key and config.qihu_tts.enabled:
            import os

            qihu_key = os.environ.get("QIHU_API_KEY", "")
        if qihu_key and config.qihu_tts.enabled:
            from app.gateway.voice_twin.qihu_tts_client import QihuTTSClient

            self.qihu_client = QihuTTSClient(api_key=qihu_key)
        # Stage 2a: doubao seed-tts-2.0 client (openspeech V3 SSE, same access_key
        # as voice clone — single openspeech account). Created when access_key is
        # available and doubao_tts is enabled. See
        # backend/docs/BYTEDANCE_MODELS_INTEGRATION_PLAN.md §阶段 2a.
        self.doubao_client = None
        if config.access_key and config.doubao_tts.enabled:
            from app.gateway.voice_twin.doubao_tts_client import DoubaoTTSClient

            self.doubao_client = DoubaoTTSClient(access_key=config.access_key)
        # A2：edge-tts 客户端（免费多语种音色 + 降级链末端兜底）。默认开启。
        self.edge_client = None
        if config.edge_tts.enabled:
            from app.gateway.voice_twin.edge_tts_client import EdgeTTSClient

            self.edge_client = EdgeTTSClient(timeout=config.edge_tts.timeout)
        self._ffmpeg_available = _check_ffmpeg()
        if not self._ffmpeg_available:
            logger.warning("ffmpeg not found in PATH. WebM transcoding will fail.")
        self._storage_base: Path | None = None
        self._training_reaper_task: asyncio.Task | None = None
        self._storage_reaper_task: asyncio.Task | None = None

    def _get_storage_base(self) -> Path:
        if self._storage_base is None:
            self._storage_base = _ensure_dirs(self.config)
        return self._storage_base

    async def prescreen(self, audio_bytes: bytes, filename: str, user_id: str = "") -> dict:
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        warnings: list[str] = []
        needs_transcode = ext in NEEDS_TRANSCODE_FORMATS

        if ext in NEEDS_TRANSCODE_FORMATS and not self._ffmpeg_available:
            return {"status": "fail", "error": f"音频格式 {ext} 需要 ffmpeg 转码，但系统未安装 ffmpeg", "temp_file_id": "", "duration": 0, "format": ext, "needs_transcode": needs_transcode}

        if len(audio_bytes) > self.config.max_upload_bytes:
            return {"status": "fail", "error": f"音频文件大小超过限制 ({self.config.max_upload_bytes // 1024 // 1024}MB)", "temp_file_id": "", "duration": 0, "format": ext, "needs_transcode": False}

        if ext not in SUPPORTED_FORMATS and ext not in NEEDS_TRANSCODE_FORMATS:
            return {"status": "fail", "error": f"不支持的音频格式: {ext}", "temp_file_id": "", "duration": 0, "format": ext, "needs_transcode": False}

        if needs_transcode:
            warnings.append("音频为 webm 格式，将自动转码为 wav")

        # temp_file_id 绑定用户（sha1(user_id)[:8] 前缀）——submit_training 校验该前缀，
        # 防止拿他人 prescreen 产物提交克隆（P2 安全）。空 user_id 兜底 "anon"。
        user_tag = hashlib.sha1(user_id.encode()).hexdigest()[:8] if user_id else "anon"
        temp_file_id = f"tmp_{user_tag}_{uuid.uuid4().hex[:12]}"
        base = self._get_storage_base()
        temp_path_with_ext = base / "temp" / f"{temp_file_id}.{ext}"
        temp_path_with_ext.write_bytes(audio_bytes)

        # Probe the real duration (was hard-coded 0). Enforce the configured
        # minimum so the frontend's duration display reflects reality and short
        # clips are rejected before a wasted Volcengine upload.
        duration = await run_ffmpeg(_probe_duration, temp_path_with_ext, ext)
        if duration <= 0:
            warnings.append("无法检测音频时长，已跳过时长校验")
        elif duration < self.config.min_duration_seconds:
            temp_path_with_ext.unlink(missing_ok=True)
            return {
                "status": "fail",
                "error": f"音频时长过短 ({duration:.1f}s < 最低 {self.config.min_duration_seconds:.0f}s)",
                "temp_file_id": "",
                "duration": duration,
                "format": ext,
                "needs_transcode": needs_transcode,
            }

        return {
            "status": "pass",
            "temp_file_id": temp_file_id,
            "duration": duration,
            "format": ext,
            "needs_transcode": needs_transcode,
            "warnings": warnings,
        }

    async def submit_training(
        self,
        temp_file_id: str,
        voice_name: str,
        model_type: int,
        language: int,
        extra_params: dict | None,
        user_id: str = "default",
        speaker_id: str | None = None,
    ) -> VoiceTwinRow:
        if self.repo is None:
            raise RuntimeError("Repository not initialized")

        base = self._get_storage_base()
        temp_dir = base / "temp"
        temp_file: Path | None = None
        for f in temp_dir.iterdir():
            if f.stem == temp_file_id:
                temp_file = f
                break

        if temp_file is None:
            raise FileNotFoundError(f"临时文件不存在: {temp_file_id}")

        # temp_file_id 前缀绑定用户：拿他人 prescreen 产物提交克隆 → 拒绝（P2 安全）。
        # 空 user_id 与 prescreen 同兜底 "anon"（曾 sha1("") 与 "anon" 不一致，直调服务必误拒）。
        expected_tag = hashlib.sha1(user_id.encode()).hexdigest()[:8] if user_id else "anon"
        if not temp_file.stem.startswith(f"tmp_{expected_tag}_"):
            raise PermissionError("temp file does not belong to this user")

        audio_bytes = temp_file.read_bytes()
        ext = temp_file.suffix.lstrip(".")
        audio_format = ext

        if ext in NEEDS_TRANSCODE_FORMATS:
            audio_bytes, audio_format = await run_ffmpeg(self._transcode_to_wav, audio_bytes)

        voice_id = uuid.uuid4().hex[:16]
        original_path = base / "originals" / f"{voice_id}.{ext}"
        temp_file.rename(original_path)

        if speaker_id is None:
            # Post-paid: auto-generate custom_speaker_id.
            # NOTE: MUST NOT start with S_, ICL_, MIX_, DiT_, BV (reserved, see V3 regex).
            speaker_id = f"usr_{voice_id}"

        # Probe duration of the persisted original so the DB column (and the
        # frontend duration display) reflects reality instead of always null.
        duration = await run_ffmpeg(_probe_duration, original_path, ext)

        row = VoiceTwinRow(
            id=voice_id,
            user_id=user_id,
            voice_name=voice_name,
            speaker_id=speaker_id,
            model_type=model_type,
            language=language,
            extra_params=json.dumps(extra_params) if extra_params else None,
            status="training",
            source_audio_path=str(original_path),
            source_audio_format=ext,
            source_audio_duration=duration or None,
        )
        row = await self.repo.create(row)

        try:
            # D2：克隆上传接入共享重试（曾裸调，一次 429/5xx/传输抖动即训练失败）
            result = await call_with_tts_retry(
                lambda: self.client.upload_audio(
                    speaker_id=speaker_id,
                    audio_bytes=audio_bytes,
                    audio_format=audio_format,
                    model_type=model_type,
                    language=language,
                    extra_params=extra_params,
                ),
                label="voice clone upload",
            )
            # V3 normalised response: {"speaker_id", "status", "demo_audio"}
            # V1 raw response:         {"BaseResp": {"StatusCode", ...}, "speaker_id", ...}
            if "BaseResp" in result:
                # V1 format
                status_code = result.get("BaseResp", {}).get("StatusCode", -1)
                if status_code != 0:
                    error_msg = result.get("BaseResp", {}).get("StatusMessage", "Upload failed")
                    await self.repo.update_status(voice_id, status="failed", error_code=status_code, error_message=error_msg)
                    row = await self.repo.get_by_id(voice_id)
                else:
                    # V1 success
                    volc_status = result.get("status", 1)
                    new_status = _VOLC_STATUS_MAP.get(volc_status, "training")
                    update_kwargs: dict = {"status": new_status}
                    if result.get("demo_audio"):
                        try:
                            update_kwargs["demo_audio_path"] = await self._cache_demo_audio(voice_id, result["demo_audio"])
                        except Exception:
                            # demo 缓存 best-effort：URL 过期/网络抖动不得把已成功的
                            # 训练误判 failed（P1#4），demo_audio_path 留空即可。
                            logger.exception("demo audio cache failed for %s; training status unaffected", voice_id)
                    row = await self.repo.update_status(voice_id, **update_kwargs)
            else:
                # V3 format — HTTP 200 = API success, check training status.
                # Default to "training" (not "failed") for unknown status codes
                # so a new/undocumented Volcengine value isn't misjudged as
                # failure; poll_status will converge on the real terminal state.
                volc_status = result.get("status", 0)
                new_status = _VOLC_STATUS_MAP.get(volc_status, "training")
                update_kwargs: dict = {"status": new_status}
                if new_status == "failed":
                    update_kwargs["error_message"] = "Training failed on remote"
                if result.get("demo_audio"):
                    try:
                        update_kwargs["demo_audio_path"] = await self._cache_demo_audio(voice_id, result["demo_audio"])
                    except Exception:
                        # demo 缓存 best-effort：URL 过期/网络抖动不得把已成功的
                        # 训练误判 failed（P1#4），demo_audio_path 留空即可。
                        logger.exception("demo audio cache failed for %s; training status unaffected", voice_id)
                row = await self.repo.update_status(voice_id, **update_kwargs)
        except Exception as e:
            logger.exception("Volcengine upload failed for voice %s", voice_id)
            await self.repo.update_status(voice_id, status="failed", error_message=str(e))
            row = await self.repo.get_by_id(voice_id)

        # D8a：update_status/get_by_id 在并发删除下可返 None——显式 404（路由层
        # 已映射 FileNotFoundError→404），删除 type: ignore 掩耳盗铃。
        if row is None:
            raise FileNotFoundError(f"Voice not found after training submission: {voice_id}")
        return row

    def _transcode_to_wav(self, audio_bytes: bytes) -> tuple[bytes, str]:
        if not self._ffmpeg_available:
            raise RuntimeError("ffmpeg not available for WebM transcoding")

        import subprocess

        proc = subprocess.run(
            ["ffmpeg", "-i", "pipe:0", "-f", "wav", "-ac", "1", "-ar", "24000", "pipe:1"],
            input=audio_bytes,
            capture_output=True,
            timeout=30,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg transcoding failed: {proc.stderr.decode()[:500]}")
        return proc.stdout, "wav"

    async def poll_status(self, voice_id: str) -> VoiceTwinRow | None:
        if self.repo is None:
            raise RuntimeError("Repository not initialized")

        row = await self.repo.get_by_id(voice_id)
        if row is None:
            return None

        if row.status not in ("training", "uploading"):
            return row

        # 训练超时收敛：远端永不就绪则永卡 training + 前端 5s 无限轮询（审计 P2）
        timeout_minutes = self.config.training_timeout_minutes
        created = getattr(row, "created_at", None)
        if created is not None:
            age = (datetime.now(UTC) - created.replace(tzinfo=created.tzinfo or UTC)).total_seconds()
            if age > timeout_minutes * 60:
                return await self.repo.update_status(voice_id, status="failed", error_message=f"训练超时（{timeout_minutes}分钟），请重新提交")

        try:
            result = await self.client.get_status(row.speaker_id, model_type=row.model_type)
            if result.get("_error"):
                # V1 API 级错误（_get_status_v1 已归一化）
                return await self.repo.update_status(voice_id, status="failed", error_message=str(result["_error"]))
            volc_status = result.get("status", 0)
            # Unknown status → "training" (keep polling), not "failed".
            new_status = _VOLC_STATUS_MAP.get(volc_status, "training")

            update_kwargs: dict = {"status": new_status}

            if new_status == "failed":
                # V1 error info in BaseResp, V3 has no extra error code on the status endpoint
                base_resp = result.get("BaseResp", {})
                code = base_resp.get("StatusCode") if base_resp else None
                # StatusCode=0/None 时不得把 ERROR_MESSAGES[0]="成功" 显示为失败原因。
                update_kwargs["error_message"] = get_friendly_error(code, for_failure=True) if code else "Training failed on remote"

            if new_status == "ready" and result.get("demo_audio"):
                demo_url = result["demo_audio"]
                try:
                    update_kwargs["demo_audio_path"] = await self._cache_demo_audio(voice_id, demo_url)
                except Exception:
                    # demo 缓存 best-effort：URL 过期/网络抖动不得让已就绪音色
                    # 永卡 training（P1#4），demo_audio_path 留空即可。
                    logger.exception("demo audio cache failed for %s; training status unaffected", voice_id)

            row = await self.repo.update_status(voice_id, **update_kwargs)
        except (httpx.TimeoutException, httpx.TransportError) as e:
            # 传输瞬断（超时/网络）：保持 training，下次轮询再试（现状）。
            logger.warning("Failed to poll status for %s: %s", voice_id, e)
        except (KeyError, TypeError, ValueError) as e:
            # 代码/数据形态错误：吞掉会让 bug 隐形整个超时窗口——立即收敛 failed。
            logger.exception("Status polling internal error for %s", voice_id)
            row = await self.repo.update_status(voice_id, status="failed", error_message=f"状态轮询内部错误: {e}")
        except VolcengineTTSHTTPError as e:
            # 4xx（鉴权/参数错）立即标 failed，不让 token 过期误报"训练超时"30 分钟（fix M1, 2026-08-10）。
            if e.status_code is not None and e.status_code < 500:
                logger.warning("Status polling auth/param error for %s: HTTP %s", voice_id, e.status_code)
                row = await self.repo.update_status(voice_id, status="failed", error_message=f"火山引擎错误 (HTTP {e.status_code}): {e}")
            else:
                # 5xx 归入传输瞬断，保持 training 继续轮询。
                logger.warning("Failed to poll status for %s: %s", voice_id, e)
        except Exception as e:
            logger.warning("Failed to poll status for %s: %s", voice_id, e)

        return row

    # ------------------------------------------------------------------
    # 训练巡检 reaper：用户关页后无人轮询 poll_status，超时收敛永不触发，
    # 行会永卡 training——后台任务每 training_reaper_interval_minutes 分钟
    # 扫一轮超时的 training/uploading 行并收敛（D1）。
    # ------------------------------------------------------------------

    def start_training_reaper(self) -> None:
        """启动训练行巡检后台任务（幂等，重复调用不重复启动）。

        repo 为 None 或 training_reaper_interval_minutes <= 0 时 no-op。
        """
        interval_minutes = self.config.training_reaper_interval_minutes
        if interval_minutes <= 0 or self.repo is None:
            return
        if self._training_reaper_task is not None and not self._training_reaper_task.done():
            return  # already running

        async def _loop() -> None:
            while True:
                try:
                    await self._reap_stale_training()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("Voice clone training reaper sweep errored (will retry next tick)")
                await asyncio.sleep(interval_minutes * 60)

        self._training_reaper_task = asyncio.create_task(_loop(), name="voice-clone-training-reaper")

    async def stop_training_reaper(self) -> None:
        """取消训练巡检后台任务（app shutdown 对称清理）。"""
        t = self._training_reaper_task
        self._training_reaper_task = None
        if t is not None and not t.done():
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass

    async def _reap_stale_training(self) -> int:
        """扫一轮：超时 training/uploading 行先尝试一次真实远端 poll，

        ready → 更新状态（demo_audio 走既有 best-effort 缓存）；
        非 ready 或 poll 异常 → 标 failed 收敛。每行独立 try/except。
        """
        if self.repo is None:
            return 0
        timeout_minutes = self.config.training_timeout_minutes
        timeout_seconds = timeout_minutes * 60
        rows = await self.repo.list_by_status(["training", "uploading"])
        reaped = 0
        for row in rows:
            try:
                created = getattr(row, "created_at", None)
                if created is None:
                    continue
                age = (datetime.now(UTC) - created.replace(tzinfo=created.tzinfo or UTC)).total_seconds()
                if age <= timeout_seconds:
                    continue

                # 先真实 poll 一次：远端已就绪不冤枉慢训练；未就绪/poll 失败再标 failed。
                ready_result: dict | None = None
                try:
                    result = await self.client.get_status(row.speaker_id, model_type=row.model_type)
                    if _VOLC_STATUS_MAP.get(result.get("status", 0), "training") == "ready":
                        ready_result = result
                except Exception:
                    logger.warning("Training reaper poll failed for %s; marking timed-out row failed", row.id, exc_info=True)

                if ready_result is not None:
                    update_kwargs: dict = {"status": "ready"}
                    demo_url = ready_result.get("demo_audio")
                    if demo_url:
                        try:
                            update_kwargs["demo_audio_path"] = await self._cache_demo_audio(row.id, demo_url)
                        except Exception:
                            # demo 缓存 best-effort：不得让已就绪音色被误判 failed（P1#4）。
                            logger.exception("demo audio cache failed for %s; training status unaffected", row.id)
                    await self.repo.update_status(row.id, **update_kwargs)
                else:
                    await self.repo.update_status(row.id, status="failed", error_message=f"训练超时（{timeout_minutes}分钟），请重新提交")
                reaped += 1
            except Exception:
                # 一行失败不影响其他行。
                logger.exception("Training reaper failed for voice %s", row.id)
        return reaped

    async def _cache_demo_audio(self, voice_id: str, url: str) -> str:
        base = self._get_storage_base()
        demo_path = base / "demos" / f"{voice_id}.wav"

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            # D4：原子写（唯一临时文件 + replace）——直写目标路径在并发/中断下
            # 会留下半截 demo 文件；tmp 名带 uuid，防同 key 并发写共用 tmp 互相截断。
            tmp_path = demo_path.with_name(f"{demo_path.name}.{uuid.uuid4().hex[:8]}.tmp")
            try:
                tmp_path.write_bytes(resp.content)
                tmp_path.replace(demo_path)
            finally:
                tmp_path.unlink(missing_ok=True)

        logger.info("Cached demo audio for voice %s", voice_id)
        return str(demo_path)

    async def synthesize(self, voice_id: str, text: str, output_format: str = "mp3", speed_ratio: float = 1.0) -> str:
        from app.gateway.voice_twin.preset_voices import is_edge_voice_id, parse_edge_voice_id

        if is_edge_voice_id(voice_id):
            # A2：edge: 音色直接走 edge 客户端（无 DB 行、无归属概念）。
            # edge_client.synthesize 内部已有 tenacity 3× 重试——不走 call_with_tts_retry
            # 外层包装（否则 3×3=9 次 retry² 嵌套，与 production_service #4 同构修复）。
            edge_client = self.edge_client
            if edge_client is None:
                raise ValueError("edge TTS service not configured")
            audio_bytes = await edge_client.synthesize(
                text=text,
                voice=parse_edge_voice_id(voice_id),
                speed=speed_ratio,
            )
            return await self._write_synth_output(audio_bytes, output_format, source_format="mp3")

        if self.repo is None:
            raise RuntimeError("Repository not initialized")

        row = await self.repo.get_by_id(voice_id)
        if row is None:
            raise FileNotFoundError(f"Voice not found: {voice_id}")
        if row.status != "ready":
            raise ValueError(f"Voice is not ready (status={row.status})")

        # D2：克隆合成接入共享重试（曾裸调）
        audio_bytes = await call_with_tts_retry(
            lambda: self.client.synthesize_sse(
                speaker_id=row.speaker_id,
                text=text,
                model_type=row.model_type,
                output_format=output_format,
                speed_ratio=speed_ratio,
                language=row.language,
            ),
            label="clone synthesize",
        )
        return await self._write_synth_output(audio_bytes, output_format)

    async def _write_synth_output(self, audio_bytes: bytes, output_format: str, source_format: str | None = None) -> str:
        """落盘合成产物；source_format 与目标不符时 ffmpeg 转码（edge 恒产 mp3）。"""
        source_format = source_format or output_format
        base = self._get_storage_base()
        output_id = uuid.uuid4().hex[:12]
        output_path = base / "outputs" / f"{output_id}.{output_format}"
        if source_format != output_format:
            from app.gateway.voice_twin.production_service import _transcode_wav

            src = base / "outputs" / f"_{output_id}.{source_format}"
            src.write_bytes(audio_bytes)
            try:
                proc = await run_ffmpeg(_transcode_wav, src, output_path)
            finally:
                src.unlink(missing_ok=True)
            if proc.returncode != 0:
                raise RuntimeError(f"ffmpeg transcode failed: {proc.stderr.decode(errors='replace')[:200]}")
        else:
            output_path.write_bytes(audio_bytes)
        return str(output_path)

    def get_demo_path(self, voice_id: str) -> str | None:
        base = self._get_storage_base()
        demo_path = base / "demos" / f"{voice_id}.wav"
        return str(demo_path) if demo_path.exists() else None

    def purge_voice_files(self, voice_id: str) -> None:
        """Delete on-disk artifacts for a voice (original upload + cached demo).

        Called by the delete route alongside the DB row deletion so deleting a
        voice twin no longer leaks originals/{id}.* and demos/{id}.wav.
        Also evicts voice_previews/{voice_id}_* — otherwise a deleted voice's
        cached preview clips remain servable (voice_id + text md5 is all the
        preview endpoint's cache key needs) and leak disk. Best-effort: missing
        files are not errors; repeated purges are no-ops.
        """
        base = self._get_storage_base()
        originals = base / "originals"
        # originals are keyed by ext (originals/{voice_id}.{ext}); remove any.
        for p in originals.glob(f"{voice_id}.*"):
            p.unlink(missing_ok=True)
        (base / "demos" / f"{voice_id}.wav").unlink(missing_ok=True)
        previews_dir = base / "voice_previews"
        if previews_dir.exists():
            for f in previews_dir.glob(f"{voice_id}_*"):
                f.unlink(missing_ok=True)

    def cleanup_storage(
        self,
        preview_dir: Path | None = None,
        *,
        preview_max_age_seconds: float = 7 * 24 * 3600,
        temp_max_age_seconds: float = 24 * 3600,
    ) -> dict[str, int]:
        """Maintenance sweep: evict stale preview caches and orphan temp files.

        VT-2 (R3, 2026-09-09): the outputs dir (synthesize results and the
        ``_``-prefixed raw intermediates) is swept at the same 24h tier as
        temp — 试听/下载产物即临时产物，超窗即回收。 Returns a counts dict
        {"previews": n, "temp": n, "outputs": n}. Safe to call on a schedule
        (e.g. the storage reaper task) or on demand. All directories are
        optional — missing dirs are skipped.
        """
        import time

        now = time.time()
        counts = {"previews": 0, "temp": 0, "outputs": 0}
        base = self._get_storage_base()
        targets: list[tuple[Path, float, str]] = []
        if preview_dir is not None:
            targets.append((Path(preview_dir), preview_max_age_seconds, "previews"))
        targets.append((base / "temp", temp_max_age_seconds, "temp"))
        targets.append((base / "outputs", temp_max_age_seconds, "outputs"))

        for directory, max_age, key in targets:
            if not directory.exists():
                continue
            for entry in directory.iterdir():
                try:
                    if now - entry.stat().st_mtime > max_age:
                        if entry.is_dir():
                            shutil.rmtree(entry, ignore_errors=True)
                        else:
                            entry.unlink(missing_ok=True)
                        counts[key] += 1
                except OSError:
                    continue
        return counts

    def start_storage_reaper(self) -> None:
        """启动存储清理后台任务（幂等，重复调用不重复启动）。

        VT-2（R3，2026-09-09）：outputs/temp 产物此前只有手工端点会清理，
        无人调用即无限堆积。每 storage_reaper_interval_minutes 分钟跑一次
        cleanup_storage；配置 <= 0 时 no-op（关闭）。
        """
        interval_minutes = self.config.storage_reaper_interval_minutes
        if interval_minutes <= 0:
            return
        if self._storage_reaper_task is not None and not self._storage_reaper_task.done():
            return  # already running

        async def _loop() -> None:
            while True:
                try:
                    counts = self.cleanup_storage()
                    if any(counts.values()):
                        logger.info("Voice clone storage reaper evicted: %s", counts)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("Voice clone storage reaper sweep errored (will retry next tick)")
                await asyncio.sleep(interval_minutes * 60)

        self._storage_reaper_task = asyncio.create_task(_loop(), name="voice-clone-storage-reaper")

    async def stop_storage_reaper(self) -> None:
        """取消存储清理后台任务（app shutdown 对称清理）。"""
        t = self._storage_reaper_task
        self._storage_reaper_task = None
        if t is not None and not t.done():
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass
