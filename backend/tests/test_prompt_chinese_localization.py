"""画面提示词中文化（Task 2，2026-08-01 反馈 5）。

用户反馈：「选『专业』后提示词变英文」。**观察完全准确** —— 只有选了
专业/轻松/电影感才变英文，选默认不变，因为 `default` 映射空串 → 不注入前缀。

根因是仓库自己写死的英文（与上游模型无关），共五处：
1. `STYLE_PREFIXES` 英文前缀 → 被塞进**中文** system prompt 要求「必须以这段英文
   开头」，LLM 顺势把整条 prompt 写成英文（**主因**）；
2. `_IMAGE_PROMPT_SYSTEM` / `_VIDEO_PROMPT_SYSTEM` 明写 "detailed English ... prompt"；
3. `_QUALITY_SUFFIX` / `_NEGATIVE_SUFFIX` 恒为英文，拖在每条 prompt 尾部；
4. `_topic_fallback_prompt` 与两处 visual_source 兜底（同一段英文的三份拷贝）；
5. 项目级 `course_models.style_prefix`（已随 Task 1 一并修，见
   test_course_models_style_prefix.py）。

为什么能安全改中文（非推断）：
- 当前默认生图是 Ark seedream，`providers/seedream.py` 对 prompt 原样透传不翻译；
- Ark 官方文档（BYTEDANCE_ARK_API_REFERENCE.md §1.2）明写 prompt 支持 ≤300 汉字，
  汉字是一等公民且中文额度是英文的一半用量；
- 实证：真实任务 vg_d0779ddd81f8 的画面词正文本就是中文且成图正常。

⚠️ 本文件只能验证「**发给 LLM 的指令**是中文」与「拼接的常量是中文」，
**不能验证 LLM 是否遵守** —— 后者需实机验证（见计划 Task 2 Step 6）。
"""

from __future__ import annotations

import re

CJK = re.compile(r"[一-鿿]")


def _has_cjk(s: str) -> bool:
    return bool(CJK.search(s))


def test_style_prefixes_are_chinese():
    """所有非空 style prefix 必须是中文，且不得残留英文风格词。"""
    from app.gateway.video_gen.style_presets import STYLE_PREFIXES

    banned = (
        "professional corporate style",
        "casual lifestyle photography",
        "cinematic film still",
        "clean composition",
        "shallow depth of field",
    )
    for key, prefix in STYLE_PREFIXES.items():
        if not prefix:  # default 映射空串，合法
            continue
        assert _has_cjk(prefix), f"{key} 的 prefix 应为中文，实际={prefix!r}"
        low = prefix.lower()
        for b in banned:
            assert b not in low, f"{key} 仍残留英文风格词 {b!r}"


def test_image_and_video_prompt_system_ask_chinese():
    """两个 system prompt 的语言要求是旁白相对锚（拍板④），不再绝对「中文」。

    历史：2026-08-01 反馈 5 修的是「明写 English」；2026-08-19（两反馈批审查
    偏离项 2）起改为与旁白一致（旁白中文→中文，旁白英文→英文）——英文稿走
    单帧兜底/重生不再是中文指令。中文稿行为不变（锚仍含中文方向）。
    键名 image_prompts / video_prompts 属 JSON 契约，**必须保持不变**
    （_parse_json_prompts 依赖它们，改了会全挂）。
    """
    from app.gateway.video_gen.orchestration.storyboard_planner import (
        _IMAGE_PROMPT_SYSTEM,
        _VIDEO_PROMPT_SYSTEM,
    )

    for name, sys_prompt in (
        ("_IMAGE_PROMPT_SYSTEM", _IMAGE_PROMPT_SYSTEM),
        ("_VIDEO_PROMPT_SYSTEM", _VIDEO_PROMPT_SYSTEM),
    ):
        assert "与旁白相同的语言" in sys_prompt, f"{name} 应带旁白相对语言锚"
        assert "旁白英文→英文" in sys_prompt, f"{name} 锚须含英文方向（否则英文稿退回中文指令）"
        assert "English" not in sys_prompt, f"{name} 不得再要求 English"
        assert "STRICT JSON" in sys_prompt or "JSON" in sys_prompt, f"{name} 须保留 JSON 契约"

    # JSON 契约键名不变（回归护栏：改了会让 _parse_json_prompts 全部解析失败）
    assert "image_prompts" in _IMAGE_PROMPT_SYSTEM
    assert "video_prompts" in _VIDEO_PROMPT_SYSTEM


def test_quality_suffix_is_chinese_no_negative_suffix():
    """_QUALITY_SUFFIX 各风格值仍为中文；_NEGATIVE_SUFFIX 已删除（2026-08-12 设计反转）。

    反转：负向引导词不再拼入每条 prompt（白熊效应 + Ark 无 negative 字段），
    水印/字幕抑制迁入系统 prompt 的正向化纯净条款。
    """
    import app.gateway.video_gen.orchestration.storyboard_planner as planner_mod
    from app.gateway.video_gen.orchestration.storyboard_planner import _QUALITY_SUFFIX

    for key, val in _QUALITY_SUFFIX.items():
        assert _has_cjk(val), f"_QUALITY_SUFFIX[{key}] 应为中文，实际={val!r}"
        assert "high quality" not in val.lower(), f"_QUALITY_SUFFIX[{key}] 仍残留英文"

    # _NEGATIVE_SUFFIX 常量已删除——模块上不应存在
    assert not hasattr(planner_mod, "_NEGATIVE_SUFFIX"), "_NEGATIVE_SUFFIX 应已删除"

    # _enhance_prompt_quality 产物不含负向引导词
    out = planner_mod._enhance_prompt_quality("测试画面", "default")
    for neg in ("避免", "畸变", "多余肢体"):
        assert neg not in out, f"产物不应包含负向词「{neg}」：{out!r}"


def test_enhance_prompt_quality_keeps_chinese_end_to_end():
    """中文正文 + 中文后缀 → 整条 prompt 不得出现英文片段。"""
    from app.gateway.video_gen.orchestration.storyboard_planner import _enhance_prompt_quality

    out = _enhance_prompt_quality("现代都市CBD全景，玻璃幕墙写字楼群", "default")
    assert "现代都市CBD全景" in out, "正文须保留"
    assert "high quality" not in out.lower()
    assert "avoiding" not in out.lower()
    assert _has_cjk(out)


def test_topic_fallback_prompt_is_chinese():
    """主题兜底 prompt 改中文（QP1-6 最后防线，绝不回退 narration 的语义不变）。"""
    from app.gateway.video_gen.orchestration.storyboard_planner import _topic_fallback_prompt
    from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig

    sb = Storyboard(title="领导力培训", frames=[], config=StoryboardConfig(video_type="ai_image", width=1920, height=1080))
    out = _topic_fallback_prompt(sb)
    assert _has_cjk(out), f"兜底 prompt 应为中文，实际={out!r}"
    assert "Cinematic abstract" not in out
    assert "领导力培训" in out, "主题须出现在兜底 prompt 里"

    # 无标题时也不能退回英文占位
    sb2 = Storyboard(title="", frames=[], config=StoryboardConfig(video_type="ai_image", width=1920, height=1080))
    out2 = _topic_fallback_prompt(sb2)
    assert _has_cjk(out2), f"无标题兜底也应为中文，实际={out2!r}"
    assert "the course topic" not in out2


def test_visual_source_fallback_prompts_are_chinese():
    """ai_image / ai_video 两处兜底 prompt 是同一段英文的拷贝，须一并中文化。"""
    import inspect

    from app.gateway.video_gen.visual_sources import ai_image, ai_video

    for mod in (ai_image, ai_video):
        src = inspect.getsource(mod)
        assert "Cinematic abstract conceptual illustration" not in src, f"{mod.__name__} 仍写死英文兜底 prompt"


def test_detect_style_matches_chinese_prefixes():
    """_detect_style 原按英文关键词嗅探；prefix 改中文后必须仍能正确分类。

    否则质量词全部退化成 default（静默失效，画面质量下降但无任何报错）。
    """
    from app.gateway.video_gen.orchestration.storyboard_planner import _detect_style
    from app.gateway.video_gen.style_presets import STYLE_PREFIXES

    # 水彩 / 插画 / 写实 三类中文 prefix 必须各自命中，而非全落 default
    assert _detect_style(STYLE_PREFIXES["watercolor"]) == "watercolor"
    assert _detect_style(STYLE_PREFIXES["education_flat"]) == "illustration"
    assert _detect_style(STYLE_PREFIXES["photorealistic"]) == "realistic"
    assert _detect_style(STYLE_PREFIXES["education_3d"]) == "realistic"

    # 英文旧值仍可识别（存量 style_prefix 可能还是英文，向后兼容）
    assert _detect_style("watercolor painting") == "watercolor"
    assert _detect_style("photorealistic portrait") == "realistic"


def test_effective_script_style_normalizes_style_ids():
    """风格 id 不得原样透传进旁白归一 prompt（会出现「风格=professional。」噪声）。"""
    from app.gateway.video_gen.orchestration.script import _effective_script_style

    # default / None → 口语（既有行为，回归护栏）
    assert _effective_script_style("default") == "口语"
    assert _effective_script_style(None) == "口语"

    # 风格 id 须译成中文风格词，不得原样透传英文
    for style_id in ("professional", "corporate", "cinematic", "warm_casual"):
        out = _effective_script_style(style_id)
        assert out != style_id, f"{style_id} 不得原样透传"
        assert _has_cjk(out), f"{style_id} 应归一为中文风格词，实际={out!r}"

    # 用户自定义的中文风格词原样保留（不能把用户输入也吞掉）
    assert _effective_script_style("口语") == "口语"
    assert _effective_script_style("正式") == "正式"
