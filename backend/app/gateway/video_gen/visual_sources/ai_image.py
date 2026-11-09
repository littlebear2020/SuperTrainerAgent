"""AI image visual source: generate images per frame via provider, fill visual_url.

P1-2: 生成成功后立即本地化到 task_dir/visuals/img_NNNN.png（对齐 ai_video D1），
消除 recompose/缩略图对远程临时 URL 的依赖（URL 过期 → 全片黑场 + 缩略图裂）。
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
from collections.abc import Awaitable, Callable
from pathlib import Path

from app.gateway._shared.safe_download import safe_download

from ..degradations import KIND_IMAGE_SIZE_MAPPED, KIND_PROMPT_FALLBACK, KIND_PROMPT_NEUTRALIZED, record_degradation
from ..orchestration.storyboard_planner import _topic_fallback_prompt
from ..providers.base import ImageRef
from ..providers.content_review import is_content_review_rejection, neutralize_prompt
from ..storyboard import Storyboard
from ..style_presets import style_prefix_for
from ._global_concurrency import get_ai_api_semaphore
from ._progress_watchdog import DEFAULT_NO_PROGRESS_TIMEOUT as _NO_PROGRESS_TIMEOUT
from ._progress_watchdog import gather_with_no_progress_watchdog
from .budget import VisualBudgetExceeded
from .graphic_text import _prompt_units, _trim_to_units

logger = logging.getLogger(__name__)

_LOCALIZE_RETRY_DELAYS = [0, 1, 2]  # 秒；测试可 monkeypatch 加速
_MIME_EXT = {"image/png": ".png", "image/jpeg": ".jpg", "image/jpg": ".jpg", "image/webp": ".webp"}


def _sniff_image_ext(data: bytes) -> str:
    """按魔数识别图片字节真实格式（B3 补全 2026-08-31：URL/上传路径的后缀不可信）。

    CDN 返回的 bytes 不一定是 PNG、data URI 的 mime 标签也可能与真实字节不符——
    按魔数校正落盘后缀，否则 JPEG 字节躺成 .png，serve Content-Type / 下游按
    扩展名识别全错。识别不了返回 ""（调用方保留原后缀）。路由风格锚图与角色图
    落盘共用本函数（勿再各抄一份）。
    """
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    return ""


# 视觉覆盖率下限：低于此值判定「部分成功其实是失败」，响亮报错让任务框架重试，
# 而不是静默交付一片占位/复用帧。ai_video 复用同一常量（Task 9/B2）。
MIN_COVERAGE = 0.6

# P1（2026-08-19）下发层风格尾：Ark seedream 混合单位预算，与
# graphic_text._ARK_PROMPT_BUDGET_UNITS 同值（图片下发统一预算）。
_ARK_IMAGE_BUDGET_UNITS = 300


def compose_dispatch_prompt(base: str, style_key: str | None, *, ark_budget: bool) -> str:
    """下发层风格尾：存库 image_prompt 不动（T-I0-② 决策），仅在发送给生图模型时拼风格；
    Ark seedream 有 ≤300 混合单位预算，超长裁头部保风格尾完整。"""
    prefix = (style_prefix_for(style_key or "") or "").strip()
    if not prefix:
        return base
    tail = f"，{prefix}"
    if ark_budget:
        base = _trim_to_units(base, _ARK_IMAGE_BUDGET_UNITS - _prompt_units(tail))
    return f"{base}{tail}"


# M3（2026-08-24 计划二期）：按帧出场声明选择参考图（确定性规则，禁止自由发挥）。
# 开关 VIDEO_GEN_SINGLE_VIEW_PER_ENTITY——R6 验收判定（2026-08-26）：单视图模式角色
# 保真度不足（性别/年龄漂移），**默认关=三视图全传**；置 1 仅为实验位，灰度后移除。
# 合成图方案（2026-08-27 计划 X9）：本开关与 VIDEO_GEN_MULTI_CHAR_VIEWS 在角色有
# 三视图合成图后仅旧路径兜底使用（无 composite 的存量 sheet），默认值不变。
_SINGLE_VIEW_ENV = "VIDEO_GEN_SINGLE_VIEW_PER_ENTITY"


def _single_view_per_entity_enabled() -> bool:
    from ..orchestration.visual_routing import _OFF_VALUES

    # R6 翻转：缺省 "0"（off）→ 默认三视图全传；显式置 1 才开单视图实验。
    return os.environ.get(_SINGLE_VIEW_ENV, "0").strip().lower() not in _OFF_VALUES


def _entity_views(entry: dict, roster_entry: dict) -> list[str]:
    """单实体视图选择：view 缺省/非法 → front_url（过渡开关开 = 每实体 1 张）。"""
    view = entry.get("view")
    if view in ("front", "side", "back"):
        u = roster_entry.get(f"{view}_url")
        if u:
            return [u]
    u = roster_entry.get("front_url")
    return [u] if u else []


def _entity_views_all(roster_entry: dict) -> list[str]:
    """过渡开关关断：声明实体回退三视图全传。"""
    return [u for u in (roster_entry.get("front_url"), roster_entry.get("side_url"), roster_entry.get("back_url")) if u]


def _frame_character_ref_paths(frame, character_roster: dict) -> list[str] | None:
    """M3 帧级角色参考选择 + 合成图方案（2026-08-27 计划 X7）。

    - 声明帧（非空 visible_characters）：按声明顺序 ≤3 实体，每实体恒 1 张
      ``entry["composite"]``（三视图合成图——三人同框 3×1 卡工作点 6，根治 T2⑤
      第 3 人被预算截断）；无 composite → 回退现视图逻辑（实验开关仅兜底路径生效）；
    - visible_characters is None（存量帧）→ 首主角 1 张 composite（与旧「三视图
      全传」信息等价）；无 composite → 三视图全传（旧行为）；
    - [] → 无角色参考（明确空镜）；
    - >3 截断（截断留痕由 fill 层记 sidecar）；名册外名字跳过（M2 归一化后罕见，防御）。
    """
    vc = getattr(frame, "visible_characters", None)
    if vc is None:
        for name, entry in character_roster.items():
            composite = entry.get("composite") or ""
            if composite:
                return [composite]  # 首主角（dict 首键 = sheets[0]）1 张合成图
            return _entity_views_all(entry)  # 兜底：三视图全传（旧行为）
        return None
    if not vc:
        return []
    single = _single_view_per_entity_enabled()
    # 实验位（T2⑤ 方案 3）：>2 角色同框时每实体视图数降为 VIDEO_GEN_MULTI_CHAR_VIEWS
    # （默认 0=不降，三视图全传；置 2 → 3 人同框 3×2=6 卡工作点）。
    # 合成图方案（2026-08-27）后仅旧路径兜底使用，默认值不变（X9）。
    try:
        _mv = int(os.environ.get("VIDEO_GEN_MULTI_CHAR_VIEWS", "0") or "0")
    except ValueError:
        _mv = 0
    refs: list[str] = []
    for item in vc[:3]:  # 决策 17：每镜角色 ≤3
        roster_entry = character_roster.get(item.get("name"))
        if not roster_entry:
            continue
        composite = roster_entry.get("composite") or ""
        if composite:
            refs.append(composite)  # 每实体恒 1 张合成图（角色 view 提示退役，决策 3）
            continue
        # 兜底：无合成图 → 现视图逻辑（X5 返回 None 的存量 sheet）
        if single:
            refs.extend(_entity_views(item, roster_entry))
        else:
            views = _entity_views_all(roster_entry)
            if _mv >= 1 and len(vc) >= 3:
                views = views[:_mv]
            refs.extend(views)
    return refs


def _frame_prop_ref_paths(frame, prop_roster: dict) -> list[str]:
    """M3 帧级道具参考选择：view 缺省 → main_image；view=i≥1 → extra_images[i-1]。

    visible_props None（存量/无声明）→ 无道具参考（旧行为）；[] → 无道具；
    >2 截断（决策 17）；名册外名字跳过。文件存在性由 fill 层校验（缺失跳过 + sidecar）。
    """
    vp = getattr(frame, "visible_props", None)
    if not vp:
        return []
    refs: list[str] = []
    for item in vp[:2]:  # 决策 17：每镜道具 ≤2
        roster_entry = prop_roster.get(item.get("name"))
        if not roster_entry:
            continue
        view = item.get("view")
        if isinstance(view, int) and not isinstance(view, bool) and view >= 1:
            extras = roster_entry.get("extra_images") or []
            if view - 1 < len(extras):
                refs.append(extras[view - 1])
                continue
            continue  # 越界补角度 → 跳过该条（按无声明处理）
        main = roster_entry.get("main_image")
        if main:
            refs.append(main)
    return refs


def build_image_ref_declaration(
    frame,
    character_roster: dict,
    prop_roster: dict,
    *,
    char_refs: list[str],
    prop_kept: list[str],
    has_anchor: bool,
    final_refs: list[str],
) -> str:
    """X12（2026-08-28 用户拍板）：图片侧（seedream 预览图）参考图带名指代声明。

    实证缺口（T2 复测 2026-08-28）：图片侧只有 refs + 场景描述，模型靠猜「图↔名」
    对应——三人同框出现性别翻转（小林→男，同帧老王/阿珍正确）；带显式指代的三人
    探针 3/3 全对。声明与 X8 视频侧方言**同模板**（vocab 唯一模板，不另起并行）。

    仅 refs 与实体名 **1:1**（全部角色走合成图）时返回声明文本（调用方前置到
    prompt），否则 ``""``——回退三视图/None 帧/cap 截断等非 1:1 形态不加
    （同 M4 不带名纪律）。锚图在位时追加「图片N为本系列风格参考」（vocab 既有文案）。
    """
    from ..prompt_translators.vocab import seedance_reference_injection

    vc = getattr(frame, "visible_characters", None)
    vp = getattr(frame, "visible_props", None)
    if not vc and not vp:
        return ""  # None（存量首主角）/ [] 空镜 → 不带名
    names: list[str] = []
    prop_names: list[str] = []
    if vc:
        char_names: list[str] = []
        for item in vc[:3]:
            if not isinstance(item, dict) or not item.get("name"):
                continue
            entry = character_roster.get(item["name"])
            if not entry:
                continue
            if not (entry.get("composite") or ""):
                return ""  # 回退三视图（1 人多张）→ 非 1:1
            char_names.append(item["name"])
        if not char_names or len(char_refs) != len(char_names):
            return ""
        names += char_names
    if vp:
        for item in vp[:2]:
            if not isinstance(item, dict) or not item.get("name"):
                continue
            if item["name"] in prop_roster:
                prop_names.append(item["name"])
        if len(prop_kept) != len(prop_names):
            return ""  # 道具文件缺失跳过后名图不齐 → 不虚报
        names += prop_names
    if not names:
        return ""
    if len(final_refs) != len(names) + (1 if has_anchor else 0):
        return ""  # cap 截断或其他错位 → 不虚报（防御 VIDEO_GEN_REF_CAP_WORKING 调低）
    decl = seedance_reference_injection(len(final_refs), "character", names, len(prop_names))
    if has_anchor:
        decl += seedance_reference_injection(len(final_refs), "style_anchor")
    return decl


def _resolve_provider_kind(provider) -> str:
    """穿透 Throttle/Fallback 包裹链取具体 provider 类名小写。

    BUG-B 教训：生产 provider 一律被包裹，``type(provider).__name__`` 拿到的
    是包裹类名（ThrottledAIImageProvider/FallbackAIImageProvider），直接判
    "ark"/"seedream" 恒 False——Ark 预算在真实链上从未生效过。
    """
    name = ""
    p = provider
    for _ in range(8):  # 链深护栏
        name = type(p).__name__.lower()
        if "fallback" not in name and "throttled" not in name:
            return name
        p = getattr(p, "_inner", None) or getattr(p, "_primary", None)
        if p is None:
            break
    return name


async def _localize_image(url: str, frame, task_dir: Path) -> str | None:
    """下载/解码远程图到 task_dir/visuals/img_NNNN.png，返回相对路径；失败返 None。

    - data:image/*;base64, URI（seedream b64_json 形态）：直接解码落盘
    - http(s)：UA + 重试 + ≥512B 校验（对齐 ai_video D1 的 _localize_video）
    失败时调用方保留远程 url，由 Worker _download_asset 兜底（现状行为不变）。
    """
    visuals_dir = task_dir / "visuals"
    visuals_dir.mkdir(parents=True, exist_ok=True)

    if url.startswith("data:"):
        try:
            header, b64 = url.split(",", 1)
            mime = header[5:].split(";")[0]
            ext = _MIME_EXT.get(mime, ".png")
            rel = f"visuals/img_{frame.index:04d}{ext}"
            (task_dir / rel).write_bytes(base64.b64decode(b64))
            return rel
        except Exception:
            logger.warning("localize data-uri image failed for frame %d", frame.index, exc_info=True)
            return None

    rel = f"visuals/img_{frame.index:04d}.png"
    out = task_dir / rel
    last_err = None
    for delay in _LOCALIZE_RETRY_DELAYS:
        try:
            # C2（2026-10-05 审计 §2-1）：出站下载统一走 SSRF 守卫（含逐跳复核）。
            content = await safe_download(url, timeout=60, headers={"User-Agent": "TrainerAI-VideoGen/1.0 (image localize)"})
            if len(content) < 512:
                last_err = f"content too small ({len(content)} bytes, likely error page)"
            else:
                out.write_bytes(content)
                return rel
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
        if delay:
            await asyncio.sleep(delay)
    logger.warning("localize image failed for frame %d: %s (keep remote url)", frame.index, last_err)
    return None


class AIImageVisualSource:
    def __init__(self, provider, *, concurrency: int = 3, global_limit: int | None = None, budget=None) -> None:
        self._provider = provider
        self._concurrency = concurrency
        # E1 (Task 13): 单任务成本闸（调用数 + 金额）。None = 不限（向后兼容）。
        self._budget = budget
        # 闸1: process-wide upstream API cap. The per-instance ``concurrency`` is
        # retained for API compatibility but no longer gates API calls — the global
        # singleton does, so 20-30 tasks can't each open 3 calls (60-90 total) and
        # hammer 字节 Ark / 360.
        self._global_limit = global_limit

    @staticmethod
    def _coverage_scope(storyboard: Storyboard) -> list:
        """覆盖率/复用口径：只算路由给 ai_image 的帧。

        mixed 任务里 ai_image 与 ai_video 并行渲染，计全帧会把对方的帧
        误算成失败（确定性，非竞态：本源永远不写对方的 visual_url）。
        """
        default_type = storyboard.config.video_type
        # graphic-native-text：graphic 帧归并 ai_image 渲染，覆盖率口径同入（修前会被
        # 误排除出分母 → graphic-only 任务误报覆盖率 0）
        return [f for f in storyboard.frames if (getattr(f, "visual_source", None) or default_type) in ("ai_image", "graphic")]

    async def render(
        self,
        storyboard: Storyboard,
        *,
        only_frames: list[int] | None = None,
        on_frame_done: Callable[[int], Awaitable[None]] | None = None,
        task_dir: Path | None = None,  # HR-T1: 接口统一，ai_image 不需占位（忽略）
        reference_image_paths: list[str] | None = None,  # Task 12: 项目角色三视图 front 注入（roster 模式下停用，M3）
        style_anchor_image_paths: list[str] | None = None,  # C4: 项目风格锚图（专用通道，决策 13）
        character_roster: dict | None = None,  # M3: name → {front_url, side_url, back_url}（roster 任务级下发）
        prop_roster: dict | None = None,  # M3: name → {main_image, extra_images}
    ) -> None:
        # C4（决策 13/17）：锚图经专用参数通道并入（合并顺序 角色 >（二期：道具）> 锚图）。
        # M3 过渡关系：roster 生效（character_roster/prop_roster 任一非空）→ 任务级
        # reference_image_paths 的角色/道具用途**停用**（整表 sheets[0] 语义退场），
        # 帧级 refs 在 fill(frame) 内按 visible_* 选择并**逐帧**过 apply_ref_cap；
        # 无 roster（无项目/脱离/无资产）→ 任务级合并 + 任务级 cap（现状行为不变）。
        from ..service import apply_ref_cap, record_ref_injection_sidecar  # 惰性导入避免环（service → visual_factory → 本模块）

        _anchor_list = list(style_anchor_image_paths or [])
        _roster_mode = bool(character_roster or prop_roster)
        _merged_refs: list[str] = []
        if not _roster_mode:
            _merged_refs, _refs_cap = apply_ref_cap(list(reference_image_paths or []) + _anchor_list)
            if _refs_cap.get("truncated") and task_dir is not None:
                record_ref_injection_sidecar(
                    task_dir,
                    frame_index=-1,
                    declared_entities=[],
                    injected_refs=[{"entity": "ref", "path": p} for p in _merged_refs],
                    truncated=[{"entity": "ref", "path": p} for p in _refs_cap["truncated"]],
                    skipped=[],
                    reason="budget",
                )
        sem = get_ai_api_semaphore(self._global_limit)  # 闸1: process-wide, not per-instance
        errors: list[str] = []

        target = storyboard.frames
        if only_frames is not None:
            target = [f for f in storyboard.frames if f.index in only_frames]

        # T-I0-④（2026-08-17 图片止血）：跨帧风格锚默认关--弱 style reference 会把
        # 整片拉向同一构图（「千篇一律」疑似放大器之一）；env=1 开回（legacy）。
        style_anchor_on = os.environ.get("VIDEO_GEN_IMAGE_STYLE_ANCHOR", "") == "1"
        last_success = [None]  # 跨帧风格锚：最近成功帧 URL（并发下 best-effort，仅 env 开时消费）

        async def fill(frame) -> None:
            # QP1-6 最后防线：image_prompt 为空也绝不把 narration 当画面词发给 provider。
            # 2026-08-01（Task 2）：原先此处写死一段英文兜底 prompt，与 ai_video.py
            # 及 _topic_fallback_prompt 是同一段英文的三份拷贝 → 改为共用同一函数
            # （已中文化），消除三处漂移。
            base_prompt = frame.image_prompt or _topic_fallback_prompt(storyboard)
            if getattr(frame, "visual_source", None) == "graphic" and getattr(frame, "graphic_spec", None):
                # graphic-native-text：图内文字指令注入（spec 已逐字符校验，文字串来自原文）
                # T5b（拍板⑦）：任务级版式卡随 storyboard 持久化传入——同任务所有
                # graphic 帧版式指令逐字一致；缺失回落确定性默认版式卡（注入器内兜底）。
                # P1（2026-08-19）：graphic 帧不走下发层风格尾——其版式卡+图内文字体系
                # 自带 Ark 300 预算不变量（inject 内部裁 base），外拼风格尾会破坏该不变量。
                from .graphic_text import inject_graphic_text

                prompt = inject_graphic_text(base_prompt, frame.graphic_spec, getattr(storyboard, "graphic_layout_card", "") or "")
            else:
                # P1（2026-08-19）下发层风格尾（确定性兜底）：存库 image_prompt 不动，
                # 仅发送时拼任务级风格（T-I0-② 退役逐帧前缀拼接后 ai_image 无任何
                # 词级保证——ai_video 有 IR style 槽+方言必拼两道，图片侧为零）。
                _provider_kind = _resolve_provider_kind(self._provider)
                is_ark = "ark" in _provider_kind or "seedream" in _provider_kind
                prompt = compose_dispatch_prompt(base_prompt, storyboard.config.style, ark_budget=is_ark)
            cfg = storyboard.config
            # T-D2（D2 方案 c，F7）：生图按帧版式槽位比例出图——remotion_course 帧
            # 在 draft 期已持久化 layout（service T-D2 钩子），图文页 5:4/金句 4:5
            # 等槽位不再被任务级 16:9 出图裁切；无版式（ai_image 任务/layout 缺失）
            # 落任务级宽高，存量行为不变。360 三档由 provider 就近贴档。
            from ..pipelines.remotion_course_props import slot_size_for_layout

            size = slot_size_for_layout(getattr(frame, "layout", None)) or f"{cfg.width}x{cfg.height}"

            async def _generate_with_moderation_retry(base_prompt: str, refs: list[str] | None) -> ImageRef:
                """审核拒绝两层重试：词表中性化 → 旁白直述；其余异常原样上抛。

                P1（2026-08-19）：对齐 ai_video 的 Task 12 + 第二层兜底——修前
                ai_image 生图路径对审核拒绝**无任何重试**，误判型（「人形光影」）
                直接废帧。改词成功后落 sidecar 告知（P0-2 语义：不默默改词）。
                """
                p = base_prompt
                pending_kind: str | None = None
                _last_err: Exception | None = None  # PEP 3110：except 块结束即删 _err，须显式保存才能在耗尽时 raise...from
                for _retry in range(3):  # 原始 1 次 + 两层重试各 1 次
                    if _retry > 0 and self._budget is not None:
                        # E1: 审核重试是真实调用必须计费——首次已由外层扣费
                        self._budget.charge()
                    try:
                        ref = await self._provider.generate_image(p, size=size, reference_image_paths=refs or None)
                    except VisualBudgetExceeded:
                        raise
                    except Exception as _err:
                        if not is_content_review_rejection(_err):
                            raise
                        _last_err = _err
                        nxt = neutralize_prompt(p)
                        if nxt != p:
                            pending_kind = KIND_PROMPT_NEUTRALIZED
                            logger.warning("AI image frame %d: moderation rejected — retrying with neutralized prompt", frame.index)
                        else:
                            narration = (frame.narration or "").strip()
                            if not narration:
                                raise  # 误判但旁白为空 → 维持上浮（覆盖率闸响亮失败）
                            nxt = narration[:120]
                            pending_kind = KIND_PROMPT_FALLBACK
                            logger.warning("AI image frame %d: moderation false-positive — retrying with narration prompt", frame.index)
                        p = nxt
                        continue
                    if _retry > 0 and pending_kind is not None and task_dir is not None:
                        try:
                            record_degradation(
                                task_dir,
                                kind=pending_kind,
                                detail=(
                                    f"第 {frame.index + 1} 镜画面提示词因内容审核被调整为更中性的表述后重新生成" if pending_kind == KIND_PROMPT_NEUTRALIZED else f"第 {frame.index + 1} 镜画面提示词疑似被内容审核误判，已改用旁白原文重新生成"
                                ),
                                frame_index=frame.index,
                            )
                        except Exception:
                            logger.warning("failed to record prompt retry sidecar", exc_info=True)
                    return ref
                # 审查修正（2026-08-19 问题2）：链接最后一次审核拒绝——裸 raise 会因
                # PEP 3110 变量删除丢失「最终死于哪层拒绝」，日志 traceback 断链。
                raise RuntimeError(f"AI image frame {frame.index}: moderation retry exhausted") from _last_err

            async with sem:
                # M3：roster 模式 → 帧级选择（角色>道具>锚图）+ 逐帧 cap + 留痕；
                # 非 roster 模式 → 任务级合并 refs（现状）。
                if _roster_mode:
                    _char_refs = _frame_character_ref_paths(frame, character_roster or {}) or []
                    _prop_refs = _frame_prop_ref_paths(frame, prop_roster or {})
                    # 道具文件缺失 → 跳过 + sidecar（M6 纪律：跳过≠失败，帧继续）
                    _prop_kept: list[str] = []
                    _missing: list[dict] = []
                    _path_to_name = {p: name for name, r in (prop_roster or {}).items() for p in [r.get("main_image"), *(r.get("extra_images") or [])] if p}
                    for _pr in _prop_refs:
                        if Path(_pr).exists():
                            _prop_kept.append(_pr)
                        else:
                            _missing.append({"entity": f"prop:{_path_to_name.get(_pr, '?')}", "path": _pr, "reason": "file_missing"})
                    refs, _frame_cap = apply_ref_cap(_char_refs + _prop_kept + _anchor_list)
                    _skipped = list(_missing)
                    # 截断留痕：声明数超限（角色>3/道具>2）与预算截断（>6）
                    _vc = getattr(frame, "visible_characters", None) or []
                    _vp = getattr(frame, "visible_props", None) or []
                    _over_declared = [{"entity": f"character:{i.get('name')}", "reason": "char_cap_3"} for i in _vc[3:]] + [{"entity": f"prop:{i.get('name')}", "reason": "prop_cap_2"} for i in _vp[2:]]
                    if task_dir is not None and (_frame_cap.get("truncated") or _skipped or _over_declared):
                        record_ref_injection_sidecar(
                            task_dir,
                            frame_index=getattr(frame, "index", 0),
                            declared_entities=[i.get("name") for i in (_vc or [])] + [i.get("name") for i in (_vp or [])],
                            injected_refs=[{"entity": "ref", "path": p} for p in refs],
                            truncated=[{"entity": "ref", "path": p} for p in _frame_cap.get("truncated", [])] + _over_declared,
                            skipped=_skipped,
                            reason=_frame_cap.get("reason", ""),
                        )
                    # X12（2026-08-28 用户拍板）：图片侧带名指代声明（同 X8 模板）前置——
                    # seedream 三人同框缺指代靠猜「图↔名」（实证性别翻转）；仅 refs 与
                    # 实体名 1:1（全角色走合成图）时生效，回退/None 帧不加（M4 纪律）。
                    _decl = build_image_ref_declaration(
                        frame,
                        character_roster or {},
                        prop_roster or {},
                        char_refs=_char_refs,
                        prop_kept=_prop_kept,
                        has_anchor=bool(_anchor_list),
                        final_refs=refs,
                    )
                    if _decl:
                        prompt = _decl + prompt
                else:
                    refs = list(_merged_refs)
                # 跨帧风格锚：角色 front + 上一帧成功图（弱 style reference，压逐帧漂移）
                if style_anchor_on and last_success[0]:
                    refs.append(last_success[0])
                # P0-4 layer 1: retry once (多数失败是偶发限流/超时)
                for attempt in range(2):
                    try:
                        # E1: 先扣费再调用——宁可少生成一帧也不超支。重试也计费
                        # （供应商侧确实产生了调用）。
                        if self._budget is not None:
                            self._budget.charge()
                        ref: ImageRef = await _generate_with_moderation_retry(prompt, refs)
                        # D2 修复批（2026-09-23）：gpt-image-2.5 尺寸贴档留痕——
                        # provider 就近映射了白名单外尺寸（如 remotion_course 槽位），
                        # 写 sidecar 让「出的图与请求规格有偏差」对用户可见。
                        _mapped_from = getattr(ref, "size_mapped_from", None)
                        if _mapped_from and task_dir is not None:
                            record_degradation(
                                task_dir,
                                kind=KIND_IMAGE_SIZE_MAPPED,
                                detail=f"第 {frame.index + 1} 镜生图尺寸 {_mapped_from} 不受所选生图模型支持，已就近贴档为 {ref.width}x{ref.height} 生成",
                                frame_index=frame.index,
                            )
                        frame.visual_url = ref.url
                        frame.visual_type = "image"
                        if style_anchor_on:
                            last_success[0] = ref.url  # 更新锚供后续帧（仅 env 开时）
                        break  # P1-2: 成功即出 retry 环；本地化移出信号量
                    except VisualBudgetExceeded:
                        # E1: 预算闸必须穿透 retry 与逐帧兜底——被 except Exception
                        # 吞掉会变成「静默少生成几帧」，正是要消灭的假成功。
                        raise
                    except Exception as e:
                        # P0-4: 只对瞬态错误重试（限流/超时/连接/断路器）。
                        # 400/401/403 这类永久错误重试成功率=0，只多扣一次费。
                        # 2026-08-08 P1-1：qihu 5xx 已映射为 ProviderUnavailableError
                        # （qihu_image.py），∈ _FALLBACK_EXC——经 Throttle 重试 + 本层
                        # 重试 + registry 降级。修前注释「Throttle 已重试 3 次」失实：
                        # 裸 RuntimeError 不在任何重试元组内，一次 500 即废帧。
                        from ..providers.registry import _FALLBACK_EXC

                        if isinstance(e, _FALLBACK_EXC) and attempt == 0:
                            logger.warning("AI image frame %d transient error, retrying: %s", frame.index, e)
                            continue
                        errors.append(f"Frame {frame.index}: {e}")
                        # exc_info 对齐 ai_video 失败分支——审核耗尽链（raise...from）
                        # 只有带 traceback 才可见（审查 2026-08-19 问题2 配套）。
                        logger.warning("AI image frame %d failed: %s", frame.index, prompt[:60], exc_info=True)
                        break
            # P1-2: 本地化移出全局信号量（下载不占上游 API 配额，对齐 ai_video D1 / commit 5aea479a5）
            if frame.visual_url and task_dir is not None:
                rel = await _localize_image(frame.visual_url, frame, task_dir)
                if rel:
                    frame.local_visual_path = rel
            if frame.visual_url and on_frame_done:
                await on_frame_done(frame.index)

        # T2: 无进展看门狗（活性判据）——20min 内没有任何一帧结束 → 上游 hang/限流。
        # 不用固定时长预算：>50 帧串行 + Tier E 续写的健康长任务会被误杀。
        await gather_with_no_progress_watchdog(
            fill,
            target,
            timeout=_NO_PROGRESS_TIMEOUT,
            message="生图阶段 20 分钟无帧完成，可能上游 hang 或限流",
        )

        # P0-4: coverage guard 在复用前算（基于原始成功数，不被复用抬高）
        # P0-4: coverage guard 在复用前算（基于原始成功数，不被复用抬高）
        # P0-3: 覆盖率口径只算本源帧，mixed 任务里 ai_video 帧不计入分母
        scope = self._coverage_scope(storyboard)
        total = len(scope)
        succeeded = sum(1 for f in scope if f.visual_url)
        coverage = succeeded / total if total else 0.0

        # Coverage guard: a silent partial-success used to yield a 1-second video
        # (only frames with visual_url are composed). Fail loudly instead so the
        # task framework can retry / surface a clear error to the user.
        if total and coverage < MIN_COVERAGE:
            first_err = errors[0] if errors else "unknown"
            raise RuntimeError(f"AI image generation coverage too low: {succeeded}/{total} frames ({coverage:.0%} < {MIN_COVERAGE:.0%}). First error: {first_err}. Provider may be slow, rate-limited, or down - please retry.")

        # P0-4: 失败帧保留 None -> 交给 worker layer-3 黑场占位（黑场 + 本帧旁白字幕）。
        # 不再复用上一帧画面：复用会导致"上一镜画面 + 本镜旁白"错位（visual/audio 错配）。
        # frame_ops.frames_to_regenerate 仍会把 None 帧标记为下次重生。
        for frame in storyboard.frames:
            if not frame.visual_url:
                logger.warning("frame %d has no visual - will render as black placeholder", frame.index)
