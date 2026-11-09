"""generate 模式：长输入走忠实切分（兜底），短输入走 LLM 生成。"""

from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_long_input_uses_faithful_split():
    """输入 >80 字 → 不调 LLM 重写，直接忠实切分（R5：兜底，主修在前端模式区分）。"""
    from app.gateway.video_gen.orchestration import script as script_mod

    long_text = "这是第一句完整的脚本内容，比较长。" * 10
    llm = AsyncMock()

    result = await script_mod.generate_narrations(llm, topic=long_text)

    llm.ainvoke.assert_not_called()
    assert len(result) > 1
    assert "这是第一句完整的脚本内容" in "".join(result)


@pytest.mark.asyncio
async def test_short_input_uses_llm_generation():
    """输入 ≤80 字 → 走 LLM 生成（现有行为不变）。"""
    from app.gateway.video_gen.orchestration import script as script_mod

    short_topic = "新员工入职培训"
    llm = AsyncMock()
    llm.ainvoke.return_value = type("R", (), {"content": "欢迎加入团队。\n这是你的工位。"})()

    await script_mod.generate_narrations(llm, topic=short_topic)

    llm.ainvoke.assert_called_once()
