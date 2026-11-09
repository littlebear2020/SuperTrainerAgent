"""T-E11（D6，2026-09-09 feedback-round1）：项目级语速三级链 + TTS section_id 透传。

- 挂项目任务语速取项目值（draft 落盘 storyboard.config.tts_speed）
- 任务显式传值覆盖项目值
- 无项目任务走 config 缺省
- TTS 调用挂项目带 section_id=course_project_id（豆包多轮语境关联 → 系列课程
  语气连续）；无项目任务 section_id=None，行为零变化
- 静态守护：编排 TTS 全部调用点（batch/单帧重生/多引擎复用蓝图）都透传
  payload 的 course_project_id
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.gateway.video_gen.config import VideoGenConfig
from app.gateway.video_gen.providers.registry import ProviderRegistry
from app.gateway.video_gen.service import VideoGenService


def _make_service(tmp_path) -> VideoGenService:
    cfg = VideoGenConfig(qihu_api_key="t", script_model="test-script-model", tts_speed=1.0)
    repo = MagicMock()
    repo.update = AsyncMock(return_value=None)
    return VideoGenService(
        repo=repo,
        config=cfg,
        providers=ProviderRegistry(cfg),
        storage_dir=str(tmp_path),
        sandbox_provider=AsyncMock(),
    )


def _seed_project(tmp_path, user="default", pid="cp_a11e11000001", **fields) -> str:
    d = tmp_path / "course_projects" / user
    d.mkdir(parents=True, exist_ok=True)
    data = {"id": pid, "user_id": user, "title": "语速项目", **fields}
    (d / f"{pid}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return pid


async def _prog(**kw):
    pass


def _draft_cfg(task_dir: Path) -> dict:
    return json.loads((task_dir / "storyboard.json").read_text(encoding="utf-8"))["config"]


async def _run_draft(svc, monkeypatch, tmp_path, task_id, payload) -> Path:
    from app.gateway.video_gen.orchestration import storyboard_planner as planner_mod

    task_dir = tmp_path / "default" / "tasks" / task_id
    task_dir.mkdir(parents=True)
    monkeypatch.setattr("app.gateway.video_gen.service.create_script_llm", lambda p, c: MagicMock())
    monkeypatch.setattr(planner_mod, "plan_course", AsyncMock())
    await svc._generate_draft("remotion_course", payload, task_dir, _prog, user_id="default", task_id=task_id)
    return task_dir


# ── 语速三级链：draft 落盘口径 ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_draft_uses_project_tts_speed(tmp_path, monkeypatch):
    """挂项目且任务未显式传语速 → storyboard.config.tts_speed = 项目值。"""
    svc = _make_service(tmp_path)
    pid = _seed_project(tmp_path, tts_speed=1.5)
    task_dir = await _run_draft(
        svc,
        monkeypatch,
        tmp_path,
        "vg_e11_1",
        {"script": "第一镜旁白。第二镜旁白。", "ai_normalize_script": False, "course_project_id": pid},
    )
    assert _draft_cfg(task_dir)["tts_speed"] == 1.5, "挂项目任务未显式传语速 → 取项目值"


@pytest.mark.asyncio
async def test_draft_explicit_speed_overrides_project(tmp_path, monkeypatch):
    """任务显式传值最高优先（覆盖项目 1.5）。"""
    svc = _make_service(tmp_path)
    pid = _seed_project(tmp_path, tts_speed=1.5)
    task_dir = await _run_draft(
        svc,
        monkeypatch,
        tmp_path,
        "vg_e11_2",
        {"script": "第一镜旁白。第二镜旁白。", "ai_normalize_script": False, "course_project_id": pid, "tts_speed": 1.8},
    )
    assert _draft_cfg(task_dir)["tts_speed"] == 1.8


@pytest.mark.asyncio
async def test_draft_no_project_uses_config_default(tmp_path, monkeypatch):
    """无项目 → config tts_speed 缺省（本链不引入任何变化）。"""
    svc = _make_service(tmp_path)
    task_dir = await _run_draft(
        svc,
        monkeypatch,
        tmp_path,
        "vg_e11_3",
        {"script": "第一镜旁白。第二镜旁白。", "ai_normalize_script": False},
    )
    assert _draft_cfg(task_dir)["tts_speed"] == 1.0


# ── section_id 透传：TTS 调用参数 ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_synthesize_audio_passes_section_id(tmp_path, monkeypatch):
    """_synthesize_audio：section_id kwarg 纯透传到 tts_mod.synthesize。"""
    from app.gateway.video_gen import service as svc_mod
    from app.gateway.video_gen.orchestration import tts as tts_mod
    from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame

    def _sb(narration: str, speed: float) -> Storyboard:
        cfg = StoryboardConfig(video_type="remotion_course", width=1920, height=1080, tts_speed=speed)
        return Storyboard(title="t", config=cfg, frames=[StoryboardFrame(index=0, narration=narration)])

    captured: dict = {}

    async def fake_synthesize(text, *, voice_id, speed, output_path, fallback_voice=None, section_id=None, **kw):
        captured["section_id"] = section_id
        captured["speed"] = speed
        Path(output_path).write_bytes(b"mp3")
        return tts_mod.TTSResult(audio_path=output_path, srt="", duration=2.0)

    monkeypatch.setattr(tts_mod, "synthesize", fake_synthesize)
    monkeypatch.setattr(svc_mod, "run_ffmpeg", AsyncMock())
    monkeypatch.setattr(svc_mod, "_write_storyboard_srt", lambda *a, **k: None)

    svc = svc_mod.VideoGenService.__new__(svc_mod.VideoGenService)
    svc._config = type("C", (), {"qihu_api_key": None, "tts_fallback_voice": "preset:146"})()

    # 挂项目：section_id = course_project_id，速度用 storyboard.config（项目解析产物）
    await svc._synthesize_audio(_sb("你好世界", 1.2), tmp_path, "preset:146", section_id="cp_a11e11000001")
    assert captured["section_id"] == "cp_a11e11000001"
    assert captured["speed"] == 1.2

    # 无项目任务：不传 section_id → synthesize 收到 None（行为零变化）
    captured.clear()
    await svc._synthesize_audio(_sb("无项目任务的旁白", 1.0), tmp_path, "preset:146")
    assert captured["section_id"] is None


@pytest.mark.asyncio
async def test_draft_audio_wires_section_id_from_payload(tmp_path, monkeypatch):
    """_generate_draft_audio：从 task payload 取 course_project_id 透传（draft TTS 后台任务）。"""
    from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame

    svc = _make_service(tmp_path)
    pid = _seed_project(tmp_path)
    task_dir = tmp_path / "default" / "tasks" / "vg_e11_4"
    task_dir.mkdir(parents=True)
    sb = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="remotion_course", width=1920, height=1080),
        frames=[StoryboardFrame(index=0, narration="draft 旁白")],
    )
    (task_dir / "storyboard.json").write_text(json.dumps(sb.to_dict(), ensure_ascii=False), encoding="utf-8")

    task = MagicMock()
    task.payload = json.dumps({"course_project_id": pid})
    svc.repo.get = AsyncMock(return_value=task)

    synth_mock = AsyncMock(return_value=("", ""))
    monkeypatch.setattr(svc, "_synthesize_audio", synth_mock)
    monkeypatch.setattr(svc, "_merge_audio_into_storyboard", AsyncMock(return_value=(1, [])))  # 2026-10-06 漂移批：(merged_count, drifted)

    await svc._generate_draft_audio("vg_e11_4", task_dir, user_id="default")
    synth_mock.assert_awaited_once()
    assert synth_mock.await_args.kwargs.get("section_id") == pid, "draft TTS 须带 section_id=course_project_id"


def test_all_tts_call_sites_wire_section_id():
    """静态守护：编排 TTS 全部调用点（batch/单帧重生/多引擎复用蓝图）透传项目 id。

    与 test_style_arbitration 的 C3-06 静态守护同手法——新增调用点漏传
    section_id 不会在单测里暴露（不传=合法缺省），只能静态兜底。透传形态统一为
    条件 kwargs（None 不传——窄签名桩/旧调用方零变化，entity_roster 同款惯用法）。
    """
    from app.gateway.video_gen import service as service_mod

    _payload_wired = [
        service_mod.VideoGenService._prepare_storyboard_and_audio,
        service_mod.VideoGenService._render_from_draft,
        service_mod.VideoGenService._run_pipeline_engine,  # reuse_blueprint 复用蓝图路径
    ]
    for fn in _payload_wired:
        src = inspect.getsource(fn)
        assert '"section_id": payload.get("course_project_id")' in src, f"{fn.__name__} 须透传 section_id"
    # _generate_draft_audio 的 payload 局部变量名是 pl
    src = inspect.getsource(service_mod.VideoGenService._generate_draft_audio)
    assert '"section_id": pl.get("course_project_id")' in src, "_generate_draft_audio 须透传 section_id"
    # _regenerate_frame（单帧重生）：直接 tts_mod.synthesize，局部变量 + 条件 kwargs
    src = inspect.getsource(service_mod.VideoGenService._regenerate_frame)
    assert '_tts_section_id = payload.get("course_project_id")' in src, "_regenerate_frame 须透传 section_id"


# ── store 持久化回环（create/update 白名单自动含新字段）─────────────────────


def test_store_persists_tts_speed_roundtrip(tmp_path):
    """create/update 走 __dataclass_fields__ 白名单 → tts_speed 直存/可改/可清。"""
    from app.gateway.video_gen.course_project_store import CourseProjectStore

    store = CourseProjectStore(base_dir=tmp_path)
    proj = store.create(user_id="u1", title="t", tts_speed=1.3)
    assert store.get(proj.id, user_id="u1").tts_speed == 1.3

    store.update(proj.id, user_id="u1", tts_speed=1.7)
    assert store.get(proj.id, user_id="u1").tts_speed == 1.7

    store.update(proj.id, user_id="u1", tts_speed=None)  # 显式清空
    assert store.get(proj.id, user_id="u1").tts_speed is None


def test_store_legacy_project_without_tts_speed_defaults_none(tmp_path):
    """存量项目 JSON 缺 tts_speed 字段 → from_dict 白名单兜底 None（零迁移）。"""
    import json as _json

    from app.gateway.video_gen.course_project_store import CourseProjectStore

    d = tmp_path / "course_projects" / "u1"
    d.mkdir(parents=True)
    (d / "cp_00000000a11e.json").write_text(
        _json.dumps({"id": "cp_00000000a11e", "user_id": "u1", "title": "存量项目"}),
        encoding="utf-8",
    )
    store = CourseProjectStore(base_dir=tmp_path)
    assert store.get("cp_00000000a11e", user_id="u1").tts_speed is None
