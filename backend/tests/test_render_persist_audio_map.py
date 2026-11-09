"""_render_from_draft 必须把 _synthesize_audio 的实测时长落盘 storyboard.json——
否则 recompose 读盘踩旧值（2026-07-21 漂移根因环 3 之"只活内存"）。"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_render_from_draft_persists_probed_durations(tmp_path, monkeypatch):
    from app.gateway.video_gen import service as svc_mod
    from app.gateway.video_gen.service import VideoGenService
    from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame

    # 盘面 storyboard：规划估值时长（旧）
    sb = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_image", width=1080, height=1920),
        frames=[
            StoryboardFrame(index=0, narration="第一句", image_prompt="p0", duration=4.0),
            StoryboardFrame(index=1, narration="第二句", image_prompt="p1", duration=4.0),
        ],
    )
    (tmp_path / "storyboard.json").write_text(json.dumps(sb.to_dict(), ensure_ascii=False), encoding="utf-8")

    svc = VideoGenService.__new__(VideoGenService)
    svc._storage_dir = str(tmp_path.parent)
    svc._sb_write_lock = asyncio.Lock()
    svc._config = SimpleNamespace(effective_frame_concurrency=lambda n: 2)

    async def fake_synth(storyboard, task_dir, voice_id, *, seconds_per_frame=None, task_id=None):
        # 模拟真实 TTS：实测时长（新）写入内存 storyboard
        for i, f in enumerate(storyboard.frames):
            f.audio_path = str(task_dir / "audio" / f"c{i}.mp3")
            f.duration = 7.5 + i
            f.speech_duration = 7.5 + i
            f.srt = ""
        return str(task_dir / "audio.mp3"), str(task_dir / "subtitle.srt")

    svc._synthesize_audio = fake_synth

    mock_visual = AsyncMock()
    mock_visual.render = AsyncMock(return_value=None)
    monkeypatch.setattr(svc_mod, "_warn_if_image_provider_not_i2i", lambda *a, **k: None)

    async def fake_caps(worker_url=None):
        return ["v1"]

    monkeypatch.setattr("app.gateway.video_gen.worker_probe.probe_worker_capabilities", fake_caps)

    progress = AsyncMock()
    with patch.object(svc, "_make_visual_source", return_value=mock_visual):
        result = await svc._render_from_draft("vg_x", "ai_image", tmp_path, progress, {}, user_id="u")

    # payload 用实测时长（修前即如此，防回归）
    assert result.payload["frames"][0]["duration"] == 7.5
    # 磁盘 storyboard.json 也必须落实测时长（本测试的核心——修前缺失）
    disk = json.loads((tmp_path / "storyboard.json").read_text(encoding="utf-8"))
    assert disk["frames"][0]["duration"] == 7.5, "实测时长未落盘 → recompose 必踩旧值"
    assert disk["frames"][1]["duration"] == 8.5
    assert disk["frames"][0]["audio_path"].endswith("c0.mp3")
    assert disk["frames"][0]["speech_duration"] == 7.5
