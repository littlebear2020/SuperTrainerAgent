"""Task 4: 画面短于旁白 → 写降级 sidecar，任务详情可见。"""

import json
from unittest.mock import MagicMock

import pytest

from app.gateway.video_gen import degradations as deg
from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame
from app.gateway.video_gen.visual_sources import ai_video as mod
from app.gateway.video_gen.visual_sources.ai_video import AIVideoVisualSource


def test_shortfall_kind_exists() -> None:
    assert hasattr(deg, "KIND_VISUAL_SHORTFALL")


def test_shortfall_recorded_with_frame_and_gap(tmp_path) -> None:
    deg.record_degradation(
        tmp_path,
        kind=deg.KIND_VISUAL_SHORTFALL,
        detail="frame 12: 画面 5.0s < 旁白 8.9s，尾部定格 3.9s",
        frame_index=12,
    )
    files = list(tmp_path.glob("*.json"))
    assert files, "应写出 sidecar 文件"
    payload = json.loads(files[0].read_text(encoding="utf-8"))
    blob = json.dumps(payload, ensure_ascii=False)
    assert "3.9" in blob and "12" in blob


def _sb(duration: float) -> Storyboard:
    cfg = StoryboardConfig(video_type="ai_video", width=720, height=1280)
    return Storyboard(title="t", config=cfg, frames=[StoryboardFrame(index=0, narration="n", duration=duration, image_prompt="p")])


def _wire(monkeypatch, probed: float) -> AIVideoVisualSource:
    async def fake_gen(prompt, *, duration=5.0, **kw):  # noqa: ANN001,ANN003
        return MagicMock(url="http://v/1")

    async def fake_localize(url, frame, task_dir):  # noqa: ANN001
        (task_dir / "visuals").mkdir(parents=True, exist_ok=True)
        (task_dir / "visuals" / "v0.mp4").write_bytes(b"x" * 2048)
        return "visuals/v0.mp4"

    monkeypatch.setattr(mod, "_localize_video", fake_localize)
    monkeypatch.setattr(mod, "_probe_video_seconds", lambda p: probed)
    provider = MagicMock(generate_video=fake_gen, max_video_duration=15.0, min_video_duration=4.0, integer_seconds_only=True)
    return AIVideoVisualSource(provider, concurrency=1)


@pytest.mark.asyncio
async def test_fill_records_shortfall_sidecar(tmp_path, monkeypatch) -> None:
    """8.9s 槽位只拿到 5.0s → sidecar 记 visual_shortfall，用户在任务详情看得到。"""
    src = _wire(monkeypatch, probed=5.0)
    await src.render(_sb(8.9), task_dir=tmp_path)
    items = deg.read_degradations(tmp_path)
    kinds = [i["kind"] for i in items]
    assert deg.KIND_VISUAL_SHORTFALL in kinds, f"应记 visual_shortfall，实际 {kinds}"
    entry = next(i for i in items if i["kind"] == deg.KIND_VISUAL_SHORTFALL)
    assert entry["frame_index"] == 0
    assert "3.9" in entry["detail"], f"detail 应含缺口秒数，实际 {entry['detail']}"


@pytest.mark.asyncio
async def test_subsecond_gap_not_recorded(tmp_path, monkeypatch) -> None:
    """亚秒缺口肉眼不可辨，记了只是噪音——不写 sidecar。"""
    src = _wire(monkeypatch, probed=8.7)
    await src.render(_sb(8.9), task_dir=tmp_path)
    kinds = [i["kind"] for i in deg.read_degradations(tmp_path)]
    assert deg.KIND_VISUAL_SHORTFALL not in kinds, f"0.2s 缺口不该记，实际 {kinds}"


@pytest.mark.asyncio
async def test_longer_visual_not_recorded(tmp_path, monkeypatch) -> None:
    """模型给得更长 → worker -t 截断，无缺口，不该告警。"""
    src = _wire(monkeypatch, probed=12.0)
    await src.render(_sb(8.9), task_dir=tmp_path)
    kinds = [i["kind"] for i in deg.read_degradations(tmp_path)]
    assert deg.KIND_VISUAL_SHORTFALL not in kinds


def test_router_has_chinese_label_for_shortfall(tmp_path) -> None:
    """详情接口的 kind→文案表是硬编码白名单，新 kind 必须登记，否则用户只看到
    兜底文案「出现降级处理（visual_shortfall）」——英文 key 泄漏给终端用户。"""
    from app.gateway.routers.video_gen import _DEGRADATION_LABELS, _attach_degradation_warning

    assert deg.KIND_VISUAL_SHORTFALL in _DEGRADATION_LABELS
    deg.record_degradation(
        tmp_path,
        kind=deg.KIND_VISUAL_SHORTFALL,
        detail="第 1 镜画面 5.0s 短于旁白 8.9s，尾部定格 3.9s",
        frame_index=0,
    )
    result: dict = {}
    _attach_degradation_warning(result, tmp_path, "ready")
    warning = result.get("degradation_warning", "")
    assert warning, "终态应上报降级告警"
    assert "visual_shortfall" not in warning, f"不得把英文 kind 泄漏给用户：{warning}"
    assert "帧 1" in warning
