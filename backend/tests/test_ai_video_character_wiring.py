"""B3': ai_video 接入角色 i2v（投诉 5 ai_video）。

修前 ``fill()`` 纯 t2v——``generate_video`` 只传 prompt+duration，完全无视
``reference_image_paths``（render 的 :58-59 注释明写"接受但忽略"）。接入后把角色参考图作
``image_urls`` 传给 seedance（first_frame + reference_image，B0），实现 ai_video 角色一致性
（seedance 不拦 seedream 生成的角色图，信任产物）。
"""

from unittest.mock import MagicMock

import pytest

from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame
from app.gateway.video_gen.visual_sources.ai_video import AIVideoVisualSource


def _sb(n: int = 2) -> Storyboard:
    cfg = StoryboardConfig(video_type="ai_video", width=720, height=1280)
    frames = [StoryboardFrame(index=i, narration=f"n{i}", duration=5.0, image_prompt=f"p{i}") for i in range(n)]
    return Storyboard(title="t", config=cfg, frames=frames)


@pytest.mark.asyncio
async def test_character_refs_passed_as_image_urls(tmp_path) -> None:
    seen: list[dict] = []

    async def fake_gen_video(prompt, *, duration=5.0, image_url=None, image_urls=None, **kw):  # noqa: ANN001,ANN003
        seen.append({"prompt": prompt, "image_urls": image_urls})
        return MagicMock(url=f"http://vid/{prompt}")

    sb = _sb(2)
    # P0-2: MagicMock 需显式声明 max_video_duration（否则 auto-attr 让 min() TypeError）
    src = AIVideoVisualSource(MagicMock(generate_video=fake_gen_video, max_video_duration=8.0), concurrency=1)
    await src.render(sb, task_dir=tmp_path, reference_image_paths=["/f.png", "/s.png", "/b.png"])

    assert len(seen) == 2, f"每帧都应调 generate_video，实际 {seen}"
    assert all(s["image_urls"] == ["/f.png", "/s.png", "/b.png"] for s in seen), f"应把三视图作 image_urls 传入（B0 分配 first_frame+reference_image），实际 {seen}"


@pytest.mark.asyncio
async def test_no_refs_and_no_preview_passes_none_image_urls(tmp_path) -> None:
    """无角色参考**且无预览图**时纯 t2v。

    2026-08-03 Task 5：原断言「无角色参考即纯 t2v」已收窄——帧自带预览图
    (visual_type=image + local_visual_path) 时会作 i2v 首帧继承构图（消除
    「预览图与成片两次独立生成、构图不一致」）。本测试因此显式说明它构造的是
    「无预览图」的帧，保住原意「无任何参考时不瞎传图」。
    """
    seen: list = []

    async def fake_gen_video(prompt, *, duration=5.0, image_url=None, image_urls=None, **kw):  # noqa: ANN001,ANN003
        seen.append(image_urls)
        return MagicMock(url=f"http://vid/{prompt}")

    sb = _sb(1)  # _sb 构造的帧无 local_visual_path
    src = AIVideoVisualSource(MagicMock(generate_video=fake_gen_video, max_video_duration=8.0), concurrency=1)
    await src.render(sb, task_dir=tmp_path)  # 无 reference_image_paths
    assert seen == [None], f"无角色参考且无预览图时 image_urls=None（纯 t2v），实际 {seen}"
