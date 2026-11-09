"""Phase C1: parse_script_to_frames 分块循环调用 on_progress 回调。"""

import json

import pytest


class _Resp:
    def __init__(self, content):
        self.content = content


@pytest.mark.asyncio
async def test_parse_script_reports_chunk_progress(monkeypatch):
    """on_progress 在每个 chunk 处理后被调用，最后一次 done==total。"""
    from app.gateway.video_gen.orchestration import script as script_mod

    # 强制 3 个 chunk，可控验证
    monkeypatch.setattr(script_mod, "_chunk_text", lambda text: ["c0", "c1", "c2"])

    async def _fake_invoke(llm, messages):
        return _Resp(json.dumps({"frames": [{"narration": "n", "image_prompt": "p", "discard": False}]}))

    monkeypatch.setattr(script_mod, "_invoke_llm_bounded", _fake_invoke)

    class _LLM:
        pass

    seen = []

    async def on_progress(done, total):
        seen.append((done, total))

    frames = await script_mod.parse_script_to_frames(_LLM(), "ignored", on_progress=on_progress)
    assert frames is not None and len(frames) > 0
    assert seen == [(1, 3), (2, 3), (3, 3)]


@pytest.mark.asyncio
async def test_parse_script_progress_optional(monkeypatch):
    """不传 on_progress 时行为不变（向后兼容）。"""
    from app.gateway.video_gen.orchestration import script as script_mod

    monkeypatch.setattr(script_mod, "_chunk_text", lambda text: ["c0"])

    async def _fake_invoke(llm, messages):
        return _Resp(json.dumps({"frames": [{"narration": "n", "image_prompt": "p", "discard": False}]}))

    monkeypatch.setattr(script_mod, "_invoke_llm_bounded", _fake_invoke)

    class _LLM:
        pass

    # 不传 on_progress，不应 raise
    frames = await script_mod.parse_script_to_frames(_LLM(), "x")
    assert frames is not None
