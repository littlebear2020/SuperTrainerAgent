"""T7（2026-08-14 计划）：字幕 LLM 重切循环总量预算 300s。

08-13 字幕批次（bcad4f2c6）在 TTS 阶段（progress 10→60 窗口）新增逐帧**串行** LLM 重切
（subtitle_llm_enabled 默认 True）。单次有界（_invoke_llm_bounded 120s），但 125 帧串行 ×
全超时最坏 = 4+ 小时无进展——与任务卡死同一观感的新放大器。修复：循环加总量预算
（time.monotonic()），到点停止，剩余帧不写入结果 dict → 规则链兜底（既有语义）。
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import patch

import pytest

from app.gateway.video_gen.config import VideoGenConfig
from app.gateway.video_gen.service import _precompute_llm_chunks
from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame


def _make_storyboard(n: int = 5) -> Storyboard:
    cfg = StoryboardConfig(video_type="ai_image", width=1080, height=1920)
    return Storyboard(
        title="t",
        config=cfg,
        frames=[StoryboardFrame(index=i, narration=f"旁白内容第{i}句") for i in range(n)],
    )


@pytest.mark.asyncio
async def test_budget_stops_loop_and_keeps_completed_frames(tmp_path, monkeypatch):
    """前 2 帧快速完成、其余 hang → 预算耗尽后停止，已完成帧保留、未处理帧走规则链（不在结果中）。"""
    from app.gateway.video_gen import service as svc_mod

    monkeypatch.setattr(svc_mod, "SUBTITLE_LLM_TOTAL_BUDGET", 0.2)

    async def _fake_resegment(narration, *, max_width, llm, cache_path):
        idx = int(narration[-2])  # 旁白内容第N句
        if idx < 2:
            return [f"快{idx}"]
        await asyncio.sleep(1)  # 在途/未处理帧 hang（有界，避免测试挂死）
        return [f"慢{idx}"]

    with (
        patch("deerflow.models.create_chat_model", return_value=object()),
        patch("app.gateway.video_gen.subtitle_engine.llm_resegment", side_effect=_fake_resegment),
    ):
        start = time.monotonic()
        result = await _precompute_llm_chunks(_make_storyboard(), VideoGenConfig(), tmp_path)
        elapsed = time.monotonic() - start

    assert elapsed < 3, f"总量预算（测试 0.2s）+ 一次在途帧后必须返回，实际 {elapsed:.1f}s"
    assert result[0] == ["快0"], "已完成帧的成果必须保留"
    assert result[1] == ["快1"], "已完成帧的成果必须保留"
    assert 3 not in result, "预算耗尽后未处理帧不得出现在结果中（→ 规则链兜底）"
    assert 4 not in result


@pytest.mark.asyncio
async def test_budget_returns_empty_when_all_frames_hang(tmp_path, monkeypatch):
    """LLM 全面 hang → 烧掉一次在途帧后即停，结果为空（全片规则链兜底），不阻塞 TTS 窗口。"""
    from app.gateway.video_gen import service as svc_mod

    monkeypatch.setattr(svc_mod, "SUBTITLE_LLM_TOTAL_BUDGET", 0.2)

    async def _fake_resegment(narration, *, max_width, llm, cache_path):
        await asyncio.sleep(1)
        return ["x"]

    with (
        patch("deerflow.models.create_chat_model", return_value=object()),
        patch("app.gateway.video_gen.subtitle_engine.llm_resegment", side_effect=_fake_resegment),
    ):
        start = time.monotonic()
        result = await _precompute_llm_chunks(_make_storyboard(), VideoGenConfig(), tmp_path)
        elapsed = time.monotonic() - start

    assert elapsed < 3, f"全面 hang 时最多烧 300s(测试 0.2s)+一次在途(1s)，实际 {elapsed:.1f}s"
    # 在途那次完成的帧保留成果（与 T3 增量收集同语义）；未处理的帧不在结果中 → 规则链兜底
    assert result == {0: ["x"]}


@pytest.mark.asyncio
async def test_no_budget_when_disabled(tmp_path):
    """subtitle_llm_enabled=False 时零 LLM 调用（既有语义不变）。"""
    calls = []

    async def _fake_resegment(narration, *, max_width, llm, cache_path):
        calls.append(narration)
        return ["x"]

    with (
        patch("deerflow.models.create_chat_model", return_value=object()),
        patch("app.gateway.video_gen.subtitle_engine.llm_resegment", side_effect=_fake_resegment),
    ):
        result = await _precompute_llm_chunks(_make_storyboard(), VideoGenConfig(subtitle_llm_enabled=False), tmp_path)
    assert result == {}
    assert calls == []


def test_budget_constant_default_300():
    """总量预算默认 300s：LLM 健康（每帧 <2s）时覆盖 150+ 帧；全面 hang 时最多烧 300s+一次在途 120s。"""
    from app.gateway.video_gen.service import SUBTITLE_LLM_TOTAL_BUDGET

    assert SUBTITLE_LLM_TOTAL_BUDGET == 300.0


def test_subtitle_llm_model_default_is_qwen38_flash():
    """2026-10-12 花名册：deepseek-v4-flash-vision-exp 删册，字幕语义重切默认迁 qwen3.8-flash。

    历史沿革：gemini-3.5-flash 撤册（2026-09-08，gemini 系 360 通道双模态停顿失格）
    → deepseek-v4-flash-vision-exp（09-08 迁入）→ qwen3.8-flash（2026-10-12，09-08
    实测备选位 + 压测默认位胜者，docs/2026-10-12-flash-trio-benchmark.md）。若默认值
    回归成已撤册模型，_precompute_llm_chunks 会在 create_chat_model 抛 Model not
    found 时记一条 warning 后返回空 dict——字幕语义重切静默退回规则链，任务照常完成
    但质量无声降级。本测试锁死默认值，作为该回归的第一道防线。
    """
    cfg = VideoGenConfig()
    assert cfg.subtitle_llm_enabled is True
    assert cfg.subtitle_llm_model == "qwen3.8-flash"
    # 已撤册模型绝不允许回来（gemini-3.6-flash 2026-08-27 下架；gemini-3.5-flash /
    # deepseek-v4-flash-vision-exp 2026-10-12 删册）
    assert cfg.subtitle_llm_model != "gemini-3.6-flash"
    assert cfg.subtitle_llm_model != "gemini-3.5-flash"
    assert cfg.subtitle_llm_model != "deepseek-v4-flash-vision-exp"


# ── 条目 7②（2026-09-12 缺陷批）：字幕模型不可用降级留痕 ────────────────────


@pytest.mark.asyncio
async def test_subtitle_llm_unavailable_records_degradation(tmp_path, monkeypatch):
    """字幕模型撤册/构造失败 → degradation 留痕（subtitle_llm_fallback）+ 规则链
    兜底（chunks={}，任务继续）——修前只有 warning log，对用户零可见。"""
    import json

    cfg = VideoGenConfig(subtitle_llm_enabled=True, subtitle_llm_model="ghost-model")

    def _boom(name, **kwargs):  # noqa: ANN001, ANN202
        raise ValueError(f"Model {name} not found in config")

    # 污染规避（全量套件）：test_subagent_executor 的 session 级 MagicMock 会顶替
    # sys.modules['deerflow.models']（父包属性指向真实模块、sys.modules 指向 mock，
    # 两者分叉）——字符串 patch / `import as` 都落在真实模块上，而 service 局部
    # from-import 读 sys.modules 拿 mock（auto-attr 不炸）→ degradation 永不触发。
    # 直接对 sys.modules 当前条目 setattr，与 service 消费同一对象，两态一致。
    import sys as _sys

    monkeypatch.setattr(_sys.modules["deerflow.models"], "create_chat_model", _boom, raising=False)
    sb = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_video", width=1920, height=1080),
        frames=[StoryboardFrame(index=0, narration="旁白。")],
    )
    chunks = await _precompute_llm_chunks(sb, cfg, tmp_path)
    assert chunks == {}  # 规则链兜底
    deg = json.loads((tmp_path / "degradations.json").read_text(encoding="utf-8"))
    assert any(i["kind"] == "subtitle_llm_fallback" and "ghost-model" in i["detail"] for i in deg["items"])
