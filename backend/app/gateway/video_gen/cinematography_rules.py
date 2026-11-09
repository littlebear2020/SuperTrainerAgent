"""T-C1 摄影规则库（Phase 2，2026-08-17）--指令级（D-C 拍板：先教 AI，字段级留 v3）。

「知识即数据」：规则在此拼装为指令文本，由 planner 注入 system prompt（T-C2），
不硬编码进提示词模板。只收 Phase 0 验证过的条目：
- 景别↔景深联动（waowaoo 实证表）；
- 主体规避：手部=慎用非禁用（用户实看 E1-B 纠正社区经验）、无参考图禁正脸特写、
  禁多物体精密咬合交互（morphing 重灾区）；
- 时长节拍（E1「一镜一拍」验证）；
- 灯光/材质词域由 STYLE_PREFIXES 五风格词表承担（材质一致性：风格前缀自带材质词，
  planner 要求正文材质词与前缀一致，不再打架）。
"""

from __future__ import annotations

# 景别 -> 景深描述（写进 environment 产出要求，与 shot_size 枚举联动）。
SHOT_DOF_TERMS: dict[str, str] = {
    "wide": "深景深，清晰展现整体空间",
    "medium": "中等景深，主体清晰背景略虚",
    "close": "浅景深，背景明显虚化",
}

SUBJECT_AVOIDANCE_RULES = "主体规避：优先选择无手部的画面方案（手部操作非必要不用）；无角色参考图时不写正脸特写；避免多个物体精密咬合/拼装的交互动作"

DURATION_BEAT_RULES = "时长节拍：单镜动作 ≤5 秒严格一拍；5-8 秒不超过两拍；超过 8 秒最多两拍"


def cinematography_instruction() -> str:
    """拼装摄影规则指令块（T-C2 消费：ai_video 任务的 system prompt）。"""
    dof_table = "；".join(f"{k}={v}" for k, v in SHOT_DOF_TERMS.items())
    return f"摄影规则（须遵守）：景别与景深联动（{dof_table}）；{SUBJECT_AVOIDANCE_RULES}；{DURATION_BEAT_RULES}。environment 字段须包含光源方向与质感，并写出与景别匹配的景深描述。"
