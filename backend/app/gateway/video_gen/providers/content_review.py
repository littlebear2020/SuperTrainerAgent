"""内容审核识别与提示词中性化（2026-08-03 Task 12）。

背景：内容审核失败**刻意不做 provider 降级**——四个 provider 的 docstring 与
``registry.py``（需求 #4）都写明「上浮给用户」，因为换网关同样过不了。
本模块不推翻该决策，只补一条正交的救援：**同 provider、换中性化说法、重试一次**。
命中「换个说法就能过」的场景，避免用户手动改词。

正则原先只存在于 ``engines/vimax/provider_bridge.py``（真实事故 vg_86d2767fe762），
现提到公共位置供 ai_video 复用。
"""

from __future__ import annotations

import re

# 2026-08-19（P1）：纳入 360 中文话术与 code 12020——360 提交期 4xx 与轮询期
# FAILED 回的都是中文（「命中敏感[色情:…]」/「内容审核不通过」），纯英文正则
# 从未命中过，中性化重试机制对 360 通道一直空转。12020 用负向前瞻防吞 120201。
_CONTENT_REVIEW_RE = re.compile(
    r"green net check|content.*(policy|review)|inappropriate content|safety|moderation|sensitive\s*content"
    r"|命中敏感|内容审核|审核(未|不)通过|敏感词|色情|低俗|违禁"
    r"|code[\"']?\s*[:=]\s*[\"']?12020(?![0-9])",
    re.IGNORECASE,
)

# Task 12：易触审核的表述 → 中性替换。保守起见只收敛明确高风险词，
# 不做泛化改写（把提示词改得面目全非等于换了画面，用户更困惑）。
_RISKY_TERMS = {
    "持枪": "持械",
    "血迹斑斑": "现场凌乱",
    "尸体": "人形轮廓",
    "暴力": "冲突",
    "枪": "工具",
    "血": "痕迹",
    "裸": "着装简约",
}


def is_content_review_rejection(exc: BaseException) -> bool:
    """是否内容审核类拒绝（而非基础设施故障）。"""
    return isinstance(exc, RuntimeError) and bool(_CONTENT_REVIEW_RE.search(str(exc)))


def neutralize_prompt(prompt: str) -> str:
    """把易触审核的表述替换为中性说法；幂等。

    返回值保证非空——全部命中被替换的极端情况下也仍有内容（替换是等长替换，不是删除）。

    幂等性依赖「替换值本身不含任何 key」：如「枪→工具」而非「枪→猎枪」。
    新增词条时务必保持该性质，否则反复中性化会不断改写
    （``test_neutralize_is_idempotent`` 会拦住）。
    """
    out = prompt
    # 长词先替换，避免「持枪」被「枪」的规则先切碎
    for term in sorted(_RISKY_TERMS, key=len, reverse=True):
        out = out.replace(term, _RISKY_TERMS[term])
    return out


# 2026-08-19：360 图片输入内容检测（code 10940，实为隐私过滤器，对含大量文字的
# 信息图预览有误拦——实网探针 img_0000.png 被拦、img_0005.png 同风格通过）。
# 拦截对象是图不是提示词：在 ai_video 救援梯子上必须先于 is_content_review_rejection
# 判断（报文里的 "SensitiveContent" 会命中 _CONTENT_REVIEW_RE 的 sensitive\s*content
# 分支），否则救援走反——中性化改词而图还在，必再败。
# 首备选放宽为 Input\w*SensitiveContentDetected：提交期 400 报文带 code 10940 双保险，
# 但轮询期 failure_reason 只透传文案（qihu_video._extract_failure_from_error_body），
# 若 360 措辞漂移（如去掉 Image 前缀）仍要识别得上。谓词 provider 无关。
_IMAGE_CONTENT_RE = re.compile(
    r"Input\w*SensitiveContentDetected|code[\"']?\s*[:=]\s*[\"']?10940(?![0-9])",
    re.IGNORECASE,
)


def is_image_content_rejection(exc: BaseException) -> bool:
    """是否图片输入内容检测拒绝（拦截的是图，不是提示词）。"""
    return isinstance(exc, RuntimeError) and bool(_IMAGE_CONTENT_RE.search(str(exc)))
