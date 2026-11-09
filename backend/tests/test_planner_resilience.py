"""P1-E9 + QP1-6 守护：画面词生成容错链。

修复前：bulk gather 无 return_exceptions（planner 内 _fill_image_prompts_robust）→
单块失败整批抛错，单帧兜底机制被绕过。修后：单块/单帧失败被吸收，失败帧最终由
QP1-6 的 _topic_fallback_prompt 兜底（绝不回退 narration 原文）。
"""

import pytest

from app.gateway.video_gen.orchestration import storyboard_planner as planner
from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame


def _sb(n: int, video_type: str = "ai_image") -> Storyboard:
    sb = Storyboard(title="t", config=StoryboardConfig(video_type=video_type, width=1152, height=2048))
    for i in range(n):
        sb.frames.append(StoryboardFrame(index=i, narration=f"旁白{i}"))
    return sb


@pytest.mark.asyncio
async def test_bulk_chunk_failure_falls_back_to_single_frame(monkeypatch):
    """一个 CHUNK 抛错 → 其余块正常；失败块帧走单帧兜底补齐（修复前整批 raise）。"""

    async def fake_enhanced(llm, mini, style_prefix, **_kwargs):
        if any(f.narration == "旁白3" for f in mini.frames):
            raise RuntimeError("chunk llm down")
        return [f"bulk-p{f.index}" for f in mini.frames], ["" for _ in mini.frames]

    async def fake_single(llm, narrations, **kwargs):
        return [f"single-p:{narrations[0]}"]

    monkeypatch.setattr(planner, "_generate_course_prompts_enhanced", fake_enhanced)
    monkeypatch.setattr(planner, "_generate_image_prompts", fake_single)

    sb = _sb(10)  # CHUNK=8 → 两块：[0..7]（含旁白3 抛错）+ [8..9]（正常）
    await planner.plan_course(None, storyboard=sb, chunk_size=8)  # E8: bulk 抗性语义走批量路径

    assert sb.frames[8].image_prompt == "bulk-p0", "成功块的帧保留 bulk 产物"
    # 失败块帧走单帧兜底；T-I0-②（2026-08-17）起单帧兜底产物=LLM 原文（前置/后缀已退役）
    assert sb.frames[3].image_prompt == "single-p:旁白3", "失败块帧走单帧兜底（T-I0-② 后产物=LLM 原文）"


@pytest.mark.asyncio
async def test_all_failure_uses_topic_fallback_without_raising(monkeypatch):
    """bulk + 单帧兜底全失败 → 任务不抛错；帧由 QP1-6 主题意象兜底（非空、非 narration）。"""

    async def fake_enhanced(llm, mini, style_prefix, **_kwargs):
        raise RuntimeError("all chunks down")

    async def fake_single(llm, narrations, **kwargs):
        raise RuntimeError("single down")

    monkeypatch.setattr(planner, "_generate_course_prompts_enhanced", fake_enhanced)
    monkeypatch.setattr(planner, "_generate_image_prompts", fake_single)

    sb = _sb(2)
    await planner.plan_course(None, storyboard=sb, chunk_size=8)  # E8: bulk 抗性语义走批量路径  # 修复前：RuntimeError 整批抛出

    expected = planner._topic_fallback_prompt(sb)
    for f in sb.frames:
        assert f.image_prompt == expected, "全失败帧由 QP1-6 主题意象兜底"
        assert f.image_prompt != f.narration, "绝不回退 narration 原文"


@pytest.mark.asyncio
async def test_empty_prompts_never_equal_narration(monkeypatch):
    """QP1-6 守护：所有 LLM 返空 → image_prompt 绝不等于 narration 原文（现行代码已满足）。"""

    async def fake_enhanced(llm, mini, style_prefix, **_kwargs):
        return ["" for _ in mini.frames], ["" for _ in mini.frames]

    async def fake_single(llm, narrations, **kwargs):
        return [""]

    monkeypatch.setattr(planner, "_generate_course_prompts_enhanced", fake_enhanced)
    monkeypatch.setattr(planner, "_generate_image_prompts", fake_single)

    sb = _sb(1)
    await planner.plan_course(None, storyboard=sb, chunk_size=8)  # E8: bulk 抗性语义走批量路径
    assert sb.frames[0].image_prompt, "主题意象兜底非空"
    assert sb.frames[0].image_prompt != "旁白0", "绝不等于 narration 原文"


# ── BUG 1-A 对齐校验测试 ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bulk_early_stop_falls_back_to_per_frame(monkeypatch):
    """BUG 1-A：LLM 早停（返 7 条而非 8 条）-> 整块不可信，该块帧走单帧兜底。"""

    async def fake_enhanced(llm, mini, style_prefix, **_kwargs):
        # 返 7 条而非 chunk 长度 8（模拟早停）
        return [f"bulk-p{j}" for j in range(7)], ["" for _ in range(7)]

    async def fake_single(llm, narrations, **kwargs):
        return [f"single-p:{narrations[0]}"]

    monkeypatch.setattr(planner, "_generate_course_prompts_enhanced", fake_enhanced)
    monkeypatch.setattr(planner, "_generate_image_prompts", fake_single)

    sb = _sb(8)  # 一块 8 帧，LLM 返 7 -> 整块丢弃
    await planner.plan_course(None, storyboard=sb, chunk_size=8)  # E8: bulk 抗性语义走批量路径

    # 全部帧走单帧兜底（bulk 被判未对齐而丢弃）
    for i, f in enumerate(sb.frames):
        assert f.image_prompt == f"single-p:旁白{i}", f"帧 {i} 应走单帧兜底（bulk 未对齐被丢弃）"


@pytest.mark.asyncio
async def test_bulk_over_return_falls_back_to_per_frame(monkeypatch):
    """BUG 1-A：LLM 多返（返 10 条而非 8 条）-> 整块不可信，该块帧走单帧兜底。"""

    async def fake_enhanced(llm, mini, style_prefix, **_kwargs):
        # 返 10 条而非 chunk 长度 8（模拟多返）
        return [f"bulk-p{j}" for j in range(10)], ["" for _ in range(10)]

    async def fake_single(llm, narrations, **kwargs):
        return [f"single-p:{narrations[0]}"]

    monkeypatch.setattr(planner, "_generate_course_prompts_enhanced", fake_enhanced)
    monkeypatch.setattr(planner, "_generate_image_prompts", fake_single)

    sb = _sb(8)
    await planner.plan_course(None, storyboard=sb, chunk_size=8)  # E8: bulk 抗性语义走批量路径

    for i, f in enumerate(sb.frames):
        assert f.image_prompt == f"single-p:旁白{i}", f"帧 {i} 应走单帧兜底（bulk 多返被丢弃）"


@pytest.mark.asyncio
async def test_bulk_empty_string_counts_as_unaligned(monkeypatch):
    """BUG 1-A：LLM 返空串（条数对但有空串）-> 整块不可信，走单帧兜底。"""

    async def fake_enhanced(llm, mini, style_prefix, **_kwargs):
        # 8 条但第 3 条是空串
        prompts = [f"bulk-p{j}" for j in range(8)]
        prompts[3] = ""
        return prompts, ["" for _ in range(8)]

    async def fake_single(llm, narrations, **kwargs):
        return [f"single-p:{narrations[0]}"]

    monkeypatch.setattr(planner, "_generate_course_prompts_enhanced", fake_enhanced)
    monkeypatch.setattr(planner, "_generate_image_prompts", fake_single)

    sb = _sb(8)
    await planner.plan_course(None, storyboard=sb, chunk_size=8)  # E8: bulk 抗性语义走批量路径

    for i, f in enumerate(sb.frames):
        assert f.image_prompt == f"single-p:旁白{i}", f"帧 {i} 应走单帧兜底（bulk 含空串被丢弃）"


@pytest.mark.asyncio
async def test_aligned_bulk_preserves_prompts(monkeypatch):
    """BUG 1-A 正向验证：LLM 返条数正确且无空串 -> bulk 产物保留，不走单帧兜底。"""

    async def fake_enhanced(llm, mini, style_prefix, **_kwargs):
        return [f"bulk-p{j}" for j in range(len(mini.frames))], ["" for _ in range(len(mini.frames))]

    async def fake_single(llm, narrations, **kwargs):
        return ["SHOULD-NOT-BE-USED"]

    monkeypatch.setattr(planner, "_generate_course_prompts_enhanced", fake_enhanced)
    monkeypatch.setattr(planner, "_generate_image_prompts", fake_single)

    sb = _sb(8)
    await planner.plan_course(None, storyboard=sb, chunk_size=8)  # E8: bulk 抗性语义走批量路径

    for i, f in enumerate(sb.frames):
        assert f.image_prompt.startswith("bulk-p"), f"帧 {i} 应保留 bulk 产物"


# ── 2026-08-19：兜底链风格注入 + 补齐重问守护 ─────────────────────────────
# P0：unaligned 降级链（单帧兜底/主题兜底/重生端点）修前零风格信息——corporate_live
# 隐喻帧事故根因。P1：unaligned 先「补齐重问」一次再落单帧（8 次单帧更贵更易超时）。


class _CapturingLLM:
    """捕获 messages 的最小 fake（ainvoke 契约，_invoke_llm_bounded 直调）。"""

    def __init__(self, content: str):
        self.calls: list[list[dict]] = []
        self._content = content

    async def ainvoke(self, messages):
        self.calls.append(messages)

        class _Resp:
            content = self._content

        return _Resp()


@pytest.mark.asyncio
async def test_unaligned_chunk_reasked_once_with_count_constraint(monkeypatch):
    """P1 补齐重问：unaligned 块先带条数强约束重问一次同块；重问对齐则保留 bulk 产物，
    不落 8 次单帧兜底。"""
    calls: list[dict] = []

    async def fake_enhanced(llm, mini, style_prefix, extra_system="", **_kwargs):
        calls.append({"n": len(mini.frames), "extra_system": extra_system})
        if len(calls) == 1:
            # 8 帧只返 4 条（corporate_live 事故实测形态）
            return [f"bulk-p{j}" for j in range(4)], ["" for _ in range(4)]
        return [f"reask-p{j}" for j in range(len(mini.frames))], ["" for _ in range(len(mini.frames))]

    async def fake_single(llm, narrations, **kwargs):
        return ["SHOULD-NOT-BE-USED"]

    monkeypatch.setattr(planner, "_generate_course_prompts_enhanced", fake_enhanced)
    monkeypatch.setattr(planner, "_generate_image_prompts", fake_single)

    sb = _sb(8)
    await planner.plan_course(None, storyboard=sb, chunk_size=8)  # E8: bulk 抗性语义走批量路径

    assert len(calls) == 2, "同块先重问一次"
    assert "数量硬约束" in calls[1]["extra_system"], "重问携带条数强约束"
    assert "精确返回 8 条" in calls[1]["extra_system"]
    for i, f in enumerate(sb.frames):
        assert f.image_prompt == f"reask-p{i}", f"帧 {i} 应保留重问后的 bulk 产物（不落单帧）"


@pytest.mark.asyncio
async def test_per_frame_fallback_receives_style_guard(monkeypatch):
    """P0：重问仍不齐 → 单帧兜底收到 style_guard（风格硬约束+实拍禁隐喻+锚定卡，
    与 bulk system 同源）。修前单帧 system 零风格信息 = 隐喻帧失守点。"""
    anchor = {"anchor_card": "全片为科技公司真人实拍场景", "style_string": "写实商务实拍"}
    guards: list[str] = []

    async def fake_anchor(llm, **kwargs):
        return anchor

    async def fake_enhanced(llm, mini, style_prefix, extra_system="", **_kwargs):
        return ["x"], [""]  # 恒不对齐（含重问）

    async def fake_single(llm, narrations, **kwargs):
        guards.append(kwargs.get("style_guard", ""))
        return [f"single-p:{narrations[0]}"]

    monkeypatch.setattr(planner, "_generate_global_anchor", fake_anchor)
    monkeypatch.setattr(planner, "_generate_course_prompts_enhanced", fake_enhanced)
    monkeypatch.setattr(planner, "_generate_image_prompts", fake_single)

    sb = _sb(2)
    await planner.plan_course(None, storyboard=sb, style_context={"style_prefix": "商务实拍风格"}, chunk_size=8)  # E8: 单帧兜底 style_guard 属批量路径

    assert guards, "单帧兜底应被调用"
    for g in guards:
        assert "商务实拍风格" in g, "风格硬约束进单帧 system"
        assert "实拍禁隐喻条款" in g, "实拍禁隐喻条款进单帧 system"
        assert "全片为科技公司真人实拍场景" in g, "锚定卡进单帧 system"


def test_topic_fallback_prompt_carries_anchor_and_live_action():
    """P0：主题兜底带锚定卡骨架 + 实拍正向尾；无锚有风格 → 风格前置；
    渲染期口径（不传 style_prefix）→ 行为与修前逐字一致。
    2026-08-19 审查跟进：锚定卡禁用清单段被剥离、实拍尾正向化（白熊效应——
    本函数产物直喂生图模型，负向措辞不被理解、被禁概念原词反成提示）。"""
    sb = _sb(1)
    sb.global_anchor = {"anchor_card": "全片为科技公司真人实拍场景，主角为年轻男开发。禁用清单：禁止机械装置、器物隐喻（如天平、齿轮）"}
    p = planner._topic_fallback_prompt(sb, "商务实拍风格")
    assert "全片为科技公司真人实拍场景" in p, "锚定卡正向段进主题兜底"
    assert "主角为年轻男开发" in p, "锚定卡正向段保留"
    assert "禁止" not in p and "天平" not in p, "禁用清单段被剥离（白熊效应）"
    assert "真人实景行为" in p, "实拍尾为正向表述"

    sb2 = _sb(1)
    p2 = planner._topic_fallback_prompt(sb2, "水彩插画风格")
    assert p2.startswith("水彩插画风格"), "无锚有风格 → 风格前置"
    assert "真人实景行为" not in p2, "非实拍不带实拍尾"

    p3 = planner._topic_fallback_prompt(sb2)
    assert p3 == "以「t」为主题的具体写实教学场景，清晰主体，柔和均匀光线，简洁构图", "渲染期口径不变"


@pytest.mark.asyncio
async def test_bulk_system_contains_exact_count_constraint():
    """P1 LLM 守约性：bulk system 含「精确 N 条」硬约束；extra_system 附加到 system 尾部。"""
    sb = _sb(3)
    llm = _CapturingLLM('{"frames": [{"image_prompt": "p0"}, {"image_prompt": "p1"}, {"image_prompt": "p2"}]}')
    prompts, _sources = await planner._generate_course_prompts_enhanced(llm, sb, "", extra_system="EXTRA-MARKER")
    system = llm.calls[0][0]["content"]
    assert "必须精确包含 3 条" in system, "条数硬约束进 bulk system"
    assert system.endswith("EXTRA-MARKER"), "extra_system 附加到 system 尾部"
    assert prompts == ["p0", "p1", "p2"]


@pytest.mark.asyncio
async def test_parse_failure_fallback_receives_style_guard():
    """P0 补缺：enhanced JSON 解析失败 → 逐帧兜底路径也必须带风格守卫——
    style_prefix 与锚定卡均在作用域内，修前此分支静默丢失全部风格约束
    （与单帧兜底同型的失守点，反馈2-1"兜底 prompt 构造处"覆盖范围）。"""
    anchor = {"anchor_card": "全片为科技公司真人实拍场景", "style_string": "写实商务实拍"}
    sb = _sb(2)
    sb.global_anchor = anchor
    # 恒返回不可解析内容：第一次 enhanced 调用解析失败 → 走 except 兜底分支
    llm = _CapturingLLM("不是JSON")

    prompts, _sources = await planner._generate_course_prompts_enhanced(llm, sb, "商务实拍风格")

    assert prompts == [], "不可解析内容兜底返回空表（既有行为）"
    assert len(llm.calls) == 2, "解析失败后应发起第二次（兜底）LLM 调用"
    fallback_system = llm.calls[1][0]["content"]
    assert "商务实拍风格" in fallback_system, "风格硬约束进解析失败兜底 system"
    assert "实拍禁隐喻条款" in fallback_system, "实拍禁隐喻条款进解析失败兜底 system"
    assert "全片为科技公司真人实拍场景" in fallback_system, "锚定卡进解析失败兜底 system"
