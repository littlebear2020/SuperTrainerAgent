"""Prompt optimization v1 回归测试（2026-08-12）。

覆盖 T1-T8 改动的核心不变量：负向词删除、风格具体质量词、解释性视觉分层模板、
主体动作层、正文长度预算、JSON 契约键、主题上下文注入、镜头词中文化。

变异验证目标：加回负向词 / 删主体动作层 / 撤 title 注入 → 对应测试必须真红。
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from app.gateway.video_gen.orchestration import storyboard_planner as planner
from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame

# ── T1/T2: 负向词删除 + 风格具体质量词 ──────────────────────────────────────


def test_prompt_no_negative_suffix():
    """任意 style 的 _enhance_prompt_quality 产物不含白熊效应类负向词。"""
    for style in ("realistic", "anime", "illustration", "watercolor", "default"):
        out = planner._enhance_prompt_quality("测试画面", style)
        for neg in ("避免", "畸变", "多余肢体", "低质量"):
            assert neg not in out, f"style={style} 产物不应包含负向词「{neg}」：{out!r}"


def test_quality_suffix_style_specific():
    """每个风格有具体技术词而非泛化「高质量」。"""
    realistic = planner._enhance_prompt_quality("x", "realistic")
    assert "浅景深" in realistic and "电影级调色" not in realistic, "realistic 不含电影级调色（与 worker 调色重叠）"
    anime = planner._enhance_prompt_quality("x", "anime")
    assert "赛璐珞" in anime
    illustration = planner._enhance_prompt_quality("x", "illustration")
    assert "矢量" in illustration or "扁平" in illustration


# ── T3: 解释性视觉分层 image 系统 prompt ────────────────────────────────────


def test_image_system_prompt_is_explanatory():
    """_IMAGE_PROMPT_SYSTEM 是解释性视觉模板而非氛围象征模板。"""
    s = planner._IMAGE_PROMPT_SYSTEM
    # 实体锚定
    assert "实体" in s or "具体" in s, "须含实体锚定指导"
    # 无文字示意图通道
    assert "示意图" in s or "剖面" in s, "须含无文字示意图通道"
    # 分层关键词
    for kw in ("主体", "光线", "构图"):
        assert kw in s, f"须含分层关键词「{kw}」"
    # 不含旧表述
    assert "氛围与象征" not in s, "不应保留旧的氛围象征表述"
    # 纯净条款（R1）：图片侧只守「水印/平台标识」——D1（2026-08-19 拍板，c5acf9e88）
    # 起图片侧允许教学性文字，「无字幕/无文字」条款已退役（现行策略由
    # test_image_prompt_text_policy.py 断言「全程无字幕」不在其中）；视频侧仍禁文字。
    assert "水印" in s, "须含水印纯净条款"
    # JSON 键不变
    assert "image_prompts" in s


# ── T4: 解释性视觉 + 强制主体动作层 video 系统 prompt ──────────────────────


def test_video_system_prompt_has_subject_action_layer():
    """_VIDEO_PROMPT_SYSTEM 含主体动作层 + 运镜分离 + 环境动态层 + 纯净条款。"""
    s = planner._VIDEO_PROMPT_SYSTEM
    # 主体动作层（须明确要求「主体在做什么」——这条是 T4 的核心改动，不能被摘要行的复述替代）
    assert "主体在做什么" in s, "须含强制主体动作指令（主体在做什么）"
    # 运镜与动作分离
    assert "运镜" in s, "须含运镜层指导"
    # 环境动态层
    assert "环境" in s or "光影" in s, "须含环境动态层指导"
    # 纯净条款
    assert "字幕" in s and "水印" in s, "须含无字幕/无水印纯净条款"
    # JSON 键不变
    assert "video_prompts" in s


# ── P1（评审反馈）：运镜双写冲突防护 ───────────────────────────────────────


def test_camera_instruction_no_camera_in_image_prompt():
    """camera_instruction 必须告知 LLM 不要在 image_prompt 文本中写运镜词。

    P1 风险：LLM 把「镜头缓慢推近」写进 image_prompt 正文，同时 camera_move 枚举
    经 compose_video_prompt 在尾部追加「缓慢推近」→ 两条可能矛盾的运镜指令。
    """
    import inspect

    src = inspect.getsource(planner._generate_course_prompts_enhanced)
    assert "不要写运镜词" in src or "image_prompt 文本中不要写运镜" in src, "camera_instruction 须明确告知运镜仅通过 camera_move 字段表达"


# ── R2: 正文长度预算 ──────────────────────────────────────────────────────


def test_prompt_length_budget():
    """最长 style_prefix + 180 字正文 + 质量后缀 < 300 字（seedream 硬顶守护）。"""
    from app.gateway.video_gen.style_presets import STYLE_PREFIXES

    longest_prefix = max(STYLE_PREFIXES.values(), key=len)  # 最长的中文 prefix
    body_180 = "字" * 180  # 180 字正文上限
    for style_key in ("realistic", "anime", "illustration", "watercolor", "default"):
        full = f"{longest_prefix}，{body_180}"
        enhanced = planner._enhance_prompt_quality(full, style_key)
        # 去掉标点和空格后的汉字数（粗略计数，保守估计）
        cjk_count = sum(1 for c in enhanced if "一" <= c <= "鿿")
        assert cjk_count <= 300, f"style={style_key}: longest_prefix + 180字正文 + 质量后缀 = {cjk_count} 汉字 > 300"


# ── JSON 契约键不变 ───────────────────────────────────────────────────────


def test_json_contract_keys_unchanged():
    """image_prompts / video_prompts / frames 键仍在各自系统 prompt 中。"""
    assert "image_prompts" in planner._IMAGE_PROMPT_SYSTEM
    assert "video_prompts" in planner._VIDEO_PROMPT_SYSTEM
    # P5（评审反馈）：frames 键在 _generate_course_prompts_enhanced 的系统 prompt 里（最承重）
    import inspect

    enhanced_src = inspect.getsource(planner._generate_course_prompts_enhanced)
    assert '"frames"' in enhanced_src, "frames 键须保留在 _generate_course_prompts_enhanced 系统 prompt 中"


# ── T5: 主题上下文注入 ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_title_injected_into_user_payload(monkeypatch):
    """_generate_course_prompts_enhanced 发往 LLM 的用户消息含 storyboard.title。"""
    captured_messages: list = []

    async def fake_invoke(llm, messages, **kwargs):
        captured_messages.extend(messages)
        resp = MagicMock()
        resp.content = json.dumps({"frames": [{"image_prompt": "测试", "visual_source": "ai_image"}]}, ensure_ascii=False)
        return resp

    monkeypatch.setattr(planner, "_invoke_llm_bounded", fake_invoke)

    sb = Storyboard(
        title="免疫系统工作原理",
        config=StoryboardConfig(video_type="ai_image", width=1920, height=1080),
    )
    sb.frames.append(StoryboardFrame(index=0, narration="免疫细胞在体内巡逻"))
    await planner._generate_course_prompts_enhanced(None, sb, "")

    assert captured_messages, "应捕获到 messages"
    user_msg = captured_messages[-1]["content"]
    assert "免疫系统工作原理" in user_msg, f"用户消息应含 title：{user_msg!r}"


@pytest.mark.asyncio
async def test_single_frame_fallback_receives_context(monkeypatch):
    """单帧兜底调用 _generate_image_prompts 时收到 context（title + 相邻旁白）。"""
    captured_kwargs: dict = {}

    async def fake_enhanced(llm, mini, style_prefix):
        return ["" for _ in mini.frames], ["" for _ in mini.frames]

    async def fake_single(llm, narrations, **kwargs):
        captured_kwargs.update(kwargs)
        return ["a detailed microscopic view of white blood cells"]

    monkeypatch.setattr(planner, "_generate_course_prompts_enhanced", fake_enhanced)
    monkeypatch.setattr(planner, "_generate_image_prompts", fake_single)

    sb = Storyboard(
        title="免疫系统工作原理",
        config=StoryboardConfig(video_type="ai_image", width=1920, height=1080),
    )
    sb.frames.append(StoryboardFrame(index=0, narration="免疫细胞在体内巡逻"))
    await planner.plan_course(None, storyboard=sb, chunk_size=8)  # E8: 单帧兜底上下文机制属批量路径

    assert "context" in captured_kwargs, "单帧兜底应收到 context 参数"
    ctx = captured_kwargs["context"]
    assert ctx is not None
    assert ctx.get("title") == "免疫系统工作原理", f"context 应含 title：{ctx!r}"


# ── T7: docstring 修正 ─────────────────────────────────────────────────────


def test_no_english_in_docstrings():
    """三处函数/模块 docstring 不再写 English。"""
    assert "English" not in planner.__doc__
    assert "English" not in planner._generate_image_prompts.__doc__
    assert "English" not in planner._generate_video_prompts.__doc__
