"""Tests for AssistService doc_type style-hint injection (light channel)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.gateway.content.assist_service import (
    AssistRequest,
    AssistService,
)
from app.gateway.content.schemas import DocType

# ---------------------------------------------------------------------------
# Test service: build without a real LLM (prompt building is pure logic)
# ---------------------------------------------------------------------------


@pytest.fixture()
def service() -> AssistService:
    """An AssistService whose LLM factory is stubbed out.

    ``_build_prompt`` only consults the request and the class-level
    ``_MODE_HINTS`` map, so it does not need a configured model.
    """
    with patch("deerflow.models.create_chat_model", return_value=MagicMock()):
        return AssistService()


# ---------------------------------------------------------------------------
# 1. op-lookup branch injects the hint
# ---------------------------------------------------------------------------


def test_build_prompt_includes_doc_type_style_hint(service: AssistService):
    """A ``polish`` request with ``doc_type="course"`` must surface the
    course style hint in the built prompt."""
    req = AssistRequest(op="polish", text="这是一段需要润色的中文文本。", doc_type="course")
    prompt = service._build_prompt(req)
    # "严谨" is a distinctive word in the course hint.
    assert "风格要求：" in prompt
    assert "严谨" in prompt


# ---------------------------------------------------------------------------
# 2. article vs course produce different prompts
# ---------------------------------------------------------------------------


def test_doc_type_article_vs_course_differ(service: AssistService):
    """Two otherwise-identical requests differing only in ``doc_type`` must
    produce prompts that differ in the style-hint portion."""
    base = dict(op="polish", text="这是一段需要润色的中文文本。")
    article_prompt = service._build_prompt(AssistRequest(**base, doc_type="article"))
    course_prompt = service._build_prompt(AssistRequest(**base, doc_type="course"))

    # Article hint present in article prompt, absent from course prompt.
    assert "面向公众号" in article_prompt
    assert "面向公众号" not in course_prompt
    # Course hint present in course prompt, absent from article prompt.
    assert "严谨" in course_prompt
    assert "严谨" not in article_prompt
    # The two prompts are not identical.
    assert article_prompt != course_prompt


# ---------------------------------------------------------------------------
# 3. general / None inject no hint (both branches)
# ---------------------------------------------------------------------------


def test_doc_type_general_or_none_injects_no_hint(service: AssistService):
    """``doc_type="general"`` and ``doc_type=None`` must not inject a
    ``风格要求：`` line, in both the op-lookup and instruction branches."""
    text = "这是一段中文文本。"

    # --- op-lookup branch ---
    req_general = AssistRequest(op="polish", text=text, doc_type="general")
    assert req_general.doc_type == "general"
    prompt_general = service._build_prompt(req_general)
    assert "风格要求：" not in prompt_general

    req_none = AssistRequest(op="polish", text=text)
    assert req_none.doc_type is None
    prompt_none = service._build_prompt(req_none)
    assert "风格要求：" not in prompt_none

    # --- instruction branch ---
    req_instr_general = AssistRequest(op="polish", text=text, instruction="改成更正式", mode="rewrite", doc_type="general")
    assert req_instr_general.doc_type == "general"
    assert "风格要求：" not in service._build_prompt(req_instr_general)


# ---------------------------------------------------------------------------
# 4. instruction branch also injects the hint
# ---------------------------------------------------------------------------


def test_doc_type_hint_appears_in_instruction_branch_too(service: AssistService):
    """An instruction-mode request with ``doc_type="case"`` must include the
    case style hint, covering the ``instruction`` branch (not just OP_PROMPTS)."""
    req = AssistRequest(
        op="polish",
        text="这是一个商业案例的描述文本。",
        instruction="改成更正式",
        mode="rewrite",
        doc_type="case",
    )
    prompt = service._build_prompt(req)
    assert "风格要求：" in prompt
    # "数据驱动" is a distinctive word in the case hint.
    assert "数据驱动" in prompt


# ---------------------------------------------------------------------------
# 5. DOC_TYPE_STYLE_HINTS map sanity
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("dt", ["article", "case", "script", "podcast", "course", "general"])
def test_doc_type_style_hints_covers_all_doc_types(dt: DocType):
    """Every value in the ``DocType`` literal has a real, non-empty hint."""
    from app.gateway.content.assist_service import DOC_TYPE_STYLE_HINTS

    hint = DOC_TYPE_STYLE_HINTS[dt]
    assert isinstance(hint, str) and hint.strip()


# ---------------------------------------------------------------------------
# 6. shorten op — valid on AssistRequest, has an OP_PROMPTS template (W2b)
# ---------------------------------------------------------------------------


def test_shorten_op_valid_and_in_op_prompts():
    """``shorten`` is a valid ``AssistRequest`` op and ``OP_PROMPTS`` has a
    ``.format()``-able template for it. Before W2b, ``shorten`` was not a
    valid op (the full-draft shorten path used a dummy ``op:"summarize"``)."""
    from app.gateway.content.assist_service import OP_PROMPTS

    # No ValidationError → ``shorten`` is accepted by the op pattern.
    req = AssistRequest(op="shorten", text="这是一段需要精简的中文文本。", full_doc=True)
    assert req.op == "shorten"

    # OP_PROMPTS has a ``shorten`` entry that formats without error.
    assert "shorten" in OP_PROMPTS
    formatted = OP_PROMPTS["shorten"].format(text="测试文本")
    assert "测试文本" in formatted


# ---------------------------------------------------------------------------
# 7. shorten (no instruction) takes the OP_PROMPTS branch, not instruction
# ---------------------------------------------------------------------------


def test_build_prompt_shorten_uses_op_branch(service: AssistService):
    """For ``op="shorten"`` with NO instruction, ``_build_prompt`` must take
    the OP_PROMPTS branch (shorten template wording), not the
    instruction+mode branch. This confirms shorten no longer needs the
    dummy-op/instruction hack."""
    req = AssistRequest(op="shorten", text="这是一段需要精简的中文文本。", full_doc=True)
    prompt = service._build_prompt(req)

    # The shorten template's distinctive wording must appear.
    assert "精简" in prompt
    # The instruction-branch scaffolding must NOT appear.
    assert "编辑模式" not in prompt
    assert "用户要求" not in prompt


# ---------------------------------------------------------------------------
# 8. shorten prompt does not leak summarize semantics (dummy-op regression guard)
# ---------------------------------------------------------------------------


def test_shorten_no_dummy_summarize(service: AssistService):
    """Building the prompt for ``op="shorten"`` must not reference summarize
    semantics — regression guard: the dummy ``op:"summarize"`` is gone and
    the shorten template is distinct from the summarize one (which collapses
    to 1-2 sentences)."""
    req = AssistRequest(op="shorten", text="这是一段需要精简的中文文本。", full_doc=True)
    prompt = service._build_prompt(req)

    # Summarize-specific wording must NOT appear in a shorten prompt.
    assert "1-2 句话" not in prompt
    assert "概括" not in prompt


# ---------------------------------------------------------------------------
# 9. instruction + op both set (mode absent) → op-flavored hint (W9 §4.4)
# ---------------------------------------------------------------------------


def test_build_prompt_instruction_plus_op_uses_op_flavor(service: AssistService):
    """When BOTH ``instruction`` AND ``op`` are set and ``mode`` is absent —
    the selection follow-up refine case — ``_build_prompt`` must derive the
    instruction-branch hint from ``req.op`` (expand→扩写), NOT the old
    mode-based default (改写). Before W9 a follow-up hardcoded mode:"polish"
    on the heavy channel; on the light channel mode=None defaulted the hint to
    改写 regardless of the round's op (§4.4 bug)."""
    req = AssistRequest(
        op="expand",
        instruction="更简短一些",
        text="这是一段中文文本，需要根据用户要求进行调整。",
    )
    assert req.mode is None  # follow-up sends no mode
    prompt = service._build_prompt(req)

    # Instruction-branch scaffolding is present (not the op-only branch).
    assert "编辑模式" in prompt
    assert "用户要求" in prompt
    # The hint is the EXPAND flavor (扩写), NOT the mode-based default (改写).
    # req.text contains neither keyword, so this is a clean discriminator.
    assert "扩写" in prompt


# ---------------------------------------------------------------------------
# 10. instruction-only (mode set) keeps mode-based hint — regression (W9)
# ---------------------------------------------------------------------------


def test_build_prompt_instruction_only_keeps_mode_behavior(service: AssistService):
    """An instruction-mode request with ``mode`` explicitly set must keep the
    mode-based hint (regression: instruction-only path unchanged by the W9
    op-honor tweak). ``op`` is a REQUIRED field (W2b decided to ADD ops, not
    make op Optional), so the instruction-only analog uses ``op="rewrite"``
    (the frontend default for custom-instruction edits) with ``mode="shorten"``
    → the hint must be 精简 (from mode), NOT 改写 (from op=rewrite). This guards
    against an "op always wins" regression."""
    req = AssistRequest(
        op="rewrite",
        instruction="改得更短",
        mode="shorten",
        text="这是一段中文文本。",
    )
    prompt = service._build_prompt(req)

    assert "编辑模式" in prompt
    # mode="shorten" → 精简 (mode-based), NOT 改写 (op=rewrite).
    assert "精简" in prompt


# ---------------------------------------------------------------------------
# 11. op-only (no instruction) unchanged — regression (W9)
# ---------------------------------------------------------------------------


def test_build_prompt_op_only_unchanged(service: AssistService):
    """``op="polish"`` with NO instruction must still use the OP_PROMPTS branch
    (polish template), not the instruction+op branch (regression: op-only path
    unchanged by W9)."""
    req = AssistRequest(op="polish", text="这是一段中文文本。")
    assert req.instruction is None
    prompt = service._build_prompt(req)

    # Polish template wording appears.
    assert "润色" in prompt
    # Instruction-branch scaffolding must NOT appear.
    assert "编辑模式" not in prompt
    assert "用户要求" not in prompt


# ---------------------------------------------------------------------------
# P0-3: full_doc output max_tokens must scale with the 20000-char input limit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_doc_max_tokens_supports_long_docs():
    """P0-3: full_doc 允许 20000 字输入，输出 max_tokens 必须 ≥16000。

    旧的 8000 让长文 polish/expand（要求原文 1.5-2 倍）必中途截断；且 W10
    diff 闸门 >6000 字只显示结果，截断最难发现。"""
    captured: dict = {}

    class FakeChunk:
        content = "x"

    class FakeLLM:
        def bind(self, **kwargs):
            captured.update(kwargs)
            return self

        async def astream(self, messages, config=None):
            yield FakeChunk()

    with patch("deerflow.models.create_chat_model", return_value=FakeLLM()), patch("app.gateway.content.assist_service.metrics"):
        svc = AssistService()
        req = AssistRequest(op="polish", text="y" * 9000, full_doc=True)
        async for _ in svc.stream(req, user_id="u"):
            pass

    assert captured["max_tokens"] >= 16000, captured


# ---------------------------------------------------------------------------
# Fix 1.1: full_doc（全文编辑）走 thinking 开的质量档；选区编辑走 fast 档
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_doc_uses_thinking_enabled_model():
    """全文编辑（full_doc=True）必须用 thinking_enabled=True 的质量模型；
    选区编辑（full_doc=False）用 thinking 关闭的快速模型。"""
    from app.gateway.content.assist_service import AssistRequest, AssistService

    built: dict = {}

    def fake_create(name, thinking_enabled=False, **kw):
        class M:
            def __init__(self) -> None:
                self.thinking = thinking_enabled
                self.astream_called = 0

            def bind(self, **kwargs):
                return self

            async def astream(self, messages, config=None):
                self.astream_called += 1

                class C:
                    content = "x"

                yield C()

        inst = M()
        built[thinking_enabled] = inst
        return inst

    with patch("deerflow.models.create_chat_model", side_effect=fake_create), patch("app.gateway.content.assist_service.metrics"):
        svc = AssistService()
        # __init__ 建了两个模型：fast(thinking off) + quality(thinking on)
        assert set(built.keys()) == {False, True}

        async for _ in svc.stream(AssistRequest(op="polish", text="x", full_doc=True), user_id="u"):
            pass
        async for _ in svc.stream(AssistRequest(op="polish", text="x", full_doc=False), user_id="u"):
            pass

    # full_doc 走 quality，选区走 fast，各一次
    assert built[True].astream_called == 1
    assert built[False].astream_called == 1


# ---------------------------------------------------------------------------
# P1-4: max_tokens/temperature must reach the model via bind(), not config
# ---------------------------------------------------------------------------


class _RecordingLLM:
    """Stub chat model: bind() records kwargs and returns self; astream yields once."""

    def __init__(self) -> None:
        self.bound: dict | None = None
        self.astream_config: dict | None = None

    def bind(self, **kwargs):
        self.bound = kwargs
        return self

    async def astream(self, messages, config=None):
        self.astream_config = config
        yield MagicMock(content="好的")


@pytest.mark.asyncio
async def test_selection_edit_binds_2000_max_tokens():
    with patch("deerflow.models.create_chat_model", return_value=MagicMock()):
        svc = AssistService()
    rec = _RecordingLLM()
    svc._llm_fast = rec
    req = AssistRequest(op="polish", text="短文本")
    async for _ in svc.stream(req, user_id="u1"):
        pass
    assert rec.bound == {"max_tokens": 2000, "temperature": 0.7}


@pytest.mark.asyncio
async def test_full_doc_binds_16384_max_tokens():
    with patch("deerflow.models.create_chat_model", return_value=MagicMock()):
        svc = AssistService()
    rec = _RecordingLLM()
    svc._llm_quality = rec
    req = AssistRequest(op="polish", text="长文本", full_doc=True)
    async for _ in svc.stream(req, user_id="u1"):
        pass
    assert rec.bound == {"max_tokens": 16384, "temperature": 0.7}


# ---------------------------------------------------------------------------
# 6b. B3-T1: continue op validation + prompt + hints
# ---------------------------------------------------------------------------

from app.gateway.content.assist_service import OP_PROMPTS  # noqa: E402


def test_continue_op_valid_schema():
    """B3-T1: op=continue passes AssistRequest validation."""
    req = AssistRequest(op="continue", text="这是一段需要续写的文本。")
    assert req.op == "continue"


def test_continue_op_in_op_prompts():
    """B3-T1: OP_PROMPTS has a 'continue' entry containing '续写'."""
    assert "continue" in OP_PROMPTS
    assert "续写" in OP_PROMPTS["continue"]


def test_continue_op_hint_present():
    """B3-T1: _OP_HINTS includes 'continue' → '续写'."""
    with patch("deerflow.models.create_chat_model", return_value=MagicMock()):
        svc = AssistService()
    assert svc._OP_HINTS.get("continue") == "续写"


def test_continue_op_builds_correct_prompt(service: AssistService):
    """B3-T1: _build_prompt with op=continue produces a prompt with the text."""
    req = AssistRequest(op="continue", text="前文内容。")
    prompt = service._build_prompt(req)
    assert "前文内容" in prompt
    assert "续写" in prompt


# ---------------------------------------------------------------------------
# C2 (2026-09-02): 流式无数据超时——上游僵死时 60s 自动放弃,正常慢流不误掐
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_idle_timeout_sends_error_frame(monkeypatch):
    """C2 闭环:超时不再裸 raise 断连——流内发 type:error 帧、不发 [DONE],
    usage 按部分用量记 error_downstream,metric 记 error 不记 ok。"""
    import asyncio as _asyncio
    import json as _json

    class FakeChunk:
        content = "x"
        usage_metadata = {"input_tokens": 3, "output_tokens": 1, "total_tokens": 4}

    class HungLLM:
        def bind(self, **kwargs):
            return self

        async def astream(self, messages, config=None):
            yield FakeChunk()  # 先出一片(带部分用量)
            await _asyncio.sleep(999)  # 然后僵死:再无数据

    monkeypatch.setenv("ASSIST_STREAM_IDLE_TIMEOUT", "0.2")
    with (
        patch("deerflow.models.create_chat_model", return_value=HungLLM()),
        patch("app.gateway.content.assist_service.metrics") as metrics_mock,
        patch("app.gateway._shared.llm_usage_sink.record_llm_usage", new_callable=AsyncMock) as usage_mock,
    ):
        svc = AssistService()
        req = AssistRequest(op="polish", text="hello")
        frames: list[str] = []
        async for piece in svc.stream(req, user_id="u"):
            frames.append(piece)

    parsed = [_json.loads(f[len("data: ") :]) for f in frames if f.startswith("data: ") and "[DONE]" not in f]
    errors = [p for p in parsed if p.get("type") == "error"]
    assert len(errors) == 1
    assert errors[0]["content"] == "AI 响应超时,请重试"
    # 超时不发 [DONE](防前端把错误收尾当正常完成)
    assert not any("[DONE]" in f for f in frames)
    # 已收到部分用量 → error_downstream,且只记一次(不双计)
    assert usage_mock.await_count == 1
    assert usage_mock.await_args.kwargs["status"] == "error_downstream"
    inc_labels = [c.kwargs["labels"] for c in metrics_mock.inc.call_args_list]
    assert {"op": "polish", "status": "error"} in inc_labels
    assert not any(lbl.get("status") == "ok" for lbl in inc_labels)


@pytest.mark.asyncio
async def test_stream_slow_but_progressing_not_cut(monkeypatch):
    """慢但在输出的流不被误掐:每个 chunk 间隔 0.1s(远小于阈值)正常完成。"""
    import asyncio as _asyncio

    class FakeChunk:
        def __init__(self, c):
            self.content = c

    class SlowLLM:
        def bind(self, **kwargs):
            return self

        async def astream(self, messages, config=None):
            for i in range(3):
                await _asyncio.sleep(0.1)
                yield FakeChunk(f"c{i}")

    monkeypatch.setenv("ASSIST_STREAM_IDLE_TIMEOUT", "0.5")
    got: list[str] = []
    with patch("deerflow.models.create_chat_model", return_value=SlowLLM()), patch("app.gateway.content.assist_service.metrics"):
        svc = AssistService()
        req = AssistRequest(op="polish", text="hello")
        async for piece in svc.stream(req, user_id="u"):
            got.append(piece)
    assert any("c2" in p for p in got)
    # 正常完成仍发 [DONE](仅超时路径跳过)
    assert any("[DONE]" in p for p in got)


@pytest.mark.asyncio
async def test_stream_idle_timeout_before_first_chunk(monkeypatch):
    """C2 补:首片前即僵死(0 chunk)——无部分用量,usage 记 error(非 error_downstream)。"""
    import asyncio as _asyncio
    import json as _json

    class HungFromStartLLM:
        def bind(self, **kwargs):
            return self

        async def astream(self, messages, config=None):
            await _asyncio.sleep(999)  # 一个字节都不出
            yield  # pragma: no cover - 永不到达

    monkeypatch.setenv("ASSIST_STREAM_IDLE_TIMEOUT", "0.2")
    monkeypatch.setenv("ASSIST_STREAM_FIRST_CHUNK_TIMEOUT", "0.2")  # 否则首片走 240s 默认档,测试会挂死
    with patch("deerflow.models.create_chat_model", return_value=HungFromStartLLM()), patch("app.gateway.content.assist_service.metrics"), patch("app.gateway._shared.llm_usage_sink.record_llm_usage", new_callable=AsyncMock) as usage_mock:
        svc = AssistService()
        req = AssistRequest(op="polish", text="hello")
        frames: list[str] = []
        async for piece in svc.stream(req, user_id="u"):
            frames.append(piece)

    parsed = [_json.loads(f[len("data: ") :]) for f in frames if f.startswith("data: ")]
    assert [p.get("type") for p in parsed] == ["error"]
    assert parsed[0]["content"] == "AI 响应超时,请重试"
    assert not any("[DONE]" in f for f in frames)
    assert usage_mock.await_count == 1
    assert usage_mock.await_args.kwargs["status"] == "error"


@pytest.mark.asyncio
async def test_stream_first_chunk_uses_wider_window(monkeypatch):
    """复核反馈问题 1:首片前静默走 240s 档(对齐模型层首片预算)——
    idle 0.2s 不误杀 1s 才到的首片;首片到达后回落 idle 档掐后续僵死。"""
    import asyncio as _asyncio
    import json as _json

    class FakeChunk:
        content = "first"

    class SlowStartLLM:
        def bind(self, **kwargs):
            return self

        async def astream(self, messages, config=None):
            await _asyncio.sleep(1.0)  # > idle 0.2、< first 5:合法慢首片
            yield FakeChunk()
            await _asyncio.sleep(999)  # 首片后僵死 → idle 档掐断

    monkeypatch.setenv("ASSIST_STREAM_IDLE_TIMEOUT", "0.2")
    monkeypatch.setenv("ASSIST_STREAM_FIRST_CHUNK_TIMEOUT", "5")
    with patch("deerflow.models.create_chat_model", return_value=SlowStartLLM()), patch("app.gateway.content.assist_service.metrics"), patch("app.gateway._shared.llm_usage_sink.record_llm_usage", new_callable=AsyncMock):
        svc = AssistService()
        req = AssistRequest(op="polish", text="hello")
        frames: list[str] = []
        async for piece in svc.stream(req, user_id="u"):
            frames.append(piece)

    parsed = [_json.loads(f[len("data: ") :]) for f in frames if f.startswith("data: ") and "[DONE]" not in f]
    # 首片未被 0.2s 误杀,已送达
    assert any(p.get("type") == "text" and "first" in p.get("text", "") for p in parsed)
    # 首片后僵死 → idle 档超时 → error 帧,无 [DONE]
    errors = [p for p in parsed if p.get("type") == "error"]
    assert len(errors) == 1
    assert errors[0]["content"] == "AI 响应超时,请重试"
    assert not any("[DONE]" in f for f in frames)


@pytest.mark.asyncio
async def test_stream_midflight_exception_sends_error_frame(monkeypatch):
    """复核反馈问题 2:中途 provider 异常不再裸 raise 断连——发 type:error 帧
    (携带异常摘要)、不发 [DONE],usage 记 error_downstream,消费端不抛。"""
    import json as _json

    class FakeChunk:
        content = "x"
        usage_metadata = {"input_tokens": 2, "output_tokens": 1, "total_tokens": 3}

    class ExplodingLLM:
        def bind(self, **kwargs):
            return self

        async def astream(self, messages, config=None):
            yield FakeChunk()
            raise RuntimeError("provider exploded")

    with (
        patch("deerflow.models.create_chat_model", return_value=ExplodingLLM()),
        patch("app.gateway.content.assist_service.metrics") as metrics_mock,
        patch("app.gateway._shared.llm_usage_sink.record_llm_usage", new_callable=AsyncMock) as usage_mock,
    ):
        svc = AssistService()
        req = AssistRequest(op="polish", text="hello")
        frames: list[str] = []
        async for piece in svc.stream(req, user_id="u"):  # 修复前这里会 raise
            frames.append(piece)

    parsed = [_json.loads(f[len("data: ") :]) for f in frames if f.startswith("data: ") and "[DONE]" not in f]
    errors = [p for p in parsed if p.get("type") == "error"]
    assert len(errors) == 1
    assert "provider exploded" in errors[0]["content"]
    assert not any("[DONE]" in f for f in frames)
    assert usage_mock.await_count == 1
    assert usage_mock.await_args.kwargs["status"] == "error_downstream"
    inc_labels = [c.kwargs["labels"] for c in metrics_mock.inc.call_args_list]
    assert {"op": "polish", "status": "error"} in inc_labels
    assert not any(lbl.get("status") == "ok" for lbl in inc_labels)
