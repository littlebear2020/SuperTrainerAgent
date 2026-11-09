"""Task 13 (E1 + E6): ai_image / ai_video 成本闸。

核实确认严重不对称——ViMax 有 ``vimax_max_image_gen=160`` /
``vimax_max_video_gen=40`` / ``vimax_budget_cny=200`` 三重闸并真实拦截；
**ai_image / ai_video 零拦截**。

配合 schema 上限量化最坏情况：``n_scenes ≤ 200``（schemas.py）×
``video_count ≤ 5`` = **单次请求最多 1000 次付费生成，无任何阻拦**。

修法：给 ai_image / ai_video 加与 ViMax 同构的调用数 + 预算闸。已生成的帧保留
（不回滚），让用户至少拿到部分成果并能看到明确的超预算错误。

注：``video_count`` 的差异化 seed / 收窄上限属**产品决策**（附录 B #1），本 Task
不动——见 commit message。
"""

import pytest

from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame
from app.gateway.video_gen.visual_sources.budget import (
    VisualBudget,
    VisualBudgetExceeded,
)


def _make_storyboard(n, video_type="ai_image"):
    cfg = StoryboardConfig(video_type=video_type, width=720, height=1280)
    frames = [StoryboardFrame(index=i, narration=f"n{i}", duration=3.0, image_prompt=f"p{i}") for i in range(n)]
    return Storyboard(title="t", config=cfg, frames=frames)


# ── 预算对象本身 ───────────────────────────────────────────────────────────


def test_budget_allows_calls_under_cap():
    b = VisualBudget(max_calls=3, max_cost_cny=100.0, unit_cost_cny=1.0)
    b.charge()
    b.charge()
    assert b.call_count == 2


def test_budget_blocks_on_call_cap():
    b = VisualBudget(max_calls=2, max_cost_cny=None, unit_cost_cny=1.0)
    b.charge()
    b.charge()
    with pytest.raises(VisualBudgetExceeded, match="calls"):
        b.charge()


def test_budget_blocks_on_cost_cap():
    b = VisualBudget(max_calls=None, max_cost_cny=2.5, unit_cost_cny=1.0)
    b.charge()
    b.charge()
    with pytest.raises(VisualBudgetExceeded, match="cost|¥"):
        b.charge()  # 3.0 > 2.5


def test_budget_uncapped_when_none():
    b = VisualBudget(max_calls=None, max_cost_cny=None, unit_cost_cny=1.0)
    for _ in range(50):
        b.charge()
    assert b.call_count == 50


def test_budget_tracks_spend():
    b = VisualBudget(max_calls=None, max_cost_cny=None, unit_cost_cny=0.3)
    b.charge()
    b.charge()
    assert b.cost_cny == pytest.approx(0.6)


# ── 配置项 ────────────────────────────────────────────────────────────────


def test_config_exposes_ai_visual_budget_knobs():
    """必须有与 ViMax 对称的配置项。"""
    from app.gateway.video_gen.config import VideoGenConfig

    cfg = VideoGenConfig()
    for field in ("ai_image_max_gen", "ai_video_max_gen", "ai_visual_budget_cny"):
        assert hasattr(cfg, field), f"config 缺 {field} —— ai_image/ai_video 仍零成本闸"


# ── 接线：超预算时抛错且保留已生成帧 ────────────────────────────────────────


@pytest.mark.asyncio
async def test_ai_image_stops_at_budget_and_keeps_done_frames(tmp_path):
    """10 帧、上限 3 次 → 抛 VisualBudgetExceeded，已生成帧保留。"""
    from unittest.mock import MagicMock

    from app.gateway.video_gen.visual_sources.ai_image import AIImageVisualSource

    async def fake_gen(prompt, **kw):
        return MagicMock(url=f"http://img/{prompt}", b64_json=None)

    provider = MagicMock()
    provider.generate_image = fake_gen

    sb = _make_storyboard(10)
    src = AIImageVisualSource(provider, concurrency=1, budget=VisualBudget(max_calls=3, unit_cost_cny=0.3))

    with pytest.raises(VisualBudgetExceeded):
        await src.render(sb, task_dir=tmp_path)

    done = [f for f in sb.frames if f.visual_url]
    assert done, "超预算后已生成的帧被丢弃了——应保留部分成果"
    assert len(done) <= 3, f"闸没生效，生成了 {len(done)} 帧"


@pytest.mark.asyncio
async def test_ai_video_stops_at_budget(tmp_path):
    """ai_video 同样受闸。"""
    from unittest.mock import MagicMock

    from app.gateway.video_gen.visual_sources.ai_video import AIVideoVisualSource

    async def fake_gen(prompt, *, duration, **kw):
        return MagicMock(url=f"http://v/{prompt}")

    provider = MagicMock()
    provider.generate_video = fake_gen
    provider.max_video_duration = 8.0

    sb = _make_storyboard(10, video_type="ai_video")
    src = AIVideoVisualSource(provider, concurrency=1, budget=VisualBudget(max_calls=2, unit_cost_cny=1.5))

    with pytest.raises(VisualBudgetExceeded):
        await src.render(sb, task_dir=tmp_path)


@pytest.mark.asyncio
async def test_no_budget_means_no_gate(tmp_path):
    """未给预算时行为不变（向后兼容）。"""
    from unittest.mock import MagicMock

    from app.gateway.video_gen.visual_sources.ai_image import AIImageVisualSource

    async def fake_gen(prompt, **kw):
        return MagicMock(url=f"http://img/{prompt}", b64_json=None)

    provider = MagicMock()
    provider.generate_image = fake_gen

    sb = _make_storyboard(6)
    src = AIImageVisualSource(provider, concurrency=1)
    await src.render(sb, task_dir=tmp_path)

    assert all(f.visual_url for f in sb.frames)


def test_service_wires_budget_into_all_visual_sources():
    """结构断言：service 的每个 AIImage/AIVideo 构造点都必须传 budget。

    漏一处 → 该路径（如 mixed 任务、单帧重生）仍零成本闸，而配置项看着已生效。
    """
    from pathlib import Path as _P

    from app.gateway.video_gen import service as svc_mod

    src = _P(svc_mod.__file__).read_text(encoding="utf-8")
    for ctor in ("AIImageVisualSource(", "AIVideoVisualSource("):
        for line in src.splitlines():
            if ctor in line and "import" not in line:
                assert "budget=" in line, f"构造点未传 budget：{line.strip()}"


def test_budget_factory_matches_bridge_unit_costs():
    """单价须与 provider_bridge 的 ViMax 单价一致，否则两条管线的 ¥ 预算不可比。"""
    from app.gateway.video_gen.engines.vimax import provider_bridge as pb

    assert pb._IMAGE_UNIT_COST_CNY == 0.3
    assert pb._VIDEO_UNIT_COST_CNY == 1.5


def test_ai_image_max_gen_default_covers_full_retry_of_200_frames():
    """2026-07-31 产品决策（审查发现 9）：重试计费 + 上限留余量。

    重试是真实付费调用（成本闸必须诚实），但 200 帧 × 2 次尝试 = 400 恰好顶死
    旧默认值，「200 帧长任务仍可完成」的设计意图数学上不成立。新默认必须
    > 400，给单帧重生 / recompose 的跨 render 累积留余量。
    """
    from app.gateway.video_gen.config import VideoGenConfig

    cfg = VideoGenConfig()
    worst_case_full_retry = 200 * 2  # n_scenes 上限 × 每帧最多 2 次尝试
    assert cfg.ai_image_max_gen > worst_case_full_retry, f"ai_image_max_gen={cfg.ai_image_max_gen} 顶死 200 帧全重试（{worst_case_full_retry}），与设计意图矛盾"


# ── T6（2026-08-18 拍板③）：T-S2 组锚开启前的 VisualBudget 成本对账 ────────


def test_default_video_budget_accommodates_group_anchor():
    """对账结论（计划 T6 第 4 条 + A13 修订 2026-08-27 成本可信化批次 v4.2）：

    T-S2 **零增量付费调用**——组首帧是本就要渲染的帧（一次调用），锚图抽取是
    本地 ffmpeg（零成本），组员带锚渲染仍是每帧一次调用（锚作 image_urls 附在
    同一次调用里，本仓定价口径按 (provider, "video") 计价、i2v/r2v 与 t2v 同价）。
    A13 用户拍板后金额闸**默认关闭**（ai_visual_budget_cny=None，估不准的指标
    不作为拦截标准），调用数闸 240 成为唯一硬顶——对账对象从「金额容纳调用数」
    变为「调用数闸在位」，T-S2 的零增量结论不受影响。运营显式启用金额闸时，
    若把 video 单价抬过 cap/240（参考线 ¥1.67 = 400/240），金额闸先顶——那是
    既有语义（超限走跳过/上浮），与 T-S2 无关。
    """
    from app.gateway.video_gen.config import VideoGenConfig

    cfg = VideoGenConfig()
    assert cfg.ai_visual_budget_cny is None, "A13：金额闸默认关闭——若要重开默认额度，须重对账 T-S2（调用数闸先于金额闸生效）"
    assert cfg.ai_video_max_gen > 0, "次数闸为唯一硬顶（A13），必须默认在位"


@pytest.mark.asyncio
async def test_group_anchor_over_budget_keeps_existing_semantics(monkeypatch, tmp_path):
    """「超限走既有跳过语义」验证用例：T-S2 默认开 + 预算耗尽 → 与修前完全一致——
    E6 预算闸穿透占位兜底向上抛（整批停止），组首帧（耗尽前完成）成果保留。"""
    from unittest.mock import MagicMock

    from app.gateway.video_gen.storyboard import SceneGroup
    from app.gateway.video_gen.visual_sources.ai_video import AIVideoVisualSource

    monkeypatch.delenv("VIDEO_GEN_GROUP_ANCHOR", raising=False)

    async def fake_localize(url, frame, td, dest_name=None):  # noqa: ANN001
        rel = f"visuals/shot_{frame.index:04d}.mp4"
        f = td / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"mp4")
        return rel

    def fake_extract(video_path, jpg_path):  # noqa: ANN001
        jpg_path.parent.mkdir(parents=True, exist_ok=True)
        jpg_path.write_bytes(b"jpg")
        return True

    import app.gateway.video_gen.visual_sources.ai_video as aiv

    monkeypatch.setattr(aiv, "_localize_video", fake_localize)
    monkeypatch.setattr(aiv, "_probe_video_seconds", lambda p: 4.0)
    monkeypatch.setattr(aiv, "_extract_anchor_image", fake_extract)

    provider = MagicMock()
    provider.supports_image_urls = True
    provider.max_video_duration = 8.0

    async def fake_gen(prompt, *, duration=4.0, **kw):  # noqa: ANN003
        return MagicMock(url=f"http://v/{prompt}")

    provider.generate_video = fake_gen

    cfg = StoryboardConfig(video_type="ai_video", width=1920, height=1080)
    frames = [StoryboardFrame(index=i, narration=f"n{i}", duration=4.0, image_prompt=f"p{i}") for i in range(2)]
    sb = Storyboard(title="t", config=cfg, frames=frames, scene_groups=[SceneGroup(index=0, title="g", frame_indices=[0, 1])])
    src = AIVideoVisualSource(provider, concurrency=1, provider_name="minimax-h3", budget=VisualBudget(max_calls=1, unit_cost_cny=1.5))

    with pytest.raises(VisualBudgetExceeded):
        await src.render(sb, task_dir=tmp_path)
    assert sb.frames[0].visual_url, "耗尽前完成的组首帧成果保留（既有语义）"
