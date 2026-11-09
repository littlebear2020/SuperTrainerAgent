"""镜头调度表（两阶段提示词生成）——S1/S2/S3/S5（2026-08-26 计划 v2.2）。

背景：E8 逐帧化下每个请求看不到其他帧的构图产出，连续镜头人物位置/机位逐帧独立
抽签（鬼畜）。阶段 1 一次轻量 LLM 调用产出全片调度表（帧序号绑定），阶段 2 逐帧
扩写注入「本帧行 + ±1 邻行 + 硬指令」，shot_size/camera_move 从表确定读取。

- S1: _plan_shot_table 契约/条数对齐/解析失败重试/二次失败 None（AC6 降级）+
      storyboard.json 持久化 + 插删帧行号重排（frame_ops，照抄 remap_scene_groups 先例）。
- S2: 逐帧扩写 user 负载首部注入调度行（script_context 之前）+ 有表时
      shot_size/camera_move 取自表（不再采信逐帧返回值）+ 无表行为与现状一致（AC3）。
- S3: 编辑/重生四场景——单帧重生复用原行不改邻行 / 插帧行级生成仅 1 次 LLM /
      编辑旁白默认保留行 / 全部重生表全量更新。
- S5: shot_table_enabled/shot_table_model 配置默认值 + 关闭=纯现状（AC3）。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.gateway.video_gen.orchestration import storyboard_planner as sp
from app.gateway.video_gen.storyboard import SceneGroup, Storyboard, StoryboardConfig, StoryboardFrame

# ── 公共 fixture ──────────────────────────────────────────────────────────


def _sb(n: int = 3, video_type: str = "ai_video") -> Storyboard:
    cfg = StoryboardConfig(video_type=video_type, width=720, height=1280)
    frames = [StoryboardFrame(index=i, narration=f"旁白{i}") for i in range(n)]
    return Storyboard(
        title="调度表测试",
        config=cfg,
        frames=frames,
        scene_groups=[SceneGroup(index=0, title="段一", frame_indices=list(range(n)))],
    )


class _LLM:
    """记录型 fake：按序吐 content，捕获 (system, user) 消息对。"""

    def __init__(self, contents: list[str]) -> None:
        self._contents = list(contents)
        self.calls: list[tuple[str, str]] = []

    async def ainvoke(self, messages):  # noqa: ANN001
        self.calls.append((messages[0]["content"], messages[1]["content"]))
        content = self._contents.pop(0) if self._contents else "{}"
        return SimpleNamespace(content=content)


_SIZES = ["wide", "medium", "close"]
_MOVES = ["static", "push_in", "pan"]


def _table_json(n: int = 3, graphic: tuple[int, ...] = (), main_engine: str = "ai_video") -> str:
    """调度表 LLM 输出桩。``graphic`` = visual_source=graphic 的帧号集合
    （2026-08-31 路由并入：行带 visual_source 列；空集 = 全主引擎）。"""
    rows = [
        {
            "frame_index": i,
            "shot_size": _SIZES[i % 3],
            "camera_move": _MOVES[i % 3],
            "subject_position": f"画面左侧{i}",
            "composition": f"三分构图{i}",
            "continuity": f"承接{i}",
            "visual_source": "graphic" if i in graphic else main_engine,
        }
        for i in range(n)
    ]
    return json.dumps({"shot_table": rows}, ensure_ascii=False)


def _table_json_legacy(n: int = 3) -> str:
    """不带 visual_source 列的表（存量形状 / 漏列场景）。"""
    rows = [
        {
            "frame_index": i,
            "shot_size": _SIZES[i % 3],
            "camera_move": _MOVES[i % 3],
            "subject_position": f"画面左侧{i}",
            "composition": f"三分构图{i}",
            "continuity": f"承接{i}",
        }
        for i in range(n)
    ]
    return json.dumps({"shot_table": rows}, ensure_ascii=False)


def _frame_json(prompt: str = "画面", shot: str = "close", move: str = "tilt", vs: str = "ai_video") -> str:
    """逐帧扩写的单帧返回（shot/move/vs 故意与表冲突——S2/路由并入断言表胜出）。"""
    return json.dumps(
        {"frames": [{"image_prompt": prompt, "shot_size": shot, "camera_move": move, "visual_source": vs}]},
        ensure_ascii=False,
    )


_ANCHOR_JSON = json.dumps(
    {"anchor_card": "锚定卡", "style_string": "写实风", "graphic_layout_card": "", "scene_groups": []},
    ensure_ascii=False,
)


def _user_json(call: tuple[str, str]) -> dict:
    return json.loads(call[1])


# ── S1：调度表生成与持久化 ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_s1_01_contract_fields_and_alignment() -> None:
    """契约字段齐全 + N 帧 N 行对齐（frame_index 0..N-1 连续）；请求携带分组与全量旁白。"""
    llm = _LLM([_table_json(3)])
    rows = await sp._plan_shot_table(llm, _sb(3), "")
    assert rows is not None and len(rows) == 3
    for i, r in enumerate(rows):
        assert r.frame_index == i
        assert r.shot_size in _SIZES and r.camera_move in _MOVES
        assert r.subject_position and r.composition and r.continuity
    # 请求上下文：全量旁白 + 场景分组 + 总帧数（联合设计所需视野）
    user = _user_json(llm.calls[0])
    assert user.get("total_frames") == 3
    assert any("旁白0" in json.dumps(v, ensure_ascii=False) for v in (user.get("scene_groups") or []))
    assert "调度" in llm.calls[0][0]


@pytest.mark.asyncio
async def test_s1_02_parse_failure_retries_once() -> None:
    """JSON 解析失败 → 重试 1 次成功（共 2 次调用）。"""
    llm = _LLM(["这不是JSON", _table_json(3)])
    rows = await sp._plan_shot_table(llm, _sb(3), "")
    assert rows is not None and len(rows) == 3
    assert len(llm.calls) == 2


@pytest.mark.asyncio
async def test_s1_03_second_failure_returns_none() -> None:
    """二次失败 → None（降级现状逐帧，AC6）。"""
    llm = _LLM(["bad", "still bad"])
    assert await sp._plan_shot_table(llm, _sb(3), "") is None
    assert len(llm.calls) == 2


@pytest.mark.asyncio
async def test_s1_04_misaligned_count_is_invalid() -> None:
    """条数不对齐（2 行给 3 帧）= 不可信 → 重试；仍不齐 → None。"""
    llm = _LLM([_table_json(2), _table_json(2)])
    assert await sp._plan_shot_table(llm, _sb(3), "") is None
    assert len(llm.calls) == 2


@pytest.mark.asyncio
async def test_s1_04b_invalid_enum_is_invalid() -> None:
    """非法枚举**系统性漂移**（2/3 行 = 66% > 20% 阈值）→ 整表不可信 → None。

    2026-09-01 语义变化（计划内）：个别行漂移（≤20%）已改为安全映射保留整表
    （见 TestRowLevelEnumTolerance）——本用例的 3 帧坏 2 行属「全表漂移还硬用」
    防线，维持整表拒绝。"""
    bad = json.dumps(
        {
            "shot_table": [
                {"frame_index": 0, "shot_size": "特写", "camera_move": "static"},
                {"frame_index": 1, "shot_size": "medium", "camera_move": "zoom"},
                {"frame_index": 2, "shot_size": "close", "camera_move": "pan"},
            ]
        },
        ensure_ascii=False,
    )
    llm = _LLM([bad, bad])
    assert await sp._plan_shot_table(llm, _sb(3), "") is None


def test_s1_05_storyboard_roundtrip_and_legacy() -> None:
    """shot_table 随 storyboard.json 持久化（to_dict/from_dict）；存量无键 → None；
    无表时 to_dict **不含** shot_table 键（AC3：关闭态产物形状与现状逐字节一致）。"""
    from app.gateway.video_gen.storyboard import ShotDirection

    sb = _sb(2)
    sb.shot_table = [ShotDirection(frame_index=0, shot_size="wide", camera_move="static", subject_position="左")]
    d = sb.to_dict()
    assert d["shot_table"][0]["frame_index"] == 0
    assert d["shot_table"][0]["subject_position"] == "左"
    sb2 = Storyboard.from_dict(d)
    assert isinstance(sb2.shot_table[0], ShotDirection)
    assert sb2.shot_table[0].camera_move == "static"
    # 存量 storyboard（无键）→ None；无表 to_dict 不含键（关闭=现状形状）
    legacy = Storyboard.from_dict({"title": "t", "config": {"video_type": "ai_image", "width": 1, "height": 1}, "frames": [{"index": 0, "narration": "n"}]})
    assert legacy.shot_table is None
    assert "shot_table" not in legacy.to_dict()


def test_s1_06_remap_after_insert_delete() -> None:
    """插删帧行号重排（照抄 remap_scene_groups_after_insert/delete 先例）：
    插入 → ≥insert 行 +1、新帧无行（行级生成另走小调用）；删除 → 移除该行、>deleted 行 -1。"""
    from app.gateway.video_gen.frame_ops import remap_shot_table_after_delete, remap_shot_table_after_insert

    rows = [{"frame_index": 0, "shot_size": "wide"}, {"frame_index": 1, "shot_size": "medium"}, {"frame_index": 2, "shot_size": "close"}]
    out = remap_shot_table_after_insert([dict(r) for r in rows], 1)
    assert [r["frame_index"] for r in out] == [0, 2, 3], "插入后旧行绑定保持（+1 平移），新帧无行"
    assert remap_shot_table_after_insert(None, 1) is None, "无表原样（零行为变化）"

    out2 = remap_shot_table_after_delete([dict(r) for r in rows], 1)
    assert [r["frame_index"] for r in out2] == [0, 1], "被删行移除，原帧2 行平移到 1"
    assert out2[1]["shot_size"] == "close", "平移行内容不变（原帧2 的行）"
    assert remap_shot_table_after_delete([], 0) == []


@pytest.mark.asyncio
async def test_s1_07_plan_course_enabled_generates_table() -> None:
    """plan_course(shot_table_enabled=True)：锚定 → 调度表 → 逐帧（LLM 调用序）。"""
    llm = _LLM([_ANCHOR_JSON, _table_json(3), _frame_json(), _frame_json(), _frame_json()])
    sb = _sb(3)
    await sp.plan_course(llm, storyboard=sb, shot_table_enabled=True)
    assert sb.shot_table is not None and len(sb.shot_table) == 3
    assert "shot_table" in sb.to_dict()
    # 调用序：锚定(1) + 调度表(1) + 逐帧(3)
    assert len(llm.calls) == 5
    assert "调度" in llm.calls[1][0], "第 2 次调用是调度表阶段"


@pytest.mark.asyncio
async def test_s1_08_plan_course_disabled_zero_extra_calls() -> None:
    """默认（不传/False）：零新增 LLM 调用、无表——既有窄调用方零变化（AC3 基线）。"""
    llm = _LLM([_ANCHOR_JSON, _frame_json(), _frame_json(), _frame_json()])
    sb = _sb(3)
    await sp.plan_course(llm, storyboard=sb)
    assert sb.shot_table is None
    assert "shot_table" not in sb.to_dict()
    assert len(llm.calls) == 4, "锚定(1)+逐帧(3)，无调度表调用"


@pytest.mark.asyncio
async def test_s1_09_failure_records_degradation_sidecar(tmp_path: Path) -> None:
    """AC6：调度表二次失败 → 任务不中断（逐帧照跑）+ degradations sidecar 留痕。"""
    llm = _LLM([_ANCHOR_JSON, "bad", "bad", _frame_json(), _frame_json(), _frame_json()])
    sb = _sb(3)
    await sp.plan_course(llm, storyboard=sb, task_dir=tmp_path, shot_table_enabled=True)
    assert sb.shot_table is None, "降级：无表"
    assert all(f.image_prompt for f in sb.frames), "逐帧扩写照常完成（任务不中断）"
    from app.gateway.video_gen.degradations import read_degradations

    recs = read_degradations(tmp_path)
    assert any(r["kind"] == "shot_table_fallback" for r in recs), f"须留 shot_table_fallback 留痕，实际 {recs}"


@pytest.mark.asyncio
async def test_s1_10_vimax_not_gated_in() -> None:
    """范围排除：vimax 任务不生成调度表（本计划不动 vimax）。"""
    llm = _LLM([_ANCHOR_JSON, _frame_json(), _frame_json(), _frame_json()])
    sb = _sb(3, video_type="vimax")
    await sp.plan_course(llm, storyboard=sb, shot_table_enabled=True)
    assert sb.shot_table is None
    assert len(llm.calls) == 4


@pytest.mark.asyncio
async def test_s1_11_shot_table_llm_override() -> None:
    """S5：shot_table_llm 显式传入 → 阶段 1 用它（默认跟随 script llm = 第 7/8 测试隐证）。"""
    main_llm = _LLM([_ANCHOR_JSON, _frame_json(), _frame_json(), _frame_json()])
    table_llm = _LLM([_table_json(3)])
    sb = _sb(3)
    await sp.plan_course(main_llm, storyboard=sb, shot_table_enabled=True, shot_table_llm=table_llm)
    assert sb.shot_table is not None
    assert len(table_llm.calls) == 1, "调度表阶段恰好 1 次独立 LLM 调用"
    assert len(main_llm.calls) == 4, "主 llm 不被调度表占用"


@pytest.mark.asyncio
async def test_s1_13_remotion_course_gated_in() -> None:
    """T-A5 历史行为（remotion_course 生成调度表）已被独立管线计划（2026-09-10 P3）
    取代：remotion_course 解析由 course_script.parse_course_script 一次到位，plan_course
    早退（锚定卡/调度表/逐帧扩写/护栏全套不执行），调度表 layout hint addon 成为
    死路径（计划 §4.5：保留不删，清理留待后续）。本测试改为钉住早退契约。"""
    llm = _LLM([_ANCHOR_JSON, _table_json(3), _frame_json(), _frame_json(), _frame_json()])
    sb = _sb(3, video_type="remotion_course")
    await sp.plan_course(llm, storyboard=sb, shot_table_enabled=True)
    assert sb.shot_table is None  # 早退：不生成调度表（parser 直出 layout）
    assert sb.global_anchor is None  # 不做锚定调用


def _table_json_with_layout(layouts: list[str | None]) -> str:
    base = json.loads(_table_json(len(layouts)))
    for row, lo in zip(base["shot_table"], layouts):
        row["layout"] = lo
    return json.dumps(base, ensure_ascii=False)


@pytest.mark.asyncio
async def test_s1_14_layout_hint_only_fills_empty_slots() -> None:
    """D14 守卫（独立管线计划 2026-09-10 P3 后语义）：remotion_course 走 plan_course
    早退——用户手改 layout 与空位都**不被触碰**（parser 直出 layout，planner hint
    通道整体退役）。"""
    table = _table_json_with_layout(["CoverPage", "ChartPage", "NoSuchLayout"])
    llm = _LLM([_ANCHOR_JSON, table, _frame_json(), _frame_json(), _frame_json()])
    sb = _sb(3, video_type="remotion_course")
    sb.frames[1].layout = "CodePage"  # 用户手改（D14 最高优先级）
    await sp.plan_course(llm, storyboard=sb, shot_table_enabled=True)
    assert sb.frames[0].layout is None, "空位帧不被 planner 填（早退）"
    assert sb.frames[1].layout == "CodePage", "用户手改帧不被覆写"
    assert sb.frames[2].layout is None, "空位帧保持空位"


# ── S2：逐帧扩写注入调度行 ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_s2_01_injection_at_head_with_neighbors() -> None:
    """注入秩序（计划写死）：调度行+邻行+硬指令位于 user 负载首部、script_context 之前；
    内容含本帧行与 ±1 邻行（±2 不含）。"""
    sb = _sb(3)
    sb.shot_table = sp._parse_shot_table_rows(_table_json(3), 3, main_engine="ai_video")
    llm = _LLM([_frame_json(), _frame_json(), _frame_json()])
    await sp._fill_image_prompts_per_frame(llm, sb, "", concurrency=8)
    for i, call in enumerate(llm.calls):
        user = _user_json(call)
        keys = list(user.keys())
        assert keys[0] == "shot_direction", f"frame{i} 调度行须在 user 负载首部，实际键序 {keys}"
        assert "script_narrations" in user and keys.index("shot_direction") < keys.index("script_narrations"), "调度行在 script_context 之前"
        sd = user["shot_direction"]
        assert sd["current"]["frame_index"] == i
        assert sd["current"]["subject_position"] == f"画面左侧{i}"
        assert ("prev" in sd) == (i > 0), f"frame{i} prev 邻行"
        assert ("next" in sd) == (i < 2), f"frame{i} next 邻行"
        if "prev" in sd:
            assert sd["prev"]["frame_index"] == i - 1
        if "next" in sd:
            assert sd["next"]["frame_index"] == i + 1
        assert "严格遵守" in user["shot_directive"], "硬指令必须随行注入"


@pytest.mark.asyncio
async def test_s2_02_shot_fields_from_table_not_llm() -> None:
    """有表时 shot_size/camera_move 取自调度表（逐帧返回的 close/tilt 不采信——病灶修复点）。"""
    sb = _sb(3)
    sb.shot_table = sp._parse_shot_table_rows(_table_json(3), 3, main_engine="ai_video")
    llm = _LLM([_frame_json(), _frame_json(), _frame_json()])
    await sp._fill_image_prompts_per_frame(llm, sb, "", concurrency=8)
    for i, f in enumerate(sb.frames):
        assert f.shot_size == _SIZES[i % 3], f"frame{i} shot_size 须取自表"
        assert f.camera_move == _MOVES[i % 3], f"frame{i} camera_move 须取自表"


@pytest.mark.asyncio
async def test_s2_03_no_table_behavior_unchanged() -> None:
    """无表（AC3 降级态）：user 负载零 shot_direction 键；镜头语言沿用逐帧返回值（现状）。"""
    sb = _sb(2)
    llm = _LLM([_frame_json(shot="medium", move="pan"), _frame_json(shot="medium", move="pan")])
    await sp._fill_image_prompts_per_frame(llm, sb, "", concurrency=8)
    for call in llm.calls:
        user = _user_json(call)
        assert "shot_direction" not in user and "shot_directive" not in user
    # 现状链路照旧：mini 返回值写回后 smooth 收敛（首镜 wide 化）
    assert sb.frames[0].camera_move == "pan", "现状：mini 返回值写回"
    assert sb.frames[0].shot_size == "wide"


@pytest.mark.asyncio
async def test_s2_04_smooth_skipped_when_table_present() -> None:
    """有表时跳过 smooth_camera_continuity（表即连贯性设计，后处理不得覆写表的调度）。"""
    sb = _sb(3)
    # 表故意违反 smooth 规则：首镜 close + 连续 push_in——若 smooth 跑了会被改掉
    raw = json.dumps(
        {"shot_table": [{"frame_index": i, "shot_size": "close", "camera_move": "push_in", "subject_position": f"左{i}", "composition": "c", "continuity": "t"} for i in range(3)]},
        ensure_ascii=False,
    )
    sb.shot_table = sp._parse_shot_table_rows(raw, 3, main_engine="ai_video")
    llm = _LLM([_frame_json(), _frame_json(), _frame_json()])
    await sp._fill_image_prompts_per_frame(llm, sb, "", concurrency=8)
    assert all(f.shot_size == "close" for f in sb.frames), "首镜 wide 化不得覆写表"
    assert all(f.camera_move == "push_in" for f in sb.frames), "连续重复运镜由表决定，smooth 不得打断"


@pytest.mark.asyncio
async def test_s2_05_frame_without_row_falls_back() -> None:
    """插帧后新帧暂无行：该帧零注入+镜头语言走现状；有行邻帧照常注入（零噪音降级）。"""
    sb = _sb(3)
    rows = sp._parse_shot_table_rows(_table_json(3), 3, main_engine="ai_video")
    sb.shot_table = [r for r in rows if r.frame_index != 1]  # frame1 无行（模拟插帧待生成）
    llm = _LLM([_frame_json(), _frame_json(shot="medium", move="pan"), _frame_json()])
    await sp._fill_image_prompts_per_frame(llm, sb, "", concurrency=8)
    user1 = _user_json(llm.calls[1])
    assert "shot_direction" not in user1, "无行帧零注入"
    assert sb.frames[1].shot_size == "medium", "无行帧沿用逐帧返回值（现状行为）"
    assert "shot_direction" in _user_json(llm.calls[0]) and "shot_direction" in _user_json(llm.calls[2])


# ── S3：编辑/重生四场景 ──────────────────────────────────────────────────


def test_s3_01_shot_direction_block_shape() -> None:
    """共用块构造：本帧+±1 邻行；首/尾帧缺省侧不含；无表/无行 → None。"""
    rows = sp._parse_shot_table_rows(_table_json(3), 3, main_engine="ai_video")
    blk0 = sp.shot_direction_block(rows, 0)
    assert blk0 is not None and "prev" not in blk0 and blk0["next"]["frame_index"] == 1
    blk1 = sp.shot_direction_block(rows, 1)
    assert blk1["prev"]["frame_index"] == 0 and blk1["next"]["frame_index"] == 2
    assert sp.shot_direction_block(rows, 9) is None
    assert sp.shot_direction_block(None, 0) is None


@pytest.mark.asyncio
async def test_s3_02_regen_prompt_context_carries_rows() -> None:
    """单帧重生：context.shot_direction 进 _generate_video_prompts user 负载首部。"""
    rows = sp._parse_shot_table_rows(_table_json(3), 3, main_engine="ai_video")
    blk = sp.shot_direction_block(rows, 1)
    llm = _LLM([json.dumps({"video_prompts": ["新画面"]}, ensure_ascii=False)])
    out = await sp._generate_video_prompts(llm, ["旁白1"], context={"title": "t", "adjacent_narrations": {"prev": "a", "next": "b"}, "shot_direction": blk})
    assert out == ["新画面"]
    user = _user_json(llm.calls[0])
    keys = list(user.keys())
    assert keys[0] == "shot_direction" and user["shot_direction"]["current"]["frame_index"] == 1
    assert "shot_directive" in user


@pytest.mark.asyncio
async def test_s3_03_generate_shot_row_single_call(tmp_path: Path) -> None:
    """插帧行级生成：±2 邻行 + 新帧旁白 + 锚定卡进请求；恰好 1 次 LLM；坏输出 → None。"""
    row_json = json.dumps({"shot_size": "medium", "camera_move": "static", "subject_position": "居中", "composition": "对称", "continuity": "承接前镜站位"}, ensure_ascii=False)
    llm = _LLM([row_json])
    row = await sp.generate_shot_row_for_frame(
        llm,
        title="t",
        narration="新帧旁白",
        prev_rows=[{"frame_index": 0, "shot_size": "wide", "camera_move": "static"}, {"frame_index": 1, "shot_size": "medium", "camera_move": "pan"}],
        next_rows=[{"frame_index": 3, "shot_size": "close", "camera_move": "tilt"}],
        anchor_card="锚定卡",
    )
    assert row is not None and row["shot_size"] == "medium" and "frame_index" not in row, "行不带帧号（调用方盖戳）"
    assert len(llm.calls) == 1
    user = _user_json(llm.calls[0])
    assert user["new_frame_narration"] == "新帧旁白" and user["anchor_card"] == "锚定卡"
    assert len(user["prev_neighbor_rows"]) == 2 and len(user["next_neighbor_rows"]) == 1
    # 坏输出 → None（不重试不阻断插帧）
    bad = _LLM(["not json"])
    assert await sp.generate_shot_row_for_frame(bad, title="t", narration="n") is None
    assert len(bad.calls) == 1


def _make_svc(tmp_path: Path):
    from app.gateway.video_gen.config import VideoGenConfig
    from app.gateway.video_gen.providers.registry import ProviderRegistry
    from app.gateway.video_gen.service import VideoGenService

    cfg = VideoGenConfig(qihu_api_key="t", engine_arch="multi_engine")
    providers = ProviderRegistry(cfg)
    mock_repo = MagicMock()
    mock_task = MagicMock()
    mock_task.payload = json.dumps({"audio_ready": True, "total_duration": 30.0})
    mock_repo.get = AsyncMock(return_value=mock_task)
    mock_repo.update = AsyncMock(return_value=None)
    svc = VideoGenService(
        repo=mock_repo,
        config=cfg,
        providers=providers,
        storage_dir=str(tmp_path),
        sandbox_provider=AsyncMock(),
    )
    return svc


def _write_sb(task_dir: Path, *, with_table: bool = True) -> None:
    data: dict = {
        "title": "t",
        "config": {"video_type": "ai_video", "width": 720, "height": 1280},
        "frames": [
            {"index": 0, "narration": "n0"},
            {"index": 1, "narration": "n1"},
            {"index": 2, "narration": "n2"},
        ],
    }
    if with_table:
        data["shot_table"] = json.loads(_table_json(3))["shot_table"]
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "storyboard.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


@pytest.mark.asyncio
async def test_s3_04_insert_frame_generates_row_once(tmp_path: Path, monkeypatch) -> None:
    """插帧只增一行且邻行不变；行级再生调用仅 1 次 LLM（S3 场景 2）。"""
    svc = _make_svc(tmp_path)
    task_dir = Path(svc._storage_dir) / "u1" / "tasks" / "vg_st"
    _write_sb(task_dir)

    calls: list[dict] = []

    async def fake_gen(llm, **kw):  # noqa: ANN001
        calls.append(kw)
        return {"shot_size": "medium", "camera_move": "pan", "subject_position": "居中", "composition": "c", "continuity": "t"}

    monkeypatch.setattr(sp, "generate_shot_row_for_frame", fake_gen)
    monkeypatch.setattr("app.gateway.video_gen.service.create_script_llm", lambda payload, cfg: object())

    await svc._add_frame("vg_st", "u1", {"narration": "mid"}, index=1)

    data = json.loads((task_dir / "storyboard.json").read_text(encoding="utf-8"))
    rows = data["shot_table"]
    assert [r["frame_index"] for r in rows] == [0, 1, 2, 3], "4 帧 4 行对齐"
    assert rows[1]["subject_position"] == "居中", "新行来自行级生成"
    assert rows[2]["subject_position"] == "画面左侧1" and rows[3]["subject_position"] == "画面左侧2", "旧行平移后内容不变（邻行不变）"
    assert len(calls) == 1, "行级再生仅 1 次 LLM 调用"
    kw = calls[0]
    assert kw["narration"] == "mid"
    assert [r["frame_index"] for r in kw["prev_rows"]] == [0], "插在 1：前邻 = 原帧0（±2 内）"
    assert [r["frame_index"] for r in kw["next_rows"]] == [2, 3], "后邻 = 平移后的原帧1/2"


@pytest.mark.asyncio
async def test_s3_05_insert_without_table_no_llm(tmp_path: Path, monkeypatch) -> None:
    """无表任务插帧：零 LLM 调用（现状行为不变）。"""
    svc = _make_svc(tmp_path)
    task_dir = Path(svc._storage_dir) / "u1" / "tasks" / "vg_st2"
    _write_sb(task_dir, with_table=False)

    async def fail_gen(llm, **kw):  # noqa: ANN001
        raise AssertionError("无表任务不得触发行级生成")

    monkeypatch.setattr(sp, "generate_shot_row_for_frame", fail_gen)
    await svc._add_frame("vg_st2", "u1", {"narration": "mid"}, index=1)
    data = json.loads((task_dir / "storyboard.json").read_text(encoding="utf-8"))
    assert len(data["frames"]) == 4 and "shot_table" not in data


@pytest.mark.asyncio
async def test_s3_06_delete_frame_removes_row(tmp_path: Path) -> None:
    svc = _make_svc(tmp_path)
    task_dir = Path(svc._storage_dir) / "u1" / "tasks" / "vg_st3"
    _write_sb(task_dir)
    await svc._delete_frame("vg_st3", "u1", 1)
    data = json.loads((task_dir / "storyboard.json").read_text(encoding="utf-8"))
    assert [r["frame_index"] for r in data["shot_table"]] == [0, 1]
    assert data["shot_table"][1]["subject_position"] == "画面左侧2", "原帧2 行平移到 1 且内容不变"


@pytest.mark.asyncio
async def test_s3_07_regen_endpoint_reuses_row_and_keeps_table(tmp_path: Path, monkeypatch) -> None:
    """S3 场景 1+3（端点级）：单帧重生注入本帧行+邻行、shot/camera 由表决定（不清 None）、
    shot_table 全部行原样保留（编辑旁白默认保留行）。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.gateway._shared.task_framework.schemas import DRAFT
    from app.gateway.routers.video_gen import router

    svc = _make_svc(tmp_path)
    # 端点用 get_effective_user_id() 拼 storyboard 路径——conftest autouse fixture
    # 已设测试用户（test-user-autouse），动态取值而非硬编码。
    from deerflow.runtime.user_context import get_effective_user_id

    task_dir = Path(svc._storage_dir) / get_effective_user_id() / "tasks" / "vg_st4"
    _write_sb(task_dir)
    task = MagicMock()
    task.id = "vg_st4"
    task.status = DRAFT
    task.payload = json.dumps({"style": "default"})
    task.result = "{}"
    svc.repo.get = AsyncMock(return_value=task)
    svc.repo.update = AsyncMock(return_value=None)

    captured: list[dict] = []

    async def fake_video_prompts(llm, narrations, context=None, timeout=None, style_guard=""):  # noqa: ANN001
        captured.append(context or {})
        return ["重生画面"]

    monkeypatch.setattr(sp, "_generate_video_prompts", fake_video_prompts)
    monkeypatch.setattr("app.gateway.video_gen.service.create_script_llm", lambda payload, cfg: object())

    app = FastAPI()
    app.include_router(router)
    app.state.video_gen_service = svc
    client = TestClient(app)
    resp = client.post("/api/video/tasks/vg_st4/storyboard/frames/1/regenerate_prompt")
    assert resp.status_code == 200, resp.text

    ctx = captured[0]
    assert ctx.get("shot_direction", {}).get("current", {}).get("frame_index") == 1, "重生 context 须携带本帧行"
    assert "prev" in ctx["shot_direction"] and "next" in ctx["shot_direction"]

    data = json.loads((task_dir / "storyboard.json").read_text(encoding="utf-8"))
    f1 = data["frames"][1]
    assert f1["image_prompt"], "image_prompt 已重生"
    assert f1["shot_size"] == "medium" and f1["camera_move"] == "push_in", "有表帧：由表决定（不再清 None）"
    rows = data.get("shot_table") or []
    assert [r["frame_index"] for r in rows] == [0, 1, 2] and rows[0]["subject_position"] == "画面左侧0", "全部行原样保留（不改邻行/默认保留行）"


@pytest.mark.asyncio
async def test_s3_08_full_regen_replaces_table() -> None:
    """S3 场景 4：全部重生走 S1 全量——既有表被新表整体替换。"""
    llm = _LLM([_ANCHOR_JSON, _table_json(3), _frame_json(), _frame_json(), _frame_json()])
    sb = _sb(3)
    old = sp._parse_shot_table_rows(_table_json(3), 3, main_engine="ai_video")
    sb.shot_table = old
    await sp.plan_course(llm, storyboard=sb, shot_table_enabled=True)
    assert sb.shot_table is not old
    assert [r.subject_position for r in sb.shot_table] == ["画面左侧0", "画面左侧1", "画面左侧2"]


# ── S5：配置与回滚 ────────────────────────────────────────────────────────


def test_s5_01_config_defaults() -> None:
    from app.gateway.video_gen.config import VideoGenConfig

    cfg = VideoGenConfig()
    assert cfg.shot_table_enabled is True, "总开关默认开（回滚 = 配置置 false）"
    assert cfg.shot_table_model is None, "默认跟随 script_model（同一 llm 实例）"


@pytest.mark.asyncio
async def test_s5_02_disabled_byte_identical_products() -> None:
    """AC3：shot_table_enabled=False → 产物形状与现状逐字节一致（无 shot_table 键、
    逐帧请求零调度内容、镜头语言沿用逐帧返回 + smooth 照旧）。"""
    llm_a = _LLM([_ANCHOR_JSON, _frame_json(shot="close", move="pan"), _frame_json(shot="close", move="pan"), _frame_json(shot="close", move="pan")])
    sb_a = _sb(3)
    await sp.plan_course(llm_a, storyboard=sb_a, shot_table_enabled=False)
    llm_b = _LLM([_ANCHOR_JSON, _frame_json(shot="close", move="pan"), _frame_json(shot="close", move="pan"), _frame_json(shot="close", move="pan")])
    sb_b = _sb(3)
    await sp.plan_course(llm_b, storyboard=sb_b)  # 不传（默认关，兼容既有调用方）

    assert json.dumps(sb_a.to_dict(), ensure_ascii=False, sort_keys=True) == json.dumps(sb_b.to_dict(), ensure_ascii=False, sort_keys=True)
    assert "shot_table" not in sb_a.to_dict()
    for call in llm_a.calls:
        assert "shot_direction" not in _user_json(call)
    # 现状镜头语言链路照旧：首镜 wide 化（smooth 未被跳过）
    assert sb_a.frames[0].shot_size == "wide"


@pytest.mark.asyncio
async def test_s1_12_timeout_scales_with_frame_count(monkeypatch) -> None:
    """AC2 实证修复回归钉（2026-08-28）：阶段 1 超时随帧数缩放——30 帧任务在默认
    120s 下必然超时降级（真实任务日志实证，deepseek-v4-flash ≈3.6s/行）。"""
    captured: list[float] = []

    async def fake_invoke(llm, messages, *, timeout=None):  # noqa: ANN001
        captured.append(timeout)
        return SimpleNamespace(content=_table_json(30))

    monkeypatch.setattr(sp, "_invoke_llm_bounded", fake_invoke)
    rows = await sp._plan_shot_table(None, _sb(30), "")
    assert rows is not None and len(rows) == 30
    assert captured == [240.0], f"30 帧 → min(900, max(180, 30*8)) = 240s，实际 {captured}"


# ── 2026-08-31 路由并入调度表 ────────────────────────────────────────────
# 根因：E8 逐帧契约下 LLM 无帧间协调抓手，graphic 推荐暴跌（同稿组批量 4/8 →
# 逐帧 0/8 A/B 实测）。修复：visual_source 并入阶段 1 同一次调用（零新增 LLM），
# 表覆写帧值、逐帧不再推荐；无表降级路径一行不改。


class TestRoutingColumnParse:
    def test_valid_values_kept(self) -> None:
        rows = sp._parse_shot_table_rows(_table_json(3, graphic=(1,)), 3, main_engine="ai_video")
        assert [r.visual_source for r in rows] == ["ai_video", "graphic", "ai_video"]

    def test_invalid_and_missing_normalized_none_no_collateral(self) -> None:
        """非法值（跨引擎/未知）与缺失 → 归一 None；其余行字段不连坐（不废整表）。"""
        raw = json.dumps(
            {
                "shot_table": [
                    {"frame_index": 0, "shot_size": "wide", "camera_move": "static", "visual_source": "remotion"},
                    {"frame_index": 1, "shot_size": "medium", "camera_move": "pan", "visual_source": "graphic"},
                    {"frame_index": 2, "shot_size": "close", "camera_move": "tilt"},
                ]
            },
            ensure_ascii=False,
        )
        rows = sp._parse_shot_table_rows(raw, 3, main_engine="ai_video")
        assert [r.visual_source for r in rows] == [None, "graphic", None]
        assert rows[0].shot_size == "wide" and rows[0].continuity == "", "非法 vs 只归一该行 vs，其余字段保留"
        assert rows[2].camera_move == "tilt", "缺失 vs 行照常产出（漏列不废表）"

    def test_legacy_rows_without_column_are_none(self) -> None:
        """存量形状（行无 visual_source 键）→ None（只读兼容）。"""
        rows = sp._parse_shot_table_rows(_table_json_legacy(2), 2, main_engine="ai_video")
        assert all(r.visual_source is None for r in rows)

    def test_ai_image_enum_narrowed_to_main_engine(self) -> None:
        """枚举随任务主引擎收窄：ai_image 任务里 ai_video 值 → 归一 None。"""
        raw = json.dumps(
            {
                "shot_table": [
                    {"frame_index": 0, "shot_size": "wide", "camera_move": "static", "visual_source": "ai_video"},
                    {"frame_index": 1, "shot_size": "medium", "camera_move": "pan", "visual_source": "graphic"},
                ]
            },
            ensure_ascii=False,
        )
        rows = sp._parse_shot_table_rows(raw, 2, main_engine="ai_image")
        assert [r.visual_source for r in rows] == [None, "graphic"]

    def test_storyboard_roundtrip_carries_column(self) -> None:
        from app.gateway.video_gen.storyboard import ShotDirection

        sb = _sb(1)
        sb.shot_table = sp._parse_shot_table_rows(_table_json(1, graphic=(0,)), 1, main_engine="ai_video")
        d = sb.to_dict()
        assert d["shot_table"][0]["visual_source"] == "graphic"
        sb2 = Storyboard.from_dict(d)
        assert isinstance(sb2.shot_table[0], ShotDirection)
        assert sb2.shot_table[0].visual_source == "graphic"

    @pytest.mark.asyncio
    async def test_shot_table_system_carries_routing_directive(self) -> None:
        """阶段 1 system 含路由判定指令（自 routing_instruction 逐字移入）+ 契约带列。"""
        llm = _LLM([_table_json(3)])
        await sp._plan_shot_table(llm, _sb(3, video_type="ai_video"), "")
        system = llm.calls[0][0]
        assert "graphic 准入门槛" in system and "路由细则" in system, "路由判定段须在调度表 system"
        assert '"visual_source": "ai_video|graphic"' in system, "输出契约行带 visual_source 列"
        assert "image_prompt" not in system, "调度表不产 image_prompt（相关表述须删）"
        assert "全片内容节奏分配" in system, "调度原则第 5 条（visual_source 分配原则）"


class TestRoutingOverwrite:
    @pytest.mark.asyncio
    async def test_table_overwrites_and_per_frame_rec_dropped(self) -> None:
        """(c)+(d)：有表时表覆写帧 visual_source；逐帧返回值不采信（全 graphic 桩不生效）。"""
        sb = _sb(3)
        sb.shot_table = sp._parse_shot_table_rows(_table_json(3, graphic=(1,)), 3, main_engine="ai_video")
        llm = _LLM([_frame_json(vs="graphic"), _frame_json(vs="graphic"), _frame_json(vs="graphic")])
        await sp._fill_image_prompts_per_frame(llm, sb, "", concurrency=8)
        assert [f.visual_source for f in sb.frames] == ["ai_video", "graphic", "ai_video"], "帧终值 = 表值（逐帧 graphic 推荐被丢弃）"

    @pytest.mark.asyncio
    async def test_rowless_frame_keeps_none_rec_not_written(self) -> None:
        """无行帧（插帧降级态）：逐帧推荐同样不写入（表存在即去推荐）——帧保持现值。"""
        sb = _sb(3)
        rows = sp._parse_shot_table_rows(_table_json(3, graphic=(0,)), 3, main_engine="ai_video")
        sb.shot_table = [r for r in rows if r.frame_index != 1]  # frame1 无行
        llm = _LLM([_frame_json(vs="graphic"), _frame_json(vs="graphic"), _frame_json(vs="graphic")])
        await sp._fill_image_prompts_per_frame(llm, sb, "", concurrency=8)
        assert sb.frames[0].visual_source == "graphic", "有行帧由表覆写"
        assert sb.frames[1].visual_source is None, "无行帧不被逐帧推荐污染（保持 None → 护栏归默认引擎）"
        assert sb.frames[2].visual_source == "ai_video"

    @pytest.mark.asyncio
    async def test_none_vs_row_leaves_frame_untouched(self) -> None:
        """行 visual_source=None（漏列归一）：帧不动（不被表改写）。"""
        sb = _sb(1)
        sb.frames[0].visual_source = "ai_video"  # 模拟帧已有值（前轮推荐/现状值）
        raw = json.dumps({"shot_table": [{"frame_index": 0, "shot_size": "wide", "camera_move": "static"}]}, ensure_ascii=False)
        sb.shot_table = sp._parse_shot_table_rows(raw, 1, main_engine="ai_video")
        llm = _LLM([_frame_json(vs="graphic")])
        await sp._fill_image_prompts_per_frame(llm, sb, "", concurrency=8)
        assert sb.frames[0].visual_source == "ai_video", "None 行不动帧（保持帧现值，不被逐帧/表改写）"

    @pytest.mark.asyncio
    async def test_no_table_rec_written_as_before(self) -> None:
        """无表（现状 pin）：逐帧推荐照写（content_routing on 时）。"""
        sb = _sb(2)
        llm = _LLM([_frame_json(vs="graphic"), _frame_json(vs="ai_video")])
        await sp._fill_image_prompts_per_frame(llm, sb, "", concurrency=8)
        assert [f.visual_source for f in sb.frames] == ["graphic", "ai_video"]


class TestPerFrameMiniSystem:
    @pytest.mark.asyncio
    async def test_settled_mini_system_has_no_routing_instruction(self) -> None:
        """有表：mini system 不含路由选择指令，改为一句「已由调度表确定」。"""
        sb = _sb(2)
        sb.shot_table = sp._parse_shot_table_rows(_table_json(2), 2, main_engine="ai_video")
        llm = _LLM([_frame_json(), _frame_json()])
        await sp._fill_image_prompts_per_frame(llm, sb, "", concurrency=8)
        for system, _user in llm.calls:
            assert "visual_source 路由" not in system, "有表时逐帧不再推荐"
            assert "已由镜头调度表确定" in system

    @pytest.mark.asyncio
    async def test_no_table_mini_system_keeps_routing_instruction(self) -> None:
        """无表（现状 pin）：mini system 保留路由指令（降级路径一行不改）。"""
        sb = _sb(2)
        llm = _LLM([_frame_json(), _frame_json()])
        await sp._fill_image_prompts_per_frame(llm, sb, "", concurrency=8)
        for system, _user in llm.calls:
            assert "visual_source 路由" in system


@pytest.mark.asyncio
async def test_plan_course_full_chain_table_routes_and_guardrail(monkeypatch) -> None:
    """全链路（桩 LLM）：shot_table 产出含 visual_source → 帧终值 = 表值 → 护栏收敛。

    表 graphic f1/f3/f5（3/8=37.5% > 30%）→ 护栏按帧序回退 f1 → 终值 f3/f5。
    extract_graphic_specs 桩为 no-op（spec 质量另有专项测试，本用例只锁路由链）。
    """

    async def _no_specs(llm, storyboard, *, task_dir=None, protect=frozenset()):  # noqa: ANN001
        return sum(1 for f in storyboard.frames if f.visual_source == "graphic")

    monkeypatch.setattr(sp, "extract_graphic_specs", _no_specs)
    llm = _LLM([_ANCHOR_JSON, _table_json(8, graphic=(1, 3, 5)), *([_frame_json(vs="graphic")] * 8)])
    sb = _sb(8)
    await sp.plan_course(llm, storyboard=sb, shot_table_enabled=True)
    expected = ["ai_video", "ai_video", "ai_video", "graphic", "ai_video", "graphic", "ai_video", "ai_video"]
    assert [f.visual_source for f in sb.frames] == expected, "帧终值 = 表值经护栏收敛（f1 超 30% 被回退；逐帧 graphic 桩不生效）"


# ── 2026-09-01 长表稳定性 + 审查 4 项处置 ────────────────────────────────
# 根因（任务 vg_cef99db8c7da，82 帧）：attempt1 第 77 行 camera_move='fade_out'
# 整表拒绝（81 行正确被连坐）→ attempt2 JSON char 14268 断裂 → AC6 降级逐帧 →
# spec 被拒按 D-A 降主引擎 → 0/82 图文镜。修：枚举映射容错 + 调用降温 0.2 +
# 重试带错误反馈 + protect 守卫/批量 settled 补齐。


class TestRowLevelEnumTolerance:
    """T1：非法枚举安全映射（shot_size→medium、camera_move→static），>20% 仍整表拒绝。"""

    @staticmethod
    def _table(n: int, *, bad_move_at: tuple[int, ...] = (), bad_size_at: tuple[int, ...] = ()) -> str:
        rows = [
            {
                "frame_index": i,
                "shot_size": "特写" if i in bad_size_at else _SIZES[i % 3],
                "camera_move": "fade_out" if i in bad_move_at else _MOVES[i % 3],
                "subject_position": f"画面左侧{i}",
                "composition": "c",
                "continuity": "t",
            }
            for i in range(n)
        ]
        return json.dumps({"shot_table": rows}, ensure_ascii=False)

    def test_fade_out_maps_to_static_row_count_intact(self) -> None:
        """82 帧事故第一环复刻：单行 fade_out → static，表 10 行全保留。"""
        rows = sp._parse_shot_table_rows(self._table(10, bad_move_at=(7,)), 10, main_engine="ai_video")
        assert len(rows) == 10, "行数完整（个别漂移不再连坐整表）"
        assert rows[7].camera_move == "static", "非法运镜映射安全保守值 static"
        assert rows[7].shot_size == _SIZES[7 % 3] and rows[7].subject_position == "画面左侧7", "同行其余字段不连坐"

    def test_illegal_size_maps_to_medium(self) -> None:
        rows = sp._parse_shot_table_rows(self._table(5, bad_size_at=(2,)), 5, main_engine="ai_video")
        assert rows[2].shot_size == "medium", "非法景别映射 medium"

    def test_at_20_percent_threshold_kept(self) -> None:
        """10 帧映射 2 行 = 恰 20%（判定 > 20% 才拒）→ 表保留。"""
        rows = sp._parse_shot_table_rows(self._table(10, bad_move_at=(1, 3)), 10, main_engine="ai_video")
        assert len(rows) == 10 and rows[1].camera_move == "static" and rows[3].camera_move == "static"

    def test_over_20_percent_rejects_whole_table(self) -> None:
        """5 帧映射 2 行 = 40% > 20% → 整表拒绝（阈值外 = 模型失控，硬用比无表更糟）。"""
        with pytest.raises(ValueError, match=">20%"):
            sp._parse_shot_table_rows(self._table(5, bad_move_at=(1, 2)), 5, main_engine="ai_video")

    def test_vs_normalization_not_counted_in_threshold(self) -> None:
        """阈值口径（硬要求4）：只数 shot_size/camera_move 映射行——visual_source
        归一 None 不进分子（10 帧全非法 vs = 0 映射行，不拒表）。"""
        raw = json.dumps(
            {"shot_table": [{"frame_index": i, "shot_size": "wide", "camera_move": "static", "visual_source": "remotion"} for i in range(10)]},
            ensure_ascii=False,
        )
        rows = sp._parse_shot_table_rows(raw, 10, main_engine="ai_video")
        assert len(rows) == 10 and all(r.visual_source is None for r in rows)

    def test_invalid_frame_index_still_rejects(self) -> None:
        """frame_index 非法仍整表拒绝（帧号错位无法安全映射——硬要求4）。"""
        raw = json.dumps({"shot_table": [{"frame_index": 99, "shot_size": "wide", "camera_move": "static"}]}, ensure_ascii=False)
        with pytest.raises(ValueError, match="frame_index"):
            sp._parse_shot_table_rows(raw, 1, main_engine="ai_video")

    def test_row_count_mismatch_still_rejects(self) -> None:
        """行数不齐仍整表拒绝（硬要求4：不因行级容错放松对齐纪律）。"""
        with pytest.raises(ValueError, match="rows, expected"):
            sp._parse_shot_table_rows(self._table(2), 3, main_engine="ai_video")


class TestEnumAliasNormalization:
    """枚举缺口修复（2026-09-01，vg_775294e3b0f5 实锤）：方向细分变体先归一再判非法。

    62 行表 attempt1 21 行 / attempt2 25 行全是 pan_left/pan_right/tilt_up/tilt_down
    ——合法运镜的方向细分（模型正常表达倾向），旧逻辑计入 20% 失控分子 → 整表
    连坐拒绝 → 降级逐帧 → graphic 推荐塌缩。归一是确定性语义映射，不进分子。
    """

    @staticmethod
    def _table(n: int, *, move_at: dict[int, str] | None = None, size_at: dict[int, str] | None = None, bad_move_at: tuple[int, ...] = ()) -> str:
        rows = [
            {
                "frame_index": i,
                "shot_size": (size_at or {}).get(i, _SIZES[i % 3]),
                "camera_move": (move_at or {}).get(i, "fade_out" if i in bad_move_at else _MOVES[i % 3]),
                "subject_position": f"画面左侧{i}",
                "composition": "c",
                "continuity": "t",
            }
            for i in range(n)
        ]
        return json.dumps({"shot_table": rows}, ensure_ascii=False)

    def test_vg775294_scenario_34_percent_variants_table_survives(self) -> None:
        """事故复刻：62 行 21 行方向变体（34% > 20%）→ 归一后表存活且值=canonical。"""
        n = 62
        move_at = {i: ("pan_left" if i % 2 else "tilt_up") for i in range(21)}
        rows = sp._parse_shot_table_rows(self._table(n, move_at=move_at), n, main_engine="ai_video")
        assert len(rows) == n, "方向变体不进失控分子——34% 变体表仍存活（修前整表拒绝）"
        assert rows[0].camera_move == "tilt", "tilt_up → tilt"
        assert rows[1].camera_move == "pan", "pan_left → pan"

    @pytest.mark.parametrize(
        ("raw", "canonical"),
        [
            ("pan_right", "pan"),
            ("tilt_down", "tilt"),
            ("zoom_in", "push_in"),
            ("zoom_out", "pull_out"),
            ("dolly_in", "push_in"),
            ("dolly_out", "pull_out"),
            ("Pan-Left", "pan"),
            ("PAN RIGHT", "pan"),
            ("Tilt-Down", "tilt"),
            ("static_shot", "static"),
        ],
    )
    def test_move_alias_matrix(self, raw: str, canonical: str) -> None:
        """别名矩阵：常见变体 → canonical（含大小写/连字符/空格归一）。"""
        rows = sp._parse_shot_table_rows(self._table(1, move_at={0: raw}), 1, main_engine="ai_video")
        assert rows[0].camera_move == canonical, f"{raw} 应归一为 {canonical}，实际 {rows[0].camera_move}"

    @pytest.mark.parametrize(
        ("raw", "canonical"),
        [("close_up", "close"), ("extreme_wide", "wide"), ("Medium Shot", "medium"), ("detail_shot", "close")],
    )
    def test_size_alias_matrix(self, raw: str, canonical: str) -> None:
        rows = sp._parse_shot_table_rows(self._table(1, size_at={0: raw}), 1, main_engine="ai_video")
        assert rows[0].shot_size == canonical, f"{raw} 应归一为 {canonical}，实际 {rows[0].shot_size}"

    def test_true_hallucination_still_counted_and_capped(self) -> None:
        """反向护栏不放松：fade_out 类真幻觉不受别名层保护，仍计数仍整表拒。"""
        with pytest.raises(ValueError, match=">20%"):
            sp._parse_shot_table_rows(self._table(5, bad_move_at=(1, 2)), 5, main_engine="ai_video")

    def test_mixed_variants_and_hallucination_only_latter_counted(self) -> None:
        """混排定性：21 处变体（不计数）+ 2 行真幻觉（2/62 < 20%）→ 表存活，
        幻觉行照旧安全映射 static。"""
        n = 62
        move_at = {i: "pan_left" for i in range(21)}
        rows = sp._parse_shot_table_rows(self._table(n, move_at=move_at, bad_move_at=(30, 31)), n, main_engine="ai_video")
        assert len(rows) == n
        assert rows[30].camera_move == "static" and rows[31].camera_move == "static", "真幻觉行仍安全映射"
        assert rows[5].camera_move == "pan", "变体行归一不受影响"

    @pytest.mark.asyncio
    async def test_e2e_plan_shot_table_with_variant_output(self) -> None:
        """端到端：LLM 全量输出方向变体 → 调度表成功生成（不再二次重试降级）。"""
        n = 4
        raw_rows = [{"frame_index": i, "shot_size": "close_up" if i % 2 else "wide", "camera_move": "pan_left" if i % 2 else "tilt_down", "subject_position": f"左{i}", "composition": "c", "continuity": "t"} for i in range(n)]
        llm = _LLM([json.dumps({"shot_table": raw_rows}, ensure_ascii=False)])
        sb = _sb(n)
        table = await sp._plan_shot_table(llm, sb, "")
        assert table is not None and len(table) == n, "变体输出不再触发整表拒绝"
        assert table[0].camera_move == "tilt" and table[1].camera_move == "pan"
        assert table[1].shot_size == "close"
        assert len(llm.calls) == 1, "attempt1 即通过——无重试"


class _BindLLM(_LLM):
    """带 bind 的记录桩（T2）：捕获 bind kwargs，返回自身继续记录 ainvoke。"""

    def __init__(self, contents: list[str]) -> None:
        super().__init__(contents)
        self.bound_kwargs: list[dict] = []

    def bind(self, **kwargs):  # noqa: ANN001, ANN003
        self.bound_kwargs.append(dict(kwargs))
        return self


class TestShotTableTemperature:
    """T2：调度表单次调用降温 bind(temperature=0.2)（hasattr 防御式）。"""

    @pytest.mark.asyncio
    async def test_binds_temperature_0_2(self) -> None:
        llm = _BindLLM([_table_json(3)])
        await sp._plan_shot_table(llm, _sb(3), "")
        assert llm.bound_kwargs == [{"temperature": 0.2}], f"调度表调用须单独降温 0.2，实际 {llm.bound_kwargs}"
        assert len(llm.calls) == 1

    @pytest.mark.asyncio
    async def test_narrow_stub_without_bind_not_broken(self) -> None:
        """窄签名桩（无 bind，同既有 _LLM）→ hasattr 防御分支：不炸、温度静默不生效。"""
        llm = _LLM([_table_json(3)])  # 故意不加 bind
        assert not hasattr(llm, "bind")
        rows = await sp._plan_shot_table(llm, _sb(3), "")
        assert rows is not None and len(rows) == 3

    @pytest.mark.asyncio
    async def test_bind_once_outside_retry_loop(self) -> None:
        """重试链不重复 bind（bind 一次复用两次 attempt）。"""
        llm = _BindLLM(["bad", _table_json(3)])
        rows = await sp._plan_shot_table(llm, _sb(3), "")
        assert rows is not None
        assert llm.bound_kwargs == [{"temperature": 0.2}], "恰好 bind 一次"
        assert len(llm.calls) == 2


class TestRetryErrorFeedback:
    """T3：attempt≥2 的 user 追加上次错误摘要（自我纠正反馈）。"""

    @pytest.mark.asyncio
    async def test_attempt2_user_carries_error_summary(self) -> None:
        llm = _LLM(["这不是JSON", _table_json(3)])
        rows = await sp._plan_shot_table(llm, _sb(3), "")
        assert rows is not None and len(llm.calls) == 2
        user1, user2 = llm.calls[0][1], llm.calls[1][1]
        assert "上次输出未通过校验" not in user1, "attempt1 user 原样（不带反馈）"
        assert user2.startswith(user1), "原始负载保留在前（追加而非替换）"
        assert "上次输出未通过校验：" in user2
        assert "请严格按枚举域与 JSON 格式重新输出全部 3 行" in user2

    @pytest.mark.asyncio
    async def test_summary_truncated_to_200_chars(self) -> None:
        """超长异常摘要截 200 字（防爆 token）。"""

        class _HugeErrorLLM(_LLM):
            async def ainvoke(self, messages):  # noqa: ANN001
                self.calls.append((messages[0]["content"], messages[1]["content"]))
                raise RuntimeError("E" * 5000)

        llm = _HugeErrorLLM([])
        assert await sp._plan_shot_table(llm, _sb(3), "") is None
        # 两次都失败：attempt2 的 user 里摘要 = 上次异常截 200 字
        user2 = llm.calls[1][1]
        seg = user2.split("上次输出未通过校验：", 1)[1]
        assert "EEEE" in seg and seg.index("。请严格按") <= 200 + 1, "摘要截断在 200 字内"


class TestProtectGuardOnTableOverwrite:
    """T4（审查 #1）：P0-5「用户手改帧不被任何规划产物覆盖」覆盖表覆写——
    protect 帧三列（shot_size/camera_move/visual_source）同豁免。"""

    def test_apply_skips_protect_frame_only(self) -> None:
        from app.gateway.video_gen.storyboard import StoryboardFrame

        frames = [
            StoryboardFrame(index=0, narration="n0", shot_size="close", camera_move="tilt", visual_source="graphic"),
            StoryboardFrame(index=1, narration="n1"),
        ]
        rows = sp._parse_shot_table_rows(_table_json(2), 2, main_engine="ai_video")
        sp._apply_shot_table_fields(frames, rows, protect=frozenset({0}))
        assert frames[0].shot_size == "close" and frames[0].camera_move == "tilt", "protect 帧 shot/camera 不被覆写"
        assert frames[0].visual_source == "graphic", "protect 帧 visual_source 不被覆写"
        assert frames[1].shot_size == _SIZES[1] and frames[1].camera_move == _MOVES[1], "非 protect 帧正常覆写"
        assert frames[1].visual_source == "ai_video"

    def test_default_empty_protect_keeps_old_behavior(self) -> None:
        """默认空集 = 既有行为（全部有行帧覆写）——向后兼容 pin。"""
        from app.gateway.video_gen.storyboard import StoryboardFrame

        frames = [StoryboardFrame(index=0, narration="n0", shot_size="close", camera_move="tilt", visual_source="graphic")]
        rows = sp._parse_shot_table_rows(_table_json(1, graphic=(0,)), 1, main_engine="ai_video")
        sp._apply_shot_table_fields(frames, rows)
        assert frames[0].shot_size == _SIZES[0] and frames[0].visual_source == "graphic"

    @pytest.mark.asyncio
    async def test_plan_course_threads_protect_through_chain(self, monkeypatch) -> None:
        """全链路透传（plan_course → robust → per_frame → writeback → apply）：
        规划前手改帧（PUT 重建场景）的 visual_source 不被表值覆写。"""

        async def _no_specs(llm, storyboard, *, task_dir=None, protect=frozenset()):  # noqa: ANN001
            return sum(1 for f in storyboard.frames if f.visual_source == "graphic")

        monkeypatch.setattr(sp, "extract_graphic_specs", _no_specs)
        # 隔离护栏占比/连续规则（只验表覆写豁免，同 TestRunCap 先例放开 env）
        monkeypatch.setenv("VIDEO_GEN_GRAPHIC_MAX_RATIO", "1.0")
        monkeypatch.setenv("VIDEO_GEN_GRAPHIC_MAX_RUN", "10")
        sb = _sb(4)
        sb.frames[1].visual_source = "graphic"  # 规划前已有值 = 用户手改（P0-5）
        # 表值：f1 行 = ai_video（与手改值冲突——若无守卫会被覆写）；f2 行 graphic
        llm = _LLM([_ANCHOR_JSON, _table_json(4, graphic=(2,)), *([_frame_json()] * 4)])
        await sp.plan_course(llm, storyboard=sb, shot_table_enabled=True)
        assert sb.frames[1].visual_source == "graphic", "protect 帧手改值不被表覆写"
        assert sb.frames[2].visual_source == "graphic", "非 protect 有行帧 = 表值"
        assert sb.frames[0].visual_source == "ai_video" and sb.frames[3].visual_source == "ai_video"


class TestBatchedSettled:
    """T5（审查 #2）：batched 路径有表时补传 visual_source_settled（与 robust 同款）。"""

    @pytest.mark.asyncio
    async def test_batched_receives_settled_when_table_present(self, monkeypatch) -> None:
        captured: list[dict] = []

        async def fake_enhanced(llm, mini, style_prefix, **kwargs):  # noqa: ANN001
            captured.append(dict(kwargs))
            return [f"画面{j}" for j in range(len(mini.frames))], ["ai_video"] * len(mini.frames)

        monkeypatch.setattr(sp, "_generate_course_prompts_enhanced", fake_enhanced)
        sb = _sb(3)
        sb.shot_table = sp._parse_shot_table_rows(_table_json(3), 3, main_engine="ai_video")
        await sp._plan_course_batched(None, sb, "")
        assert captured and all(kw.get("visual_source_settled") is True for kw in captured), f"组批量调用须带 settled=True，实际 {captured}"

    @pytest.mark.asyncio
    async def test_batched_no_table_no_settled(self, monkeypatch) -> None:
        """无表（现状 pin）：不传 settled（降级路径行为不变）。"""
        captured: list[dict] = []

        async def fake_enhanced(llm, mini, style_prefix, **kwargs):  # noqa: ANN001
            captured.append(dict(kwargs))
            return [f"画面{j}" for j in range(len(mini.frames))], ["ai_video"] * len(mini.frames)

        monkeypatch.setattr(sp, "_generate_course_prompts_enhanced", fake_enhanced)
        sb = _sb(3)
        await sp._plan_course_batched(None, sb, "")
        assert captured and all("visual_source_settled" not in kw for kw in captured)

    @pytest.mark.asyncio
    async def test_batched_table_present_sources_not_written(self, monkeypatch) -> None:
        """2026-09-01 复核修复（审查再反馈）：有表时逐组返回的 visual_source
        **不写入**——settled mini 的输出契约仍含该字段，写回层须与
        _writeback_and_smooth 同款表门控，否则表行 vs=None 的帧被残留推荐
        污染（表覆写对 None 行不触碰，残留存活）。"""

        async def fake_enhanced(llm, mini, style_prefix, **kwargs):  # noqa: ANN001
            return [f"画面{j}" for j in range(len(mini.frames))], ["graphic"] * len(mini.frames)

        monkeypatch.setattr(sp, "_generate_course_prompts_enhanced", fake_enhanced)
        sb = _sb(3)
        # 表行全 vs=None（漏列归一形态）——恰好是会被残留推荐污染的行
        sb.shot_table = sp._parse_shot_table_rows(_table_json_legacy(3), 3, main_engine="ai_video")
        await sp._plan_course_batched(None, sb, "")
        assert all(f.visual_source is None for f in sb.frames), "有表时组返回的 graphic 残留不得写入（帧保持 None → 护栏归默认引擎）"

    @pytest.mark.asyncio
    async def test_batched_no_table_sources_written_as_before(self, monkeypatch) -> None:
        """对照 pin：无表时组返回的推荐照写（只填 None 帧——降级路径现状）。"""

        async def fake_enhanced(llm, mini, style_prefix, **kwargs):  # noqa: ANN001
            return [f"画面{j}" for j in range(len(mini.frames))], ["graphic"] * len(mini.frames)

        monkeypatch.setattr(sp, "_generate_course_prompts_enhanced", fake_enhanced)
        sb = _sb(3)
        await sp._plan_course_batched(None, sb, "")
        assert [f.visual_source for f in sb.frames] == ["graphic", "graphic", "graphic"], "无表：组推荐照写（现状）"
