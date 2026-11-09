"""H3（MiniMax）方言翻译器：中文三字段 + [Shot] 时间戳（D-B：中文直出）。

结构（官方三字段，字段名保持 wire 契约、内容中文）：
  integrated_multimodal_description: 主体/动作/环境/风格 + 景别 + 运镜三维语法
    + [Shot N] At <mm:ss.mmm> 切点（≤8s 单 Shot；>8s 两 Shot，切点严格递增）
  overall_soundscape: 一句动作耦合音效（D-A；IR 缺 audio -> N/A）
  non_diegetic_music: N/A（BGM 由管线混；音频整轨在合成时丢弃）
公共不变量：无台词/对白（H3_CONSTRAINT_TAIL）。
"""

from __future__ import annotations

from . import vocab
from .legacy import translate as _legacy


def _ts(seconds: float) -> str:
    m, s = divmod(max(seconds, 0.0), 60)
    return f"{int(m):02d}:{s:06.3f}"


def _description(frame) -> str:  # noqa: ANN001
    ir = frame.visual_ir or {}
    parts = [str(ir.get(k) or "").strip() for k in ("subject", "action", "environment", "style")]
    shot = vocab.H3_SHOT_TERMS.get(frame.shot_size or "")
    cam = vocab.H3_CAMERA_TERMS.get(frame.camera_move or "")
    if shot:
        parts.append(shot)
    if cam:
        parts.append(cam)
    return "。".join(p for p in parts if p)


def translate(frame, *, reference_count: int = 0, reference_kind: str | None = None, entity_names: list[str] | None = None, prop_count: int = 0) -> str:  # noqa: ANN001
    ir = frame.visual_ir
    if not ir:
        return _legacy(frame)  # 空 IR 防御性降级（路由层已挡，双保险）
    desc = _description(frame)
    if not desc:
        return _legacy(frame)  # IR 全空字段 -> 不可翻译，legacy 透传
    dur = float(frame.duration or 0.0)
    if dur > 8.0:
        mid = round(dur / 2, 3)
        desc = f"[Shot 1] At {_ts(0)}，{desc}。[Shot 2] At {_ts(mid)}，镜头延续，主体完成动作的后续阶段，画面保持连贯。"
    else:
        desc = f"[Shot 1] At {_ts(0)}，{desc}。"
    # r2v（2026-09-04 计划 S3）：参考图指代声明前置进 integrated_multimodal_
    # description 头部（r2v 参考图随 content[] 全量下发，声明与实际下发图集
    # 一致）；首帧语义 kind / 无参考图 → vocab 返回空串 → 输出与现状逐字节一致。
    injection = vocab.h3_reference_injection(reference_count, reference_kind, entity_names, prop_count)
    if injection:
        desc = f"{injection} {desc}"
    audio = str(ir.get("audio") or "").strip()
    soundscape = audio if audio else vocab.H3_MUSIC_NA
    return f"integrated_multimodal_description: {desc} {vocab.H3_CONSTRAINT_TAIL}\noverall_soundscape: {soundscape}\nnon_diegetic_music: {vocab.H3_MUSIC_NA}"
