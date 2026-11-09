"""T-I0-②③④（2026-08-17 图片止血）：退役逐帧机械拼接 + 多样性指令 + 风格锚默认关。

用户实测病灶「生图千篇一律」的三个构造性来源：
② course 路径「每条必须以风格词开头」+ 单帧兜底 style_prefix 前置 + _enhance
  质量后缀--逐帧同词开头/同词结尾，模板化直达模型（_enhance 保留为 legacy 函数）；
③ 相邻帧景别/视角/句式无多样性约束；
④ ai_image 跨帧风格锚（last_success 弱参考）默认开--把整片拉向同一构图，
  env VIDEO_GEN_IMAGE_STYLE_ANCHOR=1 开回。
"""

from __future__ import annotations

import asyncio

import pytest

from app.gateway.video_gen.orchestration import storyboard_planner as planner
from app.gateway.video_gen.providers.base import ImageRef
from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame


class _CapLLM:
    """捕获 messages 的假 LLM，返回固定 frames JSON。"""

    def __init__(self, content: str) -> None:
        self._content = content
        self.messages: list = []

    async def ainvoke(self, messages):
        self.messages.append(messages)

        class _Resp:
            content = self._content

        return _Resp()


def _sb(n: int = 1, video_type: str = "ai_image") -> Storyboard:
    return Storyboard(
        title="t",
        config=StoryboardConfig(video_type=video_type, width=1152, height=2048),
        frames=[StoryboardFrame(index=i, narration=f"旁白{i}") for i in range(n)],
    )


# ── ② 风格指令：全片统一自然融入，不再逐帧机械开头 ──────────────────────


@pytest.mark.asyncio
async def test_course_style_instruction_unified_not_mechanical():
    llm = _CapLLM('{"frames": [{"image_prompt": "p", "visual_source": "ai_image"}]}')
    await planner._generate_course_prompts_enhanced(llm, _sb(1), "扁平插画，暖色调")
    sys = " ".join(str(m.get("content", "")) for m in llm.messages[0])
    assert "扁平插画，暖色调" in sys, "风格前缀仍须进 system prompt"
    assert "每条画面描述必须以" not in sys, "不得再要求逐帧以风格词开头（机械拼接已退役）"
    assert "统一" in sys and "自然融入" in sys


# ── ③ 多样性指令 ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_course_diversity_instruction_present():
    llm = _CapLLM('{"frames": [{"image_prompt": "p", "visual_source": "ai_image"}]}')
    await planner._generate_course_prompts_enhanced(llm, _sb(1), "")
    sys = " ".join(str(m.get("content", "")) for m in llm.messages[0])
    assert "景别" in sys and "句式" in sys, "相邻帧景别/视角/句式多样性指令须在场"
    assert "开头" in sys, "禁止相同开头/结尾的要求须在场"


# ── ② bulk 产物 = LLM 原文（后缀追加退役）──────────────────────────────


@pytest.mark.asyncio
async def test_course_bulk_prompts_raw_no_suffix():
    llm = _CapLLM('{"frames": [{"image_prompt": "raw-prompt", "visual_source": "ai_image"}]}')
    prompts, _ = await planner._generate_course_prompts_enhanced(llm, _sb(1), "扁平插画")
    assert prompts == ["raw-prompt"], "bulk 产物不得再追加质量后缀（_enhance 退役为 legacy）"


# ── ② 单帧兜底产物 = LLM 原文（prefix 前置 + 后缀追加双退役）────────────


@pytest.mark.asyncio
async def test_single_frame_fallback_raw_no_prefix_no_suffix(monkeypatch):
    async def fake_enhanced(llm, mini, style_prefix):
        return [], []  # 未对齐 -> 整块丢弃 -> 全部落单帧兜底

    async def fake_single(llm, narrations, **kwargs):
        return [f"single-p:{narrations[0]}"]

    monkeypatch.setattr(planner, "_generate_course_prompts_enhanced", fake_enhanced)
    monkeypatch.setattr(planner, "_generate_image_prompts", fake_single)
    sb = _sb(4)
    await planner._fill_image_prompts_robust(None, sb, "扁平插画，暖色调", chunk_size=8)  # E8: 单帧兜底机制属批量路径
    assert sb.frames[2].image_prompt == "single-p:旁白2", "单帧兜底不得前置风格词/追加后缀"


# ── ④ 跨帧风格锚默认关 / env=1 开回 ─────────────────────────────────────


class _FakeImageProvider:
    def __init__(self, n: int) -> None:
        self._n = n
        self.kwargs_calls: list[dict] = []

    async def generate_image(self, prompt: str, **kwargs):
        self.kwargs_calls.append(kwargs)
        i = len(self.kwargs_calls) - 1
        return ImageRef(url=f"https://cdn/{i}.png", width=1152, height=2048, source="qihu")


def _render_sb() -> Storyboard:
    cfg = StoryboardConfig(video_type="ai_image", width=1080, height=1920)
    return Storyboard(
        title="t",
        config=cfg,
        frames=[
            StoryboardFrame(index=0, narration="a", image_prompt="p0", duration=5.0),
            StoryboardFrame(index=1, narration="b", image_prompt="p1", duration=4.0),
        ],
    )


def _serialize_api(monkeypatch) -> None:
    """换新 1 槽全局信号量：帧串行 -> 第 2 帧确定性地晚于第 1 帧完成（锚测试确定性）。"""
    from app.gateway.video_gen.visual_sources import _global_concurrency

    monkeypatch.setattr(_global_concurrency, "_SEM", asyncio.Semaphore(1))


@pytest.mark.asyncio
async def test_style_anchor_off_by_default(monkeypatch):
    """④默认关：任何帧都不携带「上一帧成功 URL」作参考。"""
    from app.gateway.video_gen.visual_sources.ai_image import AIImageVisualSource

    monkeypatch.delenv("VIDEO_GEN_IMAGE_STYLE_ANCHOR", raising=False)
    provider = _FakeImageProvider(2)
    src = AIImageVisualSource(provider)
    await src.render(_render_sb())
    assert len(provider.kwargs_calls) == 2
    for kw in provider.kwargs_calls:
        assert not kw.get("reference_image_paths"), f"默认不得注入风格锚: {kw}"


@pytest.mark.asyncio
async def test_style_anchor_env_reenables(monkeypatch):
    """④env=1 开回（legacy 行为保留：第 2 帧带第 1 帧 URL 弱参考）。"""
    from app.gateway.video_gen.visual_sources.ai_image import AIImageVisualSource

    monkeypatch.setenv("VIDEO_GEN_IMAGE_STYLE_ANCHOR", "1")
    _serialize_api(monkeypatch)
    provider = _FakeImageProvider(2)
    src = AIImageVisualSource(provider)
    await src.render(_render_sb())
    assert len(provider.kwargs_calls) == 2
    assert provider.kwargs_calls[0].get("reference_image_paths") is None
    assert provider.kwargs_calls[1].get("reference_image_paths") == ["https://cdn/0.png"]
