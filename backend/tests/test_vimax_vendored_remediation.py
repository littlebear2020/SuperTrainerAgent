"""2026-10-15 vendored-lane remediation regressions (T1-T7).

These tests intentionally exercise vendored behavior without touching the app-side
Lane 2 files. Cross-lane contracts locked here:

- ``video_clip_static_fallback`` payload must at least contain ``shot_idx`` and
  ``reason``.
- static video placeholders are marked with ``<video>.placeholder`` and regenerated
  on a later non-budget run.
- ``VimaxBudgetExceeded`` must bubble out of per-shot fallback handlers (fail-loud).
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from langchain_core.runnables import Runnable
from PIL import Image
from vimax.agents.storyboard_artist import StoryboardArtist
from vimax.interfaces import Camera, CharacterInScene, ShotBriefDescription, ShotDescription
from vimax.pipelines.idea2video_pipeline import Idea2VideoPipeline
from vimax.pipelines.script2video_pipeline import Script2VideoPipeline

from app.gateway.video_gen.engines.vimax.provider_bridge import VimaxBudgetExceeded


def _character(name: str, idx: int = 0) -> CharacterInScene:
    return CharacterInScene(idx=idx, identifier_in_scene=name, is_visible=True, static_features=f"{name} static", dynamic_features=f"{name} dynamic")


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


def _brief(idx: int) -> ShotBriefDescription:
    return ShotBriefDescription(idx=idx, is_last=(idx == 1), cam_idx=0, visual_desc=f"visual {idx}", audio_desc="sound")


class _BytesOutput:
    def __init__(self, payload: bytes = b"artifact"):
        self.payload = payload

    def save(self, path: str) -> None:
        with open(path, "wb") as handle:
            handle.write(self.payload)


class _RecordingImageGenerator:
    def __init__(self, failures: int = 0):
        self.failures = failures
        self.calls: list[dict] = []

    async def generate_single_image(self, prompt: str, reference_image_paths=None, **kwargs):
        self.calls.append({"prompt": prompt, "reference_image_paths": reference_image_paths, **kwargs})
        if self.failures > 0:
            self.failures -= 1
            raise RuntimeError("image generation failed")
        return _BytesOutput(b"real-image")


def _pipeline(tmp_path, image_generator=None) -> Script2VideoPipeline:
    return Script2VideoPipeline(chat_model=object(), image_generator=image_generator or SimpleNamespace(_aspect_ratio=None), video_generator=SimpleNamespace(), working_dir=str(tmp_path))


def _patch_static_frame(monkeypatch, video_payload: bytes = b"static-video") -> None:
    from app.gateway.video_gen.engines.vimax import _static_frame

    monkeypatch.setattr(_static_frame, "resolve_target_size", lambda generator: (16, 9))

    def write_static(path: str, *args, **kwargs) -> None:
        with open(path, "wb") as handle:
            handle.write(video_payload)

    monkeypatch.setattr(_static_frame, "make_static_video_from_frame", write_static)
    monkeypatch.setattr(_static_frame, "_make_solid_color_video", write_static)


# ── T1: first-frame failures must set dependency events and emit the contract ──


@pytest.mark.asyncio
async def test_first_frame_selector_failure_sets_events_and_keeps_sibling_frame(tmp_path, monkeypatch):
    pipe = _pipeline(tmp_path, _RecordingImageGenerator())
    pipe.frame_events = {0: {"first_frame": asyncio.Event()}, 1: {"first_frame": asyncio.Event()}}
    selector_calls = 0
    events: list[tuple[str, dict]] = []

    def progress(stage, message, metadata=None):
        events.append((stage, dict(metadata or {})))

    async def selector(*args, **kwargs):
        nonlocal selector_calls
        selector_calls += 1
        if selector_calls == 1:
            raise RuntimeError("selector failed")
        return {"reference_image_path_and_text_pairs": [], "text_prompt": "second frame"}

    monkeypatch.setattr(pipe.reference_image_selector, "select_reference_images_and_generate_prompt", selector)
    _patch_static_frame(monkeypatch, b"png-placeholder")

    camera = Camera(idx=0, active_shot_idxs=[0, 1], parent_cam_idx=None)
    await asyncio.wait_for(
        pipe.generate_frames_for_single_camera(
            camera=camera,
            shot_descriptions=[_shot(0), _shot(1)],
            characters=[],
            character_portraits_registry={},
            priority_shot_idxs=[],
            progress=progress,
        ),
        timeout=1,
    )

    assert pipe.frame_events[0]["first_frame"].is_set()
    assert pipe.frame_events[1]["first_frame"].is_set()
    with Image.open(tmp_path / "shots" / "0" / "first_frame.png") as placeholder:
        assert placeholder.size == (16, 9)
    assert (tmp_path / "shots" / "1" / "first_frame.png").read_bytes() == b"real-image"
    fallback = [metadata for stage, metadata in events if stage == "video_clip_static_fallback"]
    assert fallback and fallback[0]["shot_idx"] == 0 and isinstance(fallback[0]["reason"], str)


@pytest.mark.asyncio
async def test_first_frame_fallback_prefers_existing_new_camera_image(tmp_path, monkeypatch):
    pipe = _pipeline(tmp_path)
    pipe.frame_events = {0: {"first_frame": asyncio.Event()}, 1: {"first_frame": asyncio.Event()}}
    pipe.frame_events[0]["first_frame"].set()
    shot_dir = tmp_path / "shots" / "1"
    shot_dir.mkdir(parents=True)
    new_camera_path = shot_dir / "new_camera_1.png"
    new_camera_path.write_bytes(b"composition-reference")
    (shot_dir / "transition_video_from_shot_0.mp4").write_bytes(b"transition")
    placeholder_calls: list[str] = []

    def fail_image(*args, **kwargs):
        raise RuntimeError("image generation failed")

    async def selector(*args, **kwargs):
        return {"reference_image_path_and_text_pairs": [], "text_prompt": "prompt"}

    monkeypatch.setattr(pipe.reference_image_selector, "select_reference_images_and_generate_prompt", selector)
    pipe.image_generator.generate_single_image = fail_image
    monkeypatch.setattr(
        "vimax.pipelines.script2video_pipeline._write_placeholder_frame",
        lambda path, generator: placeholder_calls.append(path),
    )

    camera = Camera(idx=1, active_shot_idxs=[1], parent_cam_idx=0, parent_shot_idx=0, missing_info="missing detail")
    await pipe.generate_frames_for_single_camera(
        camera=camera,
        shot_descriptions=[_shot(0), _shot(1)],
        characters=[],
        character_portraits_registry={},
        priority_shot_idxs=[],
    )

    assert (shot_dir / "first_frame.png").read_bytes() == b"composition-reference"
    assert placeholder_calls == []
    assert pipe.frame_events[1]["first_frame"].is_set()


@pytest.mark.asyncio
async def test_first_frame_image_failure_sets_event_and_falls_back(tmp_path, monkeypatch):
    generator = _RecordingImageGenerator(failures=1)
    pipe = _pipeline(tmp_path, generator)
    pipe.frame_events = {0: {"first_frame": asyncio.Event()}}
    events: list[tuple[str, dict]] = []

    async def selector(*args, **kwargs):
        return {"reference_image_path_and_text_pairs": [], "text_prompt": "prompt"}

    monkeypatch.setattr(pipe.reference_image_selector, "select_reference_images_and_generate_prompt", selector)
    _patch_static_frame(monkeypatch, b"first-frame-placeholder")

    camera = Camera(idx=0, active_shot_idxs=[0], parent_cam_idx=None)
    await asyncio.wait_for(
        pipe.generate_frames_for_single_camera(
            camera=camera,
            shot_descriptions=[_shot(0)],
            characters=[],
            character_portraits_registry={},
            priority_shot_idxs=[],
            progress=lambda stage, message, metadata=None: events.append((stage, dict(metadata or {}))),
        ),
        timeout=1,
    )

    assert generator.calls, "the real image path was attempted"
    assert pipe.frame_events[0]["first_frame"].is_set()
    with Image.open(tmp_path / "shots" / "0" / "first_frame.png") as placeholder:
        assert placeholder.size == (16, 9)
    assert any(stage == "video_clip_static_fallback" for stage, _ in events)


@pytest.mark.asyncio
async def test_first_frame_budget_exception_does_not_wake_dependents(tmp_path, monkeypatch):
    pipe = _pipeline(tmp_path)
    pipe.frame_events = {0: {"first_frame": asyncio.Event()}}

    async def selector(*args, **kwargs):
        return {"reference_image_path_and_text_pairs": [], "text_prompt": "prompt"}

    async def budget_generate(*args, **kwargs):
        raise VimaxBudgetExceeded("image budget exhausted")

    monkeypatch.setattr(pipe.reference_image_selector, "select_reference_images_and_generate_prompt", selector)
    pipe.image_generator = SimpleNamespace(_aspect_ratio=None)
    pipe.image_generator.generate_single_image = budget_generate

    camera = Camera(idx=0, active_shot_idxs=[0], parent_cam_idx=None)
    with pytest.raises(VimaxBudgetExceeded):
        await pipe.generate_frames_for_single_camera(
            camera=camera,
            shot_descriptions=[_shot(0)],
            characters=[],
            character_portraits_registry={},
            priority_shot_idxs=[],
        )

    assert not pipe.frame_events[0]["first_frame"].is_set(), "budget exhaustion must not wake paid dependents"


@pytest.mark.asyncio
async def test_render_gather_cancels_remaining_tasks_on_fatal_error():
    from vimax.pipelines.script2video_pipeline import _gather_cancelling_remaining

    sibling_started = asyncio.Event()

    async def fatal():
        await sibling_started.wait()
        raise VimaxBudgetExceeded("budget exhausted")

    sibling_finished = asyncio.Event()

    async def sibling():
        sibling_started.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            sibling_finished.set()
            raise

    tasks = [asyncio.create_task(fatal()), asyncio.create_task(sibling())]
    with pytest.raises(VimaxBudgetExceeded):
        await _gather_cancelling_remaining(tasks)
    assert sibling_started.is_set()
    assert sibling_finished.is_set()


# ── T2: budget fail-loud, placeholder marker, and non-budget regeneration ─────


@pytest.mark.asyncio
async def test_video_budget_exception_is_not_swallowed_by_static_fallback(tmp_path, monkeypatch):
    pipe = _pipeline(tmp_path)
    pipe.frame_events[0] = {"first_frame": asyncio.Event()}
    pipe.frame_events[0]["first_frame"].set()
    (tmp_path / "shots" / "0").mkdir(parents=True)
    (tmp_path / "shots" / "0" / "first_frame.png").write_bytes(b"png")
    _patch_static_frame(monkeypatch)

    async def generate(*args, **kwargs):
        raise VimaxBudgetExceeded("budget exhausted")

    pipe.video_generator.generate_single_video = generate
    with pytest.raises(VimaxBudgetExceeded):
        await pipe.generate_video_for_single_shot(_shot(), characters=[], style="Cartoon")


@pytest.mark.asyncio
async def test_static_video_fallback_writes_placeholder_marker(tmp_path, monkeypatch):
    pipe = _pipeline(tmp_path)
    pipe.frame_events[0] = {"first_frame": asyncio.Event()}
    pipe.frame_events[0]["first_frame"].set()
    (tmp_path / "shots" / "0").mkdir(parents=True)
    _patch_static_frame(monkeypatch, b"static-placeholder")
    video = tmp_path / "shots" / "0" / "video.mp4"
    from vimax.pipelines import script2video_pipeline

    marker_calls: list[bool] = []
    real_write_marker = script2video_pipeline._write_placeholder_marker

    def recording_marker(path: str) -> None:
        marker_calls.append(video.exists())
        real_write_marker(path)

    monkeypatch.setattr(script2video_pipeline, "_write_placeholder_marker", recording_marker)

    async def generate(*args, **kwargs):
        raise RuntimeError("ordinary provider failure")

    pipe.video_generator.generate_single_video = generate
    await pipe.generate_video_for_single_shot(_shot(), characters=[], style="Cartoon")

    marker = tmp_path / "shots" / "0" / "video.mp4.placeholder"
    assert marker_calls == [False], "marker must be durable before placeholder video write"
    assert video.read_bytes() == b"static-placeholder"
    assert marker.exists()


@pytest.mark.asyncio
async def test_marked_video_placeholder_regenerates_on_non_budget_resume(tmp_path):
    pipe = _pipeline(tmp_path)
    pipe.frame_events[0] = {"first_frame": asyncio.Event()}
    pipe.frame_events[0]["first_frame"].set()
    shot_dir = tmp_path / "shots" / "0"
    shot_dir.mkdir(parents=True)
    (shot_dir / "video.mp4").write_bytes(b"static-placeholder")
    (shot_dir / "video.mp4.placeholder").write_text("", encoding="utf-8")
    calls: list[str] = []

    async def generate(*args, **kwargs):
        calls.append("called")
        return _BytesOutput(b"real-video")

    pipe.video_generator.generate_single_video = generate
    await pipe.generate_video_for_single_shot(_shot(), characters=[], style="Cartoon")

    assert calls == ["called"]
    assert (shot_dir / "video.mp4").read_bytes() == b"real-video"
    assert not (shot_dir / "video.mp4.placeholder").exists()


@pytest.mark.asyncio
async def test_frame_budget_exception_is_not_swallowed_by_b5_fallback(tmp_path, monkeypatch):
    generator = _RecordingImageGenerator(failures=1)
    pipe = _pipeline(tmp_path, generator)
    pipe.frame_events[0] = {"first_frame": asyncio.Event()}

    async def selector(*args, **kwargs):
        return {"reference_image_path_and_text_pairs": [], "text_prompt": "prompt"}

    monkeypatch.setattr(pipe.reference_image_selector, "select_reference_images_and_generate_prompt", selector)

    async def budget_generate(*args, **kwargs):
        raise VimaxBudgetExceeded("budget exhausted")

    generator.generate_single_image = budget_generate
    with pytest.raises(VimaxBudgetExceeded):
        await pipe.generate_frame_for_single_shot(
            shot_idx=0,
            frame_type="first_frame",
            first_shot_ff_path_and_text_pair=(str(tmp_path / "unused.png"), "desc"),
            frame_desc="frame",
            visible_characters=[],
            character_portraits_registry={},
        )


# ── T3: LLM prompt contract and idea2video style propagation ─────────────────


def test_character_dynamic_features_contract_uses_empty_string_for_missing_values():
    field = CharacterInScene.model_fields["dynamic_features"]
    description = field.description or ""
    assert "empty string" in description
    assert "never return null" in description
    assert "None" not in description


class _CaptureDecomposeChat(Runnable):
    def __init__(self):
        self.value: dict | None = None

    def invoke(self, value, config=None, **kwargs):
        raise RuntimeError("sync path is not under test")

    async def ainvoke(self, value, config=None, **kwargs):
        self.value = value
        return json.dumps(
            {
                "ff_desc": "first",
                "ff_vis_char_idxs": [],
                "lf_desc": "last",
                "lf_vis_char_idxs": [],
                "motion_desc": "motion",
                "variation_type": "small",
                "variation_reason": "reason",
            }
        )


@pytest.mark.asyncio
async def test_decompose_prompt_numbers_characters():
    chat = _CaptureDecomposeChat()
    artist = StoryboardArtist(chat_model=chat)
    await artist.decompose_visual_description(shot_brief_desc=_brief(0), characters=[_character("Alice", 0), _character("Bob", 1)])
    characters = chat.value.messages[1].content
    assert "Character 0: Alice" in characters
    assert "Character 1: Bob" in characters


@pytest.mark.asyncio
async def test_idea2video_three_views_all_receive_user_style(tmp_path):
    generator = _RecordingImageGenerator()
    pipeline = Idea2VideoPipeline(chat_model=object(), image_generator=generator, video_generator=SimpleNamespace(), working_dir=str(tmp_path))

    await pipeline.generate_portraits_for_single_character(_character("Alice"), "Watercolor storybook")

    assert len(generator.calls) == 3
    assert all("Watercolor storybook" in call["prompt"] for call in generator.calls)


# ── T4: storyboard/camera/concat validation ──────────────────────────────────


class _StoryboardSequenceChat(Runnable):
    def __init__(self):
        self.calls = 0

    def invoke(self, value, config=None, **kwargs):
        raise RuntimeError("sync path is not under test")

    async def ainvoke(self, value, config=None, **kwargs):
        self.calls += 1
        shots = [
            {"idx": 0, "is_last": False, "cam_idx": 0, "visual_desc": "v0", "audio_desc": "a0"},
            {"idx": 1, "is_last": True, "cam_idx": 0, "visual_desc": "v1", "audio_desc": "a1"},
        ]
        if self.calls == 1:
            shots[1]["idx"] = 0
        return json.dumps({"storyboard": shots})


@pytest.mark.asyncio
async def test_storyboard_reasks_when_shot_indices_are_not_contiguous(monkeypatch):
    import tenacity
    import vimax.agents.storyboard_artist as storyboard_artist_module
    from vimax.agents.storyboard_artist import StoryboardArtist as Artist

    monkeypatch.setattr(storyboard_artist_module, "_STORYBOARD_RETRY_WAIT", tenacity.wait_none())
    chat = _StoryboardSequenceChat()
    artist = Artist(chat_model=chat)
    storyboard = await artist.design_storyboard(script="script", characters=[], user_requirement=None)
    assert chat.calls == 2
    assert [shot.idx for shot in storyboard] == [0, 1]


class _CameraTreeSequenceChat(Runnable):
    def __init__(self, *parent_shot_idxs):
        self.parent_shot_idxs = parent_shot_idxs
        self.calls = 0

    def invoke(self, value, config=None, **kwargs):
        raise RuntimeError("sync path is not under test")

    async def ainvoke(self, value, config=None, **kwargs):
        parent_shot_idx = self.parent_shot_idxs[min(self.calls, len(self.parent_shot_idxs) - 1)]
        self.calls += 1
        return json.dumps(
            {
                "camera_parent_items": [
                    None,
                    {
                        "parent_cam_idx": 0,
                        "parent_shot_idx": parent_shot_idx,
                        "reason": "coverage",
                        "is_parent_fully_covers_child": False,
                        "missing_info": "detail",
                    },
                ]
            }
        )


@pytest.mark.asyncio
async def test_camera_tree_reasks_on_unknown_parent_shot(monkeypatch):
    import tenacity
    from vimax.agents.camera_image_generator import CameraImageGenerator

    monkeypatch.setattr(CameraImageGenerator.construct_camera_tree.retry, "wait", tenacity.wait_none())
    chat = _CameraTreeSequenceChat(99, 0)
    generator = CameraImageGenerator(chat_model=chat, image_generator=SimpleNamespace(), video_generator=SimpleNamespace())
    cameras = [Camera(idx=0, active_shot_idxs=[0]), Camera(idx=1, active_shot_idxs=[1])]
    descriptions = [_shot(0), _shot(1)]
    result = await generator.construct_camera_tree(cameras=cameras, shot_descs=descriptions)
    assert chat.calls == 2
    assert result[1].parent_shot_idx == 0


def test_camera_tree_rejects_parent_shot_outside_parent_active_shots():
    from vimax.agents.camera_image_generator import _validate_camera_tree

    cameras = [Camera(idx=0, active_shot_idxs=[0]), Camera(idx=1, active_shot_idxs=[1], parent_cam_idx=0, parent_shot_idx=99)]
    with pytest.raises(ValueError, match="parent shot"):
        _validate_camera_tree(cameras)


@pytest.mark.asyncio
async def test_persistent_malformed_camera_tree_fails_loud_within_timeout(monkeypatch):
    import tenacity
    from vimax.agents.camera_image_generator import CameraImageGenerator

    monkeypatch.setattr(CameraImageGenerator.construct_camera_tree.retry, "wait", tenacity.wait_none())
    chat = _CameraTreeSequenceChat(99)
    generator = CameraImageGenerator(chat_model=chat, image_generator=SimpleNamespace(), video_generator=SimpleNamespace())
    cameras = [Camera(idx=0, active_shot_idxs=[0]), Camera(idx=1, active_shot_idxs=[1])]
    with pytest.raises(ValueError, match="parent shot"):
        await asyncio.wait_for(generator.construct_camera_tree(cameras=cameras, shot_descs=[_shot(0), _shot(1)]), timeout=1)
    assert chat.calls == 3


def test_both_active_pipelines_reject_empty_concat_input():
    from vimax.pipelines import idea2video_pipeline, script2video_pipeline

    for module in (script2video_pipeline, idea2video_pipeline):
        assert hasattr(module, "_raise_if_empty_video_paths")
        with pytest.raises(ValueError, match="No video clips"):
            module._raise_if_empty_video_paths([], "final.mp4")


# ── T5: retry hardening for the two missed sites ─────────────────────────────


def test_camera_tree_retry_has_backoff_and_reraise():
    import tenacity
    from vimax.agents.camera_image_generator import CameraImageGenerator

    retry = CameraImageGenerator.construct_camera_tree.retry
    assert retry.reraise is True
    assert isinstance(retry.wait, tenacity.wait_exponential)


# best_image_selector 的 @retry 站点测试已随 novel2movie 管线拆除移除（T1，
# 2026-10-16）；保留 agents 的 retry 硬化由 test_vimax_llm_timeout.py 守护。


# ── T6: portrait gate and per-character references ──────────────────────────


class _BrokenImageOutput:
    def save(self, path: str) -> None:
        with open(path, "wb") as handle:
            handle.write(b"not a valid image")


@pytest.mark.asyncio
async def test_portrait_gate_rejects_unreadable_images_and_regenerates(tmp_path, monkeypatch):
    from vimax.agents.character_portraits_generator import generate_portrait_with_gate

    monkeypatch.setenv("VIMAX_PORTRAIT_FACE_CHECK", "0")
    calls = 0

    async def generate():
        nonlocal calls
        calls += 1
        return _BrokenImageOutput()

    result = await generate_portrait_with_gate(generate, str(tmp_path / "front.png"), identifier="Alice", view="front")
    assert calls == 2
    assert result["acceptable"] is False
    assert "unreadable" in result["reasons"]


@pytest.mark.asyncio
async def test_portrait_gate_rejects_truncated_png_and_regenerates(tmp_path, monkeypatch):
    from vimax.agents.character_portraits_generator import generate_portrait_with_gate

    monkeypatch.setenv("VIMAX_PORTRAIT_FACE_CHECK", "0")
    source = tmp_path / "complete.png"
    Image.new("RGB", (1024, 1024), "teal").save(source)
    truncated_payload = source.read_bytes()[: source.stat().st_size // 2]
    calls = 0

    class _TruncatedPngOutput:
        def save(self, path: str) -> None:
            with open(path, "wb") as handle:
                handle.write(truncated_payload)

    async def generate():
        nonlocal calls
        calls += 1
        return _TruncatedPngOutput()

    result = await generate_portrait_with_gate(generate, str(tmp_path / "front.png"), identifier="Alice", view="front")
    assert calls == 2
    assert result["acceptable"] is False
    assert "unreadable" in result["reasons"]


@pytest.mark.asyncio
async def test_side_and_back_references_match_each_character_sheet(tmp_path):
    from vimax.agents.character_portraits_generator import CharacterPortraitsGenerator

    alice_side, alice_back, bob_side, bob_back = (tmp_path / f"{name}.png" for name in ("alice-side", "alice-back", "bob-side", "bob-back"))
    for path, color in zip((alice_side, alice_back, bob_side, bob_back), ("red", "blue", "green", "yellow")):
        Image.new("RGB", (8, 8), color).save(path)

    generator = CharacterPortraitsGenerator(
        image_generator=None,
        character_sheets=[
            {"name": "Alice", "side_url": str(alice_side), "back_url": str(alice_back)},
            {"name": "Bob", "side_url": str(bob_side), "back_url": str(bob_back)},
        ],
    )
    calls: list[tuple[str, list[str]]] = []

    class _RecordingGenerator:
        async def generate_single_image(self, prompt, reference_image_paths=None, **kwargs):
            calls.append((prompt, list(reference_image_paths or [])))
            return _BytesOutput()

    generator.image_generator = _RecordingGenerator()
    front = str(tmp_path / "front.png")
    await generator.generate_side_portrait(_character("Alice"), front, style="Cartoon")
    await generator.generate_back_portrait(_character("Alice"), front, style="Cartoon")
    await generator.generate_side_portrait(_character("Bob"), front, style="Cartoon")
    await generator.generate_back_portrait(_character("Bob"), front, style="Cartoon")

    assert calls[0][1] == [front, str(alice_side)]
    assert calls[1][1] == [front, str(alice_back)]
    assert calls[2][1] == [front, str(bob_side)]
    assert calls[3][1] == [front, str(bob_back)]


# ── T7: offloaded saves and atomic JSON persistence ─────────────────────────


@pytest.mark.asyncio
async def test_video_save_is_offloaded_from_event_loop(tmp_path, monkeypatch):
    from vimax.pipelines import script2video_pipeline

    pipe = _pipeline(tmp_path)
    pipe.frame_events[0] = {"first_frame": asyncio.Event()}
    pipe.frame_events[0]["first_frame"].set()
    (tmp_path / "shots" / "0").mkdir(parents=True)
    calls: list[tuple[object, tuple]] = []

    async def generate(*args, **kwargs):
        return _BytesOutput(b"real-video")

    pipe.video_generator.generate_single_video = generate
    real_to_thread = asyncio.to_thread

    async def recording_to_thread(func, /, *args, **kwargs):
        calls.append((func, args))
        return await real_to_thread(func, *args, **kwargs)

    monkeypatch.setattr(script2video_pipeline.asyncio, "to_thread", recording_to_thread)
    await pipe.generate_video_for_single_shot(_shot(), characters=[], style="Cartoon")
    assert calls and calls[0][0].__name__ == "save"


def test_atomic_json_dump_never_leaks_partial_target(tmp_path):
    from vimax.pipelines.script2video_pipeline import _atomic_json_dump

    target = tmp_path / "artifact.json"

    class Unserializable:
        pass

    with pytest.raises(TypeError):
        _atomic_json_dump(Unserializable(), str(target))
    assert not target.exists()
    assert not list(tmp_path.glob("*.tmp"))


def test_atomic_json_dump_creates_shared_readable_artifact(tmp_path):
    from vimax.pipelines.script2video_pipeline import _atomic_json_dump

    target = tmp_path / "artifact.json"
    _atomic_json_dump({"ok": True}, str(target))

    assert target.stat().st_mode & 0o777 == 0o644


@pytest.mark.asyncio
async def test_gate_save_is_offloaded_from_event_loop(tmp_path, monkeypatch):
    from vimax.agents import character_portraits_generator

    monkeypatch.setenv("VIMAX_PORTRAIT_FACE_CHECK", "0")
    calls: list[object] = []
    real_to_thread = asyncio.to_thread

    async def generate():
        return _BytesOutput(b"portrait")

    async def recording_to_thread(func, /, *args, **kwargs):
        calls.append(func)
        return await real_to_thread(func, *args, **kwargs)

    monkeypatch.setattr(character_portraits_generator.asyncio, "to_thread", recording_to_thread)
    await character_portraits_generator.generate_portrait_with_gate(generate, str(tmp_path / "front.png"), identifier="Alice", view="front")
    assert calls and calls[0].__name__ == "save"
