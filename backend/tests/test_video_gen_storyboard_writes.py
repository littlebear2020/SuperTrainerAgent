"""Tests that storyboard-mutating router endpoints route writes through the
unified ``_update_storyboard`` safe writer (Lock + read-merge-write + atomic write).

Critical#1 from the post-MVP review: three endpoints previously did raw full-dict
overwrites of storyboard.json, which can lose concurrent writes — e.g. the ~50s
``_generate_draft_audio`` background TTS task, or SSE ``visual_url`` updates that
land between an endpoint's read and its write.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.gateway._shared.task_framework.schemas import DRAFT
from app.gateway.routers.video_gen import (
    UpdateFrameRequest,
    regenerate_frame_prompt,
    update_storyboard_frame,
    upload_frame_visual,
)
from app.gateway.video_gen.config import VideoGenConfig
from app.gateway.video_gen.providers.registry import ProviderRegistry
from app.gateway.video_gen.service import VideoGenService
from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame


def _make_service(tmp_path: Path) -> VideoGenService:
    cfg = VideoGenConfig(qihu_api_key="t", script_model="test-script-model")  # E6: script_model 显式化后不再回落 models[0]
    repo = MagicMock()
    # add_dirty_frame is awaited by the upload route (added in 5082ccd8) — bind
    # an async mock so `await svc.repo.add_dirty_frame(...)` resolves.
    repo.add_dirty_frame = AsyncMock(return_value=None)
    # T4(ee042bc03) 起端点末尾 await svc.touch_task → repo.update——绑定 async mock
    repo.update = AsyncMock(return_value=None)
    # B5（2026-09-28）：draft 改旁白 → _invalidate_audio_artifacts 内 await repo.get
    repo.get = AsyncMock(return_value=None)
    return VideoGenService(
        repo=repo,
        config=cfg,
        providers=ProviderRegistry(cfg),
        storage_dir=str(tmp_path),
        sandbox_provider=AsyncMock(),
    )


def _draft_task() -> MagicMock:
    task = MagicMock()
    task.status = DRAFT
    task.task_type = "ai_image"
    # 2026-08-12（prompt-optimization-v1 P2）：regen 端点新增 json.loads(task.payload)
    # 解析 style——MagicMock 自动属性是 truthy 非 str，会让 json.loads 抛 TypeError。
    # 生产 task.payload 是 str | None（repo json.dumps 存库），fixture 须给真实 str。
    task.payload = "{}"
    return task


def _task_dir(tmp_path: Path) -> Path:
    d = tmp_path / "u1" / "tasks" / "vg_test"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_storyboard(tmp_path: Path, sb: Storyboard) -> Path:
    d = _task_dir(tmp_path)
    (d / "storyboard.json").write_text(json.dumps(sb.to_dict(), ensure_ascii=False), encoding="utf-8")
    return d / "storyboard.json"


def _request_with_svc(svc: VideoGenService) -> MagicMock:
    request = MagicMock()
    request.app.state.video_gen_service = svc
    return request


# ---------------------------------------------------------------------------
# #2 regenerate_frame_prompt — must NOT clobber a concurrent write (real behavior)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_regenerate_frame_prompt_preserves_concurrent_write(tmp_path):
    """While the LLM regenerates a frame's prompt, a concurrent writer (e.g. the
    background TTS task) may land ``audio_path`` on storyboard.json. The endpoint
    must read-merge-write — not overwrite from the stale in-memory Storyboard
    loaded before the LLM call — or the concurrent ``audio_path`` is lost.
    """
    svc = _make_service(tmp_path)
    svc.get_task = AsyncMock(return_value=_draft_task())

    sb = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_image", width=1080, height=1920),
        frames=[StoryboardFrame(index=0, narration="hello world", image_prompt="old prompt")],
    )
    sb_path = _write_storyboard(tmp_path, sb)

    def _fake_generate_image_prompts(llm, narrations, **kwargs):
        # Simulate a concurrent _generate_draft_audio write landing mid-regeneration.
        data = json.loads(sb_path.read_text(encoding="utf-8"))
        data["frames"][0]["audio_path"] = "/audio/concurrent_tts.mp3"
        sb_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return ["regenerated prompt"]

    with (
        patch("app.gateway.routers.video_gen.get_effective_user_id", return_value="u1"),
        patch("deerflow.models.create_chat_model", return_value=MagicMock()),
        patch(
            "app.gateway.video_gen.orchestration.storyboard_planner._generate_image_prompts",
            new=AsyncMock(side_effect=_fake_generate_image_prompts),
        ),
    ):
        await regenerate_frame_prompt(_request_with_svc(svc), "vg_test", 0)

    on_disk = json.loads(sb_path.read_text(encoding="utf-8"))
    # 2026-08-12（prompt-optimization-v1 P2）：regen 端点现在会追加风格质量后缀
    # （payload 无 style → default 后缀），与 bulk 帧同构——这是预期行为变更。
    assert on_disk["frames"][0]["image_prompt"].startswith("regenerated prompt，")
    assert "质感" in on_disk["frames"][0]["image_prompt"], "regen 帧须带质量后缀（与 bulk 帧画风一致）"
    assert on_disk["frames"][0]["audio_path"] == "/audio/concurrent_tts.mp3", "concurrent audio_path was clobbered by a stale full-overwrite of storyboard.json"


@pytest.mark.asyncio
async def test_regenerate_uses_project_style_when_course_project_id_present(tmp_path):
    """R-A: 任务带 course_project_id 时，regen 用项目 style_prefix 而非 payload style。

    防护：若有人把优先级改回 payload-only，课程项目任务 regen 出的帧
    会静默退化回 default 画风——与 bulk 帧不一致。
    """
    svc = _make_service(tmp_path)
    # 任务 payload 含 course_project_id 但不含 style——须走项目风格路径
    task = MagicMock()
    task.status = DRAFT
    task.task_type = "ai_image"
    task.payload = json.dumps({"course_project_id": "cp_test"})
    svc.get_task = AsyncMock(return_value=task)

    sb = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_image", width=1080, height=1920),
        frames=[StoryboardFrame(index=0, narration="hello")],
    )
    sb_path = _write_storyboard(tmp_path, sb)

    # 项目 style loader mock——返回水彩风格 prefix
    svc._load_course_project_style = MagicMock(return_value={"style_prefix": "水彩手绘风格，柔和笔触，通透质感，纸纹肌理"})

    async def _fake_generate(llm, narrations, **kwargs):
        return ["a beautiful scene"]

    with (
        patch("app.gateway.routers.video_gen.get_effective_user_id", return_value="u1"),
        patch("deerflow.models.create_chat_model", return_value=MagicMock()),
        patch(
            "app.gateway.video_gen.orchestration.storyboard_planner._generate_image_prompts",
            new=AsyncMock(side_effect=_fake_generate),
        ),
    ):
        await regenerate_frame_prompt(_request_with_svc(svc), "vg_test", 0)

    on_disk = json.loads(sb_path.read_text(encoding="utf-8"))
    prompt = on_disk["frames"][0]["image_prompt"]
    assert prompt.startswith("水彩手绘风格"), f"regen 须用项目 style_prefix：{prompt!r}"
    assert "纸纹肌理" in prompt, f"须含水彩风格质量词：{prompt!r}"
    # 确认项目 style loader 被调用（而非 payload style 回落）
    svc._load_course_project_style.assert_called_once_with("cp_test", user_id="u1")


@pytest.mark.asyncio
async def test_regenerate_accepts_dict_payload(tmp_path):
    """A3 (2026-08-13): task.payload 为 dict（非 str）时 regen 不得 TypeError。

    生产 payload 一律 json.dumps 存库（str | None），此为防御性一致性修复
    （对齐 service.py _generate_frame_preview 的 isinstance 守卫写法）。
    """
    svc = _make_service(tmp_path)
    task = MagicMock()
    task.status = DRAFT
    task.task_type = "ai_image"
    task.payload = {}  # dict, not str — json.loads would raise TypeError
    svc.get_task = AsyncMock(return_value=task)

    sb = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_image", width=1080, height=1920),
        frames=[StoryboardFrame(index=0, narration="hello", image_prompt="old")],
    )
    sb_path = _write_storyboard(tmp_path, sb)

    async def _fake_generate(llm, narrations, **kwargs):
        return ["regenerated prompt"]

    with (
        patch("app.gateway.routers.video_gen.get_effective_user_id", return_value="u1"),
        patch("deerflow.models.create_chat_model", return_value=MagicMock()),
        patch(
            "app.gateway.video_gen.orchestration.storyboard_planner._generate_image_prompts",
            new=AsyncMock(side_effect=_fake_generate),
        ),
    ):
        await regenerate_frame_prompt(_request_with_svc(svc), "vg_test", 0)

    on_disk = json.loads(sb_path.read_text(encoding="utf-8"))
    assert on_disk["frames"][0]["image_prompt"].startswith("regenerated prompt，"), "dict payload 不应中断 regen"


@pytest.mark.asyncio
async def test_regenerate_ai_video_clears_camera_fields(tmp_path):
    """R-B: ai_video regen 后磁盘 shot_size/camera_move 确为 None。

    防护：_VIDEO_PROMPT_SYSTEM 第 2 层要求 LLM 把运镜写进正文，
    若不清枚举字段，compose_video_prompt 会双写运镜（正文 + 枚举追加）。
    updater 重构时若丢掉这两个字段的清空，双运镜会悄悄回归。
    """
    svc = _make_service(tmp_path)
    svc.get_task = AsyncMock(return_value=_draft_task())

    sb = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_video", width=1080, height=1920),
        frames=[
            StoryboardFrame(
                index=0,
                narration="hello",
                image_prompt="old",
                shot_size="wide",
                camera_move="push_in",
            )
        ],
    )
    sb_path = _write_storyboard(tmp_path, sb)

    async def _fake_generate(llm, narrations, **kwargs):
        return ["a scene with camera slowly pushing in"]

    with (
        patch("app.gateway.routers.video_gen.get_effective_user_id", return_value="u1"),
        patch("deerflow.models.create_chat_model", return_value=MagicMock()),
        patch(
            "app.gateway.video_gen.orchestration.storyboard_planner._generate_video_prompts",
            new=AsyncMock(side_effect=_fake_generate),
        ),
    ):
        await regenerate_frame_prompt(_request_with_svc(svc), "vg_test", 0)

    on_disk = json.loads(sb_path.read_text(encoding="utf-8"))
    frame0 = on_disk["frames"][0]
    assert frame0["shot_size"] is None, "ai_video regen 后 shot_size 须清 None（防双写运镜）"
    assert frame0["camera_move"] is None, "ai_video regen 后 camera_move 须清 None（防双写运镜）"
    assert frame0["image_prompt"], "image_prompt 须有值"


# ---------------------------------------------------------------------------
# #1 update_storyboard_frame — must delegate to the unified writer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_storyboard_frame_uses_unified_writer(tmp_path):
    svc = _make_service(tmp_path)
    svc.get_task = AsyncMock(return_value=_draft_task())
    sb = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_image", width=1080, height=1920),
        frames=[StoryboardFrame(index=0, narration="hello", image_prompt="old")],
    )
    sb_path = _write_storyboard(tmp_path, sb)

    spy = AsyncMock(wraps=svc._update_storyboard)
    with (
        patch("app.gateway.routers.video_gen.get_effective_user_id", return_value="u1"),
        patch.object(svc, "_update_storyboard", spy),
    ):
        await update_storyboard_frame(_request_with_svc(svc), "vg_test", 0, UpdateFrameRequest(narration="new narration"))

    spy.assert_awaited_once()
    on_disk = json.loads(sb_path.read_text(encoding="utf-8"))
    assert on_disk["frames"][0]["narration"] == "new narration"


# ---------------------------------------------------------------------------
# #3 upload_frame_visual — must delegate to the unified writer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_frame_visual_uses_unified_writer(tmp_path):
    svc = _make_service(tmp_path)
    svc.get_task = AsyncMock(return_value=_draft_task())
    sb = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_image", width=1080, height=1920),
        frames=[
            StoryboardFrame(
                index=0,
                narration="hello",
                visual_url="http://old/x.mp4",
                visual_type="video",
            )
        ],
    )
    sb_path = _write_storyboard(tmp_path, sb)

    file = MagicMock()
    file.filename = "frame.png"
    file.content_type = "image/png"
    file.read = AsyncMock(return_value=b"\x89PNG\r\n\x1a\n" + b"0" * 32)  # SEC-M7: 真魔数过校验
    spy = AsyncMock(wraps=svc._update_storyboard)
    with (
        patch("app.gateway.routers.video_gen.get_effective_user_id", return_value="u1"),
        patch.object(svc, "_update_storyboard", spy),
    ):
        await upload_frame_visual(_request_with_svc(svc), "vg_test", 0, file)

    spy.assert_awaited_once()
    on_disk = json.loads(sb_path.read_text(encoding="utf-8"))
    assert on_disk["frames"][0]["local_visual_path"] == "visuals/frame_000.png"
    assert on_disk["frames"][0]["visual_type"] == "image"
    assert on_disk["frames"][0]["visual_url"] is None


# ---------------------------------------------------------------------------
# direct-render path (_prepare_storyboard_video) must persist storyboard.json
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_direct_render_writes_storyboard_json(tmp_path, monkeypatch):
    """直渲路径(auto_render)必须落盘 storyboard.json，否则 /storyboard、/timeline 404。"""
    svc = _make_service(tmp_path)

    storyboard = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_image", width=1080, height=1920),
        frames=[StoryboardFrame(index=0, narration="测试")],
    )

    audio_path = tmp_path / "audio.mp3"
    srt_path = tmp_path / "sub.srt"
    svc._prepare_storyboard_and_audio = AsyncMock(return_value=(storyboard, str(audio_path), str(srt_path)))
    svc._has_mixed_sources = MagicMock(return_value=False)
    visual = MagicMock()
    visual.render = AsyncMock()
    svc._make_visual_source = MagicMock(return_value=visual)
    svc._make_on_frame_done = MagicMock(return_value=lambda *a, **k: None)

    monkeypatch.setattr("app.gateway.video_gen.worker_probe.probe_worker_capabilities", AsyncMock(return_value={}))
    monkeypatch.setattr("app.gateway.video_gen.worker_probe.decide_pipeline_version", MagicMock(return_value="v2"))
    monkeypatch.setattr("app.gateway.video_gen.service.build_worker_payload", MagicMock(return_value={}))

    task_dir = _task_dir(tmp_path)
    progress = AsyncMock()
    payload = {"output_format": "mp4", "quality": "standard", "engine": "auto"}

    await svc._prepare_storyboard_video("ai_image", payload, task_dir, progress)

    sb_path = task_dir / "storyboard.json"
    assert sb_path.exists(), "直渲路径必须落盘 storyboard.json"
    data = json.loads(sb_path.read_text(encoding="utf-8"))
    assert len(data["frames"]) == 1


@pytest.mark.asyncio
async def test_get_storyboard_allows_failed_status(tmp_path, monkeypatch):
    """Failed task should be viewable in the storyboard editor — to fix the
    prompt that caused the failure. Consistent with single-frame visual regen
    (regenerate_frame L865 allows ready/failed) + /re-render which already allow
    failed; otherwise the user is stuck (can re-render but can't see/edit the
    offending prompt)."""
    from app.gateway.routers import video_gen as router_mod

    svc = _make_service(tmp_path)
    task = MagicMock()
    task.status = "failed"
    task.id = "vg_test"
    task.result = "{}"
    task.payload = "{}"
    svc.get_task = AsyncMock(return_value=task)
    task_dir = tmp_path / "u1" / "tasks" / "vg_test"
    task_dir.mkdir(parents=True)
    (task_dir / "storyboard.json").write_text(
        json.dumps(
            {
                "title": "t",
                "config": {"video_type": "ai_image", "width": 720, "height": 1280},
                "frames": [{"index": 0, "narration": "n", "duration": 5.0}],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(router_mod, "_get_service", lambda req: svc)
    monkeypatch.setattr(router_mod, "get_effective_user_id", lambda: "u1")

    result = await router_mod.get_storyboard(MagicMock(), "vg_test")
    assert isinstance(result, dict), "failed task storyboard should be viewable, not 409"


@pytest.mark.asyncio
async def test_direct_render_passes_project_character_ref(tmp_path, monkeypatch):
    """Task 12 Step 4: 项目绑定角色 → ai_image 直渲把 front_url 作 reference_image_paths 透传给 render。

    既有 Task 12(commit e554f5ee)只给 visual_sources 加了 reference_image_paths 参数，
    service.py 调用点未透传 → 项目角色三视图从未真正注入 ai_image。此处补齐调用点。
    """
    from app.gateway.video_gen.course_models import CharacterSheet, CourseProject

    svc = _make_service(tmp_path)
    # 项目带 character_sheets（_run_pipeline 会读它注入 payload，这里直接预置）
    proj = CourseProject(
        id="cp_dddddddddddd",
        user_id="u1",
        title="t",
        character_sheets=[CharacterSheet(name="小明", front_url="/img/front.png")],
    )
    pdir = tmp_path / "course_projects" / "u1"
    pdir.mkdir(parents=True)
    (pdir / "cp_dddddddddddd.json").write_text(json.dumps(proj.to_dict(), ensure_ascii=False), encoding="utf-8")

    storyboard = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_image", width=1080, height=1920),
        frames=[StoryboardFrame(index=0, narration="测试")],
    )
    svc._prepare_storyboard_and_audio = AsyncMock(return_value=(storyboard, str(tmp_path / "a.mp3"), str(tmp_path / "s.srt")))
    svc._has_mixed_sources = MagicMock(return_value=False)
    visual = MagicMock()
    visual.render = AsyncMock()
    svc._make_visual_source = MagicMock(return_value=visual)
    svc._make_on_frame_done = MagicMock(return_value=lambda *a, **k: None)
    monkeypatch.setattr("app.gateway.video_gen.worker_probe.probe_worker_capabilities", AsyncMock(return_value={}))
    monkeypatch.setattr("app.gateway.video_gen.worker_probe.decide_pipeline_version", MagicMock(return_value="v2"))
    monkeypatch.setattr("app.gateway.video_gen.service.build_worker_payload", MagicMock(return_value={}))

    task_dir = _task_dir(tmp_path)
    progress = AsyncMock()
    # 模拟 _run_pipeline 已注入 character_sheets 到 payload（_inject_project_character_context）
    payload = {
        "course_project_id": "cp_dddddddddddd",
        "character_sheets": [{"front_url": "/img/front.png"}],
        "output_format": "mp4",
        "quality": "standard",
        "engine": "auto",
    }

    await svc._prepare_storyboard_video("ai_image", payload, task_dir, progress)

    _, kwargs = visual.render.call_args
    assert kwargs.get("reference_image_paths") == ["/img/front.png"], f"项目角色 front_url 应作 reference_image_paths 透传，实际 {kwargs}"


# ---------------------------------------------------------------------------
# speech_duration 落盘：draft audio / 单帧 regenerate 两条部分键写路径不丢字段
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_draft_audio_persists_speech_duration(tmp_path, monkeypatch):
    """draft 音频后台任务写盘 storyboard.json 时须带 speech_duration（_build_frame_audio_map 不丢键）。"""
    from app.gateway.video_gen import service as svc_mod
    from app.gateway.video_gen.orchestration import tts as tts_mod

    svc = _make_service(tmp_path)
    task = MagicMock()
    task.payload = "{}"
    svc.repo.get = AsyncMock(return_value=task)
    svc.repo.update = AsyncMock(return_value=None)

    sb = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_image", width=1080, height=1920),
        frames=[StoryboardFrame(index=0, narration="这是第一帧的旁白内容。")],
    )
    sb_path = _write_storyboard(tmp_path, sb)

    async def fake_synthesize(text, *, voice_id, speed, output_path, fallback_voice=None, **kwargs):
        Path(output_path).write_bytes(b"mp3")
        return tts_mod.TTSResult(audio_path=output_path, srt="", duration=3.0)

    monkeypatch.setattr(tts_mod, "synthesize", fake_synthesize)

    async def fake_run_ffmpeg(*a, **k):
        return None

    monkeypatch.setattr(svc_mod, "run_ffmpeg", fake_run_ffmpeg)

    await svc._generate_draft_audio("vg_test", _task_dir(tmp_path))

    on_disk = json.loads(sb_path.read_text(encoding="utf-8"))
    frame0 = on_disk["frames"][0]
    assert frame0.get("audio_path"), "draft 音频须落盘"
    assert frame0["duration"] == 3.0
    assert frame0["speech_duration"] == 3.0, "draft 路径盘上 storyboard.json 丢 speech_duration"


@pytest.mark.asyncio
async def test_regenerate_frame_audio_persists_speech_duration(tmp_path, monkeypatch):
    """单帧 regenerate 音频后，_audio_updater 写盘须带 speech_duration（不丢键）。"""
    from app.gateway.video_gen import service as svc_mod
    from app.gateway.video_gen.orchestration import tts as tts_mod

    svc = _make_service(tmp_path)
    task = MagicMock()
    task.status = "ready"
    task.task_type = "ai_image"
    task.payload = "{}"
    svc.repo.get = AsyncMock(return_value=task)

    sb = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_image", width=1080, height=1920),
        frames=[StoryboardFrame(index=0, narration="旁白内容", duration=3.0, speech_duration=3.0)],
    )
    sb_path = _write_storyboard(tmp_path, sb)

    fake_visual = MagicMock()
    fake_visual.render = AsyncMock(return_value=None)
    monkeypatch.setattr(svc, "_make_visual_source", lambda *a, **k: fake_visual)

    async def fake_synthesize(text, *, voice_id, speed, output_path, fallback_voice=None, **kwargs):
        Path(output_path).write_bytes(b"mp3")
        return tts_mod.TTSResult(audio_path=output_path, srt="", duration=4.0)

    monkeypatch.setattr(tts_mod, "synthesize", fake_synthesize)

    async def fake_run_ffmpeg(*a, **k):
        return None

    monkeypatch.setattr(svc_mod, "run_ffmpeg", fake_run_ffmpeg)

    await svc._regenerate_frame("vg_test", "u1", 0, _task_dir(tmp_path))

    on_disk = json.loads(sb_path.read_text(encoding="utf-8"))
    frame0 = on_disk["frames"][0]
    assert frame0["duration"] == 4.0
    assert frame0["speech_duration"] == 4.0, "regenerate 路径盘上 storyboard.json 丢 speech_duration"
