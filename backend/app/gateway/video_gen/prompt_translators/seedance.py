"""Seedance 2.0 方言翻译器（seedance-ark 直连 + seedance 360 通道同用）：中文五槽。

结构（官方五槽线性 + 位置递减服从率 -> 前置优先）：
  [参考图分工指代]（reference_count>0 前置）-> 主体+动作 -> 场景环境（含光影色调）
  -> 景别+运镜（官方术语，一镜一运镜）-> 视觉风格 -> 约束尾（无字幕/无水印/无文字）。
"""

from __future__ import annotations

from . import vocab
from .legacy import translate as _legacy


def translate(frame, *, reference_count: int = 0, reference_kind: str | None = None, entity_names: list[str] | None = None, prop_count: int = 0) -> str:  # noqa: ANN001
    ir = frame.visual_ir
    if not ir:
        return _legacy(frame)  # 空 IR 防御性降级（路由层已挡，双保险）
    # M4：entity_names 仅在与 refs 1:1 对应时由上层传入（帧级声明实体，每实体 1 图）。
    # X8：prop_count（末尾道具条目数，条件传参——缺省 0 全按角色，窄签名兼容）。
    ref = vocab.seedance_reference_injection(reference_count, reference_kind, entity_names, prop_count)
    subject = str(ir.get("subject") or "").strip()
    action = str(ir.get("action") or "").strip()
    environment = str(ir.get("environment") or "").strip()
    style = str(ir.get("style") or "").strip()
    if not any((subject, action, environment, style)):
        return _legacy(frame)  # IR 全空字段 -> 不可翻译，legacy 透传
    slots: list[str] = []
    if ref:
        slots.append(ref)
    if subject or action:
        slots.append("，".join(p for p in (subject, action) if p))
    if environment:
        slots.append(environment)
    shot = vocab.SEEDANCE_SHOT_TERMS.get(frame.shot_size or "")
    cam = vocab.SEEDANCE_CAMERA_TERMS.get(frame.camera_move or "")
    lens = "，".join(p for p in (shot, cam) if p)
    if lens:
        slots.append(lens)
    if style:
        slots.append(style)
    return "。".join(slots) + "。" + vocab.SEEDANCE_CONSTRAINT_TAIL
