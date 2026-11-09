"""E5 扩展：vimax narration_generator 的 LLM 调用也须有界超时（T5 计划只列了 3 个文件，此为同缺陷类的未覆盖点）。"""

import asyncio

import pytest


class _HangingLLM:
    async def ainvoke(self, messages):
        await asyncio.sleep(30)
        return None


class _OkResp:
    content = "旁白文本"


class _OkLLM:
    async def ainvoke(self, messages):
        return _OkResp()


@pytest.mark.asyncio
async def test_generate_narration_text_bounded_on_timeout():
    """LLM hang 时 _generate_narration_text 在有界时间内抛 RuntimeError（不无限挂）。"""
    from app.gateway.video_gen.engines.vimax.narration_generator import _generate_narration_text

    with pytest.raises(RuntimeError, match="timed out|timeout|超时"):
        await _generate_narration_text(_HangingLLM(), "shot desc", "ctx", timeout=0.05)


@pytest.mark.asyncio
async def test_generate_narration_text_returns_on_success():
    """正常返回时透传 LLM 内容（不回归）。"""
    from app.gateway.video_gen.engines.vimax.narration_generator import _generate_narration_text

    text = await _generate_narration_text(_OkLLM(), "shot desc", "ctx")
    assert text == "旁白文本"
