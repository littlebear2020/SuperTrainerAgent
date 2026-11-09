"""content-match T7：graphic spec 提取 LLM pass（仅 graphic 帧，chunk 分块，逐帧过校验）。

- mock LLM → 合法 spec 写回 frame.graphic_spec
- 幻觉数字 → 拒绝 → spec 保持 None + KIND_GRAPHIC_SPEC_REJECTED sidecar（task_dir 提供时）
- chunk 失败（异常/坏 JSON）不拖垮整批 → 该批帧按缺失降级（spec=None，常规画面）
- 非 graphic 帧零调用（llm.ainvoke 只被 graphic 批触发）
- spec 形状不合法（超 4 要点等）→ 拒绝
"""

from __future__ import annotations

import json

import pytest

from app.gateway.video_gen.degradations import KIND_GRAPHIC_SPEC_FIELD_OMITTED, KIND_GRAPHIC_SPEC_REJECTED, read_degradations
from app.gateway.video_gen.orchestration.graphic_spec_extractor import extract_graphic_specs
from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame


class _FakeLLM:
    def __init__(self, contents: list[str]):
        self._contents = list(contents)
        self.calls = 0

    async def ainvoke(self, messages):
        self.calls += 1
        content = self._contents.pop(0) if self._contents else "{}"

        class _Resp:
            pass

        _Resp.content = content
        return _Resp()


class _BoomLLM:
    calls = 0

    async def ainvoke(self, messages):
        type(self).calls += 1
        raise RuntimeError("LLM down")


def _sb(sources: list[str], narrations: list[str] | None = None) -> Storyboard:
    cfg = StoryboardConfig(video_type="ai_image", width=1920, height=1080)
    narrations = narrations or [f"旁白{i}" for i in range(len(sources))]
    return Storyboard(title="t", config=cfg, frames=[StoryboardFrame(index=i, narration=n, visual_source=s) for i, (s, n) in enumerate(zip(sources, narrations))])


def _specs_json(mapping: dict[int, dict]) -> str:
    return json.dumps({"specs": [{"index": i, "spec": s} for i, s in mapping.items()]}, ensure_ascii=False)


@pytest.mark.asyncio
async def test_valid_specs_written_back():
    sb = _sb(["graphic", "ai_image", "graphic"], narrations=["增长37%，非常快。", "普通旁白", "先做A，再做B。"])
    llm = _FakeLLM(
        [
            _specs_json({0: {"type": "big_number", "number": "37", "unit": "%"}, 2: {"type": "steps", "steps": ["先做A", "再做B"]}}),
        ]
    )
    written = await extract_graphic_specs(llm, sb)
    assert written == 2
    assert sb.frames[0].graphic_spec == {"type": "big_number", "number": "37", "unit": "%"}
    assert sb.frames[1].graphic_spec is None, "非 graphic 帧不参与"
    assert sb.frames[2].graphic_spec["type"] == "steps"


@pytest.mark.asyncio
async def test_hallucinated_number_rejected_with_sidecar(tmp_path):
    sb = _sb(["graphic"], narrations=["实际只增长了37%。"])
    llm = _FakeLLM([_specs_json({0: {"type": "big_number", "number": "48", "unit": "%"}})])
    written = await extract_graphic_specs(llm, sb, task_dir=tmp_path)
    assert written == 0
    assert sb.frames[0].graphic_spec is None, "幻觉 spec 不得写回"
    entries = [e for e in read_degradations(tmp_path) if e["kind"] == KIND_GRAPHIC_SPEC_REJECTED]
    assert entries and entries[0]["frame_index"] == 0
    assert "48" in entries[0]["detail"], "拒绝原因含具体数字（可读）"


@pytest.mark.asyncio
async def test_no_task_dir_still_works_no_sidecar(tmp_path):
    """task_dir 缺省（旧调用形态）→ 不写 sidecar 但流程不崩。"""
    sb = _sb(["graphic"], narrations=["增长37%。"])
    llm = _FakeLLM([_specs_json({0: {"type": "big_number", "number": "37"}})])
    written = await extract_graphic_specs(llm, sb)
    assert written == 1
    assert not (tmp_path / "degradations.json").exists()


@pytest.mark.asyncio
async def test_malformed_spec_rejected():
    """形状不合法（超 4 要点）→ parse 拒绝 → spec=None。"""
    sb = _sb(["graphic"], narrations=["要点一。要点二。"])
    llm = _FakeLLM([_specs_json({0: {"type": "text_card", "title": "t", "points": ["a", "b", "c", "d", "e"]}})])
    written = await extract_graphic_specs(llm, sb)
    assert written == 0
    assert sb.frames[0].graphic_spec is None


@pytest.mark.asyncio
async def test_chunk_failure_does_not_kill_batch(tmp_path):
    """单块异常（LLM 挂）→ 该块帧按缺失降级，整体不抛。"""
    sb = _sb(["graphic"], narrations=["内容。"])
    written = await extract_graphic_specs(_BoomLLM(), sb, task_dir=tmp_path)
    assert written == 0
    assert sb.frames[0].graphic_spec is None  # 渲染期按常规画面出图


@pytest.mark.asyncio
async def test_bad_json_does_not_kill_batch():
    sb = _sb(["graphic"], narrations=["内容。"])
    llm = _FakeLLM(["这不是 JSON"])
    written = await extract_graphic_specs(llm, sb)
    assert written == 0


@pytest.mark.asyncio
async def test_no_graphic_frames_zero_llm_calls():
    sb = _sb(["ai_image", "ai_video"])
    llm = _FakeLLM([])
    written = await extract_graphic_specs(llm, sb)
    assert written == 0
    assert llm.calls == 0, "无 graphic 帧不得产生任何 LLM 调用"


@pytest.mark.asyncio
async def test_chunking_aligns_with_convention(monkeypatch):
    """CHUNK=8 分块：9 个 graphic 帧 → 2 次 LLM 调用。"""
    sources = ["graphic"] * 9
    sb = _sb(sources, narrations=[f"第{i}条旁白。" for i in range(9)])
    llm = _FakeLLM([_specs_json({})])
    await extract_graphic_specs(llm, sb)
    assert llm.calls == 2


@pytest.mark.asyncio
async def test_parse_reject_reason_surfaces_in_sidecar(tmp_path):
    """2026-09-02 标题截断修复：parse 拒绝的**真实字段级原因**透传进 sidecar detail
    ——修前 parse 失败一律写死「spec 形状不合法（含单点伪归纳）」，无从排查。"""
    sb = _sb(["graphic"], narrations=["强调一个数字。"])
    llm = _FakeLLM([_specs_json({0: {"type": "big_number", "number": ""}})])
    await extract_graphic_specs(llm, sb, task_dir=tmp_path)
    entries = [e for e in read_degradations(tmp_path) if e["kind"] == KIND_GRAPHIC_SPEC_REJECTED]
    assert entries and "number" in entries[0]["detail"], f"detail 须含 parse 真实原因（缺 number），实际：{entries}"


@pytest.mark.asyncio
async def test_field_omission_keeps_frame_and_records_sidecar(tmp_path):
    """字段级省略（2026-09-02 产品拍板）：标题 >14 字 → spec 写回（标题置空）、
    **帧不降级**（卡片必出）、KIND_GRAPHIC_SPEC_FIELD_OMITTED 留痕。"""
    sb = _sb(["graphic"], narrations=["要点甲。要点乙。"])
    llm = _FakeLLM([_specs_json({0: {"type": "text_card", "title": "甲" * 15, "points": ["要点甲", "要点乙"]}})])
    written = await extract_graphic_specs(llm, sb, task_dir=tmp_path)
    assert written == 1, "省略不是拒绝，spec 照常写回"
    assert sb.frames[0].graphic_spec["title"] == ""
    assert sb.frames[0].visual_source == "graphic", "字段级省略不降级（卡片照出）"
    entries = [e for e in read_degradations(tmp_path) if e["kind"] == KIND_GRAPHIC_SPEC_FIELD_OMITTED]
    assert entries and "标题超长" in entries[0]["detail"] and entries[0]["frame_index"] == 0
    rejected = [e for e in read_degradations(tmp_path) if e["kind"] == KIND_GRAPHIC_SPEC_REJECTED]
    assert not rejected, "省略与拒绝是两级，不得混入 rejected 留痕"


@pytest.mark.asyncio
async def test_field_omission_without_task_dir_no_sidecar_no_crash():
    """task_dir 缺省时字段级省略不留痕但不崩（与拒绝路径同形态）。"""
    sb = _sb(["graphic"], narrations=["要点甲。要点乙。"])
    llm = _FakeLLM([_specs_json({0: {"type": "text_card", "title": "甲" * 15, "points": ["要点甲", "要点乙"]}})])
    written = await extract_graphic_specs(llm, sb)
    assert written == 1 and sb.frames[0].visual_source == "graphic"


def test_extract_prompt_states_render_budget_and_consequence():
    """提取提示词写明 14 字标题预算、中英各计 1 字计数口径与超限后果（源头收敛：
    LLM 字符计数不可靠，不写口径它会自认合规地产出超长标题）。"""
    from app.gateway.video_gen.orchestration.graphic_spec_extractor import _SPEC_EXTRACT_SYSTEM

    assert "14字" in _SPEC_EXTRACT_SYSTEM
    assert "各计 1 字" in _SPEC_EXTRACT_SYSTEM, "计数口径必须写明（LLM 会把英文词当 1 个词）"
    assert "30字" not in _SPEC_EXTRACT_SYSTEM, "旧 30 字标题预算不得残留"
    assert _SPEC_EXTRACT_SYSTEM.count("不予显示") >= 3, "标题 + text_card/steps 标签的超限后果共 ≥3 处"


def test_extract_prompt_states_label_word_folding():
    """2026-09-03 词折算：标签计数规则（英文词每词折 2 汉字、术语可直接用英文）
    必须写进提示词——否则 LLM 会为压字数把术语意译掉，卡片失去复现术语的意义。"""
    from app.gateway.video_gen.orchestration.graphic_spec_extractor import _SPEC_EXTRACT_SYSTEM

    assert "折 2 个汉字" in _SPEC_EXTRACT_SYSTEM, "词折算规则须写明"
    assert "直接用英文" in _SPEC_EXTRACT_SYSTEM, "明确允许术语保留英文（压字数≠意译丢术语）"


class TestDemotionOnRejection:
    """T6（D-A 已拍板）：规划期 spec 拒绝/缺失 → 降级主引擎（visual_source=None），
    sidecar 注明；protect 帧（用户手改）不降级，维持 graphic 标记（渲染期常规画面）。"""

    @pytest.mark.asyncio
    async def test_rejected_spec_demotes_frame_to_main_engine(self, tmp_path):
        sb = _sb(["graphic"], narrations=["实际只增长了37%。"])
        llm = _FakeLLM([_specs_json({0: {"type": "big_number", "number": "48", "unit": "%"}})])
        await extract_graphic_specs(llm, sb, task_dir=tmp_path)
        assert sb.frames[0].visual_source is None, "幻觉拒绝后应降级主引擎（出 AI 画面）"
        entries = [e for e in read_degradations(tmp_path) if e["kind"] == KIND_GRAPHIC_SPEC_REJECTED]
        assert entries and "降级" in entries[0]["detail"]

    @pytest.mark.asyncio
    async def test_missing_spec_demotes_too(self, tmp_path):
        """LLM 未返回该帧 spec（拒答/漏帧）→ 同样降级（本就不该上卡的内容）。"""
        sb = _sb(["graphic", "graphic"], narrations=["第一句。", "第二句。"])
        llm = _FakeLLM([_specs_json({1: {"type": "text_card", "title": "t", "points": ["a", "b"]}})])
        await extract_graphic_specs(llm, sb, task_dir=tmp_path)
        assert sb.frames[0].visual_source is None
        assert sb.frames[1].visual_source == "graphic", "成功帧不受影响"

    @pytest.mark.asyncio
    async def test_malformed_spec_demotes(self, tmp_path):
        sb = _sb(["graphic"], narrations=["要点。"])
        llm = _FakeLLM([_specs_json({0: {"type": "text_card", "title": "t", "points": ["a"]}})])  # 单点伪归纳
        await extract_graphic_specs(llm, sb, task_dir=tmp_path)
        assert sb.frames[0].visual_source is None, "单点 text_card（形状收紧后拒绝）→ 降级"

    @pytest.mark.asyncio
    async def test_protected_frame_not_demoted(self, tmp_path):
        """protect 帧（用户手动 graphic）拒绝 → 保持 graphic 标记，渲染期按常规画面出图。"""
        sb = _sb(["graphic"], narrations=["内容。"])
        llm = _FakeLLM(["坏 JSON"])
        await extract_graphic_specs(llm, sb, task_dir=tmp_path, protect={0})
        assert sb.frames[0].visual_source == "graphic", "手动帧不降级（D5 渲染期语义保持）"
        # sidecar 注明「protect 帧维持图文镜」（区别于降级帧的「已降级主引擎」前缀）
