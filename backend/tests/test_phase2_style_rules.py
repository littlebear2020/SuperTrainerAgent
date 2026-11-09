"""Phase 2（2026-08-17）：五风格词表 + T-C1 摄影规则库指令级注入。

覆盖：五风格 key 词域解析 / education_flat 退役仍可解析（存量只读）/
摄影规则注入（仅 ai_video）/ 规则内容锚（景深联动/手部慎用/节拍）。
"""

from __future__ import annotations

import pytest

from app.gateway.video_gen.cinematography_rules import cinematography_instruction
from app.gateway.video_gen.orchestration import storyboard_planner as planner
from app.gateway.video_gen.style_presets import STYLE_PREFIXES, VIDEO_STYLE_KEYS


class _CapLLM:
    def __init__(self, content: str) -> None:
        self._content = content
        self.messages: list = []

    async def ainvoke(self, messages):
        self.messages.append(messages)

        class _Resp:
            content = self._content

        return _Resp()


def _sb(video_type: str) -> object:
    from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame

    return Storyboard(
        title="t",
        config=StoryboardConfig(video_type=video_type, width=1920, height=1080),
        frames=[StoryboardFrame(index=0, narration="n")],
    )


# ── 五风格词表 ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "key,markers",
    [
        ("precision_eng", ["拉丝金属", "工程美学"]),
        ("paper_collage", ["纹理纸", "毛边"]),
        ("papercraft_theater", ["纸雕", "暖黄灯"]),
        ("character_3d", ["次表面散射", "弹性挤压"]),
        ("corporate_live", ["窗光", "纪实质感"]),
        ("isometric_info", ["等距", "留白"]),
        ("whiteboard", ["马克笔", "荧光笔"]),
    ],
)
def test_five_style_prefixes(key: str, markers: list[str]):
    prefix = STYLE_PREFIXES[key]
    assert all(m in prefix for m in markers), f"{key} 词域缺失: {prefix}"


def test_video_style_keys_all_resolvable():
    assert all(k in STYLE_PREFIXES and STYLE_PREFIXES[k] for k in VIDEO_STYLE_KEYS)


def test_education_flat_retired_but_readable():
    """退役 = UI 不给新选；后端只读兼容（存量 payload 不 422 不退化）。"""
    from app.gateway.video_gen.style_presets import resolve_style_key

    assert STYLE_PREFIXES["education_flat"], "存量 education_flat 仍须有前缀"
    assert resolve_style_key("education_flat") == "education_flat"


# ── T-C1 规则库内容锚 ──────────────────────────────────────────────────


def test_cinematography_instruction_contents():
    ins = cinematography_instruction()
    assert "景深" in ins, "景别↔景深联动表须在场"
    # 主体规避（手部慎用口径——E1-B 实证纠正社区「禁用」经验，见 cinematography_rules.py 头注）：
    # 拆成三条可独立失败的断言（原 `A and not B or A` 恒等 A，永不红）。
    assert "手部" in ins, "主体规避须包含手部规则"
    assert "非必要不用" in ins or "慎用" in ins, "手部规则须为「慎用/非必要不用」软规避口径"
    assert "禁止手部" not in ins and "禁用手部" not in ins, "手部规则不得硬化为禁用（与 E1-B 实证口径冲突）"
    assert "正脸特写" in ins
    assert "一拍" in ins, "时长节拍"
    assert "光源方向" in ins, "environment 产出要求升级"


# ── T-C2 注入（仅 ai_video）────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rules_injected_only_for_video():
    llm_v = _CapLLM('{"frames": [{"image_prompt": "p", "visual_source": "ai_video"}]}')
    await planner._generate_course_prompts_enhanced(llm_v, _sb("ai_video"), "")
    sys_v = " ".join(str(m.get("content", "")) for m in llm_v.messages[0])
    assert "摄影规则" in sys_v and "景深" in sys_v

    llm_i = _CapLLM('{"frames": [{"image_prompt": "p", "visual_source": "ai_image"}]}')
    await planner._generate_course_prompts_enhanced(llm_i, _sb("ai_image"), "")
    sys_i = " ".join(str(m.get("content", "")) for m in llm_i.messages[0])
    assert "摄影规则" not in sys_i, "ai_image 无运镜概念，不注入摄影规则"


@pytest.mark.asyncio
async def test_five_style_prefix_flows_into_course_prompt():
    """五风格经 style_prefix 进入「全片统一」声明（T-I0-② 语义）。"""
    llm = _CapLLM('{"frames": [{"image_prompt": "p", "visual_source": "ai_video"}]}')
    await planner._generate_course_prompts_enhanced(llm, _sb("ai_video"), STYLE_PREFIXES["precision_eng"])
    sys = " ".join(str(m.get("content", "")) for m in llm.messages[0])
    assert "拉丝金属" in sys and "统一" in sys
