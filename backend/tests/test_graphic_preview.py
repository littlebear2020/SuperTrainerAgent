"""graphic 帧预览（graphic-native-text 2026-08-17 版）：预览=最终所见。

W1 时代的「预览=渲染卡 PNG」已随 Pillow 渲染器退役；现在 graphic 帧预览走 AI 生图，
但提示词经 inject_graphic_text 注入图内文字指令——与 fill 同一注入器，预览/成片一致。
"""

from __future__ import annotations

import base64
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.gateway.video_gen.config import VideoGenConfig
from app.gateway.video_gen.service import VideoGenService
from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame

_HEX = "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c626001000000ffff03000006000557bfabd40000000049454e44ae426082"
_TINY_PNG = "data:image/png;base64," + base64.b64encode(bytes.fromhex(_HEX)).decode()


class _ProviderStub:
    def __init__(self):
        self.calls = 0
        self.prompts: list[str] = []

    async def generate_image(self, prompt, size=None, reference_image_paths=None):  # noqa: ANN001
        self.calls += 1
        self.prompts.append(prompt)
        return SimpleNamespace(url=_TINY_PNG)


def _make_service(tmp_path, task_status="draft"):
    svc = VideoGenService.__new__(VideoGenService)
    svc._sb_write_lock = __import__("asyncio").Lock()
    svc._storage_dir = str(tmp_path)
    svc._config = VideoGenConfig()
    _vg_stub = _ProviderStub()
    # E7：预览经 make_ai_image(payload.image_provider)；None → 默认实例。
    svc._providers = SimpleNamespace(ai_image=_vg_stub, make_ai_image=lambda name: _vg_stub)
    task = SimpleNamespace(id="vg_w1", task_type="ai_image", status=task_status, payload=json.dumps({"mode": "generate"}))
    svc.repo = SimpleNamespace(get=AsyncMock(return_value=task), add_dirty_frame=AsyncMock())
    return svc, task


def _seed(tmp_path, sources: list[str], specs: list[dict | None] | None = None, upload_idx: int | None = None):
    cfg = StoryboardConfig(video_type="ai_image", width=640, height=360)
    frames = []
    for i, s in enumerate(sources):
        f = StoryboardFrame(index=i, narration=f"旁白{i}。要点甲。要点乙。", image_prompt=f"画面描述{i}", visual_source=s)
        if specs and specs[i]:
            f.graphic_spec = specs[i]
        frames.append(f)
    sb = Storyboard(title="t", config=cfg, frames=frames)
    task_dir = tmp_path / "default" / "tasks" / "vg_w1"
    task_dir.mkdir(parents=True)
    (task_dir / "storyboard.json").write_text(json.dumps(sb.to_dict(), ensure_ascii=False), encoding="utf-8")
    if upload_idx is not None:
        (task_dir / "visuals").mkdir(exist_ok=True)
        (task_dir / f"visuals/frame_{upload_idx:03d}.jpg").write_bytes(b"x")
        data = json.loads((task_dir / "storyboard.json").read_text(encoding="utf-8"))
        data["frames"][upload_idx]["local_visual_path"] = f"visuals/frame_{upload_idx:03d}.jpg"
        (task_dir / "storyboard.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return task_dir


class TestGraphicPreviewNativeText:
    @pytest.mark.asyncio
    async def test_graphic_preview_injects_text_prompt(self, tmp_path):
        """graphic 帧预览：走 AI 生图 + prompt 含引号原文文字（与 fill 同注入器）。"""
        svc, task = _make_service(tmp_path)
        _seed(tmp_path, ["graphic"], specs=[{"type": "big_number", "number": "137", "unit": "%"}])
        await svc._generate_frame_preview("vg_w1", 0, "default")
        stub = svc._providers.ai_image
        assert stub.calls == 1
        assert "「137%」" in stub.prompts[0], "预览 prompt 须注入图内文字指令（预览=最终所见）"
        assert "信息图" in stub.prompts[0]
        data = json.loads((tmp_path / "default/tasks/vg_w1/storyboard.json").read_text(encoding="utf-8"))
        assert data["frames"][0]["visual_url"] is not None, "预览写 visual_url（AI 图）"

    @pytest.mark.asyncio
    async def test_graphic_preview_without_spec_plain_prompt(self, tmp_path):
        """无 spec（校验拒绝降级失败残留/手切未提取）：按普通画面渲染，不注入。"""
        svc, task = _make_service(tmp_path)
        _seed(tmp_path, ["graphic"], specs=[None])
        await svc._generate_frame_preview("vg_w1", 0, "default")
        stub = svc._providers.ai_image
        assert stub.calls == 1
        assert stub.prompts[0] == "画面描述0"

    @pytest.mark.asyncio
    async def test_graphic_preview_uses_task_level_layout_card(self, tmp_path):
        """T5b（拍板⑦）：预览须与 fill 用**同一任务级版式卡**（storyboard.json 持久化
        字段）——预览注入默认卡而成片注入任务卡会造成预览/成片版式不一致。"""
        svc, task = _make_service(tmp_path)
        task_dir = _seed(tmp_path, ["graphic"], specs=[{"type": "big_number", "number": "137", "unit": "%"}])
        card = "深蓝主色与浅灰辅助，加粗黑体，等距网格骨架"
        data = json.loads((task_dir / "storyboard.json").read_text(encoding="utf-8"))
        data["graphic_layout_card"] = card
        (task_dir / "storyboard.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        await svc._generate_frame_preview("vg_w1", 0, "default")
        stub = svc._providers.ai_image
        assert stub.calls == 1
        assert f"。全片信息图版式统一：{card}" in stub.prompts[0], "预览 prompt 须带任务级版式卡（与 fill 同源）"

    @pytest.mark.asyncio
    async def test_non_graphic_frame_unchanged(self, tmp_path):
        svc, task = _make_service(tmp_path)
        _seed(tmp_path, ["ai_image"])
        await svc._generate_frame_preview("vg_w1", 0, "default")
        stub = svc._providers.ai_image
        assert stub.calls == 1 and stub.prompts[0] == "画面描述0"

    @pytest.mark.asyncio
    async def test_upload_guard_still_blocks(self, tmp_path):
        svc, task = _make_service(tmp_path)
        _seed(tmp_path, ["graphic"], specs=[{"type": "text_card", "title": "t", "points": ["甲", "乙"]}], upload_idx=0)
        with pytest.raises(ValueError):
            await svc._generate_frame_preview("vg_w1", 0, "default")
