"""Task 3d: 段落级转场——scene_group 边界用 fade，段内用 base。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from pipelines.video_gen_ai_visual import _pick_transition  # noqa: E402


def test_scene_group_boundary_gets_fade():
    """段落边界帧用 fade（强转场）。"""
    starts = {0, 3}
    assert _pick_transition(3, starts, base="none") == "fade"
    assert _pick_transition(0, starts, base="none") == "fade"


def test_intra_scene_gets_base():
    """段内帧用 base（柔切/无切）。"""
    starts = {0, 3}
    assert _pick_transition(1, starts, base="none") == "none"
    assert _pick_transition(2, starts, base="none") == "none"


def test_no_scene_groups_all_base():
    """无 scene_groups → 全 base。"""
    assert _pick_transition(5, set(), base="none") == "none"
