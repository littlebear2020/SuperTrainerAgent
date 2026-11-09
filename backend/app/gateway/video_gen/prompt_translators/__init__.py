"""提示词方言层（Phase 1，2026-08-17）：同一份 IR，按 provider 方言序列化。

形态=A 纯代码翻译器（0 额外 LLM 调用/确定性/可离线测试）：
创造性决策（主体/动作/隐喻/音效）属 planner 的一次 LLM 调用（T2 产出 visual_ir），
方言翻译（槽序/符号/时间戳/术语映射）是机械且必须精确的，归代码。

路由（ai_video.fill 按 provider_name 分派）：
- minimax-h3        -> h3（中文三字段 + [Shot] 时间戳，D-B 拍板中文；2026-09-04
  r2v 起含参考图分工指代）
- seedance*（含 ark）-> seedance（中文五槽 + 参考图分工指代）
- 其余 / 无 IR / env=legacy -> legacy（现行 compose_video_prompt 透传）

env VIDEO_GEN_PROMPT_DIALECT = v2 | legacy。**默认 v2**（2026-08-18 Phase 5 盲评定稿：
H3 口径达标-美感 v2 71%+硬门槛过+客观质检零差；Seedance 零劣化；用户拍板切默认）。
legacy = 一键回滚位（设 VIDEO_GEN_PROMPT_DIALECT=legacy 恢复旧拼装行为）。
"""

from __future__ import annotations

import os

from . import h3, legacy, seedance

__all__ = ["prompt_dialect_mode", "translate_video_prompt", "translator_for"]


def prompt_dialect_mode() -> str:
    """v2 | legacy（默认 v2，2026-08-18 盲评定稿切默认；legacy 为回滚位）。"""
    return "legacy" if os.environ.get("VIDEO_GEN_PROMPT_DIALECT", "v2").strip().lower() == "legacy" else "v2"


def translator_for(provider_name: str):  # noqa: ANN201
    """按 provider 名选方言模块；未知 -> legacy（不猜）。"""
    n = (provider_name or "").strip().lower()
    if "minimax-h3" in n or n == "h3":
        return h3
    if n.startswith("seedance"):
        return seedance
    return legacy


def translate_video_prompt(frame, *, provider_name: str, reference_count: int = 0, reference_kind: str | None = None, entity_names: list[str] | None = None, prop_count: int = 0) -> str:  # noqa: ANN001
    """方言层唯一入口。

    不变量（P0-5）：无 visual_ir（旧 storyboard/手改/拆帧子帧）或 env=legacy 或未知
    provider -> legacy 透传（现行 compose_video_prompt 语义，逐字节不变）。

    entity_names（二期 M4 带名指代）：seedance 与 h3（2026-09-04 r2v 起）两方言
    消费（legacy 保持现状）；条件透传防窄签名方言崩。
    prop_count（X8 合成图方案）：entity_names 末尾道具条目数（角色/道具区分措辞）；
    seedance/h3 消费，条件透传（缺省 0 = 全按角色）。
    """
    if prompt_dialect_mode() != "v2":
        return legacy.translate(frame, reference_count=reference_count, reference_kind=reference_kind)
    if not getattr(frame, "visual_ir", None):
        return legacy.translate(frame, reference_count=reference_count, reference_kind=reference_kind)
    t = translator_for(provider_name)
    if entity_names and t is seedance:
        # X8：prop_count 条件传参（>0 才带——窄签名兼容 + 全角色路径零变化）
        return t.translate(frame, reference_count=reference_count, reference_kind=reference_kind, entity_names=entity_names, **({"prop_count": prop_count} if prop_count else {}))
    elif entity_names and t is h3:
        # 2026-09-04 r2v S3：H3 带名指代（seedance 行原样保留，两方言独立维护）；
        # prop_count 同规条件传参。窄签名保护保持：legacy 仍不透传。
        return t.translate(frame, reference_count=reference_count, reference_kind=reference_kind, entity_names=entity_names, **({"prop_count": prop_count} if prop_count else {}))
    return t.translate(frame, reference_count=reference_count, reference_kind=reference_kind)
