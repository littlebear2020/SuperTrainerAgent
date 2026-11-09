"""确定性视觉路由护栏 + 路由开关（content-match T5/T6，2026-08-14）。

LLM 只管推荐（T4 提示层已按主引擎收窄枚举），本层兜底保证多样性/枚举/成本
硬约束不被 LLM 漂移击穿——分块并发调 LLM 时块间互不可见（同
smooth_camera_continuity 的结构性原因），只能在全部结果汇总后统一收敛。

六规则（D3 默认值，阈值 env 可调）：
0. 枚举 + D8 零跨引擎：帧 visual_source 只允许 ∈ {任务主引擎, graphic}，
   其余值（未知/空/None/跨引擎/存量 remotion）一律回退主引擎——ai_image 任务
   永不产生生视频费用（D8 已拍板选项 B）。
1. graphic 占比 ≤ VIDEO_GEN_GRAPHIC_MAX_RATIO（默认 0.3），超出按帧序回退（确定性）。
   2026-08-31 路由并入批：统一 30% 上限（用户拍板）——取消「文稿有数字 → 0.5」
   分支（推荐端已并入调度表全片规划，护栏不再按文稿特征浮动）。
2. graphic 连续 ≤ VIDEO_GEN_GRAPHIC_MAX_RUN（默认 3）——防全片像 PPT。
3. 首/末镜不 graphic（开场收尾要氛围画面）。
4. 开关 off → 清全部帧 visual_source（回滚语义：新规划任务回到任务级统一源；
   已规划 storyboard 不受影响，见计划 §七）。
5. 非 ai_image/ai_video 任务（vimax 等）不触碰——引擎层不消费 frame.visual_source。
"""

from __future__ import annotations

import logging
import os

from ..storyboard import Storyboard

logger = logging.getLogger(__name__)

ROUTING_ENV = "VIDEO_GEN_CONTENT_ROUTING"
_OFF_VALUES = ("", "0", "false", "no", "off")

_DEFAULT_MAX_RATIO = 0.3  # 2026-08-31 拍板：0.5 → 0.3 统一上限（取消有数字 0.5 分支）
_DEFAULT_MAX_RUN = 3
_RATIO_ENV = "VIDEO_GEN_GRAPHIC_MAX_RATIO"
# 2026-08-31：默认路径不再区分有无数字；NO_DIGITS env 保留读取（向后兼容存量
# 部署的显式覆盖），优先级 = RATIO > RATIO_NO_DIGITS > 默认 0.3。
_RATIO_NO_DIGITS_ENV = "VIDEO_GEN_GRAPHIC_MAX_RATIO_NO_DIGITS"
_DEFAULT_MAX_RATIO_NO_DIGITS = 0.3
_RUN_ENV = "VIDEO_GEN_GRAPHIC_MAX_RUN"


def content_routing_enabled() -> bool:
    """D2（已拍板）：默认 on；``VIDEO_GEN_CONTENT_ROUTING=0|false|no|off`` 一键回退。"""
    return os.environ.get(ROUTING_ENV, "1").strip().lower() not in _OFF_VALUES


def _env_float(name: str, default: float) -> float:
    try:
        val = float(os.environ.get(name, "").strip())
        return val if val >= 0 else default
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        val = int(os.environ.get(name, "").strip())
        return val if val >= 0 else default
    except ValueError:
        return default


def normalize_visual_routing(storyboard: Storyboard, *, protect: frozenset[int] | set[int] = frozenset(), ratio_override: float | None = None) -> None:
    """对 ai_image/ai_video 任务的 storyboard.frames 就地收敛路由（纯函数式，无 IO）。

    ``protect``：帧号集合——**规划前已存在 visual_source 的帧**（用户手改值）。
    全部规则对其豁免（P0-5 不变量：手动改的不被 LLM 推荐/护栏覆盖；D8 管 LLM
    推荐漂移，不管用户手选——手动切 ai_video 是现行产品能力，M7/续点都支持）。

    ``ratio_override``（T3 2026-09-01）：任务级占比上限（0-1 小数，如 0.5）。
    **上限≠保障**——只抬天花板不设下限，推荐数仍由调度表按内容全片规划。
    优先级：任务级 > RATIO env > NO_DIGITS env（向后兼容存量覆盖）> 默认 0.3。
    None/0 = 未指定，走 env/默认链（行为与改动前完全一致）。
    """
    video_type = storyboard.config.video_type
    if video_type not in ("ai_image", "ai_video", "remotion_course"):
        return  # 规则 5：vimax 等不触碰（引擎层不消费，现状不变量）
    frames = storyboard.frames
    if not frames:
        return
    if video_type == "remotion_course":
        # remotion_course（2026-09-08 T4）：主引擎 = ai_image（课程视频默认生图为主），
        # 但允许 ai_video B-roll（验收场景 1：ai_image+ai_video+graphic 混合）——
        # 枚举集合比 ai_image 任务宽一档；None（未规划）统一落 ai_image，保证
        # has_mixed_sources/mixed 视觉源看到的全是真实引擎名。
        default = "ai_image"
        for f in frames:
            if f.index in protect:
                continue
            if f.visual_source not in ("ai_image", "ai_video", "graphic"):
                f.visual_source = default
        _clamp_graphic_rules(frames, protect, ratio_override, default)
        return
    default = video_type  # 任务主引擎

    if not content_routing_enabled():
        # 规则 4（回滚语义）：off → 清帧 visual_source（protect 集=用户手改，保留）
        for f in frames:
            if f.index not in protect:
                f.visual_source = None
        return

    # 规则 0：枚举 + D8 零跨引擎（只作用于本次规划写入的帧）
    for f in frames:
        if f.index in protect:
            continue
        if f.visual_source not in (default, "graphic"):
            f.visual_source = default

    _clamp_graphic_rules(frames, protect, ratio_override, default)

    # 规则 1/2/3 已抽到 _clamp_graphic_rules（remotion_course 分支复用同一套护栏）


def _clamp_graphic_rules(frames, protect, ratio_override, default) -> None:
    """graphic 护栏三规则（占比上限 / 连续 ≤3 / 首末镜）——ai_image/ai_video 与
    remotion_course 共用（2026-09-08 T4 抽出，行为对既有类型逐字节不变）。"""
    # 规则 1：占比上限（超出按帧序回退——从头回退保证幂等可重放）。
    # 2026-08-31 路由并入批（用户拍板）：统一 30% 上限——显式 RATIO env >
    # NO_DIGITS env（向后兼容存量覆盖）> 默认 0.3；不再按文稿有无数字浮动
    # （「有数字 50%」分支取消，推荐端已并入调度表全片规划）。
    # T3（2026-09-01）：链头插入任务级 ratio_override（用户下拉 30/40/50/70%），
    # 仅影响规则 1 上限——连续 ≤3 与首末镜规则不受影响。
    if ratio_override:
        ratio = ratio_override
    elif os.environ.get(_RATIO_ENV, "").strip():
        ratio = _env_float(_RATIO_ENV, _DEFAULT_MAX_RATIO)
    elif os.environ.get(_RATIO_NO_DIGITS_ENV, "").strip():
        ratio = _env_float(_RATIO_NO_DIGITS_ENV, _DEFAULT_MAX_RATIO_NO_DIGITS)
    else:
        ratio = _DEFAULT_MAX_RATIO
    max_graphic = int(len(frames) * ratio)
    graphic_indices = [i for i, f in enumerate(frames) if f.visual_source == "graphic" and i not in protect]
    if len(graphic_indices) > max_graphic:
        excess = len(graphic_indices) - max_graphic
        for i in graphic_indices[:excess]:
            frames[i].visual_source = default
        logger.info("visual routing: graphic 占比 %.0f%% 超上限 %.0f%%，回退前 %d 帧", 100 * len(graphic_indices) / len(frames), 100 * ratio, excess)

    # 规则 2：连续 ≤3（第 4 个连续 graphic 回退默认，计数重置；protect 帧豁免但计入连续）
    max_run = _env_int(_RUN_ENV, _DEFAULT_MAX_RUN)
    run = 0
    for i, f in enumerate(frames):
        if f.visual_source == "graphic":
            run += 1
            if run > max_run and i not in protect:
                f.visual_source = default
                run = 0
        else:
            run = 0

    # 规则 3：首/末镜不 graphic（protect 帧豁免）
    if frames[0].visual_source == "graphic" and frames[0].index not in protect:
        frames[0].visual_source = default
    if frames[-1].visual_source == "graphic" and frames[-1].index not in protect:
        frames[-1].visual_source = default
