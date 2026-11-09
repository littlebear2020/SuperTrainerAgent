"""共享降级留痕 sidecar（Task 23：C11 / C13 / C6 三条静默降级）。

与 Task 10 的 ``placeholder_frames.json`` 并列但**互补**：那个只记「哪些帧变成了
占位」（同质、纯帧号集合），这个记异质降级事件——BGM 没混上、逐镜对齐失败、台词
被硬截尾。共同点是**功能降级了却不告诉用户**：修前三处都只写 logger，用户看到的
是一个 status=ready / progress=100 的「成功」任务，与完美渲染不可区分。

设计取舍：
- **只 append 不覆盖**：一次渲染里可能同时发生多种降级，且分属不同阶段（worker
  混音期 vs ViMax 旁白生成期），没有单一写入点能一次收齐。
- **同 kind+frame 去重**：重试路径会反复走到同一降级分支，不去重会刷屏。
- **所有失败都吞**：留痕机制本身绝不能成为新的故障源——写不进去就只剩 log，
  但主渲染流程必须继续。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEGRADATION_SIDECAR_NAME = "degradations.json"

# 已知降级类型（仅作文档用途，不做枚举校验——新增 kind 不该被这里卡住）
KIND_BGM_FAILED = "bgm_failed"
KIND_ALIGN_FAILED = "align_failed"
KIND_DIALOGUE_TRUNCATED = "dialogue_truncated"
# 决策 3（2026-07-31）：音色降级——豆包配额耗尽/限流/传输错误时静默换成备用音色，
# 用户听得出中途换声却看不到任何解释。降级语义保持不变，只补留痕。
KIND_VOICE_FALLBACK = "voice_fallback"
KIND_VISUAL_SHORTFALL = "visual_shortfall"  # Task 4: 模型产出短于旁白槽位，尾部定格补齐
KIND_PREVIEW_I2V_UNSUPPORTED = "preview_i2v_unsupported"  # Task 5: provider 无 i2v，预览图未作首帧
KIND_PROMPT_NEUTRALIZED = "prompt_neutralized"  # Task 12: 内容审核拒绝后提示词被中性化改写重试
# P1（2026-08-19）：审核误判但词表无可中性化 → 改用旁白直述 prompt 重试
KIND_PROMPT_FALLBACK = "prompt_fallback_narration"
KIND_VIDEO_EXTENDED = "video_extended"  # Phase 2: 画面短于旁白，已自动延长（续写 i2v）
KIND_FRAME_SPLIT = "frame_split"  # Phase 3: 超长旁白帧已自动拆分为多帧
KIND_LOW_VISUAL_QUALITY = "low_visual_quality"  # T-Q1(Phase 4): 黑帧/冻结检测命中--数据收集先行(只记录不重生,D-D)
# content-match（2026-08-14）：graphic spec 拒绝留痕。
# graphic-native-text（2026-08-17）：Pillow 渲染器退役，KIND_GRAPHIC_RENDER_FAILED 一并移除
# （存量 sidecar 里出现该 kind 由读取侧兜底文案处理，无害）。
KIND_GRAPHIC_SPEC_REJECTED = "graphic_spec_rejected"  # spec 幻觉被事实校验层拒绝 → 帧降级主引擎
# 2026-09-02（标题截断修复，产品拍板）：spec 有效但标题 >14 字/标签 >6 字被**字段级
# 省略**（卡片照出、帧不降级）——留痕告知用户「该镜部分文字因超长未显示」，
# 与 rejected（整帧降级）是两级语义，观测时分开统计省略率。
KIND_GRAPHIC_SPEC_FIELD_OMITTED = "graphic_spec_field_omitted"
# 2026-08-19：上游图片内容检测（10940，多为隐私过滤器误判）拦预览图/组锚图
# → 丢图降级纯 t2v 重试出片（sidecar detail 区分 preview/group_anchor 两来源）。
KIND_PREVIEW_IMAGE_REJECTED = "preview_image_rejected"
# 2026-08-20：character 被拦不丢图不重试、直接占位——留痕可行动指引
# （更换角色形象后可重试本镜）。
KIND_CHARACTER_IMAGE_REJECTED = "character_image_rejected"
# 条目 7（2026-09-12 缺陷批）：撤册模型名两处留痕——config 级名（subtitle/
# shot_table/vimax）建单时花名册未命中（任务继续走各自回退链，须可见）；字幕
# LLM 运行时构造失败回退规则链（config.py 明写「不得静默退回规则链」）。
KIND_MODEL_ROSTER_MISS = "model_roster_miss"
# D2 修复批（2026-09-23）：gpt-image-2.5 系请求尺寸不在实测白名单（如
# remotion_course 的 800x640/540x675 槽位）→ 就近贴档生成。留痕口径同
# visual_shortfall：功能上出了图但与请求规格有偏差，用户应可见。
KIND_IMAGE_SIZE_MAPPED = "image_size_mapped"
KIND_SUBTITLE_LLM_FALLBACK = "subtitle_llm_fallback"
# 2026-10-14 vimax 配音修复批：T4 无标签音效启发式命中（防误杀旁白的审计位）；
# T6 配音格式漂移守门（未识别 [...] 前缀标签，detail 含样本前 80 字）。
KIND_SFX_HEURISTIC = "sfx_heuristic"
KIND_FORMAT_DRIFT = "format_drift"


def read_degradations(task_dir: Path | str) -> list[dict[str, Any]]:
    """读出降级记录；文件缺失或损坏一律返回空表。

    坏 JSON 不抛——详情接口读它，不能因为一个 sidecar 损坏就把整个任务详情打成 500。
    """
    path = Path(task_dir) / DEGRADATION_SIDECAR_NAME
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    items = raw.get("items") if isinstance(raw, dict) else None
    if not isinstance(items, list):
        return []
    return [i for i in items if isinstance(i, dict) and i.get("kind")]


def record_degradation(
    task_dir: Path | str,
    *,
    kind: str,
    detail: str,
    frame_index: int | None = None,
    requested_voice: str | None = None,
    actual_voice: str | None = None,
) -> None:
    """追加一条降级记录（同 kind+frame_index 幂等）。

    绝不抛异常：调用点全部位于「已经出错、正在降级继续」的分支里，再抛一次
    只会把可恢复的降级升级成整任务失败。
    """
    try:
        path = Path(task_dir) / DEGRADATION_SIDECAR_NAME
        items = read_degradations(path.parent)
        if any(i.get("kind") == kind and i.get("frame_index") == frame_index for i in items):
            return
        entry: dict[str, Any] = {"kind": kind, "detail": str(detail)[:500]}
        if frame_index is not None:
            entry["frame_index"] = frame_index
        # Plan B（2026-10-19）：音色降级的结构化字段——draft storyboard 的
        # audio_degradation_warning 只消费「带 frame_index 的归位记录」（attempt
        # 记录无 frame_index，不得当成功降级提示）。
        if requested_voice is not None:
            entry["requested_voice"] = str(requested_voice)
        if actual_voice is not None:
            entry["actual_voice"] = str(actual_voice)
        items.append(entry)
        path.write_text(json.dumps({"items": items}, ensure_ascii=False), encoding="utf-8")
    except Exception:  # noqa: BLE001
        logger.warning("record degradation %s failed", kind, exc_info=True)
