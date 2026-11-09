"""Regression tests for ViMax camera-tree validation (vg_78a149025efe).

Root cause: ``construct_camera_tree`` asks the LLM (now gemini-3.6-flash) to emit a
``CameraTreeResponse`` — a parent_cam_idx per camera. gemini occasionally returns a
camera that points at ITSELF as its parent (parent_cam_idx == idx). The old
``_validate_camera_tree`` raised ``ValueError("Camera N lists itself as its parent")``
with NO retry and NO auto-fix → the whole video task failed at 15%.

Two fixes:
  1. ``_validate_camera_tree`` now AUTO-CORRECTS a self-parent to None (a camera
     pointing at itself is semantically "no parent" — a harmless data quirk, not a
     fatal error). Cycles and unknown parents still raise (those would deadlock).
  2. ``construct_camera_tree`` gains ``@retry`` (mirrors every other ViMax LLM agent
     — reference_image_selector, scene_extractor, etc. all have it) so a malformed
     LLM response gets re-generated instead of failing the task.

These touch vendored ``packages/vimax`` (plan §2.1 allows vendor edits).
"""

from unittest.mock import MagicMock

import pytest
from vimax.interfaces import Camera

# ── Fix 1: _validate_camera_tree auto-corrects self-parent ────────────────────


def test_validate_camera_tree_auto_corrects_self_parent_to_none():
    """A camera whose parent_cam_idx == its own idx is semantically 'no parent'.
    It must be auto-corrected to None, not raise — this was the vg_78a149025efe crash."""
    from vimax.agents.camera_image_generator import _validate_camera_tree

    cameras = [
        Camera(idx=0, active_shot_idxs=[0], parent_cam_idx=0),  # ← self-parent (the bug)
        Camera(idx=1, active_shot_idxs=[1], parent_cam_idx=0),  # normal child of cam 0
    ]

    # must NOT raise
    _validate_camera_tree(cameras)

    # self-parent corrected to None (treated as a root camera)
    assert cameras[0].parent_cam_idx is None
    # the normal child is untouched
    assert cameras[1].parent_cam_idx == 0


def test_validate_camera_tree_still_raises_on_cycle():
    """A genuine cycle (A→B→A) must still raise — it would deadlock frame generation.
    Self-parent auto-fix must NOT mask real cycles."""
    from vimax.agents.camera_image_generator import _validate_camera_tree

    cameras = [
        Camera(idx=0, active_shot_idxs=[0], parent_cam_idx=1),  # 0 → 1
        Camera(idx=1, active_shot_idxs=[1], parent_cam_idx=0),  # 1 → 0  (cycle!)
    ]
    with pytest.raises(ValueError, match="Cycle"):
        _validate_camera_tree(cameras)


def test_validate_camera_tree_still_raises_on_unknown_parent():
    """A parent pointing at a nonexistent camera must still raise."""
    from vimax.agents.camera_image_generator import _validate_camera_tree

    cameras = [Camera(idx=0, active_shot_idxs=[0], parent_cam_idx=99)]  # 99 doesn't exist
    with pytest.raises(ValueError, match="unknown parent"):
        _validate_camera_tree(cameras)


def test_validate_camera_tree_passes_valid_tree():
    """A well-formed tree passes unchanged."""
    from vimax.agents.camera_image_generator import _validate_camera_tree

    cameras = [
        Camera(idx=0, active_shot_idxs=[0], parent_cam_idx=None),  # root
        Camera(idx=1, active_shot_idxs=[1], parent_cam_idx=0),
        Camera(idx=2, active_shot_idxs=[2], parent_cam_idx=0),
    ]
    _validate_camera_tree(cameras)
    assert cameras[0].parent_cam_idx is None
    assert cameras[1].parent_cam_idx == 0
    assert cameras[2].parent_cam_idx == 0


# ── Fix 2: construct_camera_tree retries on a malformed LLM response ──────────


@pytest.mark.asyncio
async def test_construct_camera_tree_retries_on_malformed_response():
    """If the LLM returns a malformed response (wrong item count — not auto-correctable),
    construct_camera_tree must retry instead of failing the whole task. Mirror the @retry
    pattern every other ViMax agent uses.

    Note: a self-parent is now auto-corrected by _validate_camera_tree (no retry needed),
    so this test uses a count-mismatch error — the OTHER validation path that can't be
    auto-fixed and genuinely needs a re-generation."""
    from vimax.agents.camera_image_generator import CameraImageGenerator
    from vimax.interfaces import ShotDescription

    gen = CameraImageGenerator(chat_model=MagicMock(), image_generator=MagicMock(), video_generator=MagicMock())

    cameras = [Camera(idx=0, active_shot_idxs=[0, 1])]
    shot_descs = [
        ShotDescription(idx=0, is_last=False, cam_idx=0, visual_desc="wide establishing shot", variation_type="large", variation_reason="root", ff_desc="x", lf_desc="x", motion_desc="y", audio_desc="z"),
        ShotDescription(idx=1, is_last=False, cam_idx=0, visual_desc="close-up of character", variation_type="medium", variation_reason="root", ff_desc="x", lf_desc="x", motion_desc="y", audio_desc="z"),
    ]

    call_count = 0

    class _FlakyChain:
        """First invoke returns the wrong number of items (raises), second returns valid."""

        def __or__(self, other):
            return self

        async def ainvoke(self, messages):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # malformed: 0 items for 1 camera → count mismatch ValueError
                return _CameraTreeResponseMother.empty()
            # valid: 1 root item for 1 camera
            return _CameraTreeResponseMother.root()

    gen.chat_model = _FlakyChain()

    result = await gen.construct_camera_tree(cameras=cameras, shot_descs=shot_descs)

    assert call_count == 2  # 1 initial (malformed) + 1 retry (valid)
    assert result[0].parent_cam_idx is None  # the retry's valid response took effect


class _CameraTreeResponseMother:
    """Build raw dicts that PydanticOutputParser would produce from the LLM.

    construct_camera_tree zips response.camera_parent_items onto the cameras, then
    validates. We return the pydantic-shaped object directly (the parser is bypassed
    by mocking the chain)."""

    @staticmethod
    def _items(items):
        resp = MagicMock()
        resp.camera_parent_items = items
        return resp

    @staticmethod
    def self_parent():
        item = MagicMock()
        item.parent_cam_idx = 0  # camera 0 points at itself (the bug)
        item.parent_shot_idx = None
        item.reason = "root"
        item.is_parent_fully_covers_child = None
        item.missing_info = None
        return _CameraTreeResponseMother._items([item])

    @staticmethod
    def empty():
        # 0 items → count mismatch with the 1 camera → ValueError (not auto-correctable)
        return _CameraTreeResponseMother._items([])

    @staticmethod
    def root():
        item = MagicMock()
        item.parent_cam_idx = None  # valid root
        item.parent_shot_idx = None
        item.reason = "root"
        item.is_parent_fully_covers_child = None
        item.missing_info = None
        return _CameraTreeResponseMother._items([item])
