"""P0-1（2026-08-03）：单帧「重新生成」也做到所见即所得。

用户对某帧点「预览」看到构图 A，再点「重新生成」，修前会清空预览图引用 → Task 5 的
构图继承条件不成立 → 退化成纯 t2v，成片构图 ≠ A。而「批量渲染/重新合成」路径不清空、
能继承。同一帧两个操作行为不一致。

清空本身是 P0-A1（修「先上传后重生被静默覆盖」），不能整个撤掉——只能收窄到
「用户上传素材」。判据 = visuals/frame_ 前缀（_USER_VISUAL_PREFIX）。
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.gateway.video_gen.config import VideoGenConfig
from app.gateway.video_gen.service import VideoGenService
from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame


def _sb(local: str | None, vtype: str | None, video_type: str = "ai_video") -> Storyboard:
    cfg = StoryboardConfig(video_type=video_type, width=720, height=1280)
    f = StoryboardFrame(index=0, narration="n", duration=5.0, image_prompt="p", visual_url="http://old", visual_type=vtype, local_visual_path=local)
    return Storyboard(title="t", config=cfg, frames=[f])


async def _run_regen(tmp_path, monkeypatch, sb: Storyboard, task_type: str = "ai_video") -> dict:
    """跑 _regenerate_frame 的画面段，返回 render 真正读到的帧状态。

    真实签名是 (task_id, user_id, frame_index, task_dir, mode)；task 从 self.repo 取、
    storyboard 从 task_dir/storyboard.json 读。
    """
    (tmp_path / "storyboard.json").write_text(json.dumps(sb.to_dict(), ensure_ascii=False), encoding="utf-8")
    (tmp_path / "audio.mp3").write_bytes(b"a")
    (tmp_path / "subtitle.srt").write_text("1", encoding="utf-8")

    captured: dict = {}

    class _Visual:
        async def render(self, storyboard, **kwargs):  # noqa: ANN001,ANN003
            fr = storyboard.frames[0]
            captured["visual_type"] = fr.visual_type
            captured["local_visual_path"] = fr.local_visual_path
            fr.visual_url = "http://new"
            fr.visual_type = "video"
            return None

    svc = VideoGenService.__new__(VideoGenService)
    svc._sb_write_lock = __import__("asyncio").Lock()
    svc._storage_dir = str(tmp_path)
    # 用真实 VideoGenConfig：_regenerate_frame 会读多个字段（ai_image_provider 等），
    # SimpleNamespace 桩容易漏字段且漏了才发现
    svc._config = VideoGenConfig()

    task = SimpleNamespace(
        id="vg_x",
        task_type=task_type,
        status="ready",
        payload=json.dumps({"mode": "generate"}),
        result="{}",
    )
    svc.repo = SimpleNamespace(get=AsyncMock(return_value=task))

    monkeypatch.setattr(svc, "_make_visual_source", lambda *a, **k: _Visual())
    monkeypatch.setattr("app.gateway.video_gen.service._inject_project_character_context", lambda *a, **k: None)

    await svc._regenerate_frame("vg_x", "u", 0, tmp_path, mode="visual_only")
    return captured


@pytest.mark.asyncio
async def test_regen_keeps_ai_preview_as_first_frame(tmp_path, monkeypatch) -> None:
    """AI 预览图（img_ 前缀）单帧重生时保留 → Task 5 能拿它作 i2v 首帧。"""
    sb = _sb("visuals/img_0000.png", "image")
    got = await _run_regen(tmp_path, monkeypatch, sb)
    assert got["visual_type"] == "image", "预览图的 visual_type 应保留，否则继承条件不成立"
    assert got["local_visual_path"] == "visuals/img_0000.png", "预览图路径应保留"


@pytest.mark.asyncio
async def test_regen_still_clears_user_upload(tmp_path, monkeypatch) -> None:
    """用户上传（frame_ 前缀）仍必须清空 —— P0-A1 不得回归。"""
    sb = _sb("visuals/frame_0001.png", "image")
    got = await _run_regen(tmp_path, monkeypatch, sb)
    assert got["local_visual_path"] is None, "用户上传素材必须清空，否则 worker 用旧图（先上传后重生 = 白花钱）"
    assert got["visual_type"] is None


@pytest.mark.asyncio
async def test_regen_ai_image_task_clears_preview(tmp_path, monkeypatch) -> None:
    """ai_image 任务不保留预览图。

    ai_image 的 render 只在 visual_url 存在时才本地化，生成失败不覆盖 local_visual_path
    → 留着旧图会把「重生失败」伪装成「重生成功」（把上一张图当新结果交付）。
    ai_video 无此风险（失败走 _make_placeholder_clip 覆写）。
    """
    sb = _sb("visuals/img_0000.png", "image", video_type="ai_image")
    got = await _run_regen(tmp_path, monkeypatch, sb, task_type="ai_image")
    assert got["local_visual_path"] is None
    assert got["visual_type"] is None


@pytest.mark.asyncio
async def test_regen_clears_stale_video_local_path(tmp_path, monkeypatch) -> None:
    """既有的本地视频（visual_type=video）不是预览图 → 仍清空，避免 worker 用旧片。"""
    sb = _sb("visuals/shot_0000.mp4", "video")
    got = await _run_regen(tmp_path, monkeypatch, sb)
    assert got["local_visual_path"] is None
    assert got["visual_type"] is None
