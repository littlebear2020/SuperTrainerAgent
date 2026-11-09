"""C1: normalize_script — 把上传文档正文经 LLM 改写成干净、TTS 友好的旁白。

fixed 模式修前纯正则 split_fixed_script，文档标题/页码/项目符号被原样念出。
"""

import pytest


class _FakeLLM:
    def __init__(self, reply):
        self.reply = reply
        self.captured = None

    async def ainvoke(self, messages):
        self.captured = messages

        class _R:
            content = self.reply

        return _R()


@pytest.mark.asyncio
async def test_normalize_script_strips_non_narrative_and_returns_clean_text():
    """文档正文（含标题/页码）→ LLM 改写 → 干净旁白，无非叙事残留。"""
    from app.gateway.video_gen.orchestration.script import normalize_script

    llm = _FakeLLM("人工智能正在改变世界。\n从医疗到交通，它无处不在。")
    out, _truncated = await normalize_script(llm, "第一章 概述\n第1页\n· 项目符号\n人工智能正在改变世界。", target_scenes=2)
    assert "第1页" not in out and "项目符号" not in out, "应剥除非叙事内容（页码/项目符号）"
    assert "人工智能" in out


@pytest.mark.asyncio
async def test_normalize_script_uses_bounded_invoke(monkeypatch):
    """normalize_script 须经 _invoke_llm_bounded（C1 复用 T5 超时保护）。"""
    from app.gateway.video_gen.orchestration import script as script_mod

    called = {}

    async def fake_bounded(llm, messages, *, timeout=120):
        called["yes"] = True

        class _R:
            content = "改写后的旁白。"

        return _R()

    monkeypatch.setattr(script_mod, "_invoke_llm_bounded", fake_bounded)
    out, _truncated = await script_mod.normalize_script(_FakeLLM("x"), "原始文档", target_scenes=3)
    assert called.get("yes") is True
    assert "改写" in out


class _EchoLLM:
    """回显 user 消息内容，记录调用次数（验证分块）。"""

    def __init__(self):
        self.calls = 0

    async def ainvoke(self, messages):
        self.calls += 1
        from types import SimpleNamespace

        return SimpleNamespace(content=messages[1]["content"])


@pytest.mark.asyncio
async def test_normalize_script_covers_long_input_not_truncated():
    """S1: 长文档（>4000 字）normalize 须全覆盖（分块重写），不静默丢尾。"""
    from app.gateway.video_gen.orchestration.script import normalize_script

    long_text = "开头导入。" + ("这是一段正文内容，讲述一些事情。" * 300) + "尾标记句在这里结束"
    assert len(long_text) > 4000
    llm = _EchoLLM()
    normalized, truncated = await normalize_script(llm, long_text, target_scenes=10)
    assert "尾标记句在这里结束" in normalized, "长文须全覆盖（修前 [:4000] 丢尾）"
    assert llm.calls > 1, "长文应分多块调用 LLM"
    assert truncated is False


@pytest.mark.asyncio
async def test_normalize_script_chunk_failure_keeps_raw_and_marks_truncated(monkeypatch):
    """S1: 某块 LLM 失败 → 保留该块原文 + truncated=True（非静默丢内容）。"""
    from app.gateway.video_gen.orchestration import script as script_mod

    long_text = "段一内容。" * 500 + "\n" + "段二内容尾标记。" * 500  # 两块以上
    call = {"n": 0}

    async def fake_bounded(llm, messages, *, timeout=120):
        call["n"] += 1
        if call["n"] == 1:  # 第一块失败
            raise RuntimeError("LLM boom")
        from types import SimpleNamespace

        return SimpleNamespace(content=messages[1]["content"])

    monkeypatch.setattr(script_mod, "_invoke_llm_bounded", fake_bounded)
    normalized, truncated = await script_mod.normalize_script(_EchoLLM(), long_text, target_scenes=10)
    assert truncated is True, "有块失败须标 truncated"
    assert "段一内容" in normalized, "失败块须保留原文（非丢弃）"
    assert "尾标记" in normalized, "成功块仍处理"
