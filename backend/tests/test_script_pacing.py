"""Task 10: 按叙事位置分配字数带，制造节奏张弛。"""

import pytest

from app.gateway.video_gen.orchestration.script import word_bounds_for_position


def test_opening_is_shorter() -> None:
    """开场简短有力——不要一上来就长篇大论。"""
    lo, hi = word_bounds_for_position(0, 10, 40, 60)
    assert hi < 60


def test_ending_is_shorter() -> None:
    """结尾收束。"""
    lo, hi = word_bounds_for_position(9, 10, 40, 60)
    assert hi < 60


def test_middle_is_at_or_above_base() -> None:
    """主体段落承载信息量，可略长于基准。"""
    lo, hi = word_bounds_for_position(5, 10, 40, 60)
    assert hi >= 60


def test_bounds_stay_ordered_and_positive() -> None:
    for total in (1, 2, 3, 7, 20):
        for i in range(total):
            lo, hi = word_bounds_for_position(i, total, 40, 60)
            assert 0 < lo <= hi, f"i={i} total={total} 得到 ({lo}, {hi})"


def test_single_frame_uses_base() -> None:
    assert word_bounds_for_position(0, 1, 40, 60) == (40, 60)


@pytest.mark.parametrize("total", [2, 3, 4, 5, 6, 7, 10, 20])
def test_total_budget_conserved_at_every_scene_count(total: int) -> None:
    """总字数预算不得因缩放而显著漂移——否则成片总时长偏离用户设定的目标。

    2026-08-03 评审修：原测试只覆盖 n=10（+3.5%），漏了小镜数的严重偏短
    （n=2 −22.5%、n=3 −11.7%、n=4 −6.2%）。用户 UI 选 2 镜是可达路径
    （_resolve_n_scenes 直接返回显式 n_scenes），旁白会少近 1/4。
    """
    base_max = 60
    scaled = sum(word_bounds_for_position(i, total, 40, base_max)[1] for i in range(total))
    flat = base_max * total
    drift = abs(scaled - flat) / flat
    assert drift < 0.08, f"n={total} 总量漂移 {drift:.1%}：缩放后 {scaled} vs 基准 {flat}"


@pytest.mark.parametrize("total", [2, 3, 4])
def test_small_scene_counts_are_not_scaled(total: int) -> None:
    """n<5 一律用基准字数带——主体镜不足以摊销开场/结尾下探。"""
    for i in range(total):
        assert word_bounds_for_position(i, total, 40, 60) == (40, 60), f"n={total} i={i} 不应缩放"


def test_scaling_still_active_at_five_scenes() -> None:
    """门槛之上必须仍有张弛（别把修复做成"全面关闭"）。"""
    assert word_bounds_for_position(0, 5, 40, 60)[1] < 60, "开场应短"
    assert word_bounds_for_position(4, 5, 40, 60)[1] < 60, "结尾应短"
    assert word_bounds_for_position(2, 5, 40, 60)[1] >= 60, "主体应不短于基准"


def test_prompt_gives_per_scene_ranges() -> None:
    """旁白是**一次** LLM 调用产出全部镜，故字数带必须逐条写进提示词。

    只算不用 = 又一个静默失效的旋钮：word_bounds_for_position 存在但提示词里
    仍只有一个统一区间，LLM 无从得知第几镜该短。
    """
    import inspect

    from app.gateway.video_gen.orchestration import script as mod

    src = inspect.getsource(mod.generate_narrations)
    assert "word_bounds_for_position" in src, "generate_narrations 未使用逐镜字数带"


def test_per_scene_lines_reach_the_llm() -> None:
    """端到端：LLM 真的收到「第 N 镜：X-Y 字」这样的逐镜约束。"""
    import asyncio
    from types import SimpleNamespace

    from app.gateway.video_gen.orchestration import script as mod

    seen: dict = {}

    class _LLM:
        async def ainvoke(self, messages):  # noqa: ANN001
            seen["user"] = messages[-1]["content"]
            return SimpleNamespace(content="\n".join(f"第{i}句旁白内容" for i in range(1, 6)))

    asyncio.run(mod.generate_narrations(_LLM(), topic="测试主题", n_scenes=5, target_duration=60.0))
    user = seen["user"]
    assert "第 1 镜" in user, f"提示词应含逐镜字数约束，实际：{user}"
    assert "第 5 镜" in user
