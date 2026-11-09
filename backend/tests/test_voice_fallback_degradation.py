"""决策 3：TTS 音色降级必须留痕（用户听得出中途换声，界面却零解释）。

背景：豆包配额耗尽（45000292）/ 并发限流 / 空音频等瞬时错误发生时，那一帧会
**悄悄换成 360 的备用音色**继续生成。成片状态「已完成」，用户点开一听——前半段
是 A 的声音，某一句突然变成 B，然后又变回 A，而界面上找不到任何说明。

产品决策（2026-07-31）：**不改降级语义**（配额耗尽不是故障，为它把已生成的 95 帧
全丢掉太重；且整批走 asyncio.gather，单帧 raise 会拖垮全批），但**必须补留痕**——
接进 Task 23 已建好的 degradations sidecar，让任务详情显示「第 N 帧音色被降级」。

留痕点选在 service 层而非 tts 层：service 本就在比对
``r.actual_voice_id != voice_id``（P2-14 的 CAS 归位逻辑），且 task_dir 在作用域内。
这样无需给 synthesize() 加 task_dir 参数穿三层，且能覆盖**所有**降级成因
（配额 / 传输错误 / 5xx / access key 缺失），不只配额那一条。
"""

from __future__ import annotations

from pathlib import Path

from app.gateway.video_gen.degradations import (
    KIND_VOICE_FALLBACK,
    read_degradations,
    record_degradation,
)


def test_voice_fallback_kind_exists() -> None:
    """音色降级须有独立 kind，便于前端给出针对性文案。"""
    assert KIND_VOICE_FALLBACK == "voice_fallback"


def test_records_requested_and_actual_voice(tmp_path: Path) -> None:
    """留痕内容要能回答「哪一帧、本该什么音色、实际什么音色」。"""
    record_degradation(
        tmp_path,
        kind=KIND_VOICE_FALLBACK,
        detail="第 3 帧音色降级：请求 doubao:spk1，实际 preset:146",
        frame_index=2,
    )

    items = read_degradations(tmp_path)
    assert len(items) == 1
    assert items[0]["kind"] == "voice_fallback"
    assert items[0]["frame_index"] == 2
    assert "doubao:spk1" in items[0]["detail"]
    assert "preset:146" in items[0]["detail"]


def test_frontend_label_covers_voice_fallback() -> None:
    """前端文案表必须认识这个 kind，否则用户只看到兜底的「出现降级处理」。"""
    from app.gateway.routers.video_gen import _DEGRADATION_LABELS

    assert KIND_VOICE_FALLBACK in _DEGRADATION_LABELS
    label = _DEGRADATION_LABELS[KIND_VOICE_FALLBACK]
    assert "音色" in label, f"文案应点明是音色问题：{label}"


def test_warning_surfaces_in_task_detail(tmp_path: Path) -> None:
    """端到端：sidecar 有音色降级记录 → 任务详情出现中文告警。"""
    from app.gateway.routers.video_gen import _attach_degradation_warning

    record_degradation(
        tmp_path,
        kind=KIND_VOICE_FALLBACK,
        detail="请求 doubao:spk1，实际 preset:146",
        frame_index=4,
    )
    result: dict = {}

    _attach_degradation_warning(result, tmp_path, "ready")

    warning = result.get("degradation_warning") or ""
    assert "音色" in warning, f"用户须能在详情页看到音色降级说明：{warning!r}"
    assert "帧 5" in warning, "应指明是哪一帧（1-based），便于用户重生成该帧"
