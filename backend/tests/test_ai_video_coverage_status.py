import json

import pytest

from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame


@pytest.mark.asyncio
async def test_ai_video_writes_coverage_sidecar_on_partial(tmp_path, monkeypatch):
    """G: 部分帧占位时写 visual_coverage.json（不抛、保 A/V 同步）。"""
    from app.gateway.video_gen.visual_sources import ai_video as av

    cfg = StoryboardConfig(video_type="ai_video", width=1080, height=1920)
    frames = [StoryboardFrame(index=i, narration=f"n{i}", duration=3.0, image_prompt="p") for i in range(4)]
    sb = Storyboard(title="t", config=cfg, frames=frames)

    # provider: 前两帧成功、后两帧抛 → 占位
    calls = {"n": 0}

    class FakeProvider:
        async def generate_video(self, prompt, *, duration=8.0, **kwargs):
            calls["n"] += 1
            if calls["n"] > 2:
                raise RuntimeError("fail")
            from app.gateway.video_gen.providers.base import VideoRef

            return VideoRef(url=f"http://x/{calls['n']}.mp4")

    src = av.AIVideoVisualSource(FakeProvider(), concurrency=1)

    # _make_placeholder_clip 走 ffmpeg——桩掉。#5 评审补强：原 asyncio.coroutine 在
    # Py3.11+ 已删除、本栈 3.12+ 跑不起来；改 async def（与真实 _make_placeholder_clip 签名一致）。
    async def _fake_placeholder(frame, td, **kwargs):
        return "visuals/p.mp4"

    monkeypatch.setattr(av, "_make_placeholder_clip", _fake_placeholder)
    await src.render(sb, task_dir=tmp_path)
    data = json.loads((tmp_path / "visual_coverage.json").read_text())
    assert data["succeeded"] == 2
    assert data["placeholder"] == 2
    assert data["total"] == 4
    assert data["coverage"] == 0.5


def _full_success_sb():
    cfg = StoryboardConfig(video_type="ai_video", width=1080, height=1920)
    frames = [StoryboardFrame(index=i, narration=f"n{i}", duration=3.0, image_prompt="p") for i in range(2)]
    return Storyboard(title="t", config=cfg, frames=frames)


class _TruncatingProvider:
    """全帧成功但 provider 置 multi_image_truncated（H3/happyhorse 截断口径）。"""

    multi_image_truncated = False

    def reset_multi_image_truncated(self) -> None:
        self.multi_image_truncated = False

    async def generate_video(self, prompt, *, duration=8.0, **kwargs):
        self.multi_image_truncated = True
        from app.gateway.video_gen.providers.base import VideoRef

        return VideoRef(url="http://x/ok.mp4")


@pytest.mark.asyncio
async def test_coverage_sidecar_records_multi_image_truncation(tmp_path):
    """偏离项 3：全帧成功但参考图被截断 → sidecar 必须保留并记 character_ref_truncated。

    修前 sidecar 只看 coverage==1.0 就删文件——截断降级对用户不可见
    （对照 registry 降级分支的 character_ref_degraded 标准）。
    """
    from app.gateway.video_gen.visual_sources import ai_video as av

    src = av.AIVideoVisualSource(_TruncatingProvider(), concurrency=1)
    await src.render(_full_success_sb(), task_dir=tmp_path)
    p = tmp_path / "visual_coverage.json"
    assert p.exists(), "截断降级时 sidecar 不得删除（coverage=1.0 也要保留）"
    data = json.loads(p.read_text())
    assert data["character_ref_truncated"] is True
    assert data["coverage"] == 1.0


@pytest.mark.asyncio
async def test_render_resets_stale_truncation_flag(tmp_path):
    """provider 单例跨任务：render 开始清零 multi_image_truncated（上一任务残留不污染本次 sidecar）。"""
    from app.gateway.video_gen.visual_sources import ai_video as av

    class _CleanProvider(_TruncatingProvider):
        """成功且不截断的桩——仅带残留 flag 与 reset 方法。"""

        async def generate_video(self, prompt, *, duration=8.0, **kwargs):
            from app.gateway.video_gen.providers.base import VideoRef

            return VideoRef(url="http://x/ok.mp4")

    stale = _CleanProvider()
    stale.multi_image_truncated = True  # 上一任务残留
    src = av.AIVideoVisualSource(stale, concurrency=1)
    await src.render(_full_success_sb(), task_dir=tmp_path)
    assert stale.multi_image_truncated is False, "render 开始应调用 reset_multi_image_truncated 清零"
    assert not (tmp_path / "visual_coverage.json").exists(), "清零后未截断 → 不写 sidecar"
