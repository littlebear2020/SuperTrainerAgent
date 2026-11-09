"""AI video visual source: generate video clips per frame via 360-海螺, fill visual_url.

HR-T1：单帧 provider 失败时生成静态占位 mp4 写 local_visual_path（修黑帧/失同步）。
复用 vimax._static_frame._make_solid_color_video。全部失败仍抛。
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import re
import subprocess
from collections.abc import Awaitable, Callable
from pathlib import Path

import httpx

from ..degradations import (
    KIND_CHARACTER_IMAGE_REJECTED,
    KIND_LOW_VISUAL_QUALITY,
    KIND_PREVIEW_I2V_UNSUPPORTED,
    KIND_PREVIEW_IMAGE_REJECTED,
    KIND_PROMPT_FALLBACK,
    KIND_PROMPT_NEUTRALIZED,
    KIND_VIDEO_EXTENDED,
    KIND_VISUAL_SHORTFALL,
    record_degradation,
)
from ..duration_policy import align_to_provider_grid, duration_shortfall
from ..orchestration.storyboard_planner import _topic_fallback_prompt
from ..orchestration.visual_routing import _OFF_VALUES
from ..prompt_translators import translate_video_prompt
from ..providers.base import VideoRef
from ..providers.content_review import is_content_review_rejection, is_image_content_rejection, neutralize_prompt
from ..storyboard import Storyboard, StoryboardFrame
from ._global_concurrency import get_ai_api_semaphore
from ._progress_watchdog import DEFAULT_NO_PROGRESS_TIMEOUT as _NO_PROGRESS_TIMEOUT
from ._progress_watchdog import gather_with_no_progress_watchdog

# B2 (Task 9): ai_video 自己的覆盖率下限，**故意低于** ai_image 的 0.6。
#
# 2026-07-13 commit cb38966ff「ai_video reports partial-coverage status (no hard
# fail)」已明确决策：ai_video 半数占位时**不硬失败**，靠 visual_coverage.json
# sidecar 告警（理由：占位保 A/V 同步，硬失败会丢掉已成功的帧）。ai_image 无此
# 占位机制（走 image_reuse），故可以更严。
#
# 0.5 让两个契约同时成立：50% 占位仍按 07-13 决策交付+告警；9/10 失败（10%）
# 则响亮失败，不再静默交付一整片灰占位标「已完成」。
from .budget import VisualBudgetExceeded  # noqa: E402

MIN_COVERAGE = 0.5

logger = logging.getLogger(__name__)


_LOCALIZE_RETRY_DELAYS = [0, 1, 2]  # 秒；测试可 monkeypatch 加速

# Ark ratio 支持集（API 参考 §2.3:141）；不在集合内的自定义比例回退 adaptive
_ARK_SUPPORTED_RATIOS = {"21:9", "16:9", "4:3", "1:1", "3:4", "9:16"}


def _size_to_ratio(width: int, height: int) -> str:
    """像素尺寸 → Ark ratio 字符串（约分；不支持的比例 → "adaptive"）。

    P0-1 修复：ai_video 此前从不把目标比例传给 provider，9:16 任务拿回默认比例
    被 Worker cover 裁掉 ~2/3 画面。
    """
    if width <= 0 or height <= 0:
        return "adaptive"
    g = math.gcd(width, height)
    r = f"{width // g}:{height // g}"
    return r if r in _ARK_SUPPORTED_RATIOS else "adaptive"


def _video_frame_entity_refs(frame, character_roster: dict, prop_roster: dict) -> tuple[list[dict], list[str], list[dict], list[dict], list[str] | None]:
    """M5：视频侧帧级参考选择（决策 17：同一实体只带 1 张视图）+ 合成图方案（X7 同构）。

    与 ai_image 侧三视图全传（R6 判定后默认）不同：视频参考图是 r2v 参考材料
    而非生图编辑基底，每实体 1 张。**角色恒 1 张 ``entry["composite"]``（三视图
    合成图，2026-08-27 计划——决策 1「分离视图不再进注入通道」/决策 3「角色
    view 提示退役」在本侧同样生效）**；无 composite → 回退 view 选择（兜底）。
    道具逻辑不动（main/extra view 选择保留，不改项）。
    帧输入顺序 角色 → 道具（M5 卡），叠加预算由调用方 apply_ref_cap 处理。

    返回 ``(injected, declared, truncated, skipped, names)``：
    - injected: ``[{"entity", "path", "view"}]``（角色在前道具在后，sidecar 全量留痕用；
      角色合成图 view 记 "composite"）
    - declared: 声明实体名（角色+道具）；None 帧取隐式首主角（语义铁律）
    - truncated: 声明超限（角色 >3 / 道具 >2，决策 17）
    - skipped: 道具文件缺失（file_missing；角色图信任名册不查盘，同 ai_image 口径）
    - names: M4 带名方言用——**仅声明帧（visible_characters 非空）且全部角色走合成图
      时非 None**，与 injected 1:1 同序；回退视图路径（无 composite 存量 sheet）→ None
      （方言降通用措辞——单张视图不虚标「三视图」，2026-08-27 复核 3.4）；None 帧
      （旧三视图全传语义，names 维持 None）→ 现状文案。
    """
    vc = getattr(frame, "visible_characters", None)
    vp = getattr(frame, "visible_props", None)
    injected: list[dict] = []
    declared: list[str] = []
    truncated: list[dict] = []
    skipped: list[dict] = []
    names: list[str] | None = None
    if vc is None:
        # 语义铁律：None（存量/无声明）→ 首主角（roster 首键 = sheets[0]，与 ai_image
        # `_frame_character_ref_paths` 同源语义）。X7：有合成图 → 1 张 composite
        # （names 维持 None——旧帧语义保守不升级带名）；无 → 三视图全传（旧行为）。
        for name, entry in character_roster.items():
            composite = entry.get("composite") or ""
            if composite:
                injected = [{"entity": name, "path": composite, "view": "composite"}]
            else:
                views = [(v, entry.get(f"{v}_url") or "") for v in ("front", "side", "back")]
                injected = [{"entity": name, "path": u, "view": v} for (v, u) in views if u]
            declared = [name]
            break
    elif vc:
        declared += [i.get("name") for i in vc if isinstance(i, dict) and i.get("name")]
        truncated += [{"entity": f"character:{i.get('name')}", "reason": "char_cap_3"} for i in vc[3:] if isinstance(i, dict)]
        fallback_used = False  # 任一角色走视图回退 → names=None（方言不虚标三视图）
        for item in vc[:3]:  # 决策 17：每镜角色 ≤3
            if not isinstance(item, dict) or not item.get("name"):
                continue
            roster_entry = character_roster.get(item.get("name"))
            if not roster_entry:
                continue  # 名册外（planner 已拦幻觉名，防御性再跳过）→ 按无声明处理
            composite = roster_entry.get("composite") or ""
            if composite:
                # X7：角色恒 1 张合成图——view 提示对角色不再生效（决策 3，合成图恒含全视角）
                injected.append({"entity": item["name"], "path": composite, "view": "composite"})
                continue
            # 兜底：无合成图 → view 选择（缺省 front；X9 后仅旧路径兜底使用）
            view = item.get("view") if item.get("view") in ("front", "side", "back") else "front"
            url = roster_entry.get(f"{view}_url") or roster_entry.get("front_url") or ""
            if url:
                fallback_used = True
                injected.append({"entity": item["name"], "path": url, "view": view})
        names = None if fallback_used else [e["entity"] for e in injected]
    # vc == [] → 本镜明确无人物：不注入角色（道具/兜底照常）
    # 道具独立处理（与 ai_image 两侧独立同语义；None = 无道具旧行为）
    if vp:
        declared += [i.get("name") for i in vp if isinstance(i, dict) and i.get("name")]
        truncated += [{"entity": f"prop:{i.get('name')}", "reason": "prop_cap_2"} for i in vp[2:] if isinstance(i, dict)]
        for item in vp[:2]:  # 决策 17：每镜道具 ≤2
            if not isinstance(item, dict) or not item.get("name"):
                continue
            roster_entry = prop_roster.get(item.get("name"))
            if not roster_entry:
                continue
            view = item.get("view")
            if isinstance(view, int) and not isinstance(view, bool) and view >= 1:
                extras = roster_entry.get("extra_images") or []
                if view - 1 >= len(extras):
                    continue  # 越界补角度 → 跳过该条（按无声明处理）
                path = extras[view - 1]
                chosen = view
            else:
                path = roster_entry.get("main_image")
                chosen = 0
            if path and Path(path).exists():
                injected.append({"entity": item["name"], "path": path, "view": chosen})
                if names is not None:
                    names.append(item["name"])  # 1:1 跟随真实注入（跳过/缺失不留名）
            elif path:
                skipped.append({"entity": f"prop:{item['name']}", "path": path, "reason": "file_missing"})
    return injected, declared, truncated, skipped, names


async def _localize_video(url: str, frame: StoryboardFrame, task_dir: Path, *, dest_name: str | None = None) -> str | None:
    """D1: 下载远程视频到 task_dir/visuals/shot_NNNN.mp4，返回相对路径。

    成功镜生成后立即本地化，消除 Worker 合成期对远程 URL 的依赖（纵深防御：远程临时
    URL 过期 / CDN 返回截断内容时被 Worker 裸下载吞成黑帧）。带 UA + 重试 + ≥1KB 内容
    校验；失败返 None，调用方保留 remote url，由 Worker _download_asset（Phase E1）兜底。

    C2 修复（评审）：dest_name 可选参数让续写片落地到不同文件名（ext_NNNN.mp4），
    避免覆盖原片 shot_NNNN.mp4。
    """
    visuals_dir = task_dir / "visuals"
    visuals_dir.mkdir(parents=True, exist_ok=True)
    rel = dest_name or f"visuals/shot_{frame.index:04d}.mp4"
    out = task_dir / rel
    headers = {"User-Agent": "TrainerAI-VideoGen/1.0 (video localize)"}
    last_err = None
    for delay in _LOCALIZE_RETRY_DELAYS:
        try:
            async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
                resp = await client.get(url, headers=headers)
                resp.raise_for_status()
                content = resp.content
            if len(content) < 1024:
                last_err = f"content too small ({len(content)} bytes, likely error page)"
            else:
                out.write_bytes(content)
                return rel
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
        if delay:
            await asyncio.sleep(delay)
    logger.warning("localize video failed for frame %d: %s (keep remote url)", frame.index, last_err)
    return None


def _probe_video_seconds(path: Path) -> float:
    """ffprobe 视频实际时长；失败返 0.0（不抛）。

    Task 3：gateway 侧首次实测「模型到底给了几秒」。修前 ``VideoRef.duration`` 只是
    回显请求值（``providers/seedance_ark.py``），与 Ark 端 ``int(round())`` 钳位
    结果不一致，下游无从判断是否需要补偿。
    ffprobe 随 ffmpeg 装在 gateway 镜像（``backend/Dockerfile``）。
    """
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if proc.returncode != 0:
            return 0.0
        return float((proc.stdout or "").strip() or 0.0)
    except Exception:
        logger.warning("ffprobe failed for %s", path, exc_info=True)
        return 0.0


def _extract_tail_frame(video_path: Path, output_png: Path) -> bool:
    """从视频末尾抽帧到 PNG（-sseof -0.3 避开文件尾坏帧）。Phase 2 续写 fallback。"""
    try:
        proc = subprocess.run(
            ["ffmpeg", "-y", "-sseof", "-0.3", "-i", str(video_path), "-frames:v", "1", "-q:v", "2", str(output_png)],
            capture_output=True,
            timeout=30,
        )
        return proc.returncode == 0 and output_png.exists()
    except Exception:
        logger.warning("tail frame extraction failed for %s", video_path, exc_info=True)
        return False


def _concat_videos_with_check(seg_a: Path, seg_b: Path, output: Path, *, timeout: int = 300) -> bool:
    """concat 两段视频（filter_complex 重编码 + ffprobe 校验时长），成功返 True。

    Phase 2 续写拼接。M1（评审）：函数名暗示 stream copy 优先但实际只走 filter_complex
    重编码——续写片与原片帧率/编码可能不一致，统一重编码 + 30fps 归一最安全。
    gateway 侧每续写帧付一次重编码 CPU 是已知成本。
    """
    try:
        dur_a = _probe_video_seconds(seg_a)
        dur_b = _probe_video_seconds(seg_b)
        expected = dur_a + dur_b
        # filter_complex concat（统一重编码，避免混规格段静默丢时长）
        concat_cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(seg_a),
            "-i",
            str(seg_b),
            "-filter_complex",
            "[0:v][1:v]concat=n=2:v=1:a=0[v]",
            "-map",
            "[v]",
            "-c:v",
            "libx264",
            "-crf",
            "20",
            "-preset",
            "fast",
            "-pix_fmt",
            "yuv420p",
            "-an",
            str(output),
        ]
        proc = subprocess.run(concat_cmd, capture_output=True, timeout=timeout)
        if proc.returncode == 0 and output.exists():
            got = _probe_video_seconds(output)
            # 非对称容差 [−0.3, +0.5]（C2 2026-08-14，与 worker _concat_two_with_duration_check
            # 同口径）：短超 0.3s = 时长被静默压缩，不可信；长超 0.5s 才不可信。
            if expected > 0 and (got >= expected - 0.3) and (got <= expected + 0.5):
                return True
            logger.warning("concat duration off: got %.3fs, expected %.3fs", got, expected)
        return False
    except Exception:
        logger.warning("concat failed", exc_info=True)
        return False


async def _make_placeholder_clip(frame: StoryboardFrame, task_dir: Path, *, width: int = 1920, height: int = 1080) -> str:
    """单帧视频生成失败的静态占位（修黑帧/失同步）。

    生成 frame.duration 时长的纯色 mp4，返回相对路径写 local_visual_path，
    使该帧在 Worker 拼接中保留（与 TTS 同步）而非被丢弃。复用 ViMax B1 纯色兜底。
    B2: width/height 须匹配目标比例（9:16 任务传 1080x1920），避免黑帧边界跳变。
    """
    from ..engines.vimax._static_frame import _make_solid_color_video

    visuals_dir = task_dir / "visuals"
    visuals_dir.mkdir(parents=True, exist_ok=True)
    rel = f"visuals/placeholder_{frame.index:04d}.mp4"
    out = task_dir / rel
    duration = float(frame.duration) if frame.duration and frame.duration > 0 else 5.0
    # _make_solid_color_video 是同步 ffmpeg 调用，放线程里避免阻塞事件循环
    await asyncio.to_thread(_make_solid_color_video, str(out), duration, "black", width=width, height=height)
    return rel


class AIVideoVisualSource:
    def __init__(
        self,
        provider,
        *,
        concurrency: int = 2,
        global_limit: int | None = None,
        budget=None,
        extend_enabled: bool = True,
        extend_min_gap: float = 3.0,
        extend_max_segments: int = 1,
        provider_name: str | None = None,
        duration_safety_factor: float = 1.25,
    ) -> None:
        self._provider = provider
        # Phase 1 T5（2026-08-17 方言层）：provider 名用于方言路由
        # （minimax-h3 -> h3 三字段；seedance* -> 五槽；其余/无 IR -> legacy）。
        self._provider_name = provider_name
        self._concurrency = concurrency
        # E6 (Task 13): 单任务成本闸（调用数 + 金额）。None = 不限（向后兼容）。
        self._budget = budget
        # 闸1: process-wide upstream API cap (mirrors ai_image).
        self._global_limit = global_limit
        # Phase 2: 续写配置（constructor 注入，与 budget 同路径）
        self._extend_enabled = extend_enabled
        self._extend_min_gap = extend_min_gap
        self._extend_max_segments = extend_max_segments
        # E9 超发裁剪保险系数（2026-08-25 补齐）：请求时长 = 旁白 × 系数对齐栅格
        # （封顶单次上限），欠交付永不落到 tpad 定格；1.0 = 旧行为（配置级回退）。
        self._duration_safety_factor = duration_safety_factor

    async def render(
        self,
        storyboard: Storyboard,
        *,
        only_frames: list[int] | None = None,
        on_frame_done: Callable[[int], Awaitable[None]] | None = None,
        task_dir: Path | None = None,
        reference_image_paths: list[str] | None = None,
        style_anchor_image_paths: list[str] | None = None,  # M5：视频侧锚图兜底（优先级末位）
        character_roster: dict | None = None,  # M5：name → {front_url, side_url, back_url}（帧级子集）
        prop_roster: dict | None = None,  # M5：name → {main_image, extra_images}
    ) -> None:
        """HR-T1: task_dir 用于部分失败时写静态占位；None 时失败帧留 visual_url=None（旧行为）。

        B3': reference_image_paths 作 image_urls 传给 seedance（first_frame + reference_image，
        B0），实现 ai_video 角色一致性——seedance 不拦 seedream 生成的角色图（信任产物）。
        C4（决策 13）→ M5（2026-08-26 二期下半场）：style_anchor_image_paths 转为**兜底
        消费**——注入优先级写死：角色/道具 refs（帧级子集，非空）> 组锚（现状条件）>
        预览图继承（现状条件）> 视频侧风格锚图（仅前三者皆空且开关开时 refs=[锚图]，
        kind="style_anchor"）；开关复用 VIDEO_GEN_STYLE_ANCHOR_IMAGE（C5 回退位）。
        roster 生效（任一非空）→ 任务级 reference_image_paths 的角色用途停用（同 ai_image
        M3 过渡关系），帧级选择 + 逐帧 apply_ref_cap + sidecar 全量留痕（决策 17/19）。
        """
        sem = get_ai_api_semaphore(self._global_limit)  # 闸1: process-wide, not per-instance
        errors: list[str] = []

        # M5：roster 帧级子集 + 视频侧锚图兜底（惰性导入避免环：service → visual_factory → 本模块，
        # 同 ai_image 先例——apply_ref_cap/record_ref_injection_sidecar 是 C10 纯函数/留痕写手）。
        from ..service import apply_ref_cap, record_ref_injection_sidecar

        _roster_mode = bool(character_roster or prop_roster)
        _style_anchor_fallback = None
        if style_anchor_image_paths and os.environ.get("VIDEO_GEN_STYLE_ANCHOR_IMAGE", "1").strip().lower() not in _OFF_VALUES:
            _style_anchor_fallback = style_anchor_image_paths[0]

        # R3/QP1-4: provider 不支持 image_urls（360-only 部署等）→ 参考图被忽略——
        # 除 log 外写 sidecar 标记，任务详情可见（修前仅一条用户不可见的 logger.warning）。
        refs_ignored = bool(reference_image_paths) and not getattr(self._provider, "supports_image_urls", False)
        if refs_ignored:
            logger.warning("ai_video provider lacks image_urls support — character reference paths will be ignored. Consider using seedance-ark backend for character consistency.")
        # degraded_without_refs 是 provider 单例（FallbackAIVideoProvider）上的实例标记，
        # 跨任务存活——render 开始时清零，避免上一任务的降级污染本次 sidecar
        # （并发任务间的归属为 best-effort，仅警告用途）。
        if hasattr(self._provider, "degraded_without_refs"):
            self._provider.degraded_without_refs = False
        # 偏离项 3（2026-08-19）：H3/happyhorse 多图截断打标同语义——render 开始
        # 清零（provider 单例跨任务，经包裹链 reset_multi_image_truncated 转发）。
        _reset_trunc = getattr(self._provider, "reset_multi_image_truncated", None)
        if callable(_reset_trunc):
            _reset_trunc()

        # Task 5：provider 不支持 i2v 时，预览图无法作首帧 → 记录受影响帧，
        # render 末尾汇总写一条 sidecar（逐帧写会刷屏）。
        preview_i2v_skipped: list[int] = []

        target = storyboard.frames
        if only_frames is not None:
            target = [f for f in storyboard.frames if f.index in only_frames]

        async def fill(frame) -> None:
            # QP1-6 最后防线：image_prompt 为空也绝不把 narration 当画面词发给 provider。
            # 2026-08-01（Task 2）：改为共用 _topic_fallback_prompt（已中文化），
            # 消除与 ai_image.py / planner 之间同一段英文的三份拷贝。
            # Task 8：拼入镜头语言（shot_size/camera_move）。无字段时与旧行为完全一致。
            # P0-2: 上限 provider 化——seedance-ark 原生 15s（海螺遗留 8s 注释作废）；
            # qihu 无 max_video_duration 属性 → 保持 8s 旧行为。
            cap = getattr(self._provider, "max_video_duration", 8.0)
            # Task 2/3：对齐到 provider 可达栅格（Ark 整数秒 [4,15]）。向上取整——
            # 长了 worker -t 截断无损，短了才要 tpad 定格（观感突兀）。
            floor = float(getattr(self._provider, "min_video_duration", 0.0) or 0.0)
            integer_only = bool(getattr(self._provider, "integer_seconds_only", False))
            gen_duration = align_to_provider_grid(
                frame.duration or cap,
                cap=cap,
                floor=floor,
                integer_seconds=integer_only,
                # E9 超发裁剪：×safety_factor 后仍被 min(cap) 封顶——旁白超单次上限
                # 的帧走续写而非超发；frame.duration 缺省（=cap）时 min 回 cap 不变。
                safety_factor=self._duration_safety_factor,
            )
            # Task 5 → 机制 M（2026-08-31）：预览图（帧自身的 local_visual_path，
            # visual_type=image）作 i2v 首帧下发，消除「预览图与成片两次独立生成、
            # 构图不一致」。
            #
            # M 起预览图与角色/道具参考**合并**（取代 08-17 互斥决策）：预览图恒放
            # 数组首位——首帧语义双重依赖此顺序（apply_ref_cap 保前缀截断护住首帧；
            # H3 `_first_frame_only` 取首图）。组锚/风格锚兜底仅在无预览图时生效
            # （组锚语义 =「无预览图帧」的场景兜底）。
            # 通道形态（T2.0 探针结果 a，任务 cgt-20260831114226-pxkd5）：360
            # seedance role 缺省多图 = 首图首帧 + 其余参考（与 Ark 同款混合语义，
            # 首帧灰度差 5.7/255、尾帧≠第二图 63/255）→ image_urls 全量下发；
            # Ark 走 role="first_frame_reference"（first_frame + reference_image×n-1）；
            # H3 取首图=预览图，角色参考降级文字契约（multi_image_truncated →
            # visual_coverage sidecar 留痕）。
            #
            # provider 能力前提：T3（2026-08-18）起 supports_image_urls 声明已扩到
            # H3(qihu)/seedance-360/happyhorse（此前仅 seedance-ark）。仍不支持的
            # provider 下静默回退纯 t2v——须诚实告知，不能只记 log（同文件 R3/QP1-4
            # 已判定「仅 logger.warning」不可接受）。
            # VIDEO_GEN_PREVIEW_INHERIT=0：一键回退位（默认开；off 值口径同组锚
            # VIDEO_GEN_GROUP_ANCHOR），误拦率异常或继承质量翻车时不发版即可全量关断。
            _preview_path: str | None = None
            if frame.visual_type == "image" and frame.local_visual_path and task_dir is not None:
                _preview = task_dir / frame.local_visual_path
                _preview_ready = _preview.exists() and getattr(self._provider, "supports_image_urls", False) and os.environ.get("VIDEO_GEN_PREVIEW_INHERIT", "1").strip().lower() not in _OFF_VALUES
                if _preview_ready:
                    _preview_path = str(_preview)
                elif _preview.exists():
                    preview_i2v_skipped.append(frame.index)  # render 末尾汇总告知
            # M5：roster 模式 → 帧级子集接管（角色 ≤3×1 + 道具 ≤2×1，决策 17）；
            # 非 roster → 任务级角色三视图语义原样。
            entity_names: list[str] | None = None  # M4 带名方言：与 refs 1:1 同序时非空（不含预览图——names 对应图片 2..n）
            _prop_count = 0  # X8：names 末尾道具条目数（区分措辞；非 roster / 无道具恒 0）
            _injected: list[dict] = []
            _declared: list[str] = []
            _cap: dict = {}
            _over: list[dict] = []
            _skipped: list[dict] = []
            if _roster_mode:
                _injected, _declared, _over, _skipped, _names = _video_frame_entity_refs(frame, character_roster or {}, prop_roster or {})
                effective_refs = None
                reference_kind = None
                _paths = [e["path"] for e in _injected]
                if _preview_path:
                    # M：预览图首位进预算（截断保前缀——首帧永不因预算丢失，角色/道具
                    # 在队尾按既有优先级被截）。工作点口径 = 预览图(1)+角色(≤3)+道具(≤2)。
                    _paths = [_preview_path] + _paths
                if _paths:
                    # 预算复用（C10 双阈值：工作点 ≤6 / 上限 ≤10）——帧输入顺序已按
                    # 预览图>角色>道具排好；截断保前缀（names/injected 同步截断保 1:1）
                    keep, _cap = apply_ref_cap(_paths)
                    effective_refs = keep
                    reference_kind = "character"
                    _n_chars = len(keep) - (1 if _preview_path else 0)  # 预览图不占 names 的 1:1 名额
                    entity_names = _names[:_n_chars] if _names else None
                    # X8（2026-08-27 计划 + 用户拍板区分措辞）：names 末尾的道具条目数
                    # （角色三视图合成图 / 道具产品参考图 分措辞——注入顺序 角色→道具）。
                    # 按 prop_roster 名册成员推导：跳过/缺失的道具不在 names 里，计数
                    # 自然对齐截断后的 1:1 列表；为 0 时条件不传（窄签名兼容）。
                    _prop_count = sum(1 for nm in (entity_names or []) if nm in (prop_roster or {}))
                if task_dir is not None and (effective_refs or _over or _skipped or _preview_path):
                    # 决策 19 全量留痕（分镜级「参考资产」标签数据源，M5）：每帧一条。
                    # M：预览图条目 entity="preview"（first_frame=True），条目顺序与
                    # 实际下发数组一致（预览图首位 + 保留的角色/道具）。
                    _preview_entry = [{"entity": "preview", "path": _preview_path, "view": "preview"}] if _preview_path else []
                    record_ref_injection_sidecar(
                        task_dir,
                        frame_index=getattr(frame, "index", 0),
                        declared_entities=_declared,
                        injected_refs=_preview_entry + _injected[: len(effective_refs or []) - (1 if _preview_path else 0)],
                        truncated=[{"entity": "ref", "path": p} for p in _cap.get("truncated", [])] + _over,
                        skipped=_skipped,
                        reason=_cap.get("reason", ""),
                        first_frame=bool(_preview_path),
                    )
            else:
                effective_refs = reference_image_paths
                reference_kind = "character" if reference_image_paths else None  # R1：指代按来源区分
                if _preview_path and task_dir is not None:
                    # M：非 roster 任务的预览图注入同样留痕（此前仅 roster 记 sidecar，
                    # 预览图生效与否无从排查）。
                    record_ref_injection_sidecar(
                        task_dir,
                        frame_index=getattr(frame, "index", 0),
                        declared_entities=[],
                        injected_refs=[{"entity": "preview", "path": _preview_path, "view": "preview"}],
                        truncated=[],
                        skipped=[],
                        reason="preview_first_frame",
                        first_frame=True,
                    )
            if _preview_path and not _roster_mode:
                # M：非 roster 合并（roster 已在 _paths 前插并整组过 apply_ref_cap）
                effective_refs = [_preview_path] + (effective_refs or [])
            if _preview_path:
                # 混合标记："preview_first" = 首帧预览图 + 角色/道具参考（方言层与
                # provider role 映射各按此分派）；仅预览图时维持 "preview"（Task 5
                # 单图首帧行为不变）。
                reference_kind = "preview_first" if effective_refs and len(effective_refs) > 1 else "preview"
                logger.info("frame %d: inheriting preview image as i2v first_frame (kind=%s)", frame.index, reference_kind)
            # T-S2：组锚注入（M 起语义 = 无预览图、无角色/道具参考帧的场景兜底）
            if group_anchor_on and not effective_refs and not (frame.visual_type == "image" and frame.local_visual_path):
                _anchor = group_anchors.get(_group_of(frame.index))
                if _anchor and not _is_group_first(frame):
                    effective_refs = [_anchor]
                    reference_kind = "group_anchor"
                    logger.info("frame %d: using group anchor reference (T-S2)", frame.index)

            # M5：视频侧风格锚图兜底（优先级末位——仅角色/道具、组锚、预览继承皆空
            # 且开关 VIDEO_GEN_STYLE_ANCHOR_IMAGE 开时 refs=[锚图]）。锚图是风格参考
            # 不是首帧：role 走 "reference"（r2v），方言走 kind="style_anchor"。
            if not effective_refs and _style_anchor_fallback:
                effective_refs = [_style_anchor_fallback]
                reference_kind = "style_anchor"
                logger.info("frame %d: using style anchor fallback reference (M5)", frame.index)
                if task_dir is not None:
                    record_ref_injection_sidecar(
                        task_dir,
                        frame_index=getattr(frame, "index", 0),
                        declared_entities=[],
                        injected_refs=[{"entity": "style_anchor", "path": _style_anchor_fallback}],
                        truncated=[],
                        skipped=[],
                        reason="style_anchor_fallback",
                    )

            # Phase 1（2026-08-17 方言层）：IR -> provider 方言（env=legacy/无 IR/未知
            # provider 时为现行 compose_video_prompt 语义，P0-5 不变量保持）；
            # 参考图张数参与 Seedance 指代注入，故在 effective_refs 计算之后。
            # M4：entity_names 条件传参（帧级声明实体 1:1 时才带名；空不传保旧签名）。
            # X8：prop_count 条件传参（names 含道具时才带——角色/道具区分措辞）。
            prompt = translate_video_prompt(
                frame,
                provider_name=self._provider_name or "",
                reference_count=len(effective_refs or []),
                reference_kind=reference_kind,
                **({"entity_names": entity_names} if entity_names else {}),
                **({"prop_count": _prop_count} if entity_names and _prop_count else {}),
            ) or _topic_fallback_prompt(storyboard)

            async def _attempt(_p: str) -> None:
                """一次完整的「生成 → 写回 → 本地化 → 实测」尝试（Task 12 抽出）。

                抽函数而非复制：中性化重试要走完全相同的成功路径，复制会让
                Task 3 的实测回写 / Task 4 的 sidecar 在重试分支上悄悄缺失。
                """
                # 仅 generate_video（上游 AI API 调用）占全局信号量；本地化下载不消耗
                # 上游配额，移出临界区（修复：慢下载 60s×3 重试长期占槽，挤占其他任务的
                # AI API 额度）。
                # E6: 先扣费再调用——生视频单价 ≈¥1.5，失控成本远高于生图
                if self._budget is not None:
                    self._budget.charge()
                async with sem:
                    ref: VideoRef = await self._provider.generate_video(
                        _p,
                        duration=gen_duration,
                        # E9（2026-08-21 360 主力切换）：续写闸启用且 provider 支持时请求
                        # 官方尾帧（360 侧 extra_body.return_last_frame；Ark 侧恒开不受
                        # 影响）——第 1 段产物即携带 last_frame_url，续写免本地抽帧
                        # （衍生物可能被审核拦）。不支持/未启用的 provider 经 **kwargs
                        # 吸收该参数，无副作用。
                        return_last_frame=self._extend_enabled and bool(getattr(self._provider, "supports_tail_frame", False)),
                        # B3': 角色参考图作 image_urls；角色分配由 seedance_ark._assign_roles 决定
                        # （1→first_frame, 2→first+last, 3+→first_frame+reference_image×(n-1)）
                        # Task 5: effective_refs = 角色三视图，或（无角色图时）本帧预览图
                        image_urls=effective_refs or None,
                        # T3（2026-08-18）role 语义映射（360 端 role 是 extra_body 全局
                        # 生成类型标识，无 per-image role；官方原文「会将所有的图像、
                        # 视频和音频对象设置为参考类型」）：character→"reference"（r2v
                        # 多参考，现状正确）；M5 起 style_anchor→"reference"（风格参考
                        # 同为 r2v，不是首帧）；preview/group_anchor→None（不设 role =
                        # 首帧 i2v，官方 seedance 示例2/H3 示例9 口径——修前 preview
                        # 会被错标为 r2v 参考图）。机制 M（2026-08-31）新增
                        # preview_first→"first_frame_reference"：Ark 侧映射
                        # first_frame+reference_image×(n-1)（_content_image_entries）；
                        # 360 seedance 侧归一为不设 role（T2.0 探针结果 a：首图首帧+
                        # 其余参考）；qihu/happyhorse 经 **kwargs 吸收，无副作用。
                        role="reference" if reference_kind in ("character", "style_anchor") else ("first_frame_reference" if reference_kind == "preview_first" else None),
                        # P0-1: 目标比例真实下发（qihu **kwargs 静默吸收，无副作用）
                        ratio=_size_to_ratio(storyboard.config.width, storyboard.config.height),
                        # 2026-08-31 官方 6 类型归一：360 seedance 对 preview_first 多图
                        # 截单图时写 dropped_360_multi_ref sidecar（读改合并本帧既有
                        # ref_injection 记录）。其他 provider 经 **kwargs 静默吸收。
                        task_dir=task_dir,
                        frame_index=getattr(frame, "index", 0),
                    )
                frame.visual_url = ref.url
                frame.visual_type = "video"
                # D1: 立即本地化，避免 Worker 合成时远程 URL 过期/失效（黑屏根因）。
                # 失败则保留 remote url，由 Worker _download_asset（E1）兜底。
                if task_dir is not None:
                    _rel = await _localize_video(ref.url, frame, task_dir)
                    if _rel:
                        frame.local_visual_path = _rel
                        # Task 3：实测回写。duration（音频权威）不动——本字段只记录
                        # 模型实际产出，供缺口告知（KIND_VISUAL_SHORTFALL sidecar）。
                        # P2-6 订正（2026-08-03）：原注释写「供 worker 定格量核对」是
                        # **过度承诺**——worker 全仓不读该字段，它自己 _probe_duration。
                        # 本字段目前纯 gateway 侧使用。
                        probed = await asyncio.to_thread(_probe_video_seconds, task_dir / _rel)
                        if probed > 0:
                            frame.actual_visual_duration = probed
                            gap = duration_shortfall(frame.duration or 0.0, probed)
                            if gap > 0.5:
                                # Phase 2: Tier E 续写——大缺口优先付费延长
                                extended = False
                                if self._extend_enabled and gap >= self._extend_min_gap and getattr(self._provider, "supports_tail_frame", False):
                                    try:
                                        extended = await self._try_extend_video(frame, ref, gap, task_dir, _rel, storyboard)
                                    except Exception:
                                        logger.warning(
                                            "frame %d: extend attempt failed, falling to shortfall",
                                            frame.index,
                                            exc_info=True,
                                        )
                                        extended = False

                                if extended:
                                    # 续写成功——已更新 local_visual_path + actual_visual_duration
                                    try:
                                        record_degradation(
                                            task_dir,
                                            kind=KIND_VIDEO_EXTENDED,
                                            detail=f"第 {frame.index + 1} 镜画面 {probed:.1f}s 短于旁白 {frame.duration:.1f}s，已尾帧续写补长",
                                            frame_index=frame.index,
                                        )
                                    except Exception:
                                        logger.warning("failed to record extended sidecar", exc_info=True)
                                    # M2 修复（评审）：续写后仍有残余缺口 → 同时记 SHORTFALL（残余量）
                                    residual_probed = frame.actual_visual_duration or 0.0
                                    residual_gap = duration_shortfall(frame.duration or 0.0, residual_probed)
                                    if residual_gap > 0.5:
                                        try:
                                            record_degradation(
                                                task_dir,
                                                kind=KIND_VISUAL_SHORTFALL,
                                                detail=f"第 {frame.index + 1} 镜续写后仍短 {residual_gap:.1f}s，worker 本地链兜底",
                                                frame_index=frame.index,
                                            )
                                        except Exception:
                                            logger.warning("failed to record residual shortfall sidecar", exc_info=True)
                                else:
                                    logger.warning(
                                        "frame %d: model returned %.2fs for a %.2fs slot — worker will freeze %.2fs",
                                        frame.index,
                                        probed,
                                        frame.duration or 0.0,
                                        gap,
                                    )
                                    # Task 4：用户可见。仅 >0.5s 才记——亚秒缺口肉眼不可辨，
                                    # 记了只是噪音。
                                    try:
                                        record_degradation(
                                            task_dir,
                                            kind=KIND_VISUAL_SHORTFALL,
                                            detail=f"第 {frame.index + 1} 镜画面 {probed:.1f}s 短于旁白 {frame.duration:.1f}s，尾部定格 {gap:.1f}s",
                                            frame_index=frame.index,
                                        )
                                    except Exception:
                                        logger.warning("failed to record shortfall sidecar", exc_info=True)
                # T-Q1（2026-08-17 Phase 4，D-D 拍板）：黑帧/冻结检测--数据收集先行，
                # 只记录不自动重生（攒发生率数据后再议自动化，涉及计费须数据支撑）。
                if task_dir is not None and _rel:
                    _lowq = await asyncio.to_thread(_scan_low_quality, task_dir / _rel, float(probed or frame.duration or 0.0))
                    if _lowq:
                        try:
                            record_degradation(task_dir, kind=KIND_LOW_VISUAL_QUALITY, detail=_lowq, frame_index=frame.index)
                        except Exception:
                            logger.warning("failed to record low-quality sidecar", exc_info=True)

                if on_frame_done:
                    await on_frame_done(frame.index)

            try:
                # Task 12：内容审核拒绝 → 同 provider 换中性化说法重试一次。内层 try
                # 必须**嵌套**而非与下方 except 并列：并列会让第二个 except Exception
                # 变成死代码，逐帧占位兜底（HR-T1）随之失效——异常直接上抛，
                # 单帧失败即整任务失败。
                try:
                    await _attempt(prompt)
                except VisualBudgetExceeded:
                    raise
                except Exception as _first_err:
                    # 不换 provider（需求 #4：审核结果上浮，换网关同样过不了）；
                    # 非审核错误一律不重试（基础设施故障重试只是成本翻倍）。
                    # 2026-08-19：图片拒绝（10940）必须先于文本审核判断——报文里的
                    # "SensitiveContent" 会命中 is_content_review_rejection 的
                    # sensitive\s*content 分支，顺序反了会让中性化梯子空转
                    # （改词救不了图，白烧一次计费重试）。
                    # 本分支对所有 supports_image_urls provider 生效（谓词只看报文，
                    # provider 无关）：H3/happyhorse/seedance-ark 的 preview/group_anchor
                    # 帧被上游图拦时同样丢图重试，语义一致且省去空调用；机制 M 的
                    # preview_first（首帧+参考混合）走「仅丢首帧保参考」专属分支。
                    if is_image_content_rejection(_first_err):
                        if reference_kind == "preview_first":
                            # 机制 M（2026-08-31）混合 kind 行为：首帧（预览图）被拒 →
                            # **仅丢首帧、保留角色/道具参考**降级 r2v 重试一次——修前
                            # 整组丢图会把角色参考一起陪葬。role 随 kind="character" 归
                            # "reference"；prompt 按剩余 refs 重翻译（预览图首帧指代
                            # 不得残留）；entity_names 本就不含预览图，剩余 1:1 对齐。
                            effective_refs = (effective_refs or [])[1:] or None
                            reference_kind = "character" if effective_refs else None
                            prompt = translate_video_prompt(
                                frame,
                                provider_name=self._provider_name or "",
                                reference_count=len(effective_refs or []),
                                reference_kind=reference_kind,
                                **({"entity_names": entity_names} if entity_names else {}),
                                **({"prop_count": _prop_count} if entity_names and _prop_count else {}),
                            ) or _topic_fallback_prompt(storyboard)
                            logger.warning(
                                "frame %d: preview first-frame image rejected by content filter — retrying as r2v with character refs kept",
                                frame.index,
                            )
                            try:
                                await _attempt(prompt)
                            except Exception as _second_err:
                                # 重试仍被图片检测拒 = 保下的角色/道具参考本身被拦（判定
                                # 确定）→ 既有 character 口径：留可行动指引后上抛（HR-T1
                                # 占位兜底），不进中性化梯子（改词救不了图）。
                                if is_image_content_rejection(_second_err) and task_dir is not None:
                                    try:
                                        record_degradation(
                                            task_dir,
                                            kind=KIND_CHARACTER_IMAGE_REJECTED,
                                            detail=f"第 {frame.index + 1} 镜角色参考图未通过上游图片内容检测，已按静态占位兜底；更换角色形象后可重试本镜",
                                            frame_index=frame.index,
                                        )
                                    except Exception:
                                        logger.warning("failed to record character_image_rejected sidecar", exc_info=True)
                                raise
                            if task_dir is not None:
                                try:
                                    record_degradation(
                                        task_dir,
                                        kind=KIND_PREVIEW_IMAGE_REJECTED,
                                        detail=f"第 {frame.index + 1} 镜预览图未通过上游图片内容检测（多为误判，first_frame_dropped: content_reject），已保留角色参考图降级为参考生视频重试，成片首帧构图可能与预览图不同",
                                        frame_index=frame.index,
                                    )
                                except Exception:
                                    logger.warning("failed to record preview_image_rejected sidecar", exc_info=True)
                        elif reference_kind in ("preview", "group_anchor"):
                            # 图片被拦（多为隐私过滤器误判）：丢图降级纯 t2v 重试一次。
                            # effective_refs/reference_kind 是 fill 局部变量，_attempt
                            # 经闭包读取——重赋值即生效（role 随 reference_kind=None
                            # 归 None，即纯 t2v）。prompt 必须按 reference_count=0
                            # 重翻译——v2 方言的「图片1为…」指代不能残留。
                            _rejected_kind = reference_kind
                            effective_refs = None
                            reference_kind = None
                            prompt = translate_video_prompt(frame, provider_name=self._provider_name or "", reference_count=0, reference_kind=None) or _topic_fallback_prompt(storyboard)
                            logger.warning(
                                "frame %d: %s image rejected by content filter — retrying as pure t2v",
                                frame.index,
                                _rejected_kind,
                            )
                            await _attempt(prompt)
                            # task_dir 此处恒非 None（preview 闸与 group_anchor_on 都以
                            # 之为前提），守卫为防御性保留，防未来新增 reference_kind
                            # 来源打破前提。
                            if task_dir is not None:
                                try:
                                    record_degradation(
                                        task_dir,
                                        kind=KIND_PREVIEW_IMAGE_REJECTED,
                                        detail=(
                                            f"第 {frame.index + 1} 镜预览图未通过上游图片内容检测（多为误判），该帧已降级为纯文生视频，成片构图可能与预览图不同"
                                            if _rejected_kind == "preview"
                                            else f"第 {frame.index + 1} 镜组锚参考图未通过上游图片内容检测（多为误判），该帧已降级为纯文生视频，与同组场景的一致性可能下降"
                                        ),
                                        frame_index=frame.index,
                                    )
                                except Exception:
                                    logger.warning("failed to record preview_image_rejected sidecar", exc_info=True)
                        else:
                            # character（角色三视图）：丢图 = 静默放弃角色一致性承诺，
                            # 不允许；同图重试必再败（图片检测对同图判定确定），
                            # 直接上抛由外层走 HR-T1 占位兜底，不进中性化梯子。
                            # 08-20 修订③：上抛前留痕可行动指引——用户看到占位帧时
                            # 知道原因与出路（更换角色形象后可重试本镜）。
                            if task_dir is not None:
                                try:
                                    record_degradation(
                                        task_dir,
                                        kind=KIND_CHARACTER_IMAGE_REJECTED,
                                        detail=f"第 {frame.index + 1} 镜角色参考图未通过上游图片内容检测，已按静态占位兜底；更换角色形象后可重试本镜",
                                        frame_index=frame.index,
                                    )
                                except Exception:
                                    logger.warning("failed to record character_image_rejected sidecar", exc_info=True)
                            raise
                    elif not is_content_review_rejection(_first_err):
                        raise
                    else:
                        _neutral = neutralize_prompt(prompt)
                        if _neutral != prompt:
                            logger.warning("frame %d: content review rejected — retrying with neutralized prompt", frame.index)
                            await _attempt(_neutral)
                            # P0-2（2026-08-03）：改词成功后必须告知用户——成片画面与用户所写
                            # 提示词不一致，只记 logger 等于又一次「默默降级不告知」（Task 4/5
                            # 反复在修的正是这类缺陷，Task 12 首版自己又犯了一次）。
                            if task_dir is not None:
                                try:
                                    record_degradation(
                                        task_dir,
                                        kind=KIND_PROMPT_NEUTRALIZED,
                                        detail=f"第 {frame.index + 1} 镜画面提示词因内容审核被调整为更中性的表述后重新生成",
                                        frame_index=frame.index,
                                    )
                                except Exception:
                                    logger.warning("failed to record prompt_neutralized sidecar", exc_info=True)
                        else:
                            # P1（2026-08-19）第二层：审核**误判**型拒绝（词表无可中性化，
                            # 如「人形光影」被拦）——此前直接 raise，本机制对误判从来空转。
                            # 改用旁白直述的朴素 prompt 重试一次；旁白为空才维持上抛。
                            narration = (frame.narration or "").strip()
                            if not narration:
                                raise
                            fallback_prompt = narration[:120]
                            logger.warning(
                                "frame %d: content review false-positive — retrying once with narration-based plain prompt",
                                frame.index,
                            )
                            await _attempt(fallback_prompt)
                            if task_dir is not None:
                                try:
                                    record_degradation(
                                        task_dir,
                                        kind=KIND_PROMPT_FALLBACK,
                                        detail=f"第 {frame.index + 1} 镜画面提示词疑似被内容审核误判，已改用旁白原文重新生成",
                                        frame_index=frame.index,
                                    )
                                except Exception:
                                    logger.warning("failed to record prompt_fallback_narration sidecar", exc_info=True)
            except VisualBudgetExceeded:
                # E6: 预算闸必须穿透逐帧占位兜底——否则会变成「静默全帧占位」
                raise
            except Exception as e:
                errors.append(f"Frame {frame.index}: {e}")
                logger.warning(
                    "AI video generation failed for frame %d: %s — 用静态占位",
                    frame.index,
                    prompt[:60],
                    exc_info=True,
                )
                # HR-T1: 占位而非 None，避免 Worker 丢弃该帧致失同步
                if task_dir is not None:
                    try:
                        # B2: 传目标比例，避免黑帧边界跳变（修前硬编码 1920x1080）
                        rel = await _make_placeholder_clip(
                            frame,
                            task_dir,
                            # P2-4：0/负值兜底——ffmpeg 收到 0 会失败并静默产黑场。
                            # 与同文件 _size_to_ratio 的 <=0 防御对齐（生产配置最小 1152，
                            # 但损坏的 storyboard.json 可注入 0）。
                            width=storyboard.config.width if storyboard.config.width > 0 else 1920,
                            height=storyboard.config.height if storyboard.config.height > 0 else 1080,
                        )
                        frame.local_visual_path = rel
                        frame.visual_type = "video"
                        frame.visual_url = None  # 占位用本地文件
                    except Exception:
                        logger.exception("placeholder also failed for frame %d", frame.index)

        # T2: 无进展看门狗（活性判据）--20min 内没有任何一帧结束 -> 上游 hang/限流。
        # 不用固定时长预算：>50 帧串行 + Tier E 续写（单帧合法最坏 ≈13.5min）会被误杀。
        #
        # T-S2（2026-08-17 Phase 3 组首帧参考锚定）：有 scene_groups 时组首帧先行
        # 渲染 -> 抽首帧作该组锚图 -> 组内其余帧带锚参考（星型--每镜参考组首，不链式
        # 防画质劣化；E3 实证 c<b<a -56%）。与角色三视图/预览图继承互斥（冲突保角色
        # 一致性，同 Task 5 先例）。
        # T6（2026-08-18 拍板③）默认开（与 T3 H3 修复一并交付；组锚零增量付费调用
        # ——组首本就要渲染，锚图抽取是本地 ffmpeg）；VIDEO_GEN_GROUP_ANCHOR=0 一键
        # 回退（off 值口径同 visual_routing._OFF_VALUES）。
        # 2026-08-19：seedance-360 排除移除（对齐改动 1a 探针结论：role=None 首帧
        # i2v 已实网验证）。组锚图来自已成功渲染的组首视频本地 ffmpeg 抽帧
        # （anchor_gN.jpg，无大量文字），被图片检测误拦概率低于信息图预览，
        # 且同受「图片拒绝 → 丢图纯 t2v 重试」梯子保护。
        # 部署闸（登记，人工事项）：H3 i2v 实网探针先行（计划 §2 P2），探针不过则
        # 部署 env 显式置 0 仅对 H3 回退；上线后跑 A/B 对照再定默认去留（拍板③折中）。
        group_anchor_on = os.environ.get("VIDEO_GEN_GROUP_ANCHOR", "1").strip().lower() not in _OFF_VALUES and bool(storyboard.scene_groups) and task_dir is not None and getattr(self._provider, "supports_image_urls", False)
        group_anchors: dict[int, str] = {}  # group.index -> 锚图绝对路径

        def _group_of(idx: int) -> int | None:
            for g in storyboard.scene_groups:
                if idx in g.frame_indices:
                    return g.index
            return None

        def _is_group_first(f) -> bool:  # noqa: ANN001
            gi = _group_of(f.index)
            if gi is None:
                return False
            member_ids = next(g.frame_indices for g in storyboard.scene_groups if g.index == gi)
            in_target = [i for i in member_ids if any(t.index == i for t in target)]
            return bool(in_target) and f.index == min(in_target)

        _watchdog_msg = "生视频阶段 20 分钟无帧完成，可能上游 hang 或限流"
        if group_anchor_on:
            firsts = [f for f in target if _is_group_first(f)]
            rest = [f for f in target if not _is_group_first(f)]
            await gather_with_no_progress_watchdog(fill, firsts, timeout=_NO_PROGRESS_TIMEOUT, message=_watchdog_msg)
            for f in firsts:
                gi = _group_of(f.index)
                if gi is None or not f.local_visual_path:
                    continue
                video_abs = task_dir / f.local_visual_path
                jpg = task_dir / f"anchor_g{gi}.jpg"
                if await asyncio.to_thread(_extract_anchor_image, video_abs, jpg):
                    group_anchors[gi] = str(jpg)
                else:
                    logger.warning("group %d anchor extraction failed - members render without anchor", gi)
            await gather_with_no_progress_watchdog(fill, rest, timeout=_NO_PROGRESS_TIMEOUT, message=_watchdog_msg)
        else:
            await gather_with_no_progress_watchdog(fill, target, timeout=_NO_PROGRESS_TIMEOUT, message=_watchdog_msg)

        # Task 5：仅当真有帧受影响时告知——默认 provider（seedance-ark）支持 i2v，
        # 不该给多数用户制造无意义提示。
        if preview_i2v_skipped and task_dir is not None:
            try:
                record_degradation(
                    task_dir,
                    kind=KIND_PREVIEW_I2V_UNSUPPORTED,
                    detail=(f"当前视频模型不支持图生视频，{len(preview_i2v_skipped)} 个分镜的预览图未能作为首帧继承，成片构图可能与预览图不同。改用 seedance-ark 可获得「预览即所得」。"),
                )
            except Exception:
                logger.warning("failed to record preview-i2v sidecar", exc_info=True)

        # P2-11 + QP1-3/4: sidecar 全帧口径 + refs 标记 + 全成功清理。
        # 口径 = 全 storyboard 的 ai_video 帧（修前按当批 only_frames 计 → 单帧重生
        # 失败把整任务警告刷成 0/1）；全成功且无 refs 标记时删除文件（修前永不清理
        # → 旧警告残留）。
        if task_dir is not None:
            self._update_coverage_sidecar(
                storyboard,
                Path(task_dir),
                character_ref_ignored=refs_ignored,
            )

        # B2 (Task 9): 按**覆盖率**判定，不再按「全部失败」。
        #
        # 旧判据 `not visual_url and not local_visual_path` 在生产路径上几乎永不
        # 成立：占位分支会写 `frame.local_visual_path = rel`，于是每个失败帧都被
        # 排除在 failed 之外 → 10 帧全占位时 failed 为空 → 不抛 → 静默交付一整片
        # 灰色占位并标「已完成」。照审计去调阈值无效，必须换判据。
        #
        # 新判据：真实成功帧（有 visual_url）/ 总帧数 < MIN_COVERAGE 即响亮失败，
        # 让任务框架重试或把明确错误交给用户。占位帧**不算**成功。
        #
        # 口径必须与 _update_coverage_sidecar 一致：**全 storyboard 的 ai_video 帧**，
        # 不是当批 target。按当批算会让单帧重生（only_frames=[1]）变成 0/1 = 0% →
        # 恒抛（test_video_provider_fallback_refs 实测抓到）；mixed 任务里按全帧不
        # 过滤又会把并行渲染中的 ai_image 帧误算成失败。
        scope = self._coverage_scope(storyboard)
        succeeded = sum(1 for f in scope if f.visual_url)
        total = len(scope)
        coverage = succeeded / total if total else 0.0
        if total and coverage < MIN_COVERAGE:
            placeholders = sum(1 for f in scope if not f.visual_url and getattr(f, "local_visual_path", None))
            first_err = errors[0] if errors else "unknown"
            raise RuntimeError(
                f"AI video generation coverage too low: {succeeded}/{total} frames ({coverage:.0%} < {MIN_COVERAGE:.0%}), {placeholders} placeholder(s). First error: {first_err}. Provider may be slow, rate-limited, or down — please retry."
            )

    async def _try_extend_video(
        self,
        frame: StoryboardFrame,
        original_ref: VideoRef,
        gap: float,
        task_dir: Path,
        original_rel: str,
        storyboard: Storyboard,
    ) -> bool:
        """Phase 2 Tier E: 续写 i2v 延长画面，消除大缺口定格。

        流程：尾帧（官方 last_frame_url 优先，fallback 本地抽帧）→
        i2v 生成续写片 → concat 原片+续写（ffprobe 校验）→ 更新 frame。

        任一步失败返 False（落回 SHORTFALL + worker 本地链），主流程不中断。
        """
        # 0) gate 下沉（2026-08-14 C3）：与调用方 _attempt 的 gate 同条件，单点真源、
        # 可直接测试。调用方 gate 保留作短路（省一次调用 + try/except 包裹）。
        if not self._extend_enabled:
            return False
        if gap < self._extend_min_gap:
            return False
        if not getattr(self._provider, "supports_tail_frame", False):
            return False

        # 1) 获取尾帧
        tail_frame_url = original_ref.last_frame_url
        tail_local: Path | None = None

        if tail_frame_url:
            # 官方尾帧：下载到本地临时文件
            tail_local = task_dir / f"visuals/_tail_{frame.index:04d}.png"
            try:
                async with httpx.AsyncClient(timeout=60) as client:
                    resp = await client.get(tail_frame_url, headers={"User-Agent": "TrainerAI-VideoGen/1.0"})
                    resp.raise_for_status()
                    if len(resp.content) > 512:
                        tail_local.write_bytes(resp.content)
                    else:
                        tail_local = None
            except Exception:
                logger.warning("frame %d: failed to download official tail frame", frame.index, exc_info=True)
                tail_local = None

        if tail_local is None:
            # Fallback: 本地抽帧（衍生物，人脸镜头可能被审核拦——见 Task 5 探针限制）
            tail_local = task_dir / f"visuals/_tail_{frame.index:04d}.png"
            original_path = task_dir / original_rel
            ok = await asyncio.to_thread(_extract_tail_frame, original_path, tail_local)
            if not ok:
                logger.warning("frame %d: local tail frame extraction failed — cannot extend", frame.index)
                return False

        # 2) 续写时长（对齐到 provider 栅格）
        cap = getattr(self._provider, "max_video_duration", 15.0)
        floor = float(getattr(self._provider, "min_video_duration", 4.0) or 4.0)
        integer_only = bool(getattr(self._provider, "integer_seconds_only", False))
        ext_dur = align_to_provider_grid(gap, cap=cap, floor=floor, integer_seconds=integer_only)
        ext_path: Path | None = None
        try:
            # 3) i2v 续写
            if self._budget is not None:
                try:
                    self._budget.charge()
                except VisualBudgetExceeded:
                    # C3：续写是付费可选步骤——预算耗尽跳过续写（worker 本地链兜底），
                    # 不向上冒泡（主生成路径的预算闸 E6 仍照常 raise 停止整批）。
                    logger.warning("frame %d: visual budget exhausted — skipping extend", frame.index)
                    return False
            extend_prompt = (frame.image_prompt or "") + "（画面延续，保持动作连续性）"
            sem = get_ai_api_semaphore(self._global_limit)
            async with sem:
                ext_ref: VideoRef = await self._provider.generate_video(
                    extend_prompt,
                    duration=ext_dur,
                    image_url=str(tail_local),
                    role="first_frame",
                    ratio=_size_to_ratio(storyboard.config.width, storyboard.config.height),
                )

            # 4) 本地化续写片
            # C2 修复：续写片必须落不同文件名，否则覆盖原片 shot_NNNN.mp4 → concat 撞名丢原片
            ext_rel = await _localize_video(ext_ref.url, frame, task_dir, dest_name=f"visuals/ext_{frame.index:04d}.mp4")
            if not ext_rel:
                logger.warning("frame %d: extend clip localization failed", frame.index)
                return False
            ext_path = task_dir / ext_rel

            # 5) concat 原片 + 续写
            original_path = task_dir / original_rel
            concat_rel = f"visuals/extended_{frame.index:04d}.mp4"
            concat_path = task_dir / concat_rel
            ok = await asyncio.to_thread(_concat_videos_with_check, original_path, ext_path, concat_path)
            if not ok:
                logger.warning("frame %d: extend concat failed — keeping original", frame.index)
                concat_path.unlink(missing_ok=True)  # A-2: 失败半成品一并清理
                return False

            # 6) 更新 frame
            frame.local_visual_path = concat_rel
            probed = await asyncio.to_thread(_probe_video_seconds, concat_path)
            if probed > 0:
                frame.actual_visual_duration = probed
            else:
                # A-3: probe 失败不得保留续写前旧值——旧值会让调用方按过期时长算出
                # 假残余缺口，记一条「第N镜续写后仍短 X s」的误报 sidecar（实际已补长）。
                # 置 None 后 duration_shortfall 返回 0.0（duration_policy.py:55-56），
                # worker 侧对未知时长自有保守定格（N9），不再产生用户不可行动的噪音。
                frame.actual_visual_duration = None
                logger.warning("frame %d: extended clip probe failed — actual_visual_duration reset to None", frame.index)
            logger.info(
                "frame %d: extended %.1fs → %.1fs (gap=%.1fs, ext_dur=%.1fs)",
                frame.index,
                original_ref.duration,
                probed,
                gap,
                ext_dur,
            )
            return True
        finally:
            # A-2: 成败一律清理临时文件——修前只在成功分支 unlink，concat 失败/
            # 本地化失败/生成异常逐帧累积占盘（ext_NNNN.mp4 + _tail_NNNN.png）。
            # 原片 shot_NNNN.mp4 与 concat 产物 extended_NNNN.mp4 是不同文件（C2），
            # 清理不波及原片；成功时续写内容已并入 concat 产物，ext 片同样可删。
            if tail_local is not None:
                tail_local.unlink(missing_ok=True)
            if ext_path is not None:
                ext_path.unlink(missing_ok=True)

    @staticmethod
    def _coverage_scope(storyboard: Storyboard) -> list:
        """覆盖率口径：全 storyboard 中路由给 ai_video 的帧（守卫与 sidecar 共用）。

        纯 ai_video 任务 = 全帧；mixed 任务只计本源帧——mixed 里 ai_image/ai_video
        并行渲染，计全帧会把尚未完成的 ai_image 帧误算成失败。
        """
        default_type = storyboard.config.video_type
        return [f for f in storyboard.frames if (getattr(f, "visual_source", None) or default_type) == "ai_video"]

    def _update_coverage_sidecar(self, storyboard: Storyboard, task_dir: Path, *, character_ref_ignored: bool = False) -> None:
        """写/清 visual_coverage.json（全帧口径 + refs 降级标记）。

        - coverage < 1.0 或有 refs 标记 → 写文件（任务详情 ai_video_coverage_warning 消费）
        - coverage == 1.0 且无标记 → 删除旧文件（修复后警告不再残留）

        字段语义与旧版一致：succeeded 仅计真实 visual_url，placeholder 计本地占位
        （占位帧不算 succeeded，否则占位补齐的任务 coverage=1.0 → 告警永不触发）。

        口径 = 全 storyboard 中路由给 ai_video 的帧（f.visual_source 缺省回退
        config.video_type）。纯 ai_video 任务 = 全帧；mixed 任务只计本源帧——
        mixed 里 ai_image/ai_video 并行渲染，若计全帧会把尚未完成的 ai_image 帧
        误算成失败，写下虚假的部分覆盖告警（且无人改写 → 残留）。
        """
        import json

        frames = self._coverage_scope(storyboard)
        total = len(frames)
        succeeded = sum(1 for f in frames if f.visual_url)
        placeholder = sum(1 for f in frames if not f.visual_url and getattr(f, "local_visual_path", None))
        coverage = succeeded / total if total else 0.0
        degraded = bool(getattr(self._provider, "degraded_without_refs", False))
        # 偏离项 3：截断降级（H3/happyhorse 只取首图）同样保留 sidecar——
        # coverage=1.0 但一致性受损，用户须可见（对照 degraded 同标准）。
        truncated = bool(getattr(self._provider, "multi_image_truncated", False))
        sidecar = task_dir / "visual_coverage.json"
        if coverage >= 1.0 and not character_ref_ignored and not degraded and not truncated:
            try:
                sidecar.unlink(missing_ok=True)
            except OSError:
                # 清理失败不拖垮已成功渲染（与下方 write guard 同 idiom）
                logger.warning("failed to remove visual_coverage.json", exc_info=True)
            return
        try:
            sidecar.write_text(
                json.dumps(
                    {
                        "coverage": round(coverage, 3),
                        "succeeded": succeeded,
                        "placeholder": placeholder,
                        "total": total,
                        "character_ref_ignored": character_ref_ignored,
                        "character_ref_degraded": degraded,
                        "character_ref_truncated": truncated,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        except OSError:
            logger.warning("failed to write visual_coverage.json", exc_info=True)


def _extract_anchor_image(video_path: Path, jpg_path: Path) -> bool:
    """T-S2：从组首帧视频抽首帧作锚图（失败返回 False，组员无锚降级渲染）。"""
    try:
        if not video_path.exists():
            return False
        r = subprocess.run(
            ["ffmpeg", "-y", "-i", str(video_path), "-frames:v", "1", "-q:v", "3", str(jpg_path)],
            capture_output=True,
            timeout=30,
        )
        return r.returncode == 0 and jpg_path.exists()
    except Exception:
        return False


def _scan_low_quality(video_path: Path, duration: float) -> str | None:
    """T-Q1（Phase 4）：blackdetect/freezedetect 逐镜扫描（gateway 侧，纯本地零成本）。

    命中判据：黑帧或冻结时长占比 >50%（半程黑/冻即「低质量风险」）。返回降级描述
    或 None；扫描自身失败静默 None（质检不阻塞主链）。D-D 拍板：只记录不重生。
    """
    try:
        if not video_path.exists() or duration <= 0:
            return None
        r = subprocess.run(
            ["ffmpeg", "-i", str(video_path), "-vf", "blackdetect=d=0.5:pic_th=0.98,freezedetect=n=-60dB:d=0.5", "-an", "-f", "null", "-"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        err = r.stderr or ""
        black = sum(float(x) for x in re.findall(r"black_duration:([0-9.]+)", err))
        # freezedetect 走 lavfi 元数据逐行输出（冒号后有空格，须 \s*）；冻结持续到
        # EOF 时只有 freeze_start、无 freeze_duration/freeze_end（tpad 定格的典型
        # 形态）——未闭合段按视频总长补齐，否则尾段定格恒漏检。
        f_starts = [float(x) for x in re.findall(r"freeze_start:\s*([0-9.]+)", err)]
        f_durs = [float(x) for x in re.findall(r"freeze_duration:\s*([0-9.]+)", err)]
        freeze = sum(f_durs)
        if len(f_starts) > len(f_durs):
            freeze += duration - f_starts[-1]
        rb, rf = black / duration, freeze / duration
        if rb > 0.5 or rf > 0.5:
            return f"画面低质量风险（黑帧 {black:.1f}s 占 {rb:.0%}，冻结 {freeze:.1f}s 占 {rf:.0%}）--数据收集中，不自动重生"
        return None
    except Exception:
        return None
