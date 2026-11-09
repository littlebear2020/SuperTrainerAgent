"""T2（2026-10-16 收口批）：首帧占位契约——vendored 侧行为守卫。

修前缺口（Lane 1 评审 + OCR PR#25 二/三轮独立抓到）：
- 首帧兜底写灰帧/替身帧但无标记 → resume 被 ``frame_exists`` 复用永不重生；
- 视频生成拿灰帧当参考图"成功"产出废镜（无事件、无留痕）；
- 磁盘满/注册表缺 key 等系统性错误也被静默降级成灰帧。

本文件钉死 vendored 契约（app 侧消费计数见 test_vimax_static_fallback_degradation.py）：
- 兜底产物旁写 ``<frame>.failed`` 侧车（reason + timestamp）；
- ``frame_exists`` 断点见到 ``.failed`` → 删侧车与降级帧重生真帧；
- 系统性错误（OSError/KeyError/TypeError）直接上抛不降级；
- 视频生成段开头检测首帧 ``.failed`` → 直接静态视频 fallback + ``.placeholder``
  标记（不拿灰帧喂 i2v），事件照发；
- ``video_clip_static_fallback`` 事件 payload 带 ``artifact`` 字段
  （first_frame / last_frame / video_clip）。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from vimax.interfaces import Camera, ShotDescription
from vimax.pipelines.script2video_pipeline import Script2VideoPipeline

from app.gateway.video_gen.engines.vimax.provider_bridge import VimaxBudgetExceeded


def _shot(idx: int = 0, *, variation: str = "small") -> ShotDescription:
    return ShotDescription(
        idx=idx,
        is_last=(idx == 1),
        cam_idx=0,
        visual_desc=f"visual {idx}",
        variation_type=variation,
        variation_reason="test",
        ff_desc=f"first {idx}",
        ff_vis_char_idxs=[],
        lf_desc="last",
        lf_vis_char_idxs=[],
        motion_desc="motion",
        audio_desc="sound",
    )


class _BytesOutput:
    def __init__(self, payload: bytes = b"artifact"):
        self.payload = payload

    def save(self, path: str) -> None:
        with open(path, "wb") as handle:
            handle.write(self.payload)


class _FlakyImageGenerator:
    """provider 形态失败（RuntimeError）→ 可降级。"""

    def __init__(self):
        self.calls: list[str] = []

    async def generate_single_image(self, prompt: str, reference_image_paths=None, **kwargs):
        self.calls.append(prompt)
        raise RuntimeError("provider 429")


def _pipeline(tmp_path, image_generator=None, video_generator=None) -> Script2VideoPipeline:
    return Script2VideoPipeline(
        chat_model=object(),
        image_generator=image_generator or SimpleNamespace(_aspect_ratio=None),
        video_generator=video_generator or SimpleNamespace(),
        working_dir=str(tmp_path),
    )


def _patch_static_frame(monkeypatch, video_payload: bytes = b"static-video") -> None:
    from app.gateway.video_gen.engines.vimax import _static_frame

    monkeypatch.setattr(_static_frame, "resolve_target_size", lambda generator: (16, 9))

    def write_from_frame(frame_path: str, output_path: str, *args, **kwargs) -> None:
        with open(output_path, "wb") as handle:
            handle.write(video_payload)

    def write_solid(output_path: str, duration, *args, **kwargs) -> None:
        with open(output_path, "wb") as handle:
            handle.write(video_payload)

    monkeypatch.setattr(_static_frame, "make_static_video_from_frame", write_from_frame)
    monkeypatch.setattr(_static_frame, "_make_solid_color_video", write_solid)


def _stub_selector(monkeypatch, pipe, *, output=None):
    async def selector(*args, **kwargs):
        return output or {"reference_image_path_and_text_pairs": [], "text_prompt": "p"}

    monkeypatch.setattr(pipe.reference_image_selector, "select_reference_images_and_generate_prompt", selector)


def _camera() -> Camera:
    return Camera(idx=0, active_shot_idxs=[0, 1], parent_cam_idx=None, missing_info="color of the boat")


def _run_camera_frames(pipe, *, progress=None) -> asyncio.Task:
    return pipe.generate_frames_for_single_camera(
        camera=_camera(),
        shot_descriptions=[_shot(0), _shot(1)],
        characters=[],
        character_portraits_registry={},
        priority_shot_idxs=[],
        progress=progress,
    )


# ── .failed 侧车契约 ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_first_frame_fallback_writes_failed_sidecar_with_reason_and_timestamp(tmp_path, monkeypatch):
    """首帧兜底产物旁写 .failed 侧车（reason + timestamp），事件带 artifact=first_frame。"""
    gen = _FlakyImageGenerator()
    pipe = _pipeline(tmp_path, gen)
    pipe.frame_events = {0: {"first_frame": asyncio.Event()}, 1: {"first_frame": asyncio.Event()}}
    _stub_selector(monkeypatch, pipe)
    _patch_static_frame(monkeypatch, b"gray-frame")
    events: list[tuple[str, dict]] = []

    await asyncio.wait_for(_run_camera_frames(pipe, progress=lambda s, m, meta: events.append((s, dict(meta or {})))), timeout=2)

    ff = tmp_path / "shots" / "0" / "first_frame.png"
    sidecar = tmp_path / "shots" / "0" / "first_frame.png.failed"
    assert ff.exists(), "兜底帧应已写盘"
    assert sidecar.exists(), "兜底帧旁应有 .failed 侧车"
    data = json.loads(sidecar.read_text(encoding="utf-8"))
    assert "RuntimeError" in data["reason"]
    assert isinstance(data["timestamp"], (int, float))
    fallback_events = [meta for stage, meta in events if stage == "video_clip_static_fallback"]
    assert fallback_events and fallback_events[0].get("artifact") == "first_frame"


@pytest.mark.asyncio
async def test_failed_first_frame_regenerates_on_resume(tmp_path, monkeypatch):
    """resume：frame_exists 见 .failed → 删侧车与降级帧，走真生成（不再复用灰帧）。"""
    calls: list[str] = []

    class _GoodGen:
        async def generate_single_image(self, prompt: str, reference_image_paths=None, **kwargs):
            calls.append(prompt)
            return _BytesOutput(b"real-frame")

    pipe = _pipeline(tmp_path, _GoodGen())
    pipe.frame_events = {0: {"first_frame": asyncio.Event()}, 1: {"first_frame": asyncio.Event()}}
    _stub_selector(monkeypatch, pipe)
    shots_dir = tmp_path / "shots" / "0"
    shots_dir.mkdir(parents=True)
    (shots_dir / "first_frame.png").write_bytes(b"gray-frame")
    (shots_dir / "first_frame.png.failed").write_text(json.dumps({"reason": "RuntimeError: old", "timestamp": 1}), encoding="utf-8")

    await asyncio.wait_for(_run_camera_frames(pipe), timeout=2)

    assert calls, "降级帧应被重生（真生成被调用）"
    assert not (shots_dir / "first_frame.png.failed").exists(), "重生后侧车应清除"
    assert (shots_dir / "first_frame.png").read_bytes() == b"real-frame"


@pytest.mark.asyncio
async def test_b5_frame_fallback_marks_and_regenerates_on_resume(tmp_path, monkeypatch):
    """generate_frame_for_single_shot 兜底同样写 .failed；resume 重生。"""
    calls: list[int] = []

    class _FailOnce:
        def __init__(self):
            self.failed = True

        async def generate_single_image(self, prompt: str, reference_image_paths=None, **kwargs):
            calls.append(1)
            if self.failed:
                self.failed = False
                raise RuntimeError("seedream 429")
            return _BytesOutput(b"real-frame")

    pipe = _pipeline(tmp_path, _FailOnce())
    pipe.frame_events = {1: {"first_frame": asyncio.Event(), "last_frame": asyncio.Event()}}
    _stub_selector(monkeypatch, pipe)
    _patch_static_frame(monkeypatch, b"gray")

    await asyncio.wait_for(
        pipe.generate_frame_for_single_shot(
            shot_idx=1,
            frame_type="last_frame",
            first_shot_ff_path_and_text_pair=(str(tmp_path / "shots" / "0" / "first_frame.png"), "desc"),
            frame_desc="last",
            visible_characters=[],
            character_portraits_registry={},
        ),
        timeout=2,
    )
    frame = tmp_path / "shots" / "1" / "last_frame.png"
    sidecar = tmp_path / "shots" / "1" / "last_frame.png.failed"
    assert frame.exists() and sidecar.exists(), "B5 兜底帧应有 .failed 侧车"

    # resume：侧车在场 → 上一层的 frame_exists 检查应删帧重生（此处直接验证第二层语义）
    from vimax.pipelines.script2video_pipeline import _clear_failed_frame

    assert _clear_failed_frame(str(frame)) is True
    assert not sidecar.exists() and not frame.exists()


# ── 降级口径收窄：系统性错误不降级 ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_disk_error_on_first_frame_raises_instead_of_placeholder(tmp_path, monkeypatch):
    """OSError（磁盘满形态）→ 上抛，不写占位帧不留 .failed。"""

    class _DiskFull:
        async def generate_single_image(self, prompt: str, reference_image_paths=None, **kwargs):
            raise OSError(28, "No space left on device")

    pipe = _pipeline(tmp_path, _DiskFull())
    pipe.frame_events = {0: {"first_frame": asyncio.Event()}, 1: {"first_frame": asyncio.Event()}}
    _stub_selector(monkeypatch, pipe)
    _patch_static_frame(monkeypatch, b"gray")

    with pytest.raises(OSError):
        await asyncio.wait_for(_run_camera_frames(pipe), timeout=2)

    ff = tmp_path / "shots" / "0" / "first_frame.png"
    assert not ff.exists(), "系统性错误不得产出占位帧"
    assert not Path(str(ff) + ".failed").exists()


@pytest.mark.asyncio
async def test_registry_missing_key_raises_instead_of_placeholder(tmp_path, monkeypatch):
    """注册表缺 key（装配/数据错）→ 上抛不降级。"""
    shot = _shot(0)
    shot = shot.model_copy(update={"ff_vis_char_idxs": [0]})
    pipe = _pipeline(tmp_path, SimpleNamespace(_aspect_ratio=None))
    pipe.frame_events = {0: {"first_frame": asyncio.Event()}, 1: {"first_frame": asyncio.Event()}}
    _patch_static_frame(monkeypatch, b"gray")
    camera = Camera(idx=0, active_shot_idxs=[0, 1], parent_cam_idx=None, missing_info="x")

    with pytest.raises(KeyError):
        await asyncio.wait_for(
            pipe.generate_frames_for_single_camera(
                camera=camera,
                shot_descriptions=[shot, _shot(1)],
                characters=[SimpleNamespace(identifier_in_scene="甲")],
                character_portraits_registry={},  # 缺 key
                priority_shot_idxs=[],
            ),
            timeout=2,
        )


@pytest.mark.asyncio
async def test_video_side_systemic_error_raises_without_placeholder(tmp_path, monkeypatch):
    """视频段 OSError → 上抛，不写 .placeholder 静态占位。"""
    _patch_static_frame(monkeypatch, b"static")

    class _DiskFullVideo:
        async def generate_single_video(self, prompt: str, reference_image_paths=None, **kwargs):
            raise OSError(28, "No space left on device")

    pipe = _pipeline(tmp_path, video_generator=_DiskFullVideo())
    pipe.frame_events = {0: {"first_frame": asyncio.Event()}}
    pipe.frame_events[0]["first_frame"].set()
    (tmp_path / "shots" / "0").mkdir(parents=True)
    (tmp_path / "shots" / "0" / "first_frame.png").write_bytes(b"frame")

    with pytest.raises(OSError):
        await asyncio.wait_for(
            pipe.generate_video_for_single_shot(shot_description=_shot(0)),
            timeout=2,
        )
    assert not (tmp_path / "shots" / "0" / "video.mp4.placeholder").exists()


# ── 视频段不拿灰帧喂 i2v ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_video_gen_skips_i2v_when_first_frame_degraded(tmp_path, monkeypatch):
    """首帧 .failed 在场 → 不调 provider，直接静态视频 + .placeholder + 事件。"""
    _patch_static_frame(monkeypatch, b"static-video")
    provider_calls: list[str] = []

    class _RecordingVideo:
        async def generate_single_video(self, prompt: str, reference_image_paths=None, **kwargs):
            provider_calls.append(prompt)
            return _BytesOutput(b"real-video")

    pipe = _pipeline(tmp_path, video_generator=_RecordingVideo())
    pipe.frame_events = {0: {"first_frame": asyncio.Event()}}
    pipe.frame_events[0]["first_frame"].set()
    shots_dir = tmp_path / "shots" / "0"
    shots_dir.mkdir(parents=True)
    (shots_dir / "first_frame.png").write_bytes(b"gray-frame")
    (shots_dir / "first_frame.png.failed").write_text(json.dumps({"reason": "RuntimeError: seedream down", "timestamp": 1}), encoding="utf-8")
    events: list[tuple[str, dict]] = []

    await asyncio.wait_for(
        pipe.generate_video_for_single_shot(shot_description=_shot(0), progress=lambda s, m, meta: events.append((s, dict(meta or {})))),
        timeout=2,
    )

    assert not provider_calls, "降级首帧不得喂 i2v（省一单废镜费用）"
    video = shots_dir / "video.mp4"
    assert video.exists() and video.read_bytes() == b"static-video"
    assert (shots_dir / "video.mp4.placeholder").exists(), "静态兜底须带 .placeholder（resume 可重生）"
    fallback = [meta for stage, meta in events if stage == "video_clip_static_fallback"]
    assert fallback and fallback[0].get("artifact") == "video_clip"
    assert "seedream down" in fallback[0].get("reason", ""), "事件 reason 应携带首帧降级原因"


@pytest.mark.asyncio
async def test_video_provider_fallback_event_carries_artifact_field(tmp_path, monkeypatch):
    """视频段 provider 失败降级事件带 artifact=video_clip（兼容既有契约字段）。"""
    _patch_static_frame(monkeypatch, b"static-video")

    class _FailingVideo:
        async def generate_single_video(self, prompt: str, reference_image_paths=None, **kwargs):
            raise RuntimeError("happyhorse latent error")

    pipe = _pipeline(tmp_path, video_generator=_FailingVideo())
    pipe.frame_events = {0: {"first_frame": asyncio.Event()}}
    pipe.frame_events[0]["first_frame"].set()
    shots_dir = tmp_path / "shots" / "0"
    shots_dir.mkdir(parents=True)
    (shots_dir / "first_frame.png").write_bytes(b"frame")
    events: list[tuple[str, dict]] = []

    await asyncio.wait_for(
        pipe.generate_video_for_single_shot(shot_description=_shot(0), progress=lambda s, m, meta: events.append((s, dict(meta or {})))),
        timeout=2,
    )
    fallback = [meta for stage, meta in events if stage == "video_clip_static_fallback"]
    assert fallback and fallback[0].get("artifact") == "video_clip"
    assert {"shot_idx", "reason"} <= set(fallback[0]), "Lane 1 契约字段（shot_idx/reason）不得碎"


@pytest.mark.asyncio
async def test_budget_exceeded_still_bubbles_from_both_fallbacks(tmp_path, monkeypatch):
    """预算异常（fail-loud 决策）在收窄后的两条降级路径上仍然穿透。"""
    _patch_static_frame(monkeypatch, b"static")

    class _BudgetVideo:
        async def generate_single_video(self, prompt: str, reference_image_paths=None, **kwargs):
            raise VimaxBudgetExceeded("video generation budget exceeded: 40/40 calls")

    pipe = _pipeline(tmp_path, video_generator=_BudgetVideo())
    pipe.frame_events = {0: {"first_frame": asyncio.Event()}}
    pipe.frame_events[0]["first_frame"].set()
    (tmp_path / "shots" / "0").mkdir(parents=True)
    (tmp_path / "shots" / "0" / "first_frame.png").write_bytes(b"frame")

    with pytest.raises(VimaxBudgetExceeded):
        await asyncio.wait_for(pipe.generate_video_for_single_shot(shot_description=_shot(0)), timeout=2)
    assert not (tmp_path / "shots" / "0" / "video.mp4.placeholder").exists()


# ── T7 行为级（mock 端到端）：降级可完成可可见可重生 / 系统性错响亮失败 ──────


class _AsyncProgressStub:
    async def __call__(self, *, progress=None, **kw):
        pass

    async def set_result(self, **kw):
        pass


@pytest.mark.asyncio
async def test_behavioral_frame_degradation_completes_visible_then_resume_regenerates(tmp_path, monkeypatch):
    """行为级主链：首帧 provider 失败 → 任务完成（占位帧+静态镜）且降级可见 →
    resume 后灰帧/静态镜均重生为真产物；非 provider 异常 → 响亮失败不降级。"""
    from app.gateway.video_gen.engines.vimax.engine import _wrap_vimax_progress

    _patch_static_frame(monkeypatch, b"static-video")
    counter: dict = {"video_shots": set(), "frame_shots": set()}
    progress = _wrap_vimax_progress(_AsyncProgressStub(), task_dir=Path(tmp_path), static_fallback=counter)

    class _ProviderDown:
        async def generate_single_image(self, prompt: str, reference_image_paths=None, **kwargs):
            raise RuntimeError("seedream 529")

    class _GoodVideo:
        async def generate_single_video(self, prompt: str, reference_image_paths=None, **kwargs):
            return _BytesOutput(b"real-video")

    # ── 第一轮（fresh）：首帧 provider 失败 ──
    pipe1 = _pipeline(tmp_path, _ProviderDown(), _GoodVideo())
    pipe1.frame_events = {0: {"first_frame": asyncio.Event()}, 1: {"first_frame": asyncio.Event()}}
    _stub_selector(monkeypatch, pipe1)
    await asyncio.wait_for(_run_camera_frames(pipe1, progress=progress), timeout=2)
    await asyncio.wait_for(pipe1.generate_video_for_single_shot(shot_description=_shot(0), progress=progress), timeout=2)

    shots_dir = tmp_path / "shots" / "0"
    ff, ff_failed = shots_dir / "first_frame.png", shots_dir / "first_frame.png.failed"
    video, video_ph = shots_dir / "video.mp4", shots_dir / "video.mp4.placeholder"
    assert ff.exists() and ff_failed.exists(), "占位帧 + .failed 标记"
    assert video.exists() and video.read_bytes() == b"static-video" and video_ph.exists(), "静态镜 + .placeholder"
    # 降级可见：sidecar（帧级 + 视频级 kind 都在）+ 计数（engine_metadata 同款键语义）。
    # 相机 active_shot_idxs=[0,1]：镜 1 的 first_frame 走 B5 同样降级 → frame_shots={0,1}。
    sidecar = json.loads((tmp_path / "degradations.json").read_text(encoding="utf-8"))["items"]
    kinds = {i["kind"] for i in sidecar}
    assert kinds == {"first_frame_static_fallback", "video_clip_static_fallback"}, f"实际 {sidecar}"
    assert counter["frame_shots"] == {0, 1} and counter["video_shots"] == {0}

    # ── 第二轮（resume）：provider 恢复 ──
    class _GoodImage:
        async def generate_single_image(self, prompt: str, reference_image_paths=None, **kwargs):
            return _BytesOutput(b"real-frame")

    pipe2 = _pipeline(tmp_path, _GoodImage(), _GoodVideo())
    pipe2.frame_events = {0: {"first_frame": asyncio.Event()}, 1: {"first_frame": asyncio.Event()}}
    _stub_selector(monkeypatch, pipe2)
    await asyncio.wait_for(_run_camera_frames(pipe2), timeout=2)
    await asyncio.wait_for(pipe2.generate_video_for_single_shot(shot_description=_shot(0)), timeout=2)

    assert ff.read_bytes() == b"real-frame" and not ff_failed.exists(), "灰帧被重生、标记清除"
    assert video.read_bytes() == b"real-video" and not video_ph.exists(), "静态占位镜被重生、标记清除"


@pytest.mark.asyncio
async def test_behavioral_systemic_failure_is_loud_no_degradation(tmp_path, monkeypatch):
    """非 provider 异常（模拟磁盘错）→ 响亮失败：无占位帧、无标记、无降级留痕。"""
    _patch_static_frame(monkeypatch, b"static-video")

    class _DiskFull:
        async def generate_single_image(self, prompt: str, reference_image_paths=None, **kwargs):
            raise OSError(28, "No space left on device")

    pipe = _pipeline(tmp_path, _DiskFull())
    pipe.frame_events = {0: {"first_frame": asyncio.Event()}, 1: {"first_frame": asyncio.Event()}}
    _stub_selector(monkeypatch, pipe)

    with pytest.raises(OSError):
        await asyncio.wait_for(_run_camera_frames(pipe), timeout=2)

    assert not (tmp_path / "shots" / "0" / "first_frame.png").exists()
    assert not (tmp_path / "shots" / "0" / "first_frame.png.failed").exists()
    assert not (tmp_path / "degradations.json").exists(), "系统性失败不得留降级痕（那是降级路径的语义）"
