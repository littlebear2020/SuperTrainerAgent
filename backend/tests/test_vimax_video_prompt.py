"""Tests for ViMax video-gen prompt anchoring (P3a/3b).

3a: the video prompt carries the Visual style directive + visible characters'
    static_features (identity anchor for i2v). Before this, the video prompt
    was bare motion_desc + audio_desc — _with_style_prefix was image-only.
3b: visible characters' front-portrait paths are appended as extra reference
    images (experimental — see plan Risk 5; happyhorse i2v's multi-ref weight
    is unverified).

Most tests exercise build_anchored_video_prompt_and_refs directly (pure
function, no pipeline needed). One integration test verifies the pipeline
method calls the helper with the right args.
"""

import asyncio
import os
import subprocess
from unittest.mock import AsyncMock

import pytest
from vimax._trainerai_video_prompt import build_anchored_video_prompt_and_refs
from vimax.interfaces import CharacterInScene, ShotDescription
from vimax.pipelines.script2video_pipeline import Script2VideoPipeline


def _has_ffmpeg() -> bool:
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
        return True
    except (OSError, subprocess.CalledProcessError):
        return False


def _manager_char() -> CharacterInScene:
    return CharacterInScene(
        idx=0,
        identifier_in_scene="部门主管",
        is_visible=True,
        static_features="中年男性，干净利落的黑色短发，两鬓斑白。",
        dynamic_features="深蓝色长袖衬衫。",
    )


def _shot(idx=0) -> ShotDescription:
    return ShotDescription(
        idx=idx,
        is_last=True,
        cam_idx=0,
        visual_desc="medium shot of two people talking",
        variation_type="small",
        variation_reason="minimal change",
        ff_desc="medium shot",
        ff_vis_char_idxs=[0],
        lf_desc="",
        lf_vis_char_idxs=[],
        motion_desc="部门主管 leans forward and speaks.",
        audio_desc='[Speaker] 部门主管: "你好。"',
    )


def test_build_prompt_carries_style(tmp_path):
    """3a: the prompt carries the Visual style directive."""
    prompt, _ = build_anchored_video_prompt_and_refs(
        shot_description=_shot(),
        working_dir=str(tmp_path),
        characters=[_manager_char()],
        style="Cartoon",
    )
    assert "Cartoon" in prompt
    assert "Visual style" in prompt


def test_build_prompt_carries_character_anchor(tmp_path):
    """3a: the prompt carries the visible character's static_features as anchor."""
    prompt, _ = build_anchored_video_prompt_and_refs(
        shot_description=_shot(),
        working_dir=str(tmp_path),
        characters=[_manager_char()],
        style="Cartoon",
    )
    assert "黑色短发" in prompt, "character static_features must be in the prompt"
    assert "部门主管" in prompt


def test_build_prompt_refs_include_first_frame(tmp_path):
    """The shot's own first_frame.png is always in the reference list."""
    _, refs = build_anchored_video_prompt_and_refs(
        shot_description=_shot(),
        working_dir=str(tmp_path),
        characters=[_manager_char()],
        style="Cartoon",
    )
    assert any("first_frame.png" in r for r in refs), refs


def test_build_prompt_refs_include_portrait(tmp_path):
    """3b: visible character's front portrait path is appended as a reference."""
    portrait = str(tmp_path / "portraits" / "mgr_front.png")
    registry = {"部门主管": {"front": {"path": portrait, "description": "front portrait"}}}
    _, refs = build_anchored_video_prompt_and_refs(
        shot_description=_shot(),
        working_dir=str(tmp_path),
        characters=[_manager_char()],
        style="Cartoon",
        character_portraits_registry=registry,
    )
    assert portrait in refs, "character portrait must be passed as a video reference"


def test_build_prompt_no_characters_no_anchor(tmp_path):
    """No characters → no [Characters] anchor; refs = just first_frame."""
    prompt, refs = build_anchored_video_prompt_and_refs(
        shot_description=_shot(),
        working_dir=str(tmp_path),
        characters=None,
        style=None,
    )
    assert "[Characters]" not in prompt
    assert len(refs) == 1, refs  # only first_frame (small variation, no last_frame)


@pytest.mark.asyncio
async def test_generate_video_for_single_shot_wires_helper_through(tmp_path):
    """Integration: the pipeline method calls build_anchored_video_prompt_and_refs
    and passes style through to the video generator. Catches wiring typos
    (e.g. forgetting to pass style= at the call site)."""
    captured_prompts = []

    class _RecordingVideoGenerator:
        async def generate_single_video(self, prompt, reference_image_paths=None, **kwargs):
            captured_prompts.append(prompt)

            class _Out:
                def save(self, path):
                    os.makedirs(os.path.dirname(path), exist_ok=True)
                    with open(path, "wb") as f:
                        f.write(b"mp4data")

            return _Out()

    pipe = Script2VideoPipeline(
        chat_model=object(),
        image_generator=AsyncMock(),
        video_generator=_RecordingVideoGenerator(),
        working_dir=str(tmp_path),
    )
    pipe.frame_events[0] = {"first_frame": asyncio.Event(), "last_frame": asyncio.Event()}
    pipe.frame_events[0]["first_frame"].set()
    # Pre-create first_frame.png so the shot is "ready".
    (tmp_path / "shots" / "0").mkdir(parents=True)
    (tmp_path / "shots" / "0" / "first_frame.png").write_bytes(b"png")

    await pipe.generate_video_for_single_shot(
        _shot(),
        characters=[_manager_char()],
        style="Cartoon",
        character_portraits_registry={"部门主管": {"front": {"path": str(tmp_path / "mgr.png"), "description": "p"}}},
    )

    assert captured_prompts, "generate_single_video was never called"
    assert "Cartoon" in captured_prompts[0], "style must reach the video prompt"
    assert "黑色短发" in captured_prompts[0], "character anchor must reach the video prompt"


# ── B1: static-frame fallback for failed shots ───────────────────────────


@pytest.mark.skipif(not _has_ffmpeg(), reason="需要 ffmpeg")
def test_make_static_video_from_frame(tmp_path):
    """B1: a PNG frame can be turned into a timed static mp4 via ffmpeg."""
    import subprocess

    from PIL import Image

    from app.gateway.video_gen.engines.vimax._static_frame import make_static_video_from_frame

    frame = tmp_path / "first_frame.png"
    Image.new("RGB", (16, 9), "red").save(frame)
    out = tmp_path / "static.mp4"
    make_static_video_from_frame(str(frame), str(out), duration=2.0)
    assert out.exists() and out.stat().st_size > 0
    dur = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0", str(out)],
        capture_output=True,
        text=True,
        timeout=10,
    ).stdout.strip()
    assert float(dur) == pytest.approx(2.0, abs=0.3)


@pytest.mark.skipif(not _has_ffmpeg(), reason="需要 ffmpeg")
def test_static_fallback_mp4_concatenates_with_real_mp4(tmp_path):
    """B1 integration: a static-fallback mp4 must concat with normal shot mp4s
    without breaking the concat demuxer. Catches encoding/timebase mismatches
    (vg_cda1b579a220 review pointed out the original 1-byte placeholder would
    silently break concat)."""
    import subprocess

    from PIL import Image

    from app.gateway.video_gen.engines.vimax._static_frame import make_static_video_from_frame

    # 1 static fallback (from a PNG) + 2 tiny real mp4s (ffmpeg-generated).
    frame = tmp_path / "ff.png"
    Image.new("RGB", (1920, 1080), "red").save(frame)
    static_mp4 = tmp_path / "static.mp4"
    make_static_video_from_frame(str(frame), str(static_mp4), duration=2.0)

    for name in ("a.mp4", "b.mp4"):
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=blue:s=1920x1080:r=24", "-t", "2", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(tmp_path / name)],
            capture_output=True,
            timeout=30,
            check=True,
        )

    # concat demuxer
    list_file = tmp_path / "list.txt"
    list_file.write_text(f"file '{static_mp4}'\nfile '{tmp_path / 'a.mp4'}'\nfile '{tmp_path / 'b.mp4'}'\n")
    out = tmp_path / "concat.mp4"
    proc = subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file), "-c", "copy", str(out)],
        capture_output=True,
        timeout=60,
    )
    assert proc.returncode == 0, f"concat failed: {proc.stderr.decode()[-400:]}"
    assert out.exists() and out.stat().st_size > 0
    dur = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0", str(out)],
        capture_output=True,
        text=True,
        timeout=10,
    ).stdout.strip()
    assert float(dur) == pytest.approx(6.0, abs=0.6), f"concat duration ~6s (3x2s); got {dur}"


@pytest.mark.skipif(not _has_ffmpeg(), reason="需要 ffmpeg（static fallback 走真实 ffmpeg）")
@pytest.mark.asyncio
async def test_generate_video_for_single_shot_falls_back_to_static_on_failure(tmp_path):
    """B1: when generate_video_for_single_shot raises (after per-shot retry
    exhausted), it catches and writes a static mp4 from first_frame.png — the
    pipeline does NOT abort."""
    import asyncio

    from PIL import Image
    from vimax.interfaces import ShotDescription
    from vimax.pipelines.script2video_pipeline import Script2VideoPipeline

    class _AlwaysFailingVideoGen:
        async def generate_single_video(self, prompt, reference_image_paths=None, **kwargs):
            raise RuntimeError("simulated persistent failure after retries")

    pipe = Script2VideoPipeline(
        chat_model=object(),
        image_generator=AsyncMock(),
        video_generator=_AlwaysFailingVideoGen(),
        working_dir=str(tmp_path),
    )
    pipe.frame_events[0] = {"first_frame": asyncio.Event(), "last_frame": asyncio.Event()}
    pipe.frame_events[0]["first_frame"].set()
    # Pre-create first_frame.png so the static fallback has a source.
    (tmp_path / "shots" / "0").mkdir(parents=True)
    Image.new("RGB", (16, 9), "blue").save(tmp_path / "shots" / "0" / "first_frame.png")

    shot = ShotDescription(
        idx=0,
        is_last=True,
        cam_idx=0,
        visual_desc="v",
        variation_type="small",
        variation_reason="r",
        ff_desc="f",
        ff_vis_char_idxs=[0],
        lf_desc="",
        lf_vis_char_idxs=[],
        motion_desc="m",
        audio_desc='[Speaker] X: "hi"',
    )
    await pipe.generate_video_for_single_shot(shot, characters=[], style="Cartoon")

    video_path = tmp_path / "shots" / "0" / "video.mp4"
    assert video_path.exists(), "static fallback mp4 must be written on failure"


# ── B3: identity-lock directive in first_frame prompt ────────────────────


@pytest.mark.asyncio
async def test_first_frame_prompt_has_identity_lock_instruction(tmp_path):
    """B3: the first_frame prompt must include a strong identity-consistency
    directive telling the image model to match the portrait reference exactly.
    NanoBanana drifts across shots without it (vg_cda1b579a220: 61/63 shots
    selected portraits but character still drifted).

    NOTE: this is a string-presence test, not an effect test — it proves the
    directive was added, not that the model obeys it. Real consistency must be
    assessed by running a real task and watching the video."""
    import asyncio

    from vimax.interfaces import CharacterInScene
    from vimax.pipelines.script2video_pipeline import Script2VideoPipeline

    captured_prompts = []

    class _RecordingImageGen:
        async def generate_single_image(self, prompt, reference_image_paths=None, **kwargs):
            captured_prompts.append(prompt)

            class _Out:
                def save(self, path):
                    import os

                    from PIL import Image

                    os.makedirs(os.path.dirname(path), exist_ok=True)
                    Image.new("RGB", (16, 9)).save(path)

            return _Out()

    pipe = Script2VideoPipeline(
        chat_model=object(),
        image_generator=_RecordingImageGen(),
        video_generator=AsyncMock(),
        working_dir=str(tmp_path),
    )
    pipe.reference_image_selector = AsyncMock()
    pipe.reference_image_selector.select_reference_images_and_generate_prompt = AsyncMock(
        return_value={
            "reference_image_path_and_text_pairs": [("/tmp/front.png", "front portrait of 部门主管")],
            "text_prompt": "medium shot of 部门主管 sitting",
        }
    )
    pipe.frame_events[0] = {"first_frame": asyncio.Event(), "last_frame": asyncio.Event()}
    pipe.frame_events[0]["first_frame"].set()
    (tmp_path / "shots" / "0").mkdir(parents=True)

    char = CharacterInScene(
        idx=0,
        identifier_in_scene="部门主管",
        is_visible=True,
        static_features="中年男性，黑色短发",
        dynamic_features="深蓝衬衫",
    )
    await pipe.generate_frame_for_single_shot(
        shot_idx=0,
        frame_type="last_frame",
        first_shot_ff_path_and_text_pair=("/tmp/ff.png", "ff desc"),
        frame_desc="last frame desc",
        visible_characters=[char],
        character_portraits_registry={"部门主管": {"front": {"path": "/tmp/front.png", "description": "p"}}},
        style="Cartoon",
    )
    assert captured_prompts, "generate_single_image was never called"
    prompt = captured_prompts[-1]
    # The identity-lock directive must appear.
    assert "identity" in prompt.lower() or "完全一致" in prompt, "prompt must carry a strong identity-consistency directive"


# ── 2026-07-22 Task 11: 3b A/B 开关（VIMAX_3B_PORTRAIT_REFS）──────────────

_REGISTRY_TOGGLE = {"部门主管": {"front": {"path": "/portraits/front.png", "description": "front view"}}}


def _build_toggle(tmp_path):
    return build_anchored_video_prompt_and_refs(
        shot_description=_shot(),
        working_dir=str(tmp_path),
        characters=[_manager_char()],
        style="realistic movie style",
        character_portraits_registry=_REGISTRY_TOGGLE,
    )


def test_3b_portrait_refs_on_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("VIMAX_3B_PORTRAIT_REFS", raising=False)
    _, refs = _build_toggle(tmp_path)
    assert "/portraits/front.png" in refs  # 默认开，保持现状


def test_3b_portrait_refs_disabled_by_env(tmp_path, monkeypatch):
    monkeypatch.setenv("VIMAX_3B_PORTRAIT_REFS", "off")
    _, refs = _build_toggle(tmp_path)
    assert "/portraits/front.png" not in refs
    # 首帧参考仍在——关掉的只是 3b 肖像追加，不是基础参考
    assert any("first_frame.png" in r for r in refs)


def test_3b_prompt_anchor_unaffected_by_switch(tmp_path, monkeypatch):
    """开关只影响 3b 参考图，3a 的 prompt 身份锚定（[Characters] + Visual style）不变。"""
    monkeypatch.setenv("VIMAX_3B_PORTRAIT_REFS", "off")
    prompt, _ = _build_toggle(tmp_path)
    assert "黑色短发" in prompt
    assert prompt.startswith("Visual style: realistic movie style.")
