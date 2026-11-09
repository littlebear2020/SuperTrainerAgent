"""T6（两反馈批 2026-08-18 P-05/拍板①③）：普通任务生产分组器 + T-S1 真实指代。

修前唯一 SceneGroup 生产器 course_pipeline.script_to_storyboard 在 app/ 内零调用
（仅 tests），普通任务 scene_groups 恒空数组 → T-S1「同一分组」对 LLM 无指代、
T-S2（env 默认关 + 生产无 groups + 能力闸门）是三重死角。本文件锁：

- 分组器：锚定调用结构化输出顺带产出场景分组（零额外 LLM 调用）；锚定失败/
  字段无效 → 确定性 CHUNK 对齐兜底（≤8 帧单块天然一组）；分组边界确定性；
  已有 groups（course_pipeline 段落分组）不覆盖（调用方意图）。
- 单帧组归并（T-S2 对单帧组无意义——组首即全组，锚定永不发生）。
- T-S1：分组后块调用 user 消息出现【组标题】段头（真实指代）。
- 两步纪律第一步：>50 帧任务即使有了 planner groups 也不分派 _plan_course_batched。
- worker v3_segmented 不随 planner groups 转活（origin 栅栏——两步纪律在 worker
  侧的对应物；v3 长期生产不可达，转活须独立验收，且 T-S2 上线后 A/B 不能被
  worker 管线变化混杂）。
- schema 链：to_dict/from_dict 往返含 origin。

T-S2 默认翻转与 H3 真实包裹链用例见 test_phase3_anchoring.py；A-1 拆帧重映射
回归见 test_frame_split_persistence.py；reorder 重映射回归见 test_frame_reorder.py。
"""

from __future__ import annotations

import json

import pytest

from app.gateway.video_gen.orchestration import storyboard_planner as planner
from app.gateway.video_gen.storyboard import SceneGroup, Storyboard, StoryboardConfig, StoryboardFrame

ANCHOR_MARK = "全片视觉锚定卡"  # 锚定生成 system 独有措辞（同 test_storyboard_global_anchor）


def _anchor_payload(groups: list | None = None) -> str:
    """锚定应答 JSON；groups=None 时缺 scene_groups 字段（旧式输出兼容面）。"""
    payload = {"anchor_card": "锚定卡：固定主角与冷蓝灰色调。", "style_string": "冷蓝灰写实基调"}
    if groups is not None:
        payload["scene_groups"] = groups
    return json.dumps(payload, ensure_ascii=False)


class _GroupingLLM:
    """锚定调用按需返回锚定 JSON；块调用解析 user 旁白（剥【组标题】段头）返回对齐 frames。

    段头过滤与生产格式化（_generate_course_prompts_enhanced 的【title】\n 旁白）
    互为镜像——不过滤会把段头当旁白条数，块恒不对齐落单帧兜底（红/绿期踩过）。
    """

    def __init__(self, anchor_content: str | None, *, echo: str = "画面") -> None:
        self._anchor = anchor_content
        self._echo = echo
        self.calls: list[list[dict]] = []

    @staticmethod
    def _narration_lines(payload: dict) -> list[str]:
        ns = payload["narrations"]
        if isinstance(ns, list):
            return ns
        return [ln for ln in ns.split("\n") if ln.strip() and not (ln.startswith("【") and ln.endswith("】"))]

    async def ainvoke(self, messages):
        self.calls.append(messages)
        if ANCHOR_MARK in messages[0]["content"] and self._anchor is not None:
            content = self._anchor
        else:
            payload = json.loads(messages[1]["content"])
            lines = self._narration_lines(payload)
            frames = [{"image_prompt": f"{self._echo}{i}", "visual_source": "ai_image"} for i in range(len(lines))]
            content = json.dumps({"frames": frames}, ensure_ascii=False)

        class _Resp:
            pass

        resp = _Resp()
        resp.content = content
        return resp


class _GarbageAnchorLLM(_GroupingLLM):
    """锚定调用返回非 JSON（解析失败 → 分组走确定性兜底）。"""

    def __init__(self) -> None:
        super().__init__("这不是 JSON")


def _sb(n: int, video_type: str = "ai_video") -> Storyboard:
    return Storyboard(
        title="分组测试",
        config=StoryboardConfig(video_type=video_type, width=1920, height=1080),
        frames=[StoryboardFrame(index=i, narration=f"第{i}帧旁白") for i in range(n)],
    )


def _indices(sb: Storyboard) -> list[list[int]]:
    return [list(g.frame_indices) for g in sb.scene_groups]


# ── 1. 确定性兜底：CHUNK 对齐 / ≤8 帧天然一组 / 逐次运行一致 ────────────


@pytest.mark.asyncio
async def test_deterministic_fallback_chunk_aligned():
    """锚定失败 → 确定性 CHUNK 对齐分组：18 帧 → [0..7]/[8..15]/[16,17]，
    与 _fill_image_prompts_robust 块边界同源（T-S1 组边界=块边界）。"""
    sb = _sb(18)
    await planner.plan_course(_GarbageAnchorLLM(), storyboard=sb)
    assert _indices(sb) == [[0, 1, 2, 3, 4, 5, 6, 7], [8, 9, 10, 11, 12, 13, 14, 15], [16, 17]]
    assert [g.title for g in sb.scene_groups] == ["段落 1", "段落 2", "段落 3"]
    assert all(g.origin == "planner" for g in sb.scene_groups)


@pytest.mark.asyncio
async def test_single_chunk_naturally_one_group():
    """≤8 帧单块任务天然一组（不依赖 LLM 字段）；锚定应答缺 scene_groups 字段同兜底。"""
    sb = _sb(5)
    await planner.plan_course(_GroupingLLM(_anchor_payload(groups=None)), storyboard=sb)
    assert _indices(sb) == [[0, 1, 2, 3, 4]]
    assert len(sb.scene_groups) == 1 and sb.scene_groups[0].origin == "planner"


@pytest.mark.asyncio
async def test_grouping_deterministic_across_runs():
    """分组边界确定性：同输入两次规划产出逐字段一致（含标题/origin）。"""

    async def _run() -> list[dict]:
        sb = _sb(12)
        await planner.plan_course(
            _GroupingLLM(_anchor_payload(groups=[{"title": "开场", "start": 0}, {"title": "深入", "start": 4}])),
            storyboard=sb,
        )
        return [{"title": g.title, "frame_indices": list(g.frame_indices), "origin": g.origin} for g in sb.scene_groups]

    assert await _run() == await _run()


# ── 2. 锚定卡顺带产出（零额外 LLM 调用）与无效回落 ──────────────────────


@pytest.mark.asyncio
async def test_groups_derived_from_anchor_output_without_extra_call():
    """锚定调用结构化输出带 scene_groups → 起点重建连续覆盖；锚定调用仍恰一次
    （分组并入锚定调用 = 零额外 LLM 调用，计划 T6 第 1 条）。"""
    llm = _GroupingLLM(_anchor_payload(groups=[{"title": "开场", "start": 0}, {"title": "原理讲解", "start": 3}]))
    sb = _sb(10)
    await planner.plan_course(llm, storyboard=sb)
    assert _indices(sb) == [[0, 1, 2], [3, 4, 5, 6, 7, 8, 9]]
    assert [g.title for g in sb.scene_groups] == ["开场", "原理讲解"]
    anchor_calls = [m for m in llm.calls if ANCHOR_MARK in m[0]["content"]]
    assert len(anchor_calls) == 1, "分组并入锚定调用——不得引入第二次 LLM 调用"
    # 契约面：锚定生成 prompt 须含分组指令（措辞退化即红——LLM 不产出字段则恒走兜底）
    anchor_system = anchor_calls[0][0]["content"]
    for mark in ("scene_groups", "首组 start 必须为 0", "连续场景组"):
        assert mark in anchor_system, f"锚定生成 prompt 缺分组指令子串 {mark!r}"


@pytest.mark.asyncio
async def test_anchor_groups_invalid_falls_back_to_deterministic():
    """scene_groups 字段无效（非 dict 条目/起点越界/空列表）→ 确定性兜底，不连坐锚定卡。"""
    for bad in ([{"title": "x", "start": 99}], ["不是对象"], []):
        sb = _sb(9)
        await planner.plan_course(_GroupingLLM(_anchor_payload(groups=bad)), storyboard=sb)
        assert _indices(sb) == [[0, 1, 2, 3, 4, 5, 6, 7], [8]], f"无效分组 {bad!r} 须走确定性兜底"
        assert sb.global_anchor and sb.global_anchor["anchor_card"], "无效分组不连坐锚定卡"


@pytest.mark.asyncio
async def test_missing_start_zero_first_group_covers_head():
    """LLM 漏给 start=0（首组起点非 0）→ 0 到首个起点之间的帧并入首组（起点重建语义）。"""
    llm = _GroupingLLM(_anchor_payload(groups=[{"title": "中段", "start": 2}]))
    sb = _sb(6)
    await planner.plan_course(llm, storyboard=sb)
    assert _indices(sb) == [[0, 1, 2, 3, 4, 5]]
    assert sb.scene_groups[0].title == "中段"


@pytest.mark.asyncio
async def test_singleton_groups_merged():
    """单帧组归并：T-S2 组锚定对单帧组无意义（组首即全组，锚定永不发生）。
    首组单帧并入后一组，中段单帧并入前一组。"""
    llm = _GroupingLLM(_anchor_payload(groups=[{"title": "孤立开场", "start": 0}, {"title": "主体段", "start": 1}, {"title": "收尾", "start": 5}]))
    sb = _sb(8)
    await planner.plan_course(llm, storyboard=sb)
    # start=0 的单帧组并入 start=1 组（保留后组标题）；无中段单帧
    assert _indices(sb) == [[0, 1, 2, 3, 4], [5, 6, 7]]
    assert [g.title for g in sb.scene_groups] == ["主体段", "收尾"]


@pytest.mark.asyncio
async def test_preexisting_groups_not_overwritten():
    """规划前已有 groups（course_pipeline 段落分组）= 调用方意图，分组器不覆盖。"""
    sb = _sb(4)
    sb.scene_groups = [SceneGroup(index=0, title="课程段落", frame_indices=[0, 1, 2, 3])]
    await planner.plan_course(_GroupingLLM(_anchor_payload(groups=[{"title": "LLM分组", "start": 0}, {"title": "不应生效", "start": 2}])), storyboard=sb)
    assert len(sb.scene_groups) == 1
    assert sb.scene_groups[0].title == "课程段落"
    assert sb.scene_groups[0].origin is None, "course 系 groups 无 origin——worker v3 栅栏只针对 planner groups"


# ── 3. T-S1：块调用 user 消息获得【组标题】真实指代 ─────────────────────


@pytest.mark.asyncio
async def test_ts1_group_headers_reach_chunk_user_message():
    """分组后每块 user 消息按组分段（【组标题】段头）——T-S1「同一分组」获得
    真实指代；且段头不破坏块对齐（桩剥段头后逐块对齐，未落单帧兜底）。"""
    llm = _GroupingLLM(_anchor_payload(groups=[{"title": "问题引入", "start": 0}, {"title": "方案拆解", "start": 4}]))
    sb = _sb(10)
    await planner.plan_course(llm, storyboard=sb, chunk_size=8)  # E8: 块边界语义属批量路径
    chunk_calls = [m for m in llm.calls if ANCHOR_MARK not in m[0]["content"]]
    assert len(chunk_calls) == 2, f"10 帧应 2 块（8+2），实际 {len(chunk_calls)}"
    assert "【问题引入】" in chunk_calls[0][1]["content"]
    assert "【方案拆解】" in chunk_calls[0][1]["content"], "组边界在块内（帧 4-7 属第二组）——段头须进同块"
    assert "【方案拆解】" in chunk_calls[1][1]["content"]
    assert all(f.image_prompt for f in sb.frames), "sanity：分块对齐未落单帧兜底"


# ── 4. 两步纪律第一步：_plan_course_batched 不转活 ─────────────────────


@pytest.mark.asyncio
async def test_batched_path_not_activated_by_planner_groups(monkeypatch):
    """>50 帧任务有了 planner groups 后仍走 _fill_image_prompts_robust（分派按规划前
    has_groups 快照）——_plan_course_batched 转活是第二步独立验收，本批不激活。"""
    called: list[str] = []

    async def _spy_batched(llm, storyboard, style_prefix):  # noqa: ANN001
        called.append("batched")
        for f in storyboard.frames:
            f.image_prompt = "批量路径产物"

    monkeypatch.setattr(planner, "_plan_course_batched", _spy_batched)
    llm = _GroupingLLM(_anchor_payload(groups=[{"title": "长片组", "start": 0}]))
    sb = _sb(60)
    await planner.plan_course(llm, storyboard=sb)
    assert called == [], "第二步未转活：planner groups 不得触发 _plan_course_batched 分派"
    assert all(f.image_prompt for f in sb.frames), "robust 路径照常完成"


# ── 5. worker v3_segmented 栅栏：planner groups 不转活 worker 分段管线 ──


def test_worker_v3_not_activated_for_planner_groups():
    """planner groups（origin="planner"）不触发 v3_segmented——worker 侧的两步纪律：
    v3 长期生产不可达（compose 探测修前恒 v1），转活自带隐患须独立验收；T-S2 上线
    后 A/B 观察也不能被 worker 管线变化混杂。course 系 groups（origin=None）行为不变。"""
    from app.gateway.video_gen.worker_probe import decide_pipeline_version

    frames = [StoryboardFrame(index=i, narration=f"n{i}") for i in range(6)]
    cfg = StoryboardConfig(video_type="ai_video", width=1920, height=1080)
    caps = ["v1", "v2_mixed", "v3_segmented"]

    planner_sb = Storyboard(
        title="t",
        config=cfg,
        frames=frames,
        scene_groups=[
            SceneGroup(index=0, title="a", frame_indices=[0, 1, 2], origin="planner"),
            SceneGroup(index=1, title="b", frame_indices=[3, 4, 5], origin="planner"),
        ],
    )
    assert decide_pipeline_version(planner_sb, caps) != "v3_segmented", "planner groups 不得转活 v3（第一步栅栏）"

    course_sb = Storyboard(
        title="t",
        config=cfg,
        frames=[StoryboardFrame(index=i, narration=f"n{i}") for i in range(6)],
        scene_groups=[
            SceneGroup(index=0, title="a", frame_indices=[0, 1, 2]),
            SceneGroup(index=1, title="b", frame_indices=[3, 4, 5]),
        ],
    )
    assert decide_pipeline_version(course_sb, caps) == "v3_segmented", "course 系 groups 行为不变（现状回归）"


# ── 6. schema 消费链：序列化/反序列化往返（storyboard.py）────────────────


def test_scene_groups_origin_roundtrip():
    """to_dict/from_dict 往返保 origin——栅栏判据随 storyboard.json 持久化不丢。"""
    sb = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_video", width=1920, height=1080),
        frames=[StoryboardFrame(index=i, narration=f"n{i}") for i in range(4)],
        scene_groups=[
            SceneGroup(index=0, title="a", frame_indices=[0, 1], origin="planner"),
            SceneGroup(index=1, title="b", frame_indices=[2, 3]),
        ],
    )
    restored = Storyboard.from_dict(json.loads(json.dumps(sb.to_dict(), ensure_ascii=False)))
    assert [g.origin for g in restored.scene_groups] == ["planner", None]
    assert [list(g.frame_indices) for g in restored.scene_groups] == [[0, 1], [2, 3]]
    # 旧 storyboard.json 无 origin 键 → 默认 None（from_dict 未知键忽略语义不变）
    legacy = json.loads(json.dumps(sb.to_dict(), ensure_ascii=False))
    for g in legacy["scene_groups"]:
        g.pop("origin", None)
    restored_legacy = Storyboard.from_dict(legacy)
    assert all(g.origin is None for g in restored_legacy.scene_groups)
