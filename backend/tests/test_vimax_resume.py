"""Resume / breakpoint-continuation tests for the ViMax pipeline (2.10).

ViMax persists intermediate artifacts under working_dir. On a re-run (e.g. after
a gateway restart mid-task — what happened to vg_0362585808e1 /
vg_920532585472 in real use), the pipeline must reuse existing artifacts instead
of re-generating them: the built-in resume mechanism (plan §5.4).

These tests drive the REAL ``Script2VideoPipeline.__call__`` /
``generate_video_for_single_shot`` with LLM + generators mocked at the provider
boundary (chat_model is an inert object; image/video generators are recording
fakes). Intermediate artifacts are pre-seeded in a tmp working_dir so the resume
checkpoints fire; recording spies prove the regeneration path is skipped.
Each checkpoint has a control test (artifact absent → regeneration happens) so
deleting the ``os.path.exists`` guard turns the suite red.

Live checkpoints asserted here (some resume checks in script2video are
commented out — only the live ones are pinned):
  - script2video_pipeline.py __call__: final_video.mp4 exists → skip concat
  - script2video_pipeline.py __call__: character_portraits_registry.json exists
    → load, skip generate_character_portraits
  - script2video_pipeline.py generate_video_for_single_shot: shots/{idx}/video.mp4
    exists → skip video generation
"""

import asyncio
import json

import pytest
from vimax.interfaces import CharacterInScene, ShotDescription
from vimax.pipelines.script2video_pipeline import Script2VideoPipeline


def _make_pipeline(working_dir):
    """Real pipeline with LLM/generators mocked at the provider boundary."""
    from unittest.mock import AsyncMock

    return Script2VideoPipeline(
        chat_model=object(),  # inert: every LLM stage below resumes from disk
        image_generator=AsyncMock(),
        video_generator=AsyncMock(),
        working_dir=str(working_dir),
    )


def _seed_text_plan_artifacts(working_dir, characters):
    """Pre-seed the disk artifacts every text-planning stage resumes from."""
    (working_dir / "characters.json").write_text(
        json.dumps([c.model_dump() for c in characters], ensure_ascii=False),
        encoding="utf-8",
    )
    (working_dir / "storyboard.json").write_text("[]", encoding="utf-8")
    (working_dir / "camera_tree.json").write_text("[]", encoding="utf-8")


def _event_recorder():
    events: list[tuple[str, dict]] = []

    def record(stage, _message, metadata):
        events.append((stage, metadata or {}))

    return events, record


def _manager_char() -> CharacterInScene:
    return CharacterInScene(
        idx=0,
        identifier_in_scene="小明",
        is_visible=True,
        static_features="中年男性，黑色短发。",
        dynamic_features="深蓝色衬衫。",
    )


@pytest.mark.asyncio
async def test_final_video_existence_skips_concatenation(tmp_path, monkeypatch):
    """final_video.mp4 already in working_dir → __call__ emits 'final_video_exists'
    and never calls concatenate_video_files (live checkpoint: a finished render
    is not re-concatenated after a restart)."""
    pipe = _make_pipeline(tmp_path)
    _seed_text_plan_artifacts(tmp_path, characters=[])
    (tmp_path / "character_portraits_registry.json").write_text("{}", encoding="utf-8")
    final_path = tmp_path / "final_video.mp4"
    final_path.write_bytes(b"fake mp4")
    # Mutating away the `if os.path.exists(final_video_path)` guard must turn
    # this red: the spy would be called.
    calls: list = []
    monkeypatch.setattr(
        "vimax.pipelines.script2video_pipeline.concatenate_video_files",
        lambda *args, **kwargs: calls.append(args),
    )
    events, record = _event_recorder()

    result = await pipe(script="s", user_requirement="r", style="realistic", quiet=True, progress=record)

    assert result == str(final_path)
    stages = [s for s, _ in events]
    assert "final_video_exists" in stages
    assert "concat_start" not in stages
    assert calls == [], "final_video.mp4 已存在时不得重新拼接"


@pytest.mark.asyncio
async def test_missing_final_video_triggers_concatenation(tmp_path, monkeypatch):
    """Control: without final_video.mp4 the same single-shot pipeline DOES concatenate.

    The non-empty clip keeps this focused on the final-video checkpoint; the empty-list
    fail-loud contract is pinned separately in test_vimax_vendored_remediation.py.
    """
    pipe = _make_pipeline(tmp_path)
    _seed_text_plan_artifacts(tmp_path, characters=[])
    (tmp_path / "storyboard.json").write_text(
        json.dumps(
            [
                {
                    "idx": 0,
                    "is_last": True,
                    "cam_idx": 0,
                    "visual_desc": "medium shot",
                    "audio_desc": "sound",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (tmp_path / "camera_tree.json").write_text(
        json.dumps([{"idx": 0, "active_shot_idxs": [0]}], ensure_ascii=False),
        encoding="utf-8",
    )
    shot_dir = tmp_path / "shots" / "0"
    shot_dir.mkdir(parents=True)
    (shot_dir / "shot_description.json").write_text(json.dumps(_shot().model_dump(), ensure_ascii=False), encoding="utf-8")
    (shot_dir / "first_frame.png").write_bytes(b"fake png")
    shot_video = shot_dir / "video.mp4"
    shot_video.write_bytes(b"fake mp4")
    (tmp_path / "character_portraits_registry.json").write_text("{}", encoding="utf-8")
    calls: list = []

    def fake_concat(paths, out_path):
        calls.append((paths, out_path))

    monkeypatch.setattr("vimax.pipelines.script2video_pipeline.concatenate_video_files", fake_concat)
    events, record = _event_recorder()

    await pipe(script="s", user_requirement="r", style="realistic", quiet=True, progress=record)

    assert len(calls) == 1
    assert calls[0][0] == [str(shot_video)]
    assert calls[0][1] == str(tmp_path / "final_video.mp4")
    stages = [s for s, _ in events]
    assert "concat_done" in stages


@pytest.mark.asyncio
async def test_character_portraits_registry_loaded_from_disk_when_present(tmp_path, monkeypatch):
    """character_portraits_registry.json exists → pipeline loads it and does NOT
    regenerate portraits (live resume checkpoint; a restart after portrait
    generation must not re-burn image API quota)."""
    pipe = _make_pipeline(tmp_path)
    characters = [_manager_char()]
    _seed_text_plan_artifacts(tmp_path, characters)
    existing = {"小明": {"front": {"path": "/tmp/x.png", "description": "front"}}}
    (tmp_path / "character_portraits_registry.json").write_text(json.dumps(existing, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "final_video.mp4").write_bytes(b"fake mp4")
    gen_calls: list = []

    async def _spy_generate(**kwargs):
        gen_calls.append(kwargs)
        return {}

    monkeypatch.setattr(pipe, "generate_character_portraits", _spy_generate)
    monkeypatch.setattr(
        "vimax.pipelines.script2video_pipeline.concatenate_video_files",
        lambda *args, **kwargs: None,
    )
    events, record = _event_recorder()

    await pipe(script="s", user_requirement="r", style="realistic", quiet=True, progress=record)

    assert gen_calls == [], "registry 已在盘上时不得重新生成角色肖像"
    loaded = [m for s, m in events if s == "character_portraits_loaded"]
    assert loaded and loaded[0].get("count") == 1


@pytest.mark.asyncio
async def test_missing_registry_triggers_portrait_generation(tmp_path, monkeypatch):
    """Control: registry file absent → generate_character_portraits IS called."""
    pipe = _make_pipeline(tmp_path)
    characters = [_manager_char()]
    _seed_text_plan_artifacts(tmp_path, characters)
    (tmp_path / "final_video.mp4").write_bytes(b"fake mp4")
    gen_calls: list = []

    async def _spy_generate(**kwargs):
        gen_calls.append(kwargs)
        return {}

    monkeypatch.setattr(pipe, "generate_character_portraits", _spy_generate)
    monkeypatch.setattr(
        "vimax.pipelines.script2video_pipeline.concatenate_video_files",
        lambda *args, **kwargs: None,
    )

    await pipe(script="s", user_requirement="r", style="realistic", quiet=True, progress=_event_recorder()[1])

    assert len(gen_calls) == 1


def _shot(idx=0) -> ShotDescription:
    return ShotDescription(
        idx=idx,
        is_last=True,
        cam_idx=0,
        visual_desc="medium shot",
        variation_type="small",
        variation_reason="minimal change",
        ff_desc="medium shot",
        ff_vis_char_idxs=[0],
        lf_desc="",
        lf_vis_char_idxs=[],
        motion_desc="小明 speaks.",
        audio_desc='[Speaker] 小明: "你好。"',
    )


@pytest.mark.asyncio
async def test_shot_video_file_is_per_shot_resume_checkpoint(tmp_path):
    """shots/{idx}/video.mp4 exists → generate_video_for_single_shot emits
    'video_clip_exists' and never touches the video generator. Per-shot
    granularity is what lets a mid-render restart finish only the missing shots."""
    pipe = _make_pipeline(tmp_path)
    shot_dir = tmp_path / "shots" / "0"
    shot_dir.mkdir(parents=True)
    (shot_dir / "video.mp4").write_bytes(b"fake mp4")
    events, record = _event_recorder()

    await pipe.generate_video_for_single_shot(_shot(), progress=record, characters=[], style="Cartoon")

    pipe.video_generator.generate_single_video.assert_not_called()
    stages = [s for s, _ in events]
    assert "video_clip_exists" in stages
    assert "video_clip_start" not in stages


@pytest.mark.asyncio
async def test_missing_shot_video_triggers_generation(tmp_path):
    """Control: video.mp4 absent → the video generator IS called and writes
    shots/{idx}/video.mp4."""
    pipe = _make_pipeline(tmp_path)
    shot_dir = tmp_path / "shots" / "0"
    shot_dir.mkdir(parents=True)
    (shot_dir / "first_frame.png").write_bytes(b"png")
    pipe.frame_events[0] = {"first_frame": asyncio.Event()}
    pipe.frame_events[0]["first_frame"].set()

    gen_calls: list = []

    class _Out:
        def save(self, path):
            with open(path, "wb") as f:
                f.write(b"mp4data")

    async def fake_generate(prompt, reference_image_paths=None, **kwargs):
        gen_calls.append(prompt)
        return _Out()

    pipe.video_generator.generate_single_video = fake_generate
    events, record = _event_recorder()

    await pipe.generate_video_for_single_shot(_shot(), progress=record, characters=[], style="Cartoon")

    assert len(gen_calls) == 1
    assert (shot_dir / "video.mp4").read_bytes() == b"mp4data"
    stages = [s for s, _ in events]
    assert "video_clip_done" in stages
