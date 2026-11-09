"""remotion_course 独立管线 T4：槽位过滤视觉源分派矩阵（§7）。

版式 × visual_source × prompt × local_visual 四维组合（mock 子源）：
- 槽位生图条件（图槽 + ai_image + prompt + 无 local）
- 嵌入图帧零调用
- ai_video 支路仅命中用户显式设置的 VideoClipPage 帧
- 其余帧零调用（模板渲染占位/播放键占位）
- AI 管线专属通道 kwargs 接收并忽略（roster/anchor 不炸）
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame
from app.gateway.video_gen.visual_sources.remotion_course_visuals import RemotionCourseVisualSource


def _source() -> RemotionCourseVisualSource:
    img = MagicMock()
    img.render = AsyncMock()
    vid = MagicMock()
    vid.render = AsyncMock()
    return RemotionCourseVisualSource(image_source=img, video_source=vid), img, vid


def _sb(frames: list[StoryboardFrame]) -> Storyboard:
    return Storyboard(
        title="t",
        config=StoryboardConfig(video_type="remotion_course", width=1920, height=1080),
        frames=frames,
    )


def _f(i: int, layout=None, **kw) -> StoryboardFrame:
    return StoryboardFrame(index=i, narration=f"帧{i}旁白。", layout=layout, **kw)


@pytest.mark.asyncio
async def test_dispatch_matrix():
    src, img, vid = _source()
    sb = _sb(
        [
            _f(0, "ImageTextRight", visual_source="ai_image", image_prompt="要点并列示意图"),  # → 生图
            _f(1, "ConceptPage", visual_source="ai_image", image_prompt="不该命中"),  # 非图槽
            _f(2, "ImageTextLeft", visual_source="ai_image", image_prompt="有词"),  # 无 prompt 字段空? 有 prompt 无 local → 命中
            _f(3, "ImageTextLeft", visual_source="ai_image", image_prompt="嵌入图帧", local_visual_path="visuals/embedded/img_1.png"),  # 有嵌入图
            _f(4, "QuotePage", image_prompt="无 source 帧"),  # visual_source=None
            _f(5, "VideoClipPage", visual_source="ai_video", image_prompt="用户显式视频"),  # → 生视频
            _f(6, "ConceptPage", visual_source="ai_video", image_prompt="非 VideoClipPage 的 ai_video"),  # 版式不符
            _f(7, "VideoClipPage", visual_source="ai_video", image_prompt="已上传视频", local_visual_path="visuals/frame_007.mp4"),  # 已有素材
            _f(8, "EndingPage", visual_source="ai_image", image_prompt="  "),  # 空 prompt
        ]
    )
    await src.render(sb)
    img.render.assert_awaited_once()
    # M1（2026-09-11 审查）：子源收**真过滤视图**（分母=目标集），不再透传
    # only_frames——覆盖率闸（_coverage_scope）不认 only_frames，透传整表会把
    # 残留 visual_source 的非目标帧计成失败。
    view = img.render.call_args.args[0]
    assert [f.index for f in view.frames] == [0, 2]
    assert "only_frames" not in img.render.call_args.kwargs
    vid.render.assert_awaited_once()
    assert [f.index for f in vid.render.call_args.args[0].frames] == [5]


@pytest.mark.asyncio
async def test_filtered_view_excludes_stale_source_frames():
    """M1 回归：上传替换/清空配图词后 visual_source 残留的帧不进覆盖率分母。"""
    src, img, vid = _source()
    sb = _sb(
        [
            _f(0, "ImageTextRight", visual_source="ai_image", image_prompt="真目标"),
            # 残留源帧：用户上传替换（upload-visual 不动 visual_source）→ 永不生成
            _f(1, "ImageTextLeft", visual_source="ai_image", image_prompt="旧词", local_visual_path="visuals/frame_001.png"),
        ]
    )
    await src.render(sb)
    view = img.render.call_args.args[0]
    assert [f.index for f in view.frames] == [0]
    # 视图与原 storyboard 共享帧对象（生成产物写回原表）且保留 config
    assert view.frames[0] is sb.frames[0]
    assert view.config is sb.config


@pytest.mark.asyncio
async def test_only_frames_intersection():
    """only_frames 与条件取交集（续点过滤由调用方传入，本源做交）。"""
    src, img, vid = _source()
    sb = _sb(
        [
            _f(0, "ImageTextRight", visual_source="ai_image", image_prompt="a"),
            _f(1, "ImageTextLeft", visual_source="ai_image", image_prompt="b"),
        ]
    )
    await src.render(sb, only_frames=[1])
    assert [f.index for f in img.render.call_args.args[0].frames] == [1]
    vid.render.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_targets_zero_calls():
    src, img, vid = _source()
    sb = _sb([_f(0, "ConceptPage"), _f(1, "SummaryPage")])
    await src.render(sb)
    img.render.assert_not_awaited()
    vid.render.assert_not_awaited()


@pytest.mark.asyncio
async def test_ai_pipeline_kwargs_ignored():
    """roster/anchor/ref kwargs 接收并忽略（宽签名兼容，不炸）。"""
    src, img, vid = _source()
    sb = _sb([_f(0, "ImageTextRight", visual_source="ai_image", image_prompt="a")])
    await src.render(
        sb,
        task_dir=None,
        reference_image_paths=["/x/front.png"],
        style_anchor_image_paths=["/x/anchor.png"],
        character_roster={"主角": {"front_url": "u"}},
        prop_roster={"道具": {"main_image": "u"}},
    )
    img.render.assert_awaited_once()
    # 子源转发不含 AI 管线专属通道
    kw = img.render.call_args.kwargs
    assert "character_roster" not in kw and "style_anchor_image_paths" not in kw and "reference_image_paths" not in kw


@pytest.mark.asyncio
async def test_subsource_failure_fail_loud():
    src, img, vid = _source()
    img.render = AsyncMock(side_effect=RuntimeError("provider down"))
    sb = _sb([_f(0, "ImageTextRight", visual_source="ai_image", image_prompt="a")])
    with pytest.raises(RuntimeError, match="remotion_course 视觉源渲染失败"):
        await src.render(sb)


# ── 条目 3（2026-09-12 缺陷批）：残留旧图帧「重新生成视频」守卫判定矩阵 ──────


def test_residual_visual_regen_block_matrix():
    """判定矩阵四行：缺陷 A 拒 / 正常视频重生放行 / 变体 B 拒 / 嵌入上传帧交 P6。

    service 层 ValueError 与 router 同步 400 共用本谓词（两层同文案）。
    """
    from app.gateway.video_gen.visual_sources.remotion_course_visuals import (
        RESIDUAL_VISUAL_BLOCK_MESSAGE,
        residual_visual_regen_block_reason,
    )

    # 缺陷 A：VideoClipPage + ai_video + 残留预览图（visual_type=="image"）→ 拒
    fa = _f(0, "VideoClipPage", visual_source="ai_video", image_prompt="视频", local_visual_path="visuals/img_0001.png", visual_type="image")
    assert residual_visual_regen_block_reason("remotion_course", fa) == RESIDUAL_VISUAL_BLOCK_MESSAGE
    # 正常视频重生：已本地化视频（visual_type=="video"）→ 放行
    fv = _f(1, "VideoClipPage", visual_source="ai_video", image_prompt="视频", local_visual_path="visuals/vid_0001.mp4", visual_type="video")
    assert residual_visual_regen_block_reason("remotion_course", fv) is None
    # 变体 B：VideoClipPage + ai_image + 残留 AI 图（visuals/img_）→ 拒
    fb = _f(2, "VideoClipPage", visual_source="ai_image", image_prompt="旧图", local_visual_path="visuals/img_0002.png")
    assert residual_visual_regen_block_reason("remotion_course", fb) == RESIDUAL_VISUAL_BLOCK_MESSAGE
    # 嵌入/上传帧：P6 既有守卫负责（本谓词跳过，不重复拦截）
    fe = _f(3, "VideoClipPage", visual_source="ai_image", image_prompt="嵌入", local_visual_path="visuals/embedded/img_1.png")
    assert residual_visual_regen_block_reason("remotion_course", fe) is None
    fu = _f(4, "VideoClipPage", visual_source="ai_video", image_prompt="上传", local_visual_path="visuals/frame_007.mp4")
    assert residual_visual_regen_block_reason("remotion_course", fu) is None
    # 非 VideoClipPage / 非 remotion_course / 无 local → 放行
    assert residual_visual_regen_block_reason("remotion_course", _f(5, "ConceptPage", visual_source="ai_image", image_prompt="x", local_visual_path="visuals/img_0003.png")) is None
    assert residual_visual_regen_block_reason("ai_video", fa) is None
    assert residual_visual_regen_block_reason("remotion_course", _f(6, "VideoClipPage", visual_source="ai_video", image_prompt="无local")) is None
    # dict 帧（router 读 storyboard.json 原始帧）与 attr 帧同判定
    fd = {"index": 7, "layout": "VideoClipPage", "visual_source": "ai_video", "image_prompt": "视频", "local_visual_path": "visuals/img_0004.png", "visual_type": "image"}
    assert residual_visual_regen_block_reason("remotion_course", fd) == RESIDUAL_VISUAL_BLOCK_MESSAGE
    # 病态态：local 存在但 visual_type 缺失 → 视同残留（宁可误拒一次不静默清素材）
    fn = _f(8, "VideoClipPage", visual_source="ai_video", image_prompt="视频", local_visual_path="visuals/img_0005.png")
    assert residual_visual_regen_block_reason("remotion_course", fn) == RESIDUAL_VISUAL_BLOCK_MESSAGE
