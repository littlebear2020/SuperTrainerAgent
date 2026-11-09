"""P1-2: ai_image 生成图本地化（对齐 ai_video D1）。

修复前：ai_image 帧只留远程 visual_url（Ark/360 临时 URL）→ URL 过期后
recompose 全量重下载失败 → 全片黑场占位；详情页缩略图也裂。
"""

import base64
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.gateway.video_gen.providers.base import ImageRef
from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame
from app.gateway.video_gen.visual_sources import ai_image as ai_image_mod
from app.gateway.video_gen.visual_sources.ai_image import AIImageVisualSource, _localize_image


def _frame(i=0):
    return StoryboardFrame(index=i, narration=f"旁白{i}", image_prompt=f"prompt{i}")


@pytest.mark.asyncio
async def test_localize_data_uri(tmp_path):
    """seedream b64_json 形态（data:image/png;base64,...）直接解码落盘。"""
    png_bytes = b"\x89PNG" + b"1" * 600
    uri = "data:image/png;base64," + base64.b64encode(png_bytes).decode()
    rel = await _localize_image(uri, _frame(3), tmp_path)
    assert rel == "visuals/img_0003.png"
    assert (tmp_path / rel).read_bytes() == png_bytes


@pytest.mark.asyncio
async def test_localize_http_success(tmp_path):
    content = b"\xff\xd8\xff" + b"2" * 600

    async def _fake_download(url, **k):
        return content

    with patch.object(ai_image_mod, "safe_download", _fake_download):
        rel = await _localize_image("http://cdn/x.jpg", _frame(0), tmp_path)
    assert rel == "visuals/img_0000.png"
    assert (tmp_path / rel).read_bytes() == content


@pytest.mark.asyncio
async def test_localize_http_too_small_returns_none(tmp_path):
    async def _fake_download(url, **k):
        return b"tiny"

    with (
        patch.object(ai_image_mod, "safe_download", _fake_download),
        patch.object(ai_image_mod, "_LOCALIZE_RETRY_DELAYS", [0]),
    ):
        rel = await _localize_image("http://cdn/x.jpg", _frame(0), tmp_path)
    assert rel is None  # 保留远程 url，worker 下载兜底


@pytest.mark.asyncio
async def test_render_localizes_and_persists_via_on_frame_done(tmp_path):
    """render() 成功后：帧带 local_visual_path，且 on_frame_done 在其后调用（落盘依赖）。"""
    provider = MagicMock()
    provider.generate_image = AsyncMock(return_value=ImageRef(url="http://cdn/a.png", width=1080, height=1920, source="t"))
    src = AIImageVisualSource(provider)
    sb = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_image", width=1080, height=1920),
        frames=[_frame(0), _frame(1)],
    )
    done: list[int] = []

    async def on_done(i):
        done.append(i)

    with patch.object(ai_image_mod, "_localize_image", new_callable=AsyncMock) as m_loc:

        async def _fake(url, frame, td):
            (tmp_path / "visuals").mkdir(exist_ok=True)
            rel = f"visuals/img_{frame.index:04d}.png"
            (tmp_path / rel).write_bytes(b"img")
            return rel

        m_loc.side_effect = _fake
        await src.render(sb, on_frame_done=on_done, task_dir=tmp_path)

    assert sb.frames[0].local_visual_path == "visuals/img_0000.png"
    assert sb.frames[1].local_visual_path == "visuals/img_0001.png"
    assert done == [0, 1]


@pytest.mark.asyncio
async def test_render_failed_frame_stays_none_no_reuse(tmp_path):
    """BUG 1-B：失败帧不再复用上帧画面->visual_url 留 None、无 local_visual_path。

    注意覆盖率闸：MIN_COVERAGE=0.6，3 帧 1 败 -> 2/3 通过（>60%）-> 不触发 coverage guard。
    """
    provider = MagicMock()
    calls = {"n": 0}

    async def gen(prompt, *, size, reference_image_paths=None):
        calls["n"] += 1
        if calls["n"] <= 2:
            return ImageRef(url=f"http://cdn/{calls['n']}.png", width=1080, height=1920, source="t")
        raise RuntimeError("boom")  # 帧2两次都失败 -> 留 None（BUG 1-B：不再复用帧1）

    provider.generate_image = gen
    src = AIImageVisualSource(provider)
    sb = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_image", width=1080, height=1920),
        frames=[_frame(0), _frame(1), _frame(2)],
    )

    async def _fake(url, frame, td):
        (tmp_path / "visuals").mkdir(exist_ok=True)
        rel = f"visuals/img_{frame.index:04d}.png"
        (tmp_path / rel).write_bytes(b"img")
        return rel

    with patch.object(ai_image_mod, "_localize_image", side_effect=_fake):
        await src.render(sb, task_dir=tmp_path)

    assert sb.frames[2].visual_url is None, "BUG 1-B：失败帧不再复用上帧图，留 None"
    assert sb.frames[2].visual_type != "image_reuse", "不再产生 image_reuse 标记"
    assert not hasattr(sb.frames[2], "local_visual_path") or sb.frames[2].local_visual_path is None, "失败帧无本地文件"
