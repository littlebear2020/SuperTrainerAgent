"""VimaxEngine (阶段 2.4) — Engine V: ViMax character-consistency story video.

Thin bridge (plan §5.2): resolve the script from the task payload → build the
ViMax config (host providers via the §5.3 bridge) → write it to working_dir →
``Script2VideoPipeline.init_from_config`` + ``__call__`` → ``final_video.mp4``
(``mode="finished"``).

``config_factory`` + ``pipeline_factory`` are DI'd for unit-testability (no real
LLM/image/video calls). The factory wiring (TrainerAI config → ViMax config, and
the real ``Script2VideoPipeline``) is supplied by the registry (阶段 2.6).

Notes:
- Wall-clock timeout is enforced by the generate_video tool layer (§1.9), not here.
- ViMax's progress callback is sync; the async ProgressUpdater mismatch means
  ViMax-internal progress is NOT forwarded for now (process visualization → §10.3).
- ``final_video.mp4`` lands at ``working_dir/final_video.mp4`` (§5.2 v3.2 correction).
"""

from __future__ import annotations

import asyncio
import datetime
import inspect
import json
import logging
import re
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml
from vimax.utils.video import concatenate_video_files

from app.gateway._shared.executors import run_ffmpeg
from app.gateway._shared.safe_download import safe_download
from app.gateway.video_gen.engines.vimax.voice_policy import ensure_vimax_voice_allowed, resolve_vimax_default_voice

from ..base import EngineContext, EngineOutcome
from .audio_overlay import overlay_audio_track
from .narration_generator import _DEFAULT_SHOT_SECONDS, _find_storyboard_files, _get_shot_duration, _load_shots
from .provider_bridge import _to_image_url

# T6（Lane 2, 2026-10-15）+ T3（2026-10-16 收口批）：vendored 静态降级事件的消费口径。
# kind：视频级沿用事件名同款字符串（degradations.record_degradation 不做枚举校验）；
# 帧级（artifact=first_frame/last_frame）单列 first_frame_static_fallback——帧级/视频级
# sidecar 不因 kind+frame_index 幂等去重互吞。契约：payload 至少
# {"shot_idx": int, "reason": str}；可选 {"artifact": str, "scene_idx": int}；
# Lane 1 落地前的过渡形态键 "error" 同样消费（契约不得因旧形态碎）。
_STATIC_FALLBACK_EVENT = "video_clip_static_fallback"
_FIRST_FRAME_FALLBACK_EVENT = "first_frame_static_fallback"

# 帧级 artifact → 中文措辞（detail 上屏用）
_FRAME_ARTIFACT_LABELS = {"first_frame": "首帧", "last_frame": "尾帧"}


def _consume_static_fallback_event(meta: Any, message: str, task_dir: Path | None, counter: dict | None) -> None:
    """video_clip_static_fallback 进度事件 → degradations sidecar + 计数。

    - 按 ``artifact`` 区分帧级（first_frame/last_frame → first_frame_static_fallback
      kind）/视频级（缺省/``video_clip`` → 事件名 kind）；计数分 ``frame_shots`` /
      ``video_shots`` 两键（终态分别落 ``vimax_static_fallback_frames`` /
      ``vimax_static_fallback_shots``）。
    - idea2video 多场景事件携带 ``scene_idx``（scene 内局部 shot_idx）→ 消费侧维护
      场景偏移表换算全局 gid（与 build_vimax_shots_manifest 的全局连续键同口径），
      否则跨场景同 idx 碰撞 → 计数偏小 + sidecar 幂等互吞。场景按 idea2video 的
      顺序渲染假设，偏移在场景首见时按更小场景的 (max_local+1) 求和锚定。
    - record_degradation 同 kind+frame_index 幂等（重试不刷屏）。
    """
    from app.gateway.video_gen.degradations import record_degradation

    if not isinstance(meta, dict) or task_dir is None:
        return
    shot_idx = meta.get("shot_idx")
    reason = meta.get("reason") or meta.get("error") or message
    artifact = meta.get("artifact")
    frame_level = isinstance(artifact, str) and artifact != "video_clip"

    # 全局 gid（多场景偏移；script2video 无 scene_idx 时 gid = shot_idx）
    gid = shot_idx if isinstance(shot_idx, int) else None
    scene_idx = meta.get("scene_idx")
    if gid is not None and isinstance(scene_idx, int) and counter is not None:
        maxes = counter.setdefault("_scene_max_local", {})
        offsets = counter.setdefault("_scene_offsets", {})
        if scene_idx not in offsets:
            offsets[scene_idx] = sum(m + 1 for s, m in maxes.items() if s < scene_idx)
        maxes[scene_idx] = max(maxes.get(scene_idx, -1), gid)
        gid = offsets[scene_idx] + gid

    display = (gid + 1) if gid is not None else "?"
    if frame_level:
        part = _FRAME_ARTIFACT_LABELS.get(str(artifact), "帧")
        record_degradation(
            task_dir,
            kind=_FIRST_FRAME_FALLBACK_EVENT,
            detail=f"第 {display} 镜{part}生成失败，已降级为占位帧（任务重试可重生真帧）：{str(reason)[:120]}",
            frame_index=gid,
        )
    else:
        record_degradation(
            task_dir,
            kind=_STATIC_FALLBACK_EVENT,
            detail=f"第 {display} 镜视频生成失败，已用静态帧占位：{str(reason)[:120]}",
            frame_index=gid,
        )
    if counter is not None and gid is not None:
        counter.setdefault("frame_shots" if frame_level else "video_shots", set()).add(gid)


logger = logging.getLogger(__name__)

_DEFAULT_STYLE = "realistic movie style"

# 参考图降级链（用户 2026-10-15 拍板决策①）：redo 逐镜重做 = 补全——若 360 通道
# 拦 AI 参考图（内容审核拒绝），依次降级为首帧参考 → 纯 prompt，并在 result 留痕；
# 不摘除 redo 功能。
_REDO_MODE_FULL = "full"
_REDO_MODE_FIRST_FRAME = "first_frame_only"
_REDO_MODE_PROMPT = "prompt_only"

# 终审 fix pass（2026-10-15）：redo 时长漂移容差——与 narration 对齐容差（0.1s）同口径，
# 其内视为不可感知，不触发时间轴对齐与留痕。
_REDO_DRIFT_TOLERANCE = 0.1


def _stage_to_percent(stage: str, meta: Any = None) -> int | None:
    """Map a ViMax stage to a coarse progress percent, refined with per-shot meta.

    The i2v phase (frames + video clips) is ~80% of render time but ViMax emitted
    only stage_start once, so progress stalled at a flat number the whole phase
    (the 'stuck at 75%' bug). When meta carries {idx, total}, advance the percent
    across the frames(40→60)/video(60→90) bands so the bar actually moves.
    """
    # per-shot granular bands (idx 0-based, total = shot count)
    if meta and isinstance(meta, dict) and "idx" in meta and "total" in meta:
        total = int(meta.get("total") or 0)
        idx = int(meta.get("idx") or 0)
        if total > 0:
            frac = (idx + 1) / total  # fraction complete within the band (idx 0-based)
            # idea2video multi-scene: rescale the band ACROSS scenes so the bar only
            # moves forward. Without scene info, every scene reuses the full 40→60 /
            # 60→90 band → the bar drops backward when a new scene starts (vg_4eea9dfd7215
            # "时而80%、又掉到40%"). scene_total present → split the band: scene_frac
            # is the GLOBAL fraction (0..1 across all scenes), so scene_0 covers the
            # first half and scene_1 starts where scene_0 ended.
            scene_total = int(meta.get("scene_total") or 0)
            scene_idx = int(meta.get("scene_idx") or 0)
            if scene_total > 0:
                scene_frac = (scene_idx + frac) / scene_total
            else:
                scene_frac = frac
            if stage in ("frame_done", "frame_start"):
                # frames band: 40% → 60%, rescaled across scenes when scene info present
                return int(40 + scene_frac * 20)
            if stage in ("video_clip_done", "video_clip_start", "frames_start"):
                # video clips (i2v) band: 60% → 90%, rescaled across scenes
                return int(60 + scene_frac * 30)

    # E2 (2026-07-22): idea2video text-plan stages — previously neither emitted nor
    # mapped, so the bar sat at 75 through the story-expansion LLM call (same class
    # as the 78% storyboard freeze fixed in E1). Values match idea2video's real
    # execution order: develop_story(2) < extract(5) < portrait(15) < write_script(18)
    # < storyboard(20).
    if "develop_story" in stage:
        return 2
    if "write_script" in stage:
        return 18
    if "extract" in stage:
        return 5
    if "portrait" in stage:
        return 15
    if "storyboard" in stage:
        # E1: design/load storyboard — portrait(15) < storyboard(20) < camera(30);
        # rescale 75+int(20*24/95)=80%. Without this the bar froze at 78% during
        # the design_storyboard LLM stage (no mapped percent) — confounding once B1
        # raised the LLM timeout to 300s.
        return 20
    if "shot_description" in stage or "decompose" in stage:
        # E1: decompose/load shot descriptions — storyboard(20) < decompose(25) <
        # camera(30); rescale→81%. Was unmapped → 78% freeze during decompose.
        return 25
    if "camera" in stage:
        return 30
    if "frame_prompt" in stage:
        return 40
    if stage.startswith("frame_"):  # frame_start / frame_done (per-shot gen)
        return 40
    if stage in ("frames_start", "video_clip_start", "video_clip_done"):
        # i2v phase (no per-shot meta) — flat 60, refined above when meta present
        return 60
    if "concat" in stage:
        return 90
    if "final" in stage or stage == "render_done":
        return 95
    return None


def _wrap_vimax_progress(async_progress: Any, task_dir: Path | None = None, static_fallback: dict | None = None) -> Callable[[str, str, Any], None]:
    """Bridge ViMax's SYNC progress callback to the async ProgressUpdater.

    ViMax's __call__ takes ``progress: Callable[[str, str, dict|None], None]``
    (sync); the task ProgressUpdater is async. On each ViMax stage:
      - map stage → coarse percent (5/15/30/.../95) and forward via progress(pct)
      - forward stage/message/meta into the task result JSON via set_result, so the
        frontend can render the current stage + per-shot meta (3.3 visualization).
        Previously this dropped everything except the percent.

    Fire-and-forget: schedules async updates on the running loop (we're inside
    ViMax's awaited __call__, so the loop is live). Unmapped stages still forward
    stage/message/meta (only the percent is skipped). Best-effort.
    """
    loop = asyncio.get_running_loop()
    # Monotonic clamp: ViMax gathers frame generation (40-60 band) and video-clip
    # generation (60-90 band) CONCURRENTLY, so their events interleave. Without a
    # high-water mark the percent jumps 50→65→52→70… and the bar visibly flickers
    # (40↔60). Only ever move the bar forward; stage/message/meta still forward every
    # event for the per-shot visualization.
    #
    # Rescale: _stage_to_percent returns ViMax's INTERNAL 0-95 range, but the task
    # already covered 0-75 in prep (script→storyboard→TTS→BGM). Without rescaling,
    # ViMax's frames (40-60) + early-video (60-75) sit BELOW prep's 75 and get
    # clamped → the bar appears FROZEN at 75 through most of the (long) render.
    # Map 0-95 → 75-99 so the entire ViMax phase is visible + advances monotonically.
    high_water = [75]  # prep ended at 75; ViMax continues from here

    def _sync_cb(stage: str, message: str, meta: Any) -> None:
        # T6：跨路契约消费——vendored 静态降级事件落 sidecar + 计数（同步小写入，
        # 对齐 narration_generator 的 record_degradation 用法；异常由其内部吞）。
        if stage == _STATIC_FALLBACK_EVENT:
            _consume_static_fallback_event(meta, message, task_dir, static_fallback)
        raw = _stage_to_percent(stage, meta)
        if raw is not None:
            pct = 75 + int(raw * 24 / 95)  # raw 0→75, raw 95→99
            if pct > high_water[0]:
                high_water[0] = pct
                loop.create_task(async_progress(progress=pct))
        # forward stage/message/meta for 3.3 frontend visualization
        loop.create_task(
            async_progress.set_result(
                render_stage=stage,
                render_message=message,
                render_meta=meta or {},
            )
        )

    return _sync_cb


def _resolve_pipeline_class(config: dict[str, Any]) -> type:
    """Map the pipeline knob to the ViMax pipeline class (no instantiation).

    idea2video → Idea2VideoPipeline (idea → story → script → render)
    script2video / default → Script2VideoPipeline (script → render)
    novel2movie → 已拆除（T1，2026-10-16）：响亮报错，不静默换管线——用户提交
    长篇小说却被静默当 script2video 处理 = 拿回 30 镜短片的静默错版。

    Exposed for unit-testing the routing decision without instantiating (which
    needs a real LLM)."""
    from vimax.pipelines.idea2video_pipeline import Idea2VideoPipeline
    from vimax.pipelines.script2video_pipeline import Script2VideoPipeline

    pipeline = config.get("pipeline") or "script2video"
    if pipeline == "idea2video":
        return Idea2VideoPipeline
    if pipeline == "novel2movie":
        raise RuntimeError("novel2movie 已于 2026-10-16 拆除（langchain 1.x 断裂 + 摘牌期零消费，用户拍板不修直接删）：请改用 script2video（传入章节剧本）或 idea2video；详见 docs/plans/2026-10-16-vimax-degradation-contract-cleanup-plan.md")
    return Script2VideoPipeline


def _default_pipeline_factory(config_path: str) -> Any:
    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)
    pipeline_cls = _resolve_pipeline_class(config)
    return pipeline_cls.init_from_config(config_path=config_path)


class VimaxEngine:
    """Engine V — ViMax story video, ``mode="finished"``."""

    name = "vimax"

    def __init__(
        self,
        *,
        config_factory: Callable[[Path, dict], dict],
        pipeline_factory: Callable[[str], Any] | None = None,
        overlay_fn: Callable[..., Any] | None = None,
        narration_llm_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._config_factory = config_factory
        self._pipeline_factory = pipeline_factory or _default_pipeline_factory
        # overlay_fn(video_path, task_dir, *, audio_override=None) -> Path.
        # Type is Callable[..., Any] to accept the audio_override kwarg (Phase 3.3).
        self._overlay_fn = overlay_fn
        # Phase 3: when provided, generate per-shot narration after ViMax render.
        # None = Phase 1/2 compatibility (use TrainerAI audio.mp3 via overlay).
        self._narration_llm_factory = narration_llm_factory

    async def render(self, ctx: EngineContext) -> EngineOutcome:
        request = ctx.request or {}
        pipeline_mode = request.get("pipeline") or "script2video"
        is_idea2video = pipeline_mode == "idea2video"

        # idea2video (旋钮①): one-line idea, NO script. ViMax expands idea → story → script.
        # script2video (旋钮② / default): caller supplies the script.
        if pipeline_mode == "novel2movie":
            # T1（2026-10-16 拆除）：novel2movie 管线已删（langchain 1.x 断裂 +
            # 摘牌期零消费，用户拍板决策①）。此处保留路由层守卫防绕过
            # _resolve_pipeline_class 的工厂直传——响亮报错，不静默换管线。
            raise RuntimeError(
                "novel2movie 已于 2026-10-16 拆除（langchain 1.x 断裂 + 摘牌期零消费，用户拍板不修直接删）：请改用 script2video（传入章节剧本）或 idea2video；详见 docs/plans/2026-10-16-vimax-degradation-contract-cleanup-plan.md"
            )
        elif is_idea2video:
            idea = request.get("idea") or request.get("topic")
            if not idea:
                raise ValueError("VimaxEngine: idea2video task payload has no `idea` (idea2video needs a one-line idea)")
        else:
            script = request.get("script")
            if not script:
                raise ValueError("VimaxEngine: task payload has no `script` (script2video needs a script)")

        # Plan B（2026-10-19，D4）：付费前防漏——ViMax 任务收到显式/存量 preset 音色
        # 在 pipeline factory（图像/视频生成入口）前即 ValueError。不静默映射（无权威
        # preset→豆包对照表，静默换声比失败更糟），文案给改选指引。doubao/Edge/clone
        # 放行；shared synthesize 的 reject 分支是同语义的第二道防线。
        ensure_vimax_voice_allowed(ctx.voice_id)

        working_dir = ctx.task_dir / "vimax_working"
        working_dir.mkdir(parents=True, exist_ok=True)

        config = self._config_factory(working_dir, request)
        config_path = working_dir / "config.yaml"
        config_path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")

        pipeline = self._pipeline_factory(str(config_path))
        # 2026-10-20 Task 3（已拍板）：仅 script2video 的分镜设计用
        # VIMAX_STORYBOARD_TIMEOUT_S（默认 420）；decompose 与 idea2video 保持 300。
        if not is_idea2video:
            from .storyboard_timeout import resolve_storyboard_timeout_s

            timeout_s = resolve_storyboard_timeout_s()
            configure = getattr(pipeline, "configure_storyboard_retry", None)
            if inspect.ismethod(configure):
                configure(max_attempts=2, timeout=timeout_s, task_retryable=False)
            else:
                # DI 测试替身（AsyncMock/窄 fake）无真实方法：直接落属性，
                # 避免调用 mock 生成未 await 的 coroutine。
                pipeline.storyboard_max_attempts = 2
                pipeline.storyboard_timeout = timeout_s
                pipeline.storyboard_task_retryable = False
        # T6（Lane 2, 2026-10-15）+ T3（2026-10-16 收口批）：静态降级计数 holder
        # （进度包装处按全局 gid 去重累计；帧级/视频级分键 + 场景偏移表）。
        static_fallback: dict = {"video_shots": set(), "frame_shots": set()}
        # T4（Lane 2, 2026-10-15）：pipeline 构造后的一切资源持有段包 try/finally——
        # vendored pipeline 持有桥接 generator（httpx client），成功/失败统一 close。
        try:
            user_requirement = request.get("topic") or (request.get("idea") or request.get("script") or "")[:80]
            # 边界热修复（2026-10-20 P1，PR#36 复审 Warning④）：**先**得出最终全局预算
            # （显式 vimax_global_shot_budget > target_duration 派生），**再**统一判定
            # 拆批与剥离——修前剥离判据只读显式预算且硬编码第二个 12，UI 派生路径
            # （只有 target_duration）拆批但不剥离，「exactly N scene」逐批注入与
            # 「只产本批片段」语义冲突。阈值复用 vendored SINGLE_BATCH_SHOT_THRESHOLD
            # （与 pipeline 拆批判定同源，不再长出第二份常数）。
            # 2026-10-20 已拍板：三层预算解析收敛为纯函数（显式 > target_duration
            # > 草稿 total_duration）；两者都缺失保持单批 + warning（不默认 180）。
            # UI 真实 payload 只有 total_duration（238.06 / 34.54），修前这里只看
            # target_duration → 恒单批 → 长稿 300s deadline（事故正路径）。
            from app.gateway.video_gen.engines.vimax.storyboard_budget import resolve_vimax_budget

            _resolved_budget = (
                None
                if is_idea2video
                else resolve_vimax_budget(
                    explicit_budget=request.get("vimax_global_shot_budget"),
                    target_duration=request.get("target_duration"),
                    storyboard_duration=request.get("total_duration"),
                )
            )
            _budget = _resolved_budget.shot_budget if _resolved_budget is not None else None
            if not is_idea2video and _resolved_budget is None:
                logger.warning(
                    "vimax budget missing for task %s (no explicit budget / target_duration / total_duration); keeping single batch",
                    ctx.task_id,
                )
            from vimax.utils.text import SINGLE_BATCH_SHOT_THRESHOLD

            _batching = not is_idea2video and _budget is not None and _budget > SINGLE_BATCH_SHOT_THRESHOLD
            # 边界收口（2026-10-19 P1-3）：拆批开启时剥离整剧 scene 级 extra
            # （「Generate exactly N scene」逐批注入会与「只产本批片段」冲突、
            # soft 低产无护栏），改注入全局镜头预算；≤阈值单批保留原 extra 语义不变。
            extra = request.get("user_requirement_extra", "")
            if _batching:
                user_requirement = f"{user_requirement}\n\nOverall shot budget for the full script: at most {_budget} shots."
            elif extra:
                # Phase 2: append user_requirement_extra (scene/shot limits) so ViMax's
                # Screenwriter + StoryboardArtist prompts see the constraints.
                user_requirement = f"{user_requirement}\n\n{extra}"
            style = ctx.style or _DEFAULT_STYLE

            if is_idea2video:
                # Idea2VideoPipeline.__call__(idea, user_requirement, style, progress=)
                # forwards progress to each per-scene Script2VideoPipeline so the task
                # bar advances during the bulk of the render (was omitted → 'stuck at
                # 75%' blind spot, identical to script2video pre-2.8).
                progress = _wrap_vimax_progress(ctx.progress, task_dir=ctx.task_dir, static_fallback=static_fallback) if ctx.progress is not None else None
                await pipeline(
                    idea=idea,
                    user_requirement=user_requirement,
                    style=style,
                    progress=progress,
                )
            else:
                progress = _wrap_vimax_progress(ctx.progress, task_dir=ctx.task_dir, static_fallback=static_fallback) if ctx.progress is not None else None
                # Plan C（2026-10-19，D6）：script2video 且宿主已提供**结构化**全局镜头
                # 预算（service 按 target_duration 计算，或上方 engine 派生）时透传
                # pipeline 拆批；idea2video 保持原路径（不传）。原 user_requirement_extra
                # 仍兼容，但不再是唯一预算来源；预算缺失/非法（≤0）= 单批。
                await pipeline(
                    script=request["script"],
                    user_requirement=user_requirement,
                    style=style,
                    progress=progress,  # bridges ViMax's sync stages → async task SSE (§10.3)
                    global_shot_budget=_budget,
                )

            out = working_dir / "final_video.mp4"

            # T2（Lane 2, 2026-10-15）：shots manifest 落 result（redo 端点的数据源）。
            # 修前生产方从不写 vimax_shots → redo 端点恒 404（前端入口死链）。
            # manifest 失败不 fail 渲染——redo 是事后能力，主产物已落盘。
            try:
                vimax_shots = await build_vimax_shots_manifest(working_dir, ctx.task_dir)
            except Exception:
                logger.exception("ViMax shots manifest collection failed; redo endpoint stays disabled for this task")
                vimax_shots = {}

            # Phase 3: generate per-shot narration audio (replaces TrainerAI's 22s audio
            # that was much shorter than ViMax's 222s video). Falls back to TrainerAI
            # audio when narration_llm_factory is None (Phase 1/2 compat) or on error.
            narration_audio = None
            # T3（Lane 2, 2026-10-15）：voice_mode_warning 标记只反映**本次**渲染——
            # 进入 narration 前清上一轮（失败重试/项目重渲）残留，防旧警告冒充本轮
            # 状态误导用户；本轮条件命中会在下方即时重写。
            (ctx.task_dir / "voice_mode_warning.txt").unlink(missing_ok=True)
            if self._narration_llm_factory is not None:
                try:
                    from .narration_generator import generate_vimax_narration

                    narration_llm = self._narration_llm_factory()
                    # T3：字幕朝向/PlayRes 从任务画幅推导（修前硬编码竖屏）。
                    orientation, video_w, video_h = _aspect_to_orientation_dims(ctx.aspect or request.get("aspect"))
                    # P1/P2: load ViMax's characters.json so narration runs in
                    # dialogue mode (per-speaker voices, actual character lines).
                    # Missing file → None → legacy single-voice LLM narration.
                    characters_path = working_dir / "characters.json"
                    characters = None
                    if characters_path.exists():
                        try:
                            characters = json.loads(characters_path.read_text(encoding="utf-8"))
                        except Exception:
                            logger.exception("Failed to load characters.json; narration falls back to legacy mode")
                            characters = None
                            # 2026-07-27 T16：用户可见化（曾只有服务端日志）——
                            # _task_with_thumbnail 把该标记文件并入 task.result。
                            (ctx.task_dir / "voice_mode_warning.txt").write_text("角色表解析失败，已回退单音色旁白（对话配音未生效）", encoding="utf-8")
                        else:
                            if not characters:
                                # 解析成功但为空表（degenerate 编剧输出）——narration_generator
                                # 会静默回退 legacy 单音色旁白，用户看到"配音只有旁白"却无提示
                                # （评审 E-1：解析异常/文件缺失已覆盖，空表是盲区）。
                                logger.warning("characters.json is empty in %s — legacy single-voice narration", working_dir)
                                (ctx.task_dir / "voice_mode_warning.txt").write_text("角色表为空，本片为单音色旁白（对话配音未生效）", encoding="utf-8")
                                # 已确知空表——下传 None 而非 []，短路 narration_generator 的
                                # `if not characters` 冗余重读分支（评审二轮 FU）。
                                characters = None
                    else:
                        # 文件缺失此前完全静默 → 落 legacy 单旁白 = "配音只有旁白"的用户体感
                        logger.info("characters.json not found in %s — legacy single-voice narration", working_dir)
                        (ctx.task_dir / "voice_mode_warning.txt").write_text("未生成角色表，本片为单音色旁白（如需多角色对话请检查编剧阶段）", encoding="utf-8")
                    narration_audio = await generate_vimax_narration(
                        working_dir=working_dir,
                        task_dir=ctx.task_dir,
                        original_script=request.get("script", ""),
                        llm=narration_llm,
                        # Plan B（2026-10-19）：voice_id 为空时按 request lang 走 ViMax
                        # 默认解析（D5 顺序：env speaker > 白名单首项 > Edge 语言默认），
                        # 不再落 preset:146 残余兜底。
                        voice_id=ctx.voice_id or resolve_vimax_default_voice(str(request.get("lang") or "zh")),
                        characters=characters,
                        # T7b（2026-10-14）：优先读 service 层三级解析结果（任务显式 >
                        # 项目 > config 缺省）——修前只读原始 payload，项目级 tts_speed
                        # 对 vimax 静默失效。默认 1.2 与 config.tts_speed 一致；service
                        # prep 的 1.0 仅用于时长估算（vimax skip_tts 不走该 TTS）。
                        speed=float(ctx.tts_speed or request.get("tts_speed") or 1.2),
                        orientation=orientation,
                        video_w=video_w,
                        video_h=video_h,
                    )
                    logger.info("ViMax narration generated: %s", narration_audio)
                except Exception:
                    logger.exception("ViMax narration generation failed; falling back to TrainerAI audio")
                    narration_audio = None

            if self._overlay_fn is not None:
                if narration_audio is not None:
                    out = await self._overlay_fn(out, ctx.task_dir, audio_override=narration_audio, subtitle_style=ctx.subtitle_style)
                else:
                    # Risk C: vimax uses skip_tts=True so audio.mp3 was never created.
                    # If narration also failed, the overlay would silently return a
                    # silent video (audio_overlay returns video_path when audio missing).
                    # Fail loud instead — a failed task the user can retry beats a
                    # "successful" silent video they don't notice.
                    fallback_audio = ctx.task_dir / "audio.mp3"
                    if not fallback_audio.exists():
                        raise RuntimeError("ViMax narration failed and no fallback audio.mp3 exists (skip_tts=True skipped TrainerAI TTS). Retry, or disable skip_tts for vimax to provide a fallback audio track.")
                    out = await self._overlay_fn(out, ctx.task_dir, subtitle_style=ctx.subtitle_style)
            engine_metadata: dict[str, Any] = {"vimax_shots": vimax_shots}
            # T6/T3：终态 result 可见计数摘要（sidecar 明细 + 此处；帧级/视频级分键）
            if static_fallback.get("video_shots"):
                engine_metadata["vimax_static_fallback_shots"] = len(static_fallback["video_shots"])
            if static_fallback.get("frame_shots"):
                engine_metadata["vimax_static_fallback_frames"] = len(static_fallback["frame_shots"])
            return EngineOutcome(mode="finished", finished_video_path=str(out), engine_metadata=engine_metadata)
        finally:
            await _close_bridge_generators(pipeline)


async def build_vimax_shots_manifest(working_dir: Path, task_dir: Path) -> dict[str, dict[str, Any]]:
    """T2：收集 vimax shots manifest（redo 端点数据源）。

    - 键 = **全局连续 id**（script2video 的 idx 本身连续；idea2video 多场景时
      scene_N 内 idx 重复，全局 id 去重保相邻镜 ±1 寻址成立），镜内原始 idx
      存 ``scene_shot_idx``。
    - 路径一律 **task_dir 相对**（result 持久化不绑服务器盘位）。
    - duration = ffprobe 实测（run_ffmpeg 池），失败落 narration 侧同款兜底值。
    - prompt = visual_desc（redo 重生提示词；与 redo 端点旧契约 v["prompt"] 一致）。
    """
    manifest: dict[str, dict[str, Any]] = {}
    gid = 0
    for sb_path in _find_storyboard_files(working_dir):
        scene_dir = sb_path.parent
        for shot in _load_shots(sb_path):
            scene_shot_idx = shot.get("idx", shot.get("index", gid))
            shot_dir = scene_dir / "shots" / str(scene_shot_idx)
            video_rel = _rel_to_task(shot_dir / "video.mp4", task_dir)
            video_exists = (shot_dir / "video.mp4").exists()
            frame = shot_dir / "first_frame.png"
            refs = [_rel_to_task(frame, task_dir)] if frame.exists() else []
            last_frame = shot_dir / "last_frame.png"
            if last_frame.exists():
                refs.append(_rel_to_task(last_frame, task_dir))
            if video_exists:
                duration = await run_ffmpeg(_get_shot_duration, shot_dir / "video.mp4")
            else:
                duration = _DEFAULT_SHOT_SECONDS
            entry = {
                "prompt": shot.get("visual_desc") or shot.get("description") or shot.get("shot_description") or "",
                "visual_path": refs[0] if refs else None,
                "video_path": video_rel,
                "duration": round(float(duration), 3),
                "reference_images": refs,
                "scene": scene_dir.name if scene_dir != working_dir else None,
                "scene_shot_idx": scene_shot_idx,
            }
            if not video_exists:
                # T5（2026-10-16 收口批）：缺失镜显式标记（路径保留供 redo 自愈
                # 重建）——redo 前置校验见他镜缺失即 422，不静默当正常镜。
                entry["video_missing"] = True
            manifest[str(gid)] = entry
            gid += 1
    return manifest


def _rel_to_task(path: Path, task_dir: Path) -> str:
    """path（working_dir 下）→ task_dir 相对路径串（manifest 持久化形态）。"""
    return str(path.relative_to(task_dir))


def _aspect_to_orientation_dims(aspect: str | None) -> tuple[str, int, int]:
    """T3：任务画幅 → (orientation, video_w, video_h)。

    16:9/横屏 → landscape 1920x1080；其余（含 None/9:16/1:1 等竖向缺省）→
    portrait 1080x1920（存量默认行为）。宽度口径与 subtitle_engine._MAX_WIDTH
    的 landscape(16)/portrait(13) 对齐。
    """
    a = (aspect or "").strip().lower()
    if a in ("16:9", "landscape", "horizontal"):
        return "landscape", 1920, 1080
    return "portrait", 1080, 1920


async def _close_bridge_generators(pipeline: Any) -> None:
    """T4（Lane 2, 2026-10-15）：close vendored pipeline 持有的桥接 generator。

    桥（Seedream360/Seedream/SeedanceVideo GeneratorForViMax）各持 httpx
    AsyncClient；修前零调用方关闭，每任务泄漏 2+ 连接。getattr 防御——
    pipeline 形态（测试替身/未来管线变更）无 generator 属性或无 close 时静默跳过；
    close 自身异常不吞渲染结果（best-effort，只留日志）。
    """
    import contextlib

    for attr in ("image_generator", "video_generator"):
        obj = getattr(pipeline, attr, None)
        close = getattr(obj, "close", None)
        if callable(close):
            with contextlib.suppress(Exception):
                await close()


# ── T2 (Lane 2, 2026-10-15)：redo 逐镜重做补全（用户拍板补全，不摘除）─────────
# （旧「Task 16 路线 A」redo_vimax_shot/RedoShot/RedoResult 死代码已删除——
# T5 收口批 2026-10-16：router 早已改走 service 新通路，旧函数零调用。）


class VimaxShotNotFound(KeyError):
    """redo 目标镜不在 task result 的 vimax_shots manifest 里。"""


class VimaxShotVideoMissing(ValueError):
    """退化态：非目标镜的原视频缺失（静态降级未产出/文件丢失），重拼无法进行。

    T5 收口批（2026-10-16）：修前 manifest 对缺失镜照记路径，redo 其他镜会在
    重拼段踩缺失文件裸 500。目标镜自身缺失不抛——redo 即自愈（新视频落位）。"""


async def _redo_generate_with_downgrade(
    provider: Any,
    prompt: str,
    *,
    references: list[str],
    duration: float,
    ratio: str | None,
) -> tuple[Any, str, str]:
    """决策①降级链：全量参考 → 首帧参考 → 纯 prompt（内容审核拒绝才降级）。

    返回 (VideoRef, reference_mode, note)。非审核类异常原样上浮（基础设施
    故障重试无益——对齐 registry 需求 #4「内容拒绝上浮」语义，但降级链是
    用户拍板的 redo 专用救援：参考图被拦不该废掉整次重做）。
    """
    from app.gateway.video_gen.providers.content_review import is_content_review_rejection

    async def _call(refs: list[str]):
        call_kwargs: dict[str, Any] = {"duration": duration}
        urls = [u for u in (_to_image_url(p) for p in refs) if u]
        if urls:
            call_kwargs["image_urls"] = urls
        if ratio:
            call_kwargs["ratio"] = ratio
        return await provider.generate_video(prompt, **call_kwargs)

    try:
        vref = await _call(references)
        # T5（2026-10-16 收口批）：mode 按实际请求形态推导——tier-0 成功 = 按可用
        # 全量参考（无降级发生），单参考不得误标 first_frame_only（修前按参考数
        # 推导，边界镜单参考挂着降级标签误导复核）。first_frame_only 只在真实
        # 降级到首帧档（下方 tier-1）时出现。
        mode = _REDO_MODE_FULL if references else _REDO_MODE_PROMPT
        return vref, mode, ""
    except RuntimeError as exc:
        if not references or not is_content_review_rejection(exc):
            raise
        first_exc = exc
    # 降级档一：仅首帧参考。OCR-1 返修（PR#26）：len(references) <= 1 时本档与
    # tier-0 是同一请求——审核拒绝后原样重打即白烧一单，跳过直接落纯 prompt。
    if len(references) > 1:
        try:
            vref = await _call(references[:1])
            return vref, _REDO_MODE_FIRST_FRAME, f"全量参考图被内容审核拒绝（{str(first_exc)[:120]}），已降级为仅首帧参考重做"
        except RuntimeError as exc:
            if not is_content_review_rejection(exc):
                raise
            # 降级档二：纯 prompt（参考图通路整体不可用，留痕不摘除）
            vref = await _call([])
            return vref, _REDO_MODE_PROMPT, f"参考图两连被内容审核拒绝（{str(first_exc)[:60]}；{str(exc)[:60]}），已降级为纯 prompt 重做"
    # 单参考（tier-0 与首帧档同请求，跳过白烧档）→ 直接纯 prompt
    vref = await _call([])
    return vref, _REDO_MODE_PROMPT, f"参考图被内容审核拒绝（{str(first_exc)[:120]}），已降级为纯 prompt 重做"


_SRT_TS_LINE = re.compile(r"^(\d{2}):(\d{2}):(\d{2})[,.](\d{3}) --> (\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*$")


def _shift_srt_cues(srt_text: str, *, boundary: float, delta: float) -> str:
    """终审 fix pass：把 start >= boundary 的 SRT cue 整体平移 delta 秒（文本手术）。

    redo 替换镜时长变化后，重拼画面从该镜起时间轴平移 delta——字幕 cue 须同移，
    否则边界后全片字幕渐进错位。start < boundary 的 cue（含跨边界起始的）不动
    （保守：跨边界 cue 的前半仍落在未变区域）。毫秒精度，钳制不为负。
    """
    lines = srt_text.splitlines(keepends=True)
    out: list[str] = []
    i = 0
    while i < len(lines):
        m = _SRT_TS_LINE.match(lines[i].rstrip("\r\n").strip()) if lines[i].strip() else None
        # 时间行须前有 cue 头（数字行）——按 SRT 块结构推进：非空行序列后跟空行
        if m:
            start = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3)) + int(m.group(4)) / 1000.0
            if start >= boundary - 1e-3:
                end = int(m.group(5)) * 3600 + int(m.group(6)) * 60 + int(m.group(7)) + int(m.group(8)) / 1000.0

                def _fmt(sec: float) -> str:
                    # 规划侧小修：整数毫秒运算防浮点进位产出非法 ",1000" 段
                    # （评审复现：01,118 + delta -0.118 → 0.999…9s → round = 1000）。
                    total_ms = max(0, int(round(sec * 1000)))
                    hh, rem = divmod(total_ms, 3_600_000)
                    mm, ss_ms = divmod(rem, 60_000)
                    ss, ms = divmod(ss_ms, 1000)
                    return f"{hh:02d}:{mm:02d}:{ss:02d},{ms:03d}"

                lines[i] = f"{_fmt(start + delta)} --> {_fmt(end + delta)}\n"
        out.append(lines[i])
        i += 1
    return "".join(out)


async def _adjust_narration_tail(audio_path: Path, *, at_seconds: float, delta: float) -> None:
    """在 at_seconds 边界插入（delta>0）/截除（delta<0）|delta| 秒，对齐重拼后的画面轴。

    delta>0（新镜更长）：边界补静音，后续语音同步推后。
    delta<0（新镜更短）：截掉边界处 |delta| 秒——镜尾多为 apad 静音（_align_chunk_to_shot），
    语音溢满的极端情况可能切到尾词；漂移已留 degradation 痕，用户可复核。
    失败 raise（调用方降级为沿用原音频 + 留痕）。
    """
    tmp = audio_path.with_name(audio_path.name + ".drift-tmp")
    audio_path.replace(tmp)
    try:
        if delta > 0:
            cmd = [
                "ffmpeg",
                "-y",
                "-i",
                str(tmp),
                "-f",
                "lavfi",
                "-t",
                f"{delta:.3f}",
                "-i",
                "anullsrc=r=24000:cl=mono",
                "-filter_complex",
                f"[0:a]asplit=2[a][b];[a]atrim=0:{at_seconds:.3f}[pre];[b]atrim={at_seconds:.3f}[post];[pre][1:a][post]concat=n=3:v=0:a=1[out]",
                "-map",
                "[out]",
                "-c:a",
                "libmp3lame",
                "-q:a",
                "4",
                str(audio_path),
            ]
        else:
            cut = max(0.0, at_seconds + delta)
            cmd = [
                "ffmpeg",
                "-y",
                "-i",
                str(tmp),
                "-filter_complex",
                f"[0:a]asplit=2[a][b];[a]atrim=0:{cut:.3f}[pre];[b]atrim={at_seconds:.3f}[post];[pre][post]concat=n=2:v=0:a=1[out]",
                "-map",
                "[out]",
                "-c:a",
                "libmp3lame",
                "-q:a",
                "4",
                str(audio_path),
            ]
        proc = await run_ffmpeg(subprocess.run, cmd, capture_output=True, timeout=300)
        if proc.returncode != 0 or not audio_path.exists() or audio_path.stat().st_size == 0:
            raise RuntimeError(f"narration drift adjust failed: {proc.stderr[-800:]!r}")
    except BaseException:
        # 规划侧 P1 返修：失败恢复。原件是唯一完好旁白——调用方对用户的承诺是
        # 「对齐失败沿用原音频」；修前 finally 无条件删 tmp 会把原件一并带走
        # （或留下 ffmpeg 半截产物冒充成功）。清半截产物 → tmp 归位 → 再上浮。
        audio_path.unlink(missing_ok=True)
        if tmp.exists():
            tmp.replace(audio_path)
        raise
    tmp.unlink(missing_ok=True)  # 仅成功路径清理


# 规划侧 P1-2 返修：漂移已应用标记。对齐先于 concat/overlay 执行（overlay 要烧
# 校正后的 subtitle.srt，不能后置），中途失败重试时 manifest 时长未回写 → delta
# 重算相同——无标记会二次平移 SRT/二次剪切旁白。标记记「同镜 + 基线时长 +
# 已应用累计平移 + 各部件完成位」：同参全成功重试零操作；同参部分完成只补
# 未完成部件（OCR-3：narration 首败不再被整单标记吞掉）；异参重试补差值
# （此前剪掉的音频不可恢复，degradation 留痕兜底）。
_REDO_DRIFT_MARKER = "redo_drift_marker.json"


def _read_drift_marker(task_dir: Path) -> dict | None:
    try:
        data = json.loads((task_dir / _REDO_DRIFT_MARKER).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _write_drift_marker(task_dir: Path, *, shot_id: int, old_duration: float, applied_delta: float, srt_applied: bool, narration_applied: bool) -> None:
    # OCR-3 返修（PR#26）：按部件记录——narration 对齐失败时同参重试不再被整单
    # 标记吞掉（SRT 已平移则防二次，narration 未成功则补试）。
    try:
        (task_dir / _REDO_DRIFT_MARKER).write_text(
            json.dumps(
                {
                    "shot_id": shot_id,
                    "old_duration": round(old_duration, 3),
                    "applied_delta": round(applied_delta, 3),
                    "srt_applied": srt_applied,
                    "narration_applied": narration_applied,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except Exception:
        logger.warning("vimax redo: drift marker write failed; retry idempotency degraded", exc_info=True)


async def _apply_redo_drift_alignment(task_dir: Path, *, boundary: float, delta: float, shot_id: int, old_duration: float, new_duration: float) -> float:
    """时长漂移对齐 + 可见留痕（SRT 平移 / narration 边界调整 / degradation）。

    返回本次**实际应用**的净平移量（幂等语义下可能为 0）。各子步独立
    best-effort：narration 调整失败沿用原音频（漂移痕已可复核），SRT 平移是
    纯文本操作不应失败；旧 .ass（派生、时间轴已失真）清除——overlay 烧录回落
    用校正后的 .srt（audio_overlay 优先 .ass 的语义）。
    """
    from app.gateway.video_gen.degradations import record_degradation

    marker = _read_drift_marker(task_dir)
    matched = marker is not None and marker.get("shot_id") == shot_id and isinstance(marker.get("old_duration"), (int, float)) and abs(float(marker["old_duration"]) - round(old_duration, 3)) < 1e-6
    srt_done = bool(marker.get("srt_applied")) if matched else False
    narration_done = bool(marker.get("narration_applied")) if matched else False
    applied = float(marker.get("applied_delta") or 0.0) if matched else 0.0

    effective = delta
    narration_amount = delta
    if matched:
        net = delta - applied
        if abs(net) < 1e-9 and srt_done and narration_done:
            logger.info("vimax redo: shot %s 漂移 %+.3fs 已全量应用（marker 命中，失败重试）；零操作", shot_id, delta)
            return 0.0
        if abs(net) >= 1e-9:
            # 异参重试：两部件都按差值再动（此前剪掉的音频不可恢复，留痕兜底）
            effective = net
            narration_amount = net
            srt_done = False
            narration_done = False
            logger.info("vimax redo: shot %s 漂移重算 %+.3fs（此前已应用 %+.3fs），补差 %+.3fs", shot_id, delta, applied, net)
        else:
            # OCR-3：同参重试且有部件未完成——SRT 已平移则跳过（防二次平移）；
            # narration 未成功则全量 delta 补试（该文件从未被成功改动）。
            logger.info("vimax redo: shot %s 同参重试：srt_done=%s narration_done=%s，补未完成部件", shot_id, srt_done, narration_done)

    srt_applied_now = srt_done
    srt = task_dir / "subtitle.srt"

    # T4（2026-10-16 收口批）：每部件成功后立即增量落 marker——SRT 平移成功即写
    # srt_applied，narration 成功即写 narration_applied。修前两部件完成后一次性
    # 写：SRT 平移后进程崩溃/marker 写失败 → 同参重试二次平移（负 delta 音频
    # 二次截除不可恢复）。中间态标记的 applied_delta 恒为目标累计值（delta），
    # 与终态语义一致（异参重试按 delta-applied 补差）。
    def _persist(srt_flag: bool, narration_flag: bool) -> None:
        _write_drift_marker(task_dir, shot_id=shot_id, old_duration=old_duration, applied_delta=delta, srt_applied=srt_flag, narration_applied=narration_flag)

    if not srt_done and srt.exists():
        shifted = _shift_srt_cues(srt.read_text(encoding="utf-8"), boundary=boundary, delta=effective)
        srt.write_text(shifted, encoding="utf-8")
        (task_dir / "subtitle.ass").unlink(missing_ok=True)
        srt_applied_now = True
        _persist(True, narration_done)  # 增量：SRT 成功即落盘
    narration = task_dir / "vimax_narration.mp3"
    adjust_note = ""
    if not narration.exists():
        narration_done = True  # 无旁白文件=无事可做，防无意义补试
    if not narration_done:
        try:
            await _adjust_narration_tail(narration, at_seconds=boundary, delta=narration_amount)
            narration_done = True
            _persist(srt_applied_now, True)  # 增量：narration 成功即落盘
        except Exception:
            logger.exception("vimax redo: narration drift adjust failed; keeping original audio (desync remains, see trail)")
            adjust_note = "；旁白音频对齐失败，沿用原音频（边界后可能音画错位）"
    record_degradation(
        task_dir,
        kind="vimax_redo_duration_drift",
        detail=f"第 {shot_id + 1} 镜重做时长 {old_duration:.2f}s → {new_duration:.2f}s（漂移 {delta:+.2f}s，本次净平移 {effective:+.2f}s），已按边界平移字幕{'; 旁白音频边界同步调整' if not adjust_note else adjust_note}",
        frame_index=shot_id,
    )
    _persist(srt_applied_now, narration_done)  # 终态（narration 失败时 narration_applied=False）
    return effective


async def redo_vimax_shot_task(
    *,
    task_dir: Path,
    working_dir: Path,
    shots_raw: dict[str, dict],
    shot_id: int,
    character_front: str | None,
    provider: Any,
    ratio: str | None = None,
    burn_subtitles: bool = True,
    subtitle_style: str = "default",
) -> dict[str, Any]:
    """redo 全链：单镜重生（含参考降级）→ 替换镜视频 → 重拼静默成片 → 重跑交付。

    修前三断点之二/之三：``_gen`` 丢 ``reference_images``（组装白算）；结果不
    回写不重拼（烧钱返回成功但视频不变）。本函数做生成侧全部工作；result 落库
    由 service 接线方（VideoGenService.redo_vimax_shot）合并持久化。
    """
    sid = str(shot_id)
    entry = shots_raw[sid]

    # T5（2026-10-16 收口批）：退化态前置校验——他镜原视频缺失时重拼必然失败
    # （concat 踩缺失文件裸 500），进 provider 前明确拒绝；目标镜自身缺失放行
    # （redo 即自愈：新视频落位后重拼恢复）。兼容旧 manifest（无 video_missing
    # 键）：回落按盘上存在性判定。
    missing_others = [k for k, v in shots_raw.items() if str(k).isdigit() and k != sid and (v.get("video_missing") or not (task_dir / str(v.get("video_path") or "")).exists())]
    if missing_others:
        raise VimaxShotVideoMissing(f"镜 {','.join(sorted(missing_others, key=int))} 的原视频缺失（静态降级未产出/文件丢失），重拼无法进行；请重新渲染任务或先重做缺失镜")

    def _resolve_ref(p: str | None) -> str | None:
        """manifest 相对路径 → 绝对；URL 形态（http(s)://、data:）原样透传。

        OCR-2 返修（PR#26）：修前 ``Path("https://…").is_absolute()`` 恒 False →
        拼成 ``task_dir / "https:/…"`` 垃圾路径（Path 折叠双斜杠）下发 provider。
        """
        if not p:
            return None
        if p.startswith(("http://", "https://", "data:")):
            return p
        path = Path(p)
        return str(path if path.is_absolute() else (task_dir / path))

    references = [r for r in ([_resolve_ref(character_front)] + [_resolve_ref(shots_raw[str(k)].get("visual_path")) for k in (shot_id - 1, shot_id + 1) if str(k) in shots_raw]) if r]

    duration = float(entry.get("duration") or _DEFAULT_SHOT_SECONDS)
    vref, mode, note = await _redo_generate_with_downgrade(provider, entry.get("prompt", ""), references=references, duration=duration, ratio=ratio)
    new_url = getattr(vref, "url", None)
    if not new_url:
        raise RuntimeError("vimax redo: provider returned no url")

    # 单镜替换：先落临时文件再原子换名——下载/写半截不破坏原镜
    shot_video = task_dir / entry["video_path"]
    shot_video.parent.mkdir(parents=True, exist_ok=True)
    tmp = shot_video.with_name(shot_video.name + ".redo-tmp")
    content = await safe_download(new_url, timeout=300, headers={"User-Agent": "TrainerAI-VideoGen/1.0 (vimax redo)"})
    tmp.write_bytes(content)
    tmp.replace(shot_video)
    new_duration = await run_ffmpeg(_get_shot_duration, shot_video)

    # 终审 fix pass（Important 1）：时长漂移对齐。替换镜时长与原镜有差（provider 量化/
    # 换通道）时，重拼画面从本镜起平移 delta——narration/字幕仍按旧轴 → 边界后全片
    # 音画错位。超容差即平移 SRT + 调整 narration 边界 + 留痕；其内视为不可感知。
    old_duration = float(entry.get("duration") or _DEFAULT_SHOT_SECONDS)
    drift = new_duration - old_duration
    duration_drift: float | None = None
    if abs(drift) >= _REDO_DRIFT_TOLERANCE:
        duration_drift = round(drift, 3)
        boundary = sum(float(shots_raw[k].get("duration") or 0.0) for k in shots_raw if str(k).isdigit() and int(k) < shot_id)
        await _apply_redo_drift_alignment(task_dir, boundary=boundary, delta=drift, shot_id=shot_id, old_duration=old_duration, new_duration=new_duration)

    # 重拼静默成片：vendored concat 幂等（final 存在会跳过）→ 先清旧产物
    final_silent = working_dir / "final_video.mp4"
    final_silent.unlink(missing_ok=True)
    ordered = [str(task_dir / shots_raw[k]["video_path"]) for k in sorted(shots_raw, key=int)]
    await run_ffmpeg(concatenate_video_files, ordered, str(final_silent))

    # 重跑交付（音频混音 + 字幕烧录 + loudnorm）：复用持久化的 narration/字幕
    narration = task_dir / "vimax_narration.mp3"
    deliverable = await overlay_audio_track(
        final_silent,
        task_dir,
        audio_override=narration if narration.exists() else None,
        burn_subtitles=burn_subtitles,
        subtitle_style=subtitle_style,
    )
    return {
        "at": datetime.datetime.now(datetime.UTC).isoformat(),
        "reference_mode": mode,
        "note": note,
        "duration_drift": duration_drift,
        "new_duration": round(float(new_duration), 3),
        "output_path": str(deliverable),
        "file_size": Path(deliverable).stat().st_size,
    }
