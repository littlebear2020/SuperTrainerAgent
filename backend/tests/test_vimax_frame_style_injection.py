"""Regression tests for ViMax frame-generation style injection (4.3 Cartoon gap).

Real run vg_f23954591917 (style="Cartoon") produced a 5-shot video with excellent
character consistency (5/5) but the style never took effect — every frame rendered
as photorealistic. Root cause: ``Script2VideoPipeline.__call__`` receives ``style``
and threads it into *character portraits* (front/side/back), but the per-frame
generation path (``generate_frames_for_single_camera`` first-frame branch and
``generate_frame_for_single_shot`` for follow-on frames) builds the image prompt
purely from ``reference_image_selector`` output, which is never told the style.
The only way style reached the frames was "implicitly, via the portrait reference
images" — and NanoBanana (gemini-3-pro) weights the写实 text description over the
cartoon reference, so the style was lost.

Fix: thread ``style`` into the frame prompt explicitly so the image model honors
the user-selected style on every frame, not just portraits. These tests pin that
the ``style`` value appears in the prompt actually sent to ``generate_single_image``
for both frame-generation paths.

These touch vendored ``packages/vimax`` — a minimal, well-marked vendor patch
(plan §2.1 allows vendor edits; the cost is tracked).
"""

from unittest.mock import AsyncMock

import pytest
from vimax.interfaces import Camera, CharacterInScene, ShotDescription
from vimax.pipelines.script2video_pipeline import Script2VideoPipeline

# ── helpers ──────────────────────────────────────────────────────────────────


class _FakeImageOutput:
    """Matches the ``.save(path)`` surface the pipeline calls after generation."""

    def __init__(self):
        self.prompt = None  # captured for assertions
        self._fake = _make_pil()

    def save(self, path):
        self._fake.save(path)


def _make_pil():
    from PIL import Image

    return Image.new("RGB", (16, 9))


class _RecordingImageGenerator:
    """Records every prompt handed to ``generate_single_image``.

    We don't mock the image generator away entirely — we want to assert the
    *text* it receives — but we write a tiny PNG so the pipeline's
    ``frame_image.save(...)`` path succeeds and its skip-if-exists logic stays
    exercised.
    """

    def __init__(self):
        self.prompts = []

    async def generate_single_image(self, prompt, reference_image_paths=None, **kwargs):
        self.prompts.append(prompt)
        return _FakeImageOutput()


def _trainer() -> CharacterInScene:
    return CharacterInScene(
        idx=0,
        identifier_in_scene="<Coach>",
        is_visible=True,
        static_features="woman in her 30s, short black hair, friendly face",
        dynamic_features="wearing a blue polo shirt",
    )


def _small_shot(idx=0, cam_idx=0, is_last=True) -> ShotDescription:
    """A minimal 'small' variation shot so only first_frame (not last_frame) is
    generated — keeps the test focused on the single frame path."""
    return ShotDescription(
        idx=idx,
        is_last=is_last,
        cam_idx=cam_idx,
        visual_desc="Medium shot of <Coach> demonstrating a squat.",
        variation_type="small",
        variation_reason="Minimal change between frames.",
        ff_desc="Medium shot. <Coach> stands ready to demonstrate a squat.",
        ff_vis_char_idxs=[0],
        lf_desc="",
        lf_vis_char_idxs=[],
        motion_desc="<Coach> lowers into a squat.",
        audio_desc="[Narration] Keep your back straight.",
    )


def _build_pipeline(tmp_path, *, style_in_registry=False) -> Script2VideoPipeline:
    """Build a pipeline with mocked LLM-bound agents and a recording image gen.

    The agents (storyboard/camera/reference-selector) are replaced with stubs so
    we don't hit any LLM; only the prompt-string assembly path is exercised."""
    img_gen = _RecordingImageGenerator()
    pipe = Script2VideoPipeline(
        chat_model=object(),  # never used; agents are stubbed below
        image_generator=img_gen,
        video_generator=AsyncMock(),
        working_dir=str(tmp_path),
    )
    # the pipeline writes per-shot selector output + frames under shots/{idx}/;
    # normally created by decompose_visual_description_..., which these tests skip.
    import os

    os.makedirs(os.path.join(str(tmp_path), "shots", "0"), exist_ok=True)
    # reference_image_selector normally calls an LLM; stub it to a canned prompt so
    # we can assert the *pipeline* layered ``style`` on top of its output.
    pipe.reference_image_selector = AsyncMock()
    pipe.reference_image_selector.select_reference_images_and_generate_prompt = AsyncMock(
        return_value={
            "reference_image_path_and_text_pairs": [("/tmp/front.png", "A front portrait of <Coach>.")],
            "text_prompt": "Generate the frame described above.",
        }
    )
    pipe.camera_image_generator = AsyncMock()
    return pipe


# ── RED: per-frame prompt must carry the user-selected style ─────────────────


@pytest.mark.asyncio
async def test_generate_frame_for_single_shot_injects_style(tmp_path):
    """Follow-on frames (last_frame / non-first first_frame) must receive the
    ``style`` string in the prompt sent to the image generator. This is the path
    that produced all-写实 frames in the Cartoon run."""
    pipe = _build_pipeline(tmp_path)
    character = _trainer()
    registry = {character.identifier_in_scene: {"front": {"path": "/tmp/front.png", "description": "portrait"}}}

    # set up frame events so generate_frame_for_single_shot can .set() them
    pipe.frame_events[0] = {"first_frame": AsyncMock(), "last_frame": AsyncMock()}
    # avoid AsyncMock attribute issues on asyncio.Event; use real Events
    import asyncio

    pipe.frame_events[0] = {"first_frame": asyncio.Event(), "last_frame": asyncio.Event()}

    await pipe.generate_frame_for_single_shot(
        shot_idx=0,
        frame_type="last_frame",
        first_shot_ff_path_and_text_pair=("/tmp/front.png", "first frame desc"),
        frame_desc="last frame desc",
        visible_characters=[character],
        character_portraits_registry=registry,
        style="Cartoon",
    )

    assert pipe.image_generator.prompts, "generate_single_image was never called"
    prompt = pipe.image_generator.prompts[-1]
    assert "Cartoon" in prompt, "last_frame prompt dropped the style — follow-on frames would render 写实 even when the user selected a non-realistic style (4.3 Cartoon regression)"


@pytest.mark.asyncio
async def test_camera_first_frame_injects_style(tmp_path):
    """The first-frame branch inside ``generate_frames_for_single_camera`` (the
    *first* shot of each camera, generated via reference_image_selector) must
    also carry ``style``. This is the path that produced the first 写实 frame in
    the Cartoon run, which then anchored all subsequent frames."""
    pipe = _build_pipeline(tmp_path)
    character = _trainer()
    registry = {character.identifier_in_scene: {"front": {"path": "/tmp/front.png", "description": "portrait"}}}
    shot = _small_shot(idx=0, cam_idx=0, is_last=True)

    import asyncio

    pipe.frame_events[0] = {"first_frame": asyncio.Event()}

    camera = Camera(idx=0, active_shot_idxs=[0])  # root camera, no parent

    await pipe.generate_frames_for_single_camera(
        camera=camera,
        shot_descriptions=[shot],
        characters=[character],
        character_portraits_registry=registry,
        priority_shot_idxs=[],
        style="Anime",
    )

    assert pipe.image_generator.prompts, "generate_single_image was never called"
    prompt = pipe.image_generator.prompts[0]
    assert "Anime" in prompt, "first_frame prompt dropped the style — the first frame of each camera would render 写实, anchoring all follow-on frames to 写实 too"


# ── B5: frame-generation fallback (must not bubble to whole-pipeline retry) ──


@pytest.mark.asyncio
async def test_generate_frame_for_single_shot_fallback_on_image_failure(tmp_path):
    """B5: if generate_single_image raises (seedream 429/5xx/content-review/budget),
    generate_frame_for_single_shot must NOT bubble — it writes a placeholder image
    (solid-color if no reusable frame) + continues, so the failure doesn't cascade
    into a _wrap_pipeline whole-pipeline retry (max_retries=2) that regenerates all
    frames + amplifies render time. Mirrors the video side's B1 (:541-562)."""
    import os

    pipe = _build_pipeline(tmp_path)

    class _FailingImageGenerator:
        async def generate_single_image(self, prompt, reference_image_paths=None, **kwargs):
            raise RuntimeError("simulated seedream 429")

    pipe.image_generator = _FailingImageGenerator()
    character = _trainer()
    registry = {character.identifier_in_scene: {"front": {"path": "/tmp/front.png", "description": "portrait"}}}
    import asyncio

    pipe.frame_events[0] = {"first_frame": asyncio.Event(), "last_frame": asyncio.Event()}

    # Should NOT raise (B5 catches + writes placeholder)
    result = await pipe.generate_frame_for_single_shot(
        shot_idx=0,
        frame_type="last_frame",
        first_shot_ff_path_and_text_pair=("/tmp/nonexistent_front.png", "first frame desc"),
        frame_desc="last frame desc",
        visible_characters=[character],
        character_portraits_registry=registry,
        style="realistic movie style",
    )

    expected_path = os.path.join(str(tmp_path), "shots", "0", "last_frame.png")
    assert result == expected_path
    assert os.path.exists(expected_path), "B5 fallback should write a placeholder image so the pipeline continues without a whole-pipeline retry"
