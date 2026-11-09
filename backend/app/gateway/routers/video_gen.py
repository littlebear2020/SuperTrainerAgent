"""Video generation API router."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import unicodedata
import uuid
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Body, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.gateway._shared.llm_usage_sink import set_llm_accounting_context
from app.gateway._shared.safe_download import safe_download
from app.gateway._shared.task_framework.schemas import DRAFT, TERMINAL_STATUSES, TaskBrief, TaskDetail
from app.gateway.video_gen.degradations import KIND_MODEL_ROSTER_MISS, record_degradation
from app.gateway.video_gen.schemas import (
    CreateVideoRequest,
    ReorderRequest,
    VideoCostEstimate,
    _model_roster,
)
from app.gateway.video_gen.service import (
    STALE_PREVIEW_RESET_MESSAGE,
    STALE_REGEN_RESET_MESSAGE,
    assert_frame_previewable_not_upload,
    fold_stale_running_states,
    frame_has_user_visual,
    validate_frame_previewable,
)
from app.gateway.video_gen.storyboard import REMOTION_LAYOUTS, Storyboard
from deerflow.config.paths import get_paths
from deerflow.runtime.user_context import get_effective_user_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/video", tags=["Video Gen"])

# 在途单帧预览（(task_id, frame_index) → asyncio.Task）。双重职责：
# 1) 去重门——手动点击与 preview-all 不重复生成同一帧；
# 2) 持强引用防后台任务被 GC（asyncio.create_task 官方要求持引用）。
_preview_inflight: dict[tuple[str, int], asyncio.Task] = {}
# H4: 单帧重生在途登记（202 异步化，与 _preview_inflight 同模式）。
_regen_inflight: dict[tuple[str, int], asyncio.Task] = {}
# 在途 preview-all 循环（task_id → asyncio.Task），防同任务批量预览叠加。
_preview_all_running: dict[str, asyncio.Task] = {}


def _get_service(request: Request):

    svc = getattr(request.app.state, "video_gen_service", None)
    if svc is None:
        raise HTTPException(status_code=503, detail="Video generation service not available")
    return svc


def _safe_warning(channel: str, fn: Callable[[], None]) -> None:
    """逐通道隔离：一路告警读取失败不连累其余。

    2026-07-29 / 历史记录：此前一个 `except Exception: pass` 包住全部六路，
    发生过一次全面失明（payload 未定义 → NameError 被吞 → 所有质量告警静默丢失，
    本文件上方注释有记录）。单路失败只记日志，其余照常读出。
    """
    try:
        fn()
    except Exception:  # noqa: BLE001
        logger.warning("quality warning channel %r failed", channel, exc_info=True)


# Task 23：降级 kind → 用户可读中文。worker/gateway 侧只写机器 kind，
# 文案集中在这里，避免各管线各写一版。
_DEGRADATION_LABELS = {
    "bgm_failed": "背景音乐混音失败，成片没有背景音乐",
    "bgm_missing": "背景音乐文件缺失，成片没有背景音乐",
    "subtitle_burn_failed": "字幕烧录失败，成片没有字幕",
    "transition_downgraded": "转场特效不可用，已降级为硬切",
    "voice_fallback": "配音音色临时降级为备用音色（可对该帧点「重新生成」恢复）",
    # Task 4（2026-08-03）：视频模型产出短于旁白槽位 → worker 尾部定格补齐。
    # 具体秒数在 sidecar 的 detail 里；本表只给统一句式（表本身是硬编码白名单，
    # 不登记就会走兜底文案，把英文 kind 泄漏给终端用户）。
    "visual_shortfall": "画面时长短于旁白，尾部已定格补齐（可对该帧点「重新生成」再试）",
    # Task 5（2026-08-03）：当前视频模型无图生视频能力 → 预览图未能作首帧继承，
    # 成片构图会与预览图不同。这是 provider 能力限制而非故障，故文案给出可行动建议。
    "preview_i2v_unsupported": "当前视频模型不支持图生视频，预览图未作为首帧继承，成片构图可能与预览图不同（改用 seedance-ark 可「预览即所得」）",
    # P0-2（2026-08-03）：内容审核拒绝后自动改词重试——成片画面与用户所写提示词
    # 不一致，必须告知，否则用户只会觉得「模型没听懂我的话」。
    "prompt_neutralized": "该镜画面提示词因内容审核被调整为中性表述后重新生成（成片画面可能与原描述略有差异）",
    # P1（2026-08-19）：审核误判（词表无可中性化）→ 改用旁白原文重试——画面
    # 会比原提示词朴素，必须告知。
    "prompt_fallback_narration": "该镜画面提示词疑似被内容审核误判，已改用旁白原文重新生成（成片画面可能比原描述朴素）",
    # Phase 2（2026-08-13）：续写延长——画面短于旁白，已自动尾帧续写补长。
    "video_extended": "画面时长短于旁白，已自动尾帧续写延长（续写后残余缺口由 worker 补齐）",
    # Phase 3（2026-08-13）：超长旁白帧自动拆分——单镜旁白过长会被拆成多帧。
    "frame_split": "部分镜头旁白过长，已自动拆分为多个镜头",
    # 2026-08-19：预览图/组锚图被上游图片内容检测误拦 → 丢图降级纯 t2v 重试出片。
    # 中性措辞（08-20 定稿）：本 kind 同时服务 preview 与 group_anchor 两路径
    # （detail 里再区分），label 不写「预览图」——组锚帧看到该措辞会与事实不符。
    "preview_image_rejected": "该镜参考图未通过上游图片内容检测（多为误判），已降级为纯文生视频重新生成，构图或同组场景一致性可能受影响",
    # 2026-08-20（评审修订③）：character 被拦不丢图不重试、直接占位——用户看到
    # 灰帧须知道原因与出路，附可行动指引。
    "character_image_rejected": "该镜角色参考图未通过检测，更换角色形象后可重试本镜",
    # 2026-09-01 文案补全：以下 kind 此前走兜底文案，把英文 kind 泄漏给终端用户
    # （「出现降级处理（graphic_spec_rejected）」）。纯文案补齐，无行为变化。
    "align_failed": "个别镜头音频对齐失败，沿用未对齐配音（该镜起可能音画错位）",
    "dialogue_truncated": "个别镜头台词过长，已加速处理仍截断了尾词",
    "low_visual_quality": "个别镜头画面疑似黑帧或冻结（质量检测命中，已留痕）",
    # 2026-09-02 复核修订：「或保底卡片」为已退役语义（text_card_from_narration
    # 保底卡 2026-08-17 随 Pillow 渲染器删除，spec 拒绝后无论降级/protect 都是
    # 常规画面出图）——修正为事实一致的单一措辞。
    "graphic_spec_rejected": "该镜图文卡规格校验未通过，已改为常规画面生成",
    # 2026-09-02 标题截断修复：spec 有效但部分文字超渲染预算被省略——卡片照出，
    # 纯告知（不进 T2 两桶：重试合成/重新生成都不是对症指引）。
    "graphic_spec_field_omitted": "该镜图文卡部分文字因超长未显示（卡片其余内容正常呈现）",
    "shot_table_fallback": "镜头调度表生成失败，已降级为逐镜独立规划（相邻镜头连贯性可能下降）",
    "tts_access_key_missing": "豆包音色密钥未配置，全片配音已降级为备用预设音色（与所选音色不同）",
    # 条目 13（2026-09-12 缺陷批）：认领异常（磁盘满/权限）与「过期」语义不同，单列 kind。
    "embedded_images_claim_failed": "文档配图认领失败（磁盘/权限异常），未自动分配，可在分镜编辑器手动上传",
    # 2026-10-14 vimax 配音修复批：T6 格式漂移守门（未识别 [...] 标签，已剥离不念出，
    # detail 含样本）；T4 无标签音效启发式命中（已静音）。纯文案，行为见 narration_generator。
    "format_drift": "本片配音格式异常：分镜音频标注含未识别标签，已自动剥离（建议反馈格式样本以便解析器兼容）",
    "sfx_heuristic": "个别镜头按无标签音效描述静音处理（如误伤旁白请调整该镜音频标注）",
    # 条目 7（2026-09-12 缺陷批）：撤册模型名留痕（建单时 config 级 / 运行时字幕回退）。
    # 复审二轮 2026-09-13：models[] 补名热更即时生效；video_gen 段是 svc._config
    # 启动快照，删字段须重启 gateway——文案补注避免管理员改完等不到消失。不再统写
    # 「已回退默认链路」（script_model 无回退，具体回退路径看各记录 detail）。
    "model_roster_miss": "配置引用的模型未在花名册注册（请联系管理员检查 config.yaml models 配置；修改 video_gen 段后需重启 gateway 生效）",
    "subtitle_llm_fallback": "字幕语义重切模型不可用，已回退规则链断句（断句质量可能下降）",
    # 2026-10-16 vimax 收口批 T3：静态降级（帧级/视频级）与 redo 漂移——这些 kind 的
    # sidecar detail 已含镜号+原因摘要，_attach_degradation_warning 直接上屏 detail
    # （本表作 detail 缺失时的兜底文案）；镜级语义用「镜」不用「帧」。
    "video_clip_static_fallback": "该镜视频生成失败，已用静态画面占位（任务重试可恢复真实画面）",
    "first_frame_static_fallback": "该镜画面帧生成失败，已降级为占位帧（任务重试可重生真实帧）",
    "vimax_redo_duration_drift": "重做镜头时长有变化，已平移字幕/旁白对齐时间轴（边界后可能轻微音画错位）",
}

# T3（2026-10-16 收口批）：vimax 镜级降级 kind——sidecar detail 由消费方写入、
# 已含全局镜号与原因摘要，透出时直接上屏 detail；镜号语义用「镜」（非「帧」）。
_VIMAX_SHOT_DETAIL_KINDS = frozenset({"video_clip_static_fallback", "first_frame_static_fallback", "vimax_redo_duration_drift"})

# T2（2026-09-01 降级提示分级）：按 kind 分桶拼指引尾巴，取代修前无条件
# 「如需完整效果请点重试合成」——recompose 只重新拼接盘面素材不重新生成帧
# （service.recompose_task docstring 自述 no re-fetch/re-TTS），定格类（生成阶段
# 产物）重试合成拿同样素材再拼一遍，指引是错的。
# - _RECOMPOSE_FIXABLE：合成阶段问题（BGM 混音失败等）——重试合成真能修；
# - _FRAME_REGEN_KINDS：生成阶段产物（画面短/图被拦/音色降级）——须对帧点
#   「重新生成」后重试合成才生效；
# - **两桶之外 = 纯告知（差集语义）**：不显式枚举——当前实际存在但未列入的
#   kind（video_extended/frame_split/prompt_neutralized 等）及未来新增自动落
#   此桶，显式枚举必漏。知悉项：_RECOMPOSE_FIXABLE 中 bgm_missing/
#   subtitle_burn_failed/transition_downgraded 当前无写入点（现状只有 bgm_failed
#   能进桶），列上无害（向前兼容）。
_RECOMPOSE_FIXABLE = frozenset({"bgm_failed", "bgm_missing", "subtitle_burn_failed", "transition_downgraded"})
_FRAME_REGEN_KINDS = frozenset({"visual_shortfall", "character_image_rejected", "preview_image_rejected", "voice_fallback"})


def _attach_degradation_warning(result: dict, task_dir: Path, status: str | None) -> None:
    """把 worker 写的 ``degradations.json`` 转成一条中文告警。

    C11/C13/C6：BGM 混音失败 / 字幕烧录失败 / 转场降级此前都只落 worker 日志，
    用户拿到"看着像成功"的片子却少了自己勾的东西。只在终态读——详情页 2 秒
    轮询，中途读到的是半成品。未知 kind 走兜底文案，宁可泛化也不静默。
    """
    if status not in ("ready", "failed"):
        return
    p = task_dir / "degradations.json"
    if not p.exists():
        return
    # 键名必须与 writer 一致：degradations.py / _degradation_sidecar.py 都写
    # {"items": [...]}。此处曾误读 "records"，导致 Task 23 建的整条降级通道
    # 从未真正上报过任何一条（BGM 失败/对齐失败/台词截断全部静默丢弃）——
    # 两侧各自单测都绿，缺的是这条跨边界的端到端断言。
    records = (json.loads(p.read_text(encoding="utf-8")) or {}).get("items") or []
    if not records:
        return
    seen: list[str] = []
    for rec in records:
        kind = rec.get("kind") or ""
        if kind in _VIMAX_SHOT_DETAIL_KINDS and rec.get("detail"):
            # T3（2026-10-16）：vimax 镜级降级——detail 已含镜号+原因摘要，直接上屏
            # （修前只显示 kind 标签，消费方写的原因全被吞）。
            label = str(rec["detail"])[:160]
        else:
            label = _DEGRADATION_LABELS.get(kind) or f"出现降级处理（{kind or '未知'}）"
            idx = rec.get("frame_index")
            if isinstance(idx, int):
                label = f"帧 {idx + 1}：{label}"
        if label not in seen:
            seen.append(label)
    # T2：分桶拼尾巴（差集语义：两桶之外即纯告知，无尾巴）。两桶皆非空 → 两条
    # 尾巴都拼（文案略长但准确）；皆空 → 纯告知（video_extended 等），不加指引。
    kinds = {rec.get("kind") or "" for rec in records}
    tails: list[str] = []
    if kinds & _RECOMPOSE_FIXABLE:
        tails.append("——以上合成问题可点「重试合成」修复")
    if kinds & _FRAME_REGEN_KINDS:
        tails.append("——画面类问题需对相应帧点「重新生成」后重试合成才生效")
    result["degradation_warning"] = "；".join(seen) + "".join(tails)


def _task_with_thumbnail(
    task,
    storage_dir: str = "",
    user_id: str = "",
    *,
    include_frame_refs: bool = False,
    include_storyboard_summary: bool = False,
) -> TaskDetail:
    """Build TaskDetail (incl payload/result), attaching thumbnail_url and title if available.

    Task 18-20: 附着失败态（字幕状态/失败帧数/时长偏差）从 task_dir 读取，前端详情页可见。
    include_frame_refs（M5，仅任务详情端点传 True）：并入 sidecar/ref_injection.json
    （分镜级「参考资产」标签数据源，决策 19；列表端点默认 False 防响应膨胀）。
    include_storyboard_summary（2026-10-20 Task 4，仅详情端点）：读盘面 storyboard.json
    得帧数/总时长；result.vimax_shots 得渲染后镜头数。列表端点不读盘（防 N 次 I/O）。
    """
    brief = TaskDetail.model_validate(task, from_attributes=True)
    result = json.loads(task.result or "{}") if hasattr(task, "result") else {}
    # Extract title: prefer storyboard title from result, then topic/script from payload
    # P1-3: payload 无条件初始化——修前只在 else 分支赋值，下方在 if 路径使用 →
    # NameError 被大 except Exception 吞掉，全部质量告警静默丢失（草稿流主线必触发）。
    # 2026-07-29 起各路告警走 _safe_warning 逐通道隔离，同类事故不再全面失明。
    payload = json.loads(task.payload) if isinstance(task.payload, str) else (task.payload or {})
    sb = result.get("storyboard") if isinstance(result, dict) else None
    # 2026-10-20 Task 5 已拍板优先级：payload.title > result.storyboard.title
    # > payload.topic > payload.script[:30]（空/纯空白 title 由 schema 归一为 None）。
    _payload_title = (payload.get("title") or "").strip() if isinstance(payload.get("title"), str) else None
    if _payload_title:
        brief.title = _payload_title
    elif sb and sb.get("title"):
        brief.title = sb["title"]
    else:
        brief.title = payload.get("topic") or (payload.get("script", "")[:30] or None)
    thumb = result.get("thumbnail_path", "")
    if thumb and Path(thumb).exists():
        brief.thumbnail_url = f"/api/video/tasks/{task.id}/thumbnail"
    else:
        # 2026-09-13 死链修复：文件失或 result 无 thumbnail_path 时，DB 陈旧
        # thumbnail_url 列值（P2-4 完成路径落库）不再下发——修前 model_validate
        # 把脏列带出 → 前端 <img> 轮询期循环 404。else 覆盖两种脏值形态。
        brief.thumbnail_url = None
    # 假就绪可见化（2026-09-13）：仅 ready 态按 result.output_path 存在性标注
    # 产物缺失（与 download 端点 L408 的存在性判定同口径）；非 ready 恒 False。
    if brief.status == "ready":
        out = result.get("output_path", "")
        brief.artifact_missing = not (out and Path(out).exists())

    # Task 18-20: 失败态可见——从 task_dir 读 subtitle_status + duration 校验
    if storage_dir and user_id and isinstance(result, dict):
        task_dir = Path(storage_dir) / user_id / "tasks" / task.id
        if include_storyboard_summary:
            disk_sb: dict | None = None
            sb_summary_path = task_dir / "storyboard.json"
            if sb_summary_path.exists():
                try:
                    parsed = json.loads(sb_summary_path.read_text(encoding="utf-8"))
                    disk_sb = parsed if isinstance(parsed, dict) else None
                except (json.JSONDecodeError, OSError):
                    logger.warning("storyboard summary parse failed for task %s", task.id, exc_info=True)
            source = disk_sb if disk_sb is not None else (sb if isinstance(sb, dict) else None)
            if source is not None:
                frames = source.get("frames")
                if isinstance(frames, list):
                    brief.frame_count = len(frames)
                total = source.get("total_duration")
                if isinstance(total, (int, float)) and not isinstance(total, bool):
                    brief.total_duration = float(total)
            shots = result.get("vimax_shots")
            brief.shot_count = len(shots) if isinstance(shots, dict) else 0
            # manifest 可用性 != 任务状态：failed/recompose/QC 仍可逐镜重做。
            brief.shots_ready = isinstance(shots, dict) and len(shots) > 0
        # M5（决策 19）：并入参考注入留痕（每帧 declared/injected/truncated/skipped）——
        # 分镜级「参考资产」标签数据源。标签是事实数据非告警：不进下方终态门控的
        # warning 通道（running 态逐帧渐进写入，轮询读到的是当前真相，无 409 风险）。
        if include_frame_refs:
            _ri_path = task_dir / "sidecar" / "ref_injection.json"
            if _ri_path.exists():
                try:
                    _ri = json.loads(_ri_path.read_text(encoding="utf-8"))
                    if isinstance(_ri, list) and _ri:
                        result["ref_injection"] = _ri
                except (json.JSONDecodeError, OSError):
                    pass
        from app.gateway.video_gen.service import (
            _check_duration_drift,
            _extract_av_drift,
            _extract_remotion_warnings,
            _extract_subtitle_status,
            _extract_visual_coverage,
        )

        def _ch_subtitle_status() -> None:
            result.setdefault("subtitle_status", _extract_subtitle_status(task_dir))

        def _ch_duration_drift() -> None:
            # 时长偏差：实际(output duration) vs 目标(target_duration)
            target = payload.get("target_duration") if isinstance(payload, dict) else None
            sb_path = task_dir / "storyboard.json"  # 实际时长从 storyboard.json 读（若有）
            if not (target and sb_path.exists()):
                return
            actual = json.loads(sb_path.read_text(encoding="utf-8")).get("total_duration")
            if actual:
                drift = _check_duration_drift(float(actual), float(target))
                if drift:
                    result.setdefault("duration_warning", drift)

        def _ch_remotion_cjk() -> None:
            # F: remotion CJK 字体缺失 warning（命名字段，前端按 duration_warning 模式渲染）
            remotion_warn = _extract_remotion_warnings(task_dir)
            if remotion_warn:
                result["remotion_cjk_warning"] = remotion_warn.get("warning")

        def _ch_visual_coverage() -> None:
            # G/QP1-3/4: ai_video 覆盖率 + 角色参考降级 warning
            cov = _extract_visual_coverage(task_dir)
            if not cov:
                return
            _warns: list[str] = []
            if cov.get("coverage", 1.0) < 1.0:
                _warns.append(f"ai_video 部分帧生成失败并用占位补齐：{cov['succeeded']}/{cov['total']} 成功（{cov['coverage']:.0%}），建议重试失败帧")
            if cov.get("character_ref_ignored"):
                _warns.append("当前视频服务不支持角色参考图，人物一致性未生效（建议配置 Ark 直连 seedance）")
            elif cov.get("character_ref_degraded"):
                _warns.append("视频服务降级，角色参考仅首图生效，人物一致性可能下降")
            elif cov.get("character_ref_truncated"):
                _warns.append("当前视频服务仅支持单张首帧参考图，角色三视图只取首图，人物一致性可能下降")
            if _warns:
                result["ai_video_coverage_warning"] = "；".join(_warns)

        def _ch_av_drift() -> None:
            # E4: 转场音画漂移 warning（xfade 使字幕/口型提前时诚实提示）
            drift = _extract_av_drift(task_dir)
            if drift and drift.get("drift_sec", 0) > 0.5:
                result["av_drift_warning"] = f"本视频含 {drift.get('transition_count', 0)} 段转场，字幕可能有约 {drift['drift_sec']:.1f}s 漂移，建议关闭转场或重新合成"

        def _ch_voice_mode() -> None:
            # T16 (2026-07-27): ViMax 对话配音回退可见化（engine 在角色表解析失败/
            # 缺失时写标记文件——此前用户完全无感知「配音只有旁白」）
            p = task_dir / "voice_mode_warning.txt"
            if p.exists():
                result["voice_mode_warning"] = p.read_text(encoding="utf-8").strip()

        def _ch_i2i() -> None:
            # 2026-07-29：_warn_if_image_provider_not_i2i 写了这个 sidecar，
            # 但全仓零读取——从写下的第一天起就从未到达用户。
            p = task_dir / "ai_image_i2i_warning.json"
            if p.exists():
                warning = json.loads(p.read_text(encoding="utf-8")).get("warning")
                if warning:
                    result["ai_image_i2i_warning"] = warning

        def _ch_degradation() -> None:
            _attach_degradation_warning(result, task_dir, getattr(task, "status", None))

        def _ch_placeholder() -> None:
            # 2026-07-29：worker 侧黑场占位降级上报（全帧占位曾静默 ready）。
            # 只在终态读——本函数是详情页 2 秒轮询的下游。
            if getattr(task, "status", None) not in ("ready", "failed"):
                return
            p = task_dir / "placeholder_frames.json"
            if not p.exists():
                return
            data = json.loads(p.read_text(encoding="utf-8"))
            indices = data.get("indices") or []
            if not indices:
                return
            shown = "、".join(str(i + 1) for i in indices[:10])
            total = data.get("total") or 0
            result["placeholder_warning"] = f"帧 {shown}{' 等' if len(indices) > 10 else ''} 的画面素材获取失败，已用黑场占位（{len(indices)}/{total} 帧）——请对这些帧点「重新生成」后重新合成"

        # 2026-08-19（反馈2-2 P1a）：全部告警通道统一终态门控——修前仅 placeholder
        # （下方 _ch_placeholder）与 degradation（_attach_degradation_warning）有
        # 门控，coverage/duration/cjk/av_drift/voice/i2i/subtitle 七通道任意状态从
        # sidecar 附加 → 自动重试 running 态读上一轮残留 → 前端可点重组按钮 → 409。
        # 半成品告警不在非终态可见（同两处既有门控的 docstring 口径）。
        _show_quality_warnings = getattr(task, "status", None) in ("ready", "failed")
        for _channel, _fn in (
            ("subtitle_status", _ch_subtitle_status),
            ("duration_warning", _ch_duration_drift),
            ("remotion_cjk_warning", _ch_remotion_cjk),
            ("ai_video_coverage_warning", _ch_visual_coverage),
            ("av_drift_warning", _ch_av_drift),
            ("voice_mode_warning", _ch_voice_mode),
            ("ai_image_i2i_warning", _ch_i2i),
            ("placeholder_warning", _ch_placeholder),
            ("degradation_warning", _ch_degradation),
        ):
            if not _show_quality_warnings:
                break
            _safe_warning(_channel, _fn)

    brief.result = result
    return brief


@router.get("/tasks", response_model=list[TaskDetail])
async def list_video_tasks(request: Request):
    svc = _get_service(request)
    user_id = get_effective_user_id()
    tasks = await svc.list_tasks(user_id)
    # T-E10（M5，2026-09-09 feedback-round1）：still 预览任务（remotion_still）是
    # 内部渲染单元（st_ 前缀、短租约），不是用户任务——修前列表被 [still] 行污染。
    tasks = [t for t in tasks if t.task_type != "remotion_still"]
    briefs = [_task_with_thumbnail(t, storage_dir=svc._storage_dir, user_id=user_id) for t in tasks]
    from app.gateway._shared.task_cost import attach_task_costs

    await attach_task_costs(briefs)
    return briefs


@router.get("/tasks/{task_id}", response_model=TaskDetail)
async def get_video_task(request: Request, task_id: str):
    svc = _get_service(request)
    user_id = get_effective_user_id()
    # T13：详情查询附 composing 未认领队位（排队体验，TaskBrief.queue_position）
    task = await svc.get_task_with_queue_position(task_id, user_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    # M5：仅详情端点并入 ref_injection（分镜级参考资产标签数据源；列表端点不并）
    # 2026-10-20：详情摘要/告警读取含同步盘 IO（storyboard.json 可达数百 KB），
    # 整个构建放线程池，避免 async 端点阻塞事件循环。
    detail = await asyncio.to_thread(
        _task_with_thumbnail,
        task,
        svc._storage_dir,
        user_id,
        include_frame_refs=True,
        include_storyboard_summary=True,
    )
    from app.gateway._shared.task_cost import attach_task_costs

    await attach_task_costs([detail])
    return detail


# ── ViMax 费用预估（2026-10-20 Task 6，只读；不参与扣费/预算闸） ─────────────

_COST_HISTORY_CACHE: OrderedDict[str, tuple[float, float | None, int]] = OrderedDict()
_COST_HISTORY_TTL_S = 60.0


def _cost_resolve_prices(payload: dict, cfg, *, voice_id: str | None = None) -> dict[str, dict | None]:
    """按 ViMax 实际 backend 选择解析价目（纯读，不写任何计费状态）。

    复用 ``_resolve_vimax_backend_selection``：image 360→seedream-360 / ark→
    seedream；video seedance/seedance-ark；TTS 由盘面 voice_id 选 provider。
    """
    from app.gateway._shared.media_pricing_resolver import resolve_media_price
    from app.gateway.video_gen.service import _resolve_vimax_backend_selection

    selection = _resolve_vimax_backend_selection(cfg, style=payload.get("style"))
    image_provider = selection["image_pricing_provider"]
    video_provider = selection["video_pricing_provider"]
    if (voice_id or "").startswith("doubao"):
        tts_provider = "doubao_tts"
    elif (voice_id or "").startswith("qihu"):
        tts_provider = "qihu_tts"
    else:
        tts_provider = "edge"
    return {
        "image": resolve_media_price(image_provider, None, "image"),
        "video": resolve_media_price(video_provider, None, "video"),
        "tts": resolve_media_price(tts_provider, None, "tts"),
    }


async def _cost_history_per_shot(svc, user_id: str, exclude_task_id: str) -> tuple[float | None, int]:
    """近 10 个成功 vimax 任务每镜成本中位数 + 样本数（60s LRU；异常 → (None,0)）。"""
    import statistics
    import time

    # 按 user 单 key：同用户多草稿共享一次历史样本；排除当前任务对 10 样本
    # 中位数影响可忽略，避免按 task_id 无界增长。
    key = user_id
    cached = _COST_HISTORY_CACHE.get(key)
    now = time.monotonic()
    if cached is not None and now - cached[0] < _COST_HISTORY_TTL_S:
        _COST_HISTORY_CACHE.move_to_end(key)
        return cached[1], cached[2]
    value: float | None = None
    sample_count = 0
    try:
        from app.gateway._shared.task_cost import attach_task_costs

        tasks = await svc.repo.list_by_user(
            user_id,
            domain=getattr(svc, "domain", "video_gen"),
            status="ready",
            task_type="vimax",
            limit=50,
        )

        def _prepare_samples(rows) -> tuple[list, list[TaskBrief], dict[str, int]]:
            picked = [t for t in rows if getattr(t, "task_type", None) == "vimax" and getattr(t, "status", None) == "ready" and getattr(t, "id", None) != exclude_task_id][:10]
            briefs = [TaskBrief.model_validate(t, from_attributes=True) for t in picked]
            counts: dict[str, int] = {}
            for t, brief in zip(picked, briefs):
                try:
                    result = json.loads(t.result or "{}") if isinstance(t.result, str) else (t.result or {})
                except json.JSONDecodeError:
                    result = {}
                shots = result.get("vimax_shots") if isinstance(result, dict) else None
                counts[brief.id] = len(shots) if isinstance(shots, dict) else 0
            return picked, briefs, counts

        # result JSON（含 vimax_shots manifest）解析放线程池，避免阻塞事件循环。
        candidates, briefs, shot_counts = await asyncio.to_thread(_prepare_samples, tasks)
        await attach_task_costs(briefs)
        ratios = [b.cost / shot_counts[b.id] for b in briefs if b.cost is not None and b.cost > 0 and shot_counts.get(b.id)]
        if ratios:
            value = float(statistics.median(ratios))
            sample_count = len(ratios)
    except Exception:
        logger.warning("cost estimate history lookup failed; marking partial", exc_info=True)
        value = None
    _COST_HISTORY_CACHE[key] = (now, value, sample_count)
    _COST_HISTORY_CACHE.move_to_end(key)
    while len(_COST_HISTORY_CACHE) > 128:
        _COST_HISTORY_CACHE.popitem(last=False)
    return value, sample_count


async def _cost_spent_cny(task) -> float | None:
    """草稿已花费——复用既有任务成本聚合（只读）。"""
    try:
        from app.gateway._shared.task_cost import attach_task_costs

        brief = TaskBrief.model_validate(task, from_attributes=True)
        await attach_task_costs([brief])
        return brief.cost
    except Exception:
        logger.warning("cost estimate spent lookup failed", exc_info=True)
        return None


@router.get("/tasks/{task_id}/cost-estimate", response_model=VideoCostEstimate)
async def get_video_cost_estimate(request: Request, task_id: str):
    """ViMax 只读费用/成片时长预估（draft/failed/ready；不触发任何 provider）。"""
    from app.gateway.video_gen.cost_estimate import _SECONDS_PER_SHOT, estimate_vimax_cost

    svc = _get_service(request)
    user_id = get_effective_user_id()
    task = await svc.repo.get(task_id, user_id=user_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.status not in (DRAFT, "failed", "ready"):
        raise HTTPException(status_code=409, detail="任务渲染进行中，完成后可预估")
    if getattr(task, "task_type", None) != "vimax":
        raise HTTPException(status_code=404, detail="仅 ViMax 任务支持费用预估")

    task_dir = Path(svc._storage_dir) / user_id / "tasks" / task_id
    sb_path = task_dir / "storyboard.json"
    if not sb_path.exists():
        raise HTTPException(status_code=404, detail="分镜数据不存在，无法预估")
    try:
        storyboard = await asyncio.to_thread(lambda: json.loads(sb_path.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, OSError):
        logger.warning("cost estimate storyboard parse failed for task %s", task_id, exc_info=True)
        raise HTTPException(status_code=404, detail="分镜数据损坏，无法预估") from None
    frames = storyboard.get("frames") if isinstance(storyboard, dict) else None
    frames = frames if isinstance(frames, list) else []
    try:
        total_duration = float(storyboard.get("total_duration") or 0.0)
    except (TypeError, ValueError):
        total_duration = 0.0
    try:
        payload = json.loads(task.payload or "{}") if isinstance(task.payload, str) else (task.payload or {})
    except json.JSONDecodeError:
        payload = {}
    from app.gateway.video_gen.engines.vimax.storyboard_budget import resolve_vimax_budget

    resolved_budget = resolve_vimax_budget(
        explicit_budget=payload.get("vimax_global_shot_budget"),
        target_duration=payload.get("target_duration"),
        storyboard_duration=total_duration,
    )
    if resolved_budget is not None:
        # 预算已是镜头数上限；盘面帧多于上限时按上限估算（238s/35帧→30镜），
        # 无帧时直接采用预算。
        n_shots = min(len(frames), resolved_budget.shot_budget) if frames else resolved_budget.shot_budget
    else:
        n_shots = max(len(frames), math.ceil(total_duration / _SECONDS_PER_SHOT) if total_duration > 0 else 0)
    if n_shots <= 0:
        raise HTTPException(status_code=404, detail="分镜数据为空，无法预估")

    # 草稿阶段通常尚未生成 vimax_working/characters.json；三视图成本不能按 0 漏算。
    n_characters = 2
    chars_path = task_dir / "vimax_working" / "characters.json"
    if chars_path.exists():
        try:
            parsed_chars = await asyncio.to_thread(lambda: json.loads(chars_path.read_text(encoding="utf-8")))
            if isinstance(parsed_chars, list):
                n_characters = len(parsed_chars)
        except (json.JSONDecodeError, OSError):
            n_characters = 2
    tts_chars = sum(len(str(f.get("narration") or "")) for f in frames if isinstance(f, dict))

    try:
        from app.gateway._shared.media_pricing_resolver import _load_media_pricing

        # resolve_media_price 读进程缓存；端点可能早于任何计费调用，先 warm 一次。
        await _load_media_pricing()
    except Exception:
        logger.warning("cost estimate media pricing warmup failed; may fall back to partial", exc_info=True)

    history_per_shot, history_sample_count = await _cost_history_per_shot(svc, user_id, task_id)
    estimate = estimate_vimax_cost(
        n_shots=n_shots,
        video_seconds=n_shots * _SECONDS_PER_SHOT,
        n_characters=n_characters,
        tts_chars=tts_chars,
        prices=_cost_resolve_prices(payload, svc._config, voice_id=(storyboard.get("config") or {}).get("voice_id")),
        history_per_shot=history_per_shot,
        history_sample_count=history_sample_count,
    )
    estimate.spent_cny = await _cost_spent_cny(task)
    return estimate


@router.delete("/tasks/{task_id}")
async def delete_video_task(request: Request, task_id: str):
    svc = _get_service(request)
    user_id = get_effective_user_id()
    deleted = await svc.delete_task(task_id, user_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"ok": True}


@router.post("/tasks/{task_id}/cancel")
async def cancel_video_task(request: Request, task_id: str):
    """取消进行中的任务（Task 22）——保留任务行与产物，只停止继续烧配额。

    修前唯一的"停止"手段是 ``DELETE /tasks/{id}``，它连产物一起删；且 worker
    侧从不回查取消标志，删除后 ffmpeg / 供应商调用照跑到底。现在 worker 的
    ``progress_cb`` 每帧回查 status，命中 cancelled 即中断。
    """
    svc = _get_service(request)
    user_id = get_effective_user_id()
    try:
        ok = await svc.cancel_task(task_id, user_id)
    except ValueError as e:
        logger.warning("SEC-M12: cancel rejected (task=%s): %s", task_id, e)
        raise HTTPException(status_code=409, detail="Task cannot be cancelled in its current state") from e
    if not ok:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"ok": True, "status": "cancelled"}


@router.get("/tasks/{task_id}/download")
async def download_video_task(request: Request, task_id: str):
    """Download the generated video for a completed task."""
    svc = _get_service(request)
    user_id = get_effective_user_id()
    task = await svc.get_task(task_id, user_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.status != "ready":
        raise HTTPException(status_code=409, detail="Task not ready")

    result = json.loads(task.result or "{}")
    output_path = result.get("output_path", "")
    if not output_path or not Path(output_path).exists():
        # 2026-09-13：文案如实化（产物已被外部清理），码保持 404；前端按码处理
        # （全前端无该字符串精确匹配，已核）。
        raise HTTPException(status_code=404, detail="产物文件已被清理或丢失")

    filename = Path(output_path).name
    ext = Path(output_path).suffix.lower()
    media_types = {".mp4": "video/mp4", ".webm": "video/webm", ".gif": "image/gif"}
    media_type = media_types.get(ext, "video/mp4")
    return FileResponse(
        output_path,
        media_type=media_type,
        filename=filename,
    )


@router.get("/tasks/{task_id}/thumbnail")
async def get_video_thumbnail(request: Request, task_id: str):
    """Serve the thumbnail image for a task."""
    svc = _get_service(request)
    user_id = get_effective_user_id()
    task = await svc.get_task(task_id, user_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")

    result = json.loads(task.result or "{}") if hasattr(task, "result") else {}
    thumb_path = result.get("thumbnail_path", "")
    if not thumb_path or not Path(thumb_path).exists():
        raise HTTPException(status_code=404, detail="Thumbnail not found")
    return FileResponse(thumb_path, media_type="image/jpeg")


_AUDIO_MIME = {
    "mp3": "audio/mpeg",
    "wav": "audio/wav",
    "m4a": "audio/x-m4a",
    "aac": "audio/aac",
    "ogg": "audio/ogg",
    "webm": "audio/webm",
}


def _render_is_terminal(status: str) -> bool:
    """True when a render task has reached a terminal state (``ready`` / ``failed``).

    The SSE ``/tasks/{id}/events`` stream keeps emitting while a task is in flight
    and only sends the terminal ``done`` event once the status is terminal — matching
    the plan's ``("ready", "failed")`` filter, rather than the earlier
    ``not in ("running", "composing")`` which closed the stream prematurely during
    the draft / pending / sub-status phases.
    """
    return status in TERMINAL_STATUSES


@router.get("/tasks/{task_id}/audio")
async def stream_frame_audio(request: Request, task_id: str, frame: int):
    """Stream a single frame's narration audio for preview playback (Range-supported).

    Resolves ``frame.audio_path`` from ``storyboard.json``. Security:
      - Owner-only (``svc.get_task`` filters by user_id → 404 for non-owners);
      - Path-traversal guard (audio file must resolve under the task dir);
      - Audio MIME whitelist.
    """
    svc = _get_service(request)
    user_id = get_effective_user_id()
    task = await svc.get_task(task_id, user_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")

    task_dir = (Path(svc._storage_dir) / user_id / "tasks" / task_id).resolve()
    sb_path = task_dir / "storyboard.json"
    if not sb_path.exists():
        raise HTTPException(status_code=404, detail="Storyboard not found")

    try:
        data = json.loads(sb_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        raise HTTPException(status_code=503, detail="Storyboard is being updated, retry shortly")

    audio_path_str = next(
        (fr.get("audio_path") for fr in data.get("frames", []) if fr.get("index") == frame),
        None,
    )
    if not audio_path_str:
        raise HTTPException(status_code=404, detail=f"Frame {frame} audio not ready")

    audio_path = Path(audio_path_str).resolve()
    try:
        audio_path.relative_to(task_dir)
    except ValueError:
        raise HTTPException(status_code=404, detail="Frame audio not available")
    if not audio_path.exists():
        raise HTTPException(status_code=404, detail="Audio file not found")

    suffix = audio_path.suffix.lstrip(".").lower()
    if suffix not in _AUDIO_MIME:
        raise HTTPException(status_code=400, detail=f"Unsupported audio type: {suffix}")
    media_type = _AUDIO_MIME[suffix]
    file_size = audio_path.stat().st_size

    range_header = request.headers.get("range", "")
    if range_header:
        match = re.match(r"bytes=(\d+)-(\d*)", range_header)
        if not match:
            raise HTTPException(status_code=416, detail="Invalid range")
        start = int(match.group(1))
        end = int(match.group(2)) if match.group(2) else file_size - 1
        if start >= file_size or end >= file_size:
            raise HTTPException(status_code=416, detail="Range not satisfiable")

        async def _stream_range():
            with open(audio_path, "rb") as f:
                f.seek(start)
                remaining = end - start + 1
                while remaining > 0:
                    data_bytes = f.read(min(8192, remaining))
                    if not data_bytes:
                        break
                    remaining -= len(data_bytes)
                    yield data_bytes

        return StreamingResponse(
            _stream_range(),
            status_code=206,
            headers={
                "Content-Range": f"bytes {start}-{end}/{file_size}",
                "Accept-Ranges": "bytes",
                "Content-Length": str(end - start + 1),
                "Content-Type": media_type,
                # F2（R5-1）：CAS 音频同 URL 换代后浏览器恒播旧缓存——padded 派生与
                # actual-voice 归位均绕开 hash 命名，cache-buster 口径不稳，统一
                # no-cache（与 visual serve 既有口径一致）。
                "Cache-Control": "no-cache",
            },
        )

    return FileResponse(str(audio_path), media_type=media_type, headers={"Cache-Control": "no-cache"})


@router.post("/ai-visual")
async def create_ai_visual_legacy():
    """P1-13: 该端点请求模型无 script/topic，service 必然抛 'No script or topic provided'
    ——稳定必坏的死端点，明确 410 引导到 /create。"""
    raise HTTPException(
        status_code=410,
        detail="该端点已废弃，请使用 POST /api/video/create（video_type=ai_image/ai_video）",
    )


@router.post("/create", response_model=list[TaskBrief])
async def create_video(request: Request, body: CreateVideoRequest):
    svc = _get_service(request)
    user_id = get_effective_user_id()
    # remotion_course=35（T7 拍板：对齐 ai_image 档——remotion 池内成片渲染优先于
    # remotion_still(20)；ai_video=30 属 ffmpeg 池与本池无竞争）
    priority = {"ai_image": 35, "ai_video": 30, "vimax": 50, "remotion_course": 35}.get(body.video_type, 40)

    payload = body.model_dump()

    # Plan B（2026-10-19，D4）：ViMax 新建显式 preset 在后台任务启动（submit）前
    # 可读拒绝；其他 video_type 的 preset 照常受理。
    if body.video_type == "vimax" and body.voice_id:
        from app.gateway.video_gen.engines.vimax.voice_policy import ensure_vimax_voice_allowed

        try:
            ensure_vimax_voice_allowed(body.voice_id)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))

    # D16（2026-09-08 T13）：remotion 池下线时提交前快速失败——不收任务悬死。
    # 探测有 60s 缓存（worker_probe._remotion_probe_cache），刚停的容器最长
    # 60s 内仍可能放行（测试用例须知：先重启 gateway 清缓存）。
    if body.video_type == "remotion_course":
        from app.gateway.video_gen.worker_probe import ensure_remotion_pool_available

        try:
            await ensure_remotion_pool_available()
        except ValueError as e:
            raise HTTPException(status_code=503, detail=str(e))

    # 决策 1（2026-07-31）：移除 video_count 多版本 fan-out。原实现提交 N 个除
    # version_index 外完全相同的任务（无 seed 差异）→ 5 倍成本、近乎一样的成片。
    # 返回值保持 list 形状不变，避免前端与既有调用方改签名。
    task = await svc.submit(
        user_id=user_id,
        task_type=body.video_type,
        payload=payload,
        priority=priority,
    )
    tasks = [TaskBrief.model_validate(task, from_attributes=True)]

    # 条目 7（2026-09-12 缺陷批）：config 级模型名（script/subtitle/shot_table）
    # 花名册校验——撤册名留痕可见（log error + 任务 degradation），任务继续走各自
    # 既有回退链（subtitle→规则链 / shot_table→跟随 script LLM）。不做 422。
    # vimax_llm_model 不参与建单预检（复审修订 2026-09-13）：双用途字段——裸
    # API model id 经 models[] 反查命中即零回退（service._resolve_vimax_story_model），
    # 「非花名册名」≠配置错误，建单侧逐单留痕是系统性误报。可见性收拢到真实回退点
    # （service 侧 fallback 处）。2026-10-12 起缺省值 qwen/qwen3.8-max 为可反查的
    # 在册裸 id（花名册批对齐，原 qwen/qwen3.6-plus 为悬空值）。
    # getattr 防御：测试替身可能只实现 submit/get_task（无 _config/_storage_dir）。
    _roster = _model_roster()
    _cfg = getattr(svc, "_config", None)
    _task_id = getattr(task, "id", None)
    _storage_dir = getattr(svc, "_storage_dir", None)
    if _roster and _cfg is not None and isinstance(_task_id, str) and isinstance(_storage_dir, str):
        task_dir = Path(_storage_dir) / user_id / "tasks" / _task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        # 复审二轮（2026-09-13）：补齐计划①四字段——config 级 script_model 纳入
        # （请求级已走 schema 422；config 级撤册任务将在分镜生成时硬失败，建单留痕
        # 给运维同款撤册可见性）。detail 按字段如实描述实际回退路径（不得统一写
        # 「已回退默认链路」——script_model 无回退）。
        _misses: list[str] = []
        for _attr, _label, _fallback in (
            ("script_model", "分镜/脚本", "无回退，任务将在分镜生成时失败"),
            ("subtitle_llm_model", "字幕语义重切", "已回退规则链"),
            # 三轮复审（2026-09-13）：「跟随分镜 LLM」只是**未配置**时的语义
            # （resolve_shot_table_llm 返回 None）；显式配置撤册名时该函数直调
            # create_chat_model 无兜底 → ValueError → 任务 failed 不重试，与
            # script_model 同为硬失败——文案同口径。
            ("shot_table_model", "镜头调度表", "无回退，任务将在分镜生成时失败"),
        ):
            _name = (getattr(_cfg, _attr, None) or "").strip()
            if _name and _name not in _roster:
                logger.error("model roster miss: video_gen.%s=%r not in config.yaml models[]", _attr, _name)
                _misses.append(f"模型 {_name}（{_label}）未在花名册注册，{_fallback}")
        if _misses:
            # 聚合成单条：sidecar 按 kind+frame 幂等去重，逐字段各记一条会互相吞掉
            # （复审修订 2026-09-13）。冷热口径：svc._config 是 lifespan 启动快照，而
            # _model_roster() 走 get_app_config() 热更——补名进 models[] 即时消除留痕；
            # 从 video_gen 段删字段须重启 gateway 才生效，文案须言明。
            record_degradation(
                task_dir,
                kind=KIND_MODEL_ROSTER_MISS,
                detail="；".join(_misses) + "；请联系管理员检查 config.yaml models 配置（修改 video_gen 段后需重启 gateway 生效）",
            )

    return tasks


# ---------------------------------------------------------------------------
# Template API
# ---------------------------------------------------------------------------


def _visible_doubao_voice_ids() -> set[str]:
    """Doubao voice ids to expose in /voices, gated by access_key + optional whitelist.

    Usability (review 痛点1): doubao TTS requires VOLCENGINE_VOICE_ACCESS_KEY + a console-
    provisioned speaker. Without the key, all doubao voices would fail (55000000, no fallback
    per requirement #4) → don't list them. Optional DOUBAO_TTS_ENABLED_SPEAKERS (comma list)
    filters to only the provisioned speakers; unset = all doubao voices (when access_key set).
    """
    import os

    from app.gateway.video_gen.orchestration.voices import CURATED_VOICES

    if not os.environ.get("VOLCENGINE_VOICE_ACCESS_KEY"):
        return set()
    all_doubao = {v["id"] for v in CURATED_VOICES if v.get("provider") == "doubao"}
    whitelist = os.environ.get("DOUBAO_TTS_ENABLED_SPEAKERS", "").strip()
    if not whitelist:
        return all_doubao
    enabled = {s.strip() for s in whitelist.split(",") if s.strip()}
    return {vid for vid in all_doubao if vid.split(":", 1)[-1] in enabled}


@router.get("/voices")
async def list_voices_endpoint(lang: str | None = None):
    """List available TTS voices, optionally filtered by language prefix."""
    import os

    from app.gateway.video_gen.orchestration.voices import CURATED_VOICES, list_voices_cached

    doubao_ids = _visible_doubao_voice_ids()
    qihu_visible = bool(os.environ.get("QIHU_API_KEY"))

    def _visible(cv: dict) -> bool:
        provider = cv.get("provider")
        if provider == "doubao":
            return cv.get("id") in doubao_ids
        if provider == "qihu":
            return qihu_visible  # 无 key 时 360 音色必失败（链末端 edge 国内不可达）→ 不列出
        return True

    if lang:
        lang_lower = lang.lower()
        voices = await list_voices_cached(lang)
        result = [
            {
                "id": v["ShortName"],
                "name": v["FriendlyName"] if "FriendlyName" in v else v["ShortName"],
                "locale": v["Locale"],
                "gender": v["Gender"],
                "provider": "edge-tts",
            }
            for v in voices
        ]
        # Append curated cloud-TTS voices (360 preset + doubao seed-tts-2.0) matching the lang.
        result.extend(cv for cv in CURATED_VOICES if cv.get("locale", "").lower().startswith(lang_lower) and cv.get("provider") in ("qihu", "doubao") and _visible(cv))
        return result

    return [cv for cv in CURATED_VOICES if _visible(cv)]


@router.get("/voices/default")
async def get_default_voice(lang: str = "zh", task_type: str | None = None):
    """FP1-5: 返回与成品一致的解析默认音色（doubao:<speaker> 或 preset:146），
    供试听选择器「默认」选项预览——修前试听恒 edge-tts YunjianNeural，成品却是
    doubao/preset，试听满意≠成品声音。

    Plan B（2026-10-19）：可选 ``task_type=vimax`` 返回 ViMax 策略解析（env
    speaker > 白名单首项 > Edge 语言默认——zh 兜底不再是 preset:146）；缺省
    query 的 URL/响应不变。
    """
    if task_type == "vimax":
        from app.gateway.video_gen.engines.vimax.voice_policy import resolve_vimax_default_voice

        return {"voice_id": resolve_vimax_default_voice(lang)}

    from app.gateway.video_gen.orchestration.voices import resolve_default_voice

    voice_id = resolve_default_voice(lang)
    if not voice_id:
        raise HTTPException(status_code=404, detail=f"no default voice for lang '{lang}'")
    return {"voice_id": voice_id}


@router.get("/voices/{voice_id}/preview")
async def preview_voice(voice_id: str, text: str | None = None, task_type: str | None = None):
    """Generate a short preview audio for a given voice using edge-tts.

    边界收口（2026-10-19 P1-1）：可选 ``task_type=vimax``——试听走 ViMax 策略
    （缺字节 key 的 doubao 直落同性别 Edge，preset 可读拒绝），不触 360。缺省
    ``task_type`` 行为不变（其他类型 preset 试听照常）。
    """
    import hashlib
    import shutil
    import tempfile

    storage = get_paths().base_dir / "video-gen/voice_previews"  # C8：CWD 相对改基址
    storage.mkdir(parents=True, exist_ok=True)
    # SEC-M8: voice_id 是路径参数，直拼缓存键落盘构成路径注入面——复用
    # voice_twin_production 的白名单清洗（路径分隔符压成 "_"，防御不依赖
    # Starlette 路由行为；与 D3 同构）。
    from app.gateway.routers.voice_twin_production import _safe_cache_voice_id

    safe_voice_id = _safe_cache_voice_id(voice_id)
    # 边界热修复（2026-10-20 P1，PR#36 复审 Warning①）：task_type 是任意 query 串，
    # 不得原样进缓存文件名——"/" 会穿目录（缓存 move 失败 503，亦是路径注入面）、
    # 超长串 ENAMETOOLONG。白名单清洗 + 截长（>32 时截成 24+1+8 共 33 字符，尾接 md5 短哈希防同前缀碰撞）。
    _scope_raw = task_type or "default"
    _policy_scope = re.sub(r"[^A-Za-z0-9_-]", "_", _scope_raw)
    if len(_policy_scope) > 32:
        _policy_scope = f"{_policy_scope[:24]}_{hashlib.md5(_scope_raw.encode()).hexdigest()[:8]}"
    # 边界热修复（2026-10-20 P1，PR#36 复审 Warning②）：vimax preset 门上移到缓存
    # 读之前——修前门在缓存读后，旁路落盘的 vimax scope 缓存文件可绕过 422 直接
    # 返 200。gate 恒先于缓存读写，命中/写入都不再可能是拒收音色。
    if task_type == "vimax":
        # 评审修复（2026-10-19）：preset 在 vimax 策略下抛 ValueError——转可读 422
        # （修前被通用 except 吞成 503「preview unavailable」，可行动文案丢失）。
        from app.gateway.video_gen.engines.vimax.voice_policy import ensure_vimax_voice_allowed

        try:
            ensure_vimax_voice_allowed(voice_id)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
    # Include text hash in cache key so different texts get different previews.
    # 评审修复（2026-10-19）：task_type 并入缓存键——vimax 策略试听与缺省试听产物可不同
    # （缺 key 的 doubao 前者 Edge、后者 360 降级），同 voice+text 共键会让 360 音频
    # 被 vimax 请求命中，击穿 VIMAX_TTS_POLICY。
    cache_key = f"{safe_voice_id}_{_policy_scope}_{hashlib.md5(text.encode()).hexdigest()[:8]}" if text else f"{safe_voice_id}_{_policy_scope}"
    cached = storage / f"{cache_key}.mp3"
    if cached.exists():
        return FileResponse(str(cached), media_type="audio/mpeg")

    # Generate preview on first request
    from app.gateway.video_gen.orchestration import tts as tts_mod

    preview_text = text or "你好，欢迎使用 Trainer AI 视频配音。今天天气不错，让我们一起创作精彩内容吧！"
    # Truncate to 100 chars for preview performance
    preview_text = preview_text[:100]
    try:
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp_f:
            tmp_path = tmp_f.name
        _preview_kwargs = {}
        if task_type == "vimax":
            from app.gateway.video_gen.engines.vimax.voice_policy import VIMAX_TTS_POLICY

            _preview_kwargs["policy"] = VIMAX_TTS_POLICY
        await tts_mod.synthesize(preview_text, voice_id=voice_id, speed=1.0, output_path=tmp_path, **_preview_kwargs)
        shutil.move(tmp_path, str(cached))
        return FileResponse(str(cached), media_type="audio/mpeg")
    except Exception:
        Path(tmp_path).unlink(missing_ok=True)
        logger.exception("voice preview synthesis failed for %s", voice_id)
        raise HTTPException(status_code=503, detail=f"Voice '{voice_id}' preview unavailable")


@router.get("/bgm/{bgm_id}/preview")
async def preview_bgm(bgm_id: str):
    """Serve a BGM file for preview."""
    from app.gateway.video_gen.service import _resolve_bgm

    bgm_path = _resolve_bgm(bgm_id)
    if not bgm_path or not Path(bgm_path).exists():
        raise HTTPException(status_code=404, detail="BGM not found")
    return FileResponse(bgm_path, media_type="audio/mpeg")


@router.post("/bgm/upload")
async def upload_bgm(request: Request, file: UploadFile = File(...)):
    """Upload a custom BGM file for video generation."""
    import uuid

    allowed_types = {"audio/mpeg", "audio/mp3", "audio/wav", "audio/ogg", "audio/x-wav", "audio/mp4", "audio/x-m4a", "audio/aac", "audio/flac", "audio/webm"}
    if file.content_type not in allowed_types:
        raise HTTPException(status_code=400, detail=f"Unsupported audio type: {file.content_type}")

    content = await file.read()
    # SEC-M7（批次 2 项 2.6）：大小 + 音频魔数校验；扩展名取自嗅探而非
    # 用户文件名（content_type 仍先过上面的白名单）。
    from app.gateway.video_gen.upload_validation import validate_audio_bytes

    try:
        sniffed_ext = validate_audio_bytes(content)
    except ValueError as e:
        logger.warning("SEC-M12: BGM upload rejected: %s", e)
        raise HTTPException(status_code=400, detail="Invalid BGM upload (unsupported audio format or too large)") from e

    user_id = get_effective_user_id()
    storage = get_paths().base_dir / "video-gen/user_bgm" / user_id  # C8：CWD 相对改基址
    storage.mkdir(parents=True, exist_ok=True)
    bgm_id = f"custom_{uuid.uuid4().hex[:8]}"
    ext = sniffed_ext
    dest = storage / f"{bgm_id}{ext}"
    dest.write_bytes(content)

    return {"id": bgm_id, "name": file.filename or bgm_id, "preview_url": f"/api/video/bgm/{bgm_id}/preview"}


@router.get("/providers")
async def list_providers(request: Request):
    """列出可选的生成模型及可用性（Task 6，2026-08-01 反馈 8）。

    可用性 = **key 是否已配置**（各 provider 工厂均 `and config.xxx_api_key` 短路），
    不发任何上游网络请求，故可安全地在打开创建对话框时调用。
    前端只渲染 `available=True` 的项；未配 key 的（如 dashscope）自动不出现。
    （2026-08-03 Task 7：原文举例含 kling，它已从名单摘除——纯桩 provider 现在
    根本不进列表，而非「在列表里但不可用」。）

    依用户决策 6/7：响应只含模型名，**不含任何限制/警告文案**。
    """
    svc = _get_service(request)
    providers = svc._providers
    return {
        "image": providers.available_image_providers(),
        "video": providers.available_video_providers(),
    }


@router.get("/bgm")
async def list_bgm():
    """List built-in + user-uploaded BGM tracks."""
    import json

    user_id = get_effective_user_id()

    # Built-in BGM
    bgm_dir = Path(__file__).parent.parent / "video_gen" / "resources" / "bgm"
    built_in = []
    if (bgm_dir / "index.json").exists():
        try:
            tracks = json.loads((bgm_dir / "index.json").read_text(encoding="utf-8")).get("tracks", [])
            for track in tracks:
                built_in.append(
                    {
                        "id": track["id"],
                        "name": track.get("name", track["id"]),
                        "preview_url": f"/api/video/bgm/{track['id']}/preview",
                        "source": "built-in",
                        "tags": track.get("tags", []),
                        "duration": track.get("duration"),
                    }
                )
        except (json.JSONDecodeError, KeyError):
            pass

    # User-uploaded BGM
    user_dir = get_paths().base_dir / "video-gen/user_bgm" / user_id  # C8：CWD 相对改基址
    custom = []
    if user_dir.exists():
        for f in sorted(user_dir.iterdir()):
            if f.suffix in (".mp3", ".wav", ".ogg"):
                custom.append(
                    {
                        "id": f.stem,
                        "name": f.name,
                        "preview_url": f"/api/video/bgm/{f.stem}/preview",
                        "source": "custom",
                    }
                )

    return {"built_in": built_in, "custom": custom}


@router.get("/templates")
async def list_templates_endpoint():
    from app.gateway.video_gen.templates import list_templates

    templates = list_templates()
    return [
        {
            "id": t.id,
            "name": t.name,
            "desc": t.desc,
            "category": t.category,
            "tags": t.tags,
            "preview": t.preview,
            "supports_video_types": t.supports_video_types,
            "params": [p.model_dump() for p in t.params],
        }
        for t in templates
    ]


@router.get("/templates/{template_id}")
async def get_template_endpoint(template_id: str):
    from app.gateway.video_gen.templates import get_template

    meta = get_template(template_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="Template not found")
    return {
        "id": meta.id,
        "name": meta.name,
        "desc": meta.desc,
        "category": meta.category,
        "tags": meta.tags,
        "preview": meta.preview,
        "supports_video_types": meta.supports_video_types,
        "params": [p.model_dump() for p in meta.params],
    }


# ---------------------------------------------------------------------------
# Script upload (fixed mode) — extract text from docx/doc/txt/md
# ---------------------------------------------------------------------------

_ALLOWED_SCRIPT_TYPES = {
    "text/plain",
    "text/markdown",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",  # .docx
    "application/msword",  # .doc
    "application/pdf",  # M2-1
}

# P2-13: browsers often send .md/.txt as application/octet-stream. The
# downstream _extract_text_from_upload already validates extensions, so fall
# back to the filename when the content_type isn't whitelisted.
_ALLOWED_SCRIPT_EXTS = (".doc", ".docx", ".txt", ".md", ".pdf")  # M2-1: +.pdf

# Script field cap (schemas.CreateVideoRequest.script max_length). Parsed text
# above this would 422 on the subsequent create — surface a warning instead.
SCRIPT_MAX_CHARS = 20000

# E5 (Task 16): /script/extract 的字节上限。修前只有扩展名白名单 + **事后**字数
# warning，`await file.read()` 前无任何字节限制 —— 而 BGM(30MB)、素材(50MB)
# 都有。上传 2GB 文档即可把 gateway 内存打满。
# 条目 18（2026-09-12 缺陷批）：30MB → 200MB（用户按生产 64C/128G/1T 基准指示放宽，
# 大 docx 常见；单端点双路径——txt/md/pdf 与 docx with_images 分支共用本常量，
# 统一放宽；probe 峰值内存 200MB/请求在 128G 下无压力）。
# nginx 联动：catch-all /api/ 50M 会先 413 截胡，docker/nginx/nginx.conf 已加
# dedicated location `= /api/video/script/extract`（210M，让后端可读报错先生效）。
SCRIPT_UPLOAD_MAX_BYTES = 200 * 1024 * 1024


def _is_acceptable_script_upload(filename: str | None, content_type: str | None) -> bool:
    """True if content_type is whitelisted OR the filename has an allowed ext.

    Avoids rejecting valid .md/.txt files browsers report as octet-stream.
    """
    if content_type and content_type in _ALLOWED_SCRIPT_TYPES:
        return True
    if filename:
        return filename.lower().endswith(_ALLOWED_SCRIPT_EXTS)
    return False


async def _extract_script_text(file: UploadFile) -> str:
    """复用 voice_twin markitdown 提取文档文本，再剥画面/镜头标记（fixed 模式用）。

    voice_twin._extract_text_from_upload 处理扩展名/大小校验 + docx→markitdown 转换 +
    markdown 清洗；这里追加 _strip_visual_markers 防止念画面描述。
    M2-1: .pdf 走 convert_file_to_markdown（pymupdf4llm→markitdown，已 bundle）——
    voice_twin._extract_text_from_upload 的 DOC_ALLOWED_EXTS 不含 pdf，且 pdf 需
    pymupdf4llm 的 two-converter 策略（扫描页 fallback）。
    """
    import tempfile

    from app.gateway.routers.voice_twin_production import _extract_text_from_upload
    from app.gateway.video_gen.orchestration.script import _strip_visual_markers
    from deerflow.utils.file_conversion import convert_file_to_markdown

    filename = file.filename or ""
    if filename.lower().endswith(".pdf"):
        # M2-1: 存临时文件 → convert_file_to_markdown（返 .md Path）→ 读文本
        content = await file.read()
        with tempfile.NamedTemporaryFile(delete=False, suffix=Path(filename).suffix) as tmp:
            tmp.write(content)
            tmp_path = Path(tmp.name)
        md_path = None
        try:
            md_path = await convert_file_to_markdown(tmp_path)
            text = md_path.read_text(encoding="utf-8") if md_path else ""
        finally:
            tmp_path.unlink(missing_ok=True)
            if md_path:
                md_path.unlink(missing_ok=True)
        return _strip_visual_markers(text)

    text, _filename = await _extract_text_from_upload(file)
    return _strip_visual_markers(text)


@router.post("/script/extract")
async def extract_script_from_upload(file: UploadFile = File(...), request: Request = None, with_images: bool = False):  # noqa: RUF013
    """上传文档（docx/doc/txt/md）→ 提取清洗文本，供 fixed 模式填充 script。

    with_images=1（remotion_course 创建页专用，默认关 = 现状逐字节不变）：仅 docx
    走 stdlib OOXML 原位提取（文本+表格+【图片N】标记+staging 暂存+expires_at）；
    非 docx 忽略该参数（pdf 附提示——图片暂不支持自动分配）。
    """
    # P2-13: content_type 兜底扩展名（浏览器常把 .md/.txt 报成 octet-stream）。
    if not _is_acceptable_script_upload(file.filename, file.content_type):
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported script type: {file.content_type} ({file.filename})",
        )
    # E5: 先查体积再读内容——UploadFile.size 由 Starlette 在落盘/缓冲时填充；
    # 缺失时退回读后校验（仍早于文本提取，不进 markitdown 转换）。
    declared = getattr(file, "size", None)
    if not isinstance(declared, int):
        declared = None  # Starlette 未填 / 非数值（含测试替身）→ 走读后校验
    if declared is not None and declared > SCRIPT_UPLOAD_MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Script file too large (max {SCRIPT_UPLOAD_MAX_BYTES // (1024 * 1024)}MB)",
        )
    if declared is None:
        try:
            probe = await file.read(SCRIPT_UPLOAD_MAX_BYTES + 1)
        except TypeError:
            probe = b""  # 测试替身的 read() 不接受 size 参数
        if isinstance(probe, (bytes, bytearray)) and len(probe) > SCRIPT_UPLOAD_MAX_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"Script file too large (max {SCRIPT_UPLOAD_MAX_BYTES // (1024 * 1024)}MB)",
            )
        try:
            await file.seek(0)
        except TypeError:
            pass

    # T6（2026-09-10 独立管线计划）：docx + with_images → 原位提取 + staging。
    # 与 markitdown 口径差异（无脚注/文本框/页眉页脚）已写入 course_staging 文档；
    # 提取失败回退 markitdown 口径（不因新路径炸既有上传流）。
    if with_images and (file.filename or "").lower().endswith(".docx"):
        from app.gateway.video_gen.orchestration import course_staging

        try:
            content = await file.read()
            # 2026-09-29 计划阶段 3：docx 解析/落盘四处同步调用逐个包 to_thread
            # （E5 上限 200MB，zipfile+XML CPU 解析秒~十秒级；口径同 markitdown
            # >1MB 路径的既有 to_thread）。异常语义不变：extract 抛错仍走下方
            # seek(0)+markitdown 回退，quota 429 原样传播。
            extracted = await asyncio.to_thread(course_staging.extract_docx_with_images, content)
        except Exception:
            logger.warning("docx with_images extraction failed, falling back to markitdown", exc_info=True)
            # 文件流已被上方 read() 消费——回退 markitdown 前必须倒带，否则损坏
            # docx 会从"提取失败 200 回退"变成"markitdown 读到空流 500"（M4，2026-09-11 审查）。
            try:
                await file.seek(0)
            except TypeError:
                pass  # 测试替身不支持 seek——无回退可倒带时由 markitdown 路径自行报错
        else:
            svc = _get_service(request)
            user_id = get_effective_user_id()
            storage_root = Path(svc._storage_dir)
            await asyncio.to_thread(course_staging.cleanup_stale_staging, storage_root)
            # 条目 18：每用户配额预检（在存 ≥10 个或在存+本次上传 >2GB → 429 可读
            # 文案）；staging 按用户命名空间落盘（认领侧同参数按属主隔离）。
            # incoming 计入本次提取的图片字节（复审修订 2026-09-13：预检不含本次
            # 上传会把峰值放到 2GB+单件）。
            quota_msg = await asyncio.to_thread(
                course_staging.staging_quota_exceeded,
                storage_root / course_staging.STAGING_DIR_NAME,
                user_id,
                incoming_bytes=sum(len(img["bytes"]) for img in extracted["images"]),
            )
            if quota_msg:
                raise HTTPException(status_code=429, detail=quota_msg)
            extract_id = await asyncio.to_thread(course_staging.stage_extracted_images, extracted["images"], storage_root / course_staging.STAGING_DIR_NAME, user_id=user_id)
            resp: dict = {
                "text": extracted["text"],
                # 二轮审查 m16：只回 {ref,name} 元信息——前端仅消费数量（images.length），
                # base64 字节纯浪费（最坏把响应撑到几十 MB）；图片本体在 staging，
                # 认领时经任务目录流转。
                "images": [{"ref": img["ref"], "name": img["name"]} for img in extracted["images"]],
                "ignored_images": extracted["ignored_images"],
                "extract_id": extract_id,
                "expires_at": course_staging.expires_at_iso(),
            }
            if len(resp["text"]) > SCRIPT_MAX_CHARS:
                resp["warning"] = f"脚本超过 {SCRIPT_MAX_CHARS} 字上限（当前 {len(resp['text'])} 字），创建任务时将被拒绝，请精简后再提交。"
            return resp

    text = await _extract_script_text(file)
    # P2-13: 超过 script 字段上限 → 附 warning，避免后续 create 422 无提示。
    resp = {"text": text}
    if with_images and (file.filename or "").lower().endswith(".pdf"):
        resp["images"] = []
        resp["ignored_images"] = 0
        resp["warning"] = "PDF 中的图片暂不支持自动分配，可在分镜编辑器手动上传"
    if len(text) > SCRIPT_MAX_CHARS:
        # 二轮审查 m15：拼接而非覆盖——超长 pdf 不能把"图片不自动分配"提示顶掉。
        _extra = f"脚本超过 {SCRIPT_MAX_CHARS} 字上限（当前 {len(text)} 字），创建任务时将被拒绝，请精简后再提交。"
        resp["warning"] = f"{resp['warning']} {_extra}" if resp.get("warning") else _extra
    return resp


# ---------------------------------------------------------------------------
# Storyboard (draft) API
# ---------------------------------------------------------------------------


@router.get("/tasks/{task_id}/storyboard")
async def get_storyboard(request: Request, task_id: str):
    """Get storyboard data for a draft/ready/failed task.

    failed is allowed (read-only) so the user can open the storyboard editor
    to view + fix the prompt that caused the failure, then re-render —
    consistent with single-frame visual regen + /re-render which already allow
    failed. composing/running also readable when the on-disk storyboard.json
    exists (M3-3a: it is written atomically by _update_storyboard and Worker
    does not mutate it during composing, so reading is safe); only 409 when
    there is genuinely no readable storyboard yet.
    """
    svc = _get_service(request)
    user_id = get_effective_user_id()
    task = await svc.get_task(task_id, user_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")

    storage = Path(svc._storage_dir) / user_id / "tasks" / task_id / "storyboard.json"
    # M3-3a: 磁盘优先。storyboard.json 由 _update_storyboard 原子写（tmp+rename），
    # Worker composing 期不写它 → 读安全。磁盘存在就 200 返回（+status/progress），
    # 消除每帧预览 onSuccess→invalidateQueries→refetch→409 风暴。
    if storage.exists():
        storyboard = json.loads(storage.read_text(encoding="utf-8"))
    else:
        # 磁盘不存在 → 原 guard（draft→200 generating；composing/running→409；ready/failed→404）
        if task.status not in (DRAFT, "ready", "failed"):
            raise HTTPException(status_code=409, detail="Task is not in draft/ready/failed status")
        result = json.loads(task.result or "{}")
        # A3: task.result 是 draft 期 _wrap_pipeline 写的旧快照（无 visual_url）。
        storyboard = result.get("storyboard")
        if not storyboard:
            # E1: 草稿生成期 storyboard.json 尚未落盘 → 200 generating（语义：正在生成≠不存在），
            # 不返 404（前端旧把 404 当 error→2s refetch 刷屏）。渲染期仍 409（上方 guard）。
            if task.status == DRAFT:
                return JSONResponse({"status": "generating"}, status_code=200)
            raise HTTPException(status_code=404, detail="Storyboard not found")
    # 合并 task payload 的音频状态（前端轮询依赖 audio_ready/audio_error）。
    # audio_ready/audio_error 写在 task.payload（service._generate_draft_audio），
    # storyboard.json 本身不含这两个字段；不合并的话前端永远读不到 → 无限转圈。
    payload = json.loads(task.payload) if isinstance(task.payload, str) else (task.payload or {})
    if isinstance(storyboard, dict):
        # 条目 35（2026-09-12）：GET 高频轮询路径——>10min 的 running 只读出折算为
        # error（前端不再无限 pending），不落盘（写入口由 POST 预检落盘重置）。
        fold_stale_running_states(storyboard)
        storyboard["audio_ready"] = payload.get("audio_ready", False)
        if payload.get("audio_error"):
            storyboard["audio_error"] = payload["audio_error"]
        # Plan A Task 3（2026-10-18）：同轮次失败阶段透传（tts|concat|subtitle|merge）——
        # 前端可区分「配音合成失败」与「拼接/字幕/合并失败」，不再只有通用文案。
        if payload.get("audio_error_stage"):
            storyboard["audio_error_stage"] = payload["audio_error_stage"]
        # Plan B Task 4（2026-10-19）：成功降级提示——只由带 frame_index 的
        # 「实际音色已归位」voice_fallback 记录生成（attempt 记录无 frame_index，
        # 「开始降级」≠「降级成功」，不得当成功提示）；audioReady 时前端琥珀条展示。
        # 评审修复：仅 vimax 任务发出——其他类型的 storyboard 响应零变化（结构化
        # 归位字段写入不分类型，若 warning 无门则 ai_image/ai_video 语义漂移）。
        if getattr(task, "task_type", "") == "vimax":
            from app.gateway.video_gen.degradations import read_degradations

            _settled_fallback = next(
                (i for i in read_degradations(Path(svc._storage_dir) / user_id / "tasks" / task_id) if i.get("kind") == "voice_fallback" and i.get("frame_index") is not None and i.get("requested_voice") and i.get("actual_voice")),
                None,
            )
            if _settled_fallback is not None:
                storyboard["audio_degradation_warning"] = f"已使用备用音色（请求 {_settled_fallback['requested_voice']}，实际 {_settled_fallback['actual_voice']}）"
        if payload.get("total_duration") is not None:
            storyboard["total_duration"] = payload["total_duration"]
        # M3-3a: 暴露 status/progress 给前端（composing 期显"合成中 N%"）
        storyboard["status"] = task.status
        storyboard["progress"] = task.progress
    return storyboard


class UpdateFrameRequest(BaseModel):
    narration: str | None = None
    image_prompt: str | None = None
    search_term: str | None = None
    visual_source: str | None = None
    # T7（2026-09-08 remotion-course）：版式写回（19 版式 PascalCase；None=清除走确定性规则）
    layout: str | None = None
    # T7（2026-09-10 独立管线计划）：版式结构化内容写回（编辑器结构化表单保存）。
    # dict 校验后透传——深校验在渲染侧 _overlay_layout_content（类型矫正/非法丢弃）。
    # 消费方仅 remotion_course；显式 null = 清除（回确定性推导）。
    layout_content: dict | None = None


class RegenerateFrameRequest(BaseModel):
    """单帧重生模式：all=画面+音频（默认，现行为）；audio_only=只重配音（改讲解词场景，
    画面不动）；visual_only=只重画面（音频/SRT 不动）。"""

    mode: Literal["all", "audio_only", "visual_only"] = "all"


class UpdateStoryboardConfigRequest(BaseModel):
    """生成后可调的运行参数（创建期默认值的用户级修正，2026-07-27）。

    生效路径：voice_id/tts_speed → 逐帧「重生配音」或整片 re-render；
    bgm_volume/subtitle_style → 「重新合成」即生效（recompose 读 storyboard.json config）。
    结构字段（width/height/video_type/fps/transition）不可经此端点改。

    voice_id 显式 null = 「默认/跟随项目」——model_fields_set 区分「未传」与
    「显式 null」（F4/R5-3，layout/visual_source 同款口径）：null → config 与
    DB payload 同步删键，回落项目/系统默认解析链。
    """

    model_config = ConfigDict(extra="forbid")

    voice_id: str | None = None
    tts_speed: float | None = Field(None, ge=0.5, le=2.0)
    bgm_volume: float | None = Field(None, ge=0.0, le=1.0)
    subtitle_style: str | None = None


@router.put("/tasks/{task_id}/storyboard/config")
async def update_storyboard_config(
    request: Request,
    task_id: str,
    body: UpdateStoryboardConfigRequest,
):
    """更新 storyboard config 的用户可调字段（draft/failed/ready）。"""
    svc = _get_service(request)
    user_id = get_effective_user_id()
    task = await svc.get_task(task_id, user_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.status not in (DRAFT, "failed", "ready"):
        raise HTTPException(status_code=409, detail="Task is not in draft/failed/ready status")

    task_dir = Path(svc._storage_dir) / user_id / "tasks" / task_id
    # F4（R5-3，2026-09-28）：显式 null voice_id = 清空回退——「未传」与「显式 null」
    # 经 model_fields_set 区分（修前 `if v is not None` 把前端「默认/跟随项目」的
    # {"voice_id": null} 吞掉，清空永不生效）。
    _explicit_voice_null = "voice_id" in body.model_fields_set and body.voice_id is None
    edits = {k: v for k, v in body.model_dump().items() if v is not None}
    if not edits and not _explicit_voice_null:
        raise HTTPException(status_code=400, detail="No config fields provided")

    # Plan B（2026-10-19，D4）：ViMax 任务保存 preset 音色在落盘前可读拒绝（按
    # task.task_type；ai_image 等其他类型不受影响）。显式 null（清空回退）放行。
    if getattr(task, "task_type", "") == "vimax" and edits.get("voice_id"):
        from app.gateway.video_gen.engines.vimax.voice_policy import ensure_vimax_voice_allowed

        try:
            ensure_vimax_voice_allowed(edits["voice_id"])
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))

    # B3（R2，2026-09-28）+ v5 diff 门控：以**当前盘面 config 值**为基线——仅当新值
    # ≠ 当前值才双写 DB payload。前端保存是全量四键提交（BGM-only 保存也带
    # voice_id），不加门控会把跟随任务 payload.voice_id 写成解析值 → re-render 的
    # `_had_explicit_voice` 误 True → P2-15 项目改音色不再传播。显式 null 删键不受
    # 门控影响（config 现存该键即真变化）。只双写 voice_id/subtitle_style——
    # `_settings_updater` 回写只碰这两键，tts_speed/bgm_volume 读 config 不读 payload。
    _cur_cfg: dict = {}
    _sb_path = task_dir / "storyboard.json"
    if _sb_path.exists():
        _cur_cfg = json.loads(_sb_path.read_text(encoding="utf-8")).get("config") or {}
    _payload_voice_write = "voice_id" in edits and edits["voice_id"] != _cur_cfg.get("voice_id")
    _style_changed = "subtitle_style" in edits and edits["subtitle_style"] != _cur_cfg.get("subtitle_style")
    _ass_warning: list[str | None] = [None]

    def _cfg_updater(data: dict) -> None:
        cfg = data.setdefault("config", {})
        cfg.update(edits)
        if _explicit_voice_null:
            cfg.pop("voice_id", None)
        if not _style_changed:
            return
        srt_path = task_dir / "subtitle.srt"
        ass_path = task_dir / "subtitle.ass"
        if edits["subtitle_style"] == "none":
            if ass_path.exists():
                try:
                    ass_path.unlink()
                except OSError:
                    logger.warning("failed to remove stale subtitle.ass for task %s", task_id)
            return
        if not srt_path.exists():
            return  # 无 srt 可派生（draft 未合成）——渲染/recompose 路径自会生成
        try:
            from app.gateway.video_gen.subtitle_engine import srt_to_ass

            srt_to_ass(
                str(srt_path),
                out_path=str(ass_path),
                video_w=int(cfg.get("width") or 1920),
                video_h=int(cfg.get("height") or 1080),
                style=edits["subtitle_style"],
            )
        except Exception as e:  # config 已落盘，派生失败不 500
            logger.warning("subtitle ASS rebuild failed after config update", exc_info=True)
            _ass_warning[0] = f"样式已保存，但字幕样式缓存刷新失败（{type(e).__name__}），重新保存一次即可重试"

    await svc._update_storyboard(task_dir, _cfg_updater)

    # B3（R2）双写 DB payload：payload 从「创建快照」变为「用户最新意图」——re-render
    # 的 `_settings_updater` 读 payload 快照无条件回写盘面 config（修前 PUT config
    # 从不回写 payload → 旧快照覆盖用户新值，改音色被改回）。`_settings_updater`
    # 逻辑不变，读到的 payload 已是用户最新意图即自然正确。
    _payload = json.loads(task.payload) if isinstance(task.payload, str) else (task.payload or {})
    _payload_dirty = False
    if _payload_voice_write and _payload.get("voice_id") != edits["voice_id"]:
        _payload["voice_id"] = edits["voice_id"]
        _payload_dirty = True
    if _explicit_voice_null and "voice_id" in _payload:
        _payload.pop("voice_id", None)
        _payload_dirty = True
    if _style_changed and _payload.get("subtitle_style") != edits["subtitle_style"]:
        _payload["subtitle_style"] = edits["subtitle_style"]
        _payload_dirty = True
    if _payload_dirty:
        await svc.repo.update(task_id, payload=json.dumps(_payload, ensure_ascii=False))

    data = json.loads((task_dir / "storyboard.json").read_text(encoding="utf-8"))
    resp = {"updated": edits, "config": data.get("config", {})}
    if _explicit_voice_null:
        resp["voice_id_cleared"] = True
    if _ass_warning[0]:
        resp["ass_warning"] = _ass_warning[0]  # B2：ASS 派生失败降级可见
    return resp


class SubtitleCue(BaseModel):
    start: str = Field(..., description="SRT timestamp HH:MM:SS,mmm")
    end: str = Field(..., description="SRT timestamp HH:MM:SS,mmm")
    text: str = Field(..., min_length=1, max_length=200)

    @field_validator("text")
    @classmethod
    def _collapse_newlines(cls, v: str) -> str:
        """折叠换行为空格（不拒绝），保证单行 cue。

        2026-07-27 前 _wrap_subtitle 会产出双行 cue，_read_srt_cues 用 "\\n".join
        原样回传前端 → 若直接 422，旧任务「不改任何字也存不了」，且报错说「不得
        包含换行」而用户没输入换行（2026-07-31 实跑复现）。故折叠而非拒绝。

        纯换行/纯空白折叠后为空串，须在此显式抛错——min_length 在 validator
        之后不再复查，依赖它会让空文本 cue 漏网。
        """
        collapsed = re.sub(r"[\r\n]+", " ", v).strip()
        if not collapsed:
            raise ValueError("Subtitle text must not be empty or whitespace-only")
        return collapsed


class UpdateSubtitleRequest(BaseModel):
    cues: list[SubtitleCue] = Field(..., min_length=1, max_length=500)


_SRT_TS_RE = re.compile(r"^\d{2}:\d{2}:\d{2},\d{3}$")


def _srt_ts_to_seconds(ts: str) -> float | None:
    """Parse 'HH:MM:SS,mmm' to seconds; None when format or range is invalid
    (mm/ss must be < 60 — the regex alone lets '99:99:99,999' through).

    有意不校验的（评审二轮确认取舍）：hh 无上限（SRT 规范未限定，实际视频不会到
    99 小时）；相邻 cue 重叠/倒序（ffmpeg 可正常烧录重叠 cue，过严会误拒合法
    快速对白）——只保证单 cue 自洽（格式 + 范围 + start<end）。"""
    if not _SRT_TS_RE.match(ts):
        return None
    hh, mm, rest = ts.split(":")
    ss, ms = rest.split(",")
    if int(mm) >= 60 or int(ss) >= 60:
        return None
    return int(hh) * 3600 + int(mm) * 60 + int(ss) + int(ms) / 1000


def _read_srt_cues(srt_path: Path) -> list[dict]:
    cues: list[dict] = []
    if not srt_path.exists():
        return cues
    for block in srt_path.read_text(encoding="utf-8").strip().split("\n\n"):
        lines = block.strip().split("\n")
        if len(lines) < 3:
            continue
        parts = lines[1].split("-->")
        if len(parts) != 2:
            continue
        cues.append(
            {
                "seq": len(cues) + 1,
                "start": parts[0].strip(),
                "end": parts[1].strip(),
                "text": "\n".join(lines[2:]),
            }
        )
    return cues


@router.get("/tasks/{task_id}/subtitle")
async def get_subtitle(request: Request, task_id: str):
    svc = _get_service(request)
    user_id = get_effective_user_id()
    task = await svc.get_task(task_id, user_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    task_dir = Path(svc._storage_dir) / user_id / "tasks" / task_id
    locked = False
    sb_path = task_dir / "storyboard.json"
    if sb_path.exists():
        locked = bool(json.loads(sb_path.read_text(encoding="utf-8")).get("subtitle_locked", False))
    return {"cues": _read_srt_cues(task_dir / "subtitle.srt"), "locked": locked}


def _restore_subtitle_style_if_none(data: dict) -> str | None:
    """手改字幕时把 subtitle_style="none" 改回 "default"，返回被改成的值。

    2026-07-29 线上实测的死锁：创建时选「无字幕」→ subtitle_style="none" →
    build_worker_payload 永久不下发 subtitle_path → 用户之后编辑字幕、保存成功、
    UI 提示「点重新合成烧入新字幕」、subtitle_locked 还让按钮可点，
    但 worker has_sub=False，字幕永不出现。

    手改字幕这个动作本身就表达了「我要字幕」，故解除锁定。
    已有具体样式（bold/minimal/outline）的不动。
    """
    cfg = data.get("config")
    if not isinstance(cfg, dict):
        return None
    if cfg.get("subtitle_style") == "none":
        cfg["subtitle_style"] = "default"
        return "default"
    return None


def _redistribute_srt_to_frames_remotion(task, task_dir: Path, cues) -> bool:
    """T-E9（2026-09-09 feedback-round1）：remotion 成片烧词级 captions（模板不读
    subtitle.srt）——用户编辑的整片 SRT 按帧边界重分配回 frame.srt（帧本地时间戳），
    recompose 的 build_remotion_props 从 frame.srt 重建 captions → 成片字幕更新。
    帧边界与 props 同口径（intro 偏移 + round(帧数×1000/30) 累计）。"""
    import bisect

    sb_path = task_dir / "storyboard.json"
    if not sb_path.exists():
        return None
    data = json.loads(sb_path.read_text(encoding="utf-8"))
    frames = data.get("frames", [])
    if not frames:
        return None
    payload = json.loads(task.payload or "{}")
    brand = (payload.get("remotion_props") or {}).get("brand") or {}
    try:
        intro_ms = round(int((brand.get("intro") or {}).get("durationInFrames") or 0) * 1000 / 30)
    except (TypeError, ValueError):
        intro_ms = 0
    starts: list[int] = []
    t = intro_ms
    for f in frames:
        d_frames = max(1, round((f.get("duration") or 3.0) * 30))
        starts.append(t)
        t += round(d_frames * 1000 / 30)
    per_frame: list[list[tuple[int, int, str]]] = [[] for _ in frames]
    for c in cues:
        s_sec = _srt_ts_to_seconds(c.start)
        e_sec = _srt_ts_to_seconds(c.end)
        if s_sec is None or e_sec is None:
            continue
        s_ms, e_ms = int(s_sec * 1000), int(e_sec * 1000)
        idx = bisect.bisect_right(starts, s_ms) - 1
        idx = max(0, min(idx, len(frames) - 1))
        local_s = max(0, s_ms - starts[idx])
        local_e = max(local_s + 1, e_ms - starts[idx])
        per_frame[idx].append((local_s, local_e, (c.text or "").strip()))

    def _fmt(ms: int) -> str:
        h, rem = divmod(ms, 3_600_000)
        m, rem = divmod(rem, 60_000)
        sec, ms2 = divmod(rem, 1000)
        return f"{h:02d}:{m:02d}:{sec:02d},{ms2:03d}"

    def _updater(d: dict) -> None:
        fs = d.get("frames", [])
        for i, cue_list in enumerate(per_frame[: len(fs)]):
            if cue_list:
                fs[i]["srt"] = "\n\n".join(f"{n}\n{_fmt(a)} --> {_fmt(b)}\n{txt}" for n, (a, b, txt) in enumerate(cue_list, 1))
            else:
                fs[i].pop("srt", None)

    return _updater


@router.put("/tasks/{task_id}/subtitle")
async def update_subtitle(request: Request, task_id: str, body: UpdateSubtitleRequest):
    """用户手改字幕：写 task_dir/subtitle.srt + 置 subtitle_locked（旁白变更才清锁重新生成）。
    仅 ready/failed（draft 还没生成 SRT）；改完点「重新合成」烧进画面。"""
    svc = _get_service(request)
    user_id = get_effective_user_id()
    task = await svc.get_task(task_id, user_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.status not in ("ready", "failed"):
        raise HTTPException(status_code=409, detail="Task must be ready or failed to edit subtitles")
    for cue in body.cues:
        start_sec = _srt_ts_to_seconds(cue.start)
        end_sec = _srt_ts_to_seconds(cue.end)
        if start_sec is None or end_sec is None:
            raise HTTPException(status_code=422, detail=f"Invalid SRT timestamp: {cue.start} --> {cue.end}")
        if start_sec >= end_sec:
            raise HTTPException(status_code=422, detail=f"SRT cue start must be before end: {cue.start} --> {cue.end}")

    task_dir = Path(svc._storage_dir) / user_id / "tasks" / task_id
    entries = [f"{i}\n{c.start} --> {c.end}\n{c.text}" for i, c in enumerate(body.cues, 1)]
    srt_content = "\n\n".join(entries) + "\n"
    srt_path = task_dir / "subtitle.srt"
    ass_path = task_dir / "subtitle.ass"

    _restored: list[str | None] = [None]
    _ass_warning: list[str | None] = [None]

    def _rebuild_ass(style: str, w: int, h: int) -> None:
        """B2（R1b，2026-09-28）：从新落盘 srt 派生 .ass——worker「ASS 优先」，
        修前只写 srt 不重建 ass，旧 .ass 恒赢，改字幕文本/样式不生效。"""
        try:
            from app.gateway.video_gen.subtitle_engine import srt_to_ass

            srt_to_ass(str(srt_path), out_path=str(ass_path), video_w=w, video_h=h, style=style)
        except Exception as e:  # 字幕文本已落盘，派生失败不 500——提升到用户可见
            logger.warning("subtitle ASS rebuild failed after subtitle edit", exc_info=True)
            _ass_warning[0] = f"字幕已保存，但字幕样式缓存刷新失败（{type(e).__name__}），重新保存一次即可重试"

    def _lock_updater(data: dict) -> None:
        # B1 竞态收口（2026-09-28）：「写 .srt → 派生 .ass → 置锁」收进同一次
        # _update_storyboard 临界区——修前先写 srt 文件、再另起临界区置锁（两步
        # 非原子），并发 recompose 在窗口内读到「新 srt + 旧锁态」。
        tmp = srt_path.with_suffix(".srt.tmp")
        tmp.write_text(srt_content, encoding="utf-8")
        tmp.replace(srt_path)  # 原子替换，recompose 并发读不拿半截文件
        data["subtitle_locked"] = True
        # 解除「无字幕」死锁——否则 build_worker_payload 永不下发 subtitle_path，
        # 用户改了字幕也永远烧不进成片（2026-07-29 线上实测）。
        # 须在派生 ASS **之前**——none→default 的修正要参与派生样式（B2）。
        _restored[0] = _restore_subtitle_style_if_none(data)
        cfg = data.get("config") or {}
        if cfg.get("subtitle_style") == "none":
            return  # restore 之后不可能为 none；防御分支（none 闸由 build_worker_payload 兜底）
        _rebuild_ass(cfg.get("subtitle_style") or "default", int(cfg.get("width") or 1920), int(cfg.get("height") or 1080))

    await svc._update_storyboard(task_dir, _lock_updater)
    # T-E9：remotion 成片烧词级 captions（不读 subtitle.srt）——编辑结果按帧
    # 边界重分配回 frame.srt，「重新合成」后成片字幕随之更新（UI 交互与
    # ai_image 一致：编辑 → 重新合成）。
    remotion_frames_updated = False
    if getattr(task, "task_type", None) == "remotion_course":
        _updater_fn = _redistribute_srt_to_frames_remotion(task, task_dir, body.cues)
        if _updater_fn is not None:
            await svc._update_storyboard(task_dir, _updater_fn)
            remotion_frames_updated = True
    resp = {
        "locked": True,
        "cues": _read_srt_cues(srt_path),
        "subtitle_style_restored": _restored[0],
        "remotion_frames_updated": remotion_frames_updated,
    }
    if _ass_warning[0]:
        resp["ass_warning"] = _ass_warning[0]  # B2：ASS 派生失败降级可见（字幕本体已保存）
    return resp


@router.put("/tasks/{task_id}/storyboard/frames/{frame_index}")
async def update_storyboard_frame(
    request: Request,
    task_id: str,
    frame_index: int,
    body: UpdateFrameRequest,
):
    """Update a single frame's fields in the storyboard."""
    svc = _get_service(request)
    user_id = get_effective_user_id()
    task = await svc.get_task(task_id, user_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    # F6: 放开 ready——成片后改 prompt/旁白是最高频修改诉求；
    # 生效链路=改字段→该帧「重新生成」（CAS miss 会重 TTS）→「重新合成」。
    # composing/running 仍 409（渲染期禁写）。
    if task.status not in (DRAFT, "failed", "ready"):
        raise HTTPException(status_code=409, detail="Task is not in draft/failed/ready status")

    storyboard_path = Path(svc._storage_dir) / user_id / "tasks" / task_id / "storyboard.json"
    if not storyboard_path.exists():
        raise HTTPException(status_code=404, detail="Storyboard file not found")

    data = json.loads(storyboard_path.read_text(encoding="utf-8"))
    frames = data.get("frames", [])
    if frame_index < 0 or frame_index >= len(frames):
        raise HTTPException(status_code=400, detail=f"frame_index {frame_index} out of range (0-{len(frames) - 1})")

    frame = frames[frame_index]
    response_data = {"updated": {}}

    # T-E3（R17，2026-09-09 feedback-round1）：layout 显式 null = 切回自动——
    # model_fields_set 区分「未传」与「显式 null」（修前 `if val is not None`
    # 把前端 LayoutSwitcher 的 {"layout": null} 吞掉，切回自动永远不生效）。
    _explicit_layout_null = "layout" in body.model_fields_set and body.layout is None
    # T7 补遗（2026-09-11 审查 B3）：visual_source 显式 null = 取消 AI 生成——
    # 同 layout 的 model_fields_set 口径（修前 `if val is not None` 把前端「取消」
    # 按钮的 {"visual_source": null} 吞掉，取消端到端失效）。仅 remotion_course
    # 放开（其他类型维持原行为零变化）。
    _explicit_vs_null = "visual_source" in body.model_fields_set and body.visual_source is None
    # T7（2026-09-10 独立管线计划）：remotion_course visual_source 用户显式设置校验——
    # 仅 "ai_image"|"ai_video"|null，且 ai_video 仅当帧版式为 VideoClipPage（决策 9：
    # parser 永不产出，只能用户经编辑器开启）。非 remotion_course 任务不新增校验（零变化）。
    if getattr(task, "task_type", "") == "remotion_course" and body.visual_source is not None:
        if body.visual_source not in ("ai_image", "ai_video"):
            raise HTTPException(status_code=400, detail="remotion_course 视觉来源仅支持 ai_image / ai_video / null")
        if body.visual_source == "ai_video":
            _effective_layout = body.layout if body.layout is not None else frame.get("layout")
            if _effective_layout != "VideoClipPage":
                raise HTTPException(status_code=400, detail="AI 视频仅可设置在 VideoClipPage（视频剪辑页）帧上")
    edits: dict[str, object] = {}
    for key in ("narration", "image_prompt", "search_term", "visual_source", "layout"):
        val = getattr(body, key, None)
        if val is not None:
            if key == "narration" and not val.strip():
                raise HTTPException(status_code=400, detail="narration cannot be empty")
            if key == "layout" and val not in REMOTION_LAYOUTS:
                raise HTTPException(status_code=400, detail=f"未知版式 {val!r}（仅支持 19 版式）")
            frame[key] = val
            response_data["updated"][key] = val
            edits[key] = val
        elif key == "layout" and _explicit_layout_null:
            frame[key] = None
            response_data["updated"][key] = None
            edits[key] = None
        elif key == "visual_source" and _explicit_vs_null and getattr(task, "task_type", "") == "remotion_course":
            frame[key] = None
            response_data["updated"][key] = None
            edits[key] = None
    # T7（2026-09-10 独立管线计划）：layout_content 写回（dict 校验后透传；
    # 显式 null = 清除——同 layout 的 model_fields_set 口径）。
    _explicit_lc_null = "layout_content" in body.model_fields_set and body.layout_content is None
    if body.layout_content is not None or _explicit_lc_null:
        if body.layout_content is not None and not isinstance(body.layout_content, dict):
            raise HTTPException(status_code=400, detail="layout_content 必须是对象（版式结构化内容）")
        _lc_val = body.layout_content if body.layout_content else None
        frame["layout_content"] = _lc_val
        response_data["updated"]["layout_content"] = _lc_val
        edits["layout_content"] = _lc_val
    # Phase 1：手改 image_prompt -> 清 IR（内存侧与磁盘 updater 对称）
    if "image_prompt" in edits:
        frame["visual_ir"] = None

    # Persist via the unified read-merge-write writer so concurrent writes (background
    # TTS audio_path, SSE visual_url) are not clobbered by a full overwrite.
    task_dir = Path(svc._storage_dir) / user_id / "tasks" / task_id
    _frame_index = frame_index
    # M1: ready/failed 态改旁白 → 显式 pending 态（recompose/re-render 守卫据此 409）。
    # 仅 narration——image_prompt 走 re-render 路径（重跑全视觉本就会拾取新 prompt）。
    # draft 不标（首渲重 TTS 重建 audio.mp3）。
    _mark_pending = task.status in ("ready", "failed") and "narration" in edits
    # B5（R4，2026-09-28）：draft 改旁白 → 帧级音频引用 + 拼接产物即时失效——
    # 修前 draft 分支不标 pending、不清 audio_path、不重置 audio_ready，
    # 改完旁白「准备音频」仍短路、试听仍是旧音。
    _draft_audio_invalidate = task.status == DRAFT and "narration" in edits

    def _frame_edit_updater(data: dict) -> None:
        fs = data.get("frames", [])
        if 0 <= _frame_index < len(fs):
            for k, v in edits.items():
                fs[_frame_index][k] = v
            # Phase 1（2026-08-17 方言层）：手改 image_prompt -> 清 visual_ir，
            # 该帧回退 legacy 透传（P0-5：翻译器不得无视用户手改）。
            if "image_prompt" in edits:
                fs[_frame_index]["visual_ir"] = None
            if _draft_audio_invalidate:
                # 清旧代音频引用（防试听/时间戳残留）；duration 保留（v3 收窄——
                # draft 帧时长来自上次 TTS 实测，清掉分镜页时长显示塌陷）。
                for k in ("audio_path", "srt", "speech_duration"):
                    fs[_frame_index].pop(k, None)
        if "narration" in edits:
            # 清锁点①（2026-07-27 字幕编辑）：旁白变更 → 用户手改字幕失效，
            # SRT 按新旁白重新生成（与 _mark_pending 同源条件）。
            data["subtitle_locked"] = False
        if _mark_pending:
            pending = data.setdefault("pending_regen_frames", [])
            if _frame_index not in pending:
                pending.append(_frame_index)

    await svc._update_storyboard(task_dir, _frame_edit_updater)
    if _draft_audio_invalidate:
        # B5 锁序（v5）：_invalidate_audio_artifacts 内部进 _synth_lock_guard，
        # 必须在 _update_storyboard **返回之后**调用——updater 在全局 _sb_write_lock
        # 临界区内执行，持 sb 锁再进 synth 锁与既有「synth → sb」获取顺序
        # （_regenerate_frame_bg 先持 synth、其内再取 sb）构成 ABBA。
        await svc._invalidate_audio_artifacts(task_id, task_dir, user_id)
    # P1-7: 纯磁盘 mutation——刷 updated_at，防 reaper 误杀活跃编辑中的草稿
    await svc.touch_task(task_id)

    response_data["storyboard"] = data
    # content-match T8⑤：visual_source 切换补 ready 态 warning（现状只看 narration/
    # image_prompt）。**只加 warning、不标 pending**——pending_regen_frames 是
    # recompose/re-render 的 409 守卫依据，切源后 recompose（重排既有素材）仍合法；
    # recompose 不拾取新源，出新画面必须走「重新生成」，warning 指引这一点。
    if task.status == "ready" and body.visual_source is not None:
        response_data["warning"] = "已切换视觉来源：请点击该帧「重新生成」出新画面后「重新合成」（「重新合成」本身不拾取新来源）"
    elif task.status == "ready" and (body.narration is not None or body.image_prompt is not None):
        response_data["warning"] = "已修改：需点击该帧「重新生成」并「重新合成」后生效（改旁白会重算配音与时长）"
    elif body.narration is not None and frame_index < len(frames):
        response_data["warning"] = "narration 已修改，对应的画面提示词可能已过期，建议重新生成该帧"

    return response_data


class BulkVisualSourceRequest(BaseModel):
    """W2（2026-08-17）：任务级视觉源批量操作。v1 仅 disable_graphic（一键关闭图文卡）。"""

    action: str = "disable_graphic"


@router.post("/tasks/{task_id}/storyboard/visual-source/bulk")
async def bulk_visual_source(request: Request, task_id: str, body: BulkVisualSourceRequest):
    """任务级一键关闭图文卡：全部 graphic 帧 visual_source → None。

    - graphic_spec **保留**（用户切回 graphic 时卡数据还在，不必重提取）
    - 与单帧 PUT 同语义：只改盘面不出新画面；不标 pending（切源后 recompose 仍合法）
    - 状态守卫与单帧 PUT 一致（draft/failed/ready；composing/running 409）
    """
    if body.action != "disable_graphic":
        raise HTTPException(status_code=400, detail=f"unsupported action: {body.action}")
    svc = _get_service(request)
    user_id = get_effective_user_id()
    task = await svc.get_task(task_id, user_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.status not in (DRAFT, "failed", "ready"):
        raise HTTPException(status_code=409, detail="Task is not in draft/failed/ready status")
    storyboard_path = Path(svc._storage_dir) / user_id / "tasks" / task_id / "storyboard.json"
    if not storyboard_path.exists():
        raise HTTPException(status_code=404, detail="Storyboard file not found")
    task_dir = Path(svc._storage_dir) / user_id / "tasks" / task_id
    disabled = [0]

    def _bulk_updater(data: dict) -> None:
        for f in data.get("frames", []):
            if f.get("visual_source") == "graphic":
                f["visual_source"] = None  # graphic_spec 保留——切回 graphic 免重提取
                disabled[0] += 1

    await svc._update_storyboard(task_dir, _bulk_updater)
    await svc.touch_task(task_id)  # 纯磁盘 mutation——刷 updated_at 防 reaper 误杀活跃编辑
    return {"disabled": disabled[0]}


class FrameCreateRequest(BaseModel):
    narration: str = ""
    image_prompt: str = ""
    index: int | None = None  # None / out-of-range → append to end


def _frame_edit_reject_detail(task_type: str) -> str:
    """P3-3 / BUG-006（2026-09-14）：非 draft 态增删帧的 409 文案——按 task_type 分版。

    功能状态维持（已渲染仍拒，行为不变），仅改文案与出路指引：
    - remotion_course：配音已合成进 audio.mp3，增删会音画错位；分镜页「重新解析」
      是既有的分镜重建出路；
    - segments 型（ai_image/ai_video/ai_visual）：主因句一致，出路是重建任务。
    两端点（插入/删除）共用本函数，主因句保持一致。
    """
    main = "仅草稿状态可增删分镜：已渲染任务的配音已合成，增删会导致音画错位"
    if task_type == "remotion_course":
        return f"{main}。课程任务可在分镜页用「重新解析」重建分镜"
    return f"{main}。如需调整请重新创建任务"


@router.post("/tasks/{task_id}/storyboard/frames")
async def add_storyboard_frame(
    request: Request,
    task_id: str,
    body: FrameCreateRequest,
):
    """M-UX: add a storyboard frame (append, or insert at index) + reindex 0..N.

    draft only. ready/failed 拒 409——recompose 不重 TTS（复用盘面 audio.mp3），
    ready 态增删后重组会音画错位（删帧音频仍含被删帧配音 / 增帧无配音）；draft→render
    才重 TTS 重建 audio.mp3，故仅 draft 允许增删。ready 态增删需走"重建音频"路径（后续）。
    """
    svc = _get_service(request)
    user_id = get_effective_user_id()
    task = await svc.get_task(task_id, user_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.status != DRAFT:
        raise HTTPException(
            status_code=409,
            detail=_frame_edit_reject_detail(task.task_type),
        )
    await svc._add_frame(task_id, user_id, body.model_dump(exclude_unset=True), body.index)
    await svc.touch_task(task_id)  # P1-7: 纯磁盘 mutation——刷 updated_at 防 reaper 误杀
    return {"status": "added"}


@router.delete("/tasks/{task_id}/storyboard/frames/{frame_index}")
async def delete_storyboard_frame(
    request: Request,
    task_id: str,
    frame_index: int,
):
    """M-UX: delete a storyboard frame + reindex 0..N continuous. draft only (见 add)。"""
    svc = _get_service(request)
    user_id = get_effective_user_id()
    task = await svc.get_task(task_id, user_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.status != DRAFT:
        raise HTTPException(
            status_code=409,
            detail=_frame_edit_reject_detail(task.task_type),
        )
    try:
        await svc._delete_frame(task_id, user_id, frame_index)
    except ValueError as e:
        # T2（2026-09-01 拆帧计划）：越界不再 200 假成功（400）。
        # SEC-M12（批次 2 项 2.10）：detail 不再透传异常原文（帧数等细节进日志）。
        logger.warning("SEC-M12: frame delete rejected (task=%s frame=%d): %s", task_id, frame_index, e)
        raise HTTPException(status_code=400, detail="Invalid frame index") from e
    await svc.touch_task(task_id)  # P1-7: 纯磁盘 mutation——刷 updated_at 防 reaper 误杀
    return {"status": "deleted"}


@router.post("/tasks/{task_id}/storyboard/frames/{frame_index}/regenerate_prompt")
async def regenerate_frame_prompt(
    request: Request,
    task_id: str,
    frame_index: int,
):
    """Regenerate image_prompt/search_term for a single frame via LLM."""
    svc = _get_service(request)
    user_id = get_effective_user_id()
    task = await svc.get_task(task_id, user_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.status not in (DRAFT, "failed"):
        raise HTTPException(status_code=409, detail="Task is not in draft/failed status")

    storyboard_path = Path(svc._storage_dir) / user_id / "tasks" / task_id / "storyboard.json"
    if not storyboard_path.exists():
        raise HTTPException(status_code=404, detail="Storyboard file not found")

    from app.gateway.video_gen.storyboard import Storyboard

    raw_storyboard = json.loads(storyboard_path.read_text(encoding="utf-8"))
    storyboard = Storyboard.from_dict(raw_storyboard)

    if frame_index < 0 or frame_index >= len(storyboard.frames):
        raise HTTPException(status_code=400, detail=f"frame_index {frame_index} out of range")

    from app.gateway.video_gen.orchestration import storyboard_planner as planner_mod
    from app.gateway.video_gen.service import create_script_llm

    frame = storyboard.frames[frame_index]
    video_type = storyboard.config.video_type

    # P2（评审反馈）：regen 端点须传 title + 相邻旁白 context + 补质量后缀，
    # 否则手动重生的帧零上下文（最需要 title 的场景）且与 bulk 帧画风不一致。
    # R-A（评审反馈）：style 来源须镜像规划路径优先级——项目风格 > payload style。
    task_payload = json.loads(task.payload) if isinstance(task.payload, str) else (task.payload or {})
    # E6（2026-08-21）：script_model 显式化（任务级覆盖 > config；不回落 models[0]）。
    llm = create_script_llm(task_payload, svc._config)
    # C3: 统一仲裁（决策 3/5/20）——重生端点经 svc._resolve_effective_style，
    # 与 generate/draft 同语义：跟随项目=项目前缀；显式选择=完全脱离（payload 前缀，
    # 项目资产零注入）；无项目=现状。哨兵/留痕/回退开关均在 helper 内处理。
    effective_style = svc._resolve_effective_style(task_payload, user_id=user_id)
    style_sp = effective_style.style_prefix
    style_key = planner_mod._detect_style(style_sp)
    # 2026-08-19（P0 兜底链风格注入）：重生单帧与 bulk 帧同一套风格保护——system 级
    # 风格守卫（风格硬约束+实拍禁隐喻+锚定卡；锚定卡取自落盘 global_anchor，storyboard
    # 自 2026-08-19 起持久化该字段，旧任务无锚 → 空守卫段，行为同现状）。既有
    # with_style_prefix 文本硬前置保持不变（system 约束与产物前缀两层并存，同 bulk）。
    _anchor = getattr(storyboard, "global_anchor", None) or {}
    style_guard = planner_mod._style_guard_instruction(style_sp, str(_anchor.get("anchor_card", "") or ""))
    n_frames = len(storyboard.frames)
    regen_ctx: dict = {"title": storyboard.title}
    if frame_index > 0:
        regen_ctx["adjacent_narrations"] = {"prev": storyboard.frames[frame_index - 1].narration}
    if frame_index < n_frames - 1:
        regen_ctx.setdefault("adjacent_narrations", {})["next"] = storyboard.frames[frame_index + 1].narration
    # S3（2026-08-26 调度表计划）：有表帧重生默认**复用原行 + ±1 邻行**（不重算，
    # 不改邻行；编辑旁白默认保留行）——调度块进 regen_ctx 由
    # _generate_image/video_prompts 注入 user 负载首部。无表/无行帧零注入（现状）。
    _sd_block = planner_mod.shot_direction_block(getattr(storyboard, "shot_table", None), frame_index)
    if _sd_block:
        regen_ctx["shot_direction"] = _sd_block

    # Regenerate prompt for this single frame; collect all field updates to persist.
    field_updates: dict[str, object] = {}
    if video_type == "ai_image":
        prompts = await planner_mod._generate_image_prompts(llm, [frame.narration], context=regen_ctx, style_guard=style_guard)
        if prompts:
            p = prompts[0]
            # T5 三轨对齐：style 硬前置走共用措辞（与角色图 _with_style / bulk 硬约束
            # 同步；T5 评审 b：本端点只重生 image_prompt，不触碰 visual_ir——该端点
            # 已有 style 硬前置，再叠 IR style 槽覆写=双重风格词）。
            p = planner_mod.with_style_prefix(style_sp, p)
            p = planner_mod._enhance_prompt_quality(p, style_key)
            frame.image_prompt = p
            field_updates["image_prompt"] = p
    elif video_type == "ai_video":
        prompts = await planner_mod._generate_video_prompts(llm, [frame.narration], context=regen_ctx, style_guard=style_guard)
        if prompts:
            p = prompts[0]
            # T5 三轨对齐：同上（硬前置共用措辞）。
            p = planner_mod.with_style_prefix(style_sp, p)
            p = planner_mod._enhance_prompt_quality(p, style_key)
            frame.image_prompt = p
            field_updates["image_prompt"] = p
            # S3（2026-08-26 调度表计划）：有表帧 shot_size/camera_move 由调度表决定
            # （S2 口径，重生不随帧变化——见 S6 前端提示文案）；无表帧保留 R-B 语义
            # （清 None 让正文成为唯一运镜来源，与 fallback 帧对齐）。
            if _sd_block:
                _row = _sd_block["current"]
                frame.shot_size = _row.get("shot_size")
                frame.camera_move = _row.get("camera_move")
                field_updates["shot_size"] = _row.get("shot_size")
                field_updates["camera_move"] = _row.get("camera_move")
            else:
                # R-B（评审反馈）：_VIDEO_PROMPT_SYSTEM 第 2 层要求 LLM 把运镜写进正文，
                # 而原帧保留 bulk 生成的 shot_size/camera_move——compose_video_prompt 会
                # 再追加枚举词，同帧出现两条可能矛盾的运镜。清 None 让正文成为唯一运镜来源，
                # 与 fallback 帧（枚举 None）行为对齐。
                frame.shot_size = None
                frame.camera_move = None
                field_updates["shot_size"] = None
                field_updates["camera_move"] = None
    else:
        raise HTTPException(status_code=400, detail=f"Cannot regenerate prompt for video_type '{video_type}'")

    # M2：旁白被编辑后的帧重生 → 重推该帧出场声明（pending_regen_frames 是旁白编辑
    # 标记，re_render_task 同源消费；该键不进 Storyboard dataclass，须读盘面原始
    # JSON——B2 修复：修前 getattr(storyboard, ...) 恒 None，重推分支为死代码）。
    # 未编辑的帧仅重生 image_prompt——**不重推** visible_*（保留原声明，M2 卡口径）。
    # 无名册（无项目/脱离）→ 不重推。
    if storyboard and frame_index in (raw_storyboard.get("pending_regen_frames") or []):
        if "effective_style_source" not in task_payload:
            task_payload["effective_style_source"] = svc._resolve_effective_style(task_payload, user_id=user_id).source
        roster = svc._planning_entity_roster(task_payload, user_id=user_id)
        if roster.get("characters") or roster.get("props"):
            vc, vp = await planner_mod.rederive_visible_declarations(llm, title=storyboard.title, narration=frame.narration, entity_roster=roster)
            if vc is not None or vp is not None:
                frame.visible_characters = vc
                frame.visible_props = vp
                field_updates["visible_characters"] = vc
                field_updates["visible_props"] = vp

    # Persist only the regenerated fields via the unified read-merge-write writer so
    # concurrent writes (background TTS audio_path, SSE visual_url) are not clobbered
    # by a full overwrite from this stale in-memory Storyboard.
    if field_updates:
        _updates = field_updates
        _frame_index = frame_index

        def _prompt_updater(data: dict) -> None:
            for f in data.get("frames", []):
                if f.get("index") == _frame_index:
                    f.update(_updates)
                    break

        task_dir = Path(svc._storage_dir) / user_id / "tasks" / task_id
        await svc._update_storyboard(task_dir, _prompt_updater)
        # P1-7: 纯磁盘 mutation——刷 updated_at，防 reaper 误杀活跃编辑中的草稿
        await svc.touch_task(task_id)

    return storyboard.to_dict()


class RenderRequest(BaseModel):
    frame_edits: dict | None = None
    quality: Literal["draft", "standard", "high", "preview"] | None = None


class UpdateStoryRequest(BaseModel):
    story: str


@router.put("/tasks/{task_id}/story")
async def update_story(
    request: Request,
    task_id: str,
    body: UpdateStoryRequest,
):
    """Edit the expanded story for an idea2video task (story_ready status).
    Persists to story.txt (render reuses it) + result.story (UI shows it)."""
    svc = _get_service(request)
    user_id = get_effective_user_id()
    try:
        await svc.update_story(task_id, user_id=user_id, story=body.story)
    except ValueError as e:
        logger.warning("SEC-M12: story edit rejected (task=%s): %s", task_id, e)
        raise HTTPException(status_code=409, detail="Task cannot be edited in its current state")
    await svc.touch_task(task_id)  # P1-7: 刷 updated_at 防 reaper 误杀（brief 显式列出）
    return {"ok": True}


class ReparseCourseRequest(BaseModel):
    """决策 15（2026-09-10 独立管线计划）：重新解析请求体。"""

    course_parse_mode: str = "faithful"


@router.post("/tasks/{task_id}/reparse")
async def reparse_course_storyboard(request: Request, task_id: str, body: ReparseCourseRequest):
    """remotion_course 重新解析：以另一档重建分镜（决策 15）。

    - 仅 ``status == draft``（其他状态 409）；仅 remotion_course（其他类型 404）。
    - **整量覆盖**重建 storyboard（layout/layout_content/visual_source/分组全部重建；
      v1 语义 = 用户手改与已上传素材丢弃——前端二次确认弹层明示）。
    - 嵌入图映射从 ``task_dir/visuals/embedded/`` 文件名重建（不依赖 staging——
      认领后重解析图片按 ``【图片N】`` 标记重新关联不丢）。
    - 复用 service._build_course_storyboard（与 P1 直渲/P2 草稿同一方法）。
    """
    import re as _re
    from dataclasses import asdict as _asdict

    if body.course_parse_mode not in ("faithful", "rewrite"):
        raise HTTPException(status_code=400, detail="course_parse_mode 仅支持 faithful / rewrite")
    svc = _get_service(request)
    user_id = get_effective_user_id()
    task = await svc.get_task(task_id, user_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.task_type != "remotion_course":
        raise HTTPException(status_code=404, detail="重新解析仅 remotion_course（文字动效）任务支持")
    if task.status != DRAFT:
        raise HTTPException(status_code=409, detail="仅草稿（draft）状态可重新解析")

    task_dir = Path(svc._storage_dir) / user_id / "tasks" / task_id
    storyboard_path = task_dir / "storyboard.json"
    if not storyboard_path.exists():
        raise HTTPException(status_code=404, detail="Storyboard file not found")

    payload = json.loads(task.payload) if isinstance(task.payload, str) else (task.payload or {})
    script = payload.get("script") or ""
    if not script:
        raise HTTPException(status_code=400, detail="任务无脚本可重新解析")
    payload["course_parse_mode"] = body.course_parse_mode
    # B1（2026-09-11 审查）：摘除 create 认领残留的 extract_id——staging 已删，
    # 重解析图源是 task_dir/visuals/embedded/（下方 image_paths 重建传入）。
    payload.pop("script_extract_id", None)

    # 嵌入图映射从任务目录重建（img_N.<ext> → ref）
    image_paths: dict[str, str] = {}
    embedded_dir = task_dir / "visuals" / "embedded"
    if embedded_dir.is_dir():
        for f in sorted(embedded_dir.iterdir()):
            m = _re.fullmatch(r"img_(\d+)(\.\w+)?", f.name)
            if m and f.is_file():
                image_paths[f"img_{m.group(1)}"] = f"visuals/embedded/{f.name}"

    frames, groups = await svc._build_course_storyboard(script, payload, task_dir, user_id=user_id, image_paths=image_paths)

    # 竞态收窄（2026-09-11 审查）：入口查过 draft 之后是分钟级 LLM 解析——期间用户
    # 可能点「开始渲染」。整量覆盖前重查一次，已离开 draft → 409（非原子，但把竞态
    # 窗口从解析时长缩到毫秒级；前端弹层 modal 也挡掉了主路径）。
    task_now = await svc.get_task(task_id, user_id)
    if task_now is None or task_now.status != DRAFT:
        raise HTTPException(status_code=409, detail="任务状态已变化（可能已开始渲染），请刷新后查看")

    _frames_dump = [_asdict(f) for f in frames]
    _groups_dump = [_asdict(g) for g in groups]

    def _updater(data: dict) -> None:
        data["frames"] = _frames_dump
        data["scene_groups"] = _groups_dump
        data["subtitle_locked"] = False  # 分镜重建 → 旧用户字幕失效（与旁白变更同口径）

    await svc._update_storyboard(task_dir, _updater)
    # 二轮审查 m6（2026-09-11）：整量覆盖重建 → 本管线的**解析类**旧留痕一并清除
    # （append-only 语义下 reparse 成功后仍展示旧「已回落/顺延/溢出」警告会误导）。
    # 跨阶段留痕（BGM/对齐/审核等）不动。
    from app.gateway.video_gen.degradations import DEGRADATION_SIDECAR_NAME
    from app.gateway.video_gen.orchestration.course_script import (
        KIND_COURSE_PARSE_FALLBACK,
        KIND_EMBEDDED_IMAGE_DEFERRED,
        KIND_EMBEDDED_IMAGES_EXPIRED,
        KIND_LAYOUT_OVERFLOW_RISK,
    )
    from app.gateway.video_gen.orchestration.course_staging import KIND_EMBEDDED_IMAGES_CLAIM_FAILED

    _course_kinds = {KIND_COURSE_PARSE_FALLBACK, KIND_LAYOUT_OVERFLOW_RISK, KIND_EMBEDDED_IMAGE_DEFERRED, KIND_EMBEDDED_IMAGES_EXPIRED, KIND_EMBEDDED_IMAGES_CLAIM_FAILED}
    _sidecar = task_dir / DEGRADATION_SIDECAR_NAME
    if _sidecar.exists():
        try:
            _items = json.loads(_sidecar.read_text(encoding="utf-8")).get("items", [])
            _kept = [i for i in _items if not (isinstance(i, dict) and i.get("kind") in _course_kinds)]
            _sidecar.write_text(json.dumps({"items": _kept}, ensure_ascii=False), encoding="utf-8")
        except Exception:
            logger.warning("reparse: clear course degradations failed", exc_info=True)
    await svc.repo.update(task_id, payload=json.dumps(payload, ensure_ascii=False))
    await svc.touch_task(task_id)

    return {
        "ok": True,
        "course_parse_mode": body.course_parse_mode,
        "n_frames": len(frames),
        "n_groups": len(groups),
        "n_embedded_images": len(image_paths),
    }


@router.post("/tasks/{task_id}/render")
async def render_from_draft(
    request: Request,
    task_id: str,
    body: RenderRequest,
):
    """Start phase 2 rendering from a draft task."""
    svc = _get_service(request)
    user_id = get_effective_user_id()

    try:
        await svc.resume_draft(task_id, user_id, frame_edits=body.frame_edits, quality_override=body.quality)
    except ValueError as e:
        # P1/BUG-008（2026-09-14）：detail 透传用户向中文文案（resume_draft 的
        # ValueError 契约同 recompose_task——用户向、无内部信息），不再吞成英文 generic。
        logger.warning("SEC-M12: render from draft rejected (task=%s): %s", task_id, e)
        raise HTTPException(status_code=409, detail=str(e))

    return {"ok": True}


# ---------------------------------------------------------------------------
# B0: Batch recompose — re-synthesize video from current disk state
# ---------------------------------------------------------------------------

# remotion_course（2026-09-08 T12）：recompose 走 _run_pipeline 的 remotion 分支
# （从盘面 storyboard.json 重建 props——qc_repair 重渲/降级交付同入口）
_RECOMPOSE_ALLOWED_TYPES = frozenset({"ai_image", "ai_video", "ai_visual", "remotion_course"})


@router.post("/tasks/{task_id}/recompose")
async def recompose_task(
    request: Request,
    task_id: str,
):
    """B0: Re-synthesize the video from current disk state (no re-fetch/re-TTS).

    Only segments-type engines (ai_image/ai_video/ai_visual/remotion_course).
    vimax/remotion return 422 (R8: they'd silently no-op via PipelineCompleted else branch).

    409 detail 契约（P1/BUG-008，2026-09-14）：
    - ValueError → 409，detail=str(e) 原文透传（对齐 re-render 端点）——service 层
      的 raise 文案必须是用户向中文（缺音频/旁白未重生/帧号列表/分镜数据损坏），
      无内部路径/堆栈/原始异常文本；
    - 非 ValueError 异常天然走 500 generic——本路由不得捕获转 409。
    """
    svc = _get_service(request)
    user_id = get_effective_user_id()
    task = await svc.get_task(task_id, user_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.task_type not in _RECOMPOSE_ALLOWED_TYPES:
        raise HTTPException(status_code=422, detail=f"Recompose not supported for {task.task_type} (segments-type only)")
    if task.status not in ("ready", "failed"):
        # P1/BUG-008 补丁：与 service 层 ValueError 契约同口径（用户向中文）
        raise HTTPException(status_code=409, detail=f"仅已完成或失败的任务可重新合成（当前状态：{task.status}）")

    # H4 互斥②：有帧正在重新生成（后台改写 audio.mp3/storyboard）→ 拒绝合成，
    # 防 recompose payload 读到半更新状态。
    if any(k[0] == task_id for k in _regen_inflight):
        raise HTTPException(status_code=409, detail="有帧正在重新生成，请等待完成后再重新合成")

    try:
        result = await svc.recompose_task(task_id, user_id)
    except ValueError as e:
        # P1/BUG-008：detail 透传用户向中文文案（缺音频/帧号列表等），前端帧高亮
        # （parseUnavailableFrames）与具体原因展示依赖它——不再吞成英文 generic。
        logger.warning("SEC-M12: recompose rejected (task=%s): %s", task_id, e)
        raise HTTPException(status_code=409, detail=str(e))

    return result


# ---------------------------------------------------------------------------
# Task 20c: 项目已更新 → 重新生成（reuse_blueprint 复用分镜重跑视觉 + force 刷新项目 refs）
# ---------------------------------------------------------------------------


@router.post("/tasks/{task_id}/re-render")
async def re_render_task(
    request: Request,
    task_id: str,
):
    """Task 20c: 重新生成视觉让项目最新角色/风格生效。

    与 /recompose 的区别：recompose 复用盘面视觉（不拾取新项目）；
    re-render 复用 storyboard 旁白/分镜但**重新生图**，force 刷新项目 character_sheets。
    仅 segments-type（ai_image/ai_video/ai_visual），ready/failed 状态。
    """
    svc = _get_service(request)
    user_id = get_effective_user_id()
    task = await svc.get_task(task_id, user_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")

    # H4 互斥②（与 recompose 同款）：有帧正在重新生成（后台改写 storyboard/audio.mp3）→
    # 拒绝 re-render，防重跑视觉拾取半更新分镜。
    if any(k[0] == task_id for k in _regen_inflight):
        raise HTTPException(status_code=409, detail="有帧正在重新生成，请等待完成后再重新生成画面")

    try:
        result = await svc.re_render_task(task_id, user_id)
    except ValueError as e:
        # 状态/类型不符 → 409/422 区分（re_render_task 统一 raise ValueError）
        msg = str(e)
        status = 422 if ("segments" in msg or "not supported" in msg) else 409
        raise HTTPException(status_code=status, detail=msg)

    return result


# ---------------------------------------------------------------------------


@router.post("/tasks/{task_id}/frames/{frame_index}/preview", status_code=202)
async def preview_frame_visual(request: Request, task_id: str, frame_index: int):
    """生成单帧预览图（draft 阶段）——异步：202 立即返回，后台生成写 storyboard.json。

    设计约束（2026-07-21 预览 504/浏览器连接耗尽修复）：本端点绝不同步等待生图
    （Ark seedream 单帧可达 240s×重试，同步等待会超 nginx 300s → 504，且每个挂起
    POST 占 1 个浏览器连接，6 连接耗尽后全站"卡死"）。前端靠 storyboard 轮询拿
    visual_url / preview_error。校验（404/409/400）仍同步，保证参数错误即时反馈。
    """
    svc = _get_service(request)
    user_id = get_effective_user_id()
    task = await svc.get_task(task_id, user_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    # 放开：draft 编辑预览 + ready 单帧换图 + failed 任务续生预览（磁盘 storyboard 保留，
    # reaper 杀任务不删盘 → failed 后仍可继续生预览图/补帧）。composing/running 仍 409。
    if task.status not in (DRAFT, "ready", "failed"):
        raise HTTPException(status_code=409, detail="Preview only available for draft, ready, or failed tasks")
    task_dir = Path(svc._storage_dir) / user_id / "tasks" / task_id
    try:
        validate_frame_previewable(task_dir, frame_index)
        # 上传帧守卫同样要在这里同步跑：_generate_frame_preview 里的那道是
        # 纵深防御，但它在后台任务里，拒绝会被写成 preview_error（红色失败态），
        # 而这不是失败——是「按设计保护了你上传的图」。同步 400 才说得清。
        assert_frame_previewable_not_upload(task_dir, frame_index)
    except ValueError as e:
        logger.info("SEC-M12: frame preview rejected (task=%s frame=%d): %s", task_id, frame_index, e)
        raise HTTPException(status_code=400, detail="Frame is not previewable")
    # 条目 35（2026-09-12）：写入口失效重置——崩溃滞留 >10min 的 running 落盘置
    # error + 可重试文案（GET 轮询只折算不落盘；无时间戳旧数据不触发）。
    await svc._expire_stale_running_state(task_id, frame_index, user_id, state_key="preview_state", error_key="preview_error", message=STALE_PREVIEW_RESET_MESSAGE)
    key = (task_id, frame_index)
    if key in _preview_inflight:
        return {"status": "already_running"}
    _preview_inflight[key] = asyncio.create_task(_preview_frame_bg(svc, task_id, frame_index, user_id))
    return {"status": "started"}


async def _preview_frame_bg(svc, task_id: str, frame_index: int, user_id: str) -> None:
    """后台单帧预览：成功由 _generate_frame_preview 写 visual_url；失败写 preview_error；
    无论如何清理在途登记。"""
    # 2026-09-01 计费修复：端点直建的后台任务不经 _wrap_pipeline（唯一 ctx 设置点），
    # 付费生图成无主行（task_id IS NULL）。在此补记账上下文——create_task 的
    # context 副本私有，task 结束自动隔离，无需 reset。
    set_llm_accounting_context(task_id, user_id, domain="video_gen")
    try:
        # preview_state 状态机（2026-09-07）：开始即写 running（并清旧 preview_error）——
        # 前端不再用「visual_url 存在」推断结案（重生已有图的帧会点击瞬间误结案）。
        # 批量路径（_preview_all_bg 直调 _generate_frame_preview）不写：批量结案走
        # unresolved 口径不读 preview_state，也免 12 并发每帧多一次整文件写。
        await svc._set_preview_state(task_id, frame_index, user_id, "running")
        await svc._generate_frame_preview(task_id, frame_index, user_id)
    except Exception as e:
        logger.warning("frame preview failed (task %s frame %d): %s", task_id, frame_index, e)
        try:
            await svc._write_preview_error(task_id, frame_index, user_id, e)
        except Exception:
            logger.warning("write preview_error failed (task %s frame %d)", task_id, frame_index, exc_info=True)
    finally:
        _preview_inflight.pop((task_id, frame_index), None)


@router.post("/tasks/{task_id}/preview-all")
async def preview_all_frames(request: Request, task_id: str):
    """F2: 批量生成所有未预览帧的预览图（自动批量，不要求用户逐帧点击）。

    读 storyboard.json 找无 visual_url 的帧，后台并发跑 _generate_frame_preview
    （_preview_sem=12 限并发，config preview_all_concurrency 可调），立即返回
    started，前端轮询 storyboard 看结果。幂等：同任务已有循环在跑 → already_running
    （防自动触发+手动连点叠加多循环抢槽）。
    """
    svc = _get_service(request)
    user_id = get_effective_user_id()
    task = await svc.get_task(task_id, user_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.status not in (DRAFT, "ready", "failed"):
        raise HTTPException(status_code=409, detail="Preview only available for draft, ready, or failed tasks")
    if task_id in _preview_all_running:
        return {"status": "already_running", "count": 0}
    task_dir = Path(svc._storage_dir) / user_id / "tasks" / task_id
    sb_path = task_dir / "storyboard.json"
    if not sb_path.exists():
        raise HTTPException(status_code=404, detail="Storyboard not found")
    storyboard = Storyboard.from_dict(json.loads(sb_path.read_text(encoding="utf-8")))
    # 2026-07-29：排除用户上传帧——它们 visual_url=None，恰好命中原条件
    # `not f.visual_url` 而被 AI 图覆盖（渲染路径早有同类守卫）
    pending = [f.index for f in storyboard.frames if not f.visual_url and not frame_has_user_visual(f)]
    if not pending:
        return {"status": "already_done", "count": 0}
    _preview_all_running[task_id] = asyncio.create_task(_preview_all_bg(svc, task_id, pending, user_id))
    return {"status": "started", "count": len(pending)}


def missing_prompt_message(frame) -> str | None:
    """preview-all 前置判定：无画面描述的帧给明确状态，避免每轮反复尝试失败。

    frame 兼容两种形态：storyboard.json 原始 dict（_preview_all_bg 循环实际传入）
    与 StoryboardFrame/SimpleNamespace（属性访问，便于单测）。
    """
    prompt = frame.get("image_prompt") if isinstance(frame, dict) else frame.image_prompt
    if (prompt or "").strip():
        return None
    return "缺少画面描述（image_prompt 为空）：请编辑画面描述后生成预览图"


async def _preview_all_bg(svc, task_id: str, frame_indices: list[int], user_id: str) -> None:
    """后台批量预览——**并发**逐帧生成（2026-08-31 并发化：每帧 create_task 同时派发，
    并发闸为 config ``preview_all_concurrency``（默认 12）——E2 串行 for + 实际单路
    是 90 帧 ≈90 分钟的根因，360 服务端 12 路实测零排队后按拍板放开）。

    2026-09-01 快照（审查 #3）：派发前**一次**读取 storyboard.json，帧级跳过检查
    （visual_url/上传帧/空 prompt）基于快照——修前每帧任务全量重读 + parse（82 帧
    瞬间 ~164 次读盘），而全帧检查几乎同刻发生，快照语义等价。快照略旧的防线：
    ``_preview_inflight`` 占位防重 + ``_generate_frame_preview`` 内部上传守卫兜底
    （占位语义不动）。快照读取失败（文件被删/损坏的并发窗口——端点调度前已做
    存在性校验）→ warning 留痕 + 清理登记 + 中止，不派发任何帧。单帧失败写
    preview_error 后继续，不阻塞其余帧（gather(return_exceptions)）。结束时清理
    _preview_all_running 登记。storyboard.json 写入全部经 svc._update_storyboard
    （自带锁 + 原子写），本函数不另加锁。
    """
    # 2026-09-01 计费修复：同 _preview_frame_bg——端点直建后台任务补记账上下文，
    # 批量生图不再是无主行。
    set_llm_accounting_context(task_id, user_id, domain="video_gen")
    task_dir = Path(svc._storage_dir) / user_id / "tasks" / task_id
    sb_path = task_dir / "storyboard.json"

    try:
        if not sb_path.exists():
            logger.warning("preview-all aborted (task %s): storyboard.json missing before dispatch", task_id)
            return
        try:
            data = json.loads(sb_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("preview-all aborted (task %s): storyboard.json unreadable (%s)", task_id, e)
            return
        frames_by_index = {f.get("index"): f for f in data.get("frames", []) if isinstance(f, dict)}

        async def _one(idx: int) -> None:
            key = (task_id, idx)
            if key in _preview_inflight:
                return
            frame = frames_by_index.get(idx)
            if frame is None or frame.get("visual_url") or frame_has_user_visual(frame):
                return
            # P0：空 image_prompt 帧给明确跳过态（写 preview_error），不再每轮
            # 反复尝试 _generate_frame_preview 然后失败刷日志（新增帧默认空 prompt）。
            _missing = missing_prompt_message(frame)
            if _missing is not None:
                logger.info("preview-all frame %d skipped: no image_prompt (task %s)", idx, task_id)
                try:
                    await svc._write_preview_error(task_id, idx, user_id, RuntimeError(_missing))
                except Exception:
                    pass
                return
            _preview_inflight[key] = asyncio.current_task()  # type: ignore[dict-item]
            try:
                await svc._generate_frame_preview(task_id, idx, user_id)
            except Exception as e:
                logger.warning("preview-all frame %d failed (task %s): %s", idx, task_id, e)
                try:
                    await svc._write_preview_error(task_id, idx, user_id, e)
                except Exception:
                    pass
            finally:
                _preview_inflight.pop(key, None)

        if frame_indices:
            # 失败隔离：_one 自吞单帧异常，return_exceptions 再兜一层（绝不取消其余帧）
            await asyncio.gather(*[asyncio.create_task(_one(idx)) for idx in frame_indices], return_exceptions=True)
    finally:
        _preview_all_running.pop(task_id, None)


# ---------------------------------------------------------------------------
# T7 静帧预览（remotion_course）：202+轮询范式（对齐 :1672 单帧预览端点）
# ---------------------------------------------------------------------------

# T-E4（R18）：键升 cache_key 级 (task_id, frame_index, cache_key)——修前同帧换版式
# 期间恒 already_running 且不自愈（旧渲染产出旧 key 文件，新 key 永不出现 → 30s 超时）。
_still_inflight: dict[tuple[str, int, str], str] = {}


def _still_cache_path(svc, user_id: str, task_id: str, cache_key: str) -> Path:
    return Path(svc._storage_dir) / user_id / "tasks" / task_id / "still_cache" / f"{cache_key}.png"


@router.post("/tasks/{task_id}/frames/{frame_index}/still-preview", status_code=202)
async def preview_frame_still(request: Request, task_id: str, frame_index: int, bulk: bool = False):
    """remotion_course 单帧静帧预览（D8 默认开启）。

    流程：缓存命中秒回（D9：key = layout + brand 关键字段 + 帧内容 hash）；
    否则插一条 remotion_still 任务，remotion-worker
    still 槽渲染 PNG 落 task_dir/still_cache/。前端轮询 GET 拿图；30s 超时
    由前端降级 M0 版式示意图（D15，不阻塞操作）。

    ``bulk``（T-E9 v4.2）：帧条批量缩略图传 true → priority=10（低于交互预览
    20 / 成片 35——10 帧任务全量触发时批量不堵用户主动预览与渲染）；交互
    预览不传（默认 false，保持 20）。
    """
    svc = _get_service(request)
    user_id = get_effective_user_id()
    task = await svc.get_task(task_id, user_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.task_type != "remotion_course":
        raise HTTPException(status_code=422, detail="静帧预览仅支持课程视频（remotion_course）")
    if task.status not in (DRAFT, "ready", "failed"):
        raise HTTPException(status_code=409, detail="Preview only available for draft, ready, or failed tasks")

    task_dir = Path(svc._storage_dir) / user_id / "tasks" / task_id
    sb_path = task_dir / "storyboard.json"
    if not sb_path.exists():
        raise HTTPException(status_code=404, detail="Storyboard file not found")
    storyboard = Storyboard.from_dict(json.loads(sb_path.read_text(encoding="utf-8")))
    if frame_index < 0 or frame_index >= len(storyboard.frames):
        raise HTTPException(status_code=400, detail=f"frame_index {frame_index} out of range")

    payload = json.loads(task.payload or "{}")
    from app.gateway.video_gen.pipelines.remotion_course_props import build_still_props

    props, cache_key = build_still_props(
        storyboard,
        frame_index,
        task_dir=task_dir,
        user_id=user_id,
        course_project_id=payload.get("course_project_id"),
        storage_dir=svc._storage_dir,
    )
    cache_file = _still_cache_path(svc, user_id, task_id, cache_key)
    if cache_file.exists() and cache_file.stat().st_size > 0:
        # review #3：缓存命中 = 该键渲染已完成，滞留的 in-flight 条目顺手清理
        # （修前成功路径永不 pop，进程生命周期内条目只增不减）
        _still_inflight.pop((task_id, frame_index, cache_key), None)
        return {"status": "cached", "cache_key": cache_key}

    key = (task_id, frame_index, cache_key)
    if key in _still_inflight:
        existing = _still_inflight.get(key)
        if existing:
            row = await svc.repo.get(existing, user_id)
            if row is not None and row.status == "composing":
                return {"status": "already_running", "cache_key": cache_key}
        _still_inflight.pop(key, None)

    still_task_id = f"st_{uuid.uuid4().hex[:12]}"
    still_payload = {
        "task_kind": "still",
        "pipeline_version": "remotion_course",
        "source_task_id": task_id,
        # 素材/缓存归属源任务目录（worker 侧按此解析产物与 symlink 根）
        "source_task_dir": str(task_dir),
        "remotion_props": props,
        "still_output": f"still_cache/{cache_key}.png",
        "still_frame": 30,
    }
    await svc.repo.create(
        id=still_task_id,
        user_id=user_id,
        domain="video_gen",
        task_type="remotion_still",
        payload=json.dumps(still_payload, ensure_ascii=False),
        # T7 拍板：低于 remotion_course(35)，成片渲染优先认领；
        # T-E9 v4.2：bulk=true（帧条批量缩略图）再降到 10——不堵交互预览(20)/成片(35)
        priority=10 if bulk else 20,
        title=f"[still] {task_id}#{frame_index}",
    )
    # repo.create 落默认 pending——worker 只认领 composing（E2E 抓到：修前任务
    # 永远无人认领，GET 轮询恒 failed）；process_task 还要求任务目录存在
    # （E2E 抓到第二处：Task directory not found 直接 failed）
    (Path(svc._storage_dir) / user_id / "tasks" / still_task_id).mkdir(parents=True, exist_ok=True)
    await svc.repo.update(still_task_id, status="composing", progress=5)
    _still_inflight[key] = still_task_id
    logger.info("SEC/T7: still preview task %s queued for %s#%d (key=%s)", still_task_id, task_id, frame_index, cache_key)
    return {"status": "started", "cache_key": cache_key}


@router.get("/tasks/{task_id}/frames/{frame_index}/still-preview")
async def get_frame_still(request: Request, task_id: str, frame_index: int, cache_key: str):
    """静帧预览轮询：文件已落盘 → PNG；still 任务进行中 → running；否则 failed/missing。"""
    svc = _get_service(request)
    user_id = get_effective_user_id()
    task = await svc.get_task(task_id, user_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    if not re.fullmatch(r"[0-9a-f]{16}", cache_key or ""):
        raise HTTPException(status_code=400, detail="invalid cache_key")
    cache_file = _still_cache_path(svc, user_id, task_id, cache_key)
    if cache_file.exists() and cache_file.stat().st_size > 0:
        # review #3：缓存命中（前端拿到图即停轮询）= 条目使命终结，顺手清理
        _still_inflight.pop((task_id, frame_index, cache_key), None)
        return FileResponse(str(cache_file), media_type="image/png")
    key = (task_id, frame_index, cache_key)
    still_id = _still_inflight.get(key)
    if still_id:
        row = await svc.repo.get(still_id, user_id)
        if row is not None and row.status == "composing":
            return {"status": "running"}
        _still_inflight.pop(key, None)
    return {"status": "failed"}


@router.post("/tasks/{task_id}/frames/{frame_index}/regenerate", status_code=202)
async def regenerate_frame(
    request: Request,
    task_id: str,
    frame_index: int,
    body: RegenerateFrameRequest | None = None,
):
    """重新生成单帧视觉——异步：202 立即返回，后台生成写 storyboard.json（regen_state/regen_error）。

    与 /preview 同一异步化理由（2026-07-21 504/连接耗尽修复）：生图最坏 240s×重试 + TTS，
    同步等待可超 nginx 300s → 504，且 504 时后端实际已成功 → toast 与真实状态矛盾。
    校验（404/409/422/400）仍同步，保证参数错误即时反馈；生成失败写 regen_state=error。
    mode（2026-07-27）：all（默认，画面+音频）/ audio_only（只重配音）/ visual_only（只重画面）。
    """
    svc = _get_service(request)
    user_id = get_effective_user_id()
    mode = body.mode if body else "all"
    task = await svc.get_task(task_id, user_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    # 放开：failed 任务也可重生单帧（黑帧修复路径），仅 ready/failed 可（composing/pending 仍 409）
    if task.status not in ("ready", "failed"):
        raise HTTPException(status_code=409, detail="Task must be ready or failed to regenerate a frame")
    # M7: 类型门（segments-type only）——vimax 走 /vimax redo、remotion 无逐镜重做；
    # 修复前 vimax 会走到 service 抛误导性 "engine_arch misconfiguration"。
    if task.task_type not in _RECOMPOSE_ALLOWED_TYPES:
        raise HTTPException(status_code=422, detail=f"Frame regenerate not supported for {task.task_type} (segments-type only)")

    task_dir = Path(svc._storage_dir) / user_id / "tasks" / task_id
    sb_path = task_dir / "storyboard.json"
    if not sb_path.exists():
        raise HTTPException(status_code=404, detail="Storyboard not found for this task")
    # 同步边界校验（即时反馈，不进后台）
    try:
        _sb = json.loads(sb_path.read_text(encoding="utf-8"))
        n_frames = len(_sb.get("frames", []))
    except (OSError, json.JSONDecodeError):
        n_frames = 0
    if frame_index < 0 or frame_index >= n_frames:
        raise HTTPException(status_code=400, detail=f"frame_index {frame_index} out of range (0-{n_frames - 1})")

    # 条目 3（2026-09-12 缺陷批）：残留旧图帧「重新生成视频」同步预检 → 400（判定
    # 矩阵与 service 层守卫同谓词 residual_visual_regen_block_reason、同文案——
    # service 层 ValueError 只能落 regen_state=error，400 只能从这里出）。audio_only
    # 不触画面不拦。
    if mode != "audio_only":
        from app.gateway.video_gen.visual_sources.remotion_course_visuals import residual_visual_regen_block_reason

        _block = residual_visual_regen_block_reason(getattr(task, "task_type", ""), _sb["frames"][frame_index])
        if _block:
            raise HTTPException(status_code=400, detail=_block)

    # H4 互斥①：任务级 pipeline（recompose/re-render/render）在跑 → 拒绝
    # （regenerate 会重写 audio.mp3/subtitle.srt，worker 可能正在读）。
    existing = svc._active_tasks.get(task_id)
    if existing is not None and not existing.done():
        raise HTTPException(status_code=409, detail="任务正在合成中，请等待完成后再重新生成帧")

    # 条目 35（2026-09-12）：写入口失效重置（regen_state 同 preview 机制）。
    await svc._expire_stale_running_state(task_id, frame_index, user_id, state_key="regen_state", error_key="regen_error", message=STALE_REGEN_RESET_MESSAGE)

    key = (task_id, frame_index)
    if key in _regen_inflight:
        return {"status": "already_running"}
    _regen_inflight[key] = asyncio.create_task(_regenerate_frame_bg(svc, task_id, frame_index, user_id, mode))
    return {"status": "started", "mode": mode}


async def _regenerate_frame_bg(svc, task_id: str, frame_index: int, user_id: str, mode: str = "all") -> None:
    """后台单帧重生：成功 regen_state=done + 标 dirty；失败 regen_state=error + regen_error；
    无论如何清理在途登记。

    H4 核心：整段持该任务的合成锁（与批量 TTS 路径 :1517 同一把）——
    两帧并发重生时各自持旧快照并发 _concat_audio 直接写同一 audio.mp3（非原子）+
    重写 subtitle.srt → 后完成者把另一帧旧代音频写入，reconcile 探测无法发现。

    ⚠️ 必须用 `_synth_lock_guard`（而非裸 `_synth_lock_for`）：被调的
    `_regenerate_frame` 音频段也需要这把锁，guard 用 ContextVar 登记持有者，
    使同协程重入复用而非自死锁（2026-08-02「重生配音一直转圈」根因）。
    换回裸锁会立刻复发死锁。
    """
    from app.gateway.video_gen.service import _synth_lock_guard

    # 2026-09-01 计费修复：同 _preview_frame_bg——端点直建后台任务补记账上下文，
    # 重生帧的生图/生视频/TTS 不再是无主行。
    set_llm_accounting_context(task_id, user_id, domain="video_gen")
    task_dir = Path(svc._storage_dir) / user_id / "tasks" / task_id
    try:
        await svc._set_regen_state(task_dir, frame_index, "running")
        # final-review finding 1: 记录起始时刻旁白。_regenerate_frame 在锁内起始读快照，
        # TTS 用快照旧文本——若 regen 途中用户 PUT 改了旁白，本次产出的音频属上一代。
        # 读盘失败/帧缺失退化为 None：保持现状无条件清除，不引入新阻塞。
        narration_at_start = None
        try:
            _sb = json.loads((task_dir / "storyboard.json").read_text(encoding="utf-8"))
            _frame = next((f for f in _sb.get("frames", []) if f.get("index") == frame_index), None)
            if _frame is not None:
                narration_at_start = _frame.get("narration")
        except (OSError, json.JSONDecodeError):
            narration_at_start = None
        async with _synth_lock_guard(str(task_dir)):
            await svc._regenerate_frame(task_id, user_id, frame_index, task_dir, mode=mode)
        await svc.repo.add_dirty_frame(task_id, frame_index)

        # M1: 清 pending_regen_frames（recompose/re-render 守卫随之放行）
        def _clear_pending(data: dict) -> None:
            pend = data.get("pending_regen_frames")
            if not (isinstance(pend, list) and frame_index in pend):
                return
            # final-review finding 1: regen 途中用户又改了旁白（PUT 落盘新文本）——
            # 本次 regen 用的是起始快照旧文本，音频属上一代；保留 pending 让守卫继续
            # 409（用户再重生一次即愈合），不得无条件清除放行音画错位。
            if narration_at_start is not None:
                current = next((f for f in data.get("frames", []) if f.get("index") == frame_index), None)
                if current is not None and current.get("narration") != narration_at_start:
                    return
            pend.remove(frame_index)

        await svc._update_storyboard(task_dir, _clear_pending)
        await svc._set_regen_state(task_dir, frame_index, "done")
    except Exception as e:
        logger.warning("frame regen failed (task %s frame %d): %s", task_id, frame_index, e)
        try:
            await svc._set_regen_state(task_dir, frame_index, "error", error=f"{type(e).__name__}: {e}")
        except Exception:
            logger.warning("write regen_error failed (task %s frame %d)", task_id, frame_index, exc_info=True)
    finally:
        _regen_inflight.pop((task_id, frame_index), None)


# ---------------------------------------------------------------------------
# Local visual upload
# ---------------------------------------------------------------------------


@router.post("/tasks/{task_id}/frames/{frame_index}/upload-visual")
async def upload_frame_visual(
    request: Request,
    task_id: str,
    frame_index: int,
    file: UploadFile = File(...),
):
    """Upload a local image/video to replace a frame's visual source."""
    svc = _get_service(request)
    user_id = get_effective_user_id()
    task = await svc.get_task(task_id, user_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.status not in (DRAFT, "ready"):
        raise HTTPException(status_code=409, detail="Task must be draft or ready")

    # M6: storyboard 必须存在且 frame_index 在界内——修复前：越界上传文件照写、
    # dirty 照标、updater 静默 no-op，返回 {"ok": true} 假成功。
    task_dir = Path(svc._storage_dir) / user_id / "tasks" / task_id
    storyboard_path = task_dir / "storyboard.json"
    if not storyboard_path.exists():
        raise HTTPException(status_code=404, detail="Storyboard not found for this task")
    _sb = json.loads(storyboard_path.read_text(encoding="utf-8"))
    _n_frames = len(_sb.get("frames", []))
    if frame_index < 0 or frame_index >= _n_frames:
        raise HTTPException(status_code=404, detail=f"Frame {frame_index} not found (0-{_n_frames - 1})")

    # Validate file type
    allowed_types = {"image/jpeg", "image/png", "image/webp", "video/mp4", "video/webm"}
    if file.content_type not in allowed_types:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {file.content_type}")

    # SEC-M7（批次 2 项 2.6）：大小检查提到 read 之前（file.size，避免整读
    # 大文件进内存才拒绝）；再字节级魔数校验，扩展名取自嗅探而非用户文件名。
    from app.gateway.video_gen.upload_validation import VISUAL_MAX_BYTES, validate_visual_bytes

    if isinstance(file.size, int) and file.size > VISUAL_MAX_BYTES:
        raise HTTPException(status_code=400, detail="File too large (max 50MB)")

    # Save to task_dir/visuals/
    visuals_dir = task_dir / "visuals"
    visuals_dir.mkdir(parents=True, exist_ok=True)

    content = await file.read()
    try:
        ext = validate_visual_bytes(content, max_bytes=VISUAL_MAX_BYTES)
    except ValueError as e:
        logger.warning("SEC-M12: visual upload rejected (task=%s frame=%d): %s", task_id, frame_index, e)
        raise HTTPException(status_code=400, detail="Invalid visual upload (unsupported format or too large)") from e
    dest = visuals_dir / f"frame_{frame_index:03d}{ext}"
    dest.write_bytes(content)

    # Update storyboard.json with the local visual path via the unified read-merge-write
    # writer so concurrent writes are not clobbered by a full overwrite.
    _local_path = f"visuals/frame_{frame_index:03d}{ext}"
    _visual_type = "image" if file.content_type.startswith("image/") else "video"
    _frame_index = frame_index

    def _visual_updater(data: dict) -> None:
        frames = data.get("frames", [])
        if 0 <= _frame_index < len(frames):
            frames[_frame_index]["local_visual_path"] = _local_path
            frames[_frame_index]["visual_type"] = _visual_type
            # Clear remote URL so Worker uses the local file
            frames[_frame_index]["visual_url"] = None
            # P0: 清 regen_state/regen_error/preview_error -- 上传是有效修复，
            # 不清则 D1 预检 frames_in_regen_error 命中 -> 409 假阳性
            # （且文案「沿用失败前的旧画面」也是错的，实际会用新上传的图）。
            frames[_frame_index].pop("regen_state", None)
            frames[_frame_index].pop("regen_error", None)
            frames[_frame_index].pop("preview_error", None)

    await svc._update_storyboard(task_dir, _visual_updater)

    # B2: track this frame as dirty (pending recompose)
    await svc.repo.add_dirty_frame(task_id, frame_index)
    # P1-7: 刷 updated_at 防 reaper 误杀（brief 显式列出；add_dirty_frame 本已刷，此为显式不变量）
    await svc.touch_task(task_id)

    return {"ok": True, "path": f"visuals/frame_{frame_index:03d}{ext}"}


# ---------------------------------------------------------------------------
# B1: Serve local visual files (for frame thumbnails)
# ---------------------------------------------------------------------------

_VISUAL_MEDIA_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
}


@router.get("/tasks/{task_id}/frames/{frame_index}/visual")
async def get_frame_visual(
    request: Request,
    task_id: str,
    frame_index: int,
):
    """B1: Serve a frame's local visual file (uploaded via /upload-visual).

    Falls back to redirecting to the remote visual_url if no local file exists.
    """
    svc = _get_service(request)
    user_id = get_effective_user_id()
    task = await svc.get_task(task_id, user_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")

    task_dir = Path(svc._storage_dir) / user_id / "tasks" / task_id
    storyboard_path = task_dir / "storyboard.json"
    if not storyboard_path.exists():
        raise HTTPException(status_code=404, detail="Storyboard not found")

    import json

    sb = json.loads(storyboard_path.read_text(encoding="utf-8"))
    frames = sb.get("frames", [])
    if frame_index < 0 or frame_index >= len(frames):
        raise HTTPException(status_code=404, detail="Frame not found")

    frame = frames[frame_index]
    local_path = frame.get("local_visual_path")
    visual_url = frame.get("visual_url")

    def _serve_local(rel_path: str) -> FileResponse | None:
        resolved = (task_dir / rel_path).resolve()
        # Path traversal guard (same pattern as /audio route)
        try:
            resolved.relative_to(task_dir.resolve())
        except ValueError:
            raise HTTPException(status_code=404, detail="Invalid visual path")
        if not resolved.exists():
            return None  # 2026-07-27：文件缺失返 None，调用方回退 visual_url（曾直接 404）
        ext = resolved.suffix.lower()
        media_type = _VISUAL_MEDIA_TYPES.get(ext, "application/octet-stream")
        # F8: no-cache——同一帧重复上传/重生后 URL 不变，强制浏览器重校验（304 兜底廉价）
        return FileResponse(str(resolved), media_type=media_type, headers={"Cache-Control": "no-cache"})

    if local_path:
        resp = _serve_local(local_path)
        if resp is not None:
            return resp

    sidecar_path = task_dir / "local_sources.json"
    if sidecar_path.exists():
        try:
            local_sources = json.loads(sidecar_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            local_sources = {}
        sidecar_rel = local_sources.get(str(frame_index))
        if sidecar_rel:
            resp = _serve_local(sidecar_rel)
            if resp is not None:
                return resp

    if visual_url:
        from fastapi.responses import RedirectResponse

        return RedirectResponse(url=visual_url)

    raise HTTPException(status_code=404, detail="No visual asset for this frame")


# ---------------------------------------------------------------------------


def _adjust_index(old_idx: int, from_idx: int, to_idx: int) -> int:
    """Map old frame index to new index after reorder (from→to).

    Frames stay in their original scene_groups — only global position changes.
    """
    if from_idx == to_idx:
        return old_idx
    if from_idx < to_idx:
        # Frame moved forward: moved frame goes to to_idx - 1
        # Frames strictly between (from, to) shift left by 1
        if old_idx == from_idx:
            return to_idx - 1
        elif from_idx < old_idx < to_idx:
            return old_idx - 1
        else:
            return old_idx
    else:
        # Frame moved backward (from > to): moved frame goes to to_idx
        # Frames in [to, from) shift right by 1
        if old_idx == from_idx:
            return to_idx
        elif to_idx <= old_idx < from_idx:
            return old_idx + 1
        else:
            return old_idx


@router.put("/tasks/{task_id}/storyboard/reorder")
async def reorder_frames(
    request: Request,
    task_id: str,
    body: ReorderRequest,
):
    """Reorder frames in the storyboard (drag-and-drop).

    Frames stay in their original scene_groups — only global array position changes.
    scene_groups[].frame_indices are remapped via _adjust_index.
    Audio files use CAS naming so no file renaming is needed.
    """
    svc = _get_service(request)
    user_id = get_effective_user_id()
    task = await svc.get_task(task_id, user_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    # H2: 收紧 draft-only——failed 态 reorder 不重 concat audio.mp3/不重写 subtitle.srt，
    # recompose 复用旧序音频（service.recompose_task → 盘面 audio.mp3）→ 视频新帧序 +
    # 音频旧帧序 = 音画错位。与增删帧 409（同文件 add/delete 端点注释）同一设计理由；
    # 「ready/failed 重排」需配套音频重建，列 P2。
    if task.status != DRAFT:
        raise HTTPException(
            status_code=409,
            detail="重排仅草稿态可用：已生成任务重排会使音频/字幕与画面错位（重新合成不重配音频）",
        )

    task_dir = Path(svc._storage_dir) / user_id / "tasks" / task_id
    frm, to = body.from_index, body.to_index

    # Validate bounds against current storyboard before mutation
    sb_path = task_dir / "storyboard.json"
    if sb_path.exists():
        sb_data = json.loads(sb_path.read_text(encoding="utf-8"))
        n_frames = len(sb_data.get("frames", []))
        if n_frames > 0 and (frm >= n_frames or to > n_frames):
            raise HTTPException(status_code=400, detail=f"Index out of range (0-{n_frames})")

    def _reorder_updater(data: dict) -> None:
        frames = data.get("frames", [])
        # Move frame from `frm` to `to`
        frame = frames.pop(frm)
        insert_pos = to if to < frm else to - 1
        if insert_pos < 0:
            insert_pos = 0
        frames.insert(insert_pos, frame)
        # Re-index all frames
        for i, f in enumerate(frames):
            f["index"] = i
        # Remap scene_groups frame_indices (frames stay in their original groups)
        for group in data.get("scene_groups", []):
            group["frame_indices"] = sorted(_adjust_index(fi, frm, to) for fi in group.get("frame_indices", []))

    await svc._update_storyboard(task_dir, _reorder_updater)
    # Invalidate manifest: segment positions changed after reorder, so next render rebuilds all segments
    manifest_path = task_dir / "manifest.json"
    manifest_path.unlink(missing_ok=True)
    # P1-3 防护（F9）：重排后 segment 缓存与新帧序错位——启用增量 recompose 后
    # 数量一致但内容错位的 seg 会被复用出鬼片；重排必须强制下次全量。
    import shutil

    shutil.rmtree(task_dir / "segments", ignore_errors=True)
    # R2: 重排改变帧序，dirty_frames 索引语义随之失效（索引 1 已指向另一帧）——
    # 必须清空。当前被 segments 清空→worker 全量回退掩盖，属防护耦合，清空语义自洽。
    # 2026-08-08 P1-3：清空必须走锁内方法——裸 update 与并发 add_dirty_frame
    # 互相覆盖（丢帧标记 → 增量 recompose 漏帧）。
    await svc.repo.clear_dirty_frames(task_id)
    # P1-7: 刷 updated_at 防 reaper 误杀（brief 显式列出；上行 update 本已刷，此为显式不变量）
    await svc.touch_task(task_id)
    return {"storyboard": json.loads((task_dir / "storyboard.json").read_text(encoding="utf-8"))}


@router.post("/tasks/{task_id}/regenerate-audio")
async def regenerate_all_audio(request: Request, task_id: str):
    """B4（R3，2026-09-28 edits 修复批）：批量重生全部配音（ready/failed；draft 走 generate-audio）。

    202 异步执行（_regenerate_frame_bg 同模式）；进度经帧级 regen_state 轮询
    （逐帧置/清，白得 fold_stale_running_states 的 10min 滞留兜底）。
    前端弹层文案须明示「将按新配音重排字幕，手动修改的字幕文本将被覆盖」。
    """
    svc = _get_service(request)
    user_id = get_effective_user_id()
    task = await svc.get_task(task_id, user_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.status not in ("ready", "failed"):
        raise HTTPException(status_code=409, detail=f"仅已完成或失败的任务可批量重生配音（当前状态：{task.status}）")

    # 决策 3 双重互斥①：任务级 pipeline（渲染/recompose/re-render）在跑 → 409
    existing = svc._active_tasks.get(task_id)
    if existing is not None and not existing.done():
        raise HTTPException(status_code=409, detail="任务正在合成中，请等待完成后再重生配音")
    # 反向互斥：单帧重生在跑（帧级键空间前缀匹配，与 recompose/re-render 守卫同口径）
    if any(k[0] == task_id for k in _regen_inflight):
        raise HTTPException(status_code=409, detail="有帧正在重新生成，请等待完成后再重生配音")
    # 防重入：payload 标记（进程内 _regen_inflight 兜不住「上次崩溃残留」）
    payload = json.loads(task.payload) if isinstance(task.payload, str) else (task.payload or {})
    if payload.get("audio_regen_running"):
        raise HTTPException(status_code=409, detail="配音批量重生进行中，请勿重复发起")

    task_dir = Path(svc._storage_dir) / user_id / "tasks" / task_id
    try:
        sb = json.loads((task_dir / "storyboard.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise HTTPException(status_code=404, detail="Storyboard not found")
    frame_indices = [f.get("index") for f in sb.get("frames", []) if isinstance(f.get("index"), int)]
    if not frame_indices:
        raise HTTPException(status_code=400, detail="无帧可重生配音")

    # v5：入口一次性登记**全部**目标帧键（值统一指向同一后台任务；create_task 后、
    # 本协程让出事件循环前的同步段完成登记，杜绝「起步后、登记前」漏防窗口）；
    # 逐帧完成由后台任务在 finally 摘除。
    for i in frame_indices:
        if (task_id, i) in _regen_inflight:
            raise HTTPException(status_code=409, detail="有帧正在重新生成，请等待完成后再重生配音")
    bg = asyncio.create_task(_regenerate_all_audio_bg(svc, task_id, user_id, frame_indices))
    for i in frame_indices:
        _regen_inflight[(task_id, i)] = bg
    return {"status": "started", "frames": frame_indices}


async def _regenerate_all_audio_bg(svc, task_id: str, user_id: str, frame_indices: list[int]) -> None:
    """B4 后台：置 audio_regen_running → service 批量重生 → 终态清除；逐帧摘登记。

    进程重启即死——残留标记由启动清理 recover_audio_regen_flags 折算失败态。
    """
    set_llm_accounting_context(task_id, user_id, domain="video_gen")  # 2026-09-01 计费：同 _regenerate_frame_bg
    try:
        task = await svc.repo.get(task_id, user_id=user_id)
        pl = json.loads(task.payload) if isinstance(task.payload, str) else (task.payload or {})
        pl["audio_regen_running"] = True
        await svc.repo.update(task_id, payload=json.dumps(pl, ensure_ascii=False))
        try:
            await svc.regenerate_all_audio(task_id, user_id)
        finally:
            task = await svc.repo.get(task_id, user_id=user_id)
            pl2 = json.loads(task.payload) if isinstance(task.payload, str) else (task.payload or {})
            pl2.pop("audio_regen_running", None)
            await svc.repo.update(task_id, payload=json.dumps(pl2, ensure_ascii=False))
    except Exception as e:
        logger.warning("regenerate-audio failed (task %s): %s", task_id, e, exc_info=True)
        try:
            task = await svc.repo.get(task_id, user_id=user_id)
            pl3 = json.loads(task.payload) if isinstance(task.payload, str) else (task.payload or {})
            pl3.pop("audio_regen_running", None)
            pl3["audio_error"] = f"批量重生配音失败：{type(e).__name__}，请重试"
            await svc.repo.update(task_id, payload=json.dumps(pl3, ensure_ascii=False))
        except Exception:
            logger.warning("write audio_error failed (task %s)", task_id, exc_info=True)
    finally:
        for i in frame_indices:
            _regen_inflight.pop((task_id, i), None)


@router.post("/tasks/{task_id}/generate-audio")
async def trigger_draft_audio(task_id: str, request: Request):
    """Manually trigger TTS audio for a draft task (recovery after restart)."""
    import asyncio

    svc = _get_service(request)
    user_id = get_effective_user_id()  # E1: 与其它 task 端点一致，按属主过滤
    task = await svc.get_task(task_id, user_id)  # E1: 修 IDOR（修前用 repo.get 绕过 user_id）
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.status != "draft":
        raise HTTPException(status_code=400, detail=f"Task is {task.status}, must be draft")

    payload = json.loads(task.payload) if isinstance(task.payload, str) else (task.payload or {})
    # Plan B（2026-10-19，D4）：存量 ViMax 草稿的显式 preset 在后台 TTS 派发前
    # 可读拒绝（按 task.task_type 判定）。
    if getattr(task, "task_type", "") == "vimax":
        from app.gateway.video_gen.engines.vimax.voice_policy import ensure_vimax_voice_allowed

        # 评审修复（2026-10-19）：后台合成读的是 storyboard.config.voice_id——
        # 存量 preset 常只存在于盘面 config（payload 无 voice 键），只校验 payload
        # 会让门形同虚设。两源同门校验。
        _sb_path = Path(svc._storage_dir) / user_id / "tasks" / task_id / "storyboard.json"
        _cfg_voice = None
        if _sb_path.exists():
            try:
                _cfg_voice = (json.loads(_sb_path.read_text(encoding="utf-8")) or {}).get("config", {}).get("voice_id")
            except Exception:
                _cfg_voice = None
        try:
            ensure_vimax_voice_allowed(payload.get("voice_id"))
            ensure_vimax_voice_allowed(_cfg_voice)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
    if payload.get("audio_ready"):
        return {"status": "already_ready", "total_duration": payload.get("total_duration")}

    task_dir = Path(svc._storage_dir) / user_id / "tasks" / task_id
    asyncio.create_task(svc._generate_draft_audio(task_id, task_dir, user_id=user_id))
    return {"status": "started"}


# ---------------------------------------------------------------------------
# SSE Progressive Preview
# ---------------------------------------------------------------------------


@router.get("/tasks/{task_id}/events")
async def task_events(request: Request, task_id: str):
    """SSE stream: push frame-level visual previews during Gateway render phase.

    Polls storyboard.json every 2s for visual_url changes and pushes frame events.
    Tolerates JSONDecodeError (file may be mid-replacement during atomic write).
    """
    import asyncio

    from sse_starlette.sse import EventSourceResponse

    svc = _get_service(request)
    user_id = get_effective_user_id()
    task_dir = Path(svc._storage_dir) / user_id / "tasks" / task_id

    async def event_generator():
        last_urls: dict[int, str] = {}
        while True:
            if await request.is_disconnected():
                break

            task = await svc.get_task(task_id, user_id)
            if task is None or _render_is_terminal(task.status):
                yield {
                    "event": "done",
                    "data": json.dumps(
                        {
                            "status": task.status if task else "unknown",
                            "progress": task.progress if task else 0,
                        }
                    ),
                }
                break

            # M3-3c: composing/running 心跳——每轮发 progress，前端 composing 期有"还活着"信号
            # （composing 期 storyboard.json 不被 Worker 改→无 frame 事件→否则前端盲等）。
            yield {
                "event": "progress",
                "data": json.dumps({"progress": task.progress, "status": task.status}),
            }

            # Read storyboard.json for frame visual_url changes (tolerate concurrent writes)
            sb_path = task_dir / "storyboard.json"
            if sb_path.exists():
                try:
                    data = json.loads(sb_path.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    data = None  # File being atomically replaced (tmp→rename), skip this cycle
                if data:
                    for frame in data.get("frames", []):
                        idx = frame.get("index", 0)
                        url = frame.get("visual_url")
                        if url and last_urls.get(idx) != url:
                            last_urls[idx] = url
                            yield {
                                "event": "frame",
                                "data": json.dumps(
                                    {
                                        "frame_index": idx,
                                        "visual_url": url,
                                        "visual_type": frame.get("visual_type", "image"),
                                        "progress": task.progress,
                                    }
                                ),
                            }

            await asyncio.sleep(2)

    return EventSourceResponse(event_generator())


# --- CourseProject CRUD ---


def _get_project_store(request: Request):
    from app.gateway.video_gen.course_project_store import CourseProjectStore

    svc = _get_service(request)
    return CourseProjectStore(base_dir=svc._storage_dir)


class CourseProjectInput(BaseModel):
    """E14: create/update_project 的 Pydantic 白名单 schema。

    旧实现用 ``body: dict = Body(...)`` 后 ``store.create(**body)``，未知字段被
    ``CourseProject.__dataclass_fields__`` 静默丢弃（silent data loss），已知字段传错类型
    也能存盘、延后到下游 style 消费才崩。改用 Pydantic 模型 + extra="forbid"：未知字段→422、
    错类型→422。字段集对齐 CourseProject 可设置字段（排除 id/user_id/created_at/updated_at）。
    """

    model_config = {"extra": "forbid"}

    title: str | None = None
    visual_style_preset: str | None = None
    character_reference: str | None = None
    color_palette: dict | None = None
    voice_id: str | None = None
    # T-E11（D6）：项目级默认语速（None = 未设置走任务缺省/config；显式 null = 清空）
    tts_speed: float | None = Field(None, ge=0.5, le=2.0)
    subtitle_style: str | None = None
    character_sheets: list | None = None
    # deprecated（C2，决策 7）：保留仅为兼容仍传该字段的旧调用方（extra="forbid" 下删除会 422）
    scene_style_refs: list | None = None
    # C1: 风格锚图本地绝对路径（None = 未设置；显式 "" = 清空——PUT exclude_unset 语义）
    style_anchor_image: str | None = None
    # M6: 道具资产（管理走 /props 端点；此处仅供整体读写同步）
    prop_assets: list | None = None
    # ── 品牌包 A 类字段（2026-09-08 remotion-course T8）──────────────────
    remotion_theme: str | None = None
    font_family: str | None = None
    logo_path: str | None = None
    intro_outro: dict | None = None

    # 条目 22（2026-09-12 缺陷批）：项目更新通路同批补校验（与 assets PUT 同机制）。
    # font_family ≤120 字 + 去控制字符；color_palette.primary（存在时）必须
    # #RRGGBB 并归一大写——修前自由 dict/str 直灌 remotion 模板 CSS。校验失败按
    # 本模型惯例 422（同错类型口径；assets 端点是 400——两处模型层/端点层惯例不同）。
    @field_validator("font_family")
    @classmethod
    def _validate_font_family(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if len(v) > 120:
            raise ValueError("font_family 超过 120 字上限")
        cleaned = "".join(ch for ch in v if unicodedata.category(ch) != "Cc").strip()
        return cleaned or None

    @field_validator("color_palette")
    @classmethod
    def _validate_color_palette(cls, v: dict | None) -> dict | None:
        if not isinstance(v, dict) or v.get("primary") is None:
            return v
        raw = str(v["primary"]).strip()
        if not raw:
            return v
        if not re.fullmatch(r"#[0-9A-Fa-f]{6}", raw):
            raise ValueError("color_palette.primary 必须是 #RRGGBB 六位十六进制色值（如 #2563EB）")
        out = dict(v)
        out["primary"] = raw.upper()
        return out


@router.post("/projects")
async def create_project(request: Request, body: CourseProjectInput):
    store = _get_project_store(request)
    user_id = get_effective_user_id()
    proj = store.create(user_id=user_id, **body.model_dump(exclude_unset=True))
    return proj.to_dict()


@router.get("/projects")
async def list_projects(request: Request):
    store = _get_project_store(request)
    user_id = get_effective_user_id()
    return {"projects": [p.to_dict() for p in store.list(user_id=user_id)]}


@router.get("/projects/{project_id}")
async def get_project(request: Request, project_id: str):
    store = _get_project_store(request)
    user_id = get_effective_user_id()
    proj = store.get(project_id, user_id=user_id)
    if not proj:
        raise HTTPException(status_code=404, detail="Project not found")
    return proj.to_dict()


@router.put("/projects/{project_id}")
async def update_project(request: Request, project_id: str, body: CourseProjectInput):
    store = _get_project_store(request)
    user_id = get_effective_user_id()
    try:
        proj = store.update(project_id, user_id=user_id, **body.model_dump(exclude_unset=True))
        return proj.to_dict()
    except ValueError:
        raise HTTPException(status_code=404, detail="Project not found")


@router.post("/projects/{project_id}/brand/logo")
async def upload_project_logo(request: Request, project_id: str, file: UploadFile = File(...)):
    """T8/T9：上传项目品牌 LOGO（存 video-gen 存储的 brand_assets/<pid>/，写回
    logo_path 相对路径；remotion_course 任务构建 props 时物化进 task_dir）。

    校验：PNG/JPG 魔数 + ≤5MB（复用 SEC-M7 图片校验通道）。
    """
    store = _get_project_store(request)
    svc = _get_service(request)
    user_id = get_effective_user_id()
    proj = store.get(project_id, user_id=user_id)
    if not proj:
        raise HTTPException(status_code=404, detail="Project not found")

    from app.gateway.video_gen.upload_validation import validate_logo_bytes

    data = await file.read()
    try:
        # T-E1（M14+m19）：嗅探认 SVG；落盘后缀取嗅探结果而非用户文件名
        # （防 PNG 字节+.svg 名落错文件）。
        suffix = validate_logo_bytes(data, max_bytes=5 * 1024 * 1024, label="logo")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    assets_dir = Path(svc._storage_dir) / "brand_assets" / project_id
    assets_dir.mkdir(parents=True, exist_ok=True)
    dest = assets_dir / f"logo{suffix}"
    dest.write_bytes(data)
    rel = f"brand_assets/{project_id}/{dest.name}"
    store.update(project_id, user_id=user_id, logo_path=rel)
    return {"logo_path": rel, "url": f"/api/video/projects/{project_id}/brand/logo-image"}


@router.get("/projects/{project_id}/brand/logo-image")
async def get_project_logo(request: Request, project_id: str):
    """LOGO 预览（配置页实时预览用；无 LOGO 404）。"""
    store = _get_project_store(request)
    svc = _get_service(request)
    user_id = get_effective_user_id()
    proj = store.get(project_id, user_id=user_id)
    if not proj or not getattr(proj, "logo_path", None):
        raise HTTPException(status_code=404, detail="No logo")
    p = Path(svc._storage_dir) / proj.logo_path
    if not p.is_file():
        raise HTTPException(status_code=404, detail="No logo file")
    media = "image/svg+xml" if p.suffix == ".svg" else f"image/{'jpeg' if p.suffix in ('.jpg', '.jpeg') else 'png'}"
    # T-E1：SVG 是 active content——对齐 artifacts 路由惯例强制 attachment（<img>
    # 预览不受影响；直连导航不执行内嵌脚本）。
    headers = {"Content-Disposition": "attachment; filename=logo.svg"} if p.suffix == ".svg" else None
    return FileResponse(str(p), media_type=media, headers=headers)


@router.delete("/projects/{project_id}")
async def delete_project(request: Request, project_id: str):
    store = _get_project_store(request)
    user_id = get_effective_user_id()
    store.delete(project_id, user_id=user_id)
    return {"status": "deleted"}


# --- Task 10/11: 角色三视图生成/上传 ---


def _upsert_sheet(sheets: list[dict], sheet_dict: dict, replace_index: int | None = None) -> list[dict]:
    """R2: append 或 replace at index（纯函数，供测试）。

    replace_index 越界/非整数（含 bool，语义非下标）→ append 兜底。防客户端经 raw body
    误传字符串等触发 TypeError→500（review 隐患①）。
    """
    if replace_index is not None and isinstance(replace_index, int) and not isinstance(replace_index, bool) and 0 <= replace_index < len(sheets):
        new = list(sheets)
        new[replace_index] = sheet_dict
        return new
    return [*sheets, sheet_dict]


def _normalize_sheet_name(name) -> str:
    """M1 name 唯一校验键：strip + 大小写不敏感。"""
    return (name or "").strip().casefold()


async def _append_character_sheet(store, project_id: str, user_id: str, sheet_dict: dict, replace_id: str | None = None) -> dict:
    """把新 CharacterSheet 追加（或按 id 原子替换）到项目并持久化。

    M1（v7 拍板）：管理端点全量改 id 寻址——replace_index 退役；新 sheet 无 id 时
    分配 uuid4().hex[:8]（与 from_dict 存量回填同格式）。name 唯一性（归一化后）
    同项目重名 → 409；替换目标自身时同名放行（重新生成场景）。
    """
    proj = store.get(project_id, user_id=user_id)
    if not proj:
        raise HTTPException(status_code=404, detail="Project not found")
    sheets = [s.to_dict() for s in proj.character_sheets]
    # 新 sheet 无 id → 分配（generate/upload 两条创建路径共用本口）
    if not sheet_dict.get("id"):
        import uuid

        sheet_dict = {**sheet_dict, "id": uuid.uuid4().hex[:8]}
    # M1 name 唯一校验：strip + 大小写不敏感；排除替换目标自身
    new_key = _normalize_sheet_name(sheet_dict.get("name"))
    if new_key:
        for s in sheets:
            if s.get("id") == replace_id:
                continue
            if _normalize_sheet_name(s.get("name")) == new_key:
                raise HTTPException(status_code=409, detail="角色名已存在")
    replace_index = None
    if replace_id:
        replace_index = next((i for i, s in enumerate(sheets) if s.get("id") == replace_id), None)
    sheets = _upsert_sheet(sheets, sheet_dict, replace_index)
    store.update(project_id, user_id=user_id, character_sheets=sheets)
    return sheet_dict


@router.post("/projects/{project_id}/character-sheets/reorder")
async def reorder_character_sheets_endpoint(request: Request, project_id: str, body: SheetIdsRequest):
    """U2 + M1: 角色表排序（body 传完整 id 数组——新顺序即数组顺序；首个 = 主角色）。

    ids 必须与当前集合完全一致（多/少/未知 id → 404）——from/to_index 下标寻址退役。
    """
    store = _get_project_store(request)
    user_id = get_effective_user_id()
    proj = store.get(project_id, user_id=user_id)
    if not proj:
        raise HTTPException(status_code=404, detail="Project not found")
    by_id = {s.id: s.to_dict() for s in proj.character_sheets}
    if len(body.ids) != len(by_id) or any(i not in by_id for i in body.ids):
        raise HTTPException(status_code=404, detail="sheet ids do not match current set")
    sheets = [by_id[i] for i in body.ids]
    store.update(project_id, user_id=user_id, character_sheets=sheets)
    return {"status": "reordered", "sheets": sheets}


@router.post("/projects/{project_id}/character-sheets/generate")
async def generate_character_sheet_endpoint(request: Request, project_id: str, body: dict = Body(...)):
    """Task 10 方式 A：文字描述生成角色三视图，存入项目。"""
    from app.gateway.video_gen.character_sheet_generator import generate_character_sheet

    store = _get_project_store(request)
    user_id = get_effective_user_id()
    svc = _get_service(request)
    ai_image_provider = svc._providers.ai_image

    # B1: 加载项目视觉风格预设，注入角色图 prompt（修前角色图完全无视项目风格）
    style_ctx: dict = {}
    try:
        loaded = svc._load_course_project_style(project_id, user_id=user_id)
        if isinstance(loaded, dict):
            style_ctx = loaded
    except Exception:
        style_ctx = {}
    style_prefix = style_ctx.get("style_prefix", "")

    async def _gen(prompt, *, reference_image=None):
        refs = [reference_image] if reference_image else None
        return await ai_image_provider.generate_image(prompt, reference_image_paths=refs)

    sheet = await generate_character_sheet(
        description=body["description"],
        name=body.get("name", ""),
        features=body.get("features", ""),
        image_gen=_gen,
        storage_dir=svc._storage_dir,
        project_id=project_id,
        style_prefix=style_prefix,
    )
    # M1：replace_index（数组下标）退役 → replace_id（稳定 id 寻址）
    return await _append_character_sheet(store, project_id, user_id, sheet.to_dict(), replace_id=body.get("replace_id"))


@router.post("/projects/{project_id}/character-sheets/upload")
async def upload_character_sheet_endpoint(
    request: Request,
    project_id: str,
    name: str = Form(...),
    features: str = Form(""),
    front: UploadFile = File(...),
    side: UploadFile | None = File(None),  # U1: 可选
    back: UploadFile | None = File(None),  # U1: 可选
):
    """Task 11 方式 B：上传角色三视图图片（front 必填，side/back 可选 U1），存入项目。"""
    from app.gateway.video_gen.character_sheet_generator import save_uploaded_character_sheet

    store = _get_project_store(request)
    user_id = get_effective_user_id()
    svc = _get_service(request)
    # SEC-M7（批次 2 项 2.6）：三视图每张补大小上限 + 图片魔数拒绝
    # （修前无上限无类型校验，bytes 直写 .png）。
    from app.gateway.video_gen.upload_validation import validate_image_bytes

    front_bytes = await front.read()
    side_bytes = await side.read() if side else None
    back_bytes = await back.read() if back else None
    try:
        validate_image_bytes(front_bytes, label="front view")
        if side_bytes:
            validate_image_bytes(side_bytes, label="side view")
        if back_bytes:
            validate_image_bytes(back_bytes, label="back view")
    except ValueError as e:
        logger.warning("SEC-M12: character sheet upload rejected (project=%s): %s", project_id, e)
        raise HTTPException(status_code=400, detail="Invalid character sheet upload (unsupported format or too large)") from e
    sheet = await save_uploaded_character_sheet(
        name=name,
        features=features,
        front_bytes=front_bytes,
        side_bytes=side_bytes,
        back_bytes=back_bytes,
        storage_dir=svc._storage_dir,
        project_id=project_id,
    )
    return await _append_character_sheet(store, project_id, user_id, sheet.to_dict())


@router.delete("/projects/{project_id}/character-sheets/{sheet_id}")
async def delete_character_sheet_endpoint(request: Request, project_id: str, sheet_id: str):
    """B7 + M1: 删除项目某角色表（按稳定 id——数组下标寻址退役，无下标漂移）。"""
    store = _get_project_store(request)
    user_id = get_effective_user_id()
    proj = store.get(project_id, user_id=user_id)
    if not proj:
        raise HTTPException(status_code=404, detail="Project not found")
    sheets = [s.to_dict() for s in proj.character_sheets]
    idx = next((i for i, s in enumerate(sheets) if s.get("id") == sheet_id), None)
    if idx is None:
        raise HTTPException(status_code=404, detail=f"character sheet {sheet_id} not found")
    removed = sheets[idx]
    sheets = sheets[:idx] + sheets[idx + 1 :]
    store.update(project_id, user_id=user_id, character_sheets=sheets)
    return {"status": "deleted", "sheet": removed}


@router.get("/projects/{project_id}/character-sheets/{tag}")
async def serve_character_sheet(request: Request, project_id: str, tag: str):
    """A4: serve character-sheet image bytes（方式 A `{tag}.png` / 方式 B `{tag}_{uuid}.png）。

    方式 A 存绝对路径但前端不可直接读本地盘；方式 B 同。补 GET route 让 <img> 能取到图。
    tag 白名单 front/side/back 防 path traversal。

    legacy：保留仅为向后兼容（老数据/外部直链）。前端已全部切 serve-by-index
    （serve_character_sheet_by_index）；多角色表项目此路由会取全项目 mtime 最新（串图），
    新调用请用 /character-sheets/{index}/{tag}。
    """
    if tag not in {"front", "side", "back"}:
        raise HTTPException(status_code=404, detail=f"character sheet {tag} not found")
    # E4 (Task 16): 补归属校验——修前按路径 glob **无 user 校验**，任何登录用户
    # 猜到/拿到 project_id 就能读他人角色表图（IDOR）。同文件的 by-index 版本
    # (serve_character_sheet_by_index) 早已做归属 + 越界双校验，这里直接对齐它。
    store = _get_project_store(request)
    user_id = get_effective_user_id()
    if not store.get(project_id, user_id=user_id):
        raise HTTPException(status_code=404, detail="Project not found")
    svc = _get_service(request)
    sheet_dir = Path(svc._storage_dir) / "character_sheets" / project_id
    # 越界校验（对齐 by-index 版）：resolved 必须在 character_sheets 之下
    base = (Path(svc._storage_dir) / "character_sheets").resolve()
    resolved_dir = sheet_dir.resolve()
    if base not in resolved_dir.parents and resolved_dir != base:
        raise HTTPException(status_code=404, detail="character sheet path outside storage")
    matches = list(sheet_dir.glob(f"{tag}*.png"))
    if not matches:
        raise HTTPException(status_code=404, detail=f"character sheet {tag} not found")
    # F2: 取最近修改的（用户最新生成/上传），非字典序第一——sorted()[0] 总取 front.png
    # （方式 A），会压过用户后上传的 front_<uuid>.png（方式 B），返回旧图。
    latest = max(matches, key=lambda p: p.stat().st_mtime)
    return FileResponse(latest, media_type="image/png")


# --- C6（决策 4）：项目风格锚图管理——AI 生成 + 手动上传双入口 ---
# 目录拍死：图片资产走 {资产类}/{pid}/ 形态（与三视图同构、无 user 维度——项目 JSON
# 才走 course_projects/{user}/）。新资产替换/删除同步清理磁盘文件（三视图现状不动）。


def _style_anchor_base_dir(storage_dir, project_id: str) -> Path:
    return Path(storage_dir) / "style_anchor" / project_id


def _style_anchor_candidate_name() -> str:
    import time
    import uuid

    return f"{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}.png"


async def _write_image_ref_to_path(image_ref, dest: Path) -> Path:
    """把 provider 返回的 ImageRef（远程 URL 或 data: URI）落盘（镜像角色图 #7）。

    返回**实际写入**的路径：真实格式与 dest 后缀不一致时会改后缀落盘（如 jpeg
    写为 .jpg），调用方必须用返回值组装 candidate_path，否则响应会指向从未写入的
    原后缀文件（固化必 404）。真实格式判定：data URI 用 mime 标签（复用
    ai_image._MIME_EXT 单一表，含 image/jpg 别名），标签缺失/异常再魔数嗅探兜底；
    URL 分支后缀不可信，一律按下载 bytes 魔数嗅探（B3 补全 2026-08-31）。"""
    import base64

    from app.gateway.video_gen.visual_sources.ai_image import _MIME_EXT, _sniff_image_ext

    dest.parent.mkdir(parents=True, exist_ok=True)
    url = image_ref.url or ""
    if url.startswith("data:"):
        header, b64 = url.split(",", 1)
        data = base64.b64decode(b64)
        ext = _MIME_EXT.get(header[5:].split(";")[0], "") or _sniff_image_ext(data)
        if ext and ext != dest.suffix:
            dest = dest.with_suffix(ext)
        dest.write_bytes(data)
        return dest
    # C2（2026-10-05 审计 §2-1）：出站下载统一走 SSRF 守卫。
    content = await safe_download(url, timeout=60.0, headers={"User-Agent": "TrainerAI-VideoGen/1.0 (style anchor)"})
    # B3 补全：CDN bytes 不一定是 PNG（URL 后缀不可信）——魔数嗅探校正后缀，
    # 否则 JPEG 字节躺成 .png，serve Content-Type / 下游按扩展名识别全错。
    ext = _sniff_image_ext(content)
    if ext and ext != dest.suffix:
        dest = dest.with_suffix(ext)
    dest.write_bytes(content)
    return dest


class SheetIdsRequest(BaseModel):
    """M1: 角色表排序——body 传完整 id 数组（新顺序即数组顺序；from/to_index 下标寻址退役）。"""

    ids: list[str] = Field(..., min_length=1)


class StyleAnchorGenerateRequest(BaseModel):
    """C6 入口①：AI 生成风格样图（出图 → 预览 → 满意固化，失败可重试、重生不限次）。

    include_people（2026-08-30 方案 B）：是否允许画面出现人物。默认 False —— 风格图
    的职责是定风格，人物长相由角色三视图管；不勾选时剥离预设人物词并追加无人物
    硬约束，勾选时保留预设原样（人物仅作氛围参考）。
    """

    prompt: str
    # bool | None（非 bool）：pydantic v2 对非 Optional 字段显式传 null 会 422，
    # 宽松接受 null 并视为 False（与 key 缺省同语义）。
    include_people: bool | None = None


@router.post("/projects/{project_id}/style-anchor-image/generate")
async def generate_style_anchor_image_endpoint(request: Request, project_id: str, body: StyleAnchorGenerateRequest):
    """AI 生成风格样图 → 落盘 candidates/（**不写项目字段**，固化走 POST set 端点）。

    prompt = 项目风格前缀（visual_style_preset 查表，无 preset 用通用描述）+ 用户补充
    描述；方案 B（2026-08-30）：默认（include_people 缺省/null/False）用去人物变体前缀
    并在末尾追加 NO_PEOPLE_DIRECTIVE 无人物硬约束；True 时预设原样保留。生成走
    ``make_ai_image(None)``——None = 默认链（Throttle+Fallback 可降级）。未固化候选的
    孤儿文件定期清理不在本计划内（已知少量，决策记录）。
    """
    store = _get_project_store(request)
    user_id = get_effective_user_id()
    proj = store.get(project_id, user_id=user_id)
    if not proj:
        raise HTTPException(status_code=404, detail="Project not found")
    svc = _get_service(request)

    from app.gateway.video_gen.style_presets import NO_PEOPLE_DIRECTIVE, style_prefix_for

    include_people = bool(body.include_people)
    preset = getattr(proj, "visual_style_preset", "")
    prefix = (style_prefix_for(preset, no_people=not include_people) or "").strip() or "高清风格参考图"
    if include_people:
        # 方案 B：勾选「包含人物」→ 预设原样保留（人物仅作氛围参考，外观仍以三视图为准）
        full_prompt = f"{prefix}，{body.prompt}" if body.prompt.strip() else prefix
    else:
        # 默认：追加无人物硬约束（放用户输入之后，盖过输入里可能出现的带人物倾向描述）
        parts = [p for p in (prefix, body.prompt.strip(), NO_PEOPLE_DIRECTIVE) if p]
        full_prompt = "，".join(parts)

    # B3 补全（2026-08-31）：落盘与生成同收编到 502 契约——畸形 data URI（无逗号）、
    # 坏 base64、下载 403/断连、ENOSPC 都会从 _write_image_ref_to_path 冒出，裸 500
    # 对用户不可操作；与 provider 失败同一句「生成失败，请重试」。
    try:
        provider = svc._providers.make_ai_image(None)
        image_ref = await provider.generate_image(full_prompt)
        cand_dir = _style_anchor_base_dir(svc._storage_dir, project_id) / "candidates"
        dest = cand_dir / _style_anchor_candidate_name()
        written = await _write_image_ref_to_path(image_ref, dest)
    except Exception as e:
        logger.warning("style anchor generate failed for %s: %s", project_id, e)
        # T5（2026-08-31 候选持久化）：生成失败清候选字段——防挂载时脏恢复出一张
        # 用户已放弃/过期的旧候选（与「固化/移除清字段」同一防脏恢复纪律）。
        try:
            store.update(project_id, user_id=user_id, style_anchor_candidate=None)
        except Exception:
            logger.warning("style anchor candidate clear-on-fail failed for %s", project_id, exc_info=True)
        logger.warning("style anchor generation failed: %s", e)
        raise HTTPException(status_code=502, detail="风格图生成失败，请重试（原因见服务端日志）") from e

    # T5（2026-08-31 候选持久化）：候选文件名落项目字段——修前候选是前端易失
    # state，退出/刷新/生成期间切走页面即丢（组件卸载把 mutate 的 onSuccess 静默
    # 丢弃，「生成后无法保存」事故三层根因之 1/3）。固化走 set 端点时清字段。
    store.update(project_id, user_id=user_id, style_anchor_candidate=written.name)

    return {
        "image_url": f"/api/video/projects/{project_id}/style-anchor-image?candidate={written.name}",
        "candidate_path": str(written),
    }


@router.post("/projects/{project_id}/style-anchor-image")
async def set_style_anchor_image_endpoint(request: Request, project_id: str):
    """固化候选（JSON ``{candidate_path}``）或手动上传（multipart ``file``）→ 写项目字段。

    替换语义（B10 新资产清理纪律）：替换/删除同步清理旧磁盘文件（三视图现状不动）。
    candidate_path 必须位于本项目 style_anchor/{pid}/candidates/ 之下（防 path traversal）。
    """
    store = _get_project_store(request)
    user_id = get_effective_user_id()
    proj = store.get(project_id, user_id=user_id)
    if not proj:
        raise HTTPException(status_code=404, detail="Project not found")
    svc = _get_service(request)
    base = _style_anchor_base_dir(svc._storage_dir, project_id)
    base.mkdir(parents=True, exist_ok=True)

    content_type = (request.headers.get("content-type") or "").lower()
    if "multipart/form-data" in content_type:
        form = await request.form()
        upload = form.get("file")
        if upload is None or not hasattr(upload, "read"):
            raise HTTPException(status_code=422, detail="multipart 上传需要 'file' 字段")
        data = await upload.read()
        if not data:
            raise HTTPException(status_code=422, detail="空文件")
        # SEC-M7（批次 2 项 2.6）+ B3 补全：大小上限 + 魔数嗅探改「拒绝」语义
        # （识别不了 → 400，不再静默保留原后缀落盘任意 bytes）。
        from app.gateway.video_gen.upload_validation import validate_image_bytes

        try:
            ext = validate_image_bytes(data, label="style anchor")
        except ValueError as e:
            logger.warning("SEC-M12: style anchor upload rejected (project=%s): %s", project_id, e)
            raise HTTPException(status_code=400, detail="Invalid style anchor image (unsupported format or too large)") from e
        dest = base / f"anchor_{_style_anchor_candidate_name()}"
        if ext != dest.suffix:
            dest = dest.with_suffix(ext)
        dest.write_bytes(data)
        new_path = str(dest)
    else:
        try:
            body = await request.json()
        except Exception:
            body = None
        candidate_path = (body or {}).get("candidate_path") if isinstance(body, dict) else None
        cand_base = (base / "candidates").resolve()
        if candidate_path:
            cand = Path(candidate_path).resolve()
            if cand_base not in cand.parents or not cand.exists():
                raise HTTPException(status_code=404, detail="candidate not found（必须位于本项目 candidates/ 下）")
            new_path = str(cand)
        else:
            # T5（2026-08-31 候选持久化）：恢复态固化只持有文件名（style_anchor_
            # candidate 字段口径）——按名解析到 candidates/ 下，同受 traversal 守卫。
            candidate_name = (body or {}).get("candidate") if isinstance(body, dict) else None
            cand = (cand_base / str(candidate_name or "")).resolve()
            if not candidate_name or cand_base not in cand.parents or not cand.exists():
                raise HTTPException(status_code=404, detail="candidate not found（需要 candidate_path / candidate / multipart file 之一）")
            new_path = str(cand)

    _delete_style_anchor_file(proj, base, keep=new_path)
    # T5：固化即清候选字段（候选文件就地成为锚图，字段不得残留——否则下次挂载
    # 又恢复出一张「已固化」的候选条）。
    proj = store.update(project_id, user_id=user_id, style_anchor_image=new_path, style_anchor_candidate=None)
    return proj.to_dict()


@router.delete("/projects/{project_id}/style-anchor-candidate")
async def clear_style_anchor_candidate_endpoint(request: Request, project_id: str):
    """T5（2026-08-31 候选持久化）：弃用未固化候选（前端「换一张」）→ 清字段 + 删候选文件。

    防脏恢复闭环：只清本地 state 不清字段的话，退出重进会把用户已弃的候选复活。
    幂等（字段空/文件不存在 → 200 no-op）。traversal 守卫同 set 端点（仅删本项目
    candidates/ 之下）。
    """
    store = _get_project_store(request)
    user_id = get_effective_user_id()
    proj = store.get(project_id, user_id=user_id)
    if not proj:
        raise HTTPException(status_code=404, detail="Project not found")
    svc = _get_service(request)
    name = getattr(proj, "style_anchor_candidate", None)
    if name:
        cand_base = (_style_anchor_base_dir(svc._storage_dir, project_id) / "candidates").resolve()
        cand = (cand_base / str(name)).resolve()
        if cand_base in cand.parents and cand.exists():
            try:
                cand.unlink()
            except OSError:
                logger.warning("style anchor candidate file cleanup failed: %s", name, exc_info=True)
    proj = store.update(project_id, user_id=user_id, style_anchor_candidate=None)
    return proj.to_dict()


def _delete_style_anchor_file(proj, base: Path, *, keep: str | None = None) -> None:
    """替换/删除时清理旧锚图磁盘文件——仅当旧路径确实位于本项目 style_anchor/{pid}/ 下
    （防恶意 PUT 写入任意路径后经本端点变成任意文件删除）。"""
    old = getattr(proj, "style_anchor_image", None)
    if not old:
        return
    old_p = Path(old)
    try:
        if base.resolve() not in old_p.resolve().parents:
            return
        if keep and old_p.resolve() == Path(keep).resolve():
            return
        if old_p.exists():
            old_p.unlink()
    except OSError:
        logger.warning("style anchor old file cleanup failed: %s", old, exc_info=True)


@router.get("/projects/{project_id}/style-anchor-image")
async def serve_style_anchor_image(request: Request, project_id: str, candidate: str | None = None):
    """serve 当前锚图（无锚图 404）；``?candidate=`` 预览候选图（生成对话框用）。

    防 path traversal：resolved 路径必须在 style_anchor/{pid} 之下（锚图字段是可经
    PUT 写入的持久化串，serve/清理前必须校验——对齐 sheet serve 的 E4 纪律）。
    """
    store = _get_project_store(request)
    user_id = get_effective_user_id()
    proj = store.get(project_id, user_id=user_id)
    if not proj:
        raise HTTPException(status_code=404, detail="Project not found")
    svc = _get_service(request)
    base = _style_anchor_base_dir(svc._storage_dir, project_id).resolve()

    if candidate is not None:
        cand = (base / "candidates" / candidate).resolve()
        if base not in cand.parents or not cand.exists():
            raise HTTPException(status_code=404, detail="candidate not found")
        # B3 补全：候选可能是 .jpg/.webp（data URI/上传按真实格式改后缀落盘）——
        # 不写死 media_type，Starlette 按后缀猜，JPEG 不再被标成 image/png。
        return FileResponse(cand)

    anchor = getattr(proj, "style_anchor_image", None)
    if not anchor:
        raise HTTPException(status_code=404, detail="No style anchor image")
    path = Path(anchor).resolve()
    if base not in path.parents or not path.exists():
        raise HTTPException(status_code=404, detail="style anchor file not found")
    return FileResponse(path)


# --- M6（决策 12/15/16/18）：道具资产管理——仅手动上传，无 AI 生成入口 ---


def _prop_base_dir(storage_dir, project_id: str) -> Path:
    # 目录拍死（B6 口径）：props/{pid}/{prop_id}/——无 user 维度（与 C6 锚图同构）
    return Path(storage_dir) / "props" / project_id


async def _save_prop_upload(data: bytes, dest: Path) -> Path:
    """落盘上传 bytes；返回**实际写入**路径——魔数嗅探校正后缀（B3 补全 2026-08-31：
    实拍道具多为 JPEG，写死 .png 会让 serve Content-Type/下载扩展名全错）。

    SEC-M7（批次 2 项 2.6）：嗅探改「拒绝」语义——识别不了的 bytes 直接 400，
    不再静默落盘；同时补每张 10MB 上限。"""
    from app.gateway.video_gen.upload_validation import validate_image_bytes

    try:
        ext = validate_image_bytes(data, label="prop image")
    except ValueError as e:
        logger.warning("SEC-M12: prop upload rejected: %s", e)
        raise HTTPException(status_code=400, detail="Invalid prop image (unsupported format or too large)") from e
    dest.parent.mkdir(parents=True, exist_ok=True)
    if ext != dest.suffix:
        dest = dest.with_suffix(ext)
    dest.write_bytes(data)
    return dest


@router.post("/projects/{project_id}/props/upload")
async def upload_prop_asset_endpoint(
    request: Request,
    project_id: str,
    name: str = Form(...),
    description: str = Form(""),
    main_image: UploadFile = File(...),
    extra_images: list[UploadFile] | None = File(None),
):
    """M6 入口（唯一）：上传道具——主图 1（必填）+ 补充角度 0~2（仅实拍，决策 16）。

    校验：extra > 2 → 422；name 唯一（strip + 大小写不敏感，同 M1 纪律）→ 409。
    不提供 AI 生成入口（决策 15/16：实物外观必须来自真实照片，AI 会虚构产品）。
    """
    store = _get_project_store(request)
    user_id = get_effective_user_id()
    proj = store.get(project_id, user_id=user_id)
    if not proj:
        raise HTTPException(status_code=404, detail="Project not found")
    svc = _get_service(request)
    extras = extra_images or []
    if len(extras) > 2:
        raise HTTPException(status_code=422, detail="补充角度最多 2 张（主图 1 + 补角度 0~2）")

    import uuid

    new_key = _normalize_sheet_name(name)
    for pa in proj.prop_assets:
        if _normalize_sheet_name(pa.name) == new_key:
            raise HTTPException(status_code=409, detail="道具名已存在")

    prop_id = uuid.uuid4().hex[:8]
    prop_dir = _prop_base_dir(svc._storage_dir, project_id) / prop_id
    main_path = await _save_prop_upload(await main_image.read(), prop_dir / "main.png")
    extra_paths = []
    for i, up in enumerate(extras):
        p = await _save_prop_upload(await up.read(), prop_dir / f"extra{i}.png")
        extra_paths.append(str(p))

    from app.gateway.video_gen.course_models import PropAsset

    prop = PropAsset(id=prop_id, name=name.strip(), main_image=str(main_path), extra_images=extra_paths, description=description)
    store.update(project_id, user_id=user_id, prop_assets=[pa.to_dict() for pa in [*proj.prop_assets, prop]])
    return prop.to_dict()


@router.delete("/projects/{project_id}/props/{prop_id}")
async def delete_prop_asset_endpoint(request: Request, project_id: str, prop_id: str):
    """删除道具：字段清除 + 磁盘目录同步清理（新资产纪律，B10）。"""
    store = _get_project_store(request)
    user_id = get_effective_user_id()
    proj = store.get(project_id, user_id=user_id)
    if not proj:
        raise HTTPException(status_code=404, detail="Project not found")
    idx = next((i for i, pa in enumerate(proj.prop_assets) if pa.id == prop_id), None)
    if idx is None:
        raise HTTPException(status_code=404, detail=f"prop {prop_id} not found")
    removed = proj.prop_assets[idx]
    svc = _get_service(request)
    prop_dir = _prop_base_dir(svc._storage_dir, project_id) / prop_id
    store.update(project_id, user_id=user_id, prop_assets=[pa.to_dict() for pa in proj.prop_assets[:idx] + proj.prop_assets[idx + 1 :]])
    # 目录拍死形态下按 id 删整目录（traversal 天然安全——prop_id 仅服务端生成 hex）
    import shutil

    if prop_dir.exists():
        shutil.rmtree(prop_dir, ignore_errors=True)
    return {"status": "deleted", "prop": removed.to_dict()}


@router.get("/projects/{project_id}/props/{prop_id}/{tag}")
async def serve_prop_asset_endpoint(request: Request, project_id: str, prop_id: str, tag: str):
    """serve 道具图（tag ∈ main/extra0/extra1；复用 sheet serve 模式 + traversal 守卫）。"""
    if tag not in {"main", "extra0", "extra1"}:
        raise HTTPException(status_code=404, detail=f"prop image tag {tag} not found")
    store = _get_project_store(request)
    user_id = get_effective_user_id()
    proj = store.get(project_id, user_id=user_id)
    if not proj:
        raise HTTPException(status_code=404, detail="Project not found")
    prop = next((pa for pa in proj.prop_assets if pa.id == prop_id), None)
    if prop is None:
        raise HTTPException(status_code=404, detail=f"prop {prop_id} not found")
    rel = prop.main_image if tag == "main" else (prop.extra_images[int(tag[5:])] if int(tag[5:]) < len(prop.extra_images) else "")
    if not rel:
        raise HTTPException(status_code=404, detail=f"prop {prop_id} has no {tag}")
    svc = _get_service(request)
    path = Path(rel).resolve()
    base = _prop_base_dir(svc._storage_dir, project_id).resolve()
    if base not in path.parents or not path.exists():
        raise HTTPException(status_code=404, detail="prop image not found")
    # 后缀已按真实格式落盘（_save_prop_upload 嗅探）——不写死 media_type，按后缀猜
    return FileResponse(path)


@router.delete("/projects/{project_id}/style-anchor-image")
async def delete_style_anchor_image_endpoint(request: Request, project_id: str):
    """清字段 + 删磁盘文件（新资产清理纪律；决策 4 双入口共用本端点收尾）。"""
    store = _get_project_store(request)
    user_id = get_effective_user_id()
    proj = store.get(project_id, user_id=user_id)
    if not proj:
        raise HTTPException(status_code=404, detail="Project not found")
    svc = _get_service(request)
    _delete_style_anchor_file(proj, _style_anchor_base_dir(svc._storage_dir, project_id))
    # T5（2026-08-31 候选持久化）：移除锚图同清候选字段（防脏恢复：删完锚图再进来
    # 不应凭空恢复出旧候选条）。
    proj = store.update(project_id, user_id=user_id, style_anchor_image="", style_anchor_candidate=None)
    return proj.to_dict()


def _resolve_sheet_image_path(proj, sheet_id: str, tag: str) -> Path:
    """Bug 1 + M1: 按 sheet 稳定 id + tag 解析该 sheet 存储的图片 FS 路径（供 FileResponse）。

    X11-2（2026-08-27 计划）：tag 白名单扩 ``composite``（三视图合成图缩略图 serve，
    同构既有 by-id 端点——traversal 守卫/归属校验零变化）。
    """
    if tag not in {"front", "side", "back", "composite"}:
        raise HTTPException(status_code=404, detail=f"character sheet {tag} not found")
    sheet = next((s for s in proj.character_sheets if s.id == sheet_id), None)
    if sheet is None:
        raise HTTPException(status_code=404, detail=f"character sheet {sheet_id} not found")
    url = getattr(sheet, f"{tag}_url", "")
    if not url:
        raise HTTPException(status_code=404, detail=f"sheet {sheet_id} has no {tag}_url")
    return Path(url)


@router.get("/projects/{project_id}/character-sheets/{sheet_id}/{tag}")
async def serve_character_sheet_by_id(request: Request, project_id: str, sheet_id: str, tag: str):
    """Bug 1 + M1: 按稳定 id 服务该 sheet 的 {tag} 图（修 serve_character_sheet 只能取最新、
    多表同图；数组下标寻址退役——下标随删除/排序漂移会串图）。"""
    store = _get_project_store(request)
    user_id = get_effective_user_id()
    proj = store.get(project_id, user_id=user_id)
    if not proj:
        raise HTTPException(status_code=404, detail="Project not found")
    path = _resolve_sheet_image_path(proj, sheet_id, tag)
    # 防 path traversal：resolved 路径必须在 storage/character_sheets 之下（front_url 来自持久化串）
    svc = _get_service(request)
    base = (Path(svc._storage_dir) / "character_sheets").resolve()
    resolved = path.resolve()
    if base not in resolved.parents and resolved != base:
        raise HTTPException(status_code=404, detail="character sheet path outside storage")
    if not resolved.exists():
        raise HTTPException(status_code=404, detail="character sheet file not found on disk")
    return FileResponse(resolved, media_type="image/png")


@router.post("/tasks/{task_id}/vimax-shots/{shot_id}/redo")
async def redo_vimax_shot_endpoint(request: Request, task_id: str, shot_id: int):
    """Task 16 + T2（Lane 2, 2026-10-15 补全）：ViMax 生成后逐镜重做。

    用 shot prompt + 项目角色 front + 相邻镜关键帧重新生成单镜画面，替换原片段，
    重拼成片并回写 task result。参考图被 360 内容审核拦截时按用户拍板决策①降级
    （首帧参考 → 纯 prompt）并在 result 留痕。重活在 service.redo_vimax_shot →
    engine.redo_vimax_shot_task；本端点只做守卫与码映射。

    T5（2026-10-16 收口批）：非 404 异常也映射为可读错误——他镜视频缺失（退化态
    重拼不可行）→ 422；其余（provider/下载/ffmpeg）→ 500 带原因摘要，不裸 500。
    """
    from app.gateway.video_gen.engines.vimax.engine import VimaxShotNotFound, VimaxShotVideoMissing

    svc = _get_service(request)
    user_id = get_effective_user_id()
    task = await svc.get_task(task_id, user_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.task_type != "vimax":
        raise HTTPException(status_code=400, detail="Shot redo only supports vimax tasks")
    try:
        return await svc.redo_vimax_shot(task_id=task_id, user_id=user_id, shot_id=shot_id)
    except VimaxShotNotFound:
        raise HTTPException(status_code=404, detail=f"Shot {shot_id} not found in task products")
    except VimaxShotVideoMissing as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 —— 端点边界：用户可读的错误码映射
        raise HTTPException(status_code=500, detail=f"vimax 单镜重做失败（{type(exc).__name__}: {str(exc)[:160]}），请稍后重试或重新渲染任务") from exc
