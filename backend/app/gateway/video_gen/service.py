"""VideoGenService — unified storyboard pipeline for video generation.

Refactored to a shared orchestration backbone:
  script → storyboard → TTS → subtitle → visual_source → Worker handoff.
Visual sources are pluggable (ai_image, ai_video, comic, remotion).
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import hashlib
import json
import logging
import math
import os
import re
import subprocess
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.gateway._shared.executors import run_ffmpeg
from app.gateway._shared.task_framework.metrics import metrics
from app.gateway._shared.task_framework.repository import TaskRepository
from app.gateway._shared.task_framework.schemas import DRAFT, STORY_READY, ensure_utc
from app.gateway._shared.task_framework.service import BaseTaskService, ProgressUpdater
from deerflow.config.paths import get_paths
from deerflow.runtime.user_context import get_effective_user_id

if TYPE_CHECKING:
    from app.gateway.video_gen.orchestration.tts import TTSPolicy

from .audio_flow import (  # noqa: F401  (re-export: 既有调用点与测试按 service 上的私名引用/patch)
    _RECONCILE_TOLERANCE_SEC,
    _concat_audio,
    _pad_audio_to_duration,
    _probe_audio_seconds,
)
from .audio_flow import (
    reconcile_durations_with_audio as _reconcile_durations_with_audio,
)
from .config import StallBudgetScalars, VideoGenConfig
from .degradations import KIND_FRAME_SPLIT, KIND_MODEL_ROSTER_MISS, KIND_SUBTITLE_LLM_FALLBACK, KIND_VOICE_FALLBACK, record_degradation
from .frame_ops import frames_to_regenerate, remap_shot_table_after_delete, remap_shot_table_after_insert
from .orchestration.visual_routing import _OFF_VALUES  # C5: off 值口径单一来源（勿统一旧开关 == "1" 形态）
from .pipeline_result import PipelineCompleted, PipelineDraft, PipelineGatewayDone
from .providers.registry import I2I_CAPABLE_IMAGE_PROVIDERS, ProviderRegistry
from .storyboard import Storyboard, remap_scene_groups_after_delete, remap_scene_groups_after_insert
from .style_presets import STYLE_PREFIXES, resolve_style_key, style_prefix_for  # noqa: F401  (re-export: 风格查表单一来源)
from .subtitle_filter import should_burn_subtitles  # noqa: F401  (re-export: ViMax 烧录决策入口)
from .visual_factory import has_mixed_sources, make_visual_budget, make_visual_source, make_visual_source_mixed
from .visual_sources.remotion_course_visuals import residual_visual_regen_block_reason

logger = logging.getLogger(__name__)


def _vimax_tts_policy_for(task: Any):
    """Plan B（2026-10-19，D4）：按 ``task.task_type``（非 payload video_type 猜测）
    返回 ViMax 专用 TTS 策略；其他任务 None = 完全旧链路。"""
    if getattr(task, "task_type", "") == "vimax":
        from app.gateway.video_gen.engines.vimax.voice_policy import VIMAX_TTS_POLICY

        return VIMAX_TTS_POLICY
    return None


# Plan A Task 3（2026-10-18）：草稿音频失败阶段 → 中文文案（payload audio_error 用）。
# 阶段枚举与 router 透传的 audio_error_stage 契约一致：tts|concat|subtitle|merge。
_AUDIO_STAGE_LABELS = {"tts": "配音合成", "concat": "音频拼接", "subtitle": "字幕生成", "merge": "音频合并"}

# E2→T2（2026-08-31 批量预览并发化）：preview 生图并发闸——避免 N 帧并发预览时
# 第 N 个等 N×单帧时长才启动→nginx 超时 504。E2 的 Semaphore(3) 是当时防 504 的
# 保守值，已被 2026-08-31 实测推翻（360 服务端 12 路并发生图 12/12 成功、零排队、
# 33s 全部完成，单张耗时不随并发上涨）——按拍板调 12，走 config
# ``preview_all_concurrency``。懒构造单例（与 visual_sources._global_concurrency
# .get_ai_api_semaphore 同模式：首个带合法 limit 的调用方定容）。
_preview_sem: asyncio.Semaphore | None = None
_PREVIEW_SEM_DEFAULT = 12


def _get_preview_sem(limit: int | None = None) -> asyncio.Semaphore:
    """预览生图并发闸（懒单例）。``limit`` advisory：进程内首个构造点定容；
    非 int/非法值（含 MagicMock 桩 config 的自动属性）回落默认 12。"""
    global _preview_sem
    if _preview_sem is None:
        n = limit if isinstance(limit, int) and not isinstance(limit, bool) and limit >= 1 else _PREVIEW_SEM_DEFAULT
        _preview_sem = asyncio.Semaphore(n)
    return _preview_sem


def _reset_preview_sem_for_test() -> None:
    """Test-only：丢掉单例，下一用例按新 config 重建。"""
    global _preview_sem
    _preview_sem = None


# T2 (2026-08-14): TTS gather elapsed 兜底预算。T1 落地后每次 TTS 调用已有界
# （SSE 整调用 120s × tenacity 3 次重试 + 360/edge 降级链），此处只防兜底之外的
# 意外 hang——按帧数缩放，长脚本（100+ 帧，T4 已拍板不设帧数上限）有足额合法时间。
TTS_GATHER_TIMEOUT_FLOOR = 1800  # s
TTS_GATHER_TIMEOUT_PER_FRAME = 60  # s


def _tts_gather_timeout(n_frames: int) -> float:
    """TTS 整批 gather 的 elapsed 上限：``max(1800, n_frames × 60)``。"""
    return max(TTS_GATHER_TIMEOUT_FLOOR, n_frames * TTS_GATHER_TIMEOUT_PER_FRAME)


# T7 (2026-08-14): 字幕 LLM 重切循环总量预算。08-13 字幕批次在 TTS 阶段窗口引入逐帧
# **串行** LLM 重切（subtitle_llm_enabled 默认 True）；单次有界（120s），但 125 帧串行 ×
# 全超时最坏 = 4+ 小时无进展。LLM 健康的常态下每帧 <2s，300s 覆盖 150+ 帧；
# LLM 全面 hang 时最多烧 300s + 一次在途 120s，剩余帧返回空 → 规则链兜底。
SUBTITLE_LLM_TOTAL_BUDGET = 300.0  # s

# T6->W5（2026-08-24 活性重构）：progress 冻结固定窗口硬编码（PROGRESS_STALL_TIMEOUT=20*60）
# 已退役--改为配置 ``video_gen.stall_progress_freeze_seconds``（默认 1800=P5 兜底窗口；
# 回滚到旧行为 = 配置 1200）。谓词 C 同时降级为「无活性覆盖阶段」的兜底（覆盖期旁路）。

# 每任务一把合成锁（TTS→concat→SRT 整段互斥）。惰性创建；锁随 task_dir 累积、
# 进程生命周期内不清理（评审④：单 Lock 对象开销可忽略，单进程 gateway 长驻可接受；
# 任务量级到数十万再考虑定期清理，YAGNI）。
_synth_locks: dict[str, asyncio.Lock] = {}


class _NullLiveness:
    """W2：task_id 缺失（存量调用/无任务上下文）时的活性挂点 no-op（async CM）。"""

    async def __aenter__(self):
        return None

    async def __aexit__(self, *exc):
        return None


_NULL_LIVENESS = _NullLiveness()

# W4：预算标量默认值（桩 config / 老版本 config 对象回落用；真值在 VideoGenConfig.stall_budget_scalars）
_DEFAULT_BUDGET_SCALARS = StallBudgetScalars()


def _synth_lock_for(task_dir: str) -> asyncio.Lock:
    lock = _synth_locks.get(task_dir)
    if lock is None:
        lock = asyncio.Lock()
        _synth_locks[task_dir] = lock
    return lock


# 本协程已持有哪些 task_dir 的合成锁。**必须是 ContextVar 而非 set/dict**：
# ContextVar 在 asyncio 里按任务隔离（`asyncio.Task` 复制上下文），所以它回答的是
# 「**我**持锁吗」，而 `Lock.locked()` 只能回答「**有人**持锁吗」。
#
# 为什么这个区别是刚需（2026-08-02 实测坐实，非推断）：同一把锁另有 4 个
# `_synthesize_audio` 持有点（渲染 :1832 / 草稿 :2150 / 合成 :2482 / 分段 :3286），
# 与 `_regenerate_frame_bg` 完全并行。若用 `locked()` 判断，「别的协程正持锁」会被
# 误读成「我已持锁」→ 跳过加锁 → 并发写 audio.mp3（撕裂，vg_93e83a7ee259 实证）。
# 实测脚本（两协程交错）确认该串扰真实可复现，故不能用 `locked()`。
#
# ⚠️ 另一条来自 ContextVar 语义的约束（2026-08-03 核实；2026-10-07 遗留批重锚）：
# ContextVar 会随 `asyncio.create_task` / `asyncio.gather` **复制到子任务**（Python
# 语言层事实，已实测：子任务读到的就是父在派发那一刻的 `_held` 快照）。因此**在
# guard 块内不得派发会「重入」本锁体系的独立子任务**（即会再调 `_synth_lock_guard`
# 或 `_synthesize_audio` / `_regenerate_frame` 的子任务）——它会继承父的
# `_held={task_dir}` 被判为「我已持锁」→ 跳过加锁 → 与父协程并发写 `audio.mp3`，
# 文件新旧代交错撕裂、reconcile 静默不可测（vg_93e83a7ee259 谱系——代号为可检索
# 索引，事故本质即本句描述）。这恰恰是 `locked()` 挡不住的「人」的失误，故记此约束。
#
# guard 块内**已知的 create_task/gather 及其安全性**（2026-10-07 复核重锚——旧注释
# 「3 个生产 guard 块…块内均无 create_task」的 AST 审计结论已随 W2 活性重构过时）：
# - `with_liveness` 的 liveness tick（W2，经 `_liveness` 挂点，`_synthesize_audio_locked`
#   内逐阶段挂）：tick = sleep + 节流 touch updated_at（纯 DB 写），不碰音频文件与锁
#   ——但它在 guard 内被派发、继承 `_held` 快照，未来给 tick 增加任何「回 guard」的
#   职责前必须重读本约束；
# - `_synthesize_audio_locked` 内 `gather` 派发的 `synth_one`：只做 TTS/ffprobe/CAS
#   文件操作，**不回 guard**——同一次合成内部的 TTS 分帧并行，子任务共享父锁是期望行为。
# guard 块按函数名索引（行号易腐，不列）：`_generate_draft_audio` / `_synthesize_audio`
# / `_invalidate_audio_artifacts` / `_regenerate_frame_bg` 音频段 / router 帧编辑失效入口。
_synth_lock_held: contextvars.ContextVar[frozenset[str]] = contextvars.ContextVar("_synth_lock_held", default=frozenset())


@contextlib.asynccontextmanager
async def _synth_lock_guard(task_dir: str):
    """获取该 task_dir 的合成锁；**同一协程内重入则复用**（不再加锁）。

    asyncio 无重入锁，而调用链上确实有两层都需要这把锁
    （`_regenerate_frame_bg` → `_regenerate_frame` 音频段）——嵌套获取会死锁
    （2026-08-02 用户实测「重生配音一直转圈」的根因）。

    与「已持锁就跳过」的朴素写法的差别见 `_synth_lock_held` 注释：那种写法
    会被**别的**协程持锁误导。这里只在**本协程**已登记时才复用。
    """
    held = _synth_lock_held.get()
    if task_dir in held:
        yield  # 本协程已持有 → 直接复用，避免自死锁
        return
    async with _synth_lock_for(task_dir):
        token = _synth_lock_held.set(held | {task_dir})
        try:
            yield
        finally:
            _synth_lock_held.reset(token)


# ── Helpers ──────────────────────────────────────────────────────────────


def _tts_concurrency(voice_id: str) -> int:
    """Determine TTS concurrency based on voice provider.

    Paid cloud TTS providers (360 preset, doubao seed-tts-2.0) get concurrency 3
    to avoid rate limits. Free edge-tts and clone voices get 5.
    """
    if voice_id.startswith(("preset:", "doubao:")):
        return 3
    return 5


def _aspect_to_size(aspect: str) -> tuple[int, int]:
    """Map aspect ratio string to (width, height) pixels — Ark seedream-native.

    M2-2: ai_image builds ``size = f"{cfg.width}x{cfg.height}"`` and passes it verbatim
    to Ark seedream. Ark only accepts a fixed size set; the old 1080x1920 was rejected
    → seedream fell back to its default (16:9), so 9:16 requests yielded 16:9 images.
    Use the same Ark-native sizes the ViMax path (provider_bridge._ASPECT_TO_SIZE) uses;
    ViMax seedream was A/B-validated against Ark with these sizes.
    """
    native = {
        "9:16": (1152, 2048),
        "16:9": (2048, 1152),
        "1:1": (1152, 1152),
    }
    return native.get(aspect, (1152, 2048))


def _get_audio_duration(path: str) -> float:
    """Get actual audio file duration in seconds（解码口径）.

    委托 orchestration.tts 的同名实现（此前为两处复制，2026-07-21 评审去重——
    统一以 tts.py 为唯一实现，防双轨漂移）。2026-08-20 起为解码口径：
    LAME gapless mp3 的格式时长含 delay/padding，解码裁掉，与 _concat_audio
    拼接口径对齐（vg_3300179a11f0 渐进音画错位根因修复）。
    """
    from .orchestration.tts import _get_audio_duration as _impl

    return _impl(path)


def _extract_thumbnail(video_path: str, task_dir: Path) -> str | None:
    """Extract a JPEG thumbnail from the video.

    Seeks to max(1s, duration*10%) to avoid black/transition frames.
    Returns the thumbnail path on success, None on failure.
    """
    thumb_path = task_dir / "thumbnail.jpg"
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0", video_path],
            capture_output=True,
            text=True,
            timeout=10,
        )
        duration = float(probe.stdout.strip())
        ss = max(1.0, duration * 0.1)
        cmd = ["ffmpeg", "-y", "-ss", str(ss), "-i", video_path, "-frames:v", "1", "-q:v", "2", str(thumb_path)]
        proc = subprocess.run(cmd, capture_output=True, timeout=30)
        if proc.returncode == 0 and thumb_path.exists():
            return str(thumb_path)
    except Exception:
        logger.warning("Thumbnail extraction failed for %s", video_path)
    return None


def build_worker_payload(
    storyboard: Storyboard,
    *,
    subtitle_path: str | None,
    subtitle_ass: str | None = None,
    audio_concat_path: str | None,
    output_format: str = "mp4",
    quality: str = "standard",
    pipeline_version: str = "v1",
) -> dict:
    """Build the payload dict handed off to the Worker for ffmpeg composition."""
    config = asdict(storyboard.config)
    groups = [asdict(g) for g in storyboard.scene_groups]
    # W1: ai_image/ai_video 强制 transition=none——xfade 重叠使视频<音频→讲解对不上。
    # 在此覆盖（不改 schema 默认，保留 vimax/remotion 等其他路径的 fade 能力）。
    if storyboard.config.video_type in ("ai_image", "ai_video"):
        config["transition"] = "none"
        # W1-ext（2026-07-21）：SceneGroup.transition_in="fade"/transition_duration=0.8 的
        # dataclass 默认经 asdict 绕过 config 级 none——v3_segmented 组内仍 xfade 重叠
        # （同型漂移）。组级一并强制 none。
        for g in groups:
            g["transition_in"] = "none"
            g["transition_out"] = "none"
    # 字幕「无」：不下发 subtitle_path 也不下发 subtitle_ass → worker has_sub=False
    # → 写 subtitle_status="skipped" 跳过烧录。T6 起 worker 优先看 subtitle_ass，
    # 必须同时置 None，否则 gateway 写了 .ass 文件 → worker 照烧（P0 回归修复）。
    if storyboard.config.subtitle_style == "none":
        subtitle_path = None
        subtitle_ass = None
    return {
        "video_type": storyboard.config.video_type,
        "config": config,
        "frames": [asdict(f) for f in storyboard.frames],
        "scene_groups": groups,
        "subtitle_path": subtitle_path,
        "subtitle_ass": subtitle_ass,
        "audio_concat_path": audio_concat_path,
        "output_format": output_format,
        "quality": quality,
        "pipeline_version": pipeline_version,
    }


def _audio_cache_key(narration: str, voice_id: str, speed: float) -> str:
    """每帧音频 CAS 键：md5(v2|narration|voice|speed)[:12]。

    2026-07-21 音画漂移根因环 1：旧键只含 narration——改音色/速度后重合成同名覆盖，
    新时长音频顶着 storyboard 旧时长记录出片（vg_93e83a7ee259 实测记录 114s vs 音频 138s）。
    键入音色+速度后，不同生成配置各存各的文件，记录与文件永远同代。
    注意：同一 storyboard 内 voice/speed 恒定 → 重复旁白帧仍命中同一文件，
    **TTS 文件级去重语义不变**（test_duplicate_narration_audio.py 守护——评审①）。
    v2 版本前缀（2026-07-28）：07-24~07-27 部署窗口 360 回退路径忽略 speed（自 4b915c180
    才真实生效）——speed≠1（StoryboardConfig 默认 1.2 自 544594726 起）的存量键下实为
    speed=1.0 内容，随 CAS 复用落地会被误命中 → 键加版本前缀防御性作废旧键（代价：存量
    任务下次 render 重 TTS 一遍 + 旧键文件成孤儿盘）。
    """
    raw = f"v2|{narration}|{voice_id}|{speed}"
    return hashlib.md5(raw.encode()).hexdigest()[:12]


_COURSE_PROJECT_ID_RE = re.compile(r"^cp_[0-9a-f]{12}$")


def _is_safe_course_project_id(course_project_id: str | None) -> bool:
    """course_project_id 必须是服务端生成的 ``cp_<12 hex>``（Task 16）。

    该 id 走**请求体**（schemas.py: ``course_project_id: str | None``），不经 ASGI
    的路径参数解码限制，于是 ``../../other-user/cp_x`` 能穿出本用户目录 ——
    service 侧拼的是 ``storage/course_projects/{user_id}/{id}.json``，实测可**跨
    用户读他人项目 json**（voice_id / subtitle_style / 角色表路径等）。

    订正审计：这是**跨租户读**，不是「可读写删任意文件」——``create()`` 用服务端
    生成的 ``cp_<uuid>``，且路径参数版本经 ASGI 拒绝编码斜杠（``..%2F`` → 404）。

    白名单式校验（不是黑名单过滤 ".."），任何不符形状的一律拒。
    """
    if not course_project_id:
        return False
    return bool(_COURSE_PROJECT_ID_RE.match(course_project_id))


def _write_silent_audio(output, seconds: float = 1.0) -> None:
    """生成 N 秒静音 mp3，用于空旁白帧占位。

    空旁白（纯画面/镜头标记剥离后无台词）送 doubao TTS 会报 `45002001 No readable text`，
    且 asyncio.gather 会让单帧错误拖垮整批 → 整个 draft audio 失败。此处给空帧一个静音占位，
    让它有合法 audio_path/duration，整批得以完成。
    """
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "anullsrc=r=24000:cl=mono",
        "-t",
        str(seconds),
        "-acodec",
        "libmp3lame",
        "-q:a",
        "9",
        str(output),
    ]
    proc = subprocess.run(cmd, capture_output=True, timeout=30)
    if proc.returncode != 0 or not Path(output).exists():
        raise RuntimeError(f"silent audio generation failed: {proc.stderr[-300:]!r}")


def _resolve_bgm(bgm_id: str, user_id: str = "default") -> str | None:
    """Resolve BGM ID to file path. Supports built-in and user-uploaded BGM."""
    import json

    # Check user-uploaded first (prefix "custom_")
    if bgm_id.startswith("custom_"):
        from deerflow.runtime.user_context import get_effective_user_id

        # E7: 后台 pipeline/reaper 派发时 contextvar 未继承 → custom BGM 静默丢失。
        # 显式传入的 user_id（非 "default"）优先；仅前台请求（"default"）才回退 contextvar。
        effective_user = user_id if user_id != "default" else get_effective_user_id()
        user_bgm_dir = get_paths().base_dir / "video-gen/user_bgm" / effective_user  # C8：CWD 相对改基址
        if user_bgm_dir.exists():
            # Match by stem (id is the stem without extension)
            for p in user_bgm_dir.iterdir():
                if p.stem == bgm_id:
                    return str(p) if p.exists() else None

    # Then built-in
    bgm_dir = Path(__file__).parent / "resources" / "bgm"
    index_path = bgm_dir / "index.json"
    if not index_path.exists():
        return None
    try:
        tracks = json.loads(index_path.read_text(encoding="utf-8")).get("tracks", [])
        for track in tracks:
            if track["id"] == bgm_id:
                path = bgm_dir / track["filename"]
                return str(path) if path.exists() else None
    except (json.JSONDecodeError, KeyError):
        pass
    return None


def _resolve_bgm_or_warn(bgm_id: str, user_id: str = "default") -> str | None:
    """解析 BGM id；未知 id（如已下架的内置曲）记 warning 并返回 None ——
    任务不带 BGM 继续合成，不抛异常（2026-07-31 删旧 3 首后老任务 re-render 配套）。"""
    path = _resolve_bgm(bgm_id, user_id)
    if path is None:
        logger.warning("BGM id %r 无法解析（可能已下架），本次合成不带 BGM", bgm_id)
    return path


def _read_course_project_character_reference(storage_dir: str | Path, project_id: str | None, user_id: str) -> str | None:
    """读 CourseProject.character_reference（用户上传角色参考图路径），无则 None。

    HR-T4：把用户在 CourseProject 里指定的角色参考图路径解析出来，供 ViMax
    front portrait 生成作 reference_image（bridge 的 generate_single_image 已支持
    reference_image_paths）。镜像 _load_course_project_style 的文件读取方式。
    """
    # 审查发现 1：本 reader 此前只判 `if not project_id`，未过白名单 ——
    # course_project_id 走请求体不受 ASGI 斜杠限制，`../victim/cp_x` 实测可
    # 跨租户读他人项目。Task 16 当初只接了 _read_course_project 一处。
    if not _is_safe_course_project_id(project_id):
        return None
    import json

    from .course_models import CourseProject

    base = Path(storage_dir) / "course_projects"
    # E15: 仅按 user_id 精确定位，删跨用户 iterdir fallback（防 IDOR 式泄漏）
    project_path = base / user_id / f"{project_id}.json"
    if not project_path.exists():
        return None
    try:
        data = json.loads(project_path.read_text(encoding="utf-8"))
        return CourseProject.from_dict(data).character_reference
    except Exception:
        logger.warning("Failed to read character_reference from project %s", project_id)
        return None


def _read_course_project_color_palette(storage_dir: str | Path, project_id: str | None, user_id: str) -> dict | None:
    """P1-E8: 读 CourseProject.color_palette（项目级调色板），无则 None。

    镜像 _read_course_project_character_reference 的文件读取方式（E15 跨用户隔离同）。
    修前：course_models.py 定义 + storyboard.py 有字段 + worker _ffmpeg_colorgrade 消费，
    但 StoryboardConfig 构造从不传值 → 项目调色板全链路静默无效。
    """
    # 审查发现 1：本 reader 此前只判 `if not project_id`，未过白名单 ——
    # course_project_id 走请求体不受 ASGI 斜杠限制，`../victim/cp_x` 实测可
    # 跨租户读他人项目。Task 16 当初只接了 _read_course_project 一处。
    if not _is_safe_course_project_id(project_id):
        return None
    import json

    from .course_models import CourseProject

    base = Path(storage_dir) / "course_projects"
    project_path = base / user_id / f"{project_id}.json"
    if not project_path.exists():
        return None
    try:
        data = json.loads(project_path.read_text(encoding="utf-8"))
        return CourseProject.from_dict(data).color_palette
    except Exception:
        logger.warning("Failed to read color_palette from project %s", project_id)
        return None


def _read_course_project_character_sheets(storage_dir: str | Path, project_id: str | None, user_id: str) -> list:
    """Task 13: 读 CourseProject.character_sheets（三视图），供 ViMax 复用。

    ViMax 注入项目三视图后不再每次临时生成 portrait——角色跨视频一致。
    返回 list[CharacterSheet]；无项目/无 sheets → []。
    """
    # 审查发现 1：本 reader 此前只判 `if not project_id`，未过白名单 ——
    # course_project_id 走请求体不受 ASGI 斜杠限制，`../victim/cp_x` 实测可
    # 跨租户读他人项目。Task 16 当初只接了 _read_course_project 一处。
    if not _is_safe_course_project_id(project_id):
        return []
    from .course_models import CharacterSheet, CourseProject

    base = Path(storage_dir) / "course_projects"
    # E15: 仅按 user_id 精确定位，删跨用户 iterdir fallback（防泄漏）
    project_path = base / user_id / f"{project_id}.json"
    if not project_path.exists():
        return []
    try:
        data = json.loads(project_path.read_text(encoding="utf-8"))
        sheets = CourseProject.from_dict(data).character_sheets
        return [s if isinstance(s, CharacterSheet) else CharacterSheet.from_dict(s) for s in (sheets or [])]
    except Exception:
        logger.warning("Failed to read character_sheets from project %s", project_id)
        return []


def validate_frame_previewable(task_dir: Path, frame_index: int) -> None:
    """同步校验单帧可预览（400 语义）：storyboard 存在、帧存在、有 image_prompt。

    Raises ValueError。供 preview 端点在调度后台任务前同步调用，
    保证参数类错误仍即时 400 而非异步静默。
    """
    sb_path = task_dir / "storyboard.json"
    if not sb_path.exists():
        raise ValueError("Storyboard not found")
    storyboard = Storyboard.from_dict(json.loads(sb_path.read_text(encoding="utf-8")))
    frame = next((f for f in storyboard.frames if f.index == frame_index), None)
    if frame is None:
        raise ValueError(f"Frame {frame_index} not found")
    if not frame.image_prompt:
        raise ValueError("Frame has no image_prompt — cannot preview")


def _character_ref_paths_from_payload(payload: dict) -> list[str] | None:
    """Task 12 / B5a: 从 payload 提取角色参考图路径，供 ai_image render 作 reference_image_paths（i2i 一致性）。

    优先 character_sheets[0] 的 front+side+back 三视图（全传，过滤空；seedream ``image:[list]`` Pro≤10），
    回退 character_reference（单图）。无项目角色 → None（ai_image 不带 reference，向后兼容）。

    X11-4（2026-08-27 计划）主链路坏路径守卫：character_reference 是自由文本字段（存量
    UI 语义=角色描述），纯文本不作 ref 返回——对齐 vimax 桥 ``_is_image_reference``
    守卫语义（URL/data: 前缀或磁盘既有文件才算图），防中文描述流向 360 /v1/images/edits。
    """
    sheets = payload.get("character_sheets")
    if sheets:
        first = sheets[0]
        # B5a: 三视图全传（front+side+back，过滤空）——修前只返 [front]，side/back 在 gateway 边界被丢。

        def _view(key: str) -> str:
            return (first.get(key) if isinstance(first, dict) else getattr(first, key, "")) or ""

        paths = [v for v in (_view("front_url"), _view("side_url"), _view("back_url")) if v]
        if paths:
            return paths
    ref = payload.get("character_reference")
    if ref and (str(ref).startswith(("http://", "https://", "data:")) or Path(ref).exists()):
        return [ref]
    return None


def create_script_llm(payload: dict | None, config: VideoGenConfig):
    """E6（2026-08-21 360 主力切换）：分镜/脚本 LLM 显式化——单一取用点。

    优先级：``payload.script_model``（任务级覆盖，照 image_provider 同款模式）
    > ``config.script_model``；两者皆缺 → **显式报错**，不回落 ``models[0]``
    （``create_chat_model(None)`` 即 models[0]：静默用聊天主力模型出分镜，
    昂贵且未按分镜任务调优；2026-10-12 起分镜模型 = qwen3.8-flash——
    2026-09-08 实测备选位 + 2026-10-12 压测默认位胜者，见
    docs/2026-10-12-flash-trio-benchmark.md）。

    调用点：service 两条管线（原 :1685/:2436）+ 单帧重生端点（routers/video_gen.py）。
    vimax 的 narration/screenwriter LLM **不经此函数**（vimax 零侵入，另有
    ``_resolve_vimax_story_model``）；字幕 LLM 读 ``subtitle_llm_model``，同样不经此函数。
    """
    from deerflow.models import create_chat_model

    name = str((payload or {}).get("script_model") or config.script_model or "").strip()
    if not name:
        raise ValueError(
            "video_gen.script_model 未配置：分镜/脚本 LLM 不再回落 models[0]。"
            "请在 config.yaml 的 video_gen 段设置 script_model（2026-10-12 起生产 = "
            "qwen3.8-flash：2026-09-08 十轮×2负载实测备选位 + 2026-10-12 压测默认位"
            "胜者，见 docs/2026-10-12-flash-trio-benchmark.md），或任务 payload 传 "
            "script_model 覆盖。"
        )
    return create_chat_model(name, thinking_enabled=False)


def resolve_shot_table_llm(config: VideoGenConfig):
    """S5（2026-08-26 调度表计划）：阶段 1 调度表 LLM 解析。

    ``config.shot_table_model`` 显式配置 → 独立实例（须在 models[] 注册，与
    script_model 同一注册体系）；未配置 → None（plan_course 内部回落 script LLM
    = script_model，计划默认「跟随 script_model」语义）。返回 None 与「独立实例」
    二值区分，调用点条件传参（未配置不传 kwarg）。
    """
    name = (config.shot_table_model or "").strip()
    if not name:
        return None
    from deerflow.models import create_chat_model

    return create_chat_model(name, thinking_enabled=False)


def _combine_vimax_style(payload_style: str | None, project_style_prefix: str | None) -> str | None:
    """B4 / Bug 2: 组合项目 ``style_prefix`` 与任务 ``style``，供 ViMax ``ctx.style``。

    ``ctx.style`` 经 ``engine.py`` → ``Script2VideoPipeline.__call__(style=...)`` →
    ``_with_style_prefix(prompt, style)`` 进每帧 frame prompt（``style`` 在 vimax 管线仅作
    prompt 前缀，非 lookup key，故组合安全）。项目 prefix 前置；都无则 None（engine 走
    ``_DEFAULT_STYLE``）。修前 ``ctx.style=payload.get("style")`` 完全无视项目视觉风格预设。

    Bug 2: ``"default"`` 是 ``CreateVideoRequest.style`` 的 schema 占位（未设置），归一为 None
    ——避免「项目有 prefix + 任务未选风格」时拼出 "watercolor, default" 噪声进每帧 prompt。
    """
    if payload_style == "default":
        payload_style = None
    if project_style_prefix:
        return f"{project_style_prefix}, {payload_style}" if payload_style else project_style_prefix
    return payload_style


def apply_ref_cap(refs: list[str], *, working_limit: int | None = None, hard_limit: int = 10) -> tuple[list[str], dict]:
    """C10（决策 17）：参考图张数双阈值闸门（图像侧一期落地，视频侧二期 M5 复用）。

    输入已按 角色 >（二期：道具）> 锚图 排好序——截断只发生在队尾（顺序优先级即截断
    优先级）。工作点默认 6（典型组合 角色≤3 + 道具≤2 + 锚图 1；参考图过多
    导致特征互染 identity mixing，一致性不升反降）；hard_limit=10 为 provider 技术上限
    断言（seedream ≤10 此前无代码强制，providers/seedream.py 全量透传）。
    实验位：`VIDEO_GEN_REF_CAP_WORKING` 可临时调整工作点（默认 6；T2⑤ 三角色同框
    截断对比测试用，验收拍板后定稿）。合成图方案（2026-08-27 计划 X9）：该开关
    及工作点 6 在角色恒带 1 张三视图合成图后仅旧路径兜底使用，默认值不变。
    H3 r2v 注记（2026-09-04 计划 S2 撤销备案）：MiniMax-H3 官方参考图上限 9 在
    工作点 6 下**不可达**（``refs[:working_limit][:hard_limit]`` 只传 hard_limit
    时输出恒 ≤6，hard_limit 永远够不到）；若日后调高 ``VIDEO_GEN_REF_CAP_WORKING``
    超过 9，需回头处理本约束（provider 层届时须加 H3 专属钳制）。
    注意：与 visual_sources/budget.py 的 VisualBudget
    （金额/次数闸）正交，命名勿混淆。返回 (保留 refs, sidecar 记录)。
    """
    if working_limit is None:
        # B2（2026-08-27 评审修复）：env 误配（非数字）不得打断渲染主链路——
        # 与 ai_image.py 的 VIDEO_GEN_MULTI_CHAR_VIEWS 同款守护（ValueError → 默认）。
        try:
            working_limit = int(os.environ.get("VIDEO_GEN_REF_CAP_WORKING", "6") or "6")
        except ValueError:
            logger.warning("VIDEO_GEN_REF_CAP_WORKING 非数字（%r），回落默认工作点 6", os.environ.get("VIDEO_GEN_REF_CAP_WORKING"))
            working_limit = 6
    refs = list(refs or [])
    keep = refs[:working_limit][:hard_limit]
    record = {
        "injected": keep,
        "truncated": refs[len(keep) :],
        "reason": "budget" if len(keep) < len(refs) else "",
    }
    return keep, record


def record_ref_injection_sidecar(
    task_dir: Path | str | None,
    *,
    frame_index: int,
    declared_entities: list | None = None,
    injected_refs: list | None = None,
    truncated: list | None = None,
    skipped: list | None = None,
    reason: str = "",
    first_frame: bool = False,
) -> None:
    """C10（决策 19）：写/合并 ``task_dir/sidecar/ref_injection.json``（按帧合并）。

    排查用（「该注入没注入 vs 注入了没画好」定位）+ 二期分镜级「参考资产」标签数据源
    （M5 扩展全量字段）。一期至少记录锚图维度（declared_entities 可空）。task_dir 为
    None 时静默跳过（无盘可写，不阻塞渲染）。

    first_frame（机制 M，2026-08-31）：该帧是否注入了预览图作 i2v 首帧（预览图条目
    entity="preview"，位于 injected_refs 首位——与实际下发数组同序）。

    复核修复（2026-08-31 第二轮）：留痕失败不得阻塞渲染——既有文件为合法 JSON 但
    顶层非数组（外部损坏，如 ``42``/``null``）时按无既有记录处理（本轮重写自愈为
    数组），外层兜底从 ``except OSError`` 放宽为 ``except Exception``。
    """
    if not task_dir:
        return
    try:
        sidecar_dir = Path(task_dir) / "sidecar"
        sidecar_dir.mkdir(parents=True, exist_ok=True)
        path = sidecar_dir / "ref_injection.json"
        records: list = []
        if path.exists():
            try:
                parsed = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                parsed = []
            if isinstance(parsed, list):
                records = parsed
        records = [r for r in records if isinstance(r, dict) and r.get("frame_index") != frame_index]
        records.append(
            {
                "frame_index": frame_index,
                "declared_entities": declared_entities or [],
                "injected_refs": injected_refs or [],
                "truncated": truncated or [],
                "skipped": skipped or [],
                "reason": reason or "",
                "first_frame": first_frame,
            }
        )
        records.sort(key=lambda r: r.get("frame_index", -1))
        path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:  # 复核修复：留痕失败不阻塞渲染（docstring 口径），非 OSError 也不再打断主流程
        logger.warning("ref_injection sidecar write failed", exc_info=True)


def composite_path_for_sheet(entry: dict, storage_dir: str | Path) -> str | None:
    """X5（2026-08-27 计划）：sheet 合成图惰性兜底——存量 sheet 无 composite_url 时
    注入现场按 front/side/back 即拼并缓存。

    ① ``entry["composite_url"]`` 非空且文件存在 → 直用（不再拼）；
    ② 否则收集存在的前/侧/背文件，调 ``build_composite_image`` 拼 1 张缓存到
       ``character_sheets/{pid}/composite_{uuid8}.png``（pid 从视图路径推导——
       标准 layout 恒为该目录；推导不出时落到首个视图同目录，语义等价）；
    ③ 都不可用 / 拼图失败 → ``None``（调用方回退视图旧逻辑，决策 6 内置降级）。

    并发同拼（两任务同帧触发）允许重复拼图：文件名 uuid 唯一无写冲突（计划 X5 边界）。
    """
    if not isinstance(entry, dict):
        return None
    cached = entry.get("composite_url") or ""
    if cached and Path(cached).exists():
        return cached

    views = [(t, entry.get(f"{t}_url") or "") for t in ("front", "side", "back")]
    existing = [(t, p) for t, p in views if p and Path(p).exists()]
    if not existing:
        return None
    # 缓存目录：标准 layout 从视图路径推导 character_sheets/{pid}/；推导不出用首视图同目录
    base_dir = Path(existing[0][1]).parent
    m = re.search(r"character_sheets[/\\]([^/\\]+)", Path(existing[0][1]).as_posix())
    if m:
        base_dir = Path(storage_dir) / "character_sheets" / m.group(1)
    # 缓存键（2026-08-28 复核修补④）：合成图缓存按 sheet 维度命名——character_sheets/{pid}/
    # 是项目内全部 sheet 的共享目录，修前 glob「composite_*.png」会把 A 角色的合成图
    # 错配给 B 角色（真实链路实证：全角色注成 sheets[0] 的合成图）。id 优先，其次
    # name，兜底 front_url 短哈希（无 id/name 的裸 dict 调用方）。
    import hashlib

    raw_key = entry.get("id") or entry.get("name") or hashlib.md5((existing[0][1]).encode()).hexdigest()[:8]
    sheet_key = re.sub(r"[^\w-]", "_", str(raw_key))
    # 缓存复用（2026-08-27 复核 3.1）：本 sheet 既有惰性拼图且新于全部视图 → 直用。
    # 修前每任务重拼 1 张 uuid 新文件（composite_url 不回写项目 JSON，下次读不到缓存
    # 路径），磁盘持续累积 + 每任务重复 PIL。视图重生成（mtime 更新）→ 旧缓存失效照拼。
    try:
        cached_candidates = sorted(base_dir.glob(f"composite_{sheet_key}_*.png"), key=lambda p: p.stat().st_mtime, reverse=True)
        newest_view = max(Path(p).stat().st_mtime for _, p in existing)
        for cand in cached_candidates:
            if cand.stat().st_mtime >= newest_view:
                return str(cand.resolve())
    except OSError:
        pass  # 目录不可读等 → 照拼（下方 try 兜底）
    try:
        import uuid as _uuid

        from .character_sheet_generator import build_composite_image

        return build_composite_image(existing, base_dir / f"composite_{sheet_key}_{_uuid.uuid4().hex[:8]}.png")
    except Exception:
        logger.warning("composite lazy build failed for sheet entry — falling back to per-view refs", exc_info=True)
        return None


def _build_character_roster(payload: dict, storage_dir: str | Path | None = None) -> dict:
    """M3：角色 roster——name → {front_url, side_url, back_url}（帧级注入用）。

    仅跟随项目（effective_style_source == "project"）时有内容；首主角必含
    （roster 首键 = sheets[0]——None 帧兜底全传，M3 过渡关系）。roster 空表示
    roster 通道不生效（任务级 reference_image_paths 旧语义继续，存量行为不变）。

    X6（2026-08-27 计划）：传 ``storage_dir`` 时每实体条目增 ``"composite"`` 键
    （经 :func:`composite_path_for_sheet` 惰性解析；不可用为 ``""`），front/side/back
    三键保留（无合成图时回退路径用）。条件传参：不传 = 旧窄签名零改动（存量调用方
    与测试桩不解析 composite）。
    """
    if (payload or {}).get("effective_style_source") != "project":
        return {}
    roster: dict = {}
    for sheet in payload.get("character_sheets") or []:
        if isinstance(sheet, dict) and sheet.get("name"):
            roster[sheet["name"]] = {
                "front_url": sheet.get("front_url") or "",
                "side_url": sheet.get("side_url") or "",
                "back_url": sheet.get("back_url") or "",
                # X6：composite 主键（注入通道优先）；storage_dir 缺省 → 空串（回退视图逻辑）
                "composite": (composite_path_for_sheet(sheet, storage_dir) or "") if storage_dir else "",
            }
    return roster


def _build_prop_roster(storage_dir: str | Path | None, payload: dict, user_id: str = "default") -> dict:
    """M3：道具 roster——name → {main_image, extra_images}（项目 prop_assets 读取）。

    门控同 _build_character_roster（跟随项目才有）。文件存在性由 ai_image fill 层
    校验（缺失跳过 + sidecar，M6 纪律）。
    """
    if not storage_dir or (payload or {}).get("effective_style_source") != "project":
        return {}
    pid = (payload or {}).get("course_project_id")
    if not _is_safe_course_project_id(pid):
        return {}
    path = Path(storage_dir) / "course_projects" / user_id / f"{pid}.json"
    if not path.exists():
        return {}
    try:
        from .course_models import CourseProject

        proj = CourseProject.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        return {}
    roster: dict = {}
    for pa in getattr(proj, "prop_assets", None) or []:
        if getattr(pa, "name", ""):
            roster[pa.name] = {"main_image": pa.main_image, "extra_images": list(pa.extra_images or [])}
    return roster


def _style_anchor_image_from_project(storage_dir: str | Path, payload: dict, user_id: str, *, task_dir: Path | str | None = None) -> str | None:
    """C4（决策 10/13）：跟随项目的任务读取项目 ``style_anchor_image`` 作生图锚图。

    专用参数通道——不经 ``reference_image_paths`` 角色列表下传（ai_video 不消费，
    视频帧风格经既有预览图继承机制传导）。开关 ``VIDEO_GEN_STYLE_ANCHOR_IMAGE``
    （默认开，C5 回退位，off 值口径 _OFF_VALUES）；
    ``effective_style_source != "project"``（显式脱离 / 未仲裁旧任务）→ None（C3 门控
    之外的第二道保险）。锚图是增强项：路径为空 = 未配置，静默 None；文件缺失 = 跳过 +
    sidecar ``skipped`` 留痕（不沿用角色图「透传→API 4xx」的响亮失败纪律——缺锚图
    不该炸整帧）。task_dir 提供时写 sidecar（frame_index=-1 = 任务级记录，锚图是
    任务级资产；二期按帧扩展）。
    """
    if os.environ.get("VIDEO_GEN_STYLE_ANCHOR_IMAGE", "1").strip().lower() in _OFF_VALUES:
        return None
    if not storage_dir:
        # 裸实例（部分既有测试经 VideoGenService.__new__ 构造、无 _storage_dir）→ 无锚图
        return None
    if (payload or {}).get("effective_style_source") != "project":
        return None
    pid = (payload or {}).get("course_project_id")
    if not _is_safe_course_project_id(pid):
        return None
    path = Path(storage_dir) / "course_projects" / user_id / f"{pid}.json"
    if not path.exists():
        return None
    try:
        from .course_models import CourseProject

        proj = CourseProject.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        return None
    anchor = getattr(proj, "style_anchor_image", None)
    if not anchor:
        return None
    if not Path(anchor).exists():
        record_ref_injection_sidecar(
            task_dir,
            frame_index=-1,
            declared_entities=[],
            injected_refs=[],
            truncated=[],
            skipped=[{"entity": "style_anchor", "path": anchor, "reason": "file_missing"}],
            reason="",
        )
        return None
    return anchor


def _warn_if_image_provider_not_i2i(config: VideoGenConfig, payload: dict, task_dir: Path, *, anchor_path: str | None = None) -> None:
    """C runtime 诚实化：若 ai_image_provider 不支持 i2i 却有项目角色参考图，
    打 warning + sidecar（避免静默 no-op）。默认 seedream 在白名单内（Task6 后），无 warning。"""
    provider = config.ai_image_provider.lower()
    if provider in I2I_CAPABLE_IMAGE_PROVIDERS:
        return
    refs = _character_ref_paths_from_payload(payload)
    # C4 边界①：告警条件从「有角色参考图」放宽为「任意参考图（含锚图）非空」——
    # 锚图被 provider 丢弃必须 sidecar + warning，不得静默。
    has_anchor = bool(anchor_path)
    if not refs and not has_anchor:
        return
    msg = f"当前 ai_image provider '{provider}' 不支持参考图（i2i），参考图（角色/风格锚图）未生效；切到 {sorted(I2I_CAPABLE_IMAGE_PROVIDERS)} 之一可启用"
    logger.warning(msg)
    try:
        (Path(task_dir) / "ai_image_i2i_warning.json").write_text(json.dumps({"warning": msg}, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


# C3（2026-08-24 计划）：全维度风格仲裁——哨兵与开关。
_STYLE_SENTINEL = "follow_project"  # C7 前端「跟随项目」选项值（决策 3）
_STYLE_ARBITRATION_ENV = "VIDEO_GEN_STYLE_ARBITRATION_V2"  # 决策 20：默认开，关断回旧「项目遮蔽」


@dataclass
class EffectiveStyle:
    """C3: 三处仲裁点（generate/draft/prompt 重生端点）统一的仲裁结果。

    source: "project"（跟随项目）| "task"（显式选择=完全脱离，含角色三视图）| "none"（无项目未显式）
    style_key: 仅留痕（决策 22），不驱动下发层风格尾（compose_dispatch_prompt 仍随任务显式 key）。
    """

    source: str
    style_prefix: str
    course_project_id: str | None
    style_key: str = ""


def _style_arbitration_v2_enabled() -> bool:
    """决策 20：默认开；off 值口径同 _OFF_VALUES（C5：勿顺手统一旧开关）。"""
    return os.environ.get(_STYLE_ARBITRATION_ENV, "1").strip().lower() not in _OFF_VALUES


def _inject_project_character_context(
    storage_dir: str | Path,
    payload: dict,
    user_id: str,
    *,
    force: bool = False,
) -> None:
    """Task 12 Step 4 + Task 20c: render 前把项目三视图/角色参考注入 payload。

    legacy 路径（ai_image/ai_video 直渲）无 _run_pipeline_engine 的注入，故在此补；
    multi_engine 路径（vimax/remotion）既有注入带 ``not payload.get(...)`` guard，幂等。

    force=True（Task 20c「项目已更新，重新生成」）覆盖 payload 既有（过期）sheets，
    强制拾取项目最新角色/风格让新改动生效。
    """
    # C3 门控（决策 3 全断口径）：显式脱离（effective_style_source == "task"）的任务
    # 不注入项目角色三视图（二期道具门控同点生效）。首渲（_run_pipeline）与重渲染
    # （re_render_task，经 C9 先仲裁）共用本入口——一处门控两处生效。
    if payload.get("effective_style_source") == "task":
        return
    pid = payload.get("course_project_id")
    if not pid:
        return
    if force or not payload.get("character_reference"):
        ref = _read_course_project_character_reference(storage_dir, pid, user_id)
        if ref:
            payload["character_reference"] = ref
    if force or not payload.get("character_sheets"):
        sheets = _read_course_project_character_sheets(storage_dir, pid, user_id)
        if sheets:
            payload["character_sheets"] = [s.to_dict() for s in sheets]
            if not payload.get("character_reference"):
                payload["character_reference"] = sheets[0].front_url
        elif force:
            # 项目已无 sheets（用户删了）→ 清掉 payload 过期快照，避免用旧参考
            payload.pop("character_sheets", None)
            payload.pop("character_reference", None)


def _write_storyboard_srt(
    storyboard: Storyboard,
    output: str,
    ass_output: str | None = None,
    *,
    llm_chunks_map: dict[int, list[str]] | None = None,
) -> None:
    """Build one SRT (canonical) + derived ASS spanning all frames.

    Uses the subtitle_engine.segment() for both edge-tts (word_cues) and
    doubao/360 (width-ratio) paths. ``subtitle_locked`` guard preserves
    user-edited SRT; ASS is still derived from the locked SRT via srt_to_ass.

    T8: ``llm_chunks_map`` (frame index → pre-computed LLM chunks) enables
    LLM semantic resegment; callers pre-compute via ``_precompute_llm_chunks``.
    """
    from app.gateway.video_gen.subtitle_engine import (
        Cue,
        normalize_word_cues,
        segment,
        srt_to_ass,
        write_ass,
        write_srt,
    )

    w = storyboard.config.width or 1920
    h = storyboard.config.height or 1080
    orientation = "portrait" if h > w else "landscape"
    style = storyboard.config.subtitle_style or "default"
    if style == "none":
        style = "default"  # none only means don't burn; ASS still derived for consistency

    # Auto-derive ASS path from SRT path when not explicitly passed
    if ass_output is None and output.endswith(".srt"):
        ass_output = output[:-4] + ".ass"

    if getattr(storyboard, "subtitle_locked", False):
        # 用户手改的字幕——narration 派生路径不得覆盖 SRT。
        # ASS 从 locked SRT 派生（用户文本/换行原样保留，不做 kinsoku 再处理）。
        logger.info("subtitle_locked: skip SRT rewrite, derive ASS from locked SRT %s", output)
        if ass_output and Path(output).exists():
            try:
                srt_to_ass(output, out_path=ass_output, video_w=w, video_h=h, style=style)
            except Exception:
                logger.warning("subtitle ASS derivation from locked SRT failed", exc_info=True)
        return

    def _parse_srt_timestamp(ts: str) -> float:
        """Parse 'HH:MM:SS,mmm' to seconds."""
        h, m, s_ms = ts.strip().split(":")
        s, ms = s_ms.split(",")
        return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000

    all_cues: list[Cue] = []
    offset = 0.0

    for frame in storyboard.frames:
        if frame.srt:
            # edge path: parse word cues -> normalize -> segment (preserve real timestamps)
            word_cues: list[tuple[str, float, float]] = []
            for block in frame.srt.strip().split("\n\n"):
                block = block.strip()
                if not block:
                    continue
                lines = block.split("\n")
                if len(lines) < 3:
                    continue
                parts = lines[1].split("-->")
                if len(parts) != 2:
                    continue
                word_cues.append(("\n".join(lines[2:]), _parse_srt_timestamp(parts[0]), _parse_srt_timestamp(parts[1])))
            normalized = normalize_word_cues(word_cues, frame.narration)
            # P0-2：edge 分支同样消费预计算的 LLM chunks——文本边界来自 LLM 重切，
            # 时间戳仍由 word_cues 区间映射（segment 天然支持 word_cues + llm_chunks 联用）。
            llm_chunks = llm_chunks_map.get(frame.index) if llm_chunks_map else None
            if normalized is not None:
                frame_cues = segment(
                    frame.narration,
                    word_cues=normalized,
                    orientation=orientation,
                    llm_chunks=llm_chunks,
                )
            else:
                # normalize mismatch -> fall back to width ratio (T5 fallback rule)
                basis = min(frame.speech_duration or frame.duration, frame.duration)
                frame_cues = segment(
                    frame.narration,
                    audio_duration=basis,
                    orientation=orientation,
                    llm_chunks=llm_chunks,
                )
        else:
            # doubao/360 path: width ratio (no word-level timestamps)
            # basis uses speech_duration (real TTS duration, no spf padding).
            basis = min(frame.speech_duration or frame.duration, frame.duration)
            frame_cues = segment(
                frame.narration,
                audio_duration=basis,
                orientation=orientation,
                llm_chunks=llm_chunks_map.get(frame.index) if llm_chunks_map else None,
            )

        for cue in frame_cues:
            all_cues.append(Cue(text=cue.text, start=cue.start + offset, end=cue.end + offset))
        offset += frame.duration

    # Write SRT (canonical, compatible with subtitle_locked/PUT editing)
    write_srt(all_cues, out_path=output)

    # Write ASS (derived product)
    if ass_output:
        try:
            write_ass(all_cues, out_path=ass_output, video_w=w, video_h=h, style=style)
        except Exception:
            logger.warning("subtitle ASS generation failed, falling back to SRT-only", exc_info=True)


def _refresh_ass_from_existing_srt(storyboard: Storyboard, task_dir: Path) -> None:
    """B1（R1a，2026-09-28 edits 修复批）：recompose 无漂移分支的字幕产物刷新。

    worker 对 .ass 恒优先（``_ffmpeg_helpers`` 的 ASS 优先判定），recompose 修前只在
    时长漂移时才重写字幕产物 → 改样式/改文本后旧 ASS 恒赢（R1）。无漂移时**不重切
    SRT**——recompose 调用点不传 ``llm_chunks_map``，重切会把 LLM 断句退化为规则链
    （v2 收窄决策）；只对现存 SRT 派生新 ASS，locked/未锁同一条路径。SRT 缺失
    （理论边界：渲染期恒写 srt，含 none 样式归一）才回退全量重建。
    """
    srt_path = task_dir / "subtitle.srt"
    if storyboard.config.subtitle_style == "none":
        # none 闸由 build_worker_payload 置空下发兜底；不派生（成片本就不烧）
        return
    style = storyboard.config.subtitle_style or "default"
    w = storyboard.config.width or 1920
    h = storyboard.config.height or 1080
    if srt_path.exists():
        try:
            from app.gateway.video_gen.subtitle_engine import srt_to_ass

            srt_to_ass(str(srt_path), out_path=str(task_dir / "subtitle.ass"), video_w=w, video_h=h, style=style)
        except Exception:
            logger.warning("recompose: ASS refresh from existing SRT failed", exc_info=True)
        return
    # 兜底：SRT 缺失（无字幕可保）→ 接受规则链重切的全量重建
    _write_storyboard_srt(storyboard, str(srt_path))


class _SynthFrameCtx:
    """逐帧 TTS 合成上下文（B4，2026-09-28 抽取共享）。

    ``_synthesize_audio_locked`` 的 synth_one 闭包与 ``regenerate_all_audio`` 批量重生
    复用同一逐帧语义：CAS 键（md5(narration|voice|speed)）、空旁白静音占位、同键兄弟帧
    收编、P2-14 actual-voice 归位、KIND_VOICE_FALLBACK 留痕、G1 spf 补静音。
    """

    def __init__(self, *, task_dir, audio_dir, voice_id, tts_speed, fallback_voice, semaphore, cas_key_locks, cas_results, seconds_per_frame, section_id, tts_mod, policy=None):
        self.task_dir = task_dir
        self.audio_dir = audio_dir
        self.voice_id = voice_id
        self.tts_speed = tts_speed
        self.fallback_voice = fallback_voice
        self.semaphore = semaphore
        self.cas_key_locks = cas_key_locks
        self.cas_results = cas_results
        self.seconds_per_frame = seconds_per_frame
        self.section_id = section_id
        # Plan B（2026-10-19）：ViMax 任务传 VIMAX_TTS_POLICY，其他任务 None（旧链路）
        self.policy = policy
        self.tts_mod = tts_mod


async def _synth_frame_cas(ctx: _SynthFrameCtx, frame) -> None:
    # 原闭包体逐字迁移（仅自由变量改经 ctx）——两处调用方语义必须保持逐字节一致。

    async with ctx.semaphore:
        # CAS naming: md5(narration|voice|speed) —— 换音色/速度不再同名覆盖
        content_hash = _audio_cache_key(frame.narration, ctx.voice_id, ctx.tts_speed)
        out = ctx.audio_dir / f"{content_hash}.mp3"
        if not frame.narration.strip():
            # 空旁白（纯画面/镜头标记剥离后无台词）→ 静音占位，避免 doubao
            # "No readable text" 经 asyncio.gather 拖垮整批 draft audio
            _write_silent_audio(out, seconds=1.0)
            frame.audio_path = str(out)
            frame.duration = 1.0
            frame.speech_duration = 0.0
            frame.srt = ""
            return
        # 同键串行：adopt 不得撞上兄弟帧在写/待归位的中间态（曾致悬空 audio_path
        # + 双帧键分叉，review-fix round 1）
        async with ctx.cas_key_locks.setdefault(content_hash, asyncio.Lock()):
            done = ctx.cas_results.get(content_hash)
            if done is not None:
                # 同键兄弟帧本轮已完成（含 P2-14 归位）→ 收编其最终元数据：降级时
                # 收敛到 actual-voice 键、不重调 TTS、speech/duration 与首帧一致
                frame.audio_path, frame.speech_duration, frame.duration, frame.srt = done
                return
            if out.exists() and out.stat().st_size > 0:
                # CAS 复用：同 narration|voice|speed 不再重调付费 TTS（draft→render
                # 曾全量重 TTS 两遍）。ffprobe 校时，与 _regenerate_frame CAS 分支同语义。
                # 锁内执行——文件不可能正被兄弟帧写入或归位搬走。
                probed = await asyncio.to_thread(_get_audio_duration, str(out))
                frame.audio_path = str(out)
                frame.speech_duration = probed if probed > 0 else max(len(frame.narration) * 0.15, 1.0)
                # G1 对齐 TTS 路径：spf 高于实测语音时补静音到 spf
                if ctx.seconds_per_frame and frame.speech_duration < ctx.seconds_per_frame:
                    # C9: 用返回值——补静音产物另存，CAS 原件保持纯 TTS 内容
                    frame.audio_path = _pad_audio_to_duration(out, ctx.seconds_per_frame)
                    frame.duration = ctx.seconds_per_frame
                else:
                    frame.duration = frame.speech_duration
                # frame.srt 保留 storyboard 现值（draft→render 时盘上 srt 与音频同代）
                ctx.cas_results[content_hash] = (frame.audio_path, frame.speech_duration, frame.duration, frame.srt)
                return
            r = await ctx.tts_mod.synthesize(
                frame.narration,
                voice_id=ctx.voice_id,
                speed=ctx.tts_speed,
                output_path=str(out),
                fallback_voice=ctx.fallback_voice,
                policy=ctx.policy,
                # T-E11：section_id 仅挂项目任务传（None 不传——窄签名桩/
                # 旧客户端零变化；与 entity_roster 同款条件 kwargs 惯用法）
                **({"section_id": ctx.section_id} if ctx.section_id else {}),
            )
            # P2-14: 降级产出按实际音色 CAS 键归位——否则错误音色顶着请求音色键被
            # 之后所有同文本同音色请求永久命中。先归位再查存在性：同键兄弟帧本调用内
            # 已由按键串行锁互斥；src 缺失但 actual_out 在（如上一轮已归位、本轮客户端
            # 未重写 src）→ 直接 adopt，不误报 missing（防御保留，语义不变）。
            if r.actual_voice_id and r.actual_voice_id != ctx.voice_id:
                actual_hash = _audio_cache_key(frame.narration, r.actual_voice_id, ctx.tts_speed)
                actual_out = ctx.audio_dir / f"{actual_hash}.mp3"
                src = Path(r.audio_path)
                if src != actual_out:
                    if src.exists():
                        src.replace(actual_out)
                    elif not actual_out.exists():
                        raise RuntimeError(f"TTS output missing: {r.audio_path}")
                    r = ctx.tts_mod.TTSResult(audio_path=str(actual_out), srt=r.srt, duration=r.duration, actual_voice_id=r.actual_voice_id)
                logger.warning("TTS degraded: requested %s, actual %s — stored under actual-voice CAS key", ctx.voice_id, r.actual_voice_id)
                # 决策 3（2026-07-31）：音色降级留痕。降级本身是有意的
                # （配额耗尽/限流不该让整批 draft audio 崩掉），但用户
                # 听得出中途换声、界面却零解释。此处是唯一能同时看到
                # 「请求音色 vs 实际音色」和 task_dir 的地方，且覆盖所有
                # 降级成因（配额/传输/5xx/access key 缺失）。
                record_degradation(
                    ctx.task_dir,
                    kind=KIND_VOICE_FALLBACK,
                    detail=f"请求 {ctx.voice_id}，实际 {r.actual_voice_id}",
                    frame_index=frame.index,
                    requested_voice=ctx.voice_id,
                    actual_voice=r.actual_voice_id,
                )
            if not Path(r.audio_path).exists():
                raise RuntimeError(f"TTS output missing: {r.audio_path}")
            frame.audio_path = r.audio_path
            tts_dur = r.duration if r.duration > 0 else max(len(frame.narration) * 0.15, 1.0)
            # 字幕配比基准：真实语音时长（在 spf 补静音之前记录）
            frame.speech_duration = tts_dur
            # G1: ctx.seconds_per_frame 驱动每帧时长——TTS 短于 spf 时补静音到 spf，画面不再闪太快
            if ctx.seconds_per_frame and tts_dur < ctx.seconds_per_frame:
                # C9: 用返回值——补静音产物另存，CAS 原件保持纯 TTS 内容
                frame.audio_path = _pad_audio_to_duration(Path(r.audio_path), ctx.seconds_per_frame)
                frame.duration = ctx.seconds_per_frame
            else:
                frame.duration = tts_dur
            frame.srt = r.srt
            ctx.cas_results[content_hash] = (frame.audio_path, frame.speech_duration, frame.duration, frame.srt)


async def _precompute_llm_chunks(
    storyboard: Storyboard,
    config: VideoGenConfig,
    task_dir: Path,
) -> dict[int, list[str]]:
    """T8: 预计算 LLM 语义重切 chunks（per-frame），供 _write_storyboard_srt 使用。

    config.subtitle_llm_enabled=False 或模型不可用时返回空 dict（规则链兜底）。
    """
    if not getattr(config, "subtitle_llm_enabled", False):
        return {}

    from app.gateway.video_gen.subtitle_engine import _MAX_WIDTH, llm_resegment

    model_name = getattr(config, "subtitle_llm_model", None)
    if not model_name:
        return {}

    try:
        from deerflow.models import create_chat_model

        llm = create_chat_model(model_name)
    except Exception:
        logger.warning("T8: subtitle LLM model %s unavailable, using rule chain", model_name, exc_info=True)
        # 条目 7②（2026-09-12 缺陷批）：质量静默降级留痕——config.py 注释明写
        # 「不得静默退回规则链」，修前只有 warning log、对用户零可见。复用
        # record_degradation（KIND_VOICE_FALLBACK 同款降级留痕模式），任务详情
        # 告警可见、任务继续（规则链兜底本身是设计行为）。
        record_degradation(
            task_dir,
            kind=KIND_SUBTITLE_LLM_FALLBACK,
            detail=f"字幕模型 {model_name} 不可用，已回退规则链断句",
        )
        return {}

    max_width = 16.0  # landscape default
    w = storyboard.config.width or 1920
    h = storyboard.config.height or 1080
    if h > w:
        max_width = _MAX_WIDTH["portrait"]

    cache_path = str(task_dir / "subtitle_llm_cache.json")
    result: dict[int, list[str]] = {}
    # T7: 总量预算——time.monotonic() 起手，每帧前检查；到点停止，
    # 剩余帧不写入 result → 规则链兜底（既有语义）。已完成帧的条目保留。
    budget_deadline = time.monotonic() + SUBTITLE_LLM_TOTAL_BUDGET
    for frame in storyboard.frames:
        if time.monotonic() >= budget_deadline:
            remaining = sum(1 for f in storyboard.frames if f.index >= frame.index and f.narration and f.narration.strip())
            logger.warning(
                "T8: subtitle LLM resegmentation budget exhausted (%.0fs) — %d remaining frames use rule chain",
                SUBTITLE_LLM_TOTAL_BUDGET,
                remaining,
            )
            break
        if not frame.narration or not frame.narration.strip():
            continue
        chunks = await llm_resegment(
            frame.narration,
            max_width=max_width,
            llm=llm,
            cache_path=cache_path,
        )
        if chunks is not None:
            result[frame.index] = chunks
    return result


# ── Service ──────────────────────────────────────────────────────────────


# 风格查表已下沉到 style_presets 模块（Task 1/2，2026-08-01），见文件头 import。
#
# 为什么下沉：本模块已 import course_models，而项目级 style_prefix 也要查同一张表，
# 若表留在这里就会 course_models → service → course_models 成环。
# 表在独立模块后两边单向依赖，且保证「任务级与项目级不会各造一套词汇」。
#
# 注入路径不变：plan_course 的 style_context.style_prefix（planner 加到每个
# image_prompt 开头）。无 CourseProject 时由 _build_style_context_from_payload
# 用 payload.style 查表；有 CourseProject 时由 course_models.style_prefix 查同一张表
# （二选一不叠加，见 _load_course_project_style 与该方法的调用点）。


# tool video_type → task framework task_type.
# 2026-07-22: remotion 摘牌——remotion 由 course-video agent + auto-video-flow
# 直写渲染（2026-07-15 split），HTTP 层已 422；工具层再映射 remotion 会绕回
# legacy template 渲染路径（_render_remotion_storyboard）。
_VIDEO_TYPE_TO_TASK_TYPE: dict[str, str] = {
    "vimax": "vimax",
}


def _blueprint_for_confirmation(storyboard: dict) -> list[dict]:
    """Inline per-frame text for the plan_storyboard tool result (2026-07-22).

    The confirmation gate must review content the user can SEE. storyboard.json
    lives gateway-side (.deer-flow/video-gen/{user}/tasks/{id}/) — outside the
    AioSandbox, so the agent's present_files/file tools cannot reach it
    (present_file_tool hard-rejects paths outside the thread outputs dir).
    Returning only n_frames made the gate confirm a black box. Narration in
    full; visual prompt capped at 120 chars; frames capped at 40 (vimax drafts
    are ≤ ~20).
    """
    blueprint: list[dict] = []
    for frame in (storyboard.get("frames") or [])[:40]:
        blueprint.append(
            {
                "index": frame.get("index"),
                "narration": frame.get("narration") or "",
                "visual": (frame.get("image_prompt") or "")[:120],
                "duration": frame.get("duration"),
            }
        )
    return blueprint


def _resolve_vimax_video_backend(style: str | None, override: str | None, config: Any) -> tuple[str, str]:
    """Pick the vimax video backend + model.

    ``override`` (VideoGenConfig.vimax_video_backend): ``"seedance"`` (default
    since C5a 2026-10-12) or ``"auto"`` (same meaning) → 360-gateway seedance
    r2v. An explicit ``seedance-ark``/``hailuo``/``happyhorse`` forces that
    backend. Unknown values raise ValueError (typo 防呆 — 不得静默落分支).

    历史沿革：2026-07 Stage 5.2 时默认 seedance-ark——当时 360 网关 seedance
    (doubao-seedance-2-0) 用 InputImageSensitiveContentDetected 拦一切类人角色
    首帧，vimax 因此迁 Ark 直连（字节"信任产物" §0.4 放行 Seedream 产物）。
    2026-10-12 用户决策迁回 360（Ark 账号级 3 并发撑不起团队规模，与 E3 口径
    一致）；人物参考图是否仍被拦以 C5c 角色 spike 实测为准——被拦则视频侧落
    hailuo/happyhorse（qihu_video_model / happyhorse_video_model）。

    Returns ``(backend, model)``. ``style`` is accepted for signature stability
    but does not drive the auto routing. Lives at module level for unit testing.
    """
    explicit = (override or "auto").lower()
    if explicit in ("auto", "seedance"):
        # C5a: auto 语义翻转 → 360 seedance（与代码默认同值；部署开关在 config
        # 显式落值，非代码默认——见计划 C5a 部署纪律）。
        return "seedance", config.seedance_video_model
    if explicit == "seedance-ark":
        return "seedance-ark", config.seedance_ark_video_model
    if explicit == "hailuo":
        return "hailuo", config.qihu_video_model
    if explicit == "happyhorse":
        return "happyhorse", config.happyhorse_video_model
    raise ValueError(f"vimax_video_backend 未知值 {override!r}——合法值: seedance(默认/auto 同义) | seedance-ark(回滚位) | hailuo | happyhorse；typo 会静默落错通道，必须显式报错（2026-10-12 C5 防呆）")


def _resolve_vimax_backend_selection(config: Any, *, style: str | None = None) -> dict[str, str]:
    """ViMax 实际 backend/model 选择 + 媒体价目 provider 映射（单一来源）。

    同时供 ``build_vimax_config`` 调用方与费用预估使用——不得在预估侧另造
    provider 推断。image: 360→seedream-360 / ark→seedream；video: seedance /
    seedance-ark 直接同名，hailuo/happyhorse 无价目行时由 resolver 返回 None
    → 预估标 partial。
    """
    image_backend = str(getattr(config, "vimax_image_backend", "360") or "360").strip().lower()
    if image_backend not in ("360", "ark"):
        raise ValueError(f"vimax_image_backend 未知值 {getattr(config, 'vimax_image_backend', None)!r}——合法值: 360(默认通道) | ark(回滚位)；typo 会静默落错通道，必须显式报错（2026-10-12 C5 防呆）")
    video_backend, video_model = _resolve_vimax_video_backend(style=style, override=getattr(config, "vimax_video_backend", None), config=config)
    return {
        "image_backend": image_backend,
        "image_model": config.seedream_360_image_model if image_backend == "360" else config.seedream_image_model,
        "image_pricing_provider": "seedream-360" if image_backend == "360" else "seedream",
        "video_backend": video_backend,
        "video_model": video_model,
        "video_pricing_provider": video_backend,
    }


def _target_to_scene_bounds(target_duration: float | None) -> tuple[int, int]:
    """P0-3: target_duration → (max_scenes, max_shots)，作下限保证（每镜约 8s）。

    120s → ≥15 镜（而非仅设上限），从源头保证镜数足够撑满目标时长。
    ViMax 路径不调此函数（ViMax scene 粒度不同，90s/scene，见 _build_vimax_payload）。
    """
    if not target_duration or target_duration <= 0:
        return 20, 40  # 既有默认
    scenes = max(8, int(target_duration / 8))  # 下限：至少够撑目标
    return scenes, scenes * 2


def _resolve_n_scenes(payload: dict) -> int:
    """H: 解析镜数——用户显式 n_scenes 优先；否则按 target_duration 下限保证（每镜~8s）。

    E4: seconds_per_frame（3-8s/帧）+ target_duration → ceil(target/spf)，用户密度胜过 8s 下限
    （8s 下限仅保证够撑，用户可选更密/更稀）。修死代码：_target_to_scene_bounds 之前只被测试引用，
    真实非-ViMax 路径用默认 5 镜、完全无视 target_duration。此处接入让 target 真影响镜数
    （下限保证，非双向约束——不自动减镜/扩写，仅保证镜数足够撑满目标时长）。
    """
    explicit = payload.get("n_scenes")
    if isinstance(explicit, int) and explicit > 0:
        return explicit
    target = payload.get("target_duration")
    spf = payload.get("seconds_per_frame")
    if spf and target and target > 0:
        spf = max(3, min(8, spf))  # E4: 密度限幅 3-8s/帧（前端 min/max 同步）
        return max(1, math.ceil(target / spf))
    if target and target > 0:
        return _target_to_scene_bounds(target)[0]  # 下限：max(8, target/8)
    return 5  # 既有默认


def _target_scenes_for_fixed(payload: dict, script_text: str) -> int:
    """fixed 模式镜数决策：显式 n_scenes 优先；spf+target_duration 推导次之；否则按文长。

    P1-E11: 修前 _target_scenes 表达式仅当 spf+target 同时存在才调 _resolve_n_scenes，
    用户显式指定的 n_scenes 被文长推断静默覆盖。本 helper 统一三路决策。
    """
    if payload.get("n_scenes") or (payload.get("seconds_per_frame") and payload.get("target_duration")):
        return _resolve_n_scenes(payload)
    from .orchestration import script as script_mod

    return script_mod._target_scenes_from_len(script_text)


# P2-6: 与 storyboard.tsx addFrame 的 narration 占位初值一致（前端 :683）
_PLACEHOLDER_NARRATION_MARKER = "（新分镜，请编辑旁白）"


def _validate_narrations_renderable(frames: list[dict]) -> None:
    """P2-6 渲染前校验：空旁白或仍是新增分镜占位文本 → ValueError 列出帧号。

    修前无校验——忘改的占位旁白被 TTS 逐字念进成片。
    """
    bad = [f.get("index", i) for i, f in enumerate(frames) if not (f.get("narration") or "").strip() or _PLACEHOLDER_NARRATION_MARKER in (f.get("narration") or "")]
    if bad:
        raise ValueError(f"分镜 {bad} 旁白为空或仍是占位文本，请先在分镜页编辑后再渲染")


# 2026-08-03 Task 14：_frames_to_regenerate 已搬到 frame_ops.py（纯搬迁，零行为变化）。
# 保留私名别名，使本文件 5 处调用点零改动。
_frames_to_regenerate = frames_to_regenerate


_USER_VISUAL_PREFIX = "visuals/frame_"

# 条目 35（2026-09-12 缺陷批）：preview_state/regen_state="running" 崩溃滞留无 TTL
# → storyboard.json 永久脏数据 + 崩溃后需手动重点。失效阈值 10min = 最长 provider
# 超时（240-300s，seedream/gpt_image2 read_timeout）×2 余量，不误杀合法在途。
# 实现位（二轮核实收窄）：POST 入口落盘重置（_expire_stale_running_state）；
# GET storyboard 高频轮询路径只读出折算（fold_stale_running_states）**不落盘**。
# 无 *_at 时间戳的旧数据不触发（向后兼容）。
RUNNING_STATE_STALE_SECONDS = 600
STALE_PREVIEW_RESET_MESSAGE = "预览中断，请重试"
STALE_REGEN_RESET_MESSAGE = "重新生成中断，请重试"


def _frame_state_is_stale(frame: dict, state_key: str, at_key: str) -> bool:
    """帧的 running 态是否超过失效阈值（非 running / 缺时间戳 → False）。"""
    if not isinstance(frame, dict) or frame.get(state_key) != "running":
        return False
    try:
        ts = datetime.fromisoformat(str(frame.get(at_key)))
    except (TypeError, ValueError):
        return False
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return (datetime.now(UTC) - ts).total_seconds() > RUNNING_STATE_STALE_SECONDS


def fold_stale_running_states(data: dict) -> None:
    """GET storyboard 读出折算：>10min 的 running 视为失效（error + 可重试文案），
    只改响应 dict、不落盘（写入口由 _expire_stale_running_state 负责）。"""
    frames = data.get("frames") if isinstance(data, dict) else None
    if not isinstance(frames, list):
        return
    for f in frames:
        if _frame_state_is_stale(f, "preview_state", "preview_state_at"):
            f["preview_state"] = "error"
            f["preview_error"] = STALE_PREVIEW_RESET_MESSAGE
        if _frame_state_is_stale(f, "regen_state", "regen_state_at"):
            f["regen_state"] = "error"
            f["regen_error"] = STALE_REGEN_RESET_MESSAGE


def frame_has_user_visual(frame) -> bool:
    """该帧的画面是否来自用户上传/替换（不得被 AI 预览覆盖）。

    2026-07-29：渲染路径早有守卫（``_frames_to_regenerate`` 内 P0-1），预览路径
    没有——``upload_frame_visual`` 把 visual_url 显式置 None，而
    ``preview_all_frames`` 的选帧条件正是 ``not visual_url``，精准命中上传帧；
    ``_generate_frame_preview`` 的 _updater 再无条件覆写 local_visual_path。

    判据用 ``visuals/frame_`` 前缀（``upload_frame_visual`` 的命名）而非「有
    local_visual_path」——后者会把 gateway 本地化的 AI 图（``visuals/img_NNNN``，
    Task 3 起每帧都写）也误判成用户素材，导致重新预览对这些帧永久失效。
    """
    if isinstance(frame, dict):
        local, url = frame.get("local_visual_path"), frame.get("visual_url")
    else:
        local = getattr(frame, "local_visual_path", None)
        url = getattr(frame, "visual_url", None)
    return bool(local) and not url and str(local).startswith(_USER_VISUAL_PREFIX)


def assert_frame_previewable_not_upload(task_dir: Path, frame_index: int) -> None:
    """预览生成前的上传守卫（409 语义）。Raises ValueError。

    storyboard 读不出来时放行——守卫的职责是保护上传帧，不该因为解析失败
    把整个预览入口连带堵死（真正的缺失/损坏由既有校验负责报错）。
    """
    sb_path = task_dir / "storyboard.json"
    if not sb_path.exists():
        return
    try:
        data = json.loads(sb_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    frame = next((f for f in data.get("frames", []) if isinstance(f, dict) and f.get("index") == frame_index), None)
    if frame is not None and frame_has_user_visual(frame):
        raise ValueError("该帧使用的是你上传的画面——生成预览会覆盖它。如需换成 AI 生成画面，请先点「重新生成」")


def _check_duration_drift(actual: float, target: float | None) -> str | None:
    """P0-3: 实际时长与目标偏差 >20% 返回中文提示，否则 None。

    在视频合成完成后调用（拿到实际时长时），写 task result.duration_warning。
    """
    if not target or target <= 0:
        return None
    drift = abs(actual - target) / target
    if drift <= 0.2:
        return None
    if actual < target:
        return f"实际时长 {actual:.0f}s 短于目标 {target:.0f}s（旁白字数不足），建议扩写脚本或增加镜头"
    return f"实际时长 {actual:.0f}s 长于目标 {target:.0f}s，建议精简脚本"


def _extract_subtitle_status(task_dir: Path) -> dict:
    """P0-2: 从 worker 写的 subtitle_status.json 提取字幕烧录状态。

    worker 烧录段写 {status: ok|failed|skipped, error?}。无文件/损坏→skipped（不崩）。
    替代旧的静默 subtitle_burn_failed.txt——失败不再对用户不可见。
    """
    status_file = Path(task_dir) / "subtitle_status.json"
    if not status_file.exists():
        return {"status": "skipped", "error": None}
    try:
        data = json.loads(status_file.read_text(encoding="utf-8"))
        return {"status": data.get("status", "skipped"), "error": data.get("error")}
    except (json.JSONDecodeError, OSError):
        return {"status": "skipped", "error": None}


def _extract_remotion_warnings(task_dir: Path) -> dict | None:
    """F: 提取 remotion CJK 字体缺失 warning（若有 sidecar）。无→None。"""
    wfile = Path(task_dir) / "remotion_cjk_warning.json"
    if not wfile.exists():
        return None
    try:
        return json.loads(wfile.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _extract_visual_coverage(task_dir: Path) -> dict | None:
    """G: 提取 ai_video 覆盖状态（partial 时 worker-side visual_source 写的 sidecar）。"""
    cfile = Path(task_dir) / "visual_coverage.json"
    if not cfile.exists():
        return None
    try:
        return json.loads(cfile.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _extract_av_drift(task_dir: Path) -> dict | None:
    """E4: 提取转场音画漂移状态（worker 拼接后 probe 写的 av_drift.json sidecar）。

    镜像 _extract_visual_coverage：转场 xfade 使视频总时长 < 音频，drift 超阈值时 worker
    写此 sidecar，gateway 据此回传诚实提示（不阻断、不修漂移）。
    """
    dfile = Path(task_dir) / "av_drift.json"
    if not dfile.exists():
        return None
    try:
        return json.loads(dfile.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _build_frame_audio_map(frames) -> dict[int, dict]:
    """E11: 构建 frame.index → 音频信息 的映射（按帧键，非 narration hash）。

    旧实现用 md5(narration) 作 key，重复旁白帧互相覆盖 → map 条目数 < 帧数，
    使 merged_count（按帧累加）与 total=len(map)（=unique narration 数）口径不一，
    阈值 merged_count>=total*0.5 语义偏松、audio_ready 误判。改按 frame.index 键后：
    每帧独立条目（不覆盖），total/merged_count 统一按帧计。**TTS 文件级去重不变**
    （_synthesize_audio 的 CAS 文件名仍 md5(narration)，相同旁白复用同一音频文件）。

    2026-10-06 漂移批（v2）：value 附带合成代 ``narration``——仅供
    _merge_audio_into_storyboard(verify_drift=True) 做内容等价比对（磁盘旁白 vs
    合成代旁白字符串直比）；**不是合并载荷**（updater 写盘前剥离，防覆写磁盘新旁白）。
    """
    return {f.index: {"audio_path": f.audio_path, "duration": f.duration, "srt": f.srt, "speech_duration": f.speech_duration, "narration": f.narration or ""} for f in frames}


def _resolve_vimax_story_model(
    vimax_llm_model: str | None,
    script_model: str | None,
    vimax_llm_base_url: str,
    task_dir: Path | None = None,
) -> Any:
    """Resolve the LLM for ViMax story generation.

    ``vimax_llm_model`` is a dual-purpose config field:
    1. ``build_vimax_config`` passes it as a bare API model id to Ark.
    2. ``create_chat_model`` expects a registered ``models[]`` name.

    This helper tries the value as a registered name first; on ValueError it
    reverse-looks-up the registered name by matching model id + base_url.
    Falls back to the default model (``create_chat_model(None)``) with a warning
    only if no match is found. That fallback is the only true roster miss, so it
    alone records a ``model_roster_miss`` degradation（复审修订 2026-09-13：建单侧
    预检对双用途字段是系统性误报——裸 model id 反查命中/缺省值非注册名都会假红，
    可见性收拢到这里的真实回退点）.
    """
    from deerflow.models import create_chat_model

    candidate = vimax_llm_model or script_model
    try:
        return create_chat_model(candidate, thinking_enabled=False)
    except ValueError:
        from deerflow.config import get_app_config

        app_cfg = get_app_config()
        for m in app_cfg.models:
            if m.model == vimax_llm_model and (not vimax_llm_base_url or getattr(m, "api_base", None) == vimax_llm_base_url or getattr(m, "base_url", None) == vimax_llm_base_url):
                return create_chat_model(m.name, thinking_enabled=False)
        logger.warning(
            "vimax_llm_model %r 既非注册名也反查不到对应 models[] 条目；回退默认模型",
            vimax_llm_model,
        )
        if task_dir is not None:
            record_degradation(
                task_dir,
                kind=KIND_MODEL_ROSTER_MISS,
                detail=f"vimax_llm_model {vimax_llm_model} 未在花名册注册且反查无果，已回退默认模型；请联系管理员检查 config.yaml（修改 video_gen 段后需重启 gateway 生效）",
            )
        return create_chat_model(None, thinking_enabled=False)


# 终审 fix pass（Important 2）：vimax redo 的 per-task 互斥锁表。事件循环单线程内
# setdefault 无 await 间隙——创建原子；锁随任务生命周期常驻（条目数 = 历史任务数，
# 每项一个空 asyncio.Lock，量级可忽略；不做清理——清理须全局锁表自身互斥，得不偿失）。
_VIMAX_REDO_LOCKS: dict[str, asyncio.Lock] = {}


def _vimax_redo_lock(task_id: str) -> asyncio.Lock:
    return _VIMAX_REDO_LOCKS.setdefault(task_id, asyncio.Lock())


async def _finish_engine_outcome(progress: Any, outcome: Any) -> None:
    """T2（Lane 2, 2026-10-15）：finished 引擎产物的 result 落库接缝。

    在原 output_path/file_size 之上并入 ``engine_metadata``（vimax shots
    manifest 走此通道进 task result——redo 端点的数据源；修前从不落库）。
    set_result 是 read→merge→write 原子合并，engine_metadata 键不会覆盖
    output_path/file_size（二者在 merge 后写入）。"""
    out = Path(outcome.finished_video_path)  # type: ignore[arg-type]
    await progress(progress=95)
    merged = dict(outcome.engine_metadata or {})
    merged.update(output_path=str(out), file_size=out.stat().st_size)
    await progress.set_result(**merged)


class VideoGenService(BaseTaskService):
    """Unified video generation service with pluggable visual sources."""

    domain = "video_gen"
    id_prefix = "vg"
    # T6->W5（2026-08-24 活性重构）：progress 冻结兜底窗口改为配置驱动
    # （stall_progress_freeze_seconds，默认 1800=P5），__init__ 注入；仅对
    # 无活性覆盖阶段生效（覆盖期 B/C 全旁路，防 08-20/21 型误杀）。
    progress_stall_timeout: int | None = None

    def __init__(
        self,
        *,
        repo: TaskRepository,
        config: VideoGenConfig,
        providers: ProviderRegistry,
        tts_service: Any = None,
        storage_dir: str = ".deer-flow/video-gen",
        sandbox_provider: Any = None,
        app_state: Any = None,
    ) -> None:
        super().__init__(
            repo=repo,
            storage_dir=storage_dir,
            max_concurrent_tasks=config.max_concurrent_tasks,
            stall_timeout=config.stall_timeout_seconds,
            max_tasks_per_user=config.max_tasks_per_user,
        )
        # W5：看门狗旋钮从 config 注入（0 值归一为 None = 谓词关闭）
        self.liveness_threshold_seconds = config.liveness_threshold_seconds or None
        self.progress_stall_timeout = config.stall_progress_freeze_seconds or None
        self.composing_backstop_enabled = config.composing_backstop_enabled
        self._config = config
        self._providers = providers
        self._tts = tts_service
        self._sandbox = sandbox_provider
        self._sb_write_lock = asyncio.Lock()
        # app.state singletons (run_manager / stream_bridge / run_context) for the
        # remotion agent delegation path; None in tests / when remotion_engine != agent.
        self._app_state = app_state
        self._agent_runner: Any = None
        self._engine_registry: Any = None  # lazy, multi_engine path only (阶段 1.5)

    # ── W1/W2：活性挂点 + W4：分阶段预算公式（标量在配置、公式在代码）─────

    def _liveness(self, task_id: str | None, *, stage: str | None = None, stage_budget: float | None = None):
        """with_liveness 快捷挂点。task_id 缺失（存量调用/无任务上下文）时 no-op。

        挂法原则（W2）：**阶段级包一层**--整个 gather/阶段一个 tick task，
        避免逐调用 churn；仅在无阶段层可包时才退到调用级。
        """
        if not task_id:
            return _NULL_LIVENESS
        return self.with_liveness(task_id, stage=stage, stage_budget=stage_budget)

    def _budget_scalars(self):
        """预算标量（桩 config / 老版本 config 对象回落默认值）。"""
        return getattr(self._config, "stall_budget_scalars", None) or _DEFAULT_BUDGET_SCALARS

    def _stage_budget_llm(self, n_frames: int, *, concurrency: int = 1) -> float:
        """LLM 阶段（脚本归一/解析 + 分镜生成循环）总预算。

        口径：串行轮数 = 2（脚本归一/解析相：parse 串行 chunk + normalize 单调用）
        + ceil(n/并发)；每轮预算 ``llm_floor``（单调用 120s×3 次重试 + 退避
        ≈ 8min，一轮并发内的最坏调用链），全程再乘裕量 margin。
        """
        sc = self._budget_scalars()
        rounds = 2 + math.ceil(max(0, n_frames) / max(1, concurrency))
        return sc.margin * rounds * sc.llm_floor

    def _stage_budget_frame_render(self, n_frames: int, *, concurrency: int = 1) -> float:
        """帧渲染阶段总预算：margin × ceil(n/并发) × frame_floor。

        ``frame_floor``（≈20min）是「生成 300s × (1+续写段数) + 本地化 180s +
        裕量」的公式化定稿值（Tier E 续写使单帧合法最坏 ≈13.5min）。
        """
        sc = self._budget_scalars()
        rounds = max(1, math.ceil(max(0, n_frames) / max(1, concurrency)))
        return sc.margin * rounds * sc.frame_floor

    def _stage_budget_tts(self, n_frames: int) -> float:
        """TTS 整批总预算：margin × max(gather 超时, per_frame×n)。

        口径统一（2026-08-25 复核）：取 ``_tts_gather_timeout(n)``（既有模块
        常量公式，gather 自身的硬超时）与标量 ``tts_per_frame×n`` 的**较大者**
        再乘裕量--否则日后只调一处（如把模块常量 TTS_GATHER_TIMEOUT_PER_FRAME
        调大、或把 tts_per_frame 调到 <60）会让预算低于实际 gather 超时，预算
        谓词先于 gather 自身超时误杀健康任务。gather 之上的 concat/字幕预切
        等附加耗时由 margin 盖住。
        """
        sc = self._budget_scalars()
        n = max(0, n_frames)
        return sc.margin * max(_tts_gather_timeout(n), sc.tts_per_frame * n)

    def _stage_budget_compose(self, video_duration_seconds: float | None) -> float:
        """合成阶段总预算：margin × max(视频时长 × 吞吐系数, remotion_agent_timeout)。

        compose_per_minute = 每分钟视频给多少秒合成预算（30s/min ≈ 0.5× 实测
        吞吐）；下限 remotion_agent_timeout（gateway 内合成主路径是 remotion
        agent 渲染，其自身超时配置就是合法时长的一部分，预算不得低于它--
        否则预算谓词会在 agent 自身超时前先杀健康渲染）。
        """
        sc = self._budget_scalars()
        duration = video_duration_seconds if video_duration_seconds and video_duration_seconds > 0 else 30.0
        by_duration = duration * sc.compose_per_minute / 60.0
        agent_timeout = getattr(self._config, "remotion_agent_timeout", 1500) or 1500
        return sc.margin * max(by_duration, agent_timeout)

    def stall_timeout_for_task(self, task) -> int:
        """E5b+B1: 按任务真实帧数缩放 stall_timeout。优先读磁盘 storyboard.json 的实际
        帧数（最准——fixed 模式上传文档时 payload 的 n_scenes/target_duration/seconds_per_frame
        全是 None，旧逻辑在此场景退回默认 1800s，致 272 帧长任务被 reaper 误杀）。
        storyboard.json 缺失时回落 payload 推导，再缺失回落 self.stall_timeout。
        """
        # 优先：磁盘 storyboard.json 实际帧数（draft 后即落盘，reaper 触发时必然存在）。
        # Task 17: 这一段独立 try——reaper 传入的精简 task 可能没有 user_id/id，
        # 旧实现让该 AttributeError 冒到外层 except，把 payload 回落也一并吞掉，
        # 于是 54 帧任务拿默认 1800s 被误杀。磁盘分支失败必须继续走 payload 推导。
        try:
            sb_path = Path(self._storage_dir) / task.user_id / "tasks" / task.id / "storyboard.json"
            if sb_path.exists():
                sb = json.loads(sb_path.read_text(encoding="utf-8"))
                n = len(sb.get("frames", []))
                if n > 0:
                    return self._config.effective_stall_timeout(n)
        except Exception:
            logger.debug("stall_timeout_for_task: disk storyboard probe failed, trying payload", exc_info=True)
        try:
            # 回落1：payload 字段推导（旧逻辑）
            raw = getattr(task, "payload", None)
            if raw:
                payload = json.loads(raw) if isinstance(raw, str) else (raw if isinstance(raw, dict) else {})
                if payload.get("target_duration") or payload.get("n_scenes") or payload.get("seconds_per_frame"):
                    return self._config.effective_stall_timeout(_resolve_n_scenes(payload))
        except Exception:
            logger.debug("stall_timeout_for_task: scale failed, using default stall_timeout", exc_info=True)
        return self.stall_timeout

    @property
    def providers(self) -> ProviderRegistry:
        """Public accessor for ProviderRegistry (used by assets_router)."""
        return self._providers

    @property
    def config(self) -> VideoGenConfig:
        """Public accessor for VideoGenConfig (used by assets_router)."""
        return self._config

    def _get_sandbox_provider(self):
        """Get sandbox provider, falling back to the global deerflow provider."""
        if self._sandbox is not None:
            return self._sandbox
        from deerflow.sandbox import get_sandbox_provider

        return get_sandbox_provider()

    async def _update_storyboard(
        self,
        task_dir: Path,
        updater: Callable[[dict], None],
    ) -> None:
        """Thread-safe + atomic storyboard.json update.

        1. Lock (prevent _generate_draft_audio and _on_frame_done concurrent writes)
        2. Read latest from disk (prevent Lost Update)
        3. Apply updater function (modify only target fields)
        4. Atomic write-back (tmp + rename, prevent SSE reading half-written file)
        """
        sb_path = task_dir / "storyboard.json"
        if not sb_path.exists():
            return
        async with self._sb_write_lock:
            data = json.loads(sb_path.read_text(encoding="utf-8"))
            updater(data)
            tmp_path = sb_path.with_suffix(".json.tmp")
            tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp_path.replace(sb_path)

    async def _add_frame(self, task_id: str, user_id: str, frame: dict, index: int | None = None) -> None:
        """M-UX: insert a frame (append if index None/out-of-range) + reindex 0..N continuous.

        Uses _update_storyboard (atomic + lock). Does NOT touch audio/SRT — a freshly
        added frame has no audio yet (audio_ready stays False until next preview/regen).
        """
        task_dir = Path(self._storage_dir) / user_id / "tasks" / task_id

        _insert_at_cell: list[int] = []

        def _updater(data: dict) -> None:
            frames = data.setdefault("frames", [])
            new = dict(frame)
            new.setdefault("narration", "")
            new.setdefault("image_prompt", "")
            if index is None or index >= len(frames):
                frames.append(new)
                insert_at = len(frames) - 1
            else:
                insert_at = max(0, index)
                frames.insert(insert_at, new)
            for i, f in enumerate(frames):
                f["index"] = i
            # P0：新帧并入分组——_add_frame 不维护 scene_groups 时，分组渲染只画
            # group.frame_indices 登记过的帧，新帧不可见也删不掉（删除按钮长在帧卡上）。
            if data.get("scene_groups"):
                data["scene_groups"] = remap_scene_groups_after_insert(data["scene_groups"], insert_at)
            # S1（2026-08-26 调度表计划）：旧行随帧平移（新帧不在此造行——行内容由
            # 行级生成小调用产出，纯重排不凭空编造调度）。
            if data.get("shot_table"):
                data["shot_table"] = remap_shot_table_after_insert(data["shot_table"], insert_at)
            _insert_at_cell.append(insert_at)

        await self._update_storyboard(task_dir, _updater)
        # S3：插帧行级调度行生成（±2 邻行 + 新帧旁白 + 锚定卡，仅 1 次 LLM）。
        # 无表/失败 → 新帧无行（该帧逐帧扩写零注入降级，不更坏；不阻断插帧）。
        if _insert_at_cell:
            await self._generate_inserted_shot_row(task_id, user_id, task_dir, _insert_at_cell[0])
        await self._invalidate_audio_artifacts(task_id, task_dir, user_id)

    async def _delete_frame(self, task_id: str, user_id: str, index: int) -> None:
        """M-UX: remove a frame + reindex 0..N continuous.

        T2（2026-09-01 拆帧计划）：越界抛 ValueError——修前静默 no-op 但端点返回
        200 假成功，用户操作与帧数状态有出入时无感知（vg_22ee06b39d19 同次
        编辑中 PUT 帧 85 返回过 400，坐实该场景真实存在）。端点转 400。
        """
        task_dir = Path(self._storage_dir) / user_id / "tasks" / task_id

        def _updater(data: dict) -> None:
            frames = data.get("frames", [])
            if not 0 <= index < len(frames):
                # 写盘前抛出：_update_storyboard 的 tmp+rename 原子写不会触碰
                # 原文件，锁随 async with 正常释放。
                raise ValueError(f"分镜 {index + 1} 不存在（当前共 {len(frames)} 镜）")
            del frames[index]
            for i, f in enumerate(frames):
                f["index"] = i
            data["frames"] = frames
            # P0：删帧同步分组序号——否则后续帧序号整体前移、分组指向错帧（渲染错内容）。
            if data.get("scene_groups"):
                data["scene_groups"] = remap_scene_groups_after_delete(data["scene_groups"], index)
            # S1（2026-08-26 调度表计划）：删帧同步调度表行（移除该行 + 后续行 -1）。
            if data.get("shot_table"):
                data["shot_table"] = remap_shot_table_after_delete(data["shot_table"], index)

        await self._update_storyboard(task_dir, _updater)
        await self._invalidate_audio_artifacts(task_id, task_dir, user_id)

    async def _generate_inserted_shot_row(self, task_id: str, user_id: str, task_dir: Path, insert_at: int) -> None:
        """S3（2026-08-26 调度表计划）：插帧行级调度行生成。

        ±2 邻行 + 新帧旁白 + 锚定卡 → 恰好 1 次 LLM（``generate_shot_row_for_frame``），
        产出行盖 ``frame_index=insert_at`` 后经原子写并回 shot_table。任何失败
        （无表/无旁白/LLM 坏输出/script_model 缺配）→ 新帧无行，走零注入降级
        （该帧镜头语言沿用逐帧返回值），绝不阻断插帧主流程。
        """
        try:
            from .orchestration import storyboard_planner as planner_mod

            sb_path = task_dir / "storyboard.json"
            if not sb_path.exists():
                return
            data = json.loads(sb_path.read_text(encoding="utf-8"))
            rows = data.get("shot_table") or []
            frames = data.get("frames") or []
            if not rows or not (0 <= insert_at < len(frames)):
                return
            narration = str(frames[insert_at].get("narration") or "").strip()
            if not narration:
                return
            prev_rows = [r for r in rows if isinstance(r, dict) and insert_at - 2 <= r.get("frame_index", -99) < insert_at]
            next_rows = [r for r in rows if isinstance(r, dict) and insert_at < r.get("frame_index", -99) <= insert_at + 2]
            task = await self.repo.get(task_id, user_id=user_id)
            payload = json.loads(task.payload) if isinstance(task.payload, str) else (task.payload or {}) if task is not None else {}
            llm = create_script_llm(payload, self._config)
            anchor_card = str((data.get("global_anchor") or {}).get("anchor_card", "") or "")
            row = await planner_mod.generate_shot_row_for_frame(
                llm,
                title=str(data.get("title") or ""),
                narration=narration,
                prev_rows=prev_rows,
                next_rows=next_rows,
                anchor_card=anchor_card,
            )
            if not row:
                return
            row["frame_index"] = insert_at

            def _row_updater(d2: dict) -> None:
                d2.setdefault("shot_table", []).append(row)
                d2["shot_table"].sort(key=lambda r: r.get("frame_index", 0))

            await self._update_storyboard(task_dir, _row_updater)
        except Exception:
            logger.warning("inserted-frame shot row generation failed for task %s (frame stays unscheduled)", task_id, exc_info=True)

    async def _invalidate_audio_artifacts(self, task_id: str, task_dir: Path, user_id: str) -> None:
        """帧增删后失效盘面拼接音频（audio.mp3/subtitle.srt）+ payload.audio_ready，
        强制下次 preview/render 重建。逐帧 audio_path 保留（GET /audio 单帧预览照常可用，
        下次 _synthesize_audio 可 CAS 复用、仅重 concat）。

        TTS 文件按内容哈希 md5(narration|voice|speed) 存储，删帧无"对应帧文件"可删，
        故只失效拼接产物，不碰逐帧 TTS。

        并发：在 _synth_lock_guard 内执行，与 _generate_draft_audio 互斥，
        防后台音频任务结束后覆写失效结果（draft 阶段增删与草稿音频高度重叠）。
        """
        async with _synth_lock_guard(str(task_dir)):
            # B2（2026-09-28）：subtitle.ass 与 srt 同族拼接产物——残留旧 .ass 会被
            # worker「ASS 优先」恒烧（R1 同根因），一并失效；逐帧 frame.srt 与 CAS
            # 音频刻意保留（下次合成复用）。
            for name in ("audio.mp3", "subtitle.srt", "subtitle.ass"):
                p = task_dir / name
                if p.exists():
                    try:
                        p.unlink()
                    except OSError:
                        logger.warning("failed to remove stale %s for task %s", name, task_id)
            task = await self.repo.get(task_id, user_id=user_id)
            if task is not None:
                pl = json.loads(task.payload) if isinstance(task.payload, str) else (task.payload or {})
                changed = False
                # 只清音频就绪态，不清 total_duration（清了 UI 显示 0 会误导；
                # 保留旧值 + audio_ready=False 标"待更新"更平滑）
                for k in ("audio_ready", "audio_error"):
                    if k in pl:
                        pl.pop(k)
                        changed = True
                if changed:
                    await self.repo.update(task_id, payload=json.dumps(pl, ensure_ascii=False))

    async def regenerate_all_audio(self, task_id: str, user_id: str) -> dict:
        """B4（R3，2026-09-28 edits 修复批）：批量重生全部配音（ready/failed 任务）。

        全帧重跑 TTS（复用 CAS——voice/speed 变化自然全帧 miss，未变帧零成本命中）
        → 重 concat audio.mp3 → 重写 SRT/ASS → 清 ``subtitle_locked`` 按新配音重切
        （决策 1：全帧时长已变，锁字幕时间轴必错位）→ ``pending_regen_frames`` 按
        成功帧清除（失败帧保留——recompose 继续 409 引导单帧重试，与失败不连坐
        语义自洽）→ payload ``audio_ready`` 置位（有失败帧不置，以 failed_frames
        汇总为准）。

        互斥/防重入由 router 层负责（``_active_tasks`` 预检 + ``_regen_inflight``
        全帧入口一次性登记 + ``audio_regen_running``）。本方法内部自进
        ``_synth_lock_guard``（与批量 TTS/单帧重生互斥写 audio.mp3）。
        remotion_course 跳过整片 SRT/ASS 重写与 segments 删除（v4 适用范围注记）：
        其成片不消费 audio.mp3/.ass，字幕走帧级 frame.srt → captions 链自洽。
        """
        task = await self.repo.get(task_id, user_id=user_id)
        if task is None:
            raise ValueError("任务不存在")
        if task.status not in ("ready", "failed"):
            raise ValueError("仅已完成或失败的任务可批量重生配音")
        task_dir = Path(self._storage_dir) / user_id / "tasks" / task_id
        sb_path = task_dir / "storyboard.json"
        if not sb_path.exists():
            raise ValueError("分镜数据缺失，无法重生配音")
        is_remotion = getattr(task, "task_type", "") == "remotion_course"

        from .orchestration import tts as tts_mod

        tts_mod.set_tts_degradation_context(task_dir)
        payload = json.loads(task.payload) if isinstance(task.payload, str) else (task.payload or {})
        _section_id = payload.get("course_project_id")

        failed_frames: list[int] = []
        succeeded: list[int] = []

        async with _synth_lock_guard(str(task_dir)):
            storyboard = Storyboard.from_dict(json.loads(sb_path.read_text(encoding="utf-8")))
            if not storyboard.frames:
                raise ValueError("无帧可重生配音")

            voice_id = storyboard.config.voice_id
            audio_dir = task_dir / "audio"
            audio_dir.mkdir(parents=True, exist_ok=True)
            ctx = _SynthFrameCtx(
                task_dir=task_dir,
                audio_dir=audio_dir,
                voice_id=voice_id,
                tts_speed=storyboard.config.tts_speed,
                fallback_voice=self._config.tts_fallback_voice,
                semaphore=asyncio.Semaphore(_tts_concurrency(voice_id or "")),
                cas_key_locks={},
                cas_results={},
                seconds_per_frame=None,  # 成片任务不重施 spf（与单帧重生口径一致）
                section_id=_section_id,
                tts_mod=tts_mod,
                policy=_vimax_tts_policy_for(task),
            )
            for frame in storyboard.frames:
                await self._set_regen_state(task_dir, frame.index, "running")
                try:
                    await _synth_frame_cas(ctx, frame)
                except Exception as e:
                    # 决策 2：单帧失败不连坐（不走 _synthesize_audio_locked 的
                    # FIRST_EXCEPTION 整批传播）——失败帧保留旧音兜底 + error 留痕，
                    # 其余帧照常完成。
                    logger.warning("regenerate-audio: frame %s TTS failed (task %s): %s", frame.index, task_id, e)
                    failed_frames.append(frame.index)
                    await self._set_regen_state(task_dir, frame.index, "error", error=f"{type(e).__name__}: {e}")
                else:
                    succeeded.append(frame.index)
                    await self._set_regen_state(task_dir, frame.index, "done")

            storyboard.recompute_duration()

            by_index = {f.index: f for f in storyboard.frames}

            def _writeback(data: dict) -> None:
                # 决策 1：清锁——SRT 将按新配音重切（弹层文案已明示覆盖手改文本）。
                data["subtitle_locked"] = False
                for f in data.get("frames", []):
                    new = by_index.get(f.get("index"))
                    if new is None or new.index in failed_frames:
                        continue  # 失败帧保留旧音/旧元数据兜底
                    f["audio_path"] = new.audio_path
                    f["duration"] = new.duration
                    f["srt"] = new.srt
                    f["speech_duration"] = new.speech_duration
                pend = data.get("pending_regen_frames")
                if isinstance(pend, list):
                    data["pending_regen_frames"] = [i for i in pend if i not in succeeded]

            await self._update_storyboard(task_dir, _writeback)
            # 与磁盘清锁对齐——_write_storyboard_srt 的 locked 守卫读内存对象
            # （_regenerate_frame 同款注释）。
            storyboard.subtitle_locked = False

            audio_concat_path = str(task_dir / "audio.mp3")
            await run_ffmpeg(_concat_audio, [f.audio_path for f in storyboard.frames], audio_concat_path, expected_seconds=sum(f.duration for f in storyboard.frames))

            # v5：重切 SRT 过 _precompute_llm_chunks（照抄渲染主路径 llm_chunks_map
            # 传参）——裸调会让 LLM 断句退化为规则链。
            if not is_remotion:
                llm_chunks = await _precompute_llm_chunks(storyboard, self._config, task_dir)
                _write_storyboard_srt(storyboard, str(task_dir / "subtitle.srt"), llm_chunks_map=llm_chunks)

        # payload 终态：无失败帧置 audio_ready；有失败帧汇总清单（不置 ready）
        pl = json.loads(task.payload) if isinstance(task.payload, str) else (task.payload or {})
        if failed_frames:
            pl.pop("audio_ready", None)
            pl["audio_regen_failed_frames"] = failed_frames
        else:
            pl["audio_ready"] = True
            pl.pop("audio_regen_failed_frames", None)
        await self.repo.update(task_id, payload=json.dumps(pl, ensure_ascii=False))

        # 决策 4：后续 recompose 必须全量——全帧时长已变，dirty 增量会音画错位；
        # 删 segments 缓存 + manifest + 清 dirty（照 reorder 端点先例）。
        if not is_remotion:
            import shutil

            shutil.rmtree(task_dir / "segments", ignore_errors=True)
            (task_dir / "manifest.json").unlink(missing_ok=True)
            await self.repo.clear_dirty_frames(task_id)
        return {"failed_frames": failed_frames, "regenerated": len(succeeded)}

    async def recover_audio_regen_flags(self) -> int:
        """B4：启动清理——后台批量随进程重启即死，把残留 ``audio_regen_running``
        清为失败态并附文案引导重新发起（app.py 启动钩子调用）。"""
        list_fn = getattr(self.repo, "list_audio_regen_running", None)
        if list_fn is None:
            return 0
        try:
            # review 收尾（2026-09-28）：默认域值与实际不符——VideoGenService.domain
            # = "video_gen"（类属性恒存在，此处默认值防御性对齐真实域，防未来重构
            # 删属性后静默扫不到残留）
            rows = await list_fn(getattr(self, "domain", "video_gen"))
        except Exception:
            logger.warning("scan audio_regen_running failed at startup", exc_info=True)
            return 0
        n = 0
        for t in rows:
            try:
                pl = json.loads(t.payload or "{}")
                if not pl.get("audio_regen_running"):
                    continue
                pl.pop("audio_regen_running", None)
                pl["audio_error"] = "服务重启中断，请重新发起批量重生配音"
                await self.repo.update(t.id, payload=json.dumps(pl, ensure_ascii=False))
                n += 1
            except Exception:
                logger.warning("clear stale audio_regen_running failed (task %s)", getattr(t, "id", "?"), exc_info=True)
        return n

    async def _cleanup_task(self, task: Any) -> None:
        """B2: do NOT delete the task_dir on cancel/stall/restart — preserve on-disk
        artifacts so a resumed render (B1) reuses them via ViMax idempotency
        (scene_N/final_video.mp4, shots/*/video.mp4, characters.json, camera_tree.json).
        The dir is removed only on explicit delete_task (below)."""
        pass  # base rmtree intentionally suppressed

    # ── D19 QC 重生状态机（2026-09-08 T12）─────────────────────────────
    # worker QC 发现异常 → qc_repair（不标 ready）；本 watcher 驱动：
    #   首次：异常场景清视觉 → 回 composing 走 Entry-1 重生（生成能力在 gateway）
    #   二次：异常场景降级占位（视觉置 none）+ qc_skip 重渲 → ready 交付 + degradations
    #   停滞：超阈值无进展 → error_messages 通道中文化告警（状态保持）
    _QC_REPAIR_STALL_WARN_ENV = "QC_REPAIR_STALL_WARN_SECONDS"

    def _qc_repair_stall_seconds(self) -> float:
        import os

        try:
            return float(os.environ.get(self._QC_REPAIR_STALL_WARN_ENV, "1800"))
        except ValueError:
            return 1800.0

    async def _process_qc_repair_tasks(self) -> int:
        """框架 reap 钩子的 video_gen 实现：扫描 qc_repair 行并推进状态机。"""
        rows = await self.repo.list_qc_repair(self.domain)
        n = 0
        for t in rows:
            try:
                await self._qc_repair_step(t)
                n += 1
            except Exception:
                logger.exception("qc_repair step failed for task %s (will retry next tick)", t.id)
        return n

    async def _qc_repair_step(self, t) -> None:  # noqa: ANN001 — TaskRow
        from datetime import UTC
        from datetime import datetime as _dt

        result = json.loads(t.result or "{}")
        qc_issues = result.get("qc_issues") or []
        retry = int(result.get("qc_retry_count") or 0)
        scenes = sorted({i["scene"] for i in qc_issues if isinstance(i, dict) and isinstance(i.get("scene"), int) and i["scene"] >= 0})
        task_dir = Path(self._storage_dir) / t.user_id / "tasks" / t.id

        # 停滞告警（30min 默认；env 可缩短供测试）：updated_at 过旧 → error_messages 透出，状态不动。
        # T-C1（R11③）：naive→aware 归一（task_framework:710 惯例）——修前 try/TypeError
        # 吞掉时差异常置 age=0.0，告警永不触发。
        updated = getattr(t, "updated_at", None)
        if updated is not None:
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=UTC)
            age = (_dt.now(UTC) - updated).total_seconds()
            if age > self._qc_repair_stall_seconds():
                logger.warning("qc_repair task %s stalled %.0fs (>%.0fs)", t.id, age, self._qc_repair_stall_seconds())
                # T-A2（R12）：告警写入同样走 CAS——用户已取消/已推进的任务不得被覆盖
                await self.repo.update_if_status(t.id, "qc_repair", error_message="质检修复长时间无进展，请联系管理员或取消任务")
                metrics.inc(
                    "task_gateway_qc_repair_total",
                    labels={"status": "stall_warned"},
                    help_text="qc_repair watcher transitions",
                )
                return

        payload = json.loads(t.payload or "{}")

        # T-C1（R11①）：storyboard 解析失败不再永卡（修前异常上抛被扫描器吞掉、
        # 每轮重试）——解析失败跳过视觉清理直接走降级交付（与二次异常同路）
        sb = None
        sb_parse_failed = False
        sb_path = task_dir / "storyboard.json"
        if sb_path.exists():
            try:
                sb = Storyboard.from_dict(json.loads(sb_path.read_text(encoding="utf-8")))
            except Exception:
                logger.exception("qc_repair task %s: storyboard.json parse failed — degraded delivery", t.id)
                sb = None
                sb_parse_failed = True

        if retry >= 1 or sb_parse_failed:
            # 二次异常/解析失败 → 降级占位交付（D19：修复失败兜底与二次异常同路）
            if sb is not None:
                for i in scenes:
                    if 0 <= i < len(sb.frames):
                        f = sb.frames[i]
                        f.visual_url = None
                        f.visual_type = None
                        f.local_visual_path = None
                sb_path.write_text(json.dumps(sb.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
            record_degradation(
                task_dir,
                kind="qc_repair_degraded",
                detail=(f"storyboard.json 解析失败（场景 {scenes}），已降级为占位画面交付" if sb_parse_failed else f"QC 重生后仍异常（场景 {scenes}），已降级为占位画面交付"),
            )
            payload["recompose_only"] = True
            payload["qc_skip"] = True  # worker 跳过 QC（降级占位是终态，不再循环）
            payload["auto_render"] = True
            # T-A2（R2/R12）：不再由 watcher 直接置 composing——修前落库瞬间 worker
            # （POLL=2s）即用**旧缺陷 props + qc_skip** 抢跑渲染并标 ready。改为
            # CAS 置 running（pipeline 驱动状态翻转，照抄 recompose_task 模式）：
            # 任务行保持非 composing 直到 _wrap_pipeline 的 Entry-1 重建
            # remotion_props 并以 merged payload 翻转 composing，worker 认领时
            # 拿到的必然是新 payload。CAS 失败=任务已被取消/并发推进，放弃。
            if not await self.repo.update_if_status(
                t.id,
                "qc_repair",
                status="running",
                progress=95,
                payload=json.dumps(payload, ensure_ascii=False),
                dirty_frames="[]",
                error_message=None,
            ):
                logger.info("qc_repair task %s: CAS lost before degraded delivery (skipped)", t.id)
                return
            self._register_active_task(t.id, lambda: asyncio.create_task(self._wrap_pipeline(t.id, t.user_id, t.task_type, payload)))
            logger.info("qc_repair task %s: degraded delivery (scenes=%s, parse_failed=%s)", t.id, scenes, sb_parse_failed)
            metrics.inc(
                "task_gateway_qc_repair_total",
                labels={"status": "degraded"},
                help_text="qc_repair watcher transitions",
            )
            return

        # 首次：计数 + 异常场景清视觉 → 回 composing 走 Entry-1（frames_to_regenerate
        # 见无视觉即重生；生成能力与凭证在 gateway，worker 只渲染）
        result["qc_retry_count"] = retry + 1
        if sb is not None:  # 缺 storyboard.json（手插任务等）沿旧语义：不清直接重生
            for i in scenes:
                if 0 <= i < len(sb.frames):
                    f = sb.frames[i]
                    f.visual_url = None
                    f.visual_type = None
                    f.local_visual_path = None
            sb_path.write_text(json.dumps(sb.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        payload.pop("recompose_only", None)
        payload["auto_render"] = True
        # T-A2（R2/R12）：同降级路径——CAS 置 running，composing 翻转交给 pipeline
        # （Entry-1 以清空视觉后的 storyboard 重建 props 再交棒 worker）。
        if not await self.repo.update_if_status(
            t.id,
            "qc_repair",
            status="running",
            progress=50,
            payload=json.dumps(payload, ensure_ascii=False),
            result=json.dumps(result, ensure_ascii=False),
            error_message="质检发现异常画面，正在重新生成",
        ):
            logger.info("qc_repair task %s: CAS lost before regeneration (skipped)", t.id)
            return
        self._register_active_task(t.id, lambda: asyncio.create_task(self._wrap_pipeline(t.id, t.user_id, t.task_type, payload)))
        logger.info("qc_repair task %s: regenerating scenes %s (attempt %d)", t.id, scenes, retry + 1)
        metrics.inc(
            "task_gateway_qc_repair_total",
            labels={"status": "regenerating"},
            help_text="qc_repair watcher transitions",
        )

    def _register_active_task(self, task_id: str, factory: Callable[[], asyncio.Task]) -> bool:
        """E2: 登记后台 pipeline task；若同 task_id 已有未完成 task 则拒绝（防并发写 task_dir + 孤儿 task）。

        factory: 无参 callable，返回新建的 asyncio.Task。仅在允许登记时才调用
        （拒绝时不调用 → 不会造出孤儿 task）。返回 True=已登记；False=已有在跑、拒绝。
        核心不变量：绝不覆盖一个仍在运行的 handle。
        """
        existing = self._active_tasks.get(task_id)
        if existing is not None and not existing.done():
            logger.warning("task %s already has a running pipeline; refusing concurrent trigger", task_id)
            return False
        self._active_tasks[task_id] = factory()
        return True

    async def recompose_task(self, task_id: str, user_id: str) -> dict:
        """B0: re-synthesize the video from current disk state (no re-fetch/re-TTS).

        Reads the on-disk storyboard.json + audio.mp3 + subtitle.srt + visuals/
        (all kept up-to-date by /regenerate and /upload-visual), builds a Worker
        payload, and dispatches via _wrap_pipeline with recompose_only=True.

        Only segments-type engines (ai_image/ai_video/ai_visual/remotion_course)
        are allowed — the caller (route) must enforce the whitelist.

        ValueError 契约（P1/BUG-008，2026-09-14，路由按 409 detail=str(e) 透传）：
        ① 本方法 raise 的 ValueError 文案即用户可见的 409 detail——raise 前必须确认
          文案用户向（中文），不含内部路径/堆栈/原始异常文本；
        ② 磁盘数据损坏类错误（storyboard.json 解析失败等）必须在此转成中文
          ValueError，不得让 JSONDecodeError 等原始异常泄漏进 detail；
        ③ 非 ValueError 异常天然走路由 500 generic——路由不得捕获转 409。
        """
        task = await self.repo.get(task_id, user_id=user_id)
        if task is None:
            raise ValueError("任务不存在或已被删除")
        if task.status not in ("ready", "failed"):
            raise ValueError(f"仅已完成或失败的任务可重新合成（当前状态：{task.status}）")

        # E13: recompose 复用盘面 audio.mp3/subtitle.srt；缺音频（TTS 阶段失败的 failed 任务）
        # 时给出明确错误，而非把不存在路径传给 worker 致其崩溃。
        task_dir = Path(self._storage_dir) / user_id / "tasks" / task_id
        if not (task_dir / "audio.mp3").exists():
            raise ValueError("缺音频（audio.mp3），无法重新合成，请先重新生成音频或整任务重生成")

        # M1 守卫：旁白已改但未重新生成的帧——recompose 不重 TTS（复用盘面 audio.mp3），
        # 直接合成会产出与编辑前完全相同的视频（白烧一次全量渲染）。显式 pending 态
        # （非 CAS 文件名推断——旧任务 CAS 键不含 voice+speed，推断法误判全部旧任务）。
        sb_path = task_dir / "storyboard.json"
        if sb_path.exists():
            try:
                _sb = json.loads(sb_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                # P1/BUG-008 v2-①（2026-09-14）：JSONDecodeError 是 ValueError 子类，
                # 不拦会把「磁盘数据损坏」伪装成 409 状态问题、并把原始解析错误文本
                # 泄漏进用户可见 detail。from None：不链原始异常（契约②）。
                raise ValueError("任务分镜数据损坏，无法重新合成，请重新解析或重建任务") from None
            _pending = [i for i in (_sb.get("pending_regen_frames") or []) if isinstance(i, int)]
            if _pending:
                raise ValueError(f"帧 {'、'.join(str(i + 1) for i in _pending)} 的旁白已修改但未重新生成配音——请先对这些帧点「重新生成」，再重新合成")

            # P1/BUG-008（2026-09-14）：视觉素材审计为 ai_image/ai_video 设计（每帧必须
            # 有画面素材），remotion_course 大部分帧**合法地无素材**（版式占位渲染，
            # 生图四条件见 visual_sources/remotion_course_visuals.py）→ 无差别执行会把
            # 全帧判 missing → ready 任务 recompose 100% 409。recompose 主路径对
            # remotion_course 只从盘面 storyboard 重建 props，本就不需要逐帧素材；
            # 缺素材风险由 worker 三扫描 QC（D19）兜底。
            # 同时放行 regen_state=error 帧：remotion 帧级配图重生失败不阻塞模板合成
            # （占位/缺图由 worker QC 兜底）——此决策勿恢复拦截（2026-09-14 拍板）。
            if task.task_type != "remotion_course":
                # 2026-07-29 线上实测：两天前的任务改台词重渲，provider 临时 URL
                # （TOS 签名 24h）已过期且无本地副本 → worker 每帧降级黑场
                # → 产出「没有画面只有字幕」的视频，状态仍 ready。此处拦在渲染之前。
                # 纯磁盘判断不够：过期 URL 的帧磁盘上「有 URL」，必须实际探测。
                from .visual_availability import (
                    audit_visual_availability,
                    format_unavailable_error,
                    frames_in_regen_error,
                    probe_remote_liveness,
                )

                # 传原始 frames dict（不走 Storyboard.from_dict——它要求 config 完整，
                # 磁盘上的历史 storyboard.json 可能缺字段，预检不该因此崩掉渲染入口）
                _frames_raw = _sb.get("frames") or []
                _audit = audit_visual_availability(_frames_raw, task_dir)
                _dead = await probe_remote_liveness(_frames_raw, task_dir)
                # D1 决策：regen_state=error 的帧即使旧 URL 仍可达也必须拦--
                # 界面已显示「失败」，静默用旧画面出片伤害信任。
                _regen_err = frames_in_regen_error(_frames_raw)
                _bad = sorted(set(_audit.missing) | set(_dead) | set(_regen_err))
                if _bad:
                    raise ValueError(
                        format_unavailable_error(
                            _bad,
                            _audit.total,
                            regen_failed=sorted(set(_regen_err) & set(_bad)),
                        )
                    )

        # review-①（2026-09-14）：payload 损坏同 storyboard 分流——`or "{}"` 只兜 None，
        # 损坏字符串仍会炸 JSONDecodeError（ValueError 子类）→ 路由透传泄漏原文。
        try:
            payload = json.loads(task.payload or "{}")
        except json.JSONDecodeError:
            raise ValueError("任务数据损坏，无法重新合成，请重建任务") from None
        # T-C1（R13）：用户手动重新合成必须重跑 QC——修前降级交付落库的 qc_skip
        # 持久化在 payload 里，手动重渲会**永久跳过质检**。
        payload.pop("qc_skip", None)
        payload["recompose_only"] = True
        payload["auto_render"] = True  # guard against draft branch

        # P1-3: 把 dirty_frames 传给 worker 做增量重渲（只重处理改动帧的 segment）。
        # dirty_frames 由 /regenerate 与 /upload-visual 标记；worker 成功后清空（worker.py R5）。
        # 注意：repo.get 返回裸 TaskRow——dirty_frames 是 Text 列 JSON 字符串（"[]"/"[0,3]"），
        # 不经过 TaskBrief 的 validator（schemas.py 只管 API 层）。必须自行解析——修前
        # list("[]") = ['[', ']'] 传入 pipeline → int('[') 崩（vg_f74c8fa2331a）。
        dirty_raw = task.dirty_frames or []
        if isinstance(dirty_raw, str):
            try:
                dirty_raw = json.loads(dirty_raw)
            except (TypeError, ValueError):
                dirty_raw = []
        dirty = [i for i in dirty_raw if isinstance(i, int)] if isinstance(dirty_raw, list) else []
        if dirty:
            payload["incremental"] = True
            payload["changed_frames"] = list(dirty)

        # R5: dirty_frames is NOT cleared here — if the pipeline fails/retries,
        # the user must still see 'N 处修改' to retry. Worker process_task
        # clears it on success (recompose_only payload only).
        if not self._register_active_task(task_id, lambda: asyncio.create_task(self._wrap_pipeline(task_id, user_id, task.task_type, payload))):
            raise ValueError("任务正在处理中，请等待当前渲染完成后再试")
        return {"task_id": task_id, "status": "composing"}

    # Task 20c: segments-type only — vimax/remotion 走各自重做端点（/vimax redo /
    # remotion 无逐镜重做），不支持重生成视觉。
    _RE_RENDER_ALLOWED_TYPES = frozenset({"ai_image", "ai_video", "ai_visual"})

    async def re_render_task(self, task_id: str, user_id: str) -> dict:
        """Task 20c: 项目已更新 → 重新生成（reuse_blueprint=True 复用分镜重跑视觉）。

        策略：改角色/风格仅影响之后新生成视频；已生成视频需手动「重新生成」。
        与 recompose_task 的关键区别——recompose 复用盘面视觉(recompose_only，
        不拾取新项目角色/风格)；re_render 复用 storyboard(旁白/分镜) 但**重新生图**，
        并 force 刷新项目 character_sheets 覆盖 payload 过期快照，让新角色/风格生效。

        ready/failed + segments-type only；frame_edits={} 触发 _render_from_draft
        视觉重生分支（reuse_blueprint 让它复用 on-disk storyboard.json，不重生成旁白）。
        """
        task = await self.repo.get(task_id, user_id=user_id)
        if task is None:
            raise ValueError(f"re-render: task {task_id} not found")
        if task.status not in ("ready", "failed"):
            raise ValueError(f"re-render: task must be ready or failed (got {task.status})")
        if task.task_type not in self._RE_RENDER_ALLOWED_TYPES:
            raise ValueError(f"re-render not supported for {task.task_type} (segments-type only)")

        # M1 守卫（同 recompose_task）：re_render 复用盘面音频，旁白未重生的帧会带出旧配音。
        _task_dir = Path(self._storage_dir) / user_id / "tasks" / task_id
        _sb_path = _task_dir / "storyboard.json"
        if _sb_path.exists():
            _sb = json.loads(_sb_path.read_text(encoding="utf-8"))
            _pending = [i for i in (_sb.get("pending_regen_frames") or []) if isinstance(i, int)]
            if _pending:
                raise ValueError(f"帧 {'、'.join(str(i + 1) for i in _pending)} 的旁白已修改但未重新生成配音——请先对这些帧点「重新生成」")
            # P2-6：re_render 走 _render_from_draft→TTS，同样拦截占位/空旁白（与 resume_draft 同源）
            _validate_narrations_renderable(_sb.get("frames", []))

            # 2026-07-29：与 recompose_task 对称的视觉素材预检。re_render 虽会重生视觉，
            # 但 _frames_to_regenerate 只重生「无 visual_url」或类型不符的帧——
            # 有 URL 且类型正确的帧（哪怕 URL 已死）、以及 local_visual_path 有值但
            # 文件已丢的帧（P0-1 上传守卫会 continue 跳过）都不会被重生，
            # 直接交给 worker 就是黑场。
            # 只拦「不会被重生 ∧ 取不到素材」的帧——本就要重生的缺失帧不能拦，
            # 否则会堵死 re_render 作为失败任务恢复路径的本职。
            # 只解析 frames（不走 Storyboard.from_dict——它要求 config 完整，
            # 磁盘上的历史 storyboard.json 可能缺字段，预检不该因此崩掉渲染入口）
            from .storyboard import StoryboardFrame
            from .visual_availability import (
                audit_visual_availability,
                format_unavailable_error,
                frames_in_regen_error,
                probe_remote_liveness,
            )

            _frames_raw = _sb.get("frames") or []
            _valid_keys = set(StoryboardFrame.__dataclass_fields__)
            _frame_objs = [StoryboardFrame(**{k: v for k, v in f.items() if k in _valid_keys}) for f in _frames_raw]
            _will_regen = set(_frames_to_regenerate(_frame_objs, task.task_type))
            _audit = audit_visual_availability(_frames_raw, _task_dir)
            _dead = await probe_remote_liveness(_frames_raw, _task_dir)
            # D1 决策：regen_state=error 的帧即使旧 URL 仍可达也必须拦。
            # 注意：frames_in_regen_error 必须对原始 dict _frames_raw 调用，
            # 不能对 _frame_objs--StoryboardFrame(**{...}) 会丢弃 regen_state 非字段键。
            # 合并顺序：先并后减--regen error 帧若本就满足重生条件（无 URL/类型不符），
            # _will_regen 会包含它，减集后放行让 re_render 修好它；不在 _will_regen 的才拦。
            _regen_err = frames_in_regen_error(_frames_raw)
            _bad = sorted((set(_audit.missing) | set(_dead) | set(_regen_err)) - _will_regen)
            if _bad:
                raise ValueError(
                    format_unavailable_error(
                        _bad,
                        _audit.total,
                        regen_failed=sorted((set(_regen_err) & set(_bad)) - _will_regen),
                    )
                )

        payload = json.loads(task.payload or "{}")
        # 强制重跑视觉：复用 storyboard 旁白/分镜 + 重新生图(带新项目 refs)，不走 recompose。
        payload["reuse_blueprint"] = True
        payload["auto_render"] = True
        payload["frame_edits"] = {}  # 触发 _render_from_draft（视觉重生）分支
        payload.pop("recompose_only", None)
        payload.pop("qc_skip", None)  # T-C1（R13）：手动重渲必须重跑 QC
        # C9（决策 14）：重渲染先重新仲裁并写回 payload——显式脱离（source=task）的
        # 任务 force 重注被 C3 门控跳过（不重注项目角色/锚图/道具，修全断破窗）；
        # 跟随项目的任务照旧 force 重注。音色/字幕不在全断口径内（决策 3 只断风格资产，
        # 决策 5 独立仲裁），重取语义不变。
        self._resolve_effective_style(payload, user_id)
        # Task 20c: force 刷新项目角色/风格——覆盖 payload 过期快照，让新改动生效。
        _inject_project_character_context(self._storage_dir, payload, user_id, force=True)

        # P2-15: re-render 重取项目级 voice/subtitle——快照冻结在草稿创建时刻，
        # 用户改项目设置后 re-render 静默用旧配置。仅未显式指定时才重取（显式选择优先）；
        # subtitle "default" 是 schema 未设值标记（CreateVideoRequest 默认，同
        # _resolve_subtitle_style 语义），同样视为未设置重取项目值。
        # （follow_project 哨兵 = 未显式——C7 三下拉「跟随项目」默认项）
        _had_explicit_voice = payload.get("voice_id") not in (None, _STYLE_SENTINEL)
        _had_explicit_subtitle = payload.get("subtitle_style") not in (None, "default", _STYLE_SENTINEL)
        if payload.get("course_project_id"):
            if payload.get("voice_id") in (None, _STYLE_SENTINEL):
                payload["voice_id"] = self._resolve_voice_id(None, payload.get("course_project_id"), user_id=user_id)
            if payload.get("subtitle_style") in (None, "default", _STYLE_SENTINEL):
                payload["subtitle_style"] = self._resolve_subtitle_style(None, payload.get("course_project_id"), user_id=user_id)
        # C9: 留痕刷新——effective_*_source 与当前仲裁一致（style 已在上方仲裁刷新；
        # 音色/字幕按「显式优先，重取记 project」标注）
        if _had_explicit_voice or payload.get("voice_id"):
            payload["effective_voice_source"] = "task" if _had_explicit_voice else "project"
        if _had_explicit_subtitle or (payload.get("subtitle_style") not in (None, "default")):
            payload["effective_subtitle_source"] = "task" if _had_explicit_subtitle else "project"
        else:
            payload["effective_subtitle_source"] = "default"

        # P2-15 生效链：_render_from_draft 的 TTS 与 build_worker_payload 读盘面
        # storyboard.json 的 config，不读 payload——只刷 payload 键对 re-render 无效，
        # 须把解析结果回写盘面 config（config 仅草稿创建时写入、无其他编辑入口，回写幂等；
        # 解析为 None/"default" 与创建时刻语义一致，不会引入漂移）。
        _new_voice = payload.get("voice_id")
        _new_subtitle = payload.get("subtitle_style")
        if _new_voice or _new_subtitle:

            def _settings_updater(data: dict) -> None:
                cfg = data.setdefault("config", {})
                if _new_voice:
                    cfg["voice_id"] = _new_voice
                if _new_subtitle:
                    cfg["subtitle_style"] = _new_subtitle

            await self._update_storyboard(_task_dir, _settings_updater)

        if not self._register_active_task(task_id, lambda: asyncio.create_task(self._wrap_pipeline(task_id, user_id, task.task_type, payload))):
            raise ValueError("任务正在处理中，请等待当前渲染完成后再试")
        return {"task_id": task_id, "status": "composing"}

    async def touch_task(self, task_id: str) -> None:
        """刷新 updated_at（P1-7）——帧编辑/上传/重排等只写磁盘 storyboard.json 的
        mutation 不调 repo，reaper 的 updated_at 谓词会把活跃编辑中的草稿误判 stalled。
        repo.update 空 kwargs 仅刷 updated_at（repository.py:67-71）。"""
        await self.repo.update(task_id)

    async def redo_vimax_shot(self, *, task_id: str, user_id: str, shot_id: int) -> dict:
        """T2（Lane 2, 2026-10-15）：vimax 逐镜重做接线。

        编排在 engine 侧（redo_vimax_shot_task：生成含参考降级链 → 替换镜视频 →
        重拼 → 重跑交付）；本方法负责 task 读取/角色参考解析/result 合并落库。
        修前三断点之三：结果不回写不重拼——烧钱返回成功但视频不变。"""
        # 终审 fix pass（Important 2）：同任务 redo 互斥。修前无人串行——两个并发 redo
        # 双 ffmpeg 写同名交付物（vimax_final*.mp4）、互清 final_video.mp4、result 全量
        # 覆写丢更新。锁粒度 = task_id；创建无 await 间隙（单事件循环原子）。
        async with _vimax_redo_lock(task_id):
            return await self._redo_vimax_shot_locked(task_id=task_id, user_id=user_id, shot_id=shot_id)

    async def _redo_vimax_shot_locked(self, *, task_id: str, user_id: str, shot_id: int) -> dict:
        from .engines.vimax.engine import VimaxShotNotFound, redo_vimax_shot_task

        task = await self.get_task(task_id, user_id)
        if task is None:
            raise ValueError(f"redo: task {task_id} not found")
        result = json.loads(task.result or "{}")
        shots_raw = result.get("vimax_shots") or {}
        if not shots_raw or str(shot_id) not in shots_raw:
            raise VimaxShotNotFound(shot_id)

        payload = json.loads(task.payload) if isinstance(task.payload, str) else (task.payload or {})
        character_front = None
        pid = payload.get("course_project_id")
        if pid:
            sheets = _read_course_project_character_sheets(self._storage_dir, pid, user_id)
            if sheets:
                character_front = sheets[0].front_url

        task_dir = Path(self._storage_dir) / user_id / "tasks" / task_id
        subtitle_style = payload.get("subtitle_style") or "default"
        out = await redo_vimax_shot_task(
            task_dir=task_dir,
            working_dir=task_dir / "vimax_working",
            shots_raw=shots_raw,
            shot_id=shot_id,
            character_front=character_front,
            provider=self._providers.ai_video,
            ratio=payload.get("aspect"),
            burn_subtitles=should_burn_subtitles(global_burn=getattr(self._config, "vimax_burn_subtitles", True), subtitle_style=subtitle_style),
            subtitle_style=subtitle_style,
        )

        # result 合并：镜时长按新视频回写 + redo 留痕（决策①：降级不摘除、可见）
        entry = result["vimax_shots"][str(shot_id)]
        entry["duration"] = out["new_duration"]
        entry["redo"] = {"at": out["at"], "reference_mode": out["reference_mode"], "note": out["note"]}
        if out.get("duration_drift") is not None:
            # 终审 fix pass：时长漂移留痕（对齐/降级明细在 degradations sidecar）
            entry["redo"]["duration_drift"] = out["duration_drift"]
        result["output_path"] = out["output_path"]
        result["file_size"] = out["file_size"]
        await self.repo.update(task_id, result=json.dumps(result, ensure_ascii=False))
        return {"regenerated_shot_id": shot_id, "reference_mode": out["reference_mode"]}

    async def get_task_with_queue_position(self, task_id: str, user_id: str):
        """T13：详情查询 + composing 未认领任务的队位附加（排队体验）。

        队位 = 本域全部 composing 且 claimed_by IS NULL 的行按认领顺序
        （priority DESC, created_at ASC）中的序号（1 基）。已认领/其他状态不附。
        """
        task = await self.repo.get(task_id, user_id)
        if task is None:
            return None
        if task.status == "composing" and not getattr(task, "claimed_by", None):
            try:
                peers = await self.repo.list_composing(self.domain)
                unclaimed = [t for t in peers if not getattr(t, "claimed_by", None)]
                unclaimed.sort(key=lambda t: (-getattr(t, "priority", 0), getattr(t, "created_at", None) or ""))
                for pos, t in enumerate(unclaimed, 1):
                    if t.id == task_id:
                        task.queue_position = pos
                        break
            except Exception:
                logger.warning("queue position computation failed for %s", task_id, exc_info=True)
        return task

    async def delete_task(self, task_id: str, user_id: str) -> bool:
        """Explicit delete: cancel + remove the task_dir (user wants it gone).
        Base delete_task cancels + repo.delete (cleanup is a no-op above); this
        override additionally rmtree's the preserved artifacts."""
        import shutil

        deleted = await super().delete_task(task_id, user_id)
        if deleted:
            task_dir = Path(self._storage_dir) / user_id / "tasks" / task_id
            if task_dir.exists():
                shutil.rmtree(task_dir, ignore_errors=True)
        return deleted

    def _should_handoff_to_worker(self, task_type: str) -> bool:
        # remotion_course（2026-09-08 T4）：交棒 remotion-worker 独立池（task_type 列
        # 即路由键，认领谓词见 worker.py _pool_task_type_predicate）。
        return task_type in ("ai_image", "ai_video", "ai_visual", "remotion_course")  # legacy compat

    def _is_engine_routable(self, task_type: str) -> bool:
        """Whether a task_type is served by the multi_engine engine layer (not legacy
        visual_sources). vimax is the engine that ONLY exists in multi_engine. remotion
        was removed (2026-07-15 split) — it renders via chat direct-write now, so it is
        NOT engine-routable (and /api/video/create rejects video_type=remotion with 422).
        Legacy visual_source types (ai_image/ai_video/comic/...) are NOT engine-routable
        and stay on _render_from_draft. Mirrors the engine registry's known engine set."""
        return task_type == "vimax"

    def _make_on_frame_done(self, storyboard: Storyboard, task_dir: Path, *, progress=None, total: int | None = None, base: int = 75, span: int = 15) -> Callable[[int], Awaitable[None]]:
        """Create an on_frame_done callback that incrementally persists visual_url to storyboard.json.

        Uses _update_storyboard (read-merge-write) to safely update only the target
        frame's visual_url/visual_type without overwriting concurrent writes.
        E5a: 每帧调 progress(base + int(span*done/total))（75→90 渐进）+ 刷 updated_at，
        不让 progress 冻在 75 致 stall reaper 双谓词命中误杀（75% 卡死根因）。progress=None 时不调。
        """
        done = [0]
        eff_total = total or len(storyboard.frames) or 1

        async def _on_frame_done(frame_index: int) -> None:
            frame = next((fr for fr in storyboard.frames if fr.index == frame_index), None)
            # P0-A1: guard 放宽——仅 local（无 url）的帧也要落盘（ai_video 占位/本地化）；
            # updater 增写 local_visual_path：重生清掉的旧上传靠这里落 None，
            # ai_video/ai_image 本地化的新路径靠这里落相对路径。
            if frame is None or (not frame.visual_url and not frame.local_visual_path):
                return

            def _updater(data: dict) -> None:
                for f in data.get("frames", []):
                    if f.get("index") == frame_index:
                        f["visual_url"] = frame.visual_url
                        f["visual_type"] = frame.visual_type
                        f["local_visual_path"] = frame.local_visual_path
                        break

            await self._update_storyboard(task_dir, _updater)
            # E5a: 每帧推进 progress（75→90）+ 刷 updated_at → stall reaper 两谓词都不触发
            done[0] += 1
            if progress is not None:
                try:
                    await progress(progress=base + int(span * done[0] / eff_total))
                except Exception:
                    logger.debug("per-frame progress update failed", exc_info=True)

        return _on_frame_done

    async def _run_pipeline(
        self,
        task_id: str,
        user_id: str,
        task_type: str,
        payload: dict,
        task_dir: Path,
        progress: ProgressUpdater,
    ) -> PipelineDraft | PipelineCompleted | PipelineGatewayDone | dict | None:
        start = time.monotonic()
        labels = {"type": task_type}
        try:
            # B0: recompose-only path — skip TTS/visual, reuse disk assets, just
            # build worker payload for ffmpeg re-synthesis. Placed BEFORE all other
            # branches (frame_edits, auto_render, engine) so it takes priority.
            if payload.get("recompose_only"):
                storyboard = Storyboard.from_dict(json.loads((task_dir / "storyboard.json").read_text(encoding="utf-8")))
                # remotion_course（T12）：qc_repair 重渲/降级交付共用 recompose 入口——
                # 从盘面 storyboard 重建 props（视觉已在 watcher 侧清理/降级），
                # 不走 ffmpeg 的 build_worker_payload。
                if task_type == "remotion_course":
                    if _reconcile_durations_with_audio(storyboard, task_dir):
                        logger.warning("recompose(remotion): frame durations drifted from audio — reconciled")
                    from .pipelines.remotion_course_props import build_remotion_props, build_remotion_worker_payload

                    props = build_remotion_props(
                        storyboard,
                        task_dir=task_dir,
                        user_id=user_id,
                        course_project_id=payload.get("course_project_id"),
                        task_payload=payload,
                        storage_dir=getattr(self, "_storage_dir", None),
                    )
                    await progress(status="running", progress=50)
                    return PipelineCompleted(payload=build_remotion_worker_payload(storyboard, props))
                bgm_path = None
                bgm_file = task_dir / "bgm.mp3"
                if bgm_file.exists():
                    bgm_path = str(bgm_file)
                    storyboard.config.bgm_path = bgm_path
                # recompose 复用盘面，时长可能陈旧（跨代/竞态/旧快照）——出 payload 前按实测纠偏，
                # 否则视频按旧时长、音频按新时长 → 音画漂移 + tpad 末帧定格（vg_93e83a7ee259）
                if _reconcile_durations_with_audio(storyboard, task_dir):
                    logger.warning("recompose: frame durations drifted from audio files — reconciled")
                    _write_storyboard_srt(storyboard, str(task_dir / "subtitle.srt"))
                    await self._merge_audio_into_storyboard(storyboard, task_dir)
                else:
                    # B1（R1a，2026-09-28）：无漂移也要刷新 ASS——worker「ASS 优先」恒烧
                    # 旧 .ass，修前无漂移分支零字幕产物动作，改样式/改文本永不生效。
                    _refresh_ass_from_existing_srt(storyboard, task_dir)
                worker_payload = build_worker_payload(
                    storyboard,
                    subtitle_path=str(task_dir / "subtitle.srt"),
                    subtitle_ass=str(task_dir / "subtitle.ass"),
                    audio_concat_path=str(task_dir / "audio.mp3"),
                    output_format=payload.get("output_format", "mp4"),
                    quality=payload.get("quality", "standard"),
                )
                # P1-3: 透传增量标记（build_worker_payload 不携带自定义键）
                if payload.get("incremental") and payload.get("changed_frames"):
                    worker_payload["incremental"] = True
                    worker_payload["changed_frames"] = [int(i) for i in payload["changed_frames"]]
                await progress(status="running", progress=50)
                metrics.inc("video_gen_task_total", labels={**labels, "status": "completed"}, help_text="Total video gen tasks by type and status")
                return PipelineCompleted(payload=worker_payload)

            # Task 12 Step 4: legacy 路径（ai_image/ai_video）render 前重读项目三视图注入
            # payload，供 _prepare_storyboard_video/_render_from_draft 作 ai_image reference_image_paths。
            # recompose_only 上面已早退（复用盘面不重生成视觉），跳过。force=False：payload
            # 既有 sheets 时不覆盖（避免每次 render 都重读项目）；Task 20c 重生成走 force=True。
            # C3: 仲裁必须先于注入——写回 payload["effective_style_source"] 后，
            # _inject_project_character_context 据此门控（显式脱离=全断）。幂等：重渲染
            # 入口（re_render_task，C9）已仲裁过时此处重算结果一致。
            self._resolve_effective_style(payload, user_id)
            _inject_project_character_context(self._storage_dir, payload, user_id)

            # Entry 3: resume from draft (POST /render)
            if payload.get("frame_edits") is not None and payload.get("auto_render"):
                # multi_engine engines (vimax, and any future engine-routable type) must
                # resume through the engine path (VimaxEngine), NOT legacy _render_from_draft
                # which hardcodes _make_visual_source and raises
                # "visual source for vimax not implemented" (3.2 end-to-end regression).
                # legacy visual_source types keep the _render_from_draft path.
                if self._config.engine_arch == "multi_engine" and self._is_engine_routable(task_type):
                    result = await self._run_pipeline_engine(task_id, user_id, task_type, payload, task_dir, progress)
                else:
                    result = await self._render_from_draft(task_id, task_type, task_dir, progress, payload, user_id=user_id)
                    if task_type == "remotion":
                        # 存量兼容路径（2026-07-22 评审确认保留）：仅 2026-07-15 前创建的
                        # remotion 草稿经 HTTP POST /tasks/{id}/render → resume_draft 可达；
                        # agent 工具面已摘牌（run_pipeline_for_tool 入口守卫）+ 新建已 422。
                        result = await self._render_remotion(task_id, result, task_dir, progress, payload=payload, user_id=user_id)
                metrics.inc("video_gen_task_total", labels={**labels, "status": "completed"}, help_text="Total video gen tasks by type and status")
                return result

            auto_render = payload.get("auto_render", True)

            if task_type in ("ai_image", "ai_video", "remotion", "ai_visual", "vimax", "remotion_course"):
                if not auto_render:
                    # P0-3: 幂等恢复——gateway 重启会把 DRAFT/STORY_READY 任务重新调度进来
                    # （task_framework GATEWAY_RECOVERABLE 含 DRAFT）。磁盘已有产物说明
                    # draft 已生成过（可能含用户逐帧编辑/重排/上传），绝不用 LLM 重生成覆盖。
                    sb_path = task_dir / "storyboard.json"
                    story_path = task_dir / "story.txt"
                    if task_type == "vimax" and payload.get("pipeline") == "idea2video" and story_path.exists():
                        logger.info("draft recovery: reusing on-disk story.txt for task %s", task_id)
                        result = PipelineDraft(story=story_path.read_text(encoding="utf-8"))
                    elif sb_path.exists():
                        logger.info("draft recovery: reusing on-disk storyboard.json for task %s", task_id)
                        storyboard = Storyboard.from_dict(json.loads(sb_path.read_text(encoding="utf-8")))
                        result = PipelineDraft(storyboard=storyboard.to_dict())
                    else:
                        # Entry 2: draft only — immediately mark as draft so UI shows 草稿
                        await progress(status=DRAFT, progress=5)
                        result = await self._generate_draft(task_type, payload, task_dir, progress, user_id=user_id, task_id=task_id)
                    # idea2video: pause at STORY_READY with the expanded story for user
                    # confirmation (no storyboard frames yet — skip TTS). Otherwise normal
                    # draft: trigger background TTS audio generation (non-blocking).
                    if isinstance(result, PipelineDraft) and result.story is not None:
                        # json.dumps the story — ProgressUpdater.__call__ passes kwargs
                        # straight to repo.update, and SQLite rejects a dict for the result
                        # column ('type dict is not supported'). Mirror the storyboard
                        # path (line ~317). Use set_result if you need merge semantics.
                        await progress(status=STORY_READY, progress=50, result=json.dumps({"story": result.story}, ensure_ascii=False))
                    else:
                        asyncio.create_task(self._generate_draft_audio(task_id, task_dir, user_id=user_id))
                    return result
                # Entry 1: full pipeline (default)
                # multi_engine engines (remotion/vimax) route through the engine layer;
                # legacy visual_source types (ai_image/ai_video/ai_visual) stay on
                # _prepare_storyboard_video (Worker handoff). The former stock_clip engine
                # was the "generic segments engine" that handled ai_image/ai_video in
                # multi_engine mode — removed with Pexels, so only true engine-routable
                # types belong on the engine path now. Mirrors Entry 3's guard at :451.
                if self._config.engine_arch == "multi_engine" and self._is_engine_routable(task_type):
                    result = await self._run_pipeline_engine(task_id, user_id, task_type, payload, task_dir, progress)
                else:
                    result = await self._prepare_storyboard_video(task_type, payload, task_dir, progress, user_id=user_id, task_id=task_id)
                    if task_type == "remotion":
                        # 存量兼容路径：同 Entry 3 注释（仅存量 remotion 任务经 HTTP 可达）。
                        result = await self._render_remotion(task_id, result, task_dir, progress, payload=payload, user_id=user_id)
            elif task_type == "remotion_legacy":
                result = await self._run_remotion(task_id, payload, task_dir, progress)
            else:
                raise ValueError(f"Unknown task_type: {task_type}")

            metrics.inc("video_gen_task_total", labels={**labels, "status": "completed"}, help_text="Total video gen tasks by type and status")
            return result
        except Exception:
            metrics.inc("video_gen_task_total", labels={**labels, "status": "failed"}, help_text="Total video gen tasks by type and status")
            raise
        finally:
            elapsed = time.monotonic() - start
            if task_type not in ("remotion", "remotion_legacy"):
                metrics.observe("video_gen_task_duration_seconds", elapsed, labels={**labels, "phase": "prepare"}, help_text="Video gen task duration in seconds")

    async def _build_course_storyboard(
        self,
        script_text: str,
        payload: dict,
        task_dir: Path,
        *,
        user_id: str = "default",
        on_progress=None,
        image_paths: dict[str, str] | None = None,
    ) -> tuple[list, list]:
        """remotion_course 专属：脚本 → (frames, scene_groups)（P1 直渲 / P2 草稿 /
        reparse 端点三处共用，2026-09-10 独立管线计划）。

        含 staging 认领（``script_extract_id``，决策 7）→ 双档解析
        （``parse_course_script``，校验失败内部重试一次）→ 容量契约拆分（overflow
        warn 留痕）→ 元帧门槛（决策 18）→ 帧构建（layout/layout_content/嵌入图/
        visual_source 一次带齐）。解析失败/校验不过 → ``units_from_paragraphs``
        兜底（degradation 留痕 ``course_parse_fidelity_fallback``）；
        ``ai_normalize_script=False`` = API 层纯切分档（零 AI，刻意行为不留痕）。
        """
        from .degradations import record_degradation
        from .orchestration import course_script as course_mod

        mode = payload.get("course_parse_mode") or "faithful"

        # staging 认领（决策 7）：extract 暂存图搬进 task_dir/visuals/embedded/。
        # 仅 create 路径（image_paths=None）认领；reparse 直接传 task_dir/visuals/
        # embedded/ 重建的映射（认领后重解析图片不丢，决策 15）。payload 里的
        # script_extract_id 在 create 认领后是残留（staging 目录已删）——无条件重认领
        # 会把重建映射覆盖成 {} 并误写 embedded_images_expired 留痕（2026-09-11 审查 B1）。
        extract_id = payload.get("script_extract_id")
        if image_paths is None:
            image_paths = {}
            if extract_id:
                try:
                    from .orchestration.course_staging import KIND_EMBEDDED_IMAGES_CLAIM_FAILED, claim_script_images

                    image_paths, _embedded_expired = claim_script_images(
                        extract_id,
                        task_dir,
                        storage_dir=getattr(self, "_storage_dir", None),
                        user_id=user_id,
                    )
                    if _embedded_expired:
                        # 过期可见（决策 B）：不静默丢图——详情页/编辑器经 degradations/
                        # payload 标记提示"暂存配图已过期，可手动上传"，任务正常继续。
                        payload["embedded_images_expired"] = True
                except Exception:
                    logger.warning("claim_script_images failed (extract_id=%s) — continuing without embedded images", extract_id, exc_info=True)
                    # 条目 13（2026-09-12 缺陷批）：修前只写 warning log——image_paths={}
                    # → parser 的 image_ref 全部 miss → 嵌入图整批静默丢失、任务照常
                    # 推进。补 degradation 留痕（磁盘/权限异常与「过期」语义不同，
                    # 单列 kind）+ payload 标记（键沿用 embedded_images_expired 减少前端改动）。
                    record_degradation(
                        task_dir,
                        kind=KIND_EMBEDDED_IMAGES_CLAIM_FAILED,
                        detail="配图认领失败（磁盘/权限异常），未自动分配，可在分镜编辑器手动上传",
                    )
                    payload["embedded_images_expired"] = True

        attempted = payload.get("ai_normalize_script", True) and len(script_text) > 80
        units = None
        if attempted:
            llm = create_script_llm(payload, self._config)
            units = await course_mod.parse_course_script(
                llm,
                script_text,
                mode=mode,
                target_units=payload.get("n_scenes"),  # 仅用户显式 n_scenes 才作为软提示
                on_progress=on_progress,
            )
        if units is None:
            units = course_mod.units_from_paragraphs(script_text)
            if attempted:
                record_degradation(
                    task_dir,
                    kind=course_mod.KIND_COURSE_PARSE_FALLBACK,
                    detail=f"course parse（{mode}）失败或校验未通过，已回落确定性段落切分",
                )
        # 一单元多图顺延（§3.6）：首图进槽，其余顺延到后续无图单元（帧记
        # image_note"来自邻近单元"，编辑器素材区标注）；无处可放 → 留痕可见。
        # 编号 1-based（编辑器/UI 口径）+ narration 前 8 字定位（m17，二轮审查：
        # 拆分后位置会位移，snippet 才找得到）。
        units, image_deferrals = course_mod.defer_extra_images(units)

        def _u(pos: int) -> str:
            u = units[pos] if 0 <= pos < len(units) else None
            snip = (u.narration or "")[:8] if u else ""
            return f"单元 {pos + 1}（「{snip}…」）" if snip else f"单元 {pos + 1}"

        for dfr in image_deferrals:
            if dfr.get("to_pos") is None:
                record_degradation(
                    task_dir,
                    kind=course_mod.KIND_EMBEDDED_IMAGE_DEFERRED,
                    detail=f"{_u(dfr.get('from_pos', 0))} 的内嵌图 {dfr.get('ref')} 之后无无图单元可顺延，已丢弃（可在编辑器手动上传）",
                )
            else:
                record_degradation(
                    task_dir,
                    kind=course_mod.KIND_EMBEDDED_IMAGE_DEFERRED,
                    detail=f"{_u(dfr.get('from_pos', 0))} 的内嵌图 {dfr.get('ref')} 顺延到{_u(dfr.get('to_pos', 0))}（一单元多图）",
                )
        units, overflow = course_mod.split_overflow_units(units)
        for ov in overflow:
            if ov.get("issue") == "bullets_overflow_unsplit":
                detail = f"单元 {ov.get('frame_unit_pos', 0) + 1} 旁白句数不足以拆满容量，末单元保留 {ov.get('bullets')} 条 bullets（> {course_mod.BULLETS_MAX_ITEMS}），画面可能溢出"
            else:
                detail = f"单元 {ov.get('frame_unit_pos', 0) + 1} title「{ov.get('title') or ''}」超长（{len(ov.get('title') or '')} 字 > {course_mod.TITLE_MAX_CHARS}），画面字段可能溢出版式"
            record_degradation(task_dir, kind=course_mod.KIND_LAYOUT_OVERFLOW_RISK, detail=detail)
        units = course_mod.gate_meta_frames(units)
        return course_mod.units_to_frames(units, image_paths=image_paths)

    async def _prepare_storyboard_and_audio(
        self,
        task_type: str,
        payload: dict,
        task_dir: Path,
        progress: ProgressUpdater,
        *,
        task_id: str | None = None,
        skip_tts: bool = False,
        user_id: str = "default",
    ) -> tuple[Storyboard, str, str]:
        """Shared prep (阶段 1.5 extract): normalize → narrations → storyboard plan → TTS → BGM.

        Phase 3.4: ``skip_tts=True`` skips the (wasted) TrainerAI TTS for vimax —
        Phase 3's narration_generator produces per-shot narration that matches the
        ViMax video length. storyboard + BGM still generated (EngineContext needs voice_id).

        Returns (storyboard, audio_concat_path, subtitle_path). Visual generation
        + Worker handoff are the caller's job (legacy _prepare_storyboard_video
        does visual_source+handoff; the multi_engine path does engine.render).
        """

        from .orchestration import script as script_mod
        from .orchestration import storyboard_planner as planner_mod
        from .storyboard import StoryboardConfig, StoryboardFrame

        await progress(status="running", progress=5)

        # Normalize legacy task types
        effective_type = task_type
        if task_type == "ai_visual":
            effective_type = "ai_image"

        mode = payload.get("mode", "fixed")
        aspect = payload.get("aspect") or self._config.default_aspect
        width, height = _aspect_to_size(aspect)
        # B2: payload 显式 > 项目 voice_id > lang 默认 > config（修前项目级音色静默失效）
        # Plan B（2026-10-19）：vimax 门/缺省解析提取为 _resolve_voice_with_vimax_gate
        # （与 _generate_draft 共用——评审修复：修前只有本函数接了 vimax 分支）。
        voice_id, _voice_source = self._resolve_voice_with_vimax_gate(task_type, payload, user_id=user_id)
        if _voice_source:
            payload["effective_voice_source"] = _voice_source

        llm = create_script_llm(payload, self._config)  # E6: 显式化，不回落 models[0]
        # T-E11（D6）：语速三级链（任务显式 > 项目 > config 缺省；与 voice_id 同构）
        resolved_tts_speed = self._resolve_tts_speed(payload.get("tts_speed"), payload.get("course_project_id"), user_id=user_id) or self._config.tts_speed

        # W2（2026-08-24 活性重构）：分镜/脚本 LLM 阶段挂滴答（叙事生成 + 分镜计划
        # 循环整体一个 tick task）。预算（W4）：轮数 = 2（脚本归一/解析相）+
        # ceil(期望帧数/prompt 并发)，每轮 llm_floor（≈8min 覆盖单调用 120s×3+退避）。
        _n_expected = _target_scenes_for_fixed(payload, payload.get("script", "")) if payload.get("script") else _resolve_n_scenes(payload)
        _llm_budget = self._stage_budget_llm(_n_expected or 0, concurrency=self._config.prompt_frame_concurrency)
        async with self._liveness(task_id, stage="storyboard_llm", stage_budget=_llm_budget):
            # 1. Narrations
            # remotion_course（2026-09-10 独立管线计划 P1）：脚本 → 结构化单元一次到位
            # （layout/layout_content/嵌入图/visual_source），不走 AI 管线的细切+plan 链。
            course_frames = None
            course_groups: list = []
            if mode == "generate" and payload.get("topic"):
                narrations = await script_mod.generate_narrations(
                    llm,
                    topic=payload["topic"],
                    n_scenes=_resolve_n_scenes(payload),
                    target_duration=payload.get("target_duration"),
                    style=payload.get("style"),
                    tts_speed=resolved_tts_speed,
                )
                title = payload["topic"]
            else:
                script_text = payload.get("script", "")
                if not script_text:
                    raise ValueError("No script or topic provided")
                if effective_type == "remotion_course":
                    # C1 同款：逐块刷 progress（5→20 区间），防 reaper 误杀。
                    async def _on_course_parse(done: int, total: int) -> None:
                        if total > 0:
                            await progress(progress=5 + int(15 * done / total))

                    course_frames, course_groups = await self._build_course_storyboard(
                        script_text,
                        payload,
                        task_dir,
                        user_id=user_id,
                        on_progress=_on_course_parse,
                    )
                    title = script_text[:30]
                else:
                    _target_scenes = _target_scenes_for_fixed(payload, script_text)  # M3-1 + P1-E11: 显式 n_scenes 优先，其次 spf+target，兜底文长
                    # S4: 结构化解析优先（识别台词/画面/噪声），失败→normalize+split 兜底
                    narrations = None
                    if payload.get("ai_normalize_script", True) and len(script_text) > 80:
                        try:
                            # C1: 逐块刷 progress（5→20 区间），让长文档解析阶段持续更新 updated_at，
                            # 避免 reaper Predicate A（updated_at 过期）误杀。
                            async def _on_parse(done: int, total: int) -> None:
                                if total > 0:
                                    await progress(progress=5 + int(15 * done / total))

                            _parsed = await script_mod.parse_script_to_frames(llm, script_text, target_scenes=_target_scenes, on_progress=_on_parse)
                            if _parsed:
                                narrations = [f["narration"] for f in _parsed if f.get("narration")]
                        except Exception:
                            logger.warning("parse_script_to_frames failed", exc_info=True)
                    if not narrations:
                        if payload.get("ai_normalize_script", True) and len(script_text) > 80:
                            try:
                                script_text, _truncated = await script_mod.normalize_script(llm, script_text, style=payload.get("style", "口语"), target_scenes=_target_scenes)
                                if _truncated:
                                    payload["script_truncated"] = True
                            except Exception:
                                logger.warning("normalize_script failed, falling back to raw split", exc_info=True)
                        narrations = script_mod.split_fixed_script(script_text, target_count=_target_scenes)
                    title = script_text[:30]
            if course_frames is None and not narrations:
                raise ValueError("No narrations produced")
            await progress(progress=20)

            # 2. Storyboard plan
            # C3 留痕（决策 5）：字幕生效来源 task > project > default
            resolved_subtitle_style = self._resolve_subtitle_style(payload.get("subtitle_style", "default"), payload.get("course_project_id"), user_id=user_id)
            _payload_subtitle = payload.get("subtitle_style")
            if _payload_subtitle and _payload_subtitle != "default" and _payload_subtitle != _STYLE_SENTINEL:
                payload["effective_subtitle_source"] = "task"
            elif resolved_subtitle_style != "default":
                payload["effective_subtitle_source"] = "project"
            else:
                payload["effective_subtitle_source"] = "default"
            cfg = StoryboardConfig(
                video_type=effective_type,
                width=width,
                height=height,
                voice_id=voice_id,
                tts_speed=resolved_tts_speed,
                style=payload.get("style", "default"),
                subtitle_style=resolved_subtitle_style,
                transition=payload.get("transition", "none"),
                transition_duration=payload.get("transition_duration", 0.5),
                bgm_volume=payload.get("bgm_volume", 0.15),
                bgm_ducking=payload.get("bgm_ducking", True),
                # P1-E8: 项目调色板接线（无项目 → None，worker 跳过 colorgrade）
                color_palette=_read_course_project_color_palette(self._storage_dir, payload.get("course_project_id"), user_id),
                # T3（2026-09-01）：任务级图文镜占比上限透传（None=默认 30%）。
                graphic_ratio=payload.get("graphic_ratio"),
            )
            # C3: 统一仲裁（决策 3/5/20）——三处仲裁点共用 _resolve_effective_style。
            # 入口（_run_pipeline / 重渲染）已仲裁并写回 payload["effective_style_source"]；
            # 此处重算（幂等）取 style_context：跟随项目=项目前缀；显式=脱离（payload
            # 前缀，项目资产零注入）；无项目=现状。哨兵已在仲裁时归一，不流入 cfg.style。
            effective_style = self._resolve_effective_style(payload, user_id=user_id)
            # P3-2/BUG-002（2026-09-14）：脚本标题（进 CoverPage 顶栏）剥标记——AI 解析路径
            # 的 title 可能携带【图片N】（topic 截断/script_text[:30] 均可能），复用 course_script。
            from .orchestration.course_script import _strip_structural_markers as _cs_strip

            storyboard = Storyboard(title=_cs_strip(title), config=cfg)
            if course_frames is not None:
                # remotion_course：P1 产出的帧已带 layout/layout_content/local_visual_path，
                # 不再从 narrations 裸构帧；scene_groups（章节聚合）一并装配。
                storyboard.frames.extend(course_frames)
                storyboard.scene_groups.extend(course_groups)
            else:
                for i, narration in enumerate(narrations):
                    storyboard.frames.append(StoryboardFrame(index=i, narration=narration))
            style_context = {"style_prefix": effective_style.style_prefix} if effective_style.style_prefix else None
            # S5（2026-08-26 调度表计划）：总开关透传（false = 纯现状逐帧，AC3 回滚位）；
            # shot_table_model 显式配置才传独立阶段 1 LLM（未配置 = 跟随 script LLM）。
            _st_llm = resolve_shot_table_llm(self._config)
            # P3（2026-09-10 独立管线计划）：remotion_course 跳过 plan_course（锚定卡/
            # 调度表/逐帧扩写/护栏/graphic spec 全套不执行）；generate 模式经 plan_course
            # 内部早退兜底。既有类型零变化。
            if course_frames is None:
                await planner_mod.plan_course(
                    llm,
                    storyboard=storyboard,
                    style_context=style_context,
                    task_dir=task_dir,
                    # E8（2026-08-21）：逐帧化配置透传（prompt_chunk_size 默认 1 = 逐帧）。
                    chunk_size=self._config.prompt_chunk_size,
                    frame_concurrency=self._config.prompt_frame_concurrency,
                    shot_table_enabled=self._config.shot_table_enabled,
                    # M2：出场声明名册（跟随项目才有才传——空名册 = 契约零变化 + 旧桩窄签名兼容）
                    **({"entity_roster": self._planning_entity_roster(payload, user_id=user_id)} if self._planning_entity_roster(payload, user_id=user_id) else {}),
                    **({"shot_table_llm": _st_llm} if _st_llm else {}),
                )
            await progress(progress=35)

        # Phase 3.4: vimax skips TrainerAI TTS — narration_generator produces per-shot
        # narration that matches the ViMax video length. storyboard + BGM still generated.
        if skip_tts:
            audio_concat_path = ""
            subtitle_path = ""
            await progress(progress=60)
        else:
            # 3-4. TTS + audio concat + SRT (shared with _render_from_draft)
            audio_concat_path, subtitle_path = await self._synthesize_audio(
                storyboard,
                task_dir,
                voice_id,
                seconds_per_frame=payload.get("seconds_per_frame"),
                task_id=task_id,
                # T-E11：section_id 仅挂项目任务传（None 不传——窄签名桩零变化）
                **({"section_id": payload.get("course_project_id")} if payload.get("course_project_id") else {}),
            )
            await progress(progress=60)

        # 4b. BGM: resolve bgm ID to file path
        bgm_id = payload.get("bgm")
        if bgm_id:
            bgm_src = _resolve_bgm_or_warn(bgm_id, user_id=user_id)
            if bgm_src:
                bgm_dest = str(task_dir / "bgm.mp3")
                import shutil

                shutil.copy2(bgm_src, bgm_dest)
                storyboard.config.bgm_path = bgm_dest

        await progress(progress=75)
        return storyboard, audio_concat_path, subtitle_path

    async def _prepare_storyboard_video(
        self,
        task_type: str,
        payload: dict,
        task_dir: Path,
        progress: ProgressUpdater,
        *,
        user_id: str = "default",
        task_id: str | None = None,
    ) -> dict:
        """Legacy orchestration (engine_arch='legacy', default): prep → visual_source → Worker payload.

        Prep is shared with the multi_engine path via _prepare_storyboard_and_audio.
        """
        storyboard, audio_concat_path, subtitle_path = await self._prepare_storyboard_and_audio(task_type, payload, task_dir, progress, user_id=user_id, task_id=task_id)
        effective_type = storyboard.config.video_type

        # 5. Visual source (remotion is template-driven, no visual source needed)
        if effective_type == "remotion":
            await progress(progress=90)
            from .pipelines.remotion_props import StoryboardResult

            return StoryboardResult(
                storyboard=storyboard,
                subtitle_path=subtitle_path,
                audio_dir=str(task_dir / "audio"),
                template_id=payload.get("template_id"),
            )

        # P1-12: 早写 storyboard.json——auto_render 长渲染（ai_video 30min+）期间让
        # _make_on_frame_done 的逐帧合并有落点（修前文件不存在 → 合并 early-return 全丢，
        # 用户全程看不到一张分镜图）。终态全量写在 render 完成后照常执行。
        early_sb_path = task_dir / "storyboard.json"
        if not early_sb_path.exists():
            # T-D2（D2 方案 c 时序前提）：draft 期即做确定性版式指派并持久化——
            # 生图（下方 visual source）按帧版式槽位比例出图须在生图前已知 layout；
            # 顺带 draft 期 LayoutSwitcher/静帧预览显示真实版式（隐-3）。
            if effective_type == "remotion_course":
                from .pipelines.remotion_course_props import assign_deterministic_layouts

                n_assigned = assign_deterministic_layouts(storyboard)
                if n_assigned:
                    logger.info("remotion_course: assigned %d deterministic layouts at draft", n_assigned)
            early_sb_path.write_text(
                json.dumps(storyboard.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        frame_concurrency = self._config.effective_frame_concurrency(len(storyboard.frames))
        # remotion_course（2026-09-10 独立管线计划 P5a）：绕过 _has_mixed_sources 判定
        # 直走工厂——新流程 storyboard 源集合常为 {None, ai_image}，None 帧被算成
        # default_type 会误判 mixed 走 MixedVisualSource 死路；槽位过滤由
        # RemotionCourseVisualSource 单点承接。存量 mixed storyboard（全帧 ai_image/
        # graphic 写死的旧任务）不受影响——本分支只按 task 级 effective_type 判定。
        if effective_type == "remotion_course":
            visual = self._make_visual_source(
                "remotion_course",
                engine=payload.get("engine", "auto"),
                concurrency=frame_concurrency,
                image_provider=payload.get("image_provider"),
                video_provider=payload.get("video_provider"),
            )
        elif self._has_mixed_sources(storyboard):
            visual = self._make_visual_source_mixed(
                storyboard,
                concurrency=frame_concurrency,
                image_provider=payload.get("image_provider"),
                video_provider=payload.get("video_provider"),
            )
        else:
            visual = self._make_visual_source(
                effective_type,
                engine=payload.get("engine", "auto"),
                concurrency=frame_concurrency,
                image_provider=payload.get("image_provider"),
                video_provider=payload.get("video_provider"),
            )
        on_frame_done = self._make_on_frame_done(storyboard, task_dir, progress=progress, total=len(storyboard.frames))
        # Task 12: 项目角色 front_url 作 reference_image_paths 注入 ai_image（i2i 一致性）。
        # mixed source 由 MixedVisualSource.render 透传给 ai_image 子源。
        # C4：跟随项目时锚图走专用参数通道（决策 13——ai_video 不消费，仅生图侧）。
        _anchor = _style_anchor_image_from_project(getattr(self, "_storage_dir", None), payload, user_id, task_dir=task_dir)
        # C4：有锚图才传该参数（条件传参——第三方/既有 VisualSource 实现的窄签名天然兼容）
        _anchor_kwargs = {"style_anchor_image_paths": [_anchor]} if _anchor else {}
        # M3：roster 通道（跟随项目且有资产才生效；生效则任务级角色/道具用途停用）
        _char_roster = _build_character_roster(payload, storage_dir=getattr(self, "_storage_dir", None))
        _prop_roster = _build_prop_roster(getattr(self, "_storage_dir", None), payload, user_id)
        _roster_kwargs = {}
        if _char_roster or _prop_roster:
            _roster_kwargs = {"character_roster": _char_roster, "prop_roster": _prop_roster}
        _warn_if_image_provider_not_i2i(self._config, payload, task_dir, anchor_path=_anchor)
        # W2（2026-08-24 活性重构）：帧渲染阶段挂滴答（渲染 gather 外层包一层；
        # 帧间无写库 2~13.5min > P1=120s）。每帧完成的 ProgressUpdater 写库保留
        # 进度语义、不兼任活性。预算（W4）：margin × ceil(n/并发) × frame_floor。
        _render_budget = self._stage_budget_frame_render(len(storyboard.frames), concurrency=frame_concurrency)
        async with self._liveness(task_id, stage="frame_render", stage_budget=_render_budget):
            await visual.render(
                storyboard,
                only_frames=_frames_to_regenerate(storyboard.frames, task_type),  # G2: 类型感知续点——仅跳过期望类型的成功帧，预览图/冻结帧/缺失都重生
                on_frame_done=on_frame_done,
                task_dir=task_dir,
                reference_image_paths=_character_ref_paths_from_payload(payload),
                **_anchor_kwargs,
                **_roster_kwargs,
            )
            await progress(progress=90)

            storyboard_path = task_dir / "storyboard.json"
            storyboard_path.write_text(
                json.dumps(storyboard.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        from .worker_probe import decide_pipeline_version, probe_worker_capabilities

        worker_caps = await probe_worker_capabilities()
        pv = decide_pipeline_version(storyboard, worker_caps)

        # Entry-1 出 payload 前按实测音频纠偏（此前只有 recompose/render-from-draft
        # 有 reconcile；pad 失败等缺口会让记录时长≠实测直达 worker）
        if _reconcile_durations_with_audio(storyboard, task_dir):
            logger.warning("Entry-1: frame durations drifted from audio files — reconciled")
            _write_storyboard_srt(storyboard, subtitle_path)
            await self._merge_audio_into_storyboard(storyboard, task_dir)

        # remotion_course（2026-09-08 T4）：出 remotion props payload 交棒独立渲染池
        # （props 完整构建在 gateway，worker 只消费——T3 拍板）。
        if task_type == "remotion_course":
            from .pipelines.remotion_course_props import build_remotion_props, build_remotion_worker_payload

            props = build_remotion_props(
                storyboard,
                task_dir=task_dir,
                user_id=user_id,
                course_project_id=payload.get("course_project_id"),
                task_payload=payload,
                storage_dir=getattr(self, "_storage_dir", None),
            )
            await progress(progress=92)
            return PipelineCompleted(payload=build_remotion_worker_payload(storyboard, props))

        return PipelineCompleted(
            payload=build_worker_payload(
                storyboard,
                subtitle_path=subtitle_path,
                subtitle_ass=subtitle_path[:-4] + ".ass" if subtitle_path else None,
                audio_concat_path=audio_concat_path,
                output_format=payload.get("output_format", "mp4"),
                quality=payload.get("quality", "standard"),
                pipeline_version=pv,
            )
        )

    async def _synthesize_audio(
        self,
        storyboard: Storyboard,
        task_dir: Path,
        voice_id: str,
        *,
        seconds_per_frame: float | None = None,
        task_id: str | None = None,
        section_id: str | None = None,
        stage_hook: Callable[[str], None] | None = None,
        policy: TTSPolicy | None = None,
    ) -> tuple[str, str]:
        """Parallel TTS synthesis + audio concat + SRT generation.

        并发纪律（2026-07-21 漂移根因环 2）：同一 task_dir 的整段合成（TTS→concat→SRT）
        互斥——草稿音频与渲染并发跑会交错出"audio.mp3 是新一代、storyboard/SRT 是旧一代"
        的撕裂态（vg_93e83a7ee259 实证）。不同 task_dir 仍并行。签名/返回值不变。

        ``section_id``（T-E11/D6）：挂课程项目的任务传 course_project_id（豆包多轮
        语境关联，系列课程语气连续）；无项目任务 None，行为零变化。

        ``stage_hook``（Plan A Task 3，2026-10-18）：阶段边界回调（stage ∈
        tts|concat|subtitle），草稿音频失败诊断用；缺省 None 不改变其他调用方。
        """
        async with _synth_lock_guard(str(task_dir)):
            return await self._synthesize_audio_locked(storyboard, task_dir, voice_id, seconds_per_frame=seconds_per_frame, task_id=task_id, section_id=section_id, stage_hook=stage_hook, policy=policy)

    async def _synthesize_audio_locked(
        self,
        storyboard: Storyboard,
        task_dir: Path,
        voice_id: str,
        *,
        seconds_per_frame: float | None = None,
        task_id: str | None = None,
        section_id: str | None = None,
        stage_hook: Callable[[str], None] | None = None,
        policy: TTSPolicy | None = None,
    ) -> tuple[str, str]:
        """Parallel TTS synthesis + audio concat + SRT generation.

        Shared by _prepare_storyboard_video and _render_from_draft.
        Returns (audio_concat_path, subtitle_path).
        """
        # Ensure QIHU_API_KEY is available for preset voices
        import os

        from .orchestration import tts as tts_mod

        # C3: 设置 task_dir 上下文，使 record_tts_degradation 能写入 sidecar
        tts_mod.set_tts_degradation_context(task_dir)

        if self._config.qihu_api_key:
            current = os.environ.get("QIHU_API_KEY", "")
            if not current:
                os.environ["QIHU_API_KEY"] = self._config.qihu_api_key

        # W2（2026-08-24 活性重构）：TTS 整批阶段挂滴答（60 帧健康最坏 60×n≈50min，
        # 帧间无写库远超 P1=120s）。阶段级包一层：gather/concat/SRT/字幕预切共享一个
        # tick task；预算 = margin × _tts_gather_timeout(n)（W4 公式，配置变预算自动变）。
        _tts_budget = self._stage_budget_tts(len(storyboard.frames))
        async with self._liveness(task_id, stage="tts_batch", stage_budget=_tts_budget):
            # P4（2026-09-10 独立管线计划）：remotion_course 跳过拆帧守卫——版式单元帧
            # 15-40s 是正常节奏；拆帧会重建帧并摧毁 layout_content/layout 关联。跳过前
            # 对 narration 跑 strip_image_markers 防御网（标记只进 image_ref，不进 TTS）。
            if storyboard.config.video_type == "remotion_course":
                from .orchestration.course_script import strip_image_markers

                for _frame in storyboard.frames:
                    if _frame.narration:
                        _frame.narration = strip_image_markers(_frame.narration)
            elif not seconds_per_frame:
                # Phase 3 Task 11: 拆镜预防层——超长旁白预拆分（spf 显式设置时不拆）
                await self._split_long_narration_frames(storyboard, task_dir)

            audio_dir = task_dir / "audio"
            audio_dir.mkdir(parents=True, exist_ok=True)

            # Determine concurrency based on voice provider
            concurrency = _tts_concurrency(voice_id)
            semaphore = asyncio.Semaphore(concurrency)

            # 同内容键并发纪律（限本调用内；跨调用由 task 级 _synth_locks 互斥）：重复旁白帧
            # 共享同一 CAS 键——按键串行锁把 [CAS 检查→adopt] 与 [synthesize→P2-14 归位] 互斥，
            # cas_results 登记同键首帧的最终元数据供后到帧直接收编（TTS 文件级去重落到帧级）。
            cas_key_locks: dict[str, asyncio.Lock] = {}
            cas_results: dict[str, tuple[str, float, float, str]] = {}

            _ctx = _SynthFrameCtx(
                task_dir=task_dir,
                audio_dir=audio_dir,
                voice_id=voice_id,
                tts_speed=storyboard.config.tts_speed,
                fallback_voice=self._config.tts_fallback_voice,
                semaphore=semaphore,
                cas_key_locks=cas_key_locks,
                cas_results=cas_results,
                seconds_per_frame=seconds_per_frame,
                section_id=section_id,
                tts_mod=tts_mod,
                policy=policy,
            )

            async def synth_one(frame):
                # B4（2026-09-28）：逐帧合成抽为共享 helper（regenerate-audio 同语义复用）
                await _synth_frame_cas(_ctx, frame)

            tasks = [asyncio.ensure_future(synth_one(f)) for f in storyboard.frames]
            if stage_hook:
                stage_hook("tts")
            # T2: elapsed 兜底——T1 已让每次 TTS 调用有界（SSE 120s×3 重试+降级链），
            # 这里只防兜底之外的意外 hang。预算按帧数缩放（max(1800, n×60)），
            # 长任务有足额合法时间；超时取消兄弟协程并抛可读 RuntimeError。
            # 用 asyncio.wait(FIRST_EXCEPTION) 而非 wait_for(gather)：wait_for 会把
            # 子任务自身抛出的 TimeoutError（降级链尽头）与整批预算超时混为一谈
            # （审查 Minor 1）——子任务异常必须原样传播，只有「无异常但预算耗尽」才报整批。
            tts_budget = _tts_gather_timeout(len(storyboard.frames))
            try:
                done, pending = await asyncio.wait(tasks, timeout=tts_budget, return_when=asyncio.FIRST_EXCEPTION)
            except asyncio.CancelledError:
                # 审查 Minor 1 回归（二审）：外层取消（= T6 reaper 谓词 C 杀任务的路径）时
                # asyncio.wait 不会替我们取消子任务——不清理则 in-flight synth_one 变孤儿，
                # 继续烧 TTS 配额 + 锁释放后仍写 CAS 文件（与重试代际交错）。这是旧实现
                # except BaseException 清理路径的等价物：取消全部 + 收敛 + 原样重抛。
                for t in tasks:
                    t.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                raise
            failed = [t for t in done if not t.cancelled() and t.exception() is not None]
            if failed:
                # 首异常传播但不取消兄弟 task——in-flight synth_one 变孤儿、锁释放后
                # 仍写 CAS 文件（与重试代际交错）。先取消并收敛再放锁（旧语义）。
                for t in pending:
                    t.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                failed[0].result()  # 原样重抛子任务异常（含其自身的 TimeoutError）
                raise AssertionError("unreachable")  # pragma: no cover
            if pending:
                # 无任何异常但预算耗尽 → 整批 elapsed 兜底触发
                for t in pending:
                    t.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                raise RuntimeError(f"TTS 阶段超时（预算 {tts_budget:.0f}s），已取消未完成帧") from None
            storyboard.recompute_duration()

            audio_concat_path = str(task_dir / "audio.mp3")
            if stage_hook:
                stage_hook("concat")
            # 2026-08-20：传 Σframe.duration 做拼接对账（漂移 >0.5s 告警，只观测）
            await run_ffmpeg(_concat_audio, [f.audio_path for f in storyboard.frames], audio_concat_path, expected_seconds=sum(f.duration for f in storyboard.frames))
            subtitle_path = str(task_dir / "subtitle.srt")
            if stage_hook:
                stage_hook("subtitle")
            # T8: LLM 语义重切（config 开启时预计算；失败静默回退规则链）
            llm_chunks = await _precompute_llm_chunks(storyboard, self._config, task_dir)
            _write_storyboard_srt(storyboard, subtitle_path, llm_chunks_map=llm_chunks)

            return audio_concat_path, subtitle_path

    async def _split_long_narration_frames(self, storyboard: Storyboard, task_dir: Path) -> None:
        """Phase 3 Task 11: 递归拆镜守卫——超长旁白帧在标点边界预拆。

        条件：无显式 spf（spf 是用户明确意愿，不拆）。
        行为：遍历 storyboard.frames，对 narration > 动态预算的帧调 split_long_narration
        递归拆分 → 插入新帧（后续帧 image_prompt 加"同场景继续"修饰）→ 重建帧序号。
        拆分帧数写入 sidecar。

        预算口径（2026-08-14 C1）：字数 = SHOT_MAX_NARRATION_SECONDS × 3.33 字/s ×
        storyboard.config.tts_speed——慢速少字多拆、快速多字少拆，与 _compute_word_bounds
        的 3.33 字/s 同源（修前死常量 40 无视 tts_speed）。

        C1 修复（评审）：StoryboardFrame 无 video_prompt 字段（视频提示词存在 image_prompt）；
        每段保留原帧的 visual_url/local_visual_path 等产物（旁白变了视觉还能用；
        T1 起从「仅首段」扩为全段继承——后续段无预览图会让机制 M 落空、
        三视图兜底闪现，见拆帧处 T1 注释）。
        """
        from .orchestration.script import SHOT_MAX_NARRATION_SECONDS, split_long_narration
        from .storyboard import StoryboardFrame

        if not storyboard.frames:
            return

        new_frames: list[StoryboardFrame] = []
        split_count = 0
        next_index = 0
        old_to_new: dict[int, list[int]] = {}  # A-1: 拆帧前 index → 拆帧后新 index 列表

        for frame in storyboard.frames:
            old_index = frame.index
            budget = round(SHOT_MAX_NARRATION_SECONDS * 3.33 * storyboard.config.tts_speed)
            segments = split_long_narration(
                frame.narration or "",
                budget_chars=budget,
            )
            if len(segments) <= 1:
                # 不拆——保留原帧，仅更新 index
                frame.index = next_index
                new_frames.append(frame)
                old_to_new[old_index] = [next_index]
                next_index += 1
                continue

            # 拆帧：每段成一个新帧
            total_len = len(frame.narration or "")
            landed = 0
            for seg_idx, seg in enumerate(segments):
                seg = seg.strip()
                if not seg:
                    continue
                suffix = "（同场景继续）" if landed > 0 else ""
                # B-4.1: 新段预置估算时长——旧 duration 按段长比例分摊；无旧值
                # （首渲前 duration=0）按 3.33 字/s × tts_speed 估（与预算公式同源）。
                # TTS 实测后由 _merge_audio_into_storyboard 覆写。
                if frame.duration and frame.duration > 0 and total_len:
                    est = round(frame.duration * len(seg) / total_len, 2)
                else:
                    est = round(len(seg) / (3.33 * storyboard.config.tts_speed), 2)
                # content-match T1：graphic_spec **故意不透传**——旁白已被改写，
                # 旧 spec 的事实核对失效；子帧继承 visual_source（含 graphic）、
                # spec=None → 渲染期按常规画面出图。勿「补全」此字段。
                new_frame = StoryboardFrame(
                    index=next_index,
                    narration=seg,
                    duration=est,
                    image_prompt=(frame.image_prompt or "") + suffix if suffix else frame.image_prompt,
                    search_term=frame.search_term,
                    visual_source=frame.visual_source,
                    shot_size=frame.shot_size,
                    camera_move=frame.camera_move,
                )
                # T1（2026-09-01 拆帧计划）：预览图三字段**每段**继承（引用同一
                # 文件，不复制，无磁盘副作用）——修前仅首个落表段继承，后续段
                # 无预览图 → 机制 M 的 i2v 首帧条件（visual_type=="image" and
                # local_visual_path）不成立 → 角色三视图合成图兜底作 H3 首帧
                # （vg_22ee06b39d19 开头闪现三视图）。原帧无产物时继承 None，
                # 新段保持空（现状兜底链不变）；代价 = 拆出各段开头共用同一
                # 预览图画面（同场景延续，可接受，重新生图会多烧生图费不做）。
                new_frame.visual_url = frame.visual_url
                new_frame.visual_type = frame.visual_type
                new_frame.local_visual_path = frame.local_visual_path
                if landed == 0:
                    # B-4.3: actual_visual_duration 只归首个**实际落表**的段——
                    # 它是原资产 ffprobe 实测时长；后续段视频将新生成，继承会
                    # 毒化 visual shortfall 判定。
                    new_frame.actual_visual_duration = frame.actual_visual_duration
                new_frames.append(new_frame)
                old_to_new.setdefault(old_index, []).append(next_index)
                next_index += 1
                landed += 1
            # B-4.2: 只计实际落表的段数——修前 len(segments)-1 未扣空段 continue，
            # sidecar 拆镜数虚高。
            split_count += max(landed - 1, 0)

        if split_count > 0:
            logger.info(
                "Phase 3 frame split: %d frames → %d frames (%d splits)",
                len(storyboard.frames),
                len(new_frames),
                split_count,
            )
            storyboard.frames = new_frames
            # A-1: scene_groups.frame_indices 重映射到新帧表。修前 stale 序号会让
            # ai_video._group_of 组锚定落错组/查无此组、build_worker_payload 把 stale
            # 分组下发（v3_segmented 组内/组间转场作用在错误镜头间）、全量落盘写回
            # 坏数据。被拆帧的多个新段连续继承原位置，归属被拆帧原来的 group。
            if storyboard.scene_groups:
                for g in storyboard.scene_groups:
                    remapped: list[int] = []
                    for fi in g.frame_indices:
                        remapped.extend(old_to_new.get(fi, [fi]))
                    g.frame_indices = remapped
            storyboard.recompute_duration()

            # T5 (2026-08-14): 拆分结果立即全量落盘——_merge_audio_into_storyboard 按
            # frame.index 键合并进磁盘 storyboard，修前只改内存 → 磁盘 125 帧 vs 内存
            # 140 帧重编号 → 第一个拆分点之后所有帧的音频元数据全部错位。落盘后
            # merge 的 index 键两侧同代。复用 _update_storyboard（锁 + 原子写，
            # 与 _generate_draft_audio 的并发纪律一致）。
            def _persist(data: dict) -> None:
                data["frames"] = [asdict(f) for f in new_frames]
                data["total_duration"] = storyboard.total_duration
                data["scene_groups"] = [asdict(g) for g in storyboard.scene_groups]  # A-1: remap 后一并落盘

            await self._update_storyboard(task_dir, _persist)
            try:
                record_degradation(
                    task_dir,
                    kind=KIND_FRAME_SPLIT,
                    detail=f"检测到 {split_count} 个超长旁白帧，已自动拆分为 {len(new_frames)} 帧",
                )
            except Exception:
                logger.warning("failed to record frame_split sidecar", exc_info=True)

    async def _merge_audio_into_storyboard(self, storyboard: Storyboard, task_dir: Path, *, verify_drift: bool = False) -> tuple[int, list[int]]:
        """把 _synthesize_audio 的实测结果（audio_path/duration/srt/speech_duration）
        按帧键合并落盘 storyboard.json，返回 (合并帧数, 漂移帧 index 列表)。

        _generate_draft_audio（E11 阈值判定）与 _render_from_draft 共用——后者此前只在
        内存更新就发 payload，磁盘残留旧时长 → recompose 读盘踩旧值（2026-07-21 漂移
        根因环 3）。合并语义同 E11：按 frame.index 键（非 narration hash），重复旁白不互覆。

        共用方共 5 个调用点（_run_pipeline / _prepare_storyboard_video / draft
        _generate_draft_audio / _render_from_draft ×2）。``verify_drift`` 仅 draft 路径
        传 True——其余路径合成与合并紧邻、无 B5 编辑面，保持修前行为零变化（其调用
        本就不接收返回值）。

        verify_drift=True（2026-10-06 漂移批 v2）：内容等价比对——**禁 CAS 键重算**
        （P2-14 降级帧文件名挂实际音色键，按请求音色重算必假阳性，v1 评审 P1）：
        - 帧级：磁盘旁白字符串 vs 合成代旁白字符串（remotion_course 磁盘侧先过同一
          strip_image_markers——合成前防御网已剥离内存侧，不 strip 则该管线全假阳性）；
        - config 级：磁盘 data["config"] 的 voice_id/tts_speed vs 合成代配置，不一致
          → 整批漂移（新配置下补配轮全帧 CAS 自然 miss 重配，语义正确）；
        - 磁盘帧不在合成代 map（窗口内新增帧）也计 drifted。
        漂移帧不合并——旧代音频永不可能写入旁白已变的帧（G1）。
        """
        frame_audio_map = _build_frame_audio_map(storyboard.frames)
        merged_count = 0
        drifted: list[int] = []
        # strip 判定与 _synthesize_audio 的防御网同源（按内存合成代 config）
        _strip = None
        if verify_drift and storyboard.config.video_type == "remotion_course":
            from .orchestration.course_script import strip_image_markers

            _strip = strip_image_markers

        def _updater(data: dict) -> None:
            nonlocal merged_count
            config_drift = False
            if verify_drift:
                disk_cfg = data.get("config") or {}
                # 磁盘缺键（损坏/旧档）→ 无法判定，不判漂移（防御方向：不放大为整批重配）
                if disk_cfg.get("voice_id") is not None and disk_cfg["voice_id"] != storyboard.config.voice_id:
                    config_drift = True
                elif disk_cfg.get("tts_speed") is not None and disk_cfg["tts_speed"] != storyboard.config.tts_speed:
                    config_drift = True
            for f in data.get("frames", []):
                idx = f.get("index")
                entry = frame_audio_map.get(idx)
                if verify_drift:
                    disk_narr = f.get("narration") or ""
                    if _strip is not None:
                        disk_narr = _strip(disk_narr)
                    if config_drift or entry is None or disk_narr != (entry.get("narration") or ""):
                        drifted.append(idx)
                        continue
                if entry is None:
                    continue
                # narration 是比对依据、不是合并载荷——写回会覆写磁盘（可能更新的）旁白
                f.update({k: v for k, v in entry.items() if k != "narration"})
                merged_count += 1

        await self._update_storyboard(task_dir, _updater)
        return merged_count, drifted

    async def _generate_draft_audio(self, task_id: str, task_dir: Path, user_id: str | None = None) -> None:
        """Background task: generate TTS audio after draft completes.

        Does not block draft return. Updates storyboard.json with audio_path/duration,
        then patches task payload with audio_ready=True.

        合并校验与补配（2026-10-06 漂移批 v2，取代 E11 前后的哈希键失效描述）：
        merge 按 frame.index 键合并，verify_drift=True 时先做内容等价比对（磁盘旁白
        vs 合成代旁白 + config 级 voice/speed）——TTS 窗口（~50s）内用户原地改旁白/
        重排帧序/改音色，旧代音频不写回错配帧（E11 把哈希键改帧键时顺带丢失的防线，
        本批复活）。检出漂移 → 持合成锁重读磁盘、整帧集重放 synth+merge（≤2 轮；
        CAS 使未变帧零 TTS 成本；_synthesize_audio 是整批语义——逐帧 TTS→concat→SRT，
        禁止部分帧调用，否则拼接产物残缺）。merged_count 只计校验通过的合并：≥50%
        置 audio_ready；两轮补配后仍在漂移（用户持续编辑）→ 放弃，漂移帧保持无
        audio_path，audio_error 报「旁白已修改 N/M」。锁挡不住仅持 sb 写锁的 PUT
        编辑插在补配轮重读与合并之间——每轮仍跑校验兜底，不靠锁外假设（R2）。
        """
        # Plan A Task 3（2026-10-18）：同轮次失败阶段诊断——stage_hook 把
        # tts/concat/subtitle 阶段边界同步到本地，merge 前本地切换；异常出口
        # 据此写 payload audio_error_stage（阶段化文案），原始 traceback 仍只进
        # 服务端日志（logger.exception），不外泄进 payload。**stage 绑定必须在
        # try 之前**（评审修复）：入口读盘/解析异常早于 try 内首段绑定，若在
        # try 内初始化，except 处理器引用 stage["current"] 会自身 UnboundLocalError
        # 崩溃 → audio_error 永不写 → 前端无限轮询。
        stage = {"current": "tts"}

        def _stage_hook(s: str) -> None:
            stage["current"] = s

        try:
            storyboard = Storyboard.from_dict(json.loads((task_dir / "storyboard.json").read_text(encoding="utf-8")))
            # P1-E11: 提前取 payload 透传 spf（修前草稿路径 spf 是死代码——_synthesize_audio
            # 支持该参数但 draft/render 两处都没传）；复用此 task 做下方 audio_ready patch。
            task = await self.repo.get(task_id, user_id=user_id)
            pl = json.loads(task.payload or "{}") if task and task.payload else {}

            # 审查 Minor 2（2026-08-14）：synth+merge 整段持合成锁——修前 merge 在锁外，
            # 「草稿 TTS 未跑完时用户确认渲染」可与 render 代交错，用旧代音频元数据
            # 覆写渲染代的编辑帧（vg_93e83a7ee259 谱系）。_synthesize_audio 内部的
            # guard 对同一协程可重入（ContextVar），不会死锁——补配轮同样适用。
            async with _synth_lock_guard(str(task_dir)):

                async def _synth_and_merge(sb: Storyboard) -> tuple[int, list[int]]:
                    # T-E11：section_id 仅挂项目任务传（None 不传——窄签名桩零变化）；
                    # 补配轮用重读代的 config（窗口内改音色 → 新配置下全帧重配，语义正确）。
                    await self._synthesize_audio(
                        sb,
                        task_dir,
                        sb.config.voice_id,
                        seconds_per_frame=pl.get("seconds_per_frame"),
                        task_id=task_id,
                        stage_hook=_stage_hook,
                        policy=_vimax_tts_policy_for(task),
                        **({"section_id": pl.get("course_project_id")} if pl.get("course_project_id") else {}),
                    )
                    stage["current"] = "merge"
                    # E11: frame.index 键合并（保 TTS 文件级去重）+ v2 内容等价校验
                    return await self._merge_audio_into_storyboard(sb, task_dir, verify_drift=True)

                merged_count, drifted = await _synth_and_merge(storyboard)
                _round = 0
                while drifted and _round < 2:
                    _round += 1
                    storyboard = Storyboard.from_dict(json.loads((task_dir / "storyboard.json").read_text(encoding="utf-8")))
                    merged_count, drifted = await _synth_and_merge(storyboard)

            # Only mark audio_ready if most frames merged successfully
            total = len(storyboard.frames)  # E11: 按帧数计（非 unique narration 数）；取最新代（补配轮重读后）
            if task is None:
                logger.warning("Task %s not found, cannot mark audio_ready", task_id)
                return
            # A3（2026-10-07 遗留批 P1-3；同批复审上移——先于分支判定，避免消失场景先打
            # ready 日志再打 vanished 的矛盾观感）：storyboard 消失（任务删除竞态——行先
            # 删、目录后删）时 merge 对缺失文件静默 no-op（merged=0），照走 give-up 会报
            # 「旁白已修改 N/N」张冠李戴；且行已删时 repo.update 本就 no-op。统一不写状态。
            if not (task_dir / "storyboard.json").exists():
                logger.warning("storyboard.json vanished for task %s, skip audio_ready/audio_error write", task_id)
                return
            if merged_count >= total * 0.5:
                pl["audio_ready"] = True
                pl["total_duration"] = storyboard.total_duration
                pl.pop("audio_error", None)
                pl.pop("audio_error_stage", None)
                logger.info("Draft audio ready for task %s: %.1fs", task_id, storyboard.total_duration)
            else:
                # A2（2026-10-07 遗留批 P1-2）：error 与 ready 互斥——run 起点快照可能残留
                # 上代成功标记（P0-3 重启恢复直发场景，trigger 入口的 already_ready 短路
                # 管不到 pipeline 直发），双标记并存会让前端三态渲染与轮询停转判断失真。
                pl.pop("audio_ready", None)
                pl["audio_error"] = f"旁白已修改，{total - merged_count}/{total} 帧需重新生成音频"
                pl.pop("audio_error_stage", None)  # 漂移 give-up 非阶段性故障，清掉上代阶段
                logger.warning("Draft audio partial merge for %s: %d/%d matched", task_id, merged_count, total)
            await self.repo.update(task_id, payload=json.dumps(pl, ensure_ascii=False))
        except Exception:
            logger.exception("Draft audio generation failed for task %s (stage=%s)", task_id, stage["current"])
            task = await self.repo.get(task_id, user_id=user_id)
            if task:
                pl = json.loads(task.payload or "{}")
                # A2 同款互斥：except 分支以最新库内 payload 为底，同样可能残留上代 ready。
                pl.pop("audio_ready", None)
                # Plan A Task 3：阶段化文案 + audio_error_stage（同轮次实际失败阶段，
                # 修前只有通用文案，四阶段不可区分）。
                _stage_label = _AUDIO_STAGE_LABELS.get(stage["current"], stage["current"])
                pl["audio_error"] = f"音频生成失败（{_stage_label}），请重试"
                pl["audio_error_stage"] = stage["current"]
                await self.repo.update(task_id, payload=json.dumps(pl, ensure_ascii=False))

    def _resolve_effective_style(self, payload: dict, user_id: str = "default") -> EffectiveStyle:
        """C3: 全维度风格仲裁（决策 3/5/20）——generate/draft/prompt 重生端点三处共用。

        V2（默认开）语义：
        - 绑项目 + style 未显式（None/""/"default"/哨兵）→ 跟随项目（source="project"）；
        - 绑项目 + style 显式其他值 → **完全脱离**（source="task"）：项目风格前缀/锚图/
          角色三视图（二期含道具）一律不注入——全断实现点在 _inject_project_character_context
          的 effective_style_source 门控；
        - 无项目 → 现状（显式=task，默认/空=none）。
        哨兵防御（TC-C3-08，V2 开/关两分支均生效）："follow_project" 恒按未设置处理，并归一为
        "default" 写回 payload——哨兵不得以原样流向 narration LLM / StoryboardConfig.style /
        storyboard.json（不产生异常前缀、不持久化脏值）。
        V2 关断（决策 20 回退位）→ 旧「项目遮蔽」分支（显式风格也被项目遮蔽），不写留痕。
        """
        payload = payload if payload is not None else {}
        pid = payload.get("course_project_id") or None
        raw_style = payload.get("style")
        unset = raw_style is None or raw_style in ("", "default", _STYLE_SENTINEL)
        if raw_style == _STYLE_SENTINEL:
            payload["style"] = "default"
        if not _style_arbitration_v2_enabled():
            # 旧「项目遮蔽」仲裁：不写留痕（决策 20）
            if pid:
                ctx = self._load_course_project_style(pid, user_id=user_id) or {}
                return EffectiveStyle(source="project", style_prefix=ctx.get("style_prefix", ""), course_project_id=pid)
            if unset:
                return EffectiveStyle(source="none", style_prefix="", course_project_id=None)
            ctx = self._build_style_context_from_payload(payload) or {}
            return EffectiveStyle(source="task", style_prefix=ctx.get("style_prefix", ""), course_project_id=None, style_key=str(raw_style))
        if pid and unset:
            ctx = self._load_course_project_style(pid, user_id=user_id) or {}
            payload["effective_style_source"] = "project"
            return EffectiveStyle(source="project", style_prefix=ctx.get("style_prefix", ""), course_project_id=pid)
        if pid:
            ctx = self._build_style_context_from_payload(payload) or {}
            payload["effective_style_source"] = "task"
            return EffectiveStyle(source="task", style_prefix=ctx.get("style_prefix", ""), course_project_id=pid, style_key=str(raw_style))
        if unset:
            payload["effective_style_source"] = "none"
            return EffectiveStyle(source="none", style_prefix="", course_project_id=None)
        ctx = self._build_style_context_from_payload(payload) or {}
        payload["effective_style_source"] = "task"
        return EffectiveStyle(source="task", style_prefix=ctx.get("style_prefix", ""), course_project_id=None, style_key=str(raw_style))

    def _planning_entity_roster(self, payload: dict, user_id: str = "default") -> dict:
        """M2：planner 出场声明名册——角色取 payload 注入的 sheets 快照，道具读项目。

        仅跟随项目（effective_style_source == "project"）时提供——显式脱离（task，
        决策 3 全断口径二期自然延伸至道具）与无项目 → 空名册（planner 输出契约零
        变化，visible_* 全 None = 旧行为）。
        """
        if (payload or {}).get("effective_style_source") != "project":
            return {}
        proj = self._read_course_project(payload.get("course_project_id"), user_id=user_id)
        roster: dict = {"characters": [], "props": []}
        # 角色优先取 payload 注入的 sheets 快照（管线运行路径）；payload 无 sheets
        # （如 prompt 重生端点从 repo 读的裸 payload）→ 回落项目本体。修前只读
        # payload——重生端点角色名册恒空，出场声明重推在生产空转（复核 B2 次生）。
        sheets = payload.get("character_sheets") or []
        if not sheets and proj:
            roster["characters"] = [{"name": cs.name, "features": cs.features or ""} for cs in proj.character_sheets if getattr(cs, "name", "")]
        else:
            roster["characters"] = [{"name": sheet["name"], "features": sheet.get("features") or ""} for sheet in sheets if isinstance(sheet, dict) and sheet.get("name")]
        for pa in getattr(proj, "prop_assets", None) or []:
            roster["props"].append({"name": pa.name, "description": getattr(pa, "description", "") or ""})
        return roster

    def _build_style_context_from_payload(self, payload: dict) -> dict | None:
        """Build style_context from the task payload's ``style`` field (no CourseProject).

        Counterpart to ``_load_course_project_style``: that one loads project-level
        style; this one translates the per-task ``style`` dropdown
        (default/professional/casual/cinematic) into a ``style_prefix`` for
        plan_course. Returns None when style is default/empty (no injection).
        """
        style_val = (payload or {}).get("style", "default")
        # 2026-08-02 审查修：过 style_prefix_for（内含 resolve_style_key），与项目级
        # course_models.style_prefix 保持同一套解析语义。原先直查 STYLE_PREFIXES，
        # 若 payload.style 是英文字面量（如从项目级复制的 payload、或第三方 API
        # 调用方传入）会**静默失掉风格前缀** —— 不报错、画面无风格，难排查。
        prefix = style_prefix_for(style_val)
        return {"style_prefix": prefix} if prefix else None

    def _load_course_project_style(self, course_project_id: str | None, *, user_id: str = "default") -> dict:
        """Load style_context from a CourseProject file."""
        # 审查发现 1：同上，本 loader 此前也未过白名单。
        if not _is_safe_course_project_id(course_project_id):
            return {}
        import json

        from .course_models import CourseProject
        from .course_pipeline import CoursePipelineService

        base = Path(self._storage_dir) / "course_projects"
        # E15: 仅按 user_id 精确定位，删跨用户 iterdir fallback（防 IDOR 式泄漏）
        project_path = base / user_id / f"{course_project_id}.json"
        if not project_path.exists():
            return {}
        try:
            data = json.loads(project_path.read_text(encoding="utf-8"))
            project = CourseProject.from_dict(data)
            return CoursePipelineService.build_style_context(project)
        except Exception:
            logger.warning("Failed to load course project %s", course_project_id)
            return {}

    def _read_course_project(self, course_project_id: str | None, *, user_id: str = "default"):
        """B2: 加载 CourseProject 对象（供读 voice_id/subtitle_style）。None=无/不存在。

        镜像 ``_load_course_project_style`` 的加载逻辑，但返回项目对象而非 style_context。
        """
        if not _is_safe_course_project_id(course_project_id):
            if course_project_id:
                logger.warning("rejected malformed course_project_id: %r", course_project_id[:80])
            return None
        import json

        from .course_models import CourseProject

        base = Path(self._storage_dir) / "course_projects"
        project_path = base / user_id / f"{course_project_id}.json"
        if not project_path.exists():
            return None
        try:
            data = json.loads(project_path.read_text(encoding="utf-8"))
            return CourseProject.from_dict(data)
        except Exception:
            logger.warning("Failed to load course project %s", course_project_id)
            return None

    def _resolve_voice_id(self, payload_voice, course_project_id, *, user_id: str = "default") -> str | None:
        """B2: payload 显式 > 项目 voice_id > None。

        返回 None 表示 payload 与项目都未设音色，调用方再走 lang 默认 / config
        （保留既有 ``resolve_default_voice(lang)`` 链路）。修前完全无视项目 voice_id。
        """
        if payload_voice and payload_voice != _STYLE_SENTINEL:  # C7：哨兵=未显式（跟随项目）
            return payload_voice
        proj = self._read_course_project(course_project_id, user_id=user_id)
        if proj and getattr(proj, "voice_id", None):
            return proj.voice_id
        return None

    def _resolve_voice_with_vimax_gate(self, task_type: str, payload: dict, *, user_id: str = "default") -> tuple[str | None, str]:
        """voice 三级解析 + Plan B（2026-10-19）vimax 门。

        ``_prepare_storyboard_and_audio`` 与 ``_generate_draft`` 共用——评审修复：
        修前 vimax 分支只落在 _prepare，UI 默认草稿路径（auto_render=false →
        ``_generate_draft``）仍把共享链的 ``preset:146`` 兜底写进
        storyboard.config.voice_id，后台草稿 TTS 被 reject 策略整体炸成通用错误
        循环。vimax：缺省走 ``resolve_vimax_default_voice``（zh 兜底不再
        preset:146）；显式/项目继承 preset 抛可读 ValueError（ensure 统一文案）。
        非 vimax：完全旧链。返回 (voice_id, effective_source)。
        """
        voice_id = self._resolve_voice_id(payload.get("voice_id"), payload.get("course_project_id"), user_id=user_id)
        source = ""
        if payload.get("voice_id") and payload.get("voice_id") != _STYLE_SENTINEL:
            source = "task"
        elif voice_id:
            source = "project"
        if not voice_id:
            lang = payload.get("lang", "zh")
            if task_type == "vimax":
                from app.gateway.video_gen.engines.vimax.voice_policy import resolve_vimax_default_voice

                voice_id = resolve_vimax_default_voice(lang)
                source = "lang_default"
            else:
                from .orchestration.voices import resolve_default_voice

                default_voice = resolve_default_voice(lang)
                voice_id = default_voice or self._config.tts_voice
                source = "lang_default" if default_voice else "config"
        elif task_type == "vimax":
            from app.gateway.video_gen.engines.vimax.voice_policy import ensure_vimax_voice_allowed

            ensure_vimax_voice_allowed(voice_id)
        return voice_id, source

    def _resolve_tts_speed(self, payload_speed, course_project_id, *, user_id: str = "default") -> float | None:
        """T-E11（D6）: payload 显式 > 项目 tts_speed > None（调用方回落 config tts_speed）。

        与 ``_resolve_voice_id`` 三级链同构；返回 None = payload 与项目都未设语速
        （``CreateVideoRequest.tts_speed`` 默认 None，无哨兵）。音频 CAS 键
        ``_audio_cache_key`` 已含 speed，项目值与缺省值自动分键。
        """
        if payload_speed:
            return float(payload_speed)
        proj = self._read_course_project(course_project_id, user_id=user_id)
        if proj and getattr(proj, "tts_speed", None):
            return float(proj.tts_speed)
        return None

    def _resolve_subtitle_style(self, payload_style, course_project_id, *, user_id: str = "default"):
        """B2: payload 显式非 default > 项目 subtitle_style > "default"。

        ``CreateVideoRequest.subtitle_style`` 的 schema 默认是 "default"，故 payload="default"
        视为未设置 → 回退项目。修前只读 payload（项目级字幕样式静默失效）。
        """
        if payload_style and payload_style != "default" and payload_style != _STYLE_SENTINEL:  # C7：哨兵=未显式
            return payload_style
        proj = self._read_course_project(course_project_id, user_id=user_id)
        if proj and getattr(proj, "subtitle_style", None):
            return proj.subtitle_style
        return "default"

    async def _generate_draft(
        self,
        task_type: str,
        payload: dict,
        task_dir: Path,
        progress: ProgressUpdater,
        *,
        user_id: str = "default",
        task_id: str | None = None,
    ) -> dict:
        """Phase 1: generate storyboard draft (LLM only, ~5s, zero TTS/visual cost)."""
        import json

        from .orchestration import script as script_mod
        from .orchestration import storyboard_planner as planner_mod
        from .storyboard import StoryboardConfig, StoryboardFrame

        await progress(progress=5)

        # W2（2026-08-24 活性重构）：分镜/脚本 LLM 阶段挂滴答（vimax story 扩写/
        # 叙事生成/分镜计划循环整体一个 tick task）。预算同 _prepare_storyboard_
        # and_audio 口径（W4 公式）。
        _n_expected = _target_scenes_for_fixed(payload, payload.get("script", "")) if payload.get("script") else _resolve_n_scenes(payload)
        _llm_budget = self._stage_budget_llm(_n_expected or 0, concurrency=self._config.prompt_frame_concurrency)
        async with self._liveness(task_id, stage="storyboard_llm", stage_budget=_llm_budget):
            # M3.1 idea2video story-confirmation flow: instead of a fake narration
            # blueprint (generate_narrations from the raw idea), expand the idea into a
            # full story via ViMax's Screenwriter.develop_story and pause at STORY_READY
            # for user confirmation. render then reuses story.txt (develop_story loads
            # from disk if present), so the confirmed story is what gets scripted.
            if task_type == "vimax" and payload.get("pipeline") == "idea2video" and payload.get("topic"):
                from vimax.agents.screenwriter import Screenwriter

                story_model = _resolve_vimax_story_model(
                    self._config.vimax_llm_model,
                    self._config.script_model,
                    self._config.vimax_llm_base_url,
                    task_dir=task_dir,
                )
                screenwriter = Screenwriter(chat_model=story_model)
                # Phase 2 (v4): inject user_requirement_extra at DRAFT stage so the
                # story expansion respects scene/shot limits from the start. Without
                # this, story.txt is already 5 scenes before render — too late.
                req = payload.get("topic", "")
                extra = payload.get("user_requirement_extra", "")
                if extra:
                    req = f"{req}\n\n{extra}"
                story = await screenwriter.develop_story(
                    idea=payload["topic"],
                    user_requirement=req,
                )
                (task_dir / "story.txt").write_text(story, encoding="utf-8")
                await progress(progress=90)
                return PipelineDraft(story=story)

            effective_type = task_type
            if task_type == "ai_visual":
                effective_type = "ai_image"

            mode = payload.get("mode", "fixed")
            aspect = payload.get("aspect") or self._config.default_aspect
            width, height = _aspect_to_size(aspect)
            # Plan B（2026-10-19 评审修复）：本函数是 UI 默认草稿路径（auto_render=false）
            # 的音色解析点——修前仍走共享链（zh 兜底 preset:146、无 preset 门），
            # 后台草稿 TTS 被 reject 策略整体炸成通用错误循环。改与 _prepare 共用
            # _resolve_voice_with_vimax_gate（vimax 缺省走 ViMax 解析；显式/项目继承
            # preset 在草稿生成前可读拒绝）。
            voice_id, _voice_source = self._resolve_voice_with_vimax_gate(task_type, payload, user_id=user_id)
            if _voice_source:
                payload["effective_voice_source"] = _voice_source

            llm = create_script_llm(payload, self._config)  # E6: 显式化，不回落 models[0]
            # T-E11（D6）：语速三级链（任务显式 > 项目 > config 缺省；与 voice_id 同构）
            resolved_tts_speed = self._resolve_tts_speed(payload.get("tts_speed"), payload.get("course_project_id"), user_id=user_id) or self._config.tts_speed

            # 1. Narrations
            # P2（2026-09-10 独立管线计划）：remotion_course 草稿路径同构分流——
            # _build_course_storyboard 一次产出结构化帧（P1 直渲/reparse 共用同一方法）。
            course_frames = None
            course_groups: list = []
            if mode == "generate" and payload.get("topic"):
                narrations = await script_mod.generate_narrations(
                    llm,
                    topic=payload["topic"],
                    n_scenes=_resolve_n_scenes(payload),
                    target_duration=payload.get("target_duration"),
                    style=payload.get("style"),
                    tts_speed=resolved_tts_speed,
                )
                title = payload["topic"]
            else:
                script_text = payload.get("script", "")
                if not script_text:
                    raise ValueError("No script or topic provided")
                if effective_type == "remotion_course":
                    # C1 同款：逐块刷 progress（5→50 区间，草稿路径口径）。
                    async def _on_course_parse(done: int, total: int) -> None:
                        if total > 0:
                            await progress(progress=5 + int(45 * done / total))

                    course_frames, course_groups = await self._build_course_storyboard(
                        script_text,
                        payload,
                        task_dir,
                        user_id=user_id,
                        on_progress=_on_course_parse,
                    )
                    title = script_text[:30]
                else:
                    _target_scenes = _target_scenes_for_fixed(payload, script_text)  # M3-1 + P1-E11: 显式 n_scenes 优先，其次 spf+target，兜底文长
                    # S4: 结构化解析优先（识别台词/画面/噪声），失败→normalize+split 兜底
                    narrations = None
                    if payload.get("ai_normalize_script", True) and len(script_text) > 80:
                        try:
                            # C1: 逐块刷 progress（5→20 区间），让长文档解析阶段持续更新 updated_at，
                            # 避免 reaper Predicate A（updated_at 过期）误杀。
                            async def _on_parse(done: int, total: int) -> None:
                                if total > 0:
                                    await progress(progress=5 + int(15 * done / total))

                            _parsed = await script_mod.parse_script_to_frames(llm, script_text, target_scenes=_target_scenes, on_progress=_on_parse)
                            if _parsed:
                                narrations = [f["narration"] for f in _parsed if f.get("narration")]
                        except Exception:
                            logger.warning("parse_script_to_frames failed", exc_info=True)
                    if not narrations:
                        if payload.get("ai_normalize_script", True) and len(script_text) > 80:
                            try:
                                script_text, _truncated = await script_mod.normalize_script(llm, script_text, style=payload.get("style", "口语"), target_scenes=_target_scenes)
                                if _truncated:
                                    payload["script_truncated"] = True
                            except Exception:
                                logger.warning("normalize_script failed, falling back to raw split", exc_info=True)
                        narrations = script_mod.split_fixed_script(script_text, target_count=_target_scenes)
                    title = script_text[:30]
            if course_frames is None and not narrations:
                raise ValueError("No narrations produced")
            await progress(progress=50)

            # 2. Storyboard plan (LLM generates image_prompt/search_term per frame)
            # C3 留痕（决策 5）：字幕生效来源 task > project > default
            resolved_subtitle_style = self._resolve_subtitle_style(payload.get("subtitle_style", "default"), payload.get("course_project_id"), user_id=user_id)
            _payload_subtitle = payload.get("subtitle_style")
            if _payload_subtitle and _payload_subtitle != "default" and _payload_subtitle != _STYLE_SENTINEL:
                payload["effective_subtitle_source"] = "task"
            elif resolved_subtitle_style != "default":
                payload["effective_subtitle_source"] = "project"
            else:
                payload["effective_subtitle_source"] = "default"
            cfg = StoryboardConfig(
                video_type=effective_type,
                width=width,
                height=height,
                voice_id=voice_id,
                tts_speed=resolved_tts_speed,
                style=payload.get("style", "default"),
                subtitle_style=resolved_subtitle_style,
                transition=payload.get("transition", "none"),
                transition_duration=payload.get("transition_duration", 0.5),
                bgm_volume=payload.get("bgm_volume", 0.15),
                bgm_ducking=payload.get("bgm_ducking", True),
                # P1-E8: 项目调色板接线（无项目 → None，worker 跳过 colorgrade）
                color_palette=_read_course_project_color_palette(self._storage_dir, payload.get("course_project_id"), user_id),
                # T3（2026-09-01）：任务级图文镜占比上限透传（None=默认 30%）。
                # 草稿路径与直渲路径两处组装形状相同，缺一不可——历史上草稿按钮漏传
                # secondsPerFrame 的同类事故就记录在这条链路（见 Task 19 注释）。
                graphic_ratio=payload.get("graphic_ratio"),
            )
            # C3: 统一仲裁（决策 3/5/20）——三处仲裁点共用 _resolve_effective_style
            # （generate/draft 同语义；显式脱离时项目前缀零注入）。
            effective_style = self._resolve_effective_style(payload, user_id=user_id)
            # P3-2/BUG-002（2026-09-14）：脚本标题（进 CoverPage 顶栏）剥标记——AI 解析路径
            # 的 title 可能携带【图片N】（topic 截断/script_text[:30] 均可能），复用 course_script。
            from .orchestration.course_script import _strip_structural_markers as _cs_strip

            storyboard = Storyboard(title=_cs_strip(title), config=cfg)
            if course_frames is not None:
                # P2：remotion_course 草稿同样直接装配结构化帧（与 P1 同构）。
                storyboard.frames.extend(course_frames)
                storyboard.scene_groups.extend(course_groups)
            else:
                for i, narration in enumerate(narrations):
                    storyboard.frames.append(StoryboardFrame(index=i, narration=narration))
            style_context = {"style_prefix": effective_style.style_prefix} if effective_style.style_prefix else None
            # S5（2026-08-26 调度表计划）：总开关透传（false = 纯现状逐帧，AC3 回滚位）；
            # shot_table_model 显式配置才传独立阶段 1 LLM（未配置 = 跟随 script LLM）。
            _st_llm = resolve_shot_table_llm(self._config)
            # P3：remotion_course 跳过 plan_course（双保险见 plan_course 内部早退）。
            if course_frames is None:
                await planner_mod.plan_course(
                    llm,
                    storyboard=storyboard,
                    style_context=style_context,
                    task_dir=task_dir,
                    # E8（2026-08-21）：逐帧化配置透传（prompt_chunk_size 默认 1 = 逐帧）。
                    chunk_size=self._config.prompt_chunk_size,
                    frame_concurrency=self._config.prompt_frame_concurrency,
                    shot_table_enabled=self._config.shot_table_enabled,
                    # M2：出场声明名册（跟随项目才有才传——空名册 = 契约零变化 + 旧桩窄签名兼容）
                    **({"entity_roster": self._planning_entity_roster(payload, user_id=user_id)} if self._planning_entity_roster(payload, user_id=user_id) else {}),
                    **({"shot_table_llm": _st_llm} if _st_llm else {}),
                )
            await progress(progress=90)

        # 3. Persist storyboard.json
        storyboard_path = task_dir / "storyboard.json"
        storyboard_path.write_text(
            json.dumps(storyboard.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        return PipelineDraft(storyboard=storyboard.to_dict())

    async def _render_from_draft(
        self,
        task_id: str,
        task_type: str,
        task_dir: Path,
        progress: ProgressUpdater,
        payload: dict,
        *,
        user_id: str = "default",
    ) -> dict | None:
        """Phase 2: render from draft (TTS → visual → compose)."""
        import shutil

        storyboard = Storyboard.from_dict(json.loads((task_dir / "storyboard.json").read_text(encoding="utf-8")))

        # Apply user edits
        frame_edits = payload.get("frame_edits")
        if frame_edits:
            for idx_str, edits in frame_edits.items():
                idx = int(idx_str)
                if 0 <= idx < len(storyboard.frames):
                    frame = storyboard.frames[idx]
                    for key, val in edits.items():
                        if val is not None and hasattr(frame, key):
                            setattr(frame, key, val)
                    # Phase 1（2026-08-17 方言层）：手改 image_prompt -> 清 visual_ir
                    # （P0-5：翻译器不得无视用户手改；该帧回退 legacy 透传）。
                    if edits.get("image_prompt") is not None:
                        frame.visual_ir = None

            # Persist edits via safe updater (read-merge-write)
            # 清锁条件与写入条件对齐（评审二轮 FU）：key 存在 AND 值非 None——
            # {"narration": null} 不触发写入（val is not None 守卫），也不得清锁，
            # 否则 narration 没变却把用户手改的精确 cue 时戳无谓重写。
            _narration_changed = any("narration" in edits and edits["narration"] is not None for edits in frame_edits.values())

            def _edits_updater(data: dict) -> None:
                for idx_str, edits in frame_edits.items():
                    idx = int(idx_str)
                    frames = data.get("frames", [])
                    if 0 <= idx < len(frames):
                        for key, val in edits.items():
                            if val is not None:
                                frames[idx][key] = val
                        # Phase 1：手改 image_prompt -> 清 IR（磁盘侧，与内存对称）
                        if edits.get("image_prompt") is not None:
                            frames[idx]["visual_ir"] = None
                # 清锁点①同源（评审 D-F2）：frame_edits 含 narration 变更时
                # （resume_draft ready+frame_edits 直达，绕过帧 PUT 清锁点①），
                # 用户手改字幕随旁白失效——磁盘+内存同步清锁，否则
                # _synthesize_audio 产新 TTS 但 SRT 被锁守卫压制，旧字幕残留。
                if _narration_changed:
                    data["subtitle_locked"] = False

            await self._update_storyboard(task_dir, _edits_updater)
            if _narration_changed:
                storyboard.subtitle_locked = False
        await progress(progress=10)

        effective_type = task_type
        if task_type == "ai_visual":
            effective_type = "ai_image"

        voice_id = storyboard.config.voice_id

        # TTS + audio concat + SRT
        # P1-E11: 透传 seconds_per_frame（修前草稿/渲染主路径 spf 是死代码）
        # 审查 Minor 2（2026-08-14）：与草稿路径对称——synth+merge 整段持合成锁，
        # 使 render 代不得与 draft 代的 [synth→merge] 交错（旧代覆写编辑帧竞态）。
        async with _synth_lock_guard(str(task_dir)):
            # T-E11：section_id 仅挂项目任务传（None 不传——窄签名桩零变化）
            audio_concat_path, subtitle_path = await self._synthesize_audio(
                storyboard, task_dir, voice_id, seconds_per_frame=payload.get("seconds_per_frame"), task_id=task_id, **({"section_id": payload.get("course_project_id")} if payload.get("course_project_id") else {})
            )
            # 实测时长落盘——recompose/后续读取拿到真值（此前只活内存，漂移根因环 3）
            await self._merge_audio_into_storyboard(storyboard, task_dir)
        await progress(progress=60)

        # BGM resolve
        bgm_id = payload.get("bgm")
        if bgm_id:
            bgm_src = _resolve_bgm_or_warn(bgm_id, user_id=user_id)
            if bgm_src:
                bgm_dest = str(task_dir / "bgm.mp3")
                shutil.copy2(bgm_src, bgm_dest)
                storyboard.config.bgm_path = bgm_dest

        await progress(progress=75)

        # Visual source
        if effective_type == "remotion":
            await progress(progress=90)
            from .pipelines.remotion_props import StoryboardResult

            return StoryboardResult(
                storyboard=storyboard,
                subtitle_path=subtitle_path,
                audio_dir=str(task_dir / "audio"),
                template_id=payload.get("template_id"),
            )

        frame_concurrency = self._config.effective_frame_concurrency(len(storyboard.frames))
        # remotion_course（2026-09-10 独立管线计划 P5a）：绕过 _has_mixed_sources 判定
        # 直走工厂——新流程 storyboard 源集合常为 {None, ai_image}，None 帧被算成
        # default_type 会误判 mixed 走 MixedVisualSource 死路；槽位过滤由
        # RemotionCourseVisualSource 单点承接。存量 mixed storyboard（全帧 ai_image/
        # graphic 写死的旧任务）不受影响——本分支只按 task 级 effective_type 判定。
        if effective_type == "remotion_course":
            visual = self._make_visual_source(
                "remotion_course",
                engine=payload.get("engine", "auto"),
                concurrency=frame_concurrency,
                image_provider=payload.get("image_provider"),
                video_provider=payload.get("video_provider"),
            )
        elif self._has_mixed_sources(storyboard):
            visual = self._make_visual_source_mixed(
                storyboard,
                concurrency=frame_concurrency,
                image_provider=payload.get("image_provider"),
                video_provider=payload.get("video_provider"),
            )
        else:
            visual = self._make_visual_source(
                effective_type,
                engine=payload.get("engine", "auto"),
                concurrency=frame_concurrency,
                image_provider=payload.get("image_provider"),
                video_provider=payload.get("video_provider"),
            )
        on_frame_done = self._make_on_frame_done(storyboard, task_dir, progress=progress, total=len(storyboard.frames))
        # Task 12: 项目角色 front_url 作 reference_image_paths 注入 ai_image（i2i 一致性）。
        # mixed source 由 MixedVisualSource.render 透传给 ai_image 子源。
        # C4：锚图专用参数通道（重渲染路径——跟随项目时重取项目最新锚图，决策 14）。
        _anchor = _style_anchor_image_from_project(getattr(self, "_storage_dir", None), payload, user_id, task_dir=task_dir)
        # C4：条件传参（同 _prepare_storyboard_video——有锚图才传）
        _anchor_kwargs = {"style_anchor_image_paths": [_anchor]} if _anchor else {}
        # M3：roster 通道（重渲染路径同款——帧级选择接管任务级整表）
        _char_roster = _build_character_roster(payload, storage_dir=getattr(self, "_storage_dir", None))
        _prop_roster = _build_prop_roster(getattr(self, "_storage_dir", None), payload, user_id)
        _roster_kwargs = {}
        if _char_roster or _prop_roster:
            _roster_kwargs = {"character_roster": _char_roster, "prop_roster": _prop_roster}
        _warn_if_image_provider_not_i2i(self._config, payload, task_dir, anchor_path=_anchor)
        # W2（2026-08-24 活性重构）：帧渲染阶段挂滴答（同 _prepare_storyboard_video 口径）。
        _render_budget = self._stage_budget_frame_render(len(storyboard.frames), concurrency=frame_concurrency)
        async with self._liveness(task_id, stage="frame_render", stage_budget=_render_budget):
            await visual.render(
                storyboard,
                only_frames=_frames_to_regenerate(storyboard.frames, task_type),  # G2: 类型感知续点——仅跳过期望类型的成功帧，预览图/冻结帧/缺失都重生
                on_frame_done=on_frame_done,
                task_dir=task_dir,
                reference_image_paths=_character_ref_paths_from_payload(payload),
                **_anchor_kwargs,
                **_roster_kwargs,
            )
        await progress(progress=90)

        from .worker_probe import decide_pipeline_version, probe_worker_capabilities

        worker_caps = await probe_worker_capabilities()
        pv = decide_pipeline_version(storyboard, worker_caps)

        # 出 payload 前校验（竞态兜底：本 run 合成期间音频文件若被另一代覆盖，按实测纠偏）
        if _reconcile_durations_with_audio(storyboard, task_dir):
            logger.warning("render: frame durations drifted from audio files — reconciled to probed values")
            _write_storyboard_srt(storyboard, subtitle_path)
            await self._merge_audio_into_storyboard(storyboard, task_dir)

        # remotion_course（2026-09-08 T4 修正：E2E 抓到 draft→render 路径漏分支——
        # 修前落 build_worker_payload → decide_pipeline_version 返回 v2_mixed →
        # remotion 池按 pipeline_version 路由进 ffmpeg mixed 管线，产出错管线成片）
        if task_type == "remotion_course":
            from .pipelines.remotion_course_props import build_remotion_props, build_remotion_worker_payload

            props = build_remotion_props(
                storyboard,
                task_dir=task_dir,
                user_id=user_id,
                course_project_id=payload.get("course_project_id"),
                task_payload=payload,
                storage_dir=getattr(self, "_storage_dir", None),
            )
            return PipelineCompleted(payload=build_remotion_worker_payload(storyboard, props))

        return PipelineCompleted(
            payload=build_worker_payload(
                storyboard,
                subtitle_path=subtitle_path,
                subtitle_ass=subtitle_path[:-4] + ".ass" if subtitle_path else None,
                audio_concat_path=audio_concat_path,
                output_format=payload.get("output_format", "mp4"),
                quality=payload.get("quality", "standard"),
                pipeline_version=pv,
            )
        )

    async def resume_draft(
        self,
        task_id: str,
        user_id: str,
        frame_edits: dict | None = None,
        quality_override: str | None = None,
    ) -> None:
        """Resume a draft task to start phase 2 rendering (called by POST /render)."""
        import asyncio

        from app.gateway._shared.task_framework.schemas import DRAFT

        task = await self.repo.get(task_id, user_id=user_id)
        # Accept both DRAFT (script2video/remotion blueprint confirm) and
        # STORY_READY (idea2video story confirm — M3.1). Both are user-confirmed
        # states that should advance to rendering.
        # P1-U2: ready 也接受——详情页「正式渲染」按钮把 preview 画质任务推进到标准渲染，
        # 修前对 ready 100% 409（detail.tsx 正式渲染入口）。
        if not task or task.status not in (DRAFT, STORY_READY, "failed", "ready"):
            raise ValueError("任务不存在，或当前状态不可渲染（仅草稿/分镜就绪/失败/已完成的任务可继续渲染）")
        # failed 续跑依赖磁盘 storyboard.json（reuse_blueprint 复用，跳过 LLM 脚本阶段）。
        # reaper 杀任务不删盘（_cleanup_task=pass），故 failed 通常仍有 storyboard；若缺失
        # （script 阶段就失败），无法续跑，提示删除重建。
        if task.status == "failed":
            task_dir = Path(self._storage_dir) / user_id / "tasks" / task_id
            if not (task_dir / "storyboard.json").exists():
                raise ValueError("failed 任务缺少 storyboard.json，无法续跑，请删除任务后重建")

        # P2-6: 渲染前拦截占位/空旁白（新增分镜忘改会被 TTS 逐字照念进成片）
        task_dir = Path(self._storage_dir) / user_id / "tasks" / task_id
        sb_file = task_dir / "storyboard.json"
        _sb_payload: dict | None = None
        if sb_file.exists():
            # review-①（2026-09-14）：同 recompose_task 的 BUG-008 v2-① 分流——
            # JSONDecodeError 是 ValueError 子类，render_from_draft 路由已 str(e) 透传，
            # 不拦会把原始解析错误文本（Expecting value…）泄漏进用户可见 409 detail。
            try:
                # 一次 off-loop 读出复用（voice gate / 预算固化 / 帧校验三处消费），
                # 避免 async 路径重复同步读大 JSON 阻塞事件循环。
                _sb_payload = json.loads(await asyncio.to_thread(sb_file.read_text, encoding="utf-8")) or {}
            except json.JSONDecodeError:
                raise ValueError("任务分镜数据损坏，无法继续渲染，请重新解析或重建任务") from None
            _frames = _sb_payload.get("frames", [])
            _validate_narrations_renderable(_frames)

        # review-①：payload 损坏同分流（用户向中文，不泄漏原文——docstring 契约②）
        try:
            payload = json.loads(task.payload)
        except json.JSONDecodeError:
            raise ValueError("任务数据损坏，无法继续渲染，请重建任务") from None

        # 边界收口（2026-10-19 P1-2）+ 边界热修复（2026-10-20 P1，PR#36 复审 Warning③）：
        # ViMax confirm 门覆盖**三源**——payload 显式、项目继承（course_project.voice_id
        # 经 _resolve_voice_id 三级链，修前漏拦直到引擎侧才炸）、盘面 storyboard.config
        # 存量。复用 _resolve_voice_with_vimax_gate（payload/项目/缺省解析三链 + preset
        # 门统一文案），盘面 config 源沿用 ensure——三源全在派发渲染（任何 LLM/付费）
        # 前同步可读拒绝（与 generate-audio 门对称），不静默换声。
        if task.task_type == "vimax":
            from app.gateway.video_gen.engines.vimax.voice_policy import ensure_vimax_voice_allowed

            _cfg_voice = (_sb_payload or {}).get("config", {}).get("voice_id")
            try:
                self._resolve_voice_with_vimax_gate(task.task_type, payload, user_id=user_id)
                ensure_vimax_voice_allowed(_cfg_voice)
            except ValueError as e:
                raise ValueError(str(e)) from None

        # 2026-10-20 Task 1（已拍板）：确认渲染固化全局镜头预算——显式
        # vimax_global_shot_budget > target_duration > 草稿 storyboard.total_duration。
        # UI 主路径 payload 只有 total_duration，渲染时 engine 才算会丢掉「已确认
        # 蓝图」的时长基准；在派发前写回 payload 让 engine/pipeline 同源消费。
        # 解析失败只 warning（engine 兜底/单批），不阻断渲染派发。
        if task.task_type == "vimax" and payload.get("pipeline") != "idea2video":
            from app.gateway.video_gen.engines.vimax.storyboard_budget import resolve_vimax_budget

            _sb_duration = (_sb_payload or {}).get("total_duration")
            try:
                _resolved = resolve_vimax_budget(
                    explicit_budget=payload.get("vimax_global_shot_budget"),
                    target_duration=payload.get("target_duration"),
                    storyboard_duration=_sb_duration,
                )
            except Exception:
                logger.warning("vimax budget resolution failed for task %s; keeping single batch", task_id, exc_info=True)
                _resolved = None
            if _resolved is not None:
                payload["vimax_global_shot_budget"] = _resolved.shot_budget
                payload["vimax_budget_source"] = _resolved.source
            else:
                logger.warning("vimax budget unresolved for task %s (no explicit/target/storyboard duration); keeping single batch", task_id)

        # Clear Worker segment cache when quality changes to avoid stale reuse
        if quality_override and quality_override != payload.get("quality"):
            import shutil

            task_dir = Path(self._storage_dir) / user_id / "tasks" / task_id
            segments_dir = task_dir / "segments"
            if segments_dir.exists():
                shutil.rmtree(segments_dir)
                logger.info("Cleared segment cache for task %s: quality %s → %s", task_id, payload.get("quality"), quality_override)
            payload["quality"] = quality_override

        payload["frame_edits"] = frame_edits or {}
        payload["auto_render"] = True
        # A1 (决策3): reuse the plan-confirmed storyboard.json instead of regenerating
        # narrations — prevents generate-mode blueprint drift on the render-after-plan path.
        payload["reuse_blueprint"] = True
        # P1-U9: recompose_only 是 recompose_task 的一次性标记（recompose_task 持久化进 payload）。
        # resume 是全量重跑——摘除并持久化，否则失败后 resume 永远走 recompose 早退分支卡死
        # （对照 re_render_task 内 pop recompose_only 的行为）。
        payload.pop("recompose_only", None)
        await self.repo.update(task_id, payload=json.dumps(payload, ensure_ascii=False))

        def _on_done(t: asyncio.Task) -> None:
            if t.exception() and not t.cancelled():
                logger.error("resume_draft task %s failed: %s", task_id, t.exception())

        def _factory() -> asyncio.Task:
            t = asyncio.create_task(self._wrap_pipeline(task_id, user_id, task.task_type, payload))
            t.add_done_callback(_on_done)
            return t

        if not self._register_active_task(task_id, _factory):
            raise ValueError("任务正在处理中，请等待当前渲染完成后再试")

    async def _generate_frame_preview(self, task_id: str, frame_index: int, user_id: str) -> str:
        """生成单帧预览图（draft 阶段视觉感知）。调 ai_image provider 用 image_prompt 生图，
        把 visual_url 写进 storyboard.json 的对应 frame，返回 url。

        I: 与 _regenerate_frame 对齐——补 reference_image_paths（角色一致性）+ size（比例），
        使预览图与最终重生图一致。正式渲染时 AIImageVisualSource.render 仍无条件覆盖 visual_url。
        """
        task = await self.repo.get(task_id, user_id=user_id)
        if task is None:
            raise ValueError("Task not found")
        task_dir = (Path(self._storage_dir) / user_id / "tasks" / task_id).resolve()
        validate_frame_previewable(task_dir, frame_index)
        # 2026-07-29：最内层上传守卫——即便选帧层失效（旧前端、直接调 API），
        # 也不会用 AI 图覆盖用户上传的画面，也不烧生图配额。
        assert_frame_previewable_not_upload(task_dir, frame_index)
        storyboard = Storyboard.from_dict(json.loads((task_dir / "storyboard.json").read_text(encoding="utf-8")))
        frame = next((f for f in storyboard.frames if f.index == frame_index), None)

        payload = json.loads(task.payload) if isinstance(task.payload, str) else (task.payload or {})
        _inject_project_character_context(self._storage_dir, payload, user_id)
        # C3 边界②：单帧预览不是仲裁点——旧任务缺 effective_style_source 时就地补算
        # （幂等），供 C4 锚图 helper 与门控消费。
        if "effective_style_source" not in payload:
            self._resolve_effective_style(payload, user_id)
        # C4 预览点分叉：不走 visual.render（直调 provider）——锚图在 service 层并入
        # refs（角色 > 锚图），随后与生成路径同样过 apply_ref_cap（C10）。
        _anchor = _style_anchor_image_from_project(getattr(self, "_storage_dir", None), payload, user_id, task_dir=task_dir)
        _warn_if_image_provider_not_i2i(self._config, payload, task_dir, anchor_path=_anchor)
        refs = _character_ref_paths_from_payload(payload)
        # M3：roster 通道生效 → 预览 refs 改走帧级选择（与正式渲染同语义；帧对象在手）
        _char_roster = _build_character_roster(payload, storage_dir=getattr(self, "_storage_dir", None))
        _prop_roster = _build_prop_roster(getattr(self, "_storage_dir", None), payload, user_id)
        if _char_roster or _prop_roster:
            from .visual_sources.ai_image import _frame_character_ref_paths, _frame_prop_ref_paths

            _prop_refs = [p for p in _frame_prop_ref_paths(frame, _prop_roster) if Path(p).exists()]
            _char_refs_pv = _frame_character_ref_paths(frame, _char_roster) or []
            refs = _char_refs_pv + _prop_refs
        if _anchor:
            refs = (refs or []) + [_anchor]
        refs, _anchor_cap = apply_ref_cap(refs)
        if _anchor_cap.get("truncated"):
            # C10：预览叉命中截断同样记 sidecar（与 ai_image 合并点同款；本帧 frame_index）
            record_ref_injection_sidecar(
                task_dir,
                frame_index=frame_index,
                injected_refs=_anchor_cap.get("injected"),
                truncated=_anchor_cap.get("truncated"),
                reason=_anchor_cap.get("reason", ""),
            )
        # X12（2026-08-28 用户拍板）：预览点同款带名指代声明（与 fill 同模板、同 1:1 纪律）
        _decl = ""
        if _char_roster or _prop_roster:
            from .visual_sources.ai_image import build_image_ref_declaration

            _decl = build_image_ref_declaration(
                frame,
                _char_roster,
                _prop_roster,
                char_refs=_char_refs_pv,
                prop_kept=_prop_refs,
                has_anchor=bool(_anchor),
                final_refs=refs,
            )
        cfg = storyboard.config
        size = f"{cfg.width}x{cfg.height}"
        # graphic-native-text（2026-08-17）：graphic 帧预览=最终所见——注入图内文字指令后
        # 走 AI 生图（文字由模型画进画面；与 fill 同一注入器，预览/成片一致）。
        # T5b（拍板⑦）：与 fill 同一任务级版式卡（预览/成片版式一致）。
        preview_prompt = frame.image_prompt or ""
        if getattr(frame, "visual_source", None) == "graphic" and getattr(frame, "graphic_spec", None):
            from .visual_sources.graphic_text import inject_graphic_text

            preview_prompt = inject_graphic_text(preview_prompt, frame.graphic_spec, getattr(storyboard, "graphic_layout_card", "") or "")
        if _decl:
            preview_prompt = _decl + preview_prompt  # X12：指代声明前置（ref 图分工先于画面描述）
        # E2→T2（2026-08-31 并发化）：并发闸——限 generate_image 同时进数
        # ≤ config preview_all_concurrency（默认 12；E2 3 路保守值已被实测推翻）。
        # E7（2026-08-21）：预览图跟随任务级生图模型——make_ai_image(payload.image_provider)
        # 与正式渲染同源（AC6：选 seedream-360 的任务预览图确为该模型所出）。
        # None → make_ai_image 返回 config 默认实例，行为与修前完全一致。
        async with _get_preview_sem(getattr(self._config, "preview_all_concurrency", None)):
            preview_provider = self._providers.make_ai_image(payload.get("image_provider"))
            image_ref = await preview_provider.generate_image(preview_prompt, size=size, reference_image_paths=refs or None)
        url = image_ref.url

        # P1-E7: 预览图立即本地化（对齐生成路径 F4），消除 TOS 临时 URL 过期 →
        # 渲染/recompose 下载失败黑场；写 local_visual_path 后 worker local 优先复用。
        # 与加固 H3 协调（v2§2）：本方案「写 local」取代其「清 local」语义——预览即新图，
        # 无需让 recompose 重下载 visual_url；dirty 标记语义两案一致。
        from types import SimpleNamespace as _SimpleNamespace

        from .visual_sources.ai_image import _localize_image

        _rel = await _localize_image(url, _SimpleNamespace(index=frame_index), task_dir)

        _frame_index = frame_index
        _url = url
        _local_rel = _rel

        def _updater(data: dict) -> None:
            for f in data.get("frames", []):
                if f.get("index") == _frame_index:
                    f["visual_url"] = _url
                    f["visual_type"] = "image"
                    # P1-E7: 本地化成功 → 写新路径（worker local 优先用新预览图）；
                    # 失败（_rel=None）→ 不动旧 local，visual_url 已是新版供 worker 兜底下载。
                    if _local_rel:
                        f["local_visual_path"] = _local_rel
                    f.pop("preview_error", None)  # 重生成功清除旧错误
                    # preview_state 状态机（2026-09-07）：与 visual_url 同一次 _update_storyboard
                    # 落盘——前端同一次轮询拿到 done+新 URL，visualVersion 变化触发 <img> 重取。
                    f["preview_state"] = "done"

        await self._update_storyboard(task_dir, _updater)
        # H3: ready/failed 态的预览就是「换图」——标 dirty 让「重新合成」增量带入成片。
        # draft 态不标（首渲走全量，无 segment 可增量；标了只会泄漏成无谓增量）。
        # getattr 容错：真实 Task 行必有 status；手搓 stub（SimpleNamespace）可能缺。
        if getattr(task, "status", None) in ("ready", "failed"):
            await self.repo.add_dirty_frame(task_id, frame_index)
        return url

    async def _write_preview_error(self, task_id: str, frame_index: int, user_id: str, exc: Exception) -> None:
        """预览失败把错误写进 storyboard.json 对应帧（前端轮询展示；下次生成成功时清除）。

        GET storyboard 返回磁盘原始 dict，额外键 preview_error 对前端可见。
        """
        task_dir = (Path(self._storage_dir) / user_id / "tasks" / task_id).resolve()
        msg = f"{type(exc).__name__}: {exc}"[:200]

        def _updater(data: dict) -> None:
            for f in data.get("frames", []):
                if f.get("index") == frame_index:
                    f["preview_error"] = msg
                    # preview_state 状态机（2026-09-07）：与 preview_error 同批写入（前端结案双保险）
                    f["preview_state"] = "error"

        await self._update_storyboard(task_dir, _updater)

    async def _set_preview_state(self, task_id: str, frame_index: int, user_id: str, state: str, error: str | None = None) -> None:
        """单帧预览状态写 storyboard.json（2026-09-07 preview_state 状态机，对齐 _set_regen_state）。

        state: "running" | "done" | "error"。running/done 清掉旧 preview_error（新一次尝试
        或成功结案，旧错误失效）；error 附截断 200 字消息。GET storyboard 返回磁盘原始 dict，
        额外键 preview_state/preview_error 对前端可见。调用点（router 层 _preview_frame_bg）
        手里没有 task_dir（不同于 regen 路由），故按 _write_preview_error 模式内部自行解析。
        条目 35（2026-09-12）：同时写 preview_state_at（失效判定的时间戳依据）。
        """
        task_dir = (Path(self._storage_dir) / user_id / "tasks" / task_id).resolve()
        _now = datetime.now(UTC).isoformat()

        def _updater(data: dict) -> None:
            for f in data.get("frames", []):
                if f.get("index") == frame_index:
                    f["preview_state"] = state
                    f["preview_state_at"] = _now
                    if state == "error":
                        f["preview_error"] = (error or "")[:200]
                    else:
                        f.pop("preview_error", None)

        await self._update_storyboard(task_dir, _updater)

    async def _expire_stale_running_state(self, task_id: str, frame_index: int, user_id: str, *, state_key: str, error_key: str, message: str) -> None:
        """条目 35：预览/重生 POST 入口的失效重置——>10min 的 running 落盘置 error。

        只在写入口调用（GET 高频轮询只 fold 不落盘）；storyboard 缺失/损坏/帧缺失
        静默跳过（新一次写入会重建状态）。best-effort：异常吞掉，不得阻塞入口。
        """
        try:
            task_dir = (Path(self._storage_dir) / user_id / "tasks" / task_id).resolve()
            sb_path = task_dir / "storyboard.json"
            data = json.loads(sb_path.read_text(encoding="utf-8"))
            frames = data.get("frames") if isinstance(data, dict) else None
            if not isinstance(frames, list):
                return
            frame = next((f for f in frames if isinstance(f, dict) and f.get("index") == frame_index), None)
            if frame is None or not _frame_state_is_stale(frame, state_key, f"{state_key}_at"):
                return

            def _updater(data: dict) -> None:
                for f in data.get("frames", []):
                    if f.get("index") == frame_index and _frame_state_is_stale(f, state_key, f"{state_key}_at"):
                        f[state_key] = "error"
                        f[error_key] = message
                        f[f"{state_key}_at"] = datetime.now(UTC).isoformat()

            await self._update_storyboard(task_dir, _updater)
        except Exception:  # noqa: BLE001
            logger.warning("expire stale %s failed (task %s frame %d)", state_key, task_id, frame_index, exc_info=True)

    async def _set_regen_state(self, task_dir: Path, frame_index: int, state: str, error: str | None = None) -> None:
        """单帧重生状态写 storyboard.json（前端轮询释放 pending；与 _write_preview_error 同机制）。

        state: "running" | "done" | "error"。running/done 清掉旧 regen_error（新一次尝试
        或成功结案）；error 附截断 200 字消息。GET storyboard 返回磁盘原始 dict，
        额外键 regen_state/regen_error 对前端可见。
        条目 35（2026-09-12）：同时写 regen_state_at（失效判定的时间戳依据）。
        """
        _now = datetime.now(UTC).isoformat()

        def _updater(data: dict) -> None:
            for f in data.get("frames", []):
                if f.get("index") == frame_index:
                    f["regen_state"] = state
                    f["regen_state_at"] = _now
                    if state == "error":
                        f["regen_error"] = (error or "")[:200]
                    else:
                        f.pop("regen_error", None)

        await self._update_storyboard(task_dir, _updater)

    async def _regenerate_frame(
        self,
        task_id: str,
        user_id: str,
        frame_index: int,
        task_dir: Path,
        mode: str = "all",
    ) -> dict:
        """Regenerate visual for a single frame in a completed task.

        Refreshes this frame's on-disk assets (visual source + per-frame TTS +
        the merged audio.mp3 + subtitle.srt) so a subsequent POST /recompose
        picks up the new state. Does NOT compose the video itself — composition
        is the job of /recompose (B0 recompose_only path). The legacy
        compose_payload.json write was removed (it had no consumer).

        mode（2026-07-27 音视频分离）：
          - "all"（默认）：画面+音频都重生（现行为）。
          - "audio_only"：只跑 TTS 段——改讲解词后重配音，画面/复用帧同步完全不动。
          - "visual_only"：只跑画面段——audio.mp3/subtitle.srt 保持原样（字节不变）。
        """
        import shutil

        task = await self.repo.get(task_id, user_id=user_id)
        if task is None:
            raise ValueError("Task not found")
        if task.status not in ("ready", "failed"):
            raise ValueError("Task must be ready or failed to regenerate a frame")

        storyboard = Storyboard.from_dict(json.loads((task_dir / "storyboard.json").read_text(encoding="utf-8")))
        if frame_index < 0 or frame_index >= len(storyboard.frames):
            raise ValueError(f"frame_index {frame_index} out of range")

        # ── 跨段绑定（2026-07-27 模式守卫前提升）─────────────────────────────
        # frame/payload/effective_type/两个 path 在画面段、音频段、BGM 段与 return
        # 跨段引用——mode 守卫会让某段整体跳过，这些绑定必须在守卫外先算好，否则
        # audio_only 下 BGM 段读 payload、return 读 effective_type 直接 NameError。
        # 全是廉价的非变更读取/字符串赋值，提升零副作用。
        frame = storyboard.frames[frame_index]
        payload = json.loads(task.payload)
        effective_type = task.task_type
        if task.task_type == "ai_visual":
            effective_type = "ai_image"
        # M7: 混合源 storyboard——帧级 visual_source override 优先（与主渲染路径
        # _has_mixed_sources → MixedVisualSource 的帧级分派一致）；否则 ai_video
        # override 帧会被重生成为图片。非 ai_image/ai_video/graphic 的 override
        # （如存量 remotion）维持 task 级类型（segments-type 任务不会出现，防御性忽略）。
        # graphic-native-text（2026-08-17）：graphic 帧按 ai_image 重生——fill 会对其
        # 注入图内文字指令（spec 有效时），与整片渲染同路径。
        frame_source = getattr(frame, "visual_source", None)
        # 2026-09-11 二轮审查 M1：remotion_course **不做帧级 override**——帧源改派裸
        # AIImage/AIVideoVisualSource 会架空 RemotionCourseVisualSource：(a) 裸源覆盖率
        # 闸按全 storyboard 的 ai_image 帧计分母（不认 only_frames），上传替换帧残留
        # visual_source 时单帧重生成功仍被误杀 "coverage too low"； (b) 裸源消费
        # roster/锚图/参考图注入，与批量渲染（课程源显式忽略）行为不一致。课程源
        # render 支持 only_frames，帧级与批量同一条路径。
        if task.task_type == "remotion_course":
            pass  # 恒走 remotion_course 工厂分支（P6 守卫仍拦截嵌入/上传帧）
        elif frame_source in ("ai_image", "ai_video"):
            effective_type = frame_source
        elif frame_source == "graphic":
            # graphic-native-text（2026-08-17）：图内文字帧按 ai_image 重生（fill 自动注入文字指令）
            effective_type = "ai_image"
        audio_concat_path = str(task_dir / "audio.mp3")
        subtitle_path = str(task_dir / "subtitle.srt")

        if mode != "audio_only":
            # P6 守卫（2026-09-10 独立管线计划）：remotion_course 的嵌入图/用户上传帧
            # 拒绝视觉重生——下方 P0-A1 清空逻辑（非 ai_video 无条件清 local_visual_path）
            # 会误删素材（嵌入图只写 local_visual_path，正是被清对象）。只能替换/删除
            # （upload-visual），与编辑器「嵌入/上传帧不提供重新生成配图」对齐。
            if getattr(task, "task_type", "") == "remotion_course" and frame.local_visual_path:
                _lv = str(frame.local_visual_path)
                if _lv.startswith("visuals/embedded/") or _lv.startswith(_USER_VISUAL_PREFIX):
                    raise ValueError("嵌入/上传素材帧不支持重新生成配图，请替换或删除后再试")
                # 条目 3（2026-09-12 缺陷批）：残留旧图帧「重新生成视频」静默 no-op 守卫
                # （判定矩阵见 residual_visual_regen_block_reason；两层同谓词同文案——
                # 本层 ValueError 进后台任务被捕获落 regen_state=error 兜底，400 只能来自
                # router 同步预检）。audio_only 不进本分支（mode 守卫已排除）。
                _block_reason = residual_visual_regen_block_reason(getattr(task, "task_type", ""), frame)
                if _block_reason:
                    raise ValueError(_block_reason)
            # Clear the frame's visual to force re-acquisition
            # H7: 捕获旧 local 路径——image_reuse 复用帧共享此文件，渲染成功后要同步它们。
            old_local_visual_path = frame.local_visual_path
            frame.visual_url = None
            # P0-A1: 用户上传/替换的素材必须清除——否则 storyboard.json 里
            # local_visual_path 仍指旧文件，worker 合成 local 优先于 url，
            # 新生成的画面被静默忽略（先上传后重生 = 白花一次生成费）。
            #
            # 2026-08-03（P0-1）：但**只清用户上传的**，AI 预览图要留。
            # 修前无条件清空，把用户点「预览」看到的那张图也清了 → Task 5 的构图继承
            # 条件（visual_type=="image" and local_visual_path）不成立 → 单帧「重新生成」
            # 退化成纯 t2v，成片构图与预览图不一致；而「批量渲染/重新合成」路径不清空、
            # 能继承。同一帧两个操作行为不一致，正是用户报的「所见非所得」。
            #
            # 判据复用 frame_has_user_visual 的 visuals/frame_ 前缀（_USER_VISUAL_PREFIX）：
            # 不能用「有 local_visual_path」——那会把 gateway 本地化的 AI 图
            # （visuals/img_NNNN / shot_NNNN）误判成用户素材。
            #
            # **仅 ai_video 保留**：ai_image 的 render 在生成失败时不写占位、也不覆盖
            # local_visual_path（ai_image.py 只在 visual_url 存在时才本地化），留着旧预览图
            # 会让「重生失败」看起来像「重生成功」——把上一张图当新结果交付。
            # ai_video 无此风险：失败走 _make_placeholder_clip 覆写 local_visual_path。
            _is_user_upload = bool(frame.local_visual_path) and str(frame.local_visual_path).startswith(_USER_VISUAL_PREFIX)
            # 2026-09-11 二轮审查 M1：remotion_course 不再翻转 effective_type 后，ai_video
            # 帧的重生 effective_type == "remotion_course"——i2v 首帧保留条件按帧源判。
            _is_video_regen = effective_type == "ai_video" or (task.task_type == "remotion_course" and frame_source == "ai_video")
            _keep_preview_for_i2v = _is_video_regen and not _is_user_upload and frame.visual_type == "image" and bool(frame.local_visual_path)
            if not _keep_preview_for_i2v:
                frame.visual_type = None
                frame.local_visual_path = None
            # else: 保留 AI 预览图（visual_type="image" + local_visual_path）供 Task 5
            #   作 i2v 首帧。render 成功后 visual_type 会被设回 "video"，不残留 "image"。

            # Re-acquire visual for this frame only (using only_frames filter)
            # Task 12: 单帧重生也注入项目角色 ref（重生本帧时仍用项目三视图保一致）。
            _inject_project_character_context(self._storage_dir, payload, user_id)
            visual = self._make_visual_source(
                effective_type,
                engine=payload.get("engine", "auto"),
                image_provider=payload.get("image_provider"),
                video_provider=payload.get("video_provider"),
            )
            on_frame_done = self._make_on_frame_done(storyboard, task_dir)
            # C3 边界② + C4：视觉重生非仲裁点——旧任务缺键就地补算（幂等）；锚图走
            # 专用参数通道（脱离/无项目/开关关 → None）。
            if "effective_style_source" not in payload:
                self._resolve_effective_style(payload, user_id)
            _anchor = _style_anchor_image_from_project(getattr(self, "_storage_dir", None), payload, user_id, task_dir=task_dir)
            # C4：条件传参（同上——有锚图才传）
            _anchor_kwargs = {"style_anchor_image_paths": [_anchor]} if _anchor else {}
            # M3：roster 通道（视觉重生同款——帧级选择接管任务级整表）
            _char_roster = _build_character_roster(payload, storage_dir=getattr(self, "_storage_dir", None))
            _prop_roster = _build_prop_roster(getattr(self, "_storage_dir", None), payload, user_id)
            _roster_kwargs = {}
            if _char_roster or _prop_roster:
                _roster_kwargs = {"character_roster": _char_roster, "prop_roster": _prop_roster}
            _warn_if_image_provider_not_i2i(self._config, payload, task_dir, anchor_path=_anchor)
            await visual.render(
                storyboard,
                only_frames=[frame_index],
                on_frame_done=on_frame_done,
                task_dir=task_dir,
                reference_image_paths=_character_ref_paths_from_payload(payload),
                **_anchor_kwargs,
                **_roster_kwargs,
            )

            # H7: image_reuse 复用帧跟随——它们共享本帧的 local 文件（ai_image 失败复用上帧，
            # visual_sources/ai_image.py:146-158）。本帧重生后：同扩展名=共享文件内容已被
            # 覆盖（复用帧缓存 segment 仍旧图 → 标 dirty 让增量 recompose 重建）；
            # 扩展名变化=复用帧 local_visual_path 成孤儿（指向旧内容文件 → 改写为新路径）。
            if old_local_visual_path and frame.local_visual_path:
                _new_local = frame.local_visual_path
                _old_local = old_local_visual_path
                reuse_indices: list[int] = []

                def _reuse_updater(data: dict) -> None:
                    for f in data.get("frames", []):
                        if f.get("index") != frame_index and f.get("local_visual_path") == _old_local:
                            f["local_visual_path"] = _new_local
                            reuse_indices.append(f["index"])

                await self._update_storyboard(task_dir, _reuse_updater)
                for fr in storyboard.frames:
                    if fr.index != frame_index and fr.local_visual_path == old_local_visual_path:
                        fr.local_visual_path = frame.local_visual_path
                for _ri in reuse_indices:
                    await self.repo.add_dirty_frame(task_id, _ri)

        # P1#2: 本段（TTS + concat + SRT）必须在该 task_dir 的合成锁保护下执行——
        # 与 recompose/草稿音频并发时曾撕裂 audio.mp3（2026-07-22 审计）。
        #
        # ⚠️ 2026-08-02 死锁修复：改用 `_synth_lock_guard`（同协程重入则复用）。
        # 起因：生产调用者 routers/video_gen.py:_regenerate_frame_bg 已持同一把锁，
        # 而此处原本无条件再取；asyncio.Lock **不可重入**（标准库无异步 RLock）——
        # 内层永久等待外层释放、外层又等着内层返回 → 死锁。
        # 用户症状：点「重生配音」接口 202 无报错，但 regen_state 永远停在
        # running，前端转圈直到 300s 超时兜底。
        #
        # ⚠️ 曾用 `Lock.locked()` 判断「是否已持有」，那是**错的**：它只能回答
        # 「有人持锁吗」。同一把锁另有 4 个 `_synthesize_audio` 持有点与本路径并行，
        # 「别人持锁」会被误判成「我已持锁」→ 跳过加锁 → 并发写 audio.mp3。
        # `_synth_lock_guard` 用 ContextVar 按协程登记，只在**本协程**已持锁时才复用。
        # 回归护栏见 tests/test_regenerate_frame_deadlock.py。
        if mode != "visual_only":
            async with _synth_lock_guard(str(task_dir)):
                # TTS 仅在该帧 narration 内容变化时重跑（CAS 命中即复用）——P0-A2：
                # 旧实现每次单帧重生都无条件重跑 TTS：浪费一次调用 + 慢 2-10s，且把 TTS
                # 故障面引入纯画面修改（TTS 失败 → 400 → 视觉已落盘但 dirty 未标，用户困死）。
                from .orchestration import tts as tts_mod

                voice_id = storyboard.config.voice_id
                audio_dir = task_dir / "audio"
                audio_dir.mkdir(parents=True, exist_ok=True)
                # CAS naming: md5(narration|voice|speed) —— 换音色/速度不再同名覆盖
                content_hash = _audio_cache_key(frame.narration, voice_id, storyboard.config.tts_speed)
                out = audio_dir / f"{content_hash}.mp3"

                if out.exists() and out.stat().st_size > 0:
                    # narration 未变（同一 CAS 文件）→ 复用现有音频，不调 TTS API。
                    # 但 audio_path/duration/srt 可能属于"改旁白又改回"前的旧版本（PUT 只改文本字段，
                    # 不动音频元数据）：用 ffprobe 探测真实时长校准——漂移才重 concat/重写 SRT；
                    # audio_path 无条件落盘纠偏（评审点②）。
                    probed = await asyncio.to_thread(_get_audio_duration, str(out))
                    if probed <= 0:
                        # R4: 探测失败（文件损坏/ffprobe 不可用）按"信任现值"处理，但留日志——
                        # 不 raise：ffprobe 缺失的环境（如精简镜像）里 raise 会把主路径打死；
                        # 真损坏的文件会在后续 concat 阶段响亮失败（非静默）。
                        logger.warning("ffprobe failed for CAS audio %s; trusting storyboard duration", out)
                    audio_drifted = probed > 0 and abs(probed - (frame.duration or 0)) > 0.05
                    frame.audio_path = str(out)
                    if audio_drifted:
                        frame.duration = probed
                        frame.speech_duration = probed  # 此路径无 spf 补静音，与 else 分支一致
                        frame.srt = ""  # 旧 srt 属于上一版旁白文本，清空让 _write_storyboard_srt 按现 narration 合成
                        storyboard.recompute_duration()

                    def _cas_updater(data: dict) -> None:
                        for f in data.get("frames", []):
                            if f.get("index") == frame_index:
                                f["audio_path"] = frame.audio_path
                                f["duration"] = frame.duration
                                f["srt"] = frame.srt
                                f["speech_duration"] = frame.speech_duration
                                break

                    await self._update_storyboard(task_dir, _cas_updater)

                    if audio_drifted:
                        await run_ffmpeg(_concat_audio, [f.audio_path for f in storyboard.frames], audio_concat_path, expected_seconds=sum(f.duration for f in storyboard.frames))
                        _write_storyboard_srt(storyboard, subtitle_path)
                else:
                    try:
                        # T-E11：section_id 仅挂项目任务传（None 不传，窄签名桩零变化）
                        _tts_section_id = payload.get("course_project_id")
                        r = await tts_mod.synthesize(
                            frame.narration,
                            voice_id=voice_id,
                            speed=storyboard.config.tts_speed,
                            output_path=str(out),
                            fallback_voice=self._config.tts_fallback_voice,
                            **({"section_id": _tts_section_id} if _tts_section_id else {}),
                        )
                        if not Path(r.audio_path).exists():
                            raise RuntimeError(f"TTS output missing: {r.audio_path}")
                    except Exception:
                        # 评审轮3（B4 残留）：视觉已重生并落盘（on_frame_done 已执行）——TTS 失败
                        # 会把整个操作打成 400，若不标 dirty，新画面被静默吞掉（「重新合成」按钮
                        # 不亮，用户困死）。标 dirty 让用户可重试 regenerate 或先合成新画面。
                        # add_dirty_frame 去重，成功后路由层再标一次也无害。
                        if frame.visual_url or frame.local_visual_path:
                            await self.repo.add_dirty_frame(task_id, frame_index)
                        raise
                    # P2-14: 降级产出按实际音色 CAS 键归位（同 _synthesize_audio 的 synth_one）——
                    # 否则错误音色顶着请求音色键被之后同文本同音色请求永久命中。先归位再查存在性：
                    # 并发重生同旁白帧时另一请求可能已完成归位——src 缺失但 actual_out 在 → adopt。
                    if r.actual_voice_id and r.actual_voice_id != voice_id:
                        actual_hash = _audio_cache_key(frame.narration, r.actual_voice_id, storyboard.config.tts_speed)
                        actual_out = audio_dir / f"{actual_hash}.mp3"
                        src = Path(r.audio_path)
                        if src != actual_out:
                            if src.exists():
                                src.replace(actual_out)
                            elif not actual_out.exists():
                                raise RuntimeError(f"TTS output missing: {r.audio_path}")
                            r = tts_mod.TTSResult(audio_path=str(actual_out), srt=r.srt, duration=r.duration, actual_voice_id=r.actual_voice_id)
                        logger.warning("TTS degraded: requested %s, actual %s — stored under actual-voice CAS key", voice_id, r.actual_voice_id)
                        # 决策 3（2026-07-31）：音色降级留痕。降级本身是有意的
                        # （配额耗尽/限流不该让整批 draft audio 崩掉），但用户
                        # 听得出中途换声、界面却零解释。此处是唯一能同时看到
                        # 「请求音色 vs 实际音色」和 task_dir 的地方，且覆盖所有
                        # 降级成因（配额/传输/5xx/access key 缺失）。
                        record_degradation(
                            task_dir,
                            kind=KIND_VOICE_FALLBACK,
                            detail=f"请求 {voice_id}，实际 {r.actual_voice_id}",
                            frame_index=frame.index,
                            requested_voice=voice_id,
                            actual_voice=r.actual_voice_id,
                        )
                    if not Path(r.audio_path).exists():
                        raise RuntimeError(f"TTS output missing: {r.audio_path}")
                    frame.audio_path = r.audio_path
                    frame.duration = r.duration if r.duration > 0 else max(len(frame.narration) * 0.15, 1.0)
                    frame.speech_duration = frame.duration  # 字幕配比基准（此路径无 spf 补静音）
                    frame.srt = r.srt
                    storyboard.recompute_duration()

                    # Persist the regenerated audio metadata to storyboard.json via the unified
                    # read-merge-write updater (Lock + atomic write) so concurrent writes — e.g.
                    # SSE visual_url updates from on_frame_done — are not clobbered. Without this
                    # the /audio?frame=N endpoint and the timeline serve the pre-regeneration
                    # audio/duration, because the on-disk storyboard.json would still hold the
                    # pre-regeneration values (POST /recompose reads storyboard.json directly).
                    def _audio_updater(data: dict) -> None:
                        # 清锁点②（2026-07-27 字幕编辑）：CAS miss 合成新 TTS → 新 SRT，
                        # 用户手改字幕随旁白失效（CAS hit 分支 narration 未变，不清锁）。
                        data["subtitle_locked"] = False
                        for f in data.get("frames", []):
                            if f.get("index") == frame_index:
                                f["audio_path"] = frame.audio_path
                                f["duration"] = frame.duration
                                f["srt"] = frame.srt
                                f["speech_duration"] = frame.speech_duration
                                break

                    await self._update_storyboard(task_dir, _audio_updater)
                    # 与 _audio_updater 的磁盘清锁对齐——_update_storyboard 是
                    # read-merge-write（不回写内存对象），不同步会让下面
                    # _write_storyboard_srt 的锁守卫误跳，旧用户字幕残留进成片
                    # （评审 D-F1：锁+T14 改音色+重生配音 组合路径）。
                    storyboard.subtitle_locked = False

                    await run_ffmpeg(_concat_audio, [f.audio_path for f in storyboard.frames], audio_concat_path, expected_seconds=sum(f.duration for f in storyboard.frames))
                    _write_storyboard_srt(storyboard, subtitle_path)

        # BGM
        bgm_id = payload.get("bgm")
        if bgm_id:
            bgm_src = _resolve_bgm_or_warn(bgm_id, user_id=user_id)
            if bgm_src:
                bgm_dest = str(task_dir / "bgm.mp3")
                shutil.copy2(bgm_src, bgm_dest)
                storyboard.config.bgm_path = bgm_dest

        if effective_type == "remotion":
            return build_worker_payload(
                storyboard,
                subtitle_path=subtitle_path,
                subtitle_ass=subtitle_path[:-4] + ".ass" if subtitle_path else None,
                audio_concat_path=audio_concat_path,
                output_format=payload.get("output_format", "mp4"),
                quality=payload.get("quality", "standard"),
            )

        # Disk assets (visual / audio.mp3 / subtitle.srt) are now refreshed.
        # Composition is the job of POST /recompose (B0) — no orphan payload write.
        return {"frame_index": frame_index, "mode": mode, "storyboard": storyboard.to_dict()}

    # ── 视觉源工厂：薄委托到 visual_factory（2026-08-03 Task 16 纯搬迁）──────
    # 保留这四个方法名是刻意的——它们把「实例状态（_providers/_config）注入」
    # 这一件事收在一处，且下游调用点与既有测试的 monkeypatch 目标零改动。

    def _has_mixed_sources(self, storyboard: Storyboard) -> bool:
        return has_mixed_sources(storyboard)

    def _make_visual_source_mixed(
        self,
        storyboard: Storyboard,
        concurrency: int | None = None,
        *,
        image_provider: str | None = None,
        video_provider: str | None = None,
    ):
        return make_visual_source_mixed(
            storyboard,
            concurrency,
            providers=self._providers,
            config=self._config,
            image_provider=image_provider,
            video_provider=video_provider,
        )

    def _make_visual_budget(self, kind: str):
        return make_visual_budget(kind, config=self._config)

    def _make_visual_source(
        self,
        task_type: str,
        engine: str = "auto",
        concurrency: int | None = None,
        *,
        image_provider: str | None = None,
        video_provider: str | None = None,
    ):
        return make_visual_source(
            task_type,
            engine,
            concurrency,
            providers=self._providers,
            config=self._config,
            image_provider=image_provider,
            video_provider=video_provider,
        )

    async def _run_remotion(
        self,
        task_id: str,
        payload: dict,
        task_dir: Path,
        progress: ProgressUpdater,
    ) -> PipelineGatewayDone:
        """Remotion runs entirely in Gateway via SandboxProvider."""
        sandbox_provider = self._get_sandbox_provider()

        from .pipelines.remotion_run import run_remotion

        sandbox_id = sandbox_provider.acquire(thread_id=task_id)
        sandbox = sandbox_provider.get(sandbox_id)
        try:
            # W2/W4（2026-08-24 活性重构，复核补挂）：remotion_legacy 存量路径同是
            # gateway 内合成，挂 compose 预算（否则 >30min 的 legacy 渲染只剩 P5
            # 兜底窗口保护）。时长口径：payload.target_duration，缺省回落 30s。
            _compose_budget = self._stage_budget_compose(payload.get("target_duration"))
            async with self._liveness(task_id, stage="compose", stage_budget=_compose_budget):
                await run_remotion(
                    payload,
                    task_dir=task_dir,
                    sandbox=sandbox,
                    progress=progress,
                )
        finally:
            sandbox_provider.release(sandbox_id)
        return PipelineGatewayDone()

    def _resolve_users_root(self) -> Path:
        """Return the ``.deer-flow/users`` root used by the thread/sandbox isolation layer.

        Same ``base_dir`` the agent runtime / sandbox use (see
        ``deerflow.config.paths.Paths``), so the agent's ``/mnt/user-data/outputs``
        physical location resolves correctly for the Gateway process's filesystem.
        """
        from deerflow.config import get_paths

        return get_paths().base_dir / "users"

    def _get_agent_runner(self):
        """DEAD (2026-07-15 split): the remotion nested-agent engine
        (RemotionAgentRunner, agent_runner.py) is removed — remotion renders via chat
        direct-write now. Retained only because EngineRegistry's ctor still accepts a
        (now-dead) ``remotion_runner_factory`` param (B2b, kept for vimax wiring/test
        compatibility); that factory is never invoked since the registry's remotion case
        is gone. Calling this raises loudly instead of ImportError-ing against the deleted
        agent_runner module (the legacy ``remotion_engine == "agent"`` dispatch branch, if
        ever re-reached, fails with a clear message instead of a confusing import error).
        """
        raise RuntimeError("remotion nested-agent path removed (2026-07-15 split); remotion renders via chat direct-write (course-video agent + auto-video-flow), not the backend engine")

    def _get_engine_registry(self):
        """Lazily build the EngineRegistry (阶段 1.5, multi_engine path only).

        remotion_runner_factory is retained-but-dead (2026-07-15 split: remotion renders
        via chat direct-write; the registry's remotion case is gone, so this factory is
        never invoked — kept only to satisfy the ctor signature for vimax wiring/tests, B2b);
        vimax_config_factory translates TrainerAI video_gen config → ViMax config.
        """
        if self._engine_registry is None:
            from .engines.registry import EngineRegistry

            svc = self

            def _remotion_runner_factory():
                return svc._get_agent_runner()

            def _vimax_config_factory(working_dir, request):
                # 阶段 2.6: translate TrainerAI video_gen config → ViMax pipeline config.
                # C5b (2026-10-12): image backend routed by vimax_image_backend —
                # "360"（默认）→ 360 网关 seedream 5.0 Pro（qihu 凭据 + 点号命名
                # bytedance/doubao-seedream-5.0-pro + Seedream360GeneratorForViMax 桥）；
                # "ark" → Ark 直连（阶段 4.2 原路径，回滚位）。消除本处对 Ark 图像
                # 凭据的硬编码。LLM runs on 360 (vimax_llm_base_url, §1.2).
                # C5a: video backend default flipped to 360 seedance
                # (见 _resolve_vimax_video_backend docstring for the历史沿革);
                # video credentials route to ark_api_key/ark_base_url when the backend
                # is seedance-ark, else qihu.
                from .engines.vimax.provider_bridge import build_vimax_config

                cfg = svc._config
                # Carry the requested video aspect (e.g. "16:9") into the image backend:
                # real-run vg_8159cd6cc3fa came back 768x768 because Nano-Banana hardcoded
                # 1:1; the aspect now flows request → image_aspect_ratio → build_vimax_config
                # → image bridge → _aspect_to_size → seedream `size`/`extra_body.size`
                # (Task 13 修复；原 NanoBanana 走 extra_body.aspectRatio，已过时).
                aspect = request.get("aspect") if isinstance(request, dict) else None
                pipeline = request.get("pipeline") if isinstance(request, dict) else None
                selection = _resolve_vimax_backend_selection(cfg, style=request.get("style") if isinstance(request, dict) else None)
                image_backend = selection["image_backend"]
                if image_backend == "360":
                    image_api_key = cfg.qihu_api_key
                    image_base_url = cfg.qihu_base_url
                else:
                    image_api_key = cfg.ark_api_key
                    image_base_url = cfg.ark_base_url
                image_model = selection["image_model"]
                # seedance(默认/auto 同义) → 360; explicit override wins.
                # 见 _resolve_vimax_video_backend docstring for the历史沿革.
                video_backend, video_model = selection["video_backend"], selection["video_model"]
                # Stage 5.2: seedance-ark uses Ark credentials; happyhorse/hailuo/seedance
                # use 360 (qihu) credentials. "ark" in backend covers seedance-ark.
                if "ark" in video_backend:
                    video_api_key = cfg.ark_api_key
                    video_base_url = cfg.ark_base_url
                else:
                    video_api_key = cfg.qihu_api_key
                    video_base_url = cfg.qihu_base_url
                return build_vimax_config(
                    working_dir=working_dir,
                    llm_model=cfg.vimax_llm_model,
                    llm_base_url=cfg.vimax_llm_base_url,
                    llm_api_key=cfg.vimax_llm_api_key(),
                    # C5b: image side routed by vimax_image_backend (360 默认 | ark 回滚位).
                    image_api_key=image_api_key,
                    image_base_url=image_base_url,
                    image_model=image_model,
                    image_backend=image_backend,
                    video_api_key=video_api_key,
                    video_base_url=video_base_url,
                    video_model=video_model,
                    video_backend=video_backend,
                    api_concurrency=cfg.vimax_api_concurrency,
                    # novel2movie 长片预算分支已随管线拆除（T1，2026-10-16）——
                    # 三个长片字段同批删除，双侧恒用短片上限。
                    max_image_calls=cfg.vimax_max_image_gen,
                    max_video_calls=cfg.vimax_max_video_gen,
                    max_cost_cny=getattr(cfg, "vimax_budget_cny", None),
                    image_aspect_ratio=aspect,
                    pipeline=pipeline or "script2video",
                    video_max_wait=cfg.vimax_video_max_wait,
                    character_reference=request.get("character_reference") if isinstance(request, dict) else None,
                    character_sheets=request.get("character_sheets") if isinstance(request, dict) else None,
                )

            self._engine_registry = EngineRegistry(
                remotion_runner_factory=_remotion_runner_factory,
                vimax_config_factory=_vimax_config_factory,
                vimax_burn_subtitles=getattr(svc._config, "vimax_burn_subtitles", True),
            )
        return self._engine_registry

    def _read_storyboard(self, task_dir: Path) -> Storyboard:
        import json

        path = task_dir / "storyboard.json"
        if not path.exists():
            raise FileNotFoundError(f"storyboard.json not found in task_dir {task_dir}")
        return Storyboard.from_dict(json.loads(path.read_text(encoding="utf-8")))

    async def _run_pipeline_engine(
        self,
        task_id: str,
        user_id: str,
        task_type: str,
        payload: dict,
        task_dir: Path,
        progress: ProgressUpdater,
    ):
        """multi_engine path (阶段 1.5): prep → engine.render → mode dispatch.

        Gated by config.engine_arch=='multi_engine'. Legacy path
        (_prepare_storyboard_video) stays byte-identical for rollback.
        """
        from .engines.base import EngineContext
        from .worker_probe import decide_pipeline_version, probe_worker_capabilities

        # HR-T4: 把 CourseProject.character_reference 注入 payload，供 vimax
        # config_factory 透传给 vendored CharacterPortraitsGenerator 作 front
        # portrait 的 reference_image（bridge generate_single_image 已支持）。
        if payload.get("course_project_id") and not payload.get("character_reference"):
            ref = _read_course_project_character_reference(self._storage_dir, payload.get("course_project_id"), user_id)
            if ref:
                payload["character_reference"] = ref

        # Task 13 / B5c: 项目三视图 character_sheets 经 build_vimax_config 的
        # character_sheets 参数端到端贯通（front + side + back 全传 vendored vimax），
        # 不再在 ViMax 边界被丢弃。front 经 seedream i2i 真生效（ViMax 桥
        # SeedreamGeneratorForViMax，ByteDance 接入后）。
        if payload.get("course_project_id") and not payload.get("character_sheets"):
            sheets = _read_course_project_character_sheets(self._storage_dir, payload.get("course_project_id"), user_id)
            if sheets:
                payload["character_sheets"] = [s.to_dict() for s in sheets]
                if not payload.get("character_reference"):
                    payload["character_reference"] = sheets[0].front_url

        # 1. blueprint (narrations → storyboard → TTS → BGM)
        # A1 (决策3 蓝图漂移): when rendering an already-planned task (reuse_blueprint
        # is set by run_pipeline_for_tool / resume_draft), reuse the on-disk
        # storyboard.json the user confirmed instead of regenerating narrations —
        # prevents generate-mode drift (confirmed blueprint ≠ rendered content).
        # vimax excluded: it runs its own pipeline from payload.script/topic (A2).
        audio_concat_path = str(task_dir / "audio.mp3")
        subtitle_path = str(task_dir / "subtitle.srt")
        sb_path = task_dir / "storyboard.json"
        # 2026-10-20 Task 2（已拍板）：vimax script2video 也复用已确认蓝图——
        # 盘面 storyboard.json 为分镜内容唯一事实源，帧旁白重建 payload.script，
        # 跳过 _prepare_storyboard_and_audio（不重新规划）；盘面无帧/无旁白才回退。
        # idea2video continue 原路径（story confirm 自有权衡）。
        _reuse = False
        _vimax_script: str | None = None
        if payload.get("reuse_blueprint") and sb_path.exists():
            if task_type == "vimax":
                if payload.get("pipeline") != "idea2video":
                    storyboard = await asyncio.to_thread(self._read_storyboard, task_dir)
                    _vimax_script = "\n".join(f.narration for f in storyboard.frames if f.narration).strip()
                    if _vimax_script:
                        _reuse = True
                    else:
                        logger.warning(
                            "vimax blueprint reuse: task=%s has no narrated frames; falling back to prep",
                            task_id,
                        )
            else:
                _reuse = True

        if _reuse:
            if task_type != "vimax":
                storyboard = await asyncio.to_thread(self._read_storyboard, task_dir)
            await progress(status="running", progress=5)
            if task_type == "vimax":
                # 不重新生成 TrainerAI TTS / 不重新规划：ViMax 只消费冻结脚本。
                payload["script"] = _vimax_script or ""
                audio_concat_path = ""
                subtitle_path = ""
                logger.info(
                    "vimax blueprint reuse: task=%s frames=%d total_duration=%.1f",
                    task_id,
                    len(storyboard.frames),
                    storyboard.total_duration,
                )
            # Reuse draft audio if fully synthesized; else (re)synthesize from the
            # confirmed storyboard (draft audio still running, or narration edited).
            elif not Path(audio_concat_path).exists() or not Path(subtitle_path).exists():
                # T-E11：section_id 仅挂项目任务传（None 不传——窄签名桩零变化）
                audio_concat_path, subtitle_path = await self._synthesize_audio(
                    storyboard, task_dir, storyboard.config.voice_id, task_id=task_id, **({"section_id": payload.get("course_project_id")} if payload.get("course_project_id") else {})
                )
            # BGM (mirror _prepare_storyboard_and_audio)
            bgm_id = payload.get("bgm")
            if bgm_id:
                bgm_src = _resolve_bgm_or_warn(bgm_id, user_id=user_id)
                if bgm_src:
                    bgm_dest = str(task_dir / "bgm.mp3")
                    import shutil

                    shutil.copy2(bgm_src, bgm_dest)
                    storyboard.config.bgm_path = bgm_dest
            await progress(progress=75)
        else:
            # Phase 3.4: vimax skips TrainerAI TTS (narration_generator handles audio).
            # storyboard + BGM still generated (EngineContext.voice_id + overlay need them).
            skip_tts = task_type == "vimax"
            storyboard, audio_concat_path, subtitle_path = await self._prepare_storyboard_and_audio(
                task_type,
                payload,
                task_dir,
                progress,
                skip_tts=skip_tts,
                user_id=user_id,
                task_id=task_id,
            )

        # 1b. persist storyboard.json — both engines read it from disk, not the
        # in-memory object: StockClipEngine._load_storyboard raises FileNotFoundError
        # without it; RemotionEngine._resolve_duration falls back but loses the real
        # duration. Also feeds the SSE/timeline endpoints (which poll this file).
        # Direct auto_render path (POST /create + auto_render=true) may have no
        # target/budget; after prep we must pass the actual storyboard duration to
        # engine so script2video still resolves the global budget and batches.
        if task_type == "vimax" and payload.get("pipeline") != "idea2video" and storyboard.total_duration and storyboard.total_duration > 0:
            payload.setdefault("total_duration", storyboard.total_duration)
        sb_path.write_text(
            json.dumps(storyboard.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # P1-9: /api/video/create 路径 vimax+generate 无 script 键（只有 agent 工具路径
        # plan_storyboard_for_tool 冻结）——engine 入口会抛错且 task_framework 重试白烧
        # 3 轮 LLM。从盘面旁白回填；盘面也没有 → 早抛人话错误（不烧 LLM）。
        if task_type == "vimax" and not payload.get("script"):
            if sb_path.exists():
                _sb = Storyboard.from_dict(json.loads(sb_path.read_text(encoding="utf-8")))
                payload["script"] = "\n".join(f.narration for f in _sb.frames if f.narration)
            if not payload.get("script"):
                raise ValueError("vimax 生成缺少脚本：请用 fixed 模式粘贴脚本，或经 vimax-video 对话创建")

        # 2. map task_type → engine video_type (remotion removed 2026-07-15: renders via chat)
        if task_type == "vimax":
            engine_type = "vimax"
        else:
            raise ValueError(f"unsupported engine task_type: {task_type!r} (vimax only — remotion renders via chat direct-write)")

        # 3. build ctx + engine.render
        # B4: 项目 style_prefix 前置到 ViMax ctx.style（→ _with_style_prefix 进每帧 prompt）。
        #     修前 ctx.style 仅 payload "style"（ViMax 美学旋钮），项目视觉风格预设对 ViMax 无效。
        proj_style = self._load_course_project_style(payload.get("course_project_id"), user_id=user_id)
        proj_prefix = proj_style.get("style_prefix") if isinstance(proj_style, dict) else None
        ctx = EngineContext(
            user_id=user_id,
            task_id=task_id,
            task_dir=task_dir,
            voice_id=storyboard.config.voice_id,
            subtitle_style=storyboard.config.subtitle_style,
            tts_speed=storyboard.config.tts_speed,  # T7b（2026-10-14）：三级解析语速透传 vimax（修前项目级静默失效）
            style=_combine_vimax_style(payload.get("style"), proj_prefix),
            aspect=payload.get("aspect"),  # T3（Lane 2）：画幅 → 字幕 orientation/PlayRes（修前 vimax 硬编码竖屏）
            progress=progress,
            request=payload,
        )
        engine = self._get_engine_registry().get_engine(engine_type, ctx)
        outcome = await engine.render(ctx)

        # 4. mode dispatch
        if outcome.mode == "segments":
            # map segments back onto storyboard frames for build_worker_payload
            for seg in outcome.segments or []:
                frame = next((f for f in storyboard.frames if f.index == seg.frame_index), None)
                if frame is not None:
                    frame.visual_url = seg.visual_path
                    frame.visual_type = seg.visual_type
            await progress(progress=90)
            worker_caps = await probe_worker_capabilities()
            pv = decide_pipeline_version(storyboard, worker_caps)
            return PipelineCompleted(
                payload=build_worker_payload(
                    storyboard,
                    subtitle_path=subtitle_path,
                    subtitle_ass=subtitle_path[:-4] + ".ass" if subtitle_path else None,
                    audio_concat_path=audio_concat_path,
                    output_format=payload.get("output_format", "mp4"),
                    quality=payload.get("quality", "standard"),
                    pipeline_version=pv,
                )
            )
        # mode == "finished" (remotion成品 / future vimax)
        await _finish_engine_outcome(progress, outcome)
        return PipelineGatewayDone()

    async def run_pipeline_for_tool(
        self,
        *,
        video_type: str,
        task_id: str,
        pipeline: str | None = None,
        style: str | None = None,
    ) -> dict:
        """阶段 1.8 wiring target: bridge the generate_video tool to the pipeline lifecycle.

        Resolves the task row by ``task_id``, runs the full pipeline via
        ``_wrap_pipeline`` (concurrency / progress → task SSE / status transitions
        / retry / stale-recovery compatible), then maps the terminal task state to
        a result dict for the tool:

          - ``ready``     → ``{"video_path": ..., "mode": "finished"}``
          - ``composing`` → ``{"mode": "segments"}`` (Worker handoff)
          - anything else → raises (the tool reports status=error).

        The tool wraps this call in ``asyncio.wait_for`` (1.9 wall-clock timeout).
        On timeout the await is cancelled; ``_wrap_pipeline`` catches
        ``CancelledError``, drains the task to ``failed``, and re-raises — so the
        tool surfaces ``status=timeout`` and the task is not left dangling.

        ``video_type`` is accepted for signature parity with the tool but does NOT
        drive engine selection in 阶段 1: ``_run_pipeline_engine`` routes by the
        task row's ``task_type`` (remotion → RemotionEngine, else StockClipEngine).
        The vimax knobs (``pipeline`` / ``style``) are injected into the in-memory
        payload so they reach the engine in 阶段 2; they are deliberately NOT
        written back to the task row.
        """
        if video_type == "remotion":
            # 2026-07-22 工具层摘牌（split B1 补执行）：历史 remotion 草稿也不得
            # 经 generate_video 触发 legacy template 渲染。
            raise ValueError("run_pipeline_for_tool: remotion 已从工具层摘牌（2026-07-22）——remotion 由 course-video agent + auto-video-flow 直写渲染")
        # SEC-H6（批次 2 项 2.2）：工具入口与 HTTP 路由层同口径做任务归属过滤——
        # 知道他人 task_id 不得跨用户触发渲染。无 auth 部署下 contextvar 未设，
        # get_effective_user_id() 返回 "default"，任务创建同为 "default"（行为与
        # 认证形态统一，不做 default 跳过）。
        tool_user_id = get_effective_user_id()
        task = await self.repo.get(task_id, user_id=tool_user_id)
        if task is None:
            raise ValueError(f"video task {task_id} not found")

        payload = json.loads(task.payload or "{}")
        if pipeline is not None:
            payload["pipeline"] = pipeline
        if style is not None:
            payload["style"] = style
        # Force the full render path — never the draft branch. The tool is the
        # render trigger for an already-planned task. reuse_blueprint makes the engine
        # path reuse the plan's confirmed storyboard.json (A1: no generate-mode drift).
        payload["auto_render"] = True
        payload["reuse_blueprint"] = True

        # M1.2: fire-and-forget. The agent's chat run (SSE stream) awaits this tool;
        # awaiting a 15-min vimax render here means a stream disconnect/timeout cancels
        # the await → CancelledError → task marked "Cancelled by user" (the root cause
        # of idea2video/script2video crashing mid-run). Schedule the pipeline as a
        # background task and return immediately; progress flows via the task SSE and
        # the agent tells the user it's rendering. Mirrors resume_draft's create_task
        # pattern (line ~900).
        def _on_done(t: asyncio.Task) -> None:
            if t.cancelled():
                return  # cancellation is expected (stream disconnect / timeout)
            exc = t.exception()
            if exc:
                logger.error("generate_video task %s failed: %s", task_id, exc)

        def _factory() -> asyncio.Task:
            t = asyncio.create_task(self._wrap_pipeline(task_id, tool_user_id, task.task_type, payload))
            t.add_done_callback(_on_done)
            return t

        if not self._register_active_task(task_id, _factory):
            # E2: tool 入口不抛——返回错误结果让 agent 友好提示「已在渲染中」
            return {"task_id": task_id, "mode": "busy", "error": f"task {task_id} already processing"}

        return {"task_id": task_id, "mode": "submitted"}

    async def check_task_status_for_tool(self, *, task_id: str) -> dict:
        """Read a video task's ground-truth status for the check_video_status tool.

        generate_video is fire-and-forget (returns {mode: "submitted"} instantly),
        so an agent cannot infer real progress from memory. This reads the DB row
        directly so the agent reports status truthfully — including FAILED (root
        cause of vg_7477487923ef: agent narrated "GPU rendering" while the task
        had been failed for 10 minutes).

        Returns a dict the agent can relay verbatim (D4, 2026-07-15 split: pruned —
        only status/progress/output_url/error_message/updated_at; no in-flight retry
        notes / file_size / internal ids the agent could brainstorm from):
          - non-terminal: {task_id, status, progress, updated_at}
          - ready:        + output_url (the download route)
          - failed:       + error_message
          - not found:    {task_id, status: "not_found"}
        """
        from datetime import UTC
        from datetime import datetime as _dt

        # SEC-H6（批次 2 项 2.2）：状态读取同样按归属过滤——跨用户读 output_url
        # 也不允许；他/她人任务对外与不存在同形（not_found），不泄露存在性。
        task = await self.repo.get(task_id, user_id=get_effective_user_id())
        if task is None:
            return {"task_id": task_id, "status": "not_found"}
        info: dict = {
            "task_id": task_id,
            "status": task.status,
            "progress": task.progress,
        }
        if task.status == "ready":
            info["output_url"] = f"/api/video/tasks/{task_id}/download"
        if task.status == "failed":
            info["error_message"] = task.error_message or "unknown error"
        # D4 (2026-07-15 split): do NOT surface in-flight error/retry notes (the old
        # `note` field) — agents brainstormed 'retry stage / GPU node / audio-video
        # alignment' from them (vg_7477487923ef). Only failed tasks surface error_message.
        if task.updated_at is not None:
            updated = ensure_utc(task.updated_at)
            info["updated_at"] = (updated or _dt.now(UTC)).isoformat()
        return info

    async def update_story(self, task_id: str, *, user_id: str, story: str) -> None:
        """Persist a user's edit to the expanded story (idea2video StoryForm).

        Rewrites story.txt (so render reuses the edited story via
        Idea2VideoPipeline.develop_story's file-load checkpoint) AND patches
        result.story (so the UI shows the edited version). Only story_ready tasks
        can be edited (the story only exists for idea2video at that stage)."""
        task = await self.repo.get(task_id, user_id=user_id)
        if task is None:
            raise ValueError(f"update_story: task {task_id} not found")
        if task.status != STORY_READY:
            raise ValueError("update_story: task is not in story_ready status")

        task_dir = Path(self._storage_dir) / user_id / "tasks" / task_id
        (task_dir / "story.txt").write_text(story, encoding="utf-8")

        existing = json.loads(task.result or "{}")
        existing["story"] = story
        await self.repo.update(task_id, result=json.dumps(existing, ensure_ascii=False))

    async def plan_storyboard_for_tool(
        self,
        *,
        video_type: str,
        script: str | None = None,
        topic: str | None = None,
        aspect: str | None = None,
        voice_id: str | None = None,
        n_scenes: int | None = None,
        style: str | None = None,
        pipeline: str | None = None,
        target_duration: int | None = None,
    ) -> dict:
        """阶段 1.8: create a draft video task + persist storyboard.json from chat.

        The course-video agent calls this to plan a blueprint it can present for
        confirmation before rendering. Maps the tool's video_type to a task_type,
        builds an ``auto_render=False`` payload (draft only — no render yet), creates
        the task row, and awaits ``_wrap_pipeline`` until the task reaches DRAFT
        (storyboard.json on disk). Returns a summary the agent can show the user.

        Both engines are plan-able (remotion, vimax); unmapped
        legacy types (e.g. cinematic) are rejected. vimax drafts produce a
        script-based blueprint that VimaxEngine later compresses into the ViMax
        pipeline input.
        """
        task_type = _VIDEO_TYPE_TO_TASK_TYPE.get(video_type)
        if task_type is None:
            raise ValueError(f"plan_storyboard: unsupported video_type {video_type!r} (vimax; remotion 由 course-video agent 直写)")
        if not script and not topic:
            raise ValueError("plan_storyboard: either script (fixed mode) or topic (generate mode) is required")

        user_id = get_effective_user_id()
        payload: dict = {
            "mode": "generate" if topic else "fixed",
            "auto_render": False,
            "aspect": aspect or self._config.default_aspect,
            "style": style or "default",
        }
        if script is not None:
            payload["script"] = script
        if topic is not None:
            payload["topic"] = topic
        if voice_id is not None:
            payload["voice_id"] = voice_id
        if n_scenes is not None:
            payload["n_scenes"] = n_scenes
        if pipeline is not None:
            payload["pipeline"] = pipeline

        # D2 (2026-07-15 split): vimax without an explicit target_duration OR n_scenes
        # defaults to 180s (3min) so a long script isn't under-split into the no-target
        # _resolve_n_scenes return-5 path. idea2video excluded (story-driven); an explicit
        # n_scenes is respected (not overridden by the duration-derived scene count).
        if target_duration is None and n_scenes is None and video_type == "vimax" and pipeline != "idea2video":
            target_duration = 180

        # Phase 2: target_duration → max_scenes + max_shots (limits ViMax scene/shot count)
        if target_duration is not None:
            from app.gateway.video_gen.engines.vimax.storyboard_budget import derive_vimax_storyboard_budget

            max_scenes, max_shots_per_scene, global_budget = derive_vimax_storyboard_budget(target_duration)
            payload["user_requirement_extra"] = f"Generate exactly {max_scenes} scene. Not more than {max_shots_per_scene} shots per scene."
            payload["n_scenes"] = max_scenes  # override: align TrainerAI narration count with ViMax scene count
            # Plan C（2026-10-19，D6）：结构化全局镜头预算（>12 时 engine/pipeline 据此
            # 确定性拆批；extra 文案仍是 ViMax 提示词约束，但不再是唯一预算来源）。
            payload["vimax_global_shot_budget"] = global_budget

        task_id = self._gen_id()
        await self.repo.create(
            id=task_id,
            user_id=user_id,
            domain=self.domain,
            task_type=task_type,
            payload=json.dumps(payload, ensure_ascii=False),
            priority=50,
        )
        await self._wrap_pipeline(task_id, user_id, task_type, payload)

        refreshed = await self.repo.get(task_id)
        if refreshed is None:
            raise RuntimeError(f"plan_storyboard: task {task_id} vanished after draft")
        if refreshed.status == "failed":
            raise RuntimeError(f"plan_storyboard: draft failed: {refreshed.error_message or 'unknown error'}")
        result = json.loads(refreshed.result or "{}") if refreshed.result else {}
        storyboard = result.get("storyboard") or {}
        # A2 (决策3): freeze vimax generate-mode narrations into payload.script
        # (mode→fixed, topic dropped). render then reuses the exact confirmed script
        # — prevents blueprint drift AND the audio/video mismatch (prep audio is split
        # from the same frozen script VimaxEngine scripts). idea2video has its own
        # story-confirmation flow (story.txt + STORY_READY) and is intentionally excluded.
        frames = storyboard.get("frames") or []
        if task_type == "vimax" and payload.get("mode") == "generate" and payload.get("pipeline") != "idea2video" and frames:
            payload["script"] = "\n".join(f.get("narration", "") for f in frames)
            payload.pop("topic", None)
            payload["mode"] = "fixed"
            await self.repo.update(task_id, payload=json.dumps(payload, ensure_ascii=False))
        story = result.get("story")
        return {
            "task_id": task_id,
            "status": refreshed.status,
            "video_type": video_type,
            "n_frames": len(storyboard.get("frames", [])),
            "total_duration": storyboard.get("total_duration"),
            # 2026-07-22 确认门黑盒修复：蓝图内容内联返回（见 _blueprint_for_confirmation）。
            "blueprint": _blueprint_for_confirmation(storyboard),
            # idea2video story-confirmation flow：确认对象是故事（result.story，
            # STORY_READY 转换时写入）。>6000 字符截断，防止长故事撑爆工具返回预算。
            "story": (story[:6000] + "…[truncated]") if isinstance(story, str) and len(story) > 6000 else story,
        }

    async def _render_remotion(
        self,
        task_id: str,
        result: dict,
        task_dir: Path,
        progress: ProgressUpdater,
        *,
        payload: dict,
        user_id: str,
    ) -> PipelineGatewayDone:
        """Dispatch remotion rendering by configured engine.

        - ``template`` (default): legacy data-driven DataDrivenComposition.
        - ``agent``: delegate to the course-video agent for per-content customization.
        """
        if self._config.remotion_engine == "agent":
            return await self._render_remotion_via_agent(task_id, result, task_dir, progress, payload=payload, user_id=user_id)
        return await self._render_remotion_storyboard(task_id, result, task_dir, progress)

    async def _render_remotion_via_agent(
        self,
        task_id: str,
        result: dict,
        task_dir: Path,
        progress: ProgressUpdater,
        *,
        payload: dict,
        user_id: str,
    ) -> PipelineGatewayDone:
        """Render a remotion task by delegating to the course-video agent."""
        storyboard = result.get("storyboard") or {}
        total_duration = storyboard.get("total_duration") or payload.get("target_duration") or 30
        mode = payload.get("mode", "fixed")
        # W2/W4（2026-08-24 活性重构）：gateway 内合成（remotion agent 渲染）挂滴答
        # + compose 预算（margin × max(时长×吞吐系数, remotion_agent_timeout)）。
        _compose_budget = self._stage_budget_compose(total_duration)
        async with self._liveness(task_id, stage="compose", stage_budget=_compose_budget):
            out = await self._get_agent_runner().render_via_agent(
                user_id=user_id,
                script=payload.get("script") if mode != "generate" else None,
                topic=payload.get("topic") if mode == "generate" else None,
                style=payload.get("style", "default"),
                voice_id=payload.get("voice_id") or self._config.tts_voice,
                aspect=payload.get("aspect") or self._config.default_aspect,
                target_duration_seconds=int(round(total_duration)),
                task_dir=task_dir,
                progress=progress,
            )
        await progress.set_result(output_path=str(out), file_size=out.stat().st_size)
        return PipelineGatewayDone()

    async def _render_remotion_storyboard(
        self,
        task_id: str,
        result: dict,
        task_dir: Path,
        progress: ProgressUpdater,
    ) -> PipelineGatewayDone:
        """Render data-driven Remotion video from StoryboardResult."""
        sandbox_provider = self._get_sandbox_provider()

        from .pipelines.remotion_run import run_remotion_storyboard

        storyboard = result["storyboard"]
        subtitle_path = result.get("subtitle_path")

        sandbox_id = sandbox_provider.acquire(thread_id=task_id)
        sandbox = sandbox_provider.get(sandbox_id)
        try:
            # W2/W4（2026-08-24 活性重构）：模板路径合成同挂 compose 预算。
            _compose_budget = self._stage_budget_compose(getattr(storyboard, "total_duration", None))
            async with self._liveness(task_id, stage="compose", stage_budget=_compose_budget):
                await run_remotion_storyboard(
                    storyboard,
                    task_dir=task_dir,
                    sandbox=sandbox,
                    progress=progress,
                    subtitle_path=subtitle_path,
                    template_id=result.get("template_id"),
                )
        finally:
            sandbox_provider.release(sandbox_id)
        return PipelineGatewayDone()
