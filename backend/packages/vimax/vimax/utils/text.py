import re


def safe_path_component(name) -> str:
    """Sanitize an LLM-derived identifier for use as a filesystem path component.

    Identifiers come from model output over user-supplied story text, so they may
    contain separators or traversal sequences; keep word characters (including
    CJK), dashes, dots and spaces, replace everything else, and strip leading
    dots so the result can never escape or hide within the working directory.
    """
    cleaned = re.sub(r"[^\w\-. ]", "_", str(name))
    cleaned = cleaned.strip().lstrip(".")
    return cleaned or "unnamed"


# ── VENDOR PATCH (TrainerAI, Plan C 2026-10-19)：长脚本确定性分批与安全合并 ──
# 设计 docs/2026-10-17-vimax-reliability-optimization-design.md §3 计划 C（D6/D8）：
# 仅 script2video 全局镜头预算 >12 时拆批；批次循环在 artist tenacity 之外。

import math
from dataclasses import dataclass

from vimax.interfaces.shot_description import ShotBriefDescription

#: 全局预算 ≤12 保持单批（不改变短脚本行为）。
SINGLE_BATCH_SHOT_THRESHOLD = 12
#: 每批目标镜头上限（拆批时的 cap）。
STORYBOARD_BATCH_SHOT_CAP = 8

#: 批次缓存指纹的 prompt 语义版本（artist 批次提示词变更时递增作废旧缓存）。
_STORYBOARD_BATCH_PROMPT_VERSION = "3"  # 2026-10-20 边界热修复：纯空白不再独立成批（尾部/前导空白折叠），旧缓存可能含空白批须作废


def _json_dumps_compact(obj) -> str:
    import json as _json

    return _json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

# 边界收口（2026-10-19 P0）：CJK/全半角句末标点（。！？…；!?;）**不要求**后随
# 空白——中文书写无空格，通用空白前瞻会让中文长单段 units=1 静默退化单批
# （PR35 拆批收益对中文失效）。ASCII 句号保留 (?=\s|$) 前瞻防 "3.5" 小数误切。
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？…；;!?])|(?<=[.])(?=\s|$)")


@dataclass
class StoryboardBatchPlan:
    """一个批次的执行计划：原文片段 + 该批镜头预算上限。"""

    text: str
    shot_budget: int


def _fold_whitespace_units(units: list[str]) -> list[str]:
    """纯空白单元折叠（边界热修复 2026-10-20 P0 Critical）。

    尾部/中置空白单元并入前一单元；前导空白（无前单元可并入）并入后一
    非空白单元。字符零丢失（拼接不变式），折叠后每个单元都含非空白内容
    ——纯空白（如尾部 ``"\\n"``、前导 ``"\\n\\n"``）绝不独占一批喂给 LLM。
    """
    folded: list[str] = []
    pending_lead = ""
    for u in units:
        if u.strip():
            folded.append(pending_lead + u)
            pending_lead = ""
        elif folded:
            folded[-1] += u
        else:
            pending_lead += u
    if pending_lead:
        # 仅全空白输入可达——plan_storyboard_batches 入口已拒空白脚本，防御兜底不丢字符
        folded.append(pending_lead)
    return folded


def _split_units(text: str) -> list[str]:
    """按段落/完整句子切分原文（保序；拼接 == 原文，无丢失无改写）。

    段落（``\\n\\n`` 之后）优先——lookbehind 切分把分隔符留在前段，字符零丢失；
    段落数不足（≤1 非空段）时按完整句子边界切。绝不硬切句中。
    """
    # 评审修复（2026-10-19）：单换行也是段界（上游英文脚本普遍单 \n）。
    # 边界热修复（2026-10-20 P0）：段切/句切两分支统一过空白折叠——修前段切
    # 首单元空白因「无前单元」直入 units[0]、句切完全无折叠，尾部 "\n" 与前导
    # 空行都会独占一批（纯空白当剧本喂 LLM）。
    units = _fold_whitespace_units([p for p in re.split(r"(?<=\n)", text) if p])
    if len([u for u in units if u.strip()]) > 1:
        return units
    # 单段：句级切分（句末标点+空白边界，标点保留前句；无边界则整段）
    sentences = _fold_whitespace_units([s for s in _SENTENCE_SPLIT_RE.split(text) if s])
    return sentences or [text]


def plan_storyboard_batches(script: str, *, global_shot_budget: int) -> list[StoryboardBatchPlan]:
    """确定性分批：批数 = ceil(global/8)（≤12 时 1 批），按段落/句子均衡装填。

    - 每段原文恰好覆盖一次（按序拼接 == 原文）；不硬切、不造空批。
    - 可分段单元不足时减少批数（可用总上限变小允许，超出全局绝不允许）。
    - 预算按全局余数分配，单批恒 ≤8。
    """
    if not script or not script.strip():
        raise ValueError("plan_storyboard_batches: script must be non-empty")
    if global_shot_budget <= 0:
        raise ValueError(f"plan_storyboard_batches: global_shot_budget must be positive, got {global_shot_budget}")

    split_batches = math.ceil(global_shot_budget / STORYBOARD_BATCH_SHOT_CAP) if global_shot_budget > SINGLE_BATCH_SHOT_THRESHOLD else 1
    units = _split_units(script)
    # 单元不足 → 减少批数（整段进批，不硬切）
    n_batches = max(1, min(split_batches, len(units)))
    base, extra = divmod(global_shot_budget, n_batches)
    if split_batches > 1:
        # 拆批路径：单批 cap 到 8（正常 ceil 切分天然 ≤8；批数被「单元不足」收缩后
        # 单批预算可能超过 cap——缩减批数导致可用总上限变小是允许的，单批上限绝不放大）。
        budgets = [min(base + (1 if i < extra else 0), STORYBOARD_BATCH_SHOT_CAP) for i in range(n_batches)]
    else:
        # ≤12 阈值路径 = 现有单次调用语义：预算原值（不为拆批凭空改镜头数，D6）。
        budgets = [global_shot_budget]

    # 顺序装填到字符权重目标（确定性）：target = ceil(total/批数)；单元超重独占一批
    total = sum(len(u) for u in units)
    target = max(1, math.ceil(total / n_batches))
    batches: list[list[str]] = [[] for _ in range(n_batches)]
    cur = 0
    for u in units:
        if cur >= n_batches - 1:
            batches[cur].append(u)
            continue
        if sum(len(x) for x in batches[cur]) + len(u) > target and batches[cur]:
            cur += 1
        batches[cur].append(u)
    # 边界收口（2026-10-19 P2，闭合 OCR bug·low）：收缩时 batches 与 budgets 同步
    # pop——修前 batches 收缩 budgets 不动，enumerate 错位致末批拿偏小预算。
    while len(batches) > 1 and not batches[-1]:
        batches.pop()
        budgets.pop()
    # 边界热修复（2026-10-20 P0）：显式 if/raise 替代 assert（`python -O` 下 assert
    # 被剥离成 no-op）；每批必须有非空白内容——纯空白批会把 "\n" 当剧本喂给 LLM。
    if len(batches) != len(budgets):
        raise ValueError("plan_storyboard_batches: internal error——批数与预算数必须对齐")
    for i, parts in enumerate(batches):
        if not parts or not "".join(parts).strip():
            raise ValueError(f"plan_storyboard_batches: internal error——批 {i} 为空或纯空白，不得独立成批")
    return [StoryboardBatchPlan(text="".join(parts), shot_budget=budget) for parts, budget in zip(batches, budgets)]


def merge_storyboard_batches(batch_shots: list[list[ShotBriefDescription]], *, global_shot_budget: int) -> list[ShotBriefDescription]:
    """安全合并：按批次顺序+批内顺序重编号全局 idx；仅全局末镜 is_last。

    - 每批须返回局部 idx==0..N-1（乱序/非连续/空批 raise，不静默拼接）。
    - ``(batch_idx, local_cam_idx)`` 首次出现映射下一全局整数：批内复用保留、
      跨批不碰撞。
    - 合并总数超全局预算 raise。
    """
    merged: list[ShotBriefDescription] = []
    cam_map: dict[tuple[int, int], int] = {}
    for b_idx, shots in enumerate(batch_shots):
        if not shots:
            raise ValueError(f"merge_storyboard_batches: batch {b_idx} returned an empty storyboard")
        idxs = [s.idx for s in shots]
        if idxs != list(range(len(shots))):
            raise ValueError(f"merge_storyboard_batches: batch {b_idx} local idx must be contiguous and zero-based; got {idxs}")
        for s in shots:
            if s.cam_idx < 0:
                raise ValueError(f"merge_storyboard_batches: batch {b_idx} shot {s.idx} has negative cam_idx {s.cam_idx}")
            key = (b_idx, s.cam_idx)
            if key not in cam_map:
                cam_map[key] = len(cam_map)
            merged.append(s.model_copy(update={"idx": len(merged), "is_last": False, "cam_idx": cam_map[key]}))
    if not merged:
        raise ValueError("merge_storyboard_batches: no shots to merge")
    if len(merged) > global_shot_budget:
        raise ValueError(f"merge_storyboard_batches: merged {len(merged)} shots exceeds global budget {global_shot_budget}")
    merged[-1] = merged[-1].model_copy(update={"is_last": True})
    return merged
