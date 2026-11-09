"""T-E9（2026-09-09 feedback-round1）：字幕编辑 → remotion 帧级 srt 重分配。

remotion 成片烧词级 captions（模板不读 subtitle.srt）——PUT /subtitle 的
remotion 分支把整片 SRT 按帧边界重分配回 frame.srt（帧本地时间戳），
recompose 重建 captions 后成片字幕更新。
"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.gateway.routers.video_gen import (
    SubtitleCue,
    UpdateSubtitleRequest,
    update_subtitle,
)


def _make_request(svc):
    class _Req:
        class app:
            class state:
                video_gen_service = svc

    return _Req()


def _make_service(tmp_path):
    from app.gateway.video_gen.config import VideoGenConfig
    from app.gateway.video_gen.providers.registry import ProviderRegistry
    from app.gateway.video_gen.service import VideoGenService

    cfg = VideoGenConfig(qihu_api_key="t")
    svc = VideoGenService(
        repo=MagicMock(),
        config=cfg,
        providers=ProviderRegistry(cfg),
        storage_dir=str(tmp_path),
        sandbox_provider=AsyncMock(),
    )
    svc.repo.update = AsyncMock(return_value=None)
    return svc


def _cue(start: str, end: str, text: str) -> SubtitleCue:
    return SubtitleCue(start=start, end=end, text=text)


@pytest.mark.asyncio
async def test_redistribute_with_intro_offset(tmp_path):
    """含 intro 项目：cue 全局时间 → 帧本地时间（减 intro + 减帧起点）。"""
    svc = _make_service(tmp_path)
    user_id = "test-user-autouse"
    task_id = "vg_sub_redist"
    task_dir = tmp_path / user_id / "tasks" / task_id
    task_dir.mkdir(parents=True)
    (task_dir / "storyboard.json").write_text(
        json.dumps(
            {
                "title": "t",
                "config": {"video_type": "remotion_course", "width": 1920, "height": 1080},
                "frames": [
                    {"index": 0, "narration": "第一帧", "duration": 4.0},
                    {"index": 1, "narration": "第二帧", "duration": 4.0},
                ],
                "scene_groups": [],
            }
        ),
        encoding="utf-8",
    )
    task = MagicMock()
    task.status = "ready"
    task.task_type = "remotion_course"
    task.payload = json.dumps({"remotion_props": {"brand": {"intro": {"durationInFrames": 90}}}})
    svc.get_task = AsyncMock(return_value=task)

    # intro=3s；帧 0 范围 [3000,7000)，帧 1 [7000,11000)
    body = UpdateSubtitleRequest(
        cues=[
            _cue("00:00:03,500", "00:00:04,000", "帧一句话"),
            _cue("00:00:08,000", "00:00:09,000", "帧二句话"),
        ]
    )
    res = await update_subtitle(_make_request(svc), task_id, body)
    assert res["remotion_frames_updated"] is True

    sb = json.loads((task_dir / "storyboard.json").read_text(encoding="utf-8"))
    assert "帧一句话" in sb["frames"][0]["srt"]
    assert "帧二句话" in sb["frames"][1]["srt"]
    # 帧本地时间戳：帧 0 cue 起点 3500-3000=500ms
    assert "00:00:00,500 --> 00:00:01,000" in sb["frames"][0]["srt"]
    # 帧本地：帧 1 cue 起点 8000-7000=1000ms
    assert "00:00:01,000 --> 00:00:02,000" in sb["frames"][1]["srt"]


@pytest.mark.asyncio
async def test_no_intro_no_offset(tmp_path):
    svc = _make_service(tmp_path)
    user_id = "test-user-autouse"
    task_id = "vg_sub_redist2"
    task_dir = tmp_path / user_id / "tasks" / task_id
    task_dir.mkdir(parents=True)
    (task_dir / "storyboard.json").write_text(
        json.dumps(
            {
                "title": "t",
                "config": {"video_type": "remotion_course", "width": 1920, "height": 1080},
                "frames": [{"index": 0, "narration": "唯一帧", "duration": 4.0}],
                "scene_groups": [],
            }
        ),
        encoding="utf-8",
    )
    task = MagicMock()
    task.status = "ready"
    task.task_type = "remotion_course"
    task.payload = "{}"
    svc.get_task = AsyncMock(return_value=task)

    body = UpdateSubtitleRequest(cues=[_cue("00:00:01,000", "00:00:02,000", "本帧字幕")])
    res = await update_subtitle(_make_request(svc), task_id, body)
    assert res["remotion_frames_updated"] is True
    sb = json.loads((task_dir / "storyboard.json").read_text(encoding="utf-8"))
    assert "00:00:01,000 --> 00:00:02,000" in sb["frames"][0]["srt"]


@pytest.mark.asyncio
async def test_non_remotion_untouched(tmp_path):
    """ai_image 任务不走重分配（烧 subtitle.srt 的既有链路）。"""
    svc = _make_service(tmp_path)
    user_id = "test-user-autouse"
    task_id = "vg_sub_redist3"
    task_dir = tmp_path / user_id / "tasks" / task_id
    task_dir.mkdir(parents=True)
    (task_dir / "storyboard.json").write_text(
        json.dumps(
            {
                "title": "t",
                "config": {"video_type": "ai_image", "width": 1920, "height": 1080},
                "frames": [{"index": 0, "narration": "n", "duration": 4.0}],
                "scene_groups": [],
            }
        ),
        encoding="utf-8",
    )
    task = MagicMock()
    task.status = "ready"
    task.task_type = "ai_image"
    task.payload = "{}"
    svc.get_task = AsyncMock(return_value=task)

    body = UpdateSubtitleRequest(cues=[_cue("00:00:01,000", "00:00:02,000", "x")])
    res = await update_subtitle(_make_request(svc), task_id, body)
    assert res["remotion_frames_updated"] is False
    sb = json.loads((task_dir / "storyboard.json").read_text(encoding="utf-8"))
    assert "srt" not in sb["frames"][0]
