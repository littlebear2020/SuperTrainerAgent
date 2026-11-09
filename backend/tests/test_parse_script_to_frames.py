"""S4: parse_script_to_frames — AI 结构化脚本解析。

把任意输入（讲解稿/分镜/公众号）解析成统一 {narration, image_prompt, discard} 结构。
"""

import pytest


class _JSONLLM:
    """返回固定 JSON 字符串的 mock LLM。"""

    def __init__(self, reply):
        self.reply = reply
        self.calls = 0

    async def ainvoke(self, messages):
        self.calls += 1
        from types import SimpleNamespace

        return SimpleNamespace(content=self.reply)


@pytest.mark.asyncio
async def test_parse_storyboard_extracts_narration_and_image():
    """分镜脚本（画面+独白）→ narration + image_prompt 分离；discard 帧丢弃。"""
    from app.gateway.video_gen.orchestration.script import parse_script_to_frames

    llm = _JSONLLM('{"content_type":"storyboard","frames":[{"narration":"小明走进了古老的森林","image_prompt":"ancient forest path, misty","discard":false},{"narration":"","image_prompt":"","discard":true}]}')
    frames = await parse_script_to_frames(llm, "小明走进了古老的森林。【画面：古老森林小路，雾气弥漫】")
    assert frames is not None
    assert len(frames) == 1, f"discard 帧应被丢弃，got {len(frames)}"
    assert frames[0]["narration"] == "小明走进了古老的森林"
    assert "forest" in frames[0]["image_prompt"].lower()


@pytest.mark.asyncio
async def test_parse_article_discards_title_and_ads():
    """公众号文章 → 标题/广告 discard，正文进 narration。"""
    from app.gateway.video_gen.orchestration.script import parse_script_to_frames

    llm = _JSONLLM(
        '{"content_type":"article","frames":[{"narration":"","image_prompt":"","discard":true},{"narration":"人工智能正在改变世界","image_prompt":"","discard":false},{"narration":"扫码关注获取更多","image_prompt":"","discard":true}]}'
    )
    frames = await parse_script_to_frames(llm, "# 标题\n人工智能正在改变世界\n扫码关注")
    assert frames is not None
    assert len(frames) == 1, f"应只保留正文帧，got {len(frames)}"
    assert "人工智能" in frames[0]["narration"]


@pytest.mark.asyncio
async def test_parse_narration_only():
    """纯讲解稿 → 全部进 narration，无 image_prompt。"""
    from app.gateway.video_gen.orchestration.script import parse_script_to_frames

    llm = _JSONLLM('{"content_type":"narration_only","frames":[{"narration":"第一句讲解","image_prompt":"","discard":false},{"narration":"第二句讲解","image_prompt":"","discard":false}]}')
    frames = await parse_script_to_frames(llm, "第一句讲解。第二句讲解。")
    assert len(frames) == 2
    assert all(f["narration"] for f in frames)


@pytest.mark.asyncio
async def test_parse_failure_returns_none():
    """LLM 返回非 JSON → None（供调用方 fallback 到 split）。"""
    from app.gateway.video_gen.orchestration.script import parse_script_to_frames

    llm = _JSONLLM("这不是 JSON")
    result = await parse_script_to_frames(llm, "some text")
    assert result is None


@pytest.mark.asyncio
async def test_parse_empty_frames_returns_none():
    """所有帧 discard 或空 narration → None。"""
    from app.gateway.video_gen.orchestration.script import parse_script_to_frames

    llm = _JSONLLM('{"content_type":"article","frames":[{"narration":"","discard":true}]}')
    result = await parse_script_to_frames(llm, "广告内容")
    assert result is None


# ---------------------------------------------------------------------------
# M3-1: content fidelity — relax char cap + derive target_scenes from length.
# ---------------------------------------------------------------------------


def test_target_scenes_from_len_proportional():
    """860字应推导出足够镜数（≥15，非恒10）；长文更多；clamp [8,80]。"""
    from app.gateway.video_gen.orchestration.script import _target_scenes_from_len

    assert _target_scenes_from_len("x" * 860) >= 15  # ceil(860/60)=15
    assert _target_scenes_from_len("x" * 3000) >= 50  # ceil(3000/60)=50
    assert _target_scenes_from_len("x" * 10) == 8  # clamp 下限 8
    assert _target_scenes_from_len("x" * 6000) == 80  # clamp 上限 80


def test_parse_system_prompts_content_fidelity():
    """M3-1: _PARSE_SYSTEM 须要求内容保真（≥90% 覆盖）+ 单镜字数上限 + discard 仅噪声。

    2026-08-14 C1 修订：字数口径「30-80 字/句」→「单镜≤40字，内容多就拆成多镜」
    （与拆镜预算口径 12s×3.33字/s 对齐，宁多镜不可丢内容）。
    """
    from app.gateway.video_gen.orchestration.script import _PARSE_SYSTEM

    assert "90%" in _PARSE_SYSTEM or "覆盖全部" in _PARSE_SYSTEM, "须要求内容覆盖 ≥90%"
    assert "单镜≤40字" in _PARSE_SYSTEM, "须要求单镜≤40字、内容多就拆成多镜"
    assert "多镜" in _PARSE_SYSTEM, "须提示拆成多镜而非压缩内容"
    # discard 仅纯噪声；标题/数据/结论须保留改写进 narration
    assert "正文" in _PARSE_SYSTEM or "结论" in _PARSE_SYSTEM or "数据" in _PARSE_SYSTEM


def test_parse_system_image_prompt_language_aligned_with_narration():
    """P-04（2026-08-18 两反馈批）：image_prompt 指令不得再要求英文描述（历史遗留反向指令）。

    该字段产物在 service 两条调用路径均只取 narration、image_prompt 被丢弃，
    指令从不生效——但留着会与 storyboard_planner 的语言锚（与旁白一致）打架，
    防误导后续复用，统一为同口径。
    """
    from app.gateway.video_gen.orchestration.script import _PARSE_SYSTEM

    assert "英文描述" not in _PARSE_SYSTEM, "image_prompt 不应要求英文描述（与旁白语言锚口径冲突）"
    assert "与旁白" in _PARSE_SYSTEM, "image_prompt 语言口径须与旁白一致（语言锚同口径）"
