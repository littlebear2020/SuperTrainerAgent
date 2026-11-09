"""一期 C3/C9：全维度优先级仲裁统一 + 重渲染门控（决策 3/5/14/20）。

计划：docs/plans/2026-08-24-course-project-consistency-plan.md 第四节 T1。
- TC-C3-01~05：`_resolve_effective_style` 新语义（跟随项目/哨兵/显式脱离/无项目两态）
- TC-C3-06：3 处仲裁点（generate/draft/prompt 重生端点）注入结果一致
- TC-C3-07：音色/字幕生效来源留痕
- TC-C3-08：`VIDEO_GEN_STYLE_ARBITRATION_V2=0` 回退旧「项目遮蔽」+ 哨兵防御
- TC-C9-01~03：重渲染门控（脱离不重注 / 跟随重取 / 留痕刷新）
"""

from __future__ import annotations

import inspect
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.gateway.video_gen.config import VideoGenConfig
from app.gateway.video_gen.course_models import CourseProject
from app.gateway.video_gen.providers.registry import ProviderRegistry
from app.gateway.video_gen.service import VideoGenService

WATERCOLOR_PREFIX = CourseProject(id="x", user_id="u", title="t", visual_style_preset="watercolor").style_prefix
PROFESSIONAL_PREFIX = CourseProject(id="x", user_id="u", title="t", visual_style_preset="professional").style_prefix


def _make_service(tmp_path) -> VideoGenService:
    cfg = VideoGenConfig(qihu_api_key="t", script_model="test-script-model")
    repo = MagicMock()
    repo.update = AsyncMock(return_value=None)
    return VideoGenService(
        repo=repo,
        config=cfg,
        providers=ProviderRegistry(cfg),
        storage_dir=str(tmp_path),
        sandbox_provider=AsyncMock(),
    )


def _seed_project(tmp_path, user="default", pid="cp_abcdef000001", **fields) -> str:
    d = tmp_path / "course_projects" / user
    d.mkdir(parents=True, exist_ok=True)
    data = {"id": pid, "user_id": user, "title": "仲裁项目", "visual_style_preset": "watercolor", **fields}
    (d / f"{pid}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return pid


async def _prog(**kw):
    pass


# ── TC-C3-01~05：_resolve_effective_style 新语义 ─────────────────────────────


def test_tc_c3_01_follow_project_when_style_unset(tmp_path):
    """绑项目 + style 未设 → source=project、注入项目前缀、留痕写入。"""
    svc = _make_service(tmp_path)
    pid = _seed_project(tmp_path)
    payload = {"course_project_id": pid}

    es = svc._resolve_effective_style(payload, user_id="default")

    assert es.source == "project"
    assert es.style_prefix == WATERCOLOR_PREFIX
    assert es.course_project_id == pid
    assert payload["effective_style_source"] == "project"


def test_tc_c3_02_sentinel_follow_project_semantics(tmp_path):
    """绑项目 + style="follow_project" → 同 TC-C3-01，且哨兵归一不落脏值。"""
    svc = _make_service(tmp_path)
    pid = _seed_project(tmp_path)
    payload = {"course_project_id": pid, "style": "follow_project"}

    es = svc._resolve_effective_style(payload, user_id="default")

    assert es.source == "project"
    assert es.style_prefix == WATERCOLOR_PREFIX
    assert payload["effective_style_source"] == "project"
    # 哨兵不得以原样流向下游（narration LLM / StoryboardConfig.style / storyboard.json）
    assert payload["style"] == "default"


def test_tc_c3_03_explicit_style_full_detach(tmp_path):
    """绑项目 + style 显式 → source=task；项目前缀/角色三视图零注入（全断口径）。"""
    from app.gateway.video_gen.service import _inject_project_character_context

    svc = _make_service(tmp_path)
    pid = _seed_project(
        tmp_path,
        character_reference="/storage/a.png",
        character_sheets=[{"name": "小明", "front_url": "/f.png", "side_url": "/s.png", "back_url": "/b.png", "features": ""}],
    )
    payload = {"course_project_id": pid, "style": "professional"}

    es = svc._resolve_effective_style(payload, user_id="default")

    assert es.source == "task"
    assert es.style_prefix == PROFESSIONAL_PREFIX  # 脱离：用 payload 风格，非项目前缀
    assert payload["effective_style_source"] == "task"

    # 全断实现点：_inject_project_character_context 门控——角色 sheets 不注入
    _inject_project_character_context(str(tmp_path), payload, "default")
    assert "character_sheets" not in payload, "显式脱离任务不得注入角色三视图"
    assert payload.get("character_reference") != "/storage/a.png"

    # 反例：跟随项目 → sheets 正常注入（门控只拦 task）
    follow_payload = {"course_project_id": pid}
    svc._resolve_effective_style(follow_payload, user_id="default")
    _inject_project_character_context(str(tmp_path), follow_payload, "default")
    assert follow_payload.get("character_sheets"), "跟随项目任务应注入角色三视图"


def test_tc_c3_04_no_project_explicit_style(tmp_path):
    """无项目 + style 显式 → source=task（现状不变）。"""
    svc = _make_service(tmp_path)
    payload = {"style": "professional"}

    es = svc._resolve_effective_style(payload, user_id="default")

    assert es.source == "task"
    assert es.style_prefix == PROFESSIONAL_PREFIX
    assert payload["effective_style_source"] == "task"


def test_tc_c3_05_no_project_default_style(tmp_path):
    """无项目 + style 默认 → source=none（现状不变）。"""
    svc = _make_service(tmp_path)
    payload = {}

    es = svc._resolve_effective_style(payload, user_id="default")

    assert es.source == "none"
    assert es.style_prefix == ""
    assert payload["effective_style_source"] == "none"


# ── TC-C3-06：3 处仲裁点注入结果一致 ────────────────────────────────────────


@pytest.mark.asyncio
async def test_tc_c3_06_draft_point_uses_helper(tmp_path, monkeypatch):
    """draft 仲裁点（_generate_draft）经统一 helper：跟随项目注入项目前缀。"""
    svc = _make_service(tmp_path)
    pid = _seed_project(tmp_path)
    task_dir = tmp_path / "default" / "tasks" / "vg_c306"
    task_dir.mkdir(parents=True)

    captured: dict = {}

    async def fake_plan_course(llm, *, storyboard, style_context, task_dir, **kw):
        captured["style_context"] = style_context

    monkeypatch.setattr("app.gateway.video_gen.orchestration.storyboard_planner.plan_course", fake_plan_course)
    monkeypatch.setattr("app.gateway.video_gen.service.create_script_llm", lambda p, c: MagicMock())

    payload = {"script": "第一镜旁白。第二镜旁白。", "ai_normalize_script": False, "course_project_id": pid}
    await svc._generate_draft("ai_image", payload, task_dir, _prog, user_id="default", task_id="vg_c306")

    assert captured["style_context"], "draft 路径应注入项目 style_context"
    assert captured["style_context"].get("style_prefix") == WATERCOLOR_PREFIX
    assert payload["effective_style_source"] == "project"

    # 显式脱离 → draft 注入 payload 前缀（同一 helper 语义）
    captured.clear()
    payload2 = {"script": "第一镜旁白。", "ai_normalize_script": False, "course_project_id": pid, "style": "professional"}
    task_dir2 = tmp_path / "default" / "tasks" / "vg_c306b"
    task_dir2.mkdir(parents=True)
    await svc._generate_draft("ai_image", payload2, task_dir2, _prog, user_id="default", task_id="vg_c306b")
    assert captured["style_context"].get("style_prefix") == PROFESSIONAL_PREFIX
    assert payload2["effective_style_source"] == "task"


def test_tc_c3_06_generate_point_routes_through_helper():
    """generate 仲裁点（_prepare_storyboard_and_audio）不得残留旧 if/else 旁路。

    静态守护（同 C8 手法）：三处仲裁点都必须经 `_resolve_effective_style`，
    禁止直接调用 `_load_course_project_style` 绕过仲裁语义。
    """
    from app.gateway.video_gen import service as service_mod

    for fn in (service_mod.VideoGenService._prepare_storyboard_and_audio, service_mod.VideoGenService._generate_draft):
        src = inspect.getsource(fn)
        assert "_resolve_effective_style(" in src, f"{fn.__name__} 须经统一仲裁 helper"
        assert "_load_course_project_style" not in src, f"{fn.__name__} 不得绕过仲裁直接取项目风格"

    from app.gateway.routers import video_gen as router_mod

    rsrc = inspect.getsource(router_mod.regenerate_frame_prompt)
    assert "_resolve_effective_style" in rsrc, "prompt 重生端点须经统一仲裁 helper"


@pytest.mark.asyncio
async def test_tc_c3_06_prompt_regen_point_detached_uses_payload_style(tmp_path):
    """prompt 重生端点：显式脱离 → 用 payload 风格前缀（旧逻辑是项目遮蔽，V2 起脱离）。"""
    from app.gateway._shared.task_framework.schemas import DRAFT
    from app.gateway.routers.video_gen import regenerate_frame_prompt
    from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame

    svc = _make_service(tmp_path)
    pid = _seed_project(tmp_path)

    task = MagicMock()
    task.status = DRAFT
    task.task_type = "ai_image"
    task.payload = json.dumps({"course_project_id": pid, "style": "professional"})
    svc.get_task = AsyncMock(return_value=task)

    task_dir = tmp_path / "u1" / "tasks" / "vg_c306c"
    task_dir.mkdir(parents=True)
    sb = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_image", width=1080, height=1920),
        frames=[StoryboardFrame(index=0, narration="hello")],
    )
    (task_dir / "storyboard.json").write_text(json.dumps(sb.to_dict(), ensure_ascii=False), encoding="utf-8")

    async def _fake_gen(llm, narrations, **kw):
        return ["regenerated prompt"]

    with (
        patch("app.gateway.routers.video_gen.get_effective_user_id", return_value="u1"),
        patch("deerflow.models.create_chat_model", return_value=MagicMock()),
        patch(
            "app.gateway.video_gen.orchestration.storyboard_planner._generate_image_prompts",
            new=AsyncMock(side_effect=_fake_gen),
        ),
    ):
        await regenerate_frame_prompt(_request_with_svc(svc), "vg_c306c", 0)

    on_disk = json.loads((task_dir / "storyboard.json").read_text(encoding="utf-8"))
    prompt = on_disk["frames"][0]["image_prompt"]
    assert prompt.startswith(PROFESSIONAL_PREFIX), f"显式脱离须用 payload 风格前缀：{prompt!r}"
    assert not prompt.startswith(WATERCOLOR_PREFIX), "显式脱离不得用项目风格前缀"


def _request_with_svc(svc):
    request = MagicMock()
    request.app.state.video_gen_service = svc
    return request


# ── TC-C3-07：音色/字幕生效来源留痕 ────────────────────────────────────────


@pytest.mark.asyncio
async def test_tc_c3_07_voice_subtitle_source_trace(tmp_path, monkeypatch):
    """三态 → effective_voice_source / effective_subtitle_source 正确。"""
    svc = _make_service(tmp_path)
    pid = _seed_project(tmp_path, voice_id="preset:332", subtitle_style="bold-yellow")
    monkeypatch.setattr("app.gateway.video_gen.service.create_script_llm", lambda p, c: MagicMock())
    monkeypatch.setattr(
        "app.gateway.video_gen.orchestration.storyboard_planner.plan_course",
        AsyncMock(),
    )

    def _task_dir(i):
        d = tmp_path / "default" / "tasks" / f"vg_c307_{i}"
        d.mkdir(parents=True)
        return d

    # ① 显式（task）
    p1 = {"script": "显式旁白。", "ai_normalize_script": False, "course_project_id": pid, "voice_id": "preset:111", "subtitle_style": "clean-white"}
    await svc._generate_draft("ai_image", p1, _task_dir(1), _prog, user_id="default", task_id="vg_c307_1")
    assert p1["effective_voice_source"] == "task"
    assert p1["effective_subtitle_source"] == "task"

    # ② 跟随项目（project）
    p2 = {"script": "跟随旁白。", "ai_normalize_script": False, "course_project_id": pid}
    await svc._generate_draft("ai_image", p2, _task_dir(2), _prog, user_id="default", task_id="vg_c307_2")
    assert p2["effective_voice_source"] == "project"
    assert p2["effective_subtitle_source"] == "project"

    # ③ 无项目 → 音色 lang_default（回落链）、字幕 default
    p3 = {"script": "无项目旁白。", "ai_normalize_script": False}
    await svc._generate_draft("ai_image", p3, _task_dir(3), _prog, user_id="default", task_id="vg_c307_3")
    assert p3["effective_voice_source"] == "lang_default"
    assert p3["effective_subtitle_source"] == "default"


@pytest.mark.asyncio
async def test_tc_c3_07_generate_path_voice_source_trace(tmp_path, monkeypatch):
    """generate 主链路（_prepare_storyboard_and_audio）音色来源留痕三态（独立复核补漏）。

    一期评审发现该路径漏写 effective_voice_source（draft/re_render 均写了）；
    skip_tts=True 跳过 TTS 重段，仅验证留痕。"""
    svc = _make_service(tmp_path)
    pid = _seed_project(tmp_path, voice_id="preset:332")
    monkeypatch.setattr("app.gateway.video_gen.service.create_script_llm", lambda p, c: MagicMock())
    monkeypatch.setattr(
        "app.gateway.video_gen.orchestration.storyboard_planner.plan_course",
        AsyncMock(),
    )

    async def _run(payload, i):
        d = tmp_path / "default" / "tasks" / f"vg_c307g_{i}"
        d.mkdir(parents=True)
        await svc._prepare_storyboard_and_audio("ai_image", payload, d, _prog, user_id="default", task_id=f"vg_c307g_{i}", skip_tts=True)

    # ① 显式（task）
    p1 = {"script": "显式旁白。", "ai_normalize_script": False, "course_project_id": pid, "voice_id": "preset:111"}
    await _run(p1, 1)
    assert p1["effective_voice_source"] == "task"

    # ② 跟随项目（project）
    p2 = {"script": "跟随旁白。", "ai_normalize_script": False, "course_project_id": pid}
    await _run(p2, 2)
    assert p2["effective_voice_source"] == "project"

    # ③ 无项目 → lang_default（回落链）
    p3 = {"script": "无项目旁白。", "ai_normalize_script": False}
    await _run(p3, 3)
    assert p3["effective_voice_source"] == "lang_default"


# ── TC-C3-08：仲裁回退开关 ─────────────────────────────────────────────────


def test_tc_c3_08_v2_off_restores_project_shadowing(tmp_path, monkeypatch):
    """V2=0 → 旧「项目遮蔽」行为：显式风格也被项目遮蔽，且不写留痕。"""
    monkeypatch.setenv("VIDEO_GEN_STYLE_ARBITRATION_V2", "0")
    svc = _make_service(tmp_path)
    pid = _seed_project(tmp_path)

    payload = {"course_project_id": pid, "style": "professional"}
    es = svc._resolve_effective_style(payload, user_id="default")

    assert es.style_prefix == WATERCOLOR_PREFIX, "旧分支：项目遮蔽（显式风格不脱离）"
    assert "effective_style_source" not in payload, "旧分支不写留痕"


def test_tc_c3_08_v2_off_sentinel_treated_as_default(tmp_path, monkeypatch):
    """V2=0 下哨兵 follow_project 同样按 default 防御：无项目不产生异常前缀/脏值。"""
    monkeypatch.setenv("VIDEO_GEN_STYLE_ARBITRATION_V2", "0")
    svc = _make_service(tmp_path)

    payload = {"style": "follow_project"}
    es = svc._resolve_effective_style(payload, user_id="default")

    assert es.source == "none"
    assert es.style_prefix == ""
    assert payload["style"] == "default", "哨兵不得持久化为脏值"
    assert "effective_style_source" not in payload

    # 绑项目 + 哨兵 → 项目遮蔽照旧，脏值同样归一
    pid = _seed_project(tmp_path)
    payload2 = {"course_project_id": pid, "style": "follow_project"}
    es2 = svc._resolve_effective_style(payload2, user_id="default")
    assert es2.style_prefix == WATERCOLOR_PREFIX
    assert payload2["style"] == "default"


# ── TC-C9-01~03：重渲染门控（决策 14）─────────────────────────────────────


def _bare_service_for_re_render(tmp_path, payload: dict) -> VideoGenService:
    """仿 test_re_render_settings_refresh 的裸实例 + FakeRepo（真实注入/解析链）。"""
    svc = VideoGenService.__new__(VideoGenService)
    svc._storage_dir = str(tmp_path)
    svc._RE_RENDER_ALLOWED_TYPES = ("ai_image", "ai_video")

    class FakeRepo:
        async def get(self, task_id, user_id=None):
            return SimpleNamespace(id=task_id, status="ready", task_type="ai_image", payload=json.dumps(payload))

    svc.repo = FakeRepo()
    return svc


def _capture_pipeline(svc, monkeypatch):
    seen: list[dict] = []

    async def fake_wrap(task_id, user_id, task_type, payload):
        seen.append(payload)

    created: list = []
    monkeypatch.setattr(svc, "_wrap_pipeline", fake_wrap)
    monkeypatch.setattr(svc, "_register_active_task", lambda tid, factory: (created.append(factory()), True)[1])

    async def finish():
        await asyncio_gather(created)

    return seen, finish


async def asyncio_gather(created):
    import asyncio

    await asyncio.gather(*[c for c in created if asyncio.iscoroutine(c) or hasattr(c, "__await__")])


@pytest.mark.asyncio
async def test_tc_c9_01_detached_re_render_injects_nothing(tmp_path, monkeypatch):
    """脱离任务重渲染 → payload 无角色 sheets/无锚图（force=True 不再无条件重注）。"""
    pid = _seed_project(
        tmp_path,
        character_reference="/storage/a.png",
        character_sheets=[{"name": "小明", "front_url": "/f.png", "side_url": "/s.png", "back_url": "/b.png", "features": ""}],
    )
    svc = _bare_service_for_re_render(tmp_path, {"course_project_id": pid, "style": "professional"})
    seen, finish = _capture_pipeline(svc, monkeypatch)

    await svc.re_render_task("vg_c901", "default")
    await finish()

    p = seen[0]
    assert "character_sheets" not in p, "全断任务重渲染不得重注角色三视图"
    assert "character_reference" not in p
    assert "style_anchor_image" not in p, "全断任务重渲染不得重注锚图"
    assert p["effective_style_source"] == "task"


@pytest.mark.asyncio
async def test_tc_c9_02_follow_re_render_re_resolves_project_settings(tmp_path, monkeypatch):
    """跟随任务改项目锚图/音色后重渲染 → 未显式部分重取。"""
    pid = _seed_project(
        tmp_path,
        voice_id="preset:332",
        subtitle_style="bold-yellow",
        character_sheets=[{"name": "小明", "front_url": "/f.png", "side_url": "/s.png", "back_url": "/b.png", "features": ""}],
    )
    svc = _bare_service_for_re_render(tmp_path, {"course_project_id": pid, "voice_id": None, "subtitle_style": None})
    seen, finish = _capture_pipeline(svc, monkeypatch)

    await svc.re_render_task("vg_c902", "default")
    await finish()

    p = seen[0]
    assert p["voice_id"] == "preset:332", "未显式指定 → 重取项目音色"
    assert p["subtitle_style"] == "bold-yellow", "未显式指定 → 重取项目字幕样式"
    assert p.get("character_sheets"), "跟随任务重渲染 force 重注项目 sheets"
    assert p["effective_style_source"] == "project"


@pytest.mark.asyncio
async def test_tc_c9_03_re_render_refreshes_source_trace(tmp_path, monkeypatch):
    """重渲染后 effective_*_source 与当前仲裁一致（显式音色优先 + 跟随风格）。"""
    pid = _seed_project(tmp_path, voice_id="preset:332", subtitle_style="bold-yellow")
    svc = _bare_service_for_re_render(
        tmp_path,
        {"course_project_id": pid, "style": None, "voice_id": "preset:111", "subtitle_style": "default"},
    )
    seen, finish = _capture_pipeline(svc, monkeypatch)

    await svc.re_render_task("vg_c903", "default")
    await finish()

    p = seen[0]
    assert p["effective_style_source"] == "project", "风格未显式 → 跟随项目"
    assert p["effective_voice_source"] == "task", "音色显式选择优先（不重取）"
    assert p["effective_subtitle_source"] == "project", "字幕 default 视为未设置 → 重取项目"
    assert p["voice_id"] == "preset:111"
    assert p["subtitle_style"] == "bold-yellow"
