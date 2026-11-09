"""Plan C Task 1（2026-10-19）：长脚本确定性分批与安全合并纯函数。

设计 D6/D8（docs/2026-10-17-vimax-reliability-optimization-design.md）：
- 全局镜头预算 >12 才拆批（常量 SINGLE_BATCH_SHOT_THRESHOLD=12），每批目标 ≤8
  （STORYBOARD_BATCH_SHOT_CAP=8）；短脚本/无预算保持单批。
- 按原文段落/完整句子确定性切分；完整原文只读上下文；批次顺序执行。
- 合并：批次顺序+批内顺序重编号全局 idx；仅全局末镜 is_last；局部 cam_idx 映射
  独立全局命名空间（批内复用保留、跨批不碰撞）；空批/乱序/超限 raise。
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock as _AsyncMock

import pytest
from langchain_core.runnables import Runnable
from vimax.interfaces.character import CharacterInScene
from vimax.interfaces.shot_description import ShotBriefDescription
from vimax.pipelines.script2video_pipeline import Script2VideoPipeline
from vimax.utils.text import (
    SINGLE_BATCH_SHOT_THRESHOLD,
    STORYBOARD_BATCH_SHOT_CAP,
    StoryboardBatchPlan,
    merge_storyboard_batches,
    plan_storyboard_batches,
)

SCRIPT = "\n\n".join(f"第{i}段：{'内容句子。' * (i % 3 + 1)}" for i in range(1, 13))


def _shot(idx: int, *, cam_idx: int = 0, is_last: bool = False, visual: str = "v") -> ShotBriefDescription:
    return ShotBriefDescription(idx=idx, is_last=is_last, cam_idx=cam_idx, visual_desc=visual, audio_desc="a")


# ── 分批 ───────────────────────────────────────────────────────────────────


def test_threshold_and_cap_constants():
    assert SINGLE_BATCH_SHOT_THRESHOLD == 12
    assert STORYBOARD_BATCH_SHOT_CAP == 8


def test_budget_12_single_batch():
    """预算 ≤12：单批、预算原值、文本=全文。"""
    plans = plan_storyboard_batches(SCRIPT, global_shot_budget=12)
    assert len(plans) == 1
    assert plans[0].shot_budget == 12
    assert plans[0].text == SCRIPT


@pytest.mark.parametrize("budget,expected_batches", [(13, 2), (24, 3), (25, 4)])
def test_budget_splits_by_cap(budget, expected_batches):
    """预算 >12：批数 = ceil(budget/8)；每批非空且 budget≤8；预算和=全局。"""
    plans = plan_storyboard_batches(SCRIPT, global_shot_budget=budget)
    assert len(plans) == expected_batches
    assert all(p.shot_budget <= STORYBOARD_BATCH_SHOT_CAP for p in plans)
    assert all(p.text.strip() for p in plans)
    assert sum(p.shot_budget for p in plans) == budget


def test_batches_cover_script_exactly_once_in_order():
    """每段原文恰好覆盖一次：按序拼接 == 原文（无丢失/重复/改写）。"""
    plans = plan_storyboard_batches(SCRIPT, global_shot_budget=19)
    assert "".join(p.text for p in plans) == SCRIPT


def test_unsplittable_text_reduces_batch_count_without_hard_cut():
    """可分段单元不足：减少批数（单批超长也整批），不硬切句子、不造空批。"""
    wall = "一句不可分割的超长连续文本没有任何段落与句子边界" * 20
    plans = plan_storyboard_batches(wall, global_shot_budget=16)
    assert len(plans) == 1, "单元不可分时不得硬切出多批"
    assert plans[0].text == wall
    assert plans[0].shot_budget <= STORYBOARD_BATCH_SHOT_CAP


def test_empty_script_raises():
    with pytest.raises(ValueError):
        plan_storyboard_batches("", global_shot_budget=19)
    with pytest.raises(ValueError):
        plan_storyboard_batches("   \n  ", global_shot_budget=19)


def test_nonpositive_budget_raises():
    with pytest.raises(ValueError):
        plan_storyboard_batches(SCRIPT, global_shot_budget=0)
    with pytest.raises(ValueError):
        plan_storyboard_batches(SCRIPT, global_shot_budget=-3)


def test_short_script_single_batch():
    plans = plan_storyboard_batches("短脚本一段。", global_shot_budget=5)
    assert len(plans) == 1
    assert plans[0].shot_budget == 5


# ── 边界热修复（2026-10-20 P0）：纯空白不得独立成批 ────────────────────────


def test_trailing_newline_not_own_batch():
    """[Critical 复现①] 尾部换行 + budget=24：句切会产出独立批 "\n"——修前该批
    把纯空白当剧本喂给 LLM。每批必须有非空白内容；拼接零丢失。"""
    script = "第一句内容甲乙丙丁戊己。第二句内容甲乙丙丙丁戊。\n"
    plans = plan_storyboard_batches(script, global_shot_budget=24)
    assert all(p.text.strip() for p in plans), f"不得有纯空白批：{[repr(p.text) for p in plans]}"
    assert "".join(p.text for p in plans) == script
    assert len(plans) >= 2 and all(p.shot_budget <= STORYBOARD_BATCH_SHOT_CAP for p in plans)


def test_leading_blank_lines_not_own_batch():
    """[Critical 复现②] 前导空行：修前 "\n\n" 因「无前单元可并入」直入 units[0]
    独占批 0。前导空白须并入后一批，拼接零丢失。"""
    script = "\n\n第一句内容甲乙丙丁戊己。\n\n第二句内容甲乙丙丙丁戊。"
    plans = plan_storyboard_batches(script, global_shot_budget=24)
    assert all(p.text.strip() for p in plans), f"不得有纯空白批：{[repr(p.text) for p in plans]}"
    assert "".join(p.text for p in plans) == script


def test_cjk_punct_plus_newline_no_whitespace_batch():
    """CJK 句末标点与换行混排：句切/段切两分支都不产纯空白批（含省略号、
    全半角标点交错），拼接零丢失。"""
    script = "开场白一句。！感叹一串？\n中段陈述；分号一断…\n末句收尾。"
    for budget in (24, 13, 8):
        plans = plan_storyboard_batches(script, global_shot_budget=budget)
        assert all(p.text.strip() for p in plans), f"budget={budget} 不得有纯空白批：{[repr(p.text) for p in plans]}"
        assert "".join(p.text for p in plans) == script


def test_interleaved_blank_lines_no_whitespace_batch():
    """段间多空行 + 尾部空白：全部折叠进相邻批，不丢字符。"""
    script = "第一段句子。\n\n\n\n第二段句子。\n\n"
    plans = plan_storyboard_batches(script, global_shot_budget=16)
    assert all(p.text.strip() for p in plans), f"不得有纯空白批：{[repr(p.text) for p in plans]}"
    assert "".join(p.text for p in plans) == script


def test_english_decimal_not_mis_split():
    """英文小数 3.5 / 2.7 不得被句切拆断（既有行为回归钉死：ASCII `.` 保留
    (?=\\s|$) 前瞻）。"""
    script = "The budget is 3.5 million. Growth reached 2.7 percent this year. Next year looks better."
    plans = plan_storyboard_batches(script, global_shot_budget=24)
    joined = "".join(p.text for p in plans)
    assert joined == script
    # 3.5 与 2.7 必须完整落在同一批内（未被小数点切开跨批）
    assert any("3.5 million." in p.text for p in plans), f"3.5 不得被小数点误切：{[repr(p.text) for p in plans]}"
    assert any("2.7 percent" in p.text for p in plans)


def test_prompt_version_pinned_for_whitespace_fix():
    """[评审 minor 实修] prompt version 必须为 "3"——P0 修复的缓存作废半机制：
    若该 bump 在后续合并中静默丢失，含空白批的旧批次缓存（LLM 产物，不经
    plan_storyboard_batches 守卫）会复活 Critical 症状且全套件无红。指纹消费点
    = script2video_pipeline 缓存 fingerprint 的 prompt_version 字段。"""
    from vimax.utils.text import _STORYBOARD_BATCH_PROMPT_VERSION

    assert _STORYBOARD_BATCH_PROMPT_VERSION == "3"


def test_whitespace_batch_internal_guard_raises(monkeypatch):
    """防御分支显式 if/raise（非 assert——`python -O` 下 assert 失效）：
    即使上游切分回归产出纯空白单元，plan 也不得放行。"""
    import vimax.utils.text as text_mod

    monkeypatch.setattr(text_mod, "_split_units", lambda s: ["\n"])
    with pytest.raises(ValueError, match="空白"):
        plan_storyboard_batches("第一句。第二句。", global_shot_budget=24)


# ── 合并 ───────────────────────────────────────────────────────────────────


def _batches_19():
    """3 批（7/6/6），各批局部 idx 0..N-1、各自末镜 is_last=True、cam_idx 局部复用。"""
    out = []
    for b, n in enumerate([7, 6, 6]):
        shots = []
        for i in range(n):
            shots.append(_shot(i, cam_idx=i % 3, is_last=(i == n - 1)))
        out.append(shots)
    return out


def test_merge_19_shots_renumbers_global_idx():
    merged = merge_storyboard_batches(_batches_19(), global_shot_budget=19)
    assert len(merged) == 19
    assert [s.idx for s in merged] == list(range(19))
    assert [s.is_last for s in merged] == [False] * 18 + [True], "仅全局末镜 is_last=True"


def test_merge_cam_idx_namespace_reuse_kept_no_cross_collision():
    """(batch_idx, local_cam_idx) 首次出现映射下一全局整数；批内复用保留、跨批不碰撞。"""
    merged = merge_storyboard_batches(_batches_19(), global_shot_budget=19)
    global_cams = [s.cam_idx for s in merged]
    # 批 0 cams [0,1,2,0,1,2,0] → 全局 [0,1,2,0,1,2,0]（复用保留）
    assert global_cams[:7] == [0, 1, 2, 0, 1, 2, 0]
    # 批 1 局部 cam 0 必须映射到新全局值（≠ 批 0 的 0/1/2）
    assert global_cams[7] == 3, f"跨批 cam_idx 不得碰撞，实际批 1 首镜 cam={global_cams[7]}"
    assert all(c >= 0 for c in global_cams)


def test_merge_empty_batch_raises():
    batches = _batches_19()
    batches[1] = []
    with pytest.raises(ValueError, match="empty"):
        merge_storyboard_batches(batches, global_shot_budget=19)


def test_merge_non_contiguous_local_idx_raises():
    batches = [_shot(0), _shot(2)]  # 缺 1
    with pytest.raises(ValueError, match="contiguous"):
        merge_storyboard_batches([batches], global_shot_budget=12)


def test_merge_out_of_order_local_idx_raises():
    batches = [_shot(1), _shot(0)]
    with pytest.raises(ValueError, match="contiguous"):
        merge_storyboard_batches([batches], global_shot_budget=12)


def test_merge_negative_cam_idx_raises():
    batches = [_shot(0, cam_idx=-1)]
    with pytest.raises(ValueError, match="cam"):
        merge_storyboard_batches([batches], global_shot_budget=12)


def test_merge_over_global_budget_raises():
    batches = [[_shot(i) for i in range(7)], [_shot(i) for i in range(7)], [_shot(i) for i in range(7)]]  # 21 > 19
    with pytest.raises(ValueError, match="budget"):
        merge_storyboard_batches(batches, global_shot_budget=19)


def test_plan_dataclass_shape():
    p = StoryboardBatchPlan(text="文本", shot_budget=8)
    assert p.text == "文本"
    assert p.shot_budget == 8


# ── artist 单批契约（可选 target_script/shot_budget；缺省零变化） ──────────


class _CapturingChatModel(Runnable):
    """捕获 messages 的 chat 桩——须 Runnable 子类（langchain | 组合校验）。"""

    def __init__(self, n_shots: int = 2):
        self.messages: list = []
        self.n_shots = n_shots

    def invoke(self, input, config=None, **kwargs):
        raise NotImplementedError("sync not used")

    async def ainvoke(self, messages, config=None, **kwargs):
        self.messages = messages
        shots = [{"idx": i, "is_last": i == self.n_shots - 1, "cam_idx": 0, "visual_desc": "v", "audio_desc": "a"} for i in range(self.n_shots)]

        from langchain_core.messages import AIMessage

        return AIMessage(content=json.dumps({"storyboard": shots}, ensure_ascii=False))


@pytest.mark.asyncio
async def test_artist_default_call_prompt_unchanged():
    """缺省参数路径不增加新提示语义（无批次注释块）。"""
    from vimax.agents.storyboard_artist import StoryboardArtist

    model = _CapturingChatModel()
    artist = StoryboardArtist(chat_model=model)
    await artist.design_storyboard(script="全篇剧本。", characters=[], retry_timeout=10)
    human = model.messages[1][1] if isinstance(model.messages[1], tuple) else str(model.messages[1])
    assert "TrainerAI batching" not in human
    assert "全篇剧本" in human


@pytest.mark.asyncio
async def test_artist_batch_params_inject_segment_and_budget():
    """target_script：只为片段产镜、全文只读上下文；shot_budget 结构化上限。"""
    from vimax.agents.storyboard_artist import StoryboardArtist

    model = _CapturingChatModel()
    artist = StoryboardArtist(chat_model=model)
    await artist.design_storyboard(
        script="全篇剧本第一段。全篇剧本第二段。",
        characters=[],
        retry_timeout=10,
        target_script="全篇剧本第一段。",
        shot_budget=8,
    )
    human = model.messages[1][1] if isinstance(model.messages[1], tuple) else str(model.messages[1])
    assert "全篇剧本第一段。" in human, "片段必须是产镜对象"
    assert "全篇剧本第二段。" in human, "全文须作为只读上下文在场"
    assert "read-only" in human or "只读" in human
    assert "8" in human, "shot_budget 须进提示词"


@pytest.mark.asyncio
async def test_artist_shot_budget_only_still_single_call_context():
    """只传 shot_budget（不传片段）：预算上限进提示，无片段注释。"""
    from vimax.agents.storyboard_artist import StoryboardArtist

    model = _CapturingChatModel()
    artist = StoryboardArtist(chat_model=model)
    await artist.design_storyboard(script="剧本。", characters=[], retry_timeout=10, shot_budget=8)
    human = model.messages[1][1] if isinstance(model.messages[1], tuple) else str(model.messages[1])
    assert "at most 8" in human or "至多 8" in human or "8" in human
    assert "segment" not in human.lower(), "只传预算（无片段）时不得注入 segment 语义"


# ── Plan C Task 2：pipeline 顺序编排、缓存与结构化预算 ────────────────────

BATCH_SCRIPT = "\n\n".join(f"第{i}段情节推进。" for i in range(1, 13))


def _char(idx: int = 0) -> CharacterInScene:
    return CharacterInScene(idx=idx, identifier_in_scene=f"角色{idx}", is_visible=True, static_features="男性", dynamic_features="沉稳")


def _make_pipeline(tmp_path: Path, name: str = "wd") -> Script2VideoPipeline:
    return Script2VideoPipeline(chat_model=object(), image_generator=_AsyncMock(), video_generator=_AsyncMock(), working_dir=str(tmp_path / name))


class _BatchArtist:
    """可编排的 artist 桩：按批返回局部 idx 连续 storyboard；可指定失败批号。"""

    def __init__(self, shots_per_call: list[int] | None = None, fail_on_calls: set[int] | None = None):
        self.calls: list[dict] = []
        self._shots_per_call = shots_per_call or [7, 6, 6]
        self._fail_on = fail_on_calls or set()

    async def design_storyboard(self, script, characters, user_requirement=None, retry_timeout=300, **kwargs):
        n = len(self.calls)
        self.calls.append({"script": script, "characters": characters, "kwargs": kwargs})
        if n in self._fail_on:
            raise RuntimeError(f"batch {n} artist boom")
        cnt = self._shots_per_call[n] if n < len(self._shots_per_call) else 2
        return [ShotBriefDescription(idx=i, is_last=(i == cnt - 1), cam_idx=i % 3, visual_desc=f"v{n}-{i}", audio_desc="a") for i in range(cnt)]


@pytest.mark.asyncio
async def test_pipeline_b19_batches_share_characters_and_merge(tmp_path):
    """>12 结构化预算：分批调用共享同一角色表；合并后全局 idx/cam/is_last 正确；根文件与批次缓存落盘。"""
    pipe = _make_pipeline(tmp_path)
    artist = _BatchArtist([7, 6, 6])
    pipe.storyboard_artist = artist
    chars = [_char(0), _char(1)]

    merged = await pipe.design_storyboard(script=BATCH_SCRIPT, characters=chars, user_requirement="", global_shot_budget=19)

    assert len(artist.calls) == 3, f"19 预算应拆 3 批，实际 {len(artist.calls)}"
    assert all(c["characters"] is chars for c in artist.calls), "所有批必须共享同一角色表对象"
    assert [s.idx for s in merged] == list(range(19))
    assert [s.is_last for s in merged] == [False] * 18 + [True]
    root = Path(pipe.working_dir) / "storyboard.json"
    assert root.exists(), "根 storyboard.json 须落盘"
    batches_dir = Path(pipe.working_dir) / "storyboard_batches"
    assert batches_dir.exists() and not batches_dir.name.startswith("scene_"), "缓存目录不得命名 scene_*"
    assert (batches_dir / "manifest.json").exists()
    assert all((batches_dir / f"batch_{i}.json").exists() for i in range(3))


@pytest.mark.asyncio
async def test_ui_payload_238_06_duration_derives_budget_30_and_lands_four_batches(tmp_path):
    """Task 8 行为级验收：UI 真实 payload（total_duration=238.06，无 target/预算）
    → resolver 预算 30 → 真实 pipeline 落 4 个批次文件（每批 ≤8）。"""
    from app.gateway.video_gen.engines.vimax.storyboard_budget import resolve_vimax_budget

    resolved = resolve_vimax_budget(explicit_budget=None, target_duration=None, storyboard_duration=238.06)
    assert resolved is not None and resolved.shot_budget == 30 and resolved.source == "storyboard_duration"

    pipe = _make_pipeline(tmp_path, "ui238")
    artist = _BatchArtist([8, 8, 8, 6])
    pipe.storyboard_artist = artist
    merged = await pipe.design_storyboard(script=BATCH_SCRIPT, characters=[_char(0)], user_requirement="", global_shot_budget=resolved.shot_budget)

    batches_dir = Path(pipe.working_dir) / "storyboard_batches"
    assert len(artist.calls) == 4
    assert (batches_dir / "manifest.json").exists()
    assert all((batches_dir / f"batch_{i}.json").exists() for i in range(4))
    assert len(merged) == 30


@pytest.mark.asyncio
async def test_pipeline_batch_resume_only_missing_batches(tmp_path):
    """第二/三批失败 → 根文件不出现；重跑只补缺失批（成功批不重调用）。"""
    pipe = _make_pipeline(tmp_path)
    artist = _BatchArtist([7, 6, 6], fail_on_calls={1})
    pipe.storyboard_artist = artist
    chars = [_char(0)]
    with pytest.raises(RuntimeError, match="batch 1"):
        await pipe.design_storyboard(script=BATCH_SCRIPT, characters=chars, user_requirement="", global_shot_budget=19)
    assert not (Path(pipe.working_dir) / "storyboard.json").exists(), "全部成功前根文件不得出现"
    assert (Path(pipe.working_dir) / "storyboard_batches" / "batch_0.json").exists(), "成功批应已缓存"

    # 重跑批 1、2（批 0 已缓存 7 镜）：桩按调用序给数 → 6+6，合计 19
    artist2 = _BatchArtist([6, 6])
    pipe.storyboard_artist = artist2
    merged = await pipe.design_storyboard(script=BATCH_SCRIPT, characters=chars, user_requirement="", global_shot_budget=19)
    assert len(merged) == 19
    assert len(artist2.calls) == 2, f"只应重跑缺失两批，实际 {len(artist2.calls)}"
    assert artist2.calls[0]["kwargs"].get("target_script") not in (None, BATCH_SCRIPT), "重跑批走批次片段"


@pytest.mark.asyncio
async def test_pipeline_fingerprint_change_invalidates_cache(tmp_path):
    """输入（script）变化 → 旧批次缓存不复用，全部重跑。"""
    pipe = _make_pipeline(tmp_path)
    artist = _BatchArtist([7, 6, 6])
    pipe.storyboard_artist = artist
    await pipe.design_storyboard(script=BATCH_SCRIPT, characters=[_char(0)], user_requirement="", global_shot_budget=19)
    assert len(artist.calls) == 3

    artist2 = _BatchArtist([6, 6, 7])
    pipe.storyboard_artist = artist2
    # 模拟「未全部成功/重跑」场景：根文件不存在（根在场 = 既有直接复用语义，不在本批范围）
    (Path(pipe.working_dir) / "storyboard.json").unlink()
    await pipe.design_storyboard(script=BATCH_SCRIPT + "\n\n新增一段情节。", characters=[_char(0)], user_requirement="", global_shot_budget=19)
    assert len(artist2.calls) == 3, "指纹失配须全部重跑（不伪造缓存命中）"


@pytest.mark.asyncio
async def test_pipeline_no_budget_or_le12_single_call(tmp_path):
    """无结构化预算 / 预算 ≤12：单次调用（无 target_script/shot_budget kwargs）。"""
    pipe = _make_pipeline(tmp_path)
    artist = _BatchArtist([5])
    pipe.storyboard_artist = artist
    await pipe.design_storyboard(script=BATCH_SCRIPT, characters=[_char(0)], user_requirement="")
    assert len(artist.calls) == 1
    assert artist.calls[0]["kwargs"].get("target_script") is None, "单批路径不得带批次片段参数"
    assert artist.calls[0]["kwargs"].get("batches_reusable") is False

    pipe2 = _make_pipeline(tmp_path, "wd2")
    artist2 = _BatchArtist([12])
    pipe2.storyboard_artist = artist2
    await pipe2.design_storyboard(script=BATCH_SCRIPT, characters=[_char(0)], user_requirement="", global_shot_budget=12)
    assert len(artist2.calls) == 1
    assert artist2.calls[0]["kwargs"].get("target_script") is None
    assert artist2.calls[0]["kwargs"].get("batches_reusable") is False


@pytest.mark.asyncio
async def test_pipeline_batch_progress_events(tmp_path):
    """每批开始/完成发事件；meta 带 batch_idx/batch_total。"""
    pipe = _make_pipeline(tmp_path)
    pipe.storyboard_artist = _BatchArtist([7, 6, 6])
    events: list[tuple] = []

    def progress(stage, message, meta=None):
        events.append((stage, message, meta or {}))

    await pipe.design_storyboard(script=BATCH_SCRIPT, characters=[_char(0)], user_requirement="", progress=progress, global_shot_budget=19)
    batch_starts = [e for e in events if e[0] == "load_storyboard" and e[2].get("batch_total")]
    batch_readys = [e for e in events if e[0] == "storyboard_batch_ready"]
    assert len(batch_starts) == 3 and len(batch_readys) == 3
    assert [e[2].get("batch_idx") for e in batch_readys] == [0, 1, 2]
    assert all(e[2].get("batch_total") == 3 for e in batch_readys)


@pytest.mark.asyncio
async def test_engine_passes_budget_only_for_script2video(tmp_path):
    """engine：script2video 且结构化预算在场时传 global_shot_budget；idea2video 不传。"""

    from app.gateway.video_gen.engines.base import EngineContext
    from app.gateway.video_gen.engines.vimax.engine import VimaxEngine

    def _ctx(tmp_path, **overrides):
        base = dict(user_id="u", task_id="t", task_dir=tmp_path, voice_id="v", style="Cartoon", request={"script": "剧本"})
        base.update(overrides)
        return EngineContext(**base)

    calls: list[dict] = []
    pipeline = _AsyncMock()

    async def capture(**kw):
        calls.append(kw)
        return None

    pipeline.side_effect = capture
    engine = VimaxEngine(config_factory=lambda wd, req: {"working_dir": str(wd)}, pipeline_factory=lambda _: pipeline)
    await engine.render(_ctx(tmp_path, request={"script": "剧本", "vimax_global_shot_budget": 19}))
    assert calls and calls[0].get("global_shot_budget") == 19, "script2video 结构化预算须透传 pipeline"

    calls.clear()
    engine2 = VimaxEngine(config_factory=lambda wd, req: {"working_dir": str(wd), "pipeline": "idea2video"}, pipeline_factory=lambda _: pipeline)
    await engine2.render(_ctx(tmp_path, request={"pipeline": "idea2video", "idea": "一个想法", "vimax_global_shot_budget": 19}))
    assert calls and calls[0].get("global_shot_budget") is None, "idea2video 不得启用拆批"


# ── 推送前对抗评审修复（2026-10-19） ──────────────────────────────────────


@pytest.mark.asyncio
async def test_merge_failure_wipes_poison_batch_cache(tmp_path):
    """[critical] merge 校验失败（如总镜超预算）→ 批次缓存作废——重试重新生成，
    不得用毒缓存死循环。"""
    pipe = _make_pipeline(tmp_path, "poison")
    # 各批超产：7+8+8=23 > 19 → merge raise
    artist = _BatchArtist([7, 8, 8])
    pipe.storyboard_artist = artist
    with pytest.raises(ValueError, match="budget"):
        await pipe.design_storyboard(script=BATCH_SCRIPT, characters=[_char(0)], user_requirement="", global_shot_budget=19)
    batches_dir = Path(pipe.working_dir) / "storyboard_batches"
    assert not any(batches_dir.glob("batch_*.json")), "merge 失败后毒缓存必须被作废清理"
    # 重试：全部重新生成（而非缓存命中后原样再炸）
    artist2 = _BatchArtist([6, 6, 7])
    pipe.storyboard_artist = artist2
    merged = await pipe.design_storyboard(script=BATCH_SCRIPT, characters=[_char(0)], user_requirement="", global_shot_budget=19)
    assert len(merged) == 19
    assert len(artist2.calls) == 3, "缓存已作废，重试必须全量重跑"


@pytest.mark.asyncio
async def test_corrupt_cached_batch_reruns(tmp_path):
    """[minor 连带] 损坏的 batch_N.json 按缺失处理重跑，不永久失败。"""
    pipe = _make_pipeline(tmp_path, "corrupt")
    artist = _BatchArtist([7, 6, 6])
    pipe.storyboard_artist = artist
    await pipe.design_storyboard(script=BATCH_SCRIPT, characters=[_char(0)], user_requirement="", global_shot_budget=19)
    (Path(pipe.working_dir) / "storyboard_batches" / "batch_1.json").write_text("{ not json")
    (Path(pipe.working_dir) / "storyboard.json").unlink()
    # 只重跑损坏的批 1（原 6 镜）——桩按调用序给数
    artist2 = _BatchArtist([6, 6])
    pipe.storyboard_artist = artist2
    merged = await pipe.design_storyboard(script=BATCH_SCRIPT, characters=[_char(0)], user_requirement="", global_shot_budget=19)
    assert len(merged) == 19
    assert len(artist2.calls) == 1, "只有损坏批重跑，好批复用"


@pytest.mark.asyncio
async def test_plan_text_artifacts_callable_without_budget(tmp_path):
    """[important] plan_text_artifacts 公共入口无 global_shot_budget 参数——调用不得 NameError。"""
    pipe = _make_pipeline(tmp_path, "pta")
    artist = _BatchArtist([3])
    pipe.storyboard_artist = artist
    # 后续阶段（decompose/camera_tree）非本断言对象——整段桩掉
    pipe.decompose_visual_descriptions = _AsyncMock(return_value=[])
    pipe.construct_camera_tree = _AsyncMock(return_value={})
    await pipe.plan_text_artifacts(script="剧本一段。", characters=[_char(0)], user_requirement="", style="Cartoon")
    assert artist.calls and artist.calls[0]["kwargs"].get("target_script") is None, "该入口走原单批语义（无批次片段）"


def test_no_blank_batch_and_ascii_sentence_units():
    """[important] 纯空白单元并入前批不独占；ASCII 句号/单换行也是切分边界。"""
    # 空白段/尾随空白：不产生空白批，拼接不变式保持
    ws_script = "段一内容。\n\n\n\n段二内容。\n\n  "
    plans = plan_storyboard_batches(ws_script, global_shot_budget=19)
    assert all(p.text.strip() for p in plans), "不得产生空白批次"
    assert "".join(p.text for p in plans) == ws_script

    # 英文（ASCII 句号 + 单换行）：多单元 → budget 19 拆 >1 批；小数不被切断
    en = "First point here.\nSecond point follows.\nThird and final point costs 3.5 units."
    plans_en = plan_storyboard_batches(en, global_shot_budget=19)
    assert len(plans_en) > 1, f"ASCII 句号/单换行须产生多单元多批，实际 {len(plans_en)} 批"
    assert "".join(p.text for p in plans_en) == en
    for p in plans_en:
        assert not p.text.strip().startswith("5 "), "小数不得被切进下一批开头"


@pytest.mark.asyncio
async def test_engine_derives_budget_from_target_duration(tmp_path):
    """[important] UI 主路径（无结构化预算、携带 target_duration）→ engine 派生同式预算拆批。"""
    from app.gateway.video_gen.engines.base import EngineContext
    from app.gateway.video_gen.engines.vimax.engine import VimaxEngine

    def _ctx(td, **overrides):
        base = dict(user_id="u", task_id="t", task_dir=tmp_path, voice_id="v", style="Cartoon", request={"script": "剧本"})
        base.update(overrides)
        return EngineContext(**base)

    calls: list[dict] = []
    pipeline = _AsyncMock()
    pipeline.side_effect = lambda **kw: calls.append(kw) or None
    engine = VimaxEngine(config_factory=lambda wd, req: {"working_dir": str(wd)}, pipeline_factory=lambda _: pipeline)
    await engine.render(_ctx(None, request={"script": "剧本", "target_duration": 180}))
    assert calls and calls[0].get("global_shot_budget") == 24, "180s → 2 scene × 12 shot = 24（同 service 公式）"


# ── 边界收口 Task 1（P0）：中文句界不依赖空白 ──────────────────────────────


def test_chinese_no_space_single_paragraph_batches():
    """[P0] 中文单段无空格多句 + budget=24 → 多批且单批 ≤8、拼接==原文。

    修前：句末标点切分要求后随空白（为防切小数加的通用前瞻）——中文无空格
    书写 units=1 → 静默退化单批（PR35 拆批收益对中文长稿失效）。
    """
    zh = "这是一段没有任何空格与换行的中文长脚本。第二句继续推进剧情。第三句引入转折冲突。第四句展开细节描写。第五句推向高潮。第六句完成收尾。"
    plans = plan_storyboard_batches(zh, global_shot_budget=24)
    assert len(plans) >= 2, f"中文无空格多句须拆多批，实际 {len(plans)} 批"
    assert all(p.shot_budget <= STORYBOARD_BATCH_SHOT_CAP for p in plans)
    assert "".join(p.text for p in plans) == zh


def test_chinese_mixed_boundaries_and_contrast_matrix():
    """混合边界（全角/半角问叹、省略号、分号）都切；对照：空格版英文与既有矩阵不回归。"""
    zh_mixed = "半角!结尾？还有省略号……以及分号；最后一句。"
    plans = plan_storyboard_batches(zh_mixed, global_shot_budget=24)
    assert "".join(p.text for p in plans) == zh_mixed
    # 至少能产生多单元（否则退化单批）——短文本可能单批，这里用足够长的重复文本
    zh_long = "剧情推进一句。剧情推进两句。剧情推进三句。剧情推进四句。剧情推进五句。剧情推进六句。"
    assert len(plan_storyboard_batches(zh_long, global_shot_budget=24)) >= 2

    # 英文小数不误切（既有回归）
    en = "It costs 3.5 units. Next point. Final point."
    plans_en = plan_storyboard_batches(en, global_shot_budget=19)
    assert "".join(p.text for p in plans_en) == en
    for p in plans_en:
        assert not p.text.strip().startswith("5 "), "小数不得被切进下一批开头"


def test_shrink_batches_budgets_aligned():
    """[P2] 单元不足收缩后批数与预算对齐：每批 ≤8、总预算不超全局、无空批。"""
    # 2 单元 + budget 25（期望 4 批）→ 收缩到 2 批：budgets 必须同步收缩语义
    script2 = "第一单元内容。第二单元内容。"
    plans = plan_storyboard_batches(script2, global_shot_budget=25)
    assert 1 <= len(plans) <= 2
    assert all(p.shot_budget <= STORYBOARD_BATCH_SHOT_CAP for p in plans), f"收缩后单批仍须 ≤8：{[p.shot_budget for p in plans]}"
    assert sum(p.shot_budget for p in plans) <= 25
    assert all(p.text.strip() for p in plans)
    assert "".join(p.text for p in plans) == script2


@pytest.mark.asyncio
async def test_pipeline_wires_batches_reusable_by_batch_count(tmp_path):
    """[确认项] 生产接线钉死：多批调用 artist 收 batches_reusable=True；单批不传（False）。
    修前该接线零测试覆盖（变异实验证明两个方向的回归都静默绿）。"""
    pipe = _make_pipeline(tmp_path, "wires")
    artist = _BatchArtist([7, 6, 6])
    pipe.storyboard_artist = artist
    await pipe.design_storyboard(script=BATCH_SCRIPT, characters=[_char(0)], user_requirement="", global_shot_budget=19)
    assert len(artist.calls) == 3
    assert all(c["kwargs"].get("batches_reusable") is True for c in artist.calls), f"多批须 True：{[c['kwargs'].get('batches_reusable') for c in artist.calls]}"

    pipe1 = _make_pipeline(tmp_path, "wires1")
    artist1 = _BatchArtist([12])
    pipe1.storyboard_artist = artist1
    await pipe1.design_storyboard(script=BATCH_SCRIPT, characters=[_char(0)], user_requirement="", global_shot_budget=12)
    assert len(artist1.calls) == 1
    assert artist1.calls[0]["kwargs"].get("batches_reusable") is False, "单批（预算≤12）须 False（保留外层重试）"


# ── Task 1（2026-10-20）：engine 三层预算兜底 ──────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload,expected_budget",
    [
        ({"script": "剧本", "total_duration": 238.06}, 30),
        ({"script": "剧本", "total_duration": 34.54}, 5),
        ({"script": "剧本"}, None),
        ({"script": "剧本", "target_duration": 180, "total_duration": 238}, 24),
        ({"script": "剧本", "target_duration": 180, "total_duration": 238, "vimax_global_shot_budget": 19}, 19),
    ],
)
async def test_engine_resolves_budget_from_storyboard_total_duration(tmp_path, payload, expected_budget):
    """UI 真实 payload 只有 total_duration（238.06 / 34.54）时 engine 必须派生预算；
    两者都缺失不默认 180（global_shot_budget=None）；显式 > target > total。"""
    from app.gateway.video_gen.engines.base import EngineContext
    from app.gateway.video_gen.engines.vimax.engine import VimaxEngine

    calls: list[dict] = []
    pipeline = _AsyncMock()
    pipeline.side_effect = lambda **kw: calls.append(kw)
    engine = VimaxEngine(config_factory=lambda wd, req: {"working_dir": str(wd)}, pipeline_factory=lambda _: pipeline)
    ctx = EngineContext(user_id="u", task_id="t", task_dir=tmp_path, voice_id="v", style="Cartoon", request=payload)

    await engine.render(ctx)

    assert calls[-1].get("global_shot_budget") == expected_budget
