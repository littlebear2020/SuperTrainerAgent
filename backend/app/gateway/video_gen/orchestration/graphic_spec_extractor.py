"""graphic spec 提取 LLM pass（content-match T7，2026-08-14）。

仅对 visual_source=="graphic" 的帧（T6 normalize 之后存活下来的）分块提取
结构化 spec，逐帧过 graphic_spec.validate_spec_against_narration 事实校验
（LLM 只提取、确定性代码核对，幻觉拒绝）。

失败语义（全部不拖垮规划）：
- 单帧 spec 幻觉/形状不合法 → spec 保持 None + KIND_GRAPHIC_SPEC_REJECTED sidecar
  （渲染期按常规画面出图；保底 text_card_from_narration 已于 2026-08-17 随
  Pillow 渲染器退役）
- 单块 LLM 异常/坏 JSON → 该块帧全部按缺失降级，下一块继续
- 总量预算封顶（_TOTAL_BUDGET，对齐 storyboard_planner._FALLBACK_TOTAL_BUDGET
  模式）——到点停止，剩余帧按缺失降级。规划阶段整体在 _wrap_pipeline 闸0 内，
  无租约顾虑（租约是 worker 阻塞 subprocess 的约束，gateway 编排不适用）。
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from ..degradations import KIND_GRAPHIC_SPEC_FIELD_OMITTED, KIND_GRAPHIC_SPEC_REJECTED, record_degradation
from ..storyboard import Storyboard
from ..visual_sources.graphic_spec import parse_graphic_spec, validate_spec_against_narration
from . import _invoke_llm_bounded

logger = logging.getLogger(__name__)

# 对齐 storyboard_planner.CHUNK=8 约定（不 import 以避免 planner↔extractor 循环依赖）
_CHUNK = 8
_TOTAL_BUDGET = 300.0  # s，对齐 _FALLBACK_TOTAL_BUDGET 模式
_PER_CHUNK_TIMEOUT = 120.0  # s，对齐 orchestration.DEFAULT_LLM_TIMEOUT

_SPEC_EXTRACT_SYSTEM = (
    "你是信息提取器。从每条旁白中提取 graphic 卡片的结构化数据。\n"
    "**硬规则 1：所有数字与关键措辞必须逐字符来自旁白/文稿原文，禁止编造、换算或改写任何数字。**\n"
    "**硬规则 2：text_card 的每条要点必须是短标签（≤6 个汉字；1 个英文单词折 2 个汉字，英文术语可直接用英文，禁止意译丢术语；关键词式，禁止照抄旁白整句）；至少 2 条要点。**\n"
    "每个帧输出一个 spec 对象，按旁白内容三选一：\n"
    '- {"type": "big_number", "number": "旁白中被强调的数字原文", "unit": "单位原文，可省略", "description": "≤60字说明，可省略"} —— 旁白强调单个关键数字/百分比/倍数\n'
    '- {"type": "steps", "title": "≤14字标题（中、英文字符各计 1 字，超出将不予显示），可省略", "steps": ["按原文提取，≤6步，每步短标签≤6 汉字（英文单词折 2 个汉字，超出将不予显示）"]} —— 旁白描述有先后顺序的流程\n'
    '- {"type": "text_card", "title": "≤14字标题（中、英文字符各计 1 字，超出将不予显示）", "points": ["按原文归纳，≤4条，每条短标签≤6 汉字（英文单词折 2 个汉字，超出将不予显示）"]} —— 多个并列要点/概念归纳\n'
    "选择规则：单个数字被强调→big_number；有明确先后步骤→steps；多个并列要点→text_card；拿不准→text_card。\n"
    '严格输出 JSON：{"specs": [{"index": 帧号, "spec": {...}}, ...]}。不要输出其他任何文字。'
)


def _parse_specs_payload(content: str) -> dict[int, dict]:
    """解析 {"specs":[{"index":N,"spec":{...}}]} → {帧号: raw spec}。坏输入返回 {}。"""
    json_str = content
    if "```" in json_str:
        json_str = json_str.split("```")[1]
        if json_str.startswith("json"):
            json_str = json_str[4:]
        json_str = json_str.split("```")[0]
    try:
        parsed = json.loads(json_str.strip())
        items = parsed.get("specs") if isinstance(parsed, dict) else None
        if not isinstance(items, list):
            return {}
        out: dict[int, dict] = {}
        for item in items:
            if isinstance(item, dict) and isinstance(item.get("index"), int) and isinstance(item.get("spec"), dict):
                out[item["index"]] = item["spec"]
        return out
    except (json.JSONDecodeError, AttributeError, TypeError, ValueError):
        return {}


async def extract_graphic_specs(llm, storyboard: Storyboard, *, task_dir: Path | None = None, protect: frozenset[int] | set[int] = frozenset()) -> int:  # noqa: ANN001
    """为 graphic 帧提取并校验 spec，写回 ``frame.graphic_spec``。返回成功帧数。

    ``protect``：plan_course 快照的规划前已存在 visual_source 帧（用户手改）——
    拒绝时不降级（维持 graphic 标记，渲染期无 spec 按常规画面出图）。非 protect
    帧拒绝/缺失 → **降级主引擎**（visual_source=None，D-A 2026-08-16 拍板：规划期
    不值得上卡的内容出 AI 画面；image_prompt 规划期已备好）。"""
    graphic_indices = [f.index for f in storyboard.frames if getattr(f, "visual_source", None) == "graphic"]
    if not graphic_indices:
        return 0  # 零 graphic 帧 → 零 LLM 调用（成本门槛）
    full_script = "\n".join(f.narration or "" for f in storyboard.frames)
    deadline = time.monotonic() + _TOTAL_BUDGET
    written = 0

    for start in range(0, len(graphic_indices), _CHUNK):
        if time.monotonic() >= deadline:
            logger.warning("graphic spec 提取总量预算 %.0fs 耗尽，剩余 %d 帧按缺失降级", _TOTAL_BUDGET, len(graphic_indices) - start)
            break
        batch = graphic_indices[start : start + _CHUNK]
        frames_payload = [{"index": fi, "narration": storyboard.frames[fi].narration or ""} for fi in batch]
        messages = [
            {"role": "system", "content": _SPEC_EXTRACT_SYSTEM},
            {"role": "user", "content": json.dumps({"title": storyboard.title, "full_script": full_script, "frames": frames_payload}, ensure_ascii=False, indent=2)},
        ]
        try:
            resp = await _invoke_llm_bounded(llm, messages, timeout=_PER_CHUNK_TIMEOUT)
            content = resp.content if hasattr(resp, "content") else str(resp)
            parsed = _parse_specs_payload(content)
        except Exception as e:  # noqa: BLE001 - 单块失败不拖垮整批；帧级按「缺失」降级
            logger.warning("graphic spec 块提取失败（帧 %s）：%s", batch, e)
            parsed = {}

        for fi in batch:
            frame = storyboard.frames[fi]
            raw = parsed.get(fi)
            if raw is None:
                _reject(frame, fi, "LLM 未返回该帧 spec", task_dir, protect)
                continue
            spec, issues = parse_graphic_spec(raw)
            if spec is None:
                # 2026-09-02：parse 现返回字段级原因，透传进 sidecar（不再写死串）
                _reject(frame, fi, "；".join(issues) or "spec 形状不合法", task_dir, protect)
                continue
            ok, reasons = validate_spec_against_narration(spec, frame.narration or "", full_script)
            if not ok:
                _reject(frame, fi, "；".join(reasons), task_dir, protect)
                continue
            frame.graphic_spec = spec
            written += 1
            if issues:
                # 字段级省略（2026-09-02 产品拍板）：spec 有效但标题 >14 字/标签 >6 字
                # 被省略——卡片照出、帧不降级，与 rejected 是两级；留痕供前端告知
                # 「部分文字未显示」并供观测省略率（高→单点调常量/查提示词遵从度）。
                logger.info("graphic spec 字段省略（帧 %d）：%s", fi, "；".join(issues))
                if task_dir is not None:
                    record_degradation(task_dir, kind=KIND_GRAPHIC_SPEC_FIELD_OMITTED, detail="；".join(issues), frame_index=fi)
    return written


def _reject(frame, frame_index: int, detail: str, task_dir: Path | None, protect) -> None:  # noqa: ANN001
    """拒绝处理（D-A）：非 protect 帧降级主引擎并留痕；protect 帧维持 graphic 标记留痕。"""
    if frame_index in protect:
        logger.info("graphic spec 拒绝（帧 %d，protect → 维持图文镜，渲染期按常规画面出图）：%s", frame_index, detail)
        if task_dir is not None:
            record_degradation(task_dir, kind=KIND_GRAPHIC_SPEC_REJECTED, detail=f"protect 帧维持图文镜（常规画面渲染）：{detail}", frame_index=frame_index)
        return
    frame.visual_source = None  # 降级主引擎（同 off 回滚语义；image_prompt 规划期已备好）
    logger.info("graphic spec 拒绝（帧 %d，已降级主引擎）：%s", frame_index, detail)
    if task_dir is not None:
        record_degradation(task_dir, kind=KIND_GRAPHIC_SPEC_REJECTED, detail=f"已降级主引擎：{detail}", frame_index=frame_index)
