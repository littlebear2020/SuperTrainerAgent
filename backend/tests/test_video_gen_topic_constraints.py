"""T4: topic 生成接入时长/画面风格约束。"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.gateway.video_gen.orchestration import script as script_mod


def _llm() -> MagicMock:
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=MagicMock(content="第一句。\n第二句。\n第三句。"))
    return llm


@pytest.mark.asyncio
async def test_target_duration_drives_word_count():
    """target_duration=30s → 按语速反推每句字数，注入 prompt。"""
    llm = _llm()
    await script_mod.generate_narrations(llm, topic="红烧肉", n_scenes=3, target_duration=30.0)
    user_msg = llm.ainvoke.call_args[0][0][1]["content"]
    assert "字" in user_msg  # 含字数约束
    assert "30" in user_msg  # 含目标时长


@pytest.mark.asyncio
async def test_style_injected_into_prompt():
    """style='水彩插画' → 注入画面风格描述到 system prompt。"""
    llm = _llm()
    await script_mod.generate_narrations(llm, topic="红烧肉", n_scenes=3, style="水彩插画")
    msgs = llm.ainvoke.call_args[0][0]
    blob = " ".join(m["content"] for m in msgs)
    assert "水彩插画" in blob


@pytest.mark.asyncio
async def test_default_style_not_injected():
    """style='default' → 不注入画面风格（避免无意义描述）。"""
    llm = _llm()
    await script_mod.generate_narrations(llm, topic="红烧肉", n_scenes=3, style="default")
    system_msg = llm.ainvoke.call_args[0][0][0]["content"]
    assert "画面风格为" not in system_msg


@pytest.mark.asyncio
async def test_no_constraints_backwards_compatible():
    """不传 duration/style → 行为与旧版一致（不崩）。"""
    result = await script_mod.generate_narrations(_llm(), topic="短题", n_scenes=2)
    assert len(result) == 3
