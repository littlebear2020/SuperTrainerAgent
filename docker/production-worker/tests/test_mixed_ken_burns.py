"""Task 11: mixed 管线的图转视频必须真有 Ken Burns。"""

import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from pipelines import _ffmpeg_helpers as H  # noqa: E402
from pipelines import video_gen_mixed  # noqa: E402


def test_image_to_video_uses_zoompan() -> None:
    """docstring 声称 Ken Burns，滤镜链就必须真有 zoompan。

    修前：docstring 写 "with Ken Burns effect"，实际只有 scale/crop/format —— 文档说谎。
    真实现在 video_gen_ai_visual._build_zoompan_vf（v1），本管线未复用。
    """
    src = inspect.getsource(video_gen_mixed._image_to_video)
    assert "zoompan" in src or "_build_zoompan_vf" in src, "mixed 管线的 _image_to_video 缺 zoompan（Ken Burns）"


def test_docstring_matches_behavior() -> None:
    doc = video_gen_mixed._image_to_video.__doc__ or ""
    src = inspect.getsource(video_gen_mixed._image_to_video)
    if "Ken Burns" in doc:
        assert "zoompan" in src or "_build_zoompan_vf" in src, "docstring 承诺 Ken Burns 但代码没有"


def test_zoompan_builder_lives_in_helpers() -> None:
    """单一实现：搬到 _ffmpeg_helpers，三条管线共用。

    不复制代码——v1 那份带着「crop 替代方案已被 ffmpeg 8.1.2 证伪」的实测结论注释，
    复制会让结论分叉。
    """
    assert hasattr(H, "_build_zoompan_vf")
    assert hasattr(H, "_ZOOM_OVERSAMPLE")


def test_v1_reuses_helpers_version() -> None:
    """v1 不得保留自己的副本（否则两份会漂移）。"""
    from pipelines import video_gen_ai_visual as av

    assert av._build_zoompan_vf is H._build_zoompan_vf, "v1 应直接复用 helpers 的实现"


def test_zoompan_vf_normalizes_rate_by_duration() -> None:
    """行程按 duration 归一化——固定速率会让长帧撞上限后静止（PPT 卡住）。"""
    short = H._build_zoompan_vf(width=640, height=360, duration=2.0, frame_index=0)
    long = H._build_zoompan_vf(width=640, height=360, duration=12.0, frame_index=0)
    assert short != long, "不同时长应得到不同 zoom 速率"
    assert "zoompan" in short and "fps=30" in short


def test_zoompan_alternates_direction() -> None:
    """偶数帧推近、奇数帧拉远，破除整片单调。"""
    even = H._build_zoompan_vf(width=640, height=360, duration=5.0, frame_index=0)
    odd = H._build_zoompan_vf(width=640, height=360, duration=5.0, frame_index=1)
    assert even != odd
    assert "min(zoom+" in even
    assert "max(zoom-" in odd


def test_mixed_passes_frame_index_for_alternation() -> None:
    """mixed 必须把帧序号传下去，否则全片同向（方向交替失效）。"""
    sig = inspect.signature(video_gen_mixed._image_to_video)
    assert "frame_index" in sig.parameters, "缺 frame_index 参数，方向交替无从生效"
    caller_src = inspect.getsource(video_gen_mixed)
    assert "frame_index=" in caller_src, "调用点未传 frame_index"
