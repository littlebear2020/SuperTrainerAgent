"""T6 (Lane 2, 2026-10-15): vimax 静态降级透出——跨路契约的消费方。

契约（plan「跨路契约」节，Lane 1 实现 vendored 侧）：vendored 管线发
``video_clip_static_fallback`` 进度事件，payload 至少含 ``{"shot_idx": int,
"reason": str}``；本路在 engine 进度包装处消费 → ``record_degradation`` 落
``degradations.json`` sidecar（`_attach_degradation_warning` 既有透出链读它，
终态详情可见）+ engine_metadata 计数「N 镜静态占位」。

修前缺口：vendored 静态降级只发瞬时进度事件，终态 result 无任何留痕——
用户拿到「N 镜是静态占位图」的成片却与完美渲染不可区分。
"""

from __future__ import annotations

import json

import pytest

from app.gateway.video_gen.engines.base import EngineContext
from app.gateway.video_gen.engines.vimax.engine import VimaxEngine


class _FakeAsyncProgress:
    """ctx.progress 替身：吃 percent + set_result（fire-and-forget 语义）。"""

    async def __call__(self, *, progress=None, **kw):
        pass

    async def set_result(self, **kw):
        pass


def _make_engine(pipeline):
    return VimaxEngine(config_factory=lambda wd, req: {"working_dir": str(wd)}, pipeline_factory=lambda _: pipeline)


async def _render_with_events(tmp_path, events: list[tuple[str, str, dict]]):
    """跑一次 script2video render，fake pipeline 收到 progress 回调后重放 events。"""

    async def pipeline(*args, **kwargs):
        progress = kwargs.get("progress")
        if progress is not None:
            for stage, message, meta in events:
                progress(stage, message, meta)
        wd = tmp_path / "vimax_working"
        wd.mkdir(parents=True, exist_ok=True)
        (wd / "final_video.mp4").write_bytes(b"V")
        return None

    engine = _make_engine(pipeline)
    return await engine.render(
        EngineContext(
            user_id="u",
            task_id="t",
            task_dir=tmp_path,
            request={"script": "s"},
            progress=_FakeAsyncProgress(),
        )
    )


def _read_sidecar(tmp_path) -> list[dict]:
    p = tmp_path / "degradations.json"
    if not p.exists():
        return []
    return json.loads(p.read_text(encoding="utf-8")).get("items", [])


@pytest.mark.asyncio
async def test_static_fallback_event_records_degradation(tmp_path):
    """契约消费：video_clip_static_fallback 事件 → degradations.json 留痕 + 计数进 result。"""
    outcome = await _render_with_events(
        tmp_path,
        [("video_clip_static_fallback", "Video clip for shot 3 fell back to static frame", {"shot_idx": 3, "reason": "happyhorse latent download failed"})],
    )

    items = _read_sidecar(tmp_path)
    matched = [i for i in items if i.get("kind") == "video_clip_static_fallback"]
    assert len(matched) == 1, f"须落一条静态降级记录，实际 {items}"
    assert matched[0].get("frame_index") == 3
    assert "latent download failed" in matched[0].get("detail", ""), f"detail 须含 reason，实际 {matched[0]}"
    assert "静态" in matched[0].get("detail", "")
    # 终态 result 可见计数（engine_metadata → service 落 result 通道）
    assert outcome.engine_metadata.get("vimax_static_fallback_shots") == 1


@pytest.mark.asyncio
async def test_static_fallback_dedupes_repeat_events_for_same_shot(tmp_path):
    """同镜重复事件（重试路径）不刷屏：sidecar 一条、计数按镜去重。"""
    events = [
        ("video_clip_static_fallback", "m", {"shot_idx": 2, "reason": "timeout 1"}),
        ("video_clip_static_fallback", "m", {"shot_idx": 2, "reason": "timeout 2"}),
        ("video_clip_static_fallback", "m", {"shot_idx": 5, "reason": "timeout 3"}),
    ]
    outcome = await _render_with_events(tmp_path, events)

    matched = [i for i in _read_sidecar(tmp_path) if i.get("kind") == "video_clip_static_fallback"]
    assert len(matched) == 2, f"同镜去重（镜 2 一条 + 镜 5 一条），实际 {matched}"
    assert outcome.engine_metadata.get("vimax_static_fallback_shots") == 2


@pytest.mark.asyncio
async def test_interim_payload_error_key_still_consumed(tmp_path):
    """Lane 1 落地前的过渡形态（payload 键 error）也须消费——契约不得因旧形态碎。"""
    outcome = await _render_with_events(tmp_path, [("video_clip_static_fallback", "m", {"shot_idx": 1, "error": "QihuServerError 500"})])

    matched = [i for i in _read_sidecar(tmp_path) if i.get("kind") == "video_clip_static_fallback"]
    assert len(matched) == 1
    assert "QihuServerError 500" in matched[0]["detail"]
    assert outcome.engine_metadata.get("vimax_static_fallback_shots") == 1


@pytest.mark.asyncio
async def test_no_fallback_no_count_key(tmp_path):
    """无降级事件 → result 不携带计数键（不污染正常任务的 result）。"""
    outcome = await _render_with_events(tmp_path, [("concat_done", "m", {"path": "/tmp/x"})])

    assert "vimax_static_fallback_shots" not in (outcome.engine_metadata or {})
    assert _read_sidecar(tmp_path) == []


# ── T3（2026-10-16 收口批）：artifact 区分 + idea2video 多场景 gid 去重 ───────


@pytest.mark.asyncio
async def test_artifact_field_distinguishes_frame_and_video_counts(tmp_path):
    """artifact 字段区分帧级/视频级：sidecar 两种 kind、计数两个键。"""
    outcome = await _render_with_events(
        tmp_path,
        [
            ("video_clip_static_fallback", "m", {"shot_idx": 3, "reason": "seedream 429", "artifact": "first_frame"}),
            ("video_clip_static_fallback", "m", {"shot_idx": 3, "reason": "first frame degraded", "artifact": "video_clip"}),
        ],
    )

    items = _read_sidecar(tmp_path)
    video_kinds = [i for i in items if i.get("kind") == "video_clip_static_fallback"]
    frame_kinds = [i for i in items if i.get("kind") == "first_frame_static_fallback"]
    assert len(video_kinds) == 1 and len(frame_kinds) == 1, f"帧级/视频级各一条，实际 {items}"
    assert "seedream 429" in frame_kinds[0]["detail"]
    assert outcome.engine_metadata.get("vimax_static_fallback_shots") == 1
    assert outcome.engine_metadata.get("vimax_static_fallback_frames") == 1


@pytest.mark.asyncio
async def test_idea2video_scene_local_idx_collision_resolved_by_gid(tmp_path):
    """多场景 scene 内局部 shot_idx 跨场景碰撞 → 全局 gid 去重（计数不偏小）。"""
    outcome = await _render_with_events(
        tmp_path,
        [
            ("video_clip_static_fallback", "m", {"shot_idx": 1, "reason": "r-a", "scene_idx": 0, "scene_total": 2, "artifact": "video_clip"}),
            ("video_clip_static_fallback", "m", {"shot_idx": 1, "reason": "r-b", "scene_idx": 1, "scene_total": 2, "artifact": "video_clip"}),
            ("video_clip_static_fallback", "m", {"shot_idx": 1, "reason": "r-b-retry", "scene_idx": 1, "scene_total": 2, "artifact": "video_clip"}),
        ],
    )

    matched = [i for i in _read_sidecar(tmp_path) if i.get("kind") == "video_clip_static_fallback"]
    assert len(matched) == 2, f"scene0 镜1 + scene1 镜1 = 两条全局记录，实际 {matched}"
    assert outcome.engine_metadata.get("vimax_static_fallback_shots") == 2
    # gid 口径与 build_vimax_shots_manifest 全局连续键一致（scene1 偏移 = scene0 最大 idx+1 = 2）
    frame_indexes = sorted(i["frame_index"] for i in matched)
    assert frame_indexes == [1, 3]


@pytest.mark.asyncio
async def test_legacy_events_without_artifact_still_count_as_video(tmp_path):
    """无 artifact 的旧形态事件仍按视频级消费（过渡兼容，不改口径）。"""
    outcome = await _render_with_events(tmp_path, [("video_clip_static_fallback", "m", {"shot_idx": 0, "reason": "legacy"})])

    matched = [i for i in _read_sidecar(tmp_path) if i.get("kind") == "video_clip_static_fallback"]
    assert len(matched) == 1
    assert outcome.engine_metadata.get("vimax_static_fallback_shots") == 1
    assert "vimax_static_fallback_frames" not in outcome.engine_metadata


def test_degradation_warning_shows_vimax_detail_with_shot_wording(tmp_path):
    """透出链：vimax 镜级降级 detail（含镜号+原因）直接上屏，措辞用「镜」非「帧」。"""
    from app.gateway.routers.video_gen import _DEGRADATION_LABELS, _attach_degradation_warning

    assert "video_clip_static_fallback" in _DEGRADATION_LABELS
    assert "first_frame_static_fallback" in _DEGRADATION_LABELS
    (tmp_path / "degradations.json").write_text(
        json.dumps({"items": [{"kind": "video_clip_static_fallback", "detail": "第 4 镜视频生成失败，已用静态帧占位：RuntimeError: 429", "frame_index": 3}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    result: dict = {}
    _attach_degradation_warning(result, tmp_path, "ready")
    warning = result.get("degradation_warning", "")
    assert "第 4 镜" in warning and "429" in warning, f"detail 须上屏（镜号+原因），实际 {warning!r}"
    assert "帧 4：" not in warning, "vimax 镜级语义不得用「帧」措辞"
    # 非 vimax kind 措辞不变（回归面）
    (tmp_path / "degradations.json").write_text(
        json.dumps({"items": [{"kind": "bgm_failed", "detail": "x", "frame_index": 3}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    result2: dict = {}
    _attach_degradation_warning(result2, tmp_path, "ready")
    assert "帧 4：" in result2.get("degradation_warning", ""), f"非 vimax kind 保持帧措辞，实际 {result2.get('degradation_warning')!r}"
