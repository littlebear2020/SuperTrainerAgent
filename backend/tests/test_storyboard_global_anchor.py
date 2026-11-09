"""T5（两反馈批 2026-08-18）：全片一致性锚定 global anchor（P-06 / 反馈1问题2）。

分块并发（CHUNK=8）块间互不可见，全片一致性（主角/场景/世界观/色调）此前零锚——
style=default 下连风格词都没有（P-06），IR style 槽逐块自由发明。修法：planner
分块前**一次**锚定调用产出锚定卡注入每块 system；IR style 槽在规划期汇总点统一
覆写为锚定卡风格串。评审补强三条各锁一条测试：
a. 锚定调用失败/解析失败 → 跳过注入走现状分块逻辑 + warning；
b. IR style 槽统一覆写仅发生在规划期汇总点——单帧重生端点不得二次覆写手改值；
c. 锚定卡语言随 T4 锁（旁白相对措辞，中文稿中文卡、英文稿英文卡）。

LLM 是否遵循锚定卡是真实模型行为，单测锁定的是**锚定卡本身必须进入每块 system**
与**确定性后处理必须发生**。
"""

from __future__ import annotations

import inspect
import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.gateway.video_gen.orchestration import storyboard_planner as planner_mod
from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame
from app.gateway.video_gen.style_presets import STYLE_PREFIXES

# 锚定生成调用的 system 判别标记（生成 prompt 独有措辞；注入头的措辞是「全片锚定卡」不含「视觉」）
ANCHOR_GEN_MARK = "全片视觉锚定卡"
# 锚定卡注入头（进入每块 system 的硬约束段标记）
ANCHOR_INJECT_MARK = "全片锚定卡"

ANCHOR_CARD_TEXT = "锚定卡正文：固定主角为穿深蓝西装的讲师林教授，场景为现代办公室与会议室，写实商务世界观，色调冷蓝灰。禁用：黄铜天平、玻璃齿轮等隐喻道具。"
ANCHOR_STYLE_TEXT = "冷蓝灰写实商务基调"


def _anchor_json() -> str:
    return json.dumps({"anchor_card": ANCHOR_CARD_TEXT, "style_string": ANCHOR_STYLE_TEXT}, ensure_ascii=False)


def _narration_lines(payload: dict) -> list[str]:
    """块调用 user 的 narrations 是换行拼接串；单帧兜底是 JSON 数组——两种都认。

    T6（2026-08-18）：planner 分组后块 user 按组分段，串里混入【组标题】段头——
    段头不是旁白，须剥掉再计数（否则块恒不对齐落单帧兜底，锚定注入断言全崩）。"""
    ns = payload["narrations"]
    if isinstance(ns, list):
        return ns
    return [ln for ln in ns.split("\n") if ln.strip() and not (ln.startswith("【") and ln.endswith("】"))]


def _frames_json(narrations: list[str], *, echo: str = "画面描述", with_ir: bool = False) -> str:
    """按旁白列表返回对齐 frames JSON；style 用旁白原文派生（块内局部序号会在
    失败回落断言里撞名——旁白原文是全局唯一键）。"""
    entries = []
    for i, narration in enumerate(narrations):
        e: dict = {"image_prompt": f"{echo}{i}", "visual_source": "ai_image"}
        if with_ir:
            e["ir"] = {
                "subject": f"主体{i}",
                "action": "做出动作",
                "environment": "环境",
                "style": f"漂移{narration}",
                "audio": "音效",
            }
        entries.append(e)
    return json.dumps({"frames": entries}, ensure_ascii=False)


class _AnchorAwareLLM:
    """锚定调用返回锚定 JSON，块调用按块内旁白数返回对齐 frames JSON。

    判别依据 system：锚定生成 prompt 含 ANCHOR_GEN_MARK（「视觉」二字是生成侧独有）。
    记录全部 calls 供「每块入参含同一锚」断言。
    """

    def __init__(self, *, echo: str = "画面描述", with_ir: bool = False):
        self.calls: list[list[dict]] = []
        self._echo = echo
        self._with_ir = with_ir

    async def ainvoke(self, messages):
        self.calls.append(messages)
        system = messages[0]["content"]
        if ANCHOR_GEN_MARK in system:
            content = _anchor_json()
        else:
            payload = json.loads(messages[1]["content"])
            content = _frames_json(_narration_lines(payload), echo=self._echo, with_ir=self._with_ir)

        class _Resp:
            pass

        resp = _Resp()
        resp.content = content
        return resp

    @property
    def anchor_calls(self) -> list[list[dict]]:
        return [m for m in self.calls if ANCHOR_GEN_MARK in m[0]["content"]]

    @property
    def chunk_calls(self) -> list[list[dict]]:
        return [m for m in self.calls if ANCHOR_GEN_MARK not in m[0]["content"]]


def _sb(n: int, video_type: str = "ai_image") -> Storyboard:
    return Storyboard(
        title="锚定测试",
        config=StoryboardConfig(video_type=video_type, width=1920, height=1080),
        frames=[StoryboardFrame(index=i, narration=f"第{i}帧旁白要点") for i in range(n)],
    )


# ── 1. 锚定卡注入：一次调用 + 每块 system 含同一锚 ─────────────────────


@pytest.mark.asyncio
async def test_anchor_call_once_before_chunks_and_every_chunk_carries_same_anchor():
    """事故形态复现：18 帧 → CHUNK=8 三块。锚定调用恰一次（先于分块、输入全量旁白），
    每块 system 含**同一份**锚定卡正文（P-06：default 风格下此前的唯一一致性来源为零）。"""
    sb = _sb(18)
    llm = _AnchorAwareLLM()
    await planner_mod.plan_course(llm, storyboard=sb, chunk_size=8)  # E8: 块计数语义走批量路径

    assert len(llm.anchor_calls) == 1, f"锚定调用应恰一次，实际 {len(llm.anchor_calls)}"
    # 输入=全量旁白 + style_prefix（18 条全部在场，不能只给单块 8 条）
    anchor_user = llm.anchor_calls[0][1]["content"]
    for i in range(18):
        assert f"第{i}帧旁白要点" in anchor_user, f"锚定调用输入缺第 {i} 条旁白（须全量旁白）"

    assert len(llm.chunk_calls) == 3, f"18 帧应 3 块 bulk，实际 {len(llm.chunk_calls)}"
    for ci, messages in enumerate(llm.chunk_calls):
        system = messages[0]["content"]
        assert ANCHOR_CARD_TEXT in system, f"第 {ci} 块 system 缺锚定卡正文"
        assert ANCHOR_INJECT_MARK in system, f"第 {ci} 块 system 缺锚定注入头"
    assert all(f.image_prompt for f in sb.frames), "sanity：分块对齐未落单帧兜底"


# ── 2. IR style 槽统一覆写（规划期汇总点；确定性后处理）─────────────────


@pytest.mark.asyncio
async def test_ir_style_slots_unified_to_anchor_style_string():
    """LLM 逐帧自由发明 style（P-06 实物：三块三种风格串）→ 规划期汇总点统一覆写
    为锚定卡风格串。无 IR 的帧保持 None（不凭空造值，QP1-6 口径）。"""
    sb = _sb(10, video_type="ai_video")  # ir 仅 ai_video 任务产出
    llm = _AnchorAwareLLM(with_ir=True)
    await planner_mod.plan_course(llm, storyboard=sb)

    assert any(f.visual_ir for f in sb.frames), "sanity：LLM 已返回 ir（含逐帧漂移的 style）"
    # fake 的 style 以旁白原文派生（两两不同）——下方全等断言只有经统一覆写才可能成立
    # （判别力由变异验证坐实：去掉汇总点覆写调用 → 全等断言必红）。
    for f in sb.frames:
        if f.visual_ir:
            assert f.visual_ir["style"] == ANCHOR_STYLE_TEXT, f"帧 {f.index} 的 IR style 槽须统一覆写为锚定卡风格串，实际 {f.visual_ir['style']!r}"


def test_unify_ir_style_slots_keeps_none_frames():
    """visual_ir=None 的帧不得被覆写出 dict（legacy 透传不变量）。"""
    frames = [
        StoryboardFrame(index=0, narration="n0", visual_ir={"subject": "s", "style": "漂移A"}),
        StoryboardFrame(index=1, narration="n1", visual_ir=None),
    ]
    planner_mod._unify_ir_style_slots(frames, "统一风格串")
    assert frames[0].visual_ir["style"] == "统一风格串"
    assert frames[1].visual_ir is None


# ── 3. 评审 b：单帧重生端点不得二次覆写手改 IR style ────────────────────


@pytest.mark.asyncio
async def test_regen_endpoint_does_not_touch_hand_edited_ir_style(tmp_path):
    """手改帧不变量：用户改过 visual_ir.style 后单帧重生 image_prompt，
    端点已有 style 硬前置——若再叠加 IR style 槽统一覆写会造成双重风格词，
    该路径必须只重生 image_prompt，不动 visual_ir。"""
    from app.gateway._shared.task_framework.schemas import DRAFT
    from app.gateway.routers.video_gen import regenerate_frame_prompt
    from app.gateway.video_gen.config import VideoGenConfig
    from app.gateway.video_gen.providers.registry import ProviderRegistry
    from app.gateway.video_gen.service import VideoGenService

    repo = MagicMock()
    repo.update = AsyncMock(return_value=None)
    svc = VideoGenService(
        repo=repo,
        config=VideoGenConfig(qihu_api_key="t", script_model="test-script-model"),  # E6: 不回落 models[0]
        providers=ProviderRegistry(VideoGenConfig(qihu_api_key="t")),
        storage_dir=str(tmp_path),
        sandbox_provider=AsyncMock(),
    )
    task = MagicMock()
    task.status = DRAFT
    task.task_type = "ai_image"
    task.payload = "{}"
    svc.get_task = AsyncMock(return_value=task)

    sb = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_image", width=1080, height=1920),
        frames=[
            StoryboardFrame(
                index=0,
                narration="hello",
                image_prompt="旧画面词",
                visual_ir={"subject": "主体", "action": "动作", "environment": "环境", "style": "用户手改风格", "audio": "音效"},
            )
        ],
    )
    d = tmp_path / "u1" / "tasks" / "vg_test"
    d.mkdir(parents=True)
    (d / "storyboard.json").write_text(json.dumps(sb.to_dict(), ensure_ascii=False), encoding="utf-8")

    request = MagicMock()
    request.app.state.video_gen_service = svc

    async def _fake_gen(llm, narrations, **kwargs):  # noqa: ANN001, ANN003
        return ["重生后的画面描述"]

    with (
        patch("app.gateway.routers.video_gen.get_effective_user_id", return_value="u1"),
        patch("deerflow.models.create_chat_model", return_value=MagicMock()),
        patch(
            "app.gateway.video_gen.orchestration.storyboard_planner._generate_image_prompts",
            new=AsyncMock(side_effect=_fake_gen),
        ),
    ):
        await regenerate_frame_prompt(request, "vg_test", 0)

    on_disk = json.loads((d / "storyboard.json").read_text(encoding="utf-8"))
    assert on_disk["frames"][0]["visual_ir"]["style"] == "用户手改风格", "单帧重生端点不得二次覆写用户手改的 IR style 槽（评审 b：style 硬前置 + 覆写 = 双重风格词）"
    assert on_disk["frames"][0]["image_prompt"].startswith("重生后的画面描述"), "sanity：image_prompt 确已重生"


# ── 4. 评审 a：锚定调用失败/解析失败回落现状分块逻辑 ────────────────────


class _AnchorFailingLLM:
    """首次调用（锚定）抛异常/返回垃圾；后续块调用正常。

    异常文案刻意不含「锚定卡」——防止 bulk chunk failed(异常文案) 的既有 warning
    误命中锚定失败日志断言（红/绿基线期间实际踩过：'锚定调用网络失败' 被嵌入
    chunk-failed warning 造成假绿）。
    """

    def __init__(self, *, garbage: bool = False, with_ir: bool = False):
        self.calls = 0
        self._garbage = garbage
        self._with_ir = with_ir

    async def ainvoke(self, messages):
        self.calls += 1

        class _Resp:
            pass

        resp = _Resp()
        if self.calls == 1:
            if self._garbage:
                # 垃圾输出（非 JSON）：解析失败路径
                resp.content = "这不是 JSON"
                return resp
            raise RuntimeError("网络失败")

        payload = json.loads(messages[1]["content"])
        resp.content = _frames_json(_narration_lines(payload), with_ir=self._with_ir)
        return resp


@pytest.mark.asyncio
async def test_anchor_call_exception_falls_back_with_warning(caplog):
    """锚定 LLM 调用失败 → 跳过注入走现状分块逻辑（不更坏）+ warning 日志。"""
    sb = _sb(10, video_type="ai_video")
    llm = _AnchorFailingLLM(with_ir=True)
    with caplog.at_level(logging.WARNING, logger="app.gateway.video_gen.orchestration.storyboard_planner"):
        await planner_mod.plan_course(llm, storyboard=sb)
    assert all(f.image_prompt for f in sb.frames), "回落现状：分块逻辑照常完成"
    assert any("锚定卡" in r.message for r in caplog.records), "锚定失败须记 warning（可观测）"
    # IR style 槽不被覆写（无锚定卡风格串可用，保持 LLM 原值——不更坏）
    for f in sb.frames:
        if f.visual_ir:
            assert f.visual_ir["style"] == f"漂移{f.narration}", f"帧 {f.index}：锚定失败时 IR style 槽须保持 LLM 原值（不得二次发明）"


@pytest.mark.asyncio
async def test_anchor_parse_failure_falls_back():
    """锚定输出解析失败（非 JSON）→ 同上回落。"""
    sb = _sb(3)
    llm = _AnchorFailingLLM(garbage=True)
    await planner_mod.plan_course(llm, storyboard=sb)
    assert all(f.image_prompt for f in sb.frames)


# ── 5. 评审 c：锚定卡语言随旁白（T4 同口径）───────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("narrations", [["中文稿第一句", "中文稿第二句"], ["English first line", "English second line"]])
async def test_anchor_generation_prompt_locks_language_to_narration(narrations):
    """锚定卡生成 prompt 必须带旁白相对语言锁（中文稿→中文卡，英文稿→英文卡）——
    否则锚定卡本身会成为新的漂移源（评审 c）。"""
    sb = Storyboard(
        title="语言锁",
        config=StoryboardConfig(video_type="ai_image", width=1920, height=1080),
        frames=[StoryboardFrame(index=i, narration=t) for i, t in enumerate(narrations)],
    )
    llm = _AnchorAwareLLM()
    await planner_mod.plan_course(llm, storyboard=sb)
    anchor_system = llm.anchor_calls[0][0]["content"]
    for mark in ("与旁白相同的语言", "旁白中文→中文", "旁白英文→英文"):
        assert mark in anchor_system, f"锚定生成 prompt 缺语言锁子串 {mark!r}"


# ── 6. 实拍风格：锚定卡强制真人行为场景 + 禁机械/器物隐喻；行为锚定 ────


@pytest.mark.asyncio
async def test_live_action_style_forces_real_human_anchor_and_forbids_metaphor():
    """corporate_live 等实拍风格：锚定生成 prompt 强制「真人行为场景、禁止机械/
    器物隐喻」（反馈1问题2：商务实拍稿产出黄铜天平/玻璃齿轮隐喻）；
    每块 system 的实体锚定指令补行为类覆盖（行为锚定）。"""
    sb = _sb(9)
    llm = _AnchorAwareLLM()
    await planner_mod.plan_course(llm, storyboard=sb, style_context={"style_prefix": STYLE_PREFIXES["corporate_live"]})

    anchor_system = llm.anchor_calls[0][0]["content"]
    assert "真人行为场景" in anchor_system, "实拍风格的锚定生成须强制真人行为场景"
    assert "机械" in anchor_system and "隐喻" in anchor_system, "实拍风格的锚定生成须禁机械/器物隐喻"

    for ci, messages in enumerate(llm.chunk_calls):
        assert "行为锚定" in messages[0]["content"], f"第 {ci} 块缺行为类覆盖（旁白述行为→画面必须呈现该行为）"


@pytest.mark.asyncio
async def test_non_live_action_style_does_not_force_real_human_clause():
    """precision_eng（写实 3D，非实拍）不得误判为实拍——按 prefix 关键词判别须排除。"""
    sb = _sb(3)
    llm = _AnchorAwareLLM()
    await planner_mod.plan_course(llm, storyboard=sb, style_context={"style_prefix": STYLE_PREFIXES["precision_eng"]})
    anchor_system = llm.anchor_calls[0][0]["content"]
    assert "真人行为场景" not in anchor_system, "3D 写实风格（precision_eng）不是实拍，不应强制真人条款"


# ── 6b. P1（2026-08-19）：bulk system 本体注入实拍禁隐喻条款 + 具象化指令分流 ──


@pytest.mark.asyncio
async def test_bulk_system_contains_live_action_clause_for_corporate_live():
    """corporate_live 的 bulk system 必须含禁机械隐喻条款，且具象化指令为真人行为版。

    修前：条款只进锚定调用一次，bulk 分块 system 本体没有——机械隐喻（绿拇指
    雕塑/废纸团/泡影网格）正是 bulk 具象化指令「可用视觉隐喻」的合法产物。
    """
    sb = _sb(9)
    llm = _AnchorAwareLLM()
    await planner_mod.plan_course(llm, storyboard=sb, style_context={"style_prefix": STYLE_PREFIXES["corporate_live"]})

    assert llm.chunk_calls, "应有分块 bulk 调用"
    for ci, messages in enumerate(llm.chunk_calls):
        system = messages[0]["content"]
        assert "禁止机械装置、器物隐喻" in system, f"第 {ci} 块 bulk system 缺实拍禁隐喻条款"
        assert "真人" in system, f"第 {ci} 块 bulk system 缺真人行为措辞"
        assert "免疫细胞" not in system, f"第 {ci} 块默认隐喻版具象化指令应被替换（免疫细胞标志词在场）"


@pytest.mark.asyncio
async def test_bulk_system_keeps_metaphor_for_non_live_style():
    """非实拍风格（paper_collage 纸艺拼贴）：禁隐喻条款不注入，默认具象化指令保留。"""
    sb = _sb(3)
    llm = _AnchorAwareLLM()
    await planner_mod.plan_course(llm, storyboard=sb, style_context={"style_prefix": STYLE_PREFIXES["paper_collage"]})

    assert llm.chunk_calls, "应有分块 bulk 调用"
    for ci, messages in enumerate(llm.chunk_calls):
        system = messages[0]["content"]
        assert "禁止机械装置、器物隐喻" not in system, f"第 {ci} 块非实拍风格不应有禁隐喻条款"
        assert "免疫细胞" in system, f"第 {ci} 块默认具象化指令（隐喻版）应在场"


# ── 7. 三轨对齐：bulk 硬约束措辞 + 共用前置 helper ──────────────────────


@pytest.mark.asyncio
async def test_bulk_style_instruction_upgraded_to_hard_constraint():
    """bulk 轨软引导升级硬约束（三轨对齐）。判别子串取硬约束措辞独有短语——
    「必须呈现/禁止」单字会被锚定头（必须遵守）与路由细则（禁止全片只选一种）
    误命中（M8 变异曾假绿，已收紧）。"""
    sb = _sb(3)
    llm = _AnchorAwareLLM()
    await planner_mod.plan_course(llm, storyboard=sb, style_context={"style_prefix": "扁平插画，暖色调"})
    for ci, messages in enumerate(llm.chunk_calls):
        system = messages[0]["content"]
        assert "扁平插画，暖色调" in system
        assert "风格统一为硬约束" in system, f"第 {ci} 块风格指令缺硬约束升级措辞"
        assert "冲突的画风或元素" in system, f"第 {ci} 块风格指令缺禁止冲突措辞"


# ── 8. T5b（拍板⑦）：锚定调用并入信息图版式卡——现场设计一次、全任务复用 ──


class _LayoutAnchorLLM(_AnchorAwareLLM):
    """锚定应答带/不带 graphic_layout_card 两种形态；块调用同父类。"""

    def __init__(self, layout_card: str):
        super().__init__()
        self._layout_card = layout_card

    async def ainvoke(self, messages):
        if ANCHOR_GEN_MARK in messages[0]["content"]:
            self.calls.append(messages)
            payload = json.loads(_anchor_json())
            if self._layout_card:
                payload["graphic_layout_card"] = self._layout_card
            content = json.dumps(payload, ensure_ascii=False)

            class _Resp:
                pass

            resp = _Resp()
            resp.content = content
            return resp
        return await super().ainvoke(messages)


@pytest.mark.asyncio
@pytest.mark.parametrize("layout,expected", [("米白底深蓝主色，加粗黑体，等距网格", "米白底深蓝主色，加粗黑体，等距网格"), ("", None)])
async def test_anchor_layout_card_persisted_to_storyboard(layout, expected):
    """锚定调用产出信息图版式卡 → plan_course 写 storyboard.graphic_layout_card
    （随 storyboard.json 持久化供渲染/预览消费）；无版式字段 → None（渲染回落
    确定性默认版式卡，见 test_graphic_text_inject.py）。"""
    sb = _sb(3)
    llm = _LayoutAnchorLLM(layout)
    await planner_mod.plan_course(llm, storyboard=sb)
    assert sb.graphic_layout_card == expected
    # 锚定生成 prompt 须含版式卡设计指令（配色基调+字体风格+版式骨架）
    anchor_system = llm.anchor_calls[0][0]["content"]
    for mark in ("配色基调", "字体风格", "版式骨架"):
        assert mark in anchor_system, f"锚定生成 prompt 缺版式卡设计维度 {mark!r}"


def test_with_style_prefix_shared_wording_wired_into_all_three_tracks():
    """重生端点硬前置与角色图 _with_style 共用同一拼装措辞（三轨对齐：改一处
    不得漏另两处——源码接线断言 + 行为等价双锁：单查源码会被残留 import 假绿
    （M7 变异曾逃逸），行为断言才是措辞同一性的真判据）。"""
    from app.gateway.routers import video_gen as router_mod
    from app.gateway.video_gen import character_sheet_generator as csg_mod

    # 共用 helper：非空前缀「，」拼接；空前缀原样返回（无前导逗号）
    assert planner_mod.with_style_prefix("水彩风格", "正文") == "水彩风格，正文"
    assert planner_mod.with_style_prefix("", "正文") == "正文"

    # 重生端点与角色图都必须走 helper（不得各自手写 f-string 拼接措辞）
    assert "with_style_prefix" in inspect.getsource(router_mod.regenerate_frame_prompt), "重生端点的 style 硬前置须走共用 helper（与 _with_style 同步措辞）"
    assert "with_style_prefix" in inspect.getsource(csg_mod._with_style), "角色图 _with_style 须委托共用 helper（与重生端点同步措辞）"
    # 行为等价：_with_style 产物与共用 helper 逐字一致（措辞漂移即红）
    assert csg_mod._with_style("水彩风格", "正文") == planner_mod.with_style_prefix("水彩风格", "正文")
    assert csg_mod._with_style("", "正文") == "正文"
