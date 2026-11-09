"""错误文案中文化：DB 里存的 error_message 会被前端原样渲染给最终用户。

修前用户看到的是 `360 video submit failed (400): {"error":{"code":"10940",...}}`。
原则：能识别的映射成中文 + 可操作建议；**识别不了的保留原文**--
吞掉原文会让线上排查失去唯一线索。
"""

from __future__ import annotations

import re

# (匹配片段, 中文文案)。按特异性从高到低匹配。
_PATTERNS: tuple[tuple[str, str], ...] = (
    ("10940", "内容未通过审核，请调整画面描述或旁白后重试"),
    ("green net check", "内容未通过审核，请调整画面描述后重试"),
    ("rate limit", "服务商限流，请稍后重试"),
    # 2026-08-08 P2-2：纯子串 "429" 会误判任何含数字 429 的无关错误
    # （如 "Frame 429 generation failed"）。对照实际错误格式
    # "360 video submit failed (400): ..."，用带括号形态提特异性。
    ("(429)", "服务商限流，请稍后重试"),
    ("status 429", "服务商限流，请稍后重试"),
    ("http 429", "服务商限流，请稍后重试"),
    ("budget exceeded", "已达本任务的生成上限，请减少分镜数量后重试"),
    ("cost exceeded", "已达本任务的费用上限，请减少分镜数量后重试"),
    # Plan C（2026-10-19）：ViMax 本地分镜 deadline（批次断点可复用）先于通用
    # timeout——文案给「重试即续跑」的可行动预期，不是服务商故障。
    ("vimax_storyboard_deadline", "分镜生成超时，请重试；已完成批次会被复用"),
    ("timeout", "服务商响应超时，请重试"),
    ("no such column", "数据库结构过旧，请联系管理员升级"),
)

# T1（2026-08-31 错误分诊）：coverage 行从 _PATTERNS 整表替换中**移除**——
# ai_video 覆盖率错误是「多帧失败的结果」，真实原因藏在首个失败里（参数不合规
# 也会走到这里，如参考图比例超限被 400 拒），一律说「服务商繁忙」是误诊。
# 现走 _triage_coverage 分诊路径，见其 docstring。

# ai_video 覆盖率错误末尾的固定英文尾句（ai_video.py 覆盖率 raise 原文）——
# 对已分诊出真实原因的用户是误导性尾巴，分诊前剥掉。
_ENGLISH_TAIL_RE = re.compile(r"Provider may be slow.*$", re.IGNORECASE | re.DOTALL)


def _lookup(text: str) -> str | None:
    """按 _PATTERNS 查表（大小写不敏感）；未命中返回 None。"""
    low = text.lower()
    for needle, cn in _PATTERNS:
        if needle.lower() in low:
            return cn
    return None


def _triage_coverage(raw: str, limit: int) -> str:
    """T1（2026-08-31）：coverage 错误分诊——抽 ``First error:`` 递归映射。

    触发任务 vg_a01c4ceb1a3c：H3 比例硬校验 [0.4,2.5] 拒收 2.783 的横排合成图，
    8 帧全败 → coverage 错误整表映射「服务商繁忙」——参数类错误被伪装成基础设施
    故障，用户重试必然再败。分诊规则：

    - 无 ``First error:`` → 中文兜底（不指认服务商）；
    - 有 → 取首个失败原因，剥英文尾句后**再走一次 _PATTERNS**：识别出
      429/超时/审核/预算类 → 直接给对应可操作文案（服务商类文案只在这条
      路径出现，语义仍然成立）；
    - 含 ``invalid param``（参数类豁免）→ 抽出 param 冒号后的具体原因原样透出，
      外层拼中文框架（比例类标注「参考图比例超限」，其余「参数不合规」）；
    - 其余未识别 → 保留原文（文件头原则），仅拼中文框架。
    """
    m = re.search(r"first error\s*:", raw, re.IGNORECASE)
    if not m:
        return "画面生成成功率过低，请重试；若持续失败请稍后再试或减少分镜数量"
    inner = _ENGLISH_TAIL_RE.sub("", raw[m.end() :]).strip().rstrip(' .,;"}').strip()
    mapped = _lookup(inner)
    if mapped is not None:
        return mapped
    pm = re.search(r"invalid param\s*:", inner, re.IGNORECASE)
    if pm:
        reason = inner[pm.end() :].strip().rstrip(' "}').strip()
        label = "参考图比例超限" if "aspect ratio" in reason.lower() else "参数不合规"
        return f"视频生成失败：{label}（{reason}），请调整后重试"[:limit]
    return f"视频生成失败（多帧失败）：{inner}"[:limit]


def to_user_message(exc: BaseException | str, *, limit: int = 500) -> str:
    """把异常/原始错误串转成面向用户的中文文案；无法识别则保留原文。

    W6b/D1（2026-08-24）：**仅当 ``str(exc)`` 为空时**前置类型名兜底--
    08-20 事故产物 ``TimeoutError: ``（空消息）经本函数后 raw 为空串，
    "timeout" 中文映射匹配不上 -> 任务 failed 且 error_message 为空。
    前置类型名后 ``TimeoutError`` 命中既有映射（服务商响应超时）；
    非空原文零回归。纯空**字符串**输入（非异常）无类型名可用，原样返回空。
    """
    # 边界收口（2026-10-19 P1-4）：本地分镜 deadline 按异常元数据分支——批次可复用
    # 才承诺复用；单批不承诺（防虚假承诺）。字符串形态（无元数据）维持既有承诺文案。
    if isinstance(exc, BaseException) and getattr(exc, "error_code", None) == "vimax_storyboard_deadline":
        if getattr(exc, "batches_reusable", False):
            return "分镜生成超时，请重试；已完成批次会被复用"[:limit]
        return "分镜生成超时，请重试"[:limit]
    raw = str(exc)
    if not raw.strip():
        if isinstance(exc, BaseException):
            raw = type(exc).__name__
        else:
            return ""
    # Font gate errors: pass through raw (preserves font names + guidance).
    # Skips the ValueError "参数或配置错误：" prefix — the message is user-actionable.
    if "missing-font-detected" in raw:
        return raw[:limit]
    low = raw.lower()
    # T1（2026-08-31）：coverage 先于查表分诊（_PATTERNS 已无该行——整表替换
    # 会让参数类错误伪装成「服务商繁忙」）。
    if "coverage too low" in low:
        return _triage_coverage(raw, limit)[:limit]
    for needle, cn in _PATTERNS:
        if needle.lower() in low:
            return cn[:limit]
    # Type-based fallback: ValueError/TypeError are caller bugs, not transient.
    # 2026-08-08 P2-2：友好前缀 + 保留原文——修前固定文案丢弃 raw，与文件头
    # 「识别不了的保留原文」原则自相矛盾（线上排查失去唯一线索）。
    if isinstance(exc, (ValueError, TypeError)):
        return f"参数或配置错误：{raw}"[:limit]
    return raw[:limit]
