"""content-match T4+T5：planner 路由提示词重写 + 写回闸门解除（VIDEO_GEN_CONTENT_ROUTING）。

- T4：系统词含内容类型→画法映射表与 graphic 枚举；按任务主引擎动态收窄
  （ai_image 任务只许 {ai_image, graphic}）；不含 remotion 产出枚举；
  JSON 契约键 frames/image_prompt/visual_source 不变
- T5：开关 on → ai_image/ai_video 任务写回 LLM 推荐（修前 explicit_type 闸门丢弃）；
  off → 与现状完全一致（丢弃）；vimax 两态都写回（现状不变）
"""

from __future__ import annotations

import pytest

from app.gateway.video_gen.orchestration import storyboard_planner as planner
from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame


class _FakeLLM:
    """按调用序返回预设 content；记录全部 messages 供提示词断言。"""

    def __init__(self, contents: list[str]):
        self._contents = list(contents)
        self.messages: list[list[dict]] = []

    async def ainvoke(self, messages):
        self.messages.append(messages)
        content = self._contents.pop(0) if self._contents else "{}"

        class _Resp:
            pass

        _Resp.content = content
        return _Resp()


def _sb(video_type: str, n: int = 2) -> Storyboard:
    cfg = StoryboardConfig(video_type=video_type, width=1920, height=1080)
    return Storyboard(title="t", config=cfg, frames=[StoryboardFrame(index=i, narration=f"旁白{i}") for i in range(n)])


def _frames_json(sources: list[str]) -> str:
    import json

    entries = [{"image_prompt": f"p{i}", "visual_source": s} for i, s in enumerate(sources)]
    return json.dumps({"frames": entries}, ensure_ascii=False)


def _anchor_json() -> str:
    """T5（2026-08-18）：plan_course 分块前多一次锚定调用——list 型 _FakeLLM 须按调用序
    预置锚定应答（否则锚定调用吃掉 frames JSON、块调用拿到空仓"{}"）。"""
    import json

    return json.dumps({"anchor_card": "锚定卡测试正文", "style_string": "统一风格串"}, ensure_ascii=False)


@pytest.fixture(autouse=True)
def _routing_on(monkeypatch):
    monkeypatch.setenv("VIDEO_GEN_CONTENT_ROUTING", "1")


class TestPromptRewrite:
    def _capture_system(self, video_type: str) -> str:
        llm = _FakeLLM([_anchor_json(), _frames_json(["ai_image", "ai_image"])])
        sb = _sb(video_type) if video_type != "ai_video" else _sb("ai_video")
        if video_type == "ai_video":
            sb = _sb("ai_video")
        import asyncio

        asyncio.run(planner.plan_course(llm, storyboard=sb, chunk_size=8))  # E8: 块级写回语义走批量路径
        # messages[0] = T5 锚定调用；块 system 在 messages[1]
        return " ".join(str(m.get("content", "")) for m in llm.messages[1])

    def test_ai_image_prompt_has_mapping_table_and_narrow_enum(self):
        system = self._capture_system("ai_image")
        assert "graphic" in system
        assert "visual_source 只能从这两个值中选择: ai_image, graphic" in system
        # 内容类型→画法映射表关键词
        assert "数据/统计" in system and "过程/步骤" in system and "多样" in system
        # remotion 移出产出枚举
        assert "remotion, ai_image, ai_video" not in system

    def test_ai_video_prompt_narrows_to_ai_video_and_graphic(self):
        system = self._capture_system("ai_video")
        assert "visual_source 只能从这两个值中选择: ai_video, graphic" in system

    def test_contract_keys_unchanged(self):
        """JSON 契约键 frames[]/image_prompt/visual_source 不动（承重，勿改）。"""
        system = self._capture_system("ai_image")
        assert '"frames"' in system and '"image_prompt"' in system and '"visual_source"' in system

    def test_legacy_types_keep_old_three_enum(self):
        """vimax 等非 explicit 任务维持旧三值枚举（现状不变量）。"""
        llm = _FakeLLM([_anchor_json(), _frames_json(["ai_image", "ai_video"])])
        sb = _sb("vimax")
        import asyncio

        asyncio.run(planner.plan_course(llm, storyboard=sb, chunk_size=8))  # E8: 块级写回语义走批量路径
        system = " ".join(str(m.get("content", "")) for m in llm.messages[1])
        assert "remotion, ai_image, ai_video" in system


class TestWritebackTwoStates:
    def _run(self, video_type: str, sources: list[str]) -> Storyboard:
        # 第二次 ainvoke 是 T7 extractor：为 graphic 帧补一份合法 spec 应答
        # （无数字要点 → 逐字符校验必然通过），否则 D-A 降级会把帧降回主引擎
        import json

        specs = [{"index": i, "spec": {"type": "text_card", "title": "t", "points": ["甲要点", "乙要点"]}} for i, sv in enumerate(sources) if sv == "graphic"]
        llm = _FakeLLM([_anchor_json(), _frames_json(sources), json.dumps({"specs": specs}, ensure_ascii=False)])
        sb = _sb(video_type, n=len(sources))
        import asyncio

        asyncio.run(planner.plan_course(llm, storyboard=sb, chunk_size=8))  # E8: 块级写回语义走批量路径
        return sb

    def test_switch_on_writes_back_graphic_recommendation(self):
        """T5 核心：on 时 ai_image 任务写回 LLM 逐镜推荐（修前被 explicit_type 丢弃）。
        graphic 落中间帧——首末镜护栏不触碰（Ends 规则单独测）。4 帧 1 graphic=25%
        ≤30% 占比上限（2026-08-31 统一 0.3，3 帧 33% 会被占比规则回退、遮蔽本用例
        的写回语义断言）。"""
        sb = self._run("ai_image", ["ai_image", "graphic", "ai_image", "ai_image"])
        assert sb.frames[0].visual_source == "ai_image"
        assert sb.frames[1].visual_source == "graphic"
        assert sb.frames[2].visual_source == "ai_image"
        assert sb.frames[3].visual_source == "ai_image"

    def test_switch_off_drops_recommendation_unchanged_behavior(self, monkeypatch):
        monkeypatch.setenv("VIDEO_GEN_CONTENT_ROUTING", "0")
        sb = self._run("ai_image", ["ai_image", "graphic"])
        assert all(f.visual_source is None for f in sb.frames), "off = 修前行为：推荐被丢弃（且 off 清空已写回值）"

    def test_unset_env_defaults_on_writes_back(self, monkeypatch):
        """T2 尾巴（计划 2026-08-18 §2 T2）：未设 VIDEO_GEN_CONTENT_ROUTING → 默认 on。

        本文件其余用例全靠 autouse setenv("1")（P-02 审计坐实的测试缺口）——测不到
        「默认值被翻转」这类回归。此处 delenv 后端到端走 plan_course，默认一旦被
        翻成 off 该用例即红（守护 D2 默认 on 语义）。4 帧 1 graphic=25%≤30%
        （占比护栏不回退——夹具口径同 test_switch_on_writes_back）。"""
        monkeypatch.delenv("VIDEO_GEN_CONTENT_ROUTING", raising=False)
        sb = self._run("ai_image", ["ai_image", "graphic", "ai_image", "ai_image"])
        assert sb.frames[1].visual_source == "graphic", "未设 env 须按默认 on 写回 graphic 推荐"

    def test_vimax_writeback_both_states(self, monkeypatch):
        """vimax（非 explicit_type）两态都写回——引擎层不消费，但行为不变量受护栏测试保护。"""
        monkeypatch.setenv("VIDEO_GEN_CONTENT_ROUTING", "1")
        sb = self._run("vimax", ["ai_video", "remotion"])
        assert sb.frames[0].visual_source == "ai_video"
        assert sb.frames[1].visual_source == "remotion", "vimax 不经 normalize，原值保留"

    def test_switch_on_ai_video_task_rejects_cross_engine_then_guardrail(self):
        """ai_video 任务推荐 ai_image（跨引擎）→ 写回后护栏回退主引擎（D8）。"""
        sb = self._run("ai_video", ["ai_video", "ai_image"])
        assert all(f.visual_source == "ai_video" for f in sb.frames)


class TestSourceAlignmentSemantics:
    """T5 语义修正：sources 未对齐只丢 sources，不连坐 prompts（修前 vimax 路径连坐）。"""

    def test_llm_omits_visual_source_field_prompts_survive(self):
        """LLM 忽略 visual_source 字段（旧式输出，全空串）→ prompts 保留、帧落默认类型。"""
        import json

        entries = [{"image_prompt": f"p{i}"} for i in range(2)]  # 无 visual_source 键
        llm = _FakeLLM([_anchor_json(), json.dumps({"frames": entries}, ensure_ascii=False)])
        sb = _sb("ai_image", n=2)
        import asyncio

        asyncio.run(planner.plan_course(llm, storyboard=sb, chunk_size=8))  # E8: 块级写回语义走批量路径
        assert sb.frames[0].image_prompt and sb.frames[0].image_prompt.startswith("p")
        assert sb.frames[0].visual_source == "ai_image"  # 空推荐 → 默认（normalize 收敛）

    def test_source_count_mismatch_drops_sources_only(self):
        """sources 条数不符（早停）→ 只丢该块 sources，prompts 仍写回。"""
        import json

        # 帧数 2、返回 frames 2，但第二个帧缺 visual_source 键（= 空推荐）
        entries2 = [{"image_prompt": "p0", "visual_source": "graphic"}, {"image_prompt": "p1"}]
        llm = _FakeLLM([_anchor_json(), json.dumps({"frames": entries2}, ensure_ascii=False)])
        sb = _sb("ai_image", n=2)
        import asyncio

        asyncio.run(planner.plan_course(llm, storyboard=sb, chunk_size=8))  # E8: 块级写回语义走批量路径
        assert sb.frames[0].visual_source == "ai_image", "空串推荐（键缺失）→ 不写回，帧保默认"
        assert sb.frames[1].visual_source == "ai_image"


class TestPlanCourseCallsGuardrailAndExtractor:
    """plan_course 末尾调用 normalize（T6）+ extractor（T7）——接线断言。"""

    def test_normalize_and_extractor_called_after_fill(self, monkeypatch):
        calls: list[str] = []

        def fake_normalize(sb, *, protect=frozenset(), **kwargs):  # T3: ratio_override 透传（桩宽容 kwargs）
            calls.append(("normalize", frozenset(protect)))

        async def fake_extract(llm, sb, task_dir=None, protect=frozenset()):
            calls.append(("extract", frozenset(protect)))
            return 0  # W3 日志行消费返回值——桩必须返回 int（真实现恒 int）

        monkeypatch.setattr("app.gateway.video_gen.orchestration.storyboard_planner.normalize_visual_routing", fake_normalize)
        monkeypatch.setattr("app.gateway.video_gen.orchestration.storyboard_planner.extract_graphic_specs", fake_extract)
        llm = _FakeLLM([_frames_json(["ai_image", "ai_image"])])
        sb = _sb("ai_image")
        import asyncio

        asyncio.run(planner.plan_course(llm, storyboard=sb, chunk_size=8))  # E8: 块级写回语义走批量路径
        assert [c[0] for c in calls] == ["normalize", "extract"]
        assert calls[0][1] == frozenset(), "全新 storyboard：无用户手改帧（protect 空）"

    def test_remotion_returns_before_guardrail(self, monkeypatch):
        calls: list[str] = []
        monkeypatch.setattr(
            "app.gateway.video_gen.orchestration.storyboard_planner.normalize_visual_routing",
            lambda sb: calls.append("normalize"),
        )
        sb = _sb("remotion")
        import asyncio

        asyncio.run(planner.plan_course(_FakeLLM(["{}"]), storyboard=sb))
        assert calls == []


class TestRoutingGateThreshold:
    """T5：graphic 准入门槛——单句不上卡、要点/步骤/数字有下限。"""

    def test_prompt_contains_admission_gate(self):
        system = TestPromptRewrite()._capture_system("ai_image")
        assert "≥2 条并列要点" in system, "text_card 须有要点下限"
        assert "≥3 步" in system, "steps 须有步数下限"
        assert "单句陈述" in system, "须有单句反例（不上卡）"
        assert "宁可选 graphic" in system, "须有零路由校准的倾向规则（08-17）"

    def test_prompt_gate_applies_to_ai_video_too(self):
        system = TestPromptRewrite()._capture_system("ai_video")
        assert "≥2 条并列要点" in system and "单句陈述" in system
