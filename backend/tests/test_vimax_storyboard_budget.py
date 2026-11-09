"""边界收口 Task 6（P2）：预算公式 DRY——engine 与 service 同源。

derive_vimax_storyboard_budget(target_duration) -> (max_scenes,
max_shots_per_scene, global_shot_budget)，两入口共用（闭合 OCR「预算公式
双写漂移」finding）。公式 = 计划 C 既有口径：scenes=ceil(td/90)、
shots=max(3, ceil(td/scenes/8))、budget=scenes*shots。
"""

import pytest

from app.gateway.video_gen.engines.vimax.storyboard_budget import (
    ResolvedVimaxBudget,
    derive_vimax_storyboard_budget,
    resolve_vimax_budget,
)


@pytest.mark.parametrize(
    "td,expected",
    [
        (60, (1, 8, 8)),  # ceil(60/90)=1; max(3, ceil(60/8))=8
        (90, (1, 12, 12)),  # ceil(90/8)=11.25→12
        (120, (2, 8, 16)),  # scenes=2; ceil(120/2/8)=7.5→8
        (180, (2, 12, 24)),  # scenes=2; ceil(180/2/8)=11.25→12
    ],
)
def test_budget_formula_matrix(td, expected):
    assert derive_vimax_storyboard_budget(td) == expected


def test_invalid_target_raises():
    with pytest.raises(ValueError):
        derive_vimax_storyboard_budget(0)
    with pytest.raises(ValueError):
        derive_vimax_storyboard_budget(-5)


# ── Task 1（2026-10-20）：三层预算解析器 ────────────────────────────────────
#
# 已拍板：显式 vimax_global_shot_budget > target_duration > 草稿 storyboard
# total_duration；两者都缺失 / 全非法 → None（保持单批 + warning，不默认 180）。


def test_resolve_explicit_wins_over_target_and_storyboard():
    got = resolve_vimax_budget(explicit_budget=19, target_duration=238, storyboard_duration=238.06)
    assert got == ResolvedVimaxBudget(shot_budget=19, source="explicit", duration_seconds=None)


def test_resolve_target_duration_wins_over_storyboard_duration():
    got = resolve_vimax_budget(target_duration=238, storyboard_duration=34.54)
    assert got is not None
    assert got.shot_budget == 30
    assert got.source == "target_duration"
    assert got.duration_seconds == 238


def test_resolve_storyboard_duration_238_makes_30_shots():
    got = resolve_vimax_budget(storyboard_duration=238.06)
    assert got is not None
    assert got.shot_budget == 30
    assert got.source == "storyboard_duration"
    assert got.duration_seconds == pytest.approx(238.06)


def test_resolve_storyboard_duration_34_makes_5_shots():
    got = resolve_vimax_budget(storyboard_duration=34.54)
    assert got is not None
    assert got.shot_budget == 5
    assert got.source == "storyboard_duration"


def test_resolve_invalid_explicit_and_target_fall_through_to_storyboard():
    got = resolve_vimax_budget(explicit_budget=-1, target_duration=0, storyboard_duration=238.06)
    assert got is not None
    assert got.shot_budget == 30
    assert got.source == "storyboard_duration"


def test_resolve_non_numeric_values_are_ignored_not_raised():
    assert resolve_vimax_budget(explicit_budget="19", target_duration="180", storyboard_duration="238.06") is None
    assert resolve_vimax_budget(explicit_budget=None, target_duration=None, storyboard_duration=None) is None


def test_resolve_all_invalid_returns_none_without_180_default():
    assert resolve_vimax_budget(explicit_budget=-1, target_duration=0, storyboard_duration=-3) is None


@pytest.mark.asyncio
async def test_engine_and_service_derivation_consistent(tmp_path):
    """engine 派生（UI 路径 fallback）与 helper 同源——两入口不再各写一份公式。"""
    from unittest.mock import AsyncMock

    from app.gateway.video_gen.engines.base import EngineContext
    from app.gateway.video_gen.engines.vimax.engine import VimaxEngine

    calls: list[dict] = []
    pipeline = AsyncMock()
    pipeline.side_effect = lambda **kw: calls.append(kw)
    engine = VimaxEngine(config_factory=lambda wd, req: {"working_dir": str(wd)}, pipeline_factory=lambda _: pipeline)

    def _ctx(**overrides):
        base = dict(user_id="u", task_id="t", task_dir=tmp_path, voice_id="v", style="Cartoon", request={"script": "剧本"})
        base.update(overrides)
        return EngineContext(**base)

    for td in (60, 90, 120, 180):
        calls.clear()
        await engine.render(_ctx(request={"script": "剧本", "target_duration": td}))
        _, _, budget = derive_vimax_storyboard_budget(td)
        assert calls[-1].get("global_shot_budget") == budget, f"td={td} engine 派生须与 helper 一致"


@pytest.mark.asyncio
@pytest.mark.parametrize("td", [60, 90, 120, 180])
async def test_plan_storyboard_for_tool_uses_derive_helper(tmp_path, td):
    """边界热修复（2026-10-20 P2）：service 侧断言——plan_storyboard_for_tool 写入
    payload 的结构化预算必须与 derive_vimax_storyboard_budget 同源（闭合
    「engine/service 一致性测试只测 engine 半边」：若 service 侧另写公式漂移
    常数，本矩阵比对即红）。"""
    import json as _json
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.gateway.video_gen.config import VideoGenConfig
    from app.gateway.video_gen.providers.registry import ProviderRegistry
    from app.gateway.video_gen.service import VideoGenService

    cfg = VideoGenConfig(qihu_api_key="t", script_model="m")
    svc = VideoGenService(repo=MagicMock(), config=cfg, providers=ProviderRegistry(cfg), storage_dir=str(tmp_path), sandbox_provider=AsyncMock())
    svc.repo.create = AsyncMock()
    svc.repo.update = AsyncMock()
    refreshed = MagicMock()
    refreshed.status = "draft"
    refreshed.result = None
    svc.repo.get = AsyncMock(return_value=refreshed)
    with patch.object(svc, "_wrap_pipeline", new=AsyncMock()):
        await svc.plan_storyboard_for_tool(video_type="vimax", script="第一段剧本内容。第二段剧本内容。", target_duration=td)
    payload = _json.loads(svc.repo.create.call_args.kwargs["payload"])
    _, _, expected = derive_vimax_storyboard_budget(td)
    assert payload.get("vimax_global_shot_budget") == expected, f"td={td}：service 写入预算须与 derive helper 一致，实际 {payload.get('vimax_global_shot_budget')}"
