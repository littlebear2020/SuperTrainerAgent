"""质量词后缀（2026-08-12 设计反转）。

**设计反转（2026-08-12，prompt-optimization-v1 T1/R1）**：
此前 P2-1（2026-08-01）刻意启用了 script.py 死代码常量的负向词语义，
把「避免模糊、低质量、畸变、肢体错误、多余肢体、水印、文字」拼入每条 prompt 尾部。

反转理由（三重论证，不是猜测）：
1. **白熊效应（white bear effect）**：「避免畸变」反而提示模型关注畸变——
   对模糊/低质量/畸变/肢体类词，文本负向引导适得其反；
2. **Ark seedream 无 negative 字段**（BYTEDANCE_ARK_API_REFERENCE.md §1.2）：
   负向词只能拼入 prompt 正文，而文本级负向引导不是有效的去畸变手段——
   正确的做法是用正面质量词（「浅景深」「细腻材质质感」）直接引导画面上限；
3. **水印/字幕抑制保留**（Ark 官方推荐 BYTEDANCE_ARK_API_REFERENCE.md 故障排查表）：
   但从「每条后缀拼负向句」改为写进系统 prompt 的正向化纯净条款
   （「画面保持纯净，全程无字幕、无水印、无标识、无任何文字」）。
"""

import pytest

from app.gateway.video_gen.orchestration import storyboard_planner as planner
from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame


def test_enhance_prompt_quality_has_style_specific_words():
    """质量词拼入 prompt，风格具体词替换泛化「高质量」。"""
    out = planner._enhance_prompt_quality("a cat on a table", "realistic")
    assert "浅景深" in out  # realistic 风格具体词
    # 不再有负向引导后缀
    for neg in ("避免模糊", "畸变", "多余肢体", "避免"):
        assert neg not in out, f"不应包含负向词「{neg}」：{out!r}"


@pytest.mark.asyncio
async def test_single_frame_fallback_gets_style_and_quality(monkeypatch):
    """bulk 全漏 -> 单帧兜底产物=LLM 原文（T-I0-② 前置/后缀已退役）。"""

    async def fake_enhanced(llm, mini, style_prefix):
        return ["" for _ in mini.frames], ["" for _ in mini.frames]  # bulk 全漏

    async def fake_single(llm, narrations, **kwargs):
        return ["a serene mountain lake"]

    monkeypatch.setattr(planner, "_generate_course_prompts_enhanced", fake_enhanced)
    monkeypatch.setattr(planner, "_generate_image_prompts", fake_single)

    sb = Storyboard(title="t", config=StoryboardConfig(video_type="ai_image", width=1152, height=2048))
    sb.frames.append(StoryboardFrame(index=0, narration="旁白0"))
    await planner.plan_course(None, storyboard=sb, style_context={"style_prefix": "写实摄影风格"}, chunk_size=8)  # E8: 单帧兜底机制属批量路径

    p = sb.frames[0].image_prompt
    p = sb.frames[0].image_prompt
    assert p == "a serene mountain lake", f"单帧兜底产物须为 LLM 原文（T-I0-② 前置/后缀已退役）：{p!r}"


@pytest.mark.asyncio
async def test_single_frame_fallback_without_style_still_gets_quality(monkeypatch):
    """无 style_prefix -> 同样保持 LLM 原文（T-I0-② 后缀追加已退役）。"""

    async def fake_enhanced(llm, mini, style_prefix):
        return ["" for _ in mini.frames], ["" for _ in mini.frames]

    async def fake_single(llm, narrations, **kwargs):
        return ["a serene mountain lake"]

    monkeypatch.setattr(planner, "_generate_course_prompts_enhanced", fake_enhanced)
    monkeypatch.setattr(planner, "_generate_image_prompts", fake_single)

    sb = Storyboard(title="t", config=StoryboardConfig(video_type="ai_image", width=1152, height=2048))
    sb.frames.append(StoryboardFrame(index=0, narration="旁白0"))
    await planner.plan_course(None, storyboard=sb, chunk_size=8)  # E8: 单帧兜底机制属批量路径

    p = sb.frames[0].image_prompt
    p = sb.frames[0].image_prompt
    assert p == "a serene mountain lake", f"无 style 时产物须为 LLM 原文：{p!r}"
