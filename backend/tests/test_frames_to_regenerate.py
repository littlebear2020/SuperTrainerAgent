"""G2: 渲染续点 only_frames 须类型感知——预览图(image)/冻结帧(image_reuse)不能当成"已生成视频"跳过。

修前 `only_frames=[f for f if not f.visual_url]` 只看 visual_url 有无，把预览图当已生成跳过
→ ai_video 任务里点过预览的帧最终是静态图而非视频。
"""

from app.gateway.video_gen.service import _frames_to_regenerate
from app.gateway.video_gen.storyboard import StoryboardFrame


def _frame(index, visual_url=None, visual_type=None):
    f = StoryboardFrame(index=index, narration=f"n{index}")
    f.visual_url = visual_url
    f.visual_type = visual_type
    return f


def test_ai_video_regenerates_preview_image_and_image_reuse_and_missing():
    """ai_video: 预览图(image)/冻结(image_reuse)/缺失 都重生为视频；仅上次成功的 video 帧跳过。"""
    frames = [
        _frame(0, visual_url="http://a/0.png", visual_type="image"),  # 预览图
        _frame(1, visual_url="http://a/1.png", visual_type="image_reuse"),  # 冻结帧
        _frame(2),  # 无 visual
        _frame(3, visual_url="http://a/3.mp4", visual_type="video"),  # 上次成功的视频
    ]
    regen = _frames_to_regenerate(frames, "ai_video")
    assert regen == [0, 1, 2], f"ai_video 须重生 image/image_reuse/缺失帧(0,1,2)，跳过 video(3)：{regen}"


def test_ai_image_reuses_successful_image_regenerates_reuse_and_missing():
    """ai_image: 成功的 image 帧复用(跳过)；image_reuse/缺失重生。"""
    frames = [
        _frame(0, visual_url="http://a/0.png", visual_type="image"),  # 成功图，复用
        _frame(1, visual_url="http://a/1.png", visual_type="image_reuse"),  # 冻结，重生
        _frame(2),  # 缺失，重生
    ]
    regen = _frames_to_regenerate(frames, "ai_image")
    assert regen == [1, 2], f"ai_image 跳过成功 image(0)，重生 image_reuse/缺失(1,2)：{regen}"


def test_no_visual_all_regenerated():
    """无任何 visual 的全新任务 → 全部重生。"""
    frames = [_frame(0), _frame(1)]
    assert _frames_to_regenerate(frames, "ai_video") == [0, 1]
    assert _frames_to_regenerate(frames, "ai_image") == [0, 1]


def test_ai_visual_mixed_skips_correct_per_frame_type():
    """ai_visual mixed：已成功的 video 帧和 image 帧都正确跳过（不误重生）。

    修前用单一 task_type 判 expected="image"，会把已成功的 video 帧误判需重生。
    修后按逐帧 visual_source 判期望类型。
    """
    frames = [
        _frame(0, visual_url="http://a/0.png", visual_type="image"),  # ai_image 成功
        _frame(1, visual_url="http://a/1.mp4", visual_type="video"),  # ai_video 成功
    ]
    frames[0].visual_source = "ai_image"
    frames[1].visual_source = "ai_video"
    regen = _frames_to_regenerate(frames, "ai_visual")
    assert regen == [], f"ai_visual mixed 两帧都已成功（image+video），不应重生：{regen}"


# ---------------------------------------------------------------------------
# P0-1: 用户上传/替换的本地素材帧不得被列入渲染重生成。
#
# 证据链：上传端点写 local_visual_path + 清 visual_url（routers/video_gen.py）
# → _frames_to_regenerate 首分支 `if not f.visual_url` 把上传帧列入重生
# → fill() 重抽并覆盖 local_visual_path → 上传静默消失。
# 修前首分支只看 visual_url 有无，把"有本地素材但 visual_url 被清空的上传帧"
# 与"真正无视觉的帧"混为一谈。修后：有 local_visual_path 的帧跳过。
# ---------------------------------------------------------------------------


def test_uploaded_local_frame_not_regenerated():
    """上传帧（visual_url=None + local_visual_path 有值）→ 跳过，不重抽（P0-1 核心）。"""
    f = StoryboardFrame(index=0, narration="n0", visual_type="image", local_visual_path="visuals/frame_000.jpg")
    # visual_url 默认 None（上传端点会清空）
    assert _frames_to_regenerate([f], "ai_image") == [], "上传本地帧不得被重抽覆盖"


def test_missing_visual_still_regenerated():
    """真正无视觉的帧（两者皆空）→ 仍列入重生（原行为保留，不得被 P0-1 误伤）。"""
    f = StoryboardFrame(index=0, narration="n0")
    assert _frames_to_regenerate([f], "ai_image") == [0]


def test_image_reuse_still_regenerated_when_local_present():
    """image_reuse 冻结帧有 local 但 visual_url 非空 → 仍重生（G2 语义不变）。"""
    f = StoryboardFrame(
        index=0,
        narration="n0",
        visual_url="http://tos/x.png",
        visual_type="image_reuse",
        local_visual_path="visuals/img_0000.png",
    )
    assert _frames_to_regenerate([f], "ai_image") == [0]


def test_uploaded_local_frame_skipped_in_mixed_batch():
    """混合批次：上传帧跳过，缺失帧重生，类型不符帧重生——三条语义并存。"""
    uploaded = StoryboardFrame(index=0, narration="n0", visual_type="image", local_visual_path="visuals/up.png")
    missing = StoryboardFrame(index=1, narration="n1")
    good_img = StoryboardFrame(index=2, narration="n2", visual_url="http://a/2.png", visual_type="image")
    regen = _frames_to_regenerate([uploaded, missing, good_img], "ai_image")
    assert regen == [1], f"仅缺失帧(1)重生，上传帧(0)跳过、成功图(2)复用：{regen}"
