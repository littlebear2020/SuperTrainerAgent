"""Tests for plan_storyboard tool → VideoGenService wiring (阶段 1.8 补件).

plan_storyboard is the missing piece that lets the course-video agent create a
draft task + persist storyboard.json from chat, so it has a task_id to feed
generate_video. It maps video_type→task_type, builds an auto_render=False
payload, creates the task row, and awaits _wrap_pipeline until DRAFT (blocking —
the agent needs the blueprint on disk before it can present/confirm it).
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.gateway.video_gen.config import VideoGenConfig
from app.gateway.video_gen.providers.registry import ProviderRegistry
from app.gateway.video_gen.service import VideoGenService


def _make_service(tmp_path):
    cfg = VideoGenConfig(qihu_api_key="t", engine_arch="multi_engine")
    providers = ProviderRegistry(cfg)
    return VideoGenService(repo=MagicMock(), config=cfg, providers=providers, storage_dir=str(tmp_path), sandbox_provider=AsyncMock())


class _FakeTask:
    def __init__(self, *, task_id="vg_1", user_id="u1", task_type="vimax", status="draft", result=None, error_message=None):
        self.id = task_id
        self.user_id = user_id
        self.task_type = task_type
        self.status = status
        self.result = json.dumps(result) if result is not None else None
        self.error_message = error_message


# ── plan_storyboard_for_tool ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_plan_storyboard_creates_draft_task_with_auto_render_false(tmp_path):
    svc = _make_service(tmp_path)
    svc.repo.create = AsyncMock()
    svc.repo.get = AsyncMock(return_value=_FakeTask(status="draft", result={"storyboard": {"frames": [{}, {}, {}], "total_duration": 9.0}}))
    svc._wrap_pipeline = AsyncMock()

    with patch("app.gateway.video_gen.service.get_effective_user_id", return_value="u1"):
        result = await svc.plan_storyboard_for_tool(video_type="vimax", script="复利是世界第八大奇迹")

    assert result["status"] == "draft"
    assert result["video_type"] == "vimax"
    assert result["n_frames"] == 3
    assert result["total_duration"] == 9.0
    assert result["task_id"].startswith("vg_")

    # task row created with mapped task_type + draft-forcing payload
    svc.repo.create.assert_awaited_once()
    create_kwargs = svc.repo.create.call_args.kwargs
    assert create_kwargs["task_type"] == "vimax"
    assert create_kwargs["user_id"] == "u1"
    assert create_kwargs["domain"] == "video_gen"
    payload = json.loads(create_kwargs["payload"])
    assert payload["auto_render"] is False  # draft, NOT immediate render
    assert payload["mode"] == "fixed"
    assert payload["script"] == "复利是世界第八大奇迹"

    # _wrap_pipeline awaited to completion (blocking until DRAFT) with the same task_id
    svc._wrap_pipeline.assert_awaited_once()
    assert svc._wrap_pipeline.call_args.args[0] == create_kwargs["id"]


@pytest.mark.asyncio
async def test_plan_storyboard_topic_uses_generate_mode_and_maps_vimax(tmp_path):
    svc = _make_service(tmp_path)
    svc.repo.create = AsyncMock()
    svc.repo.get = AsyncMock(return_value=_FakeTask(status="draft", result={"storyboard": {"frames": []}}))
    svc._wrap_pipeline = AsyncMock()

    with patch("app.gateway.video_gen.service.get_effective_user_id", return_value="u1"):
        await svc.plan_storyboard_for_tool(video_type="vimax", topic="太空探索简史", n_scenes=4)

    payload = json.loads(svc.repo.create.call_args.kwargs["payload"])
    assert payload["mode"] == "generate"
    assert payload["topic"] == "太空探索简史"
    assert payload["n_scenes"] == 4
    assert payload["auto_render"] is False
    assert svc.repo.create.call_args.kwargs["task_type"] == "vimax"  # vimax → vimax


@pytest.mark.asyncio
async def test_plan_storyboard_rejects_unsupported_video_type(tmp_path):
    """Legacy/unmapped video types (e.g. cinematic) must fail fast, not create a task
    that can't render. vimax is now a supported engine (阶段 2.6)."""
    svc = _make_service(tmp_path)
    svc.repo.create = AsyncMock()

    with pytest.raises(ValueError, match="cinematic"):
        await svc.plan_storyboard_for_tool(video_type="cinematic", script="x")

    svc.repo.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_plan_storyboard_requires_script_or_topic(tmp_path):
    svc = _make_service(tmp_path)
    svc.repo.create = AsyncMock()

    with pytest.raises(ValueError, match="script.*or.*topic"):
        await svc.plan_storyboard_for_tool(video_type="vimax")

    svc.repo.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_plan_storyboard_raises_when_draft_failed(tmp_path):
    svc = _make_service(tmp_path)
    svc.repo.create = AsyncMock()
    svc.repo.get = AsyncMock(return_value=_FakeTask(status="failed", error_message="LLM blew up"))
    svc._wrap_pipeline = AsyncMock()

    with patch("app.gateway.video_gen.service.get_effective_user_id", return_value="u1"):
        with pytest.raises(RuntimeError, match="draft failed"):
            await svc.plan_storyboard_for_tool(video_type="vimax", script="x")


# ── wire_plan_storyboard_executor ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_wire_plan_storyboard_executor_makes_tool_delegate(tmp_path):
    from app.gateway.video_gen.engines.plan_storyboard_tool import PlanStoryboardTool, wire_plan_storyboard_executor

    svc = _make_service(tmp_path)
    svc.repo.create = AsyncMock()
    svc.repo.get = AsyncMock(return_value=_FakeTask(status="draft", result={"storyboard": {"frames": [{}]}}))
    svc._wrap_pipeline = AsyncMock()

    tool = PlanStoryboardTool()
    assert tool.execute_fn is None

    wire_plan_storyboard_executor(tool, svc)

    with patch("app.gateway.video_gen.service.get_effective_user_id", return_value="u1"):
        result = await tool._arun(video_type="vimax", script="hello")
    assert result["status"] == "draft"
    assert result["task_id"].startswith("vg_")


def test_plan_storyboard_tool_singleton_name():
    from app.gateway.video_gen.engines.plan_storyboard_tool import plan_storyboard_tool

    assert plan_storyboard_tool.name == "plan_storyboard"


def test_plan_storyboard_tool_advertises_vimax_in_schema_and_description():
    """The course-video SOUL tells the agent to call plan_storyboard for ALL video
    types including vimax. If the tool's field description / tool description omit
    vimax, the LLM may believe the tool doesn't support it and skip blueprint
    planning for story videos — a soft break in the chat path. Both must list vimax."""
    from app.gateway.video_gen.engines.plan_storyboard_tool import PlanStoryboardTool

    tool = PlanStoryboardTool()
    field_desc = tool.args_schema.model_fields["video_type"].description or ""
    assert "vimax" in field_desc, f"video_type field desc must list vimax, got: {field_desc!r}"
    assert "vimax" in tool.description, "tool description must mention vimax"


def test_plan_storyboard_description_routes_confirmation_through_ask_clarification():
    """2026-07-21 (vimax 对话流修复): 蓝图确认不再给"present_files 或 ask_clarification"
    二选一——选 present_files+纯文本等待会被 TodoMiddleware 防退出逻辑打回
    （jump_to model），逼 agent 跳过确认直接渲染。确认统一走 ask_clarification。"""
    from app.gateway.video_gen.engines.plan_storyboard_tool import PlanStoryboardTool

    tool = PlanStoryboardTool()
    assert "present_files 或 ask_clarification" not in tool.description
    assert "ask_clarification" in tool.description


@pytest.mark.asyncio
async def test_plan_storyboard_accepts_vimax_and_maps_task_type(tmp_path):
    """vimax must be plan-able end-to-end (not just not-rejected). The agent calls
    plan_storyboard(video_type='vimax') per SOUL step 2; it must create a vimax task."""
    svc = _make_service(tmp_path)
    svc.repo.create = AsyncMock()
    svc.repo.get = AsyncMock(return_value=_FakeTask(status="draft", result={"storyboard": {"frames": [{}]}}))
    svc._wrap_pipeline = AsyncMock()

    with patch("app.gateway.video_gen.service.get_effective_user_id", return_value="u1"):
        result = await svc.plan_storyboard_for_tool(video_type="vimax", script="一个角色的故事")

    assert result["status"] == "draft"
    assert result["video_type"] == "vimax"
    assert svc.repo.create.call_args.kwargs["task_type"] == "vimax"


# ── idea2video: story-confirmation flow (M3.1) ───────────────────────────────
# idea2video's draft must NOT produce a fake narration-based blueprint. Instead it
# expands the idea into a full story (ViMax develop_story) and stops at story_ready,
# so the user confirms the STORY before render. render then reuses story.txt.


@pytest.mark.asyncio
async def test_plan_storyboard_idea2video_expands_story_and_returns_story_ready(tmp_path, monkeypatch):
    """For vimax + topic (idea2video) mode, _generate_draft must expand the idea
    into a story (Screenwriter.develop_story) and return a PipelineDraft carrying
    the story (NOT a narration-based storyboard). The Entry 2 caller then sets
    status STORY_READY for user confirmation. generate_narrations (the fake
    blueprint path) must NOT run.

    Tested at the _generate_draft level directly: that's where the branch lives, and
    testing through plan_storyboard_for_tool would require _wrap_pipeline to run for
    real (heavy) — so we exercise the unit that owns the decision."""
    from unittest.mock import AsyncMock

    from vimax.agents.screenwriter import Screenwriter

    from app.gateway.video_gen.pipeline_result import PipelineDraft

    svc = _make_service(tmp_path)
    progress = AsyncMock()

    # ViMax Screenwriter.develop_story → a deterministic fake story
    async def fake_develop_story(self, idea, user_requirement=None):
        return f"扩写后的故事：{idea}"

    monkeypatch.setattr(Screenwriter, "develop_story", fake_develop_story)
    # avoid real LLM construction (config has no model key in tests)
    monkeypatch.setattr("deerflow.models.create_chat_model", lambda *a, **k: MagicMock())

    with patch(
        "app.gateway.video_gen.orchestration.script.generate_narrations",
        new=AsyncMock(side_effect=AssertionError("idea2video must NOT call generate_narrations")),
    ) as gen_narrations:
        result = await svc._generate_draft(
            task_type="vimax",
            payload={"pipeline": "idea2video", "topic": "发光贝壳的男孩", "mode": "generate", "aspect": "16:9"},
            task_dir=tmp_path,
            progress=progress,
        )

    # story-expansion branch taken
    assert isinstance(result, PipelineDraft)
    assert result.story is not None
    assert "发光贝壳的男孩" in result.story
    # no storyboard blueprint produced (the fake-blueprint path)
    assert result.storyboard == {}
    # story.txt persisted on disk (render will load + reuse it)
    assert (tmp_path / "story.txt").read_text(encoding="utf-8") == result.story
    gen_narrations.assert_not_called()


# ── tool-layer _arun forwards pipeline (regression: vg_30329afaaea9) ──────────
# The service layer (plan_storyboard_for_tool) accepts a pipeline kwarg and persists
# it to the task payload. But the TOOL layer (_arun) used to OMIT pipeline from both
# its signature and the execute() call → pipeline was silently dropped at the tool
# boundary. Result: idea2video requests reached VimaxEngine with pipeline=None →
# script2video branch → "task payload has no `script`" (vg_30329afaaea9).


@pytest.mark.asyncio
async def test_plan_storyboard_tool_arun_forwards_pipeline_to_executor():
    """_arun must forward the pipeline kwarg to execute_fn. Without this, an
    idea2video plan loses its pipeline at the tool boundary and later fails in
    VimaxEngine as script2video (no script) — vg_30329afaaea9."""
    from app.gateway.video_gen.engines.plan_storyboard_tool import PlanStoryboardTool

    captured = {}

    async def fake_execute(**kwargs):
        captured.update(kwargs)
        return {"task_id": "vg_x", "status": "draft", "video_type": "vimax", "n_frames": 0}

    tool = PlanStoryboardTool(execute_fn=fake_execute)

    await tool._arun(
        video_type="vimax",
        topic="一个数字小精灵的故事",
        pipeline="idea2video",  # ← this was silently dropped before the fix
        style="Anime Style",
    )

    # pipeline must reach the executor (it's what gets persisted to the task payload)
    assert captured.get("pipeline") == "idea2video", f"_arun dropped pipeline: got {captured.get('pipeline')!r} — idea2video requests would fail in VimaxEngine as script2video (no script)"
    # and the other args are still forwarded
    assert captured.get("video_type") == "vimax"
    assert captured.get("topic") == "一个数字小精灵的故事"


# ── idea2video story edit persistence (M3.1 follow-up) ────────────────────────


@pytest.mark.asyncio
async def test_update_story_rewrites_story_txt_and_result_for_story_ready_task(tmp_path):
    """A user editing the expanded story (idea2video StoryForm) must persist: the
    PUT /tasks/{id}/story handler calls update_story, which rewrites story.txt
    (so render reuses the edited story) AND patches result.story (so the UI shows
    the edited version). Task must be in story_ready to allow editing."""
    svc = _make_service(tmp_path)

    # task at story_ready with an original story on disk + in result
    task_dir = tmp_path / "u1" / "tasks" / "vg_story1"
    task_dir.mkdir(parents=True)
    (task_dir / "story.txt").write_text("原始故事", encoding="utf-8")

    task = MagicMock()
    task.id = "vg_story1"
    task.user_id = "u1"
    task.task_type = "vimax"
    task.status = "story_ready"
    task.result = json.dumps({"story": "原始故事"}, ensure_ascii=False)
    svc.repo.get = AsyncMock(return_value=task)
    svc.repo.update = AsyncMock()
    svc._storage_dir = str(tmp_path)

    updated = {}

    async def fake_set_result(**kwargs):
        updated.update(kwargs)

    # update_story uses the repo directly for result patch + writes story.txt
    await svc.update_story("vg_story1", user_id="u1", story="用户修改后的故事")

    # story.txt rewritten (render will load this)
    assert (task_dir / "story.txt").read_text(encoding="utf-8") == "用户修改后的故事"
    # result.story patched in the DB
    svc.repo.update.assert_awaited()
    # task_id passed positionally, result patched in kwargs
    assert svc.repo.update.call_args.args[0] == "vg_story1"
    assert "用户修改后的故事" in svc.repo.update.call_args.kwargs["result"]


@pytest.mark.asyncio
async def test_update_story_rejects_non_story_ready_task(tmp_path):
    """Only story_ready tasks (idea2video awaiting story confirm) can have their
    story edited — not draft (no story) or running."""
    svc = _make_service(tmp_path)
    task = MagicMock()
    task.status = "draft"
    svc.repo.get = AsyncMock(return_value=task)

    with pytest.raises(ValueError, match="story_ready"):
        await svc.update_story("vg_x", user_id="u1", story="edited")


@pytest.mark.asyncio
async def test_plan_storyboard_freezes_vimax_generate_script(tmp_path):
    """A2 (决策3): vimax + generate + script2video must freeze the plan-time
    narrations into payload.script (mode→fixed, topic dropped) so render reuses
    the exact confirmed content — prevents blueprint drift AND the audio/video
    mismatch (prep audio is split from the same frozen script VimaxEngine scripts)."""
    svc = _make_service(tmp_path)
    svc.repo.create = AsyncMock()
    svc.repo.get = AsyncMock(
        return_value=_FakeTask(
            task_type="vimax",
            status="draft",
            result={"storyboard": {"frames": [{"narration": "第一句"}, {"narration": "第二句"}], "total_duration": 6.0}},
        )
    )
    svc.repo.update = AsyncMock()
    svc._wrap_pipeline = AsyncMock()

    with patch("app.gateway.video_gen.service.get_effective_user_id", return_value="u1"):
        await svc.plan_storyboard_for_tool(video_type="vimax", topic="职场沟通")

    svc.repo.update.assert_awaited()
    frozen_payload = json.loads(svc.repo.update.call_args.kwargs["payload"])
    assert frozen_payload["script"] == "第一句\n第二句"
    assert "topic" not in frozen_payload
    assert frozen_payload["mode"] == "fixed"


@pytest.mark.asyncio
async def test_plan_storyboard_does_not_freeze_vimax_idea2video(tmp_path):
    """idea2video has its own story-confirmation flow (story.txt + STORY_READY);
    A2 freeze must not touch it (pipeline == idea2video is excluded)."""
    svc = _make_service(tmp_path)
    svc.repo.create = AsyncMock()
    svc.repo.get = AsyncMock(return_value=_FakeTask(task_type="vimax", status="story_ready", result={"story": "一段故事"}))
    svc.repo.update = AsyncMock()
    svc._wrap_pipeline = AsyncMock()

    with patch("app.gateway.video_gen.service.get_effective_user_id", return_value="u1"):
        await svc.plan_storyboard_for_tool(video_type="vimax", topic="一个想法", pipeline="idea2video")

    svc.repo.update.assert_not_awaited()


@pytest.mark.asyncio
async def test_plan_storyboard_target_duration_sets_scene_limits(tmp_path):
    """Phase 2: target_duration=60 → max_scenes=1, max_shots=8, user_requirement_extra injected,
    n_scenes overridden to 1."""
    svc = _make_service(tmp_path)
    svc.repo.create = AsyncMock()
    svc.repo.get = AsyncMock(return_value=_FakeTask(status="draft", result={"storyboard": {"frames": []}}))
    svc._wrap_pipeline = AsyncMock()

    with patch("app.gateway.video_gen.service.get_effective_user_id", return_value="u1"):
        await svc.plan_storyboard_for_tool(
            video_type="vimax",
            topic="测试",
            pipeline="idea2video",
            target_duration=60,
        )

    payload = json.loads(svc.repo.create.call_args.kwargs["payload"])
    assert payload["n_scenes"] == 1, f"n_scenes should be 1 for 60s, got {payload['n_scenes']}"
    assert "user_requirement_extra" in payload
    assert "exactly 1 scene" in payload["user_requirement_extra"]
    assert "not more than 8 shots" in payload["user_requirement_extra"].lower()


@pytest.mark.asyncio
async def test_plan_storyboard_target_duration_180s_two_scenes(tmp_path):
    """Phase 2: target_duration=180 → max_scenes=2."""
    svc = _make_service(tmp_path)
    svc.repo.create = AsyncMock()
    svc.repo.get = AsyncMock(return_value=_FakeTask(status="draft", result={"storyboard": {"frames": []}}))
    svc._wrap_pipeline = AsyncMock()

    with patch("app.gateway.video_gen.service.get_effective_user_id", return_value="u1"):
        await svc.plan_storyboard_for_tool(
            video_type="vimax",
            topic="测试",
            pipeline="idea2video",
            target_duration=180,
        )

    payload = json.loads(svc.repo.create.call_args.kwargs["payload"])
    assert payload["n_scenes"] == 2
    assert "exactly 2 scene" in payload["user_requirement_extra"]


@pytest.mark.asyncio
async def test_plan_storyboard_no_target_duration_no_extra(tmp_path):
    """Phase 2: without target_duration, no user_requirement_extra is injected."""
    svc = _make_service(tmp_path)
    svc.repo.create = AsyncMock()
    svc.repo.get = AsyncMock(return_value=_FakeTask(status="draft", result={"storyboard": {"frames": []}}))
    svc._wrap_pipeline = AsyncMock()

    with patch("app.gateway.video_gen.service.get_effective_user_id", return_value="u1"):
        await svc.plan_storyboard_for_tool(video_type="vimax", topic="测试", pipeline="idea2video")

    payload = json.loads(svc.repo.create.call_args.kwargs["payload"])
    assert "user_requirement_extra" not in payload


@pytest.mark.asyncio
async def test_plan_storyboard_vimax_script2video_defaults_target_duration_180(tmp_path):
    """D2 (2026-07-15 split): vimax script2video (fixed script) without an explicit
    target_duration defaults to 180s — injects scene limits so a long script isn't
    under-split. idea2video is excluded (it's story-driven; duration follows the
    expanded story — see test_plan_storyboard_no_target_duration_no_extra)."""
    svc = _make_service(tmp_path)
    svc.repo.create = AsyncMock()
    svc.repo.get = AsyncMock(return_value=_FakeTask(status="draft", result={"storyboard": {"frames": []}}))
    svc._wrap_pipeline = AsyncMock()

    with patch("app.gateway.video_gen.service.get_effective_user_id", return_value="u1"):
        await svc.plan_storyboard_for_tool(video_type="vimax", script="一段较长的脚本内容", pipeline="script2video")

    payload = json.loads(svc.repo.create.call_args.kwargs["payload"])
    assert "user_requirement_extra" in payload, "vimax script2video should default target_duration=180 → inject scene limits"
    assert payload["n_scenes"] == 2  # 180s → max_scenes=ceil(180/90)=2


@pytest.mark.asyncio
async def test_plan_course_large_storyboard_no_narration_fallback(monkeypatch):
    """M1-1: 170 帧无 scene_groups + bulk LLM 提前停返短 → 每帧仍得真实 image_prompt，
    绝不回退 narration（修前 :178 回退致 #22 起画面词==旁白）。"""
    from app.gateway.video_gen.orchestration import storyboard_planner as sp
    from app.gateway.video_gen.storyboard import (
        Storyboard,
        StoryboardConfig,
        StoryboardFrame,
    )

    sb = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_image", width=1152, height=2048),
    )
    for i in range(170):
        sb.frames.append(StoryboardFrame(index=i, narration=f"旁白{i}"))

    async def short_bulk(llm, storyboard, style_prefix, **kw):
        # 模拟 LLM 对任意块提前停止，只返 3 条
        return ["bulk0", "bulk1", "bulk2"], ["ai_image"] * 3

    monkeypatch.setattr(sp, "_generate_course_prompts_enhanced", short_bulk)

    async def perframe(llm, narrations, **kw):
        return [f"pf-{n}" for n in narrations]

    monkeypatch.setattr(sp, "_generate_image_prompts", perframe)

    # E8（2026-08-21）：默认 chunk=1 逐帧——short_bulk 对 1 帧 mini 恒未对齐
    # （返 3 条），重试 1 次后全部落主题模板（QP1-6 不变量不变）。
    await sp.plan_course(None, storyboard=sb, style_context={})

    assert len(sb.frames) == 170
    bad = [i for i, f in enumerate(sb.frames) if not f.image_prompt or f.image_prompt == f.narration]
    assert not bad, f"这些帧回退了 narration 或空: {bad[:5]}"
    assert "以「t」为主题" in sb.frames[0].image_prompt, "逐帧模式两次未对齐 → 主题模板兜底"
    assert "以「t」为主题" in sb.frames[3].image_prompt


@pytest.mark.asyncio
async def test_plan_course_large_storyboard_bulk_fallback_path(monkeypatch):
    """E8 配置级回退：chunk_size=8 时保留批量路径——bulk 未对齐整块丢弃后，
    缺失帧走单帧 _generate_image_prompts 兜底（修前主路径的机制不变）。"""
    from app.gateway.video_gen.orchestration import storyboard_planner as sp
    from app.gateway.video_gen.storyboard import (
        Storyboard,
        StoryboardConfig,
        StoryboardFrame,
    )

    sb = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_image", width=1152, height=2048),
    )
    for i in range(16):
        sb.frames.append(StoryboardFrame(index=i, narration=f"旁白{i}"))

    async def short_bulk(llm, storyboard, style_prefix, **kw):
        return ["bulk0", "bulk1", "bulk2"], ["ai_image"] * 3

    monkeypatch.setattr(sp, "_generate_course_prompts_enhanced", short_bulk)

    async def perframe(llm, narrations, **kw):
        return [f"pf-{n}" for n in narrations]

    monkeypatch.setattr(sp, "_generate_image_prompts", perframe)

    await sp.plan_course(None, storyboard=sb, style_context={}, chunk_size=8)

    bad = [i for i, f in enumerate(sb.frames) if not f.image_prompt or f.image_prompt == f.narration]
    assert not bad, f"这些帧回退了 narration 或空: {bad[:5]}"
    # BUG 1-A 起：bulk 返 3 条 < chunk 长度 8 → 判未对齐整块丢弃，所有帧走单帧兜底。
    assert sb.frames[0].image_prompt == "pf-旁白0"
    assert sb.frames[3].image_prompt == "pf-旁白3"


# ── 2026-07-22: 确认门黑盒修复——返回蓝图内容，不只是计数 ─────────────────


@pytest.mark.asyncio
async def test_plan_storyboard_returns_blueprint_content_for_confirmation_gate(tmp_path):
    """2026-07-22 确认门黑盒修复：确认门必须确认用户看得见的内容。storyboard.json
    在 AioSandbox 外（.deer-flow/video-gen/...），agent 的 present_files/文件工具
    够不着（present_file_tool 硬拒沙箱外路径）——只回 n_frames 个数字等于让
    agent 和用户确认一个黑盒。返回值必须内联携带每镜文本内容。"""
    svc = _make_service(tmp_path)
    svc.repo.create = AsyncMock()
    svc.repo.get = AsyncMock(
        return_value=_FakeTask(
            status="draft",
            result={
                "storyboard": {
                    "frames": [
                        {"index": 0, "narration": "旁白一", "image_prompt": "a cat in a hat", "duration": 5.0},
                        {"index": 1, "narration": "旁白二", "image_prompt": "a dog", "duration": 6.0},
                    ],
                    "total_duration": 11.0,
                }
            },
        )
    )
    svc._wrap_pipeline = AsyncMock()

    with patch("app.gateway.video_gen.service.get_effective_user_id", return_value="u1"):
        result = await svc.plan_storyboard_for_tool(video_type="vimax", script="两只动物的故事")

    assert result["n_frames"] == 2
    assert [f["narration"] for f in result["blueprint"]] == ["旁白一", "旁白二"]
    assert result["blueprint"][0]["visual"] == "a cat in a hat"
    assert result["blueprint"][0]["duration"] == 5.0


@pytest.mark.asyncio
async def test_plan_storyboard_returns_story_for_idea2video_confirmation(tmp_path):
    """idea2video 的确认对象是扩写的故事（status=story_ready，result.story）。
    SOUL 步骤 3 要求'展示扩写后的故事'——故事同样必须内联返回，否则 idea2video
    的确认门也是黑盒。"""
    svc = _make_service(tmp_path)
    svc.repo.create = AsyncMock()
    svc.repo.get = AsyncMock(return_value=_FakeTask(status="story_ready", result={"story": "小明走进森林…"}))
    svc._wrap_pipeline = AsyncMock()

    with patch("app.gateway.video_gen.service.get_effective_user_id", return_value="u1"):
        result = await svc.plan_storyboard_for_tool(video_type="vimax", topic="森林冒险", pipeline="idea2video")

    assert result["status"] == "story_ready"
    assert result["story"] == "小明走进森林…"
    assert result["blueprint"] == []  # 故事阶段还没有分镜


def test_plan_storyboard_description_requires_showing_blueprint_content():
    """只报'共 N 镜约 X 秒'的确认 = 确认黑盒。工具描述必须要求先把 blueprint/story
    内容贴给用户再 ask_clarification。"""
    from app.gateway.video_gen.engines.plan_storyboard_tool import PlanStoryboardTool

    tool = PlanStoryboardTool()
    assert "blueprint" in tool.description
    assert "ask_clarification" in tool.description


# ── 2026-07-22: remotion 工具层摘牌（split B1 补执行）──────────────────────


@pytest.mark.asyncio
async def test_plan_storyboard_rejects_remotion_after_tool_layer_removal(tmp_path):
    """2026-07-22: remotion 从工具层摘牌。2026-07-15 split 的 B1 只在 HTTP 层执行了
    （/api/video/create 422），工具层仍映射 remotion → 一次工具调用即可绕回已删除的
    legacy template 渲染路径（_render_remotion_storyboard，依赖沙箱 /opt/remotion-template）。
    remotion 走 course-video agent + auto-video-flow 直写，不再经过 plan_storyboard。"""
    svc = _make_service(tmp_path)
    with pytest.raises(ValueError, match="unsupported video_type"):
        await svc.plan_storyboard_for_tool(video_type="remotion", script="复利是世界第八大奇迹")


@pytest.mark.asyncio
async def test_run_pipeline_for_tool_rejects_remotion(tmp_path):
    """generate_video 入口同样摘牌：历史 remotion 草稿也不得再经工具触发 legacy 渲染。"""
    svc = _make_service(tmp_path)
    with pytest.raises(ValueError, match="remotion"):
        await svc.run_pipeline_for_tool(video_type="remotion", task_id="vg_legacy")


def test_plan_storyboard_tool_schema_no_longer_advertises_remotion():
    from app.gateway.video_gen.engines.plan_storyboard_tool import PlanStoryboardTool

    tool = PlanStoryboardTool()
    field_desc = tool.args_schema.model_fields["video_type"].description or ""
    assert "remotion" not in field_desc
    assert "remotion" not in tool.description


def test_generate_video_tool_schema_no_longer_advertises_remotion():
    from app.gateway.video_gen.engines.tool import GenerateVideoTool

    tool = GenerateVideoTool()
    field_desc = tool.args_schema.model_fields["video_type"].description or ""
    assert "remotion" not in field_desc
    assert "remotion" not in tool.description
