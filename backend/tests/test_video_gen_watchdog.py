"""W2/W4/W5/W6（2026-08-24 看门狗活性重构）：video_gen 侧埋点、预算公式、配置收口、目录保全。

- W2：长阶段（分镜/脚本 LLM、TTS 整批、帧渲染、合成）阶段级挂 with_liveness
  （阶段级包一层，整个阶段一个 tick task）；
- W4：预算公式（标量在配置、公式在代码，``_tts_gather_timeout`` 为范本）；
- W5：liveness_threshold_seconds / stall_progress_freeze_seconds /
  composing_backstop_enabled / stall_budget_scalars / 帧并发分档配置化；
- W6：video_gen 任何收尸路径不删 task_dir（现状锁死，N2④）。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.gateway._shared.task_framework.models import TaskRow
from app.gateway._shared.task_framework.repository import TaskRepository
from app.gateway._shared.task_framework.service import ProgressUpdater


@pytest_asyncio.fixture(loop_scope="function")
async def async_engine():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(TaskRow.__table__.create)
    yield engine
    await engine.dispose()


@pytest.fixture()
def session_factory(async_engine):
    return async_sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture()
def repo(session_factory):
    return TaskRepository(session_factory=session_factory)


@pytest.fixture()
def vg_svc(repo, tmp_path):
    from unittest.mock import MagicMock

    from app.gateway.video_gen.config import VideoGenConfig
    from app.gateway.video_gen.providers.registry import ProviderRegistry
    from app.gateway.video_gen.service import VideoGenService

    cfg = VideoGenConfig(script_model="qwen3.8-flash")
    svc = VideoGenService(
        repo=repo,
        config=cfg,
        providers=ProviderRegistry(cfg),
        storage_dir=str(tmp_path),
        sandbox_provider=MagicMock(),
    )
    svc._liveness_ping_interval = 0.05  # 测试加速：滴答周期=节流窗口
    return svc


async def _age(repo, task_id, delta: timedelta) -> None:
    old = datetime.now(UTC) - delta
    async with repo._sf() as session:
        await session.execute(sa_update(TaskRow).where(TaskRow.id == task_id).values(updated_at=old))
        await session.commit()


async def _mk_task(repo, task_id="vg_t1"):
    await repo.create(id=task_id, user_id="u1", domain="video_gen", task_type="ai_image", payload="{}")
    await repo.update(task_id, status="running", progress=30)
    return task_id


class _TouchSpy:
    def __init__(self, repo):
        self.repo = repo
        self.count = 0
        self._orig = repo.touch

    async def __call__(self, task_id):
        self.count += 1
        await self._orig(task_id)

    def attach(self, repo):
        repo.touch = self  # type: ignore[method-assign]
        return self


# ---------------------------------------------------------------------------
# W5：配置默认值与注入
# ---------------------------------------------------------------------------


def test_watchdog_config_defaults():
    """W5/P1/P5/P10：默认值 120s / 1800s / True。"""
    from app.gateway.video_gen.config import VideoGenConfig

    cfg = VideoGenConfig()
    assert cfg.liveness_threshold_seconds == 120
    assert cfg.stall_progress_freeze_seconds == 1800
    assert cfg.composing_backstop_enabled is True


def test_budget_scalars_defaults():
    """W5/P4：标量默认 {margin:1.25, llm_floor:480, frame_floor:1200, tts_per_frame:60, compose_per_minute:30}。"""
    from app.gateway.video_gen.config import VideoGenConfig

    sc = VideoGenConfig().stall_budget_scalars
    assert sc.margin == 1.25
    assert sc.llm_floor == 480
    assert sc.frame_floor == 1200
    assert sc.tts_per_frame == 60
    assert sc.compose_per_minute == 30


def test_service_injects_watchdog_config(repo, tmp_path):
    """W5：VideoGenService.__init__ 从 config 注入三个看门狗旋钮。"""
    from unittest.mock import MagicMock

    from app.gateway.video_gen.config import VideoGenConfig
    from app.gateway.video_gen.providers.registry import ProviderRegistry
    from app.gateway.video_gen.service import VideoGenService

    cfg = VideoGenConfig(script_model="m")
    svc = VideoGenService(repo=MagicMock(), config=cfg, providers=ProviderRegistry(cfg), storage_dir=str(tmp_path), sandbox_provider=MagicMock())
    assert svc.liveness_threshold_seconds == 120
    assert svc.progress_stall_timeout == 1800
    assert svc.composing_backstop_enabled is True

    cfg2 = VideoGenConfig(script_model="m", liveness_threshold_seconds=0, stall_progress_freeze_seconds=1200, composing_backstop_enabled=False)
    svc2 = VideoGenService(repo=MagicMock(), config=cfg2, providers=ProviderRegistry(cfg2), storage_dir=str(tmp_path), sandbox_provider=MagicMock())
    assert svc2.liveness_threshold_seconds is None, "0 = 主谓词禁用（P9 kill-switch）"
    assert svc2.progress_stall_timeout == 1200, "1200 = 回滚值（旧 20 分钟窗口），非关闭"
    assert svc2.composing_backstop_enabled is False

    cfg3 = VideoGenConfig(script_model="m", stall_progress_freeze_seconds=0)
    svc3 = VideoGenService(repo=MagicMock(), config=cfg3, providers=ProviderRegistry(cfg3), storage_dir=str(tmp_path), sandbox_provider=MagicMock())
    assert svc3.progress_stall_timeout is None, "0 = 兜底谓词关闭"


def test_app_wires_video_gen_reaper_interval_60():
    """W5/P3：app.py 给 video_gen 的 reaper 注入 60s 扫描周期（voice_twin/PPT 保持默认 300s）。"""
    from pathlib import Path

    app_py = Path(__file__).resolve().parents[1] / "app" / "gateway" / "app.py"
    src = app_py.read_text(encoding="utf-8")
    # video_gen：显式 interval=60（恰好一处）
    assert src.count("vg_svc.start_stall_reaper(interval=60)") == 1, "video_gen reaper 须显式 interval=60（P3）"
    # 兄弟模块：保持默认（裸调用，不传 interval）
    assert src.count("prod_svc.start_stall_reaper()") == 1
    assert src.count("ct_svc.start_stall_reaper()") == 1
    assert src.count("start_stall_reaper(interval=") == 1, "仅 video_gen 显式传 interval"


def test_frame_concurrency_tiers_p8():
    """W5/P8：>50 帧档默认 2（生产值，旧硬编码 1）；分档阈值/档位值配置化。"""
    from app.gateway.video_gen.config import VideoGenConfig

    cfg = VideoGenConfig()
    assert cfg.effective_frame_concurrency(10) == cfg.frame_concurrency
    assert cfg.effective_frame_concurrency(30) == cfg.frame_concurrency_mid
    assert cfg.effective_frame_concurrency(51) == cfg.frame_concurrency_large
    assert cfg.frame_concurrency_large == 2, "P8：>50 帧档生产值 2 起步（p99 复核后可升 4）"

    cfg2 = VideoGenConfig(frame_concurrency_large=4, frame_concurrency_tier2_frames=100)
    assert cfg2.effective_frame_concurrency(80) == cfg2.frame_concurrency_mid, "阈值可配：80 帧落在 mid 档"
    assert cfg2.effective_frame_concurrency(120) == 4


# ---------------------------------------------------------------------------
# W4：预算公式（标量在配置、公式在代码）
# ---------------------------------------------------------------------------


def test_budget_formula_follows_config(vg_svc):
    """W4/P4：改标量 -> 预算输出按公式变化（公式在代码里推导，配置不放公式字符串）。"""
    from app.gateway.video_gen.config import VideoGenConfig
    from app.gateway.video_gen.service import VideoGenService

    svc = VideoGenService.__new__(VideoGenService)
    svc._config = VideoGenConfig(script_model="m")
    sc = svc._config.stall_budget_scalars

    # LLM：margin × (2 + ceil(n/并发)) × llm_floor（2 轮 = 脚本归一/解析相 + 分镜循环）
    assert svc._stage_budget_llm(8, concurrency=8) == sc.margin * 3 * sc.llm_floor
    assert svc._stage_budget_llm(0) == sc.margin * 2 * sc.llm_floor  # n=0 -> 2 轮（纯脚本归一/解析相）
    assert svc._stage_budget_llm(17, concurrency=8) == sc.margin * (2 + 3) * sc.llm_floor  # ceil(17/8)=3

    # 帧渲染：margin × ceil(n/并发) × frame_floor
    assert svc._stage_budget_frame_render(10, concurrency=2) == sc.margin * 5 * sc.frame_floor
    assert svc._stage_budget_frame_render(1, concurrency=1) == sc.margin * 1 * sc.frame_floor

    # TTS 整批：margin × max(_tts_gather_timeout(n), per_frame×n)（口径统一：
    # 预算恒 >= gather 自身超时，防两处旋钮漂移让预算先于 gather 杀健康批）
    from app.gateway.video_gen.service import _tts_gather_timeout

    assert svc._stage_budget_tts(0) == sc.margin * _tts_gather_timeout(0)
    assert svc._stage_budget_tts(60) == sc.margin * max(_tts_gather_timeout(60), 60 * 60)
    assert svc._stage_budget_tts(200) == sc.margin * max(_tts_gather_timeout(200), 60 * 200)
    assert svc._stage_budget_tts(200) >= sc.margin * _tts_gather_timeout(200), "预算必须盖住 gather 超时"

    # 合成：margin × max(视频时长 × compose_per_minute/60, remotion_agent_timeout)
    assert svc._stage_budget_compose(600) == sc.margin * max(600 * sc.compose_per_minute / 60, svc._config.remotion_agent_timeout)
    assert svc._stage_budget_compose(None) >= sc.margin * svc._config.remotion_agent_timeout

    # 改标量 -> 输出跟着变（公式追踪配置）
    svc._config = VideoGenConfig(
        script_model="m",
        stall_budget_scalars={"margin": 2.0, "llm_floor": 300, "frame_floor": 600, "tts_per_frame": 30, "compose_per_minute": 15},
    )
    assert svc._stage_budget_llm(8, concurrency=8) == 2.0 * 3 * 300
    assert svc._stage_budget_frame_render(10, concurrency=2) == 2.0 * 5 * 600
    assert svc._stage_budget_tts(200) == 2.0 * max(_tts_gather_timeout(200), 30 * 200)  # gather 恒 >= 模块常量公式
    assert svc._stage_budget_compose(600) == 2.0 * max(600 * 15 / 60, svc._config.remotion_agent_timeout)


def test_budget_tts_floor_constant_alignment(vg_svc):
    """TTS 预算的 1800 下限与 _tts_gather_timeout 的 TTS_GATHER_TIMEOUT_FLOOR 同源。"""
    from app.gateway.video_gen import service as vg_service_mod

    sc = vg_svc._config.stall_budget_scalars
    assert vg_svc._stage_budget_tts(0) == sc.margin * vg_service_mod.TTS_GATHER_TIMEOUT_FLOOR


# ---------------------------------------------------------------------------
# W2：长阶段埋点（阶段级挂滴答）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_llm_stage_pings_during_slow_call(vg_svc, repo, tmp_path, monkeypatch):
    """W2：分镜/脚本 LLM 阶段挂滴答--mock 慢 LLM（单调用 0.15s≈生产 5min 放大快进），
    期间 updated_at 按期刷新、登记 storyboard_llm 阶段；阶段结束登记摘除。"""
    from app.gateway.video_gen import service as vg_mod
    from app.gateway.video_gen.orchestration import script as script_mod
    from app.gateway.video_gen.orchestration import storyboard_planner as planner_mod

    tid = await _mk_task(repo)
    spy = _TouchSpy(repo).attach(repo)
    task_dir = tmp_path / "u1" / "tasks" / tid
    task_dir.mkdir(parents=True, exist_ok=True)
    progress = ProgressUpdater(repo, tid)

    observed: dict = {}

    async def _slow_parse(llm, raw_text, *, target_scenes=None, on_progress=None):
        observed["covered"] = vg_svc._liveness_covered(tid)
        observed["stages"] = [s[0] for s in vg_svc._stage_entries(tid)]
        await asyncio.sleep(0.15)  # 慢 LLM
        return [{"narration": f"第{i}句"} for i in range(3)]

    # S5（2026-08-26 调度表批）：service 现传 shot_table_enabled（配置默认开）——
    # 桩加 **kw 容差（本测试只关心活性 ping，不锁 plan_course 签名）。
    async def _fast_plan(llm, *, storyboard, style_context, task_dir, chunk_size=None, frame_concurrency=None, **_kw):
        return None

    monkeypatch.setattr(script_mod, "parse_script_to_frames", _slow_parse)
    monkeypatch.setattr(planner_mod, "plan_course", _fast_plan)
    monkeypatch.setattr(vg_mod, "create_script_llm", lambda payload, config: object())

    payload = {"script": "培训脚本内容。" * 30, "mode": "fixed", "ai_normalize_script": True}
    await vg_svc._generate_draft("ai_image", payload, task_dir, progress, user_id="u1", task_id=tid)

    assert observed["covered"] is True, "LLM 阶段期间任务必须被活性覆盖"
    assert "storyboard_llm" in observed["stages"], f"应登记 storyboard_llm 阶段：{observed['stages']}"
    assert spy.count >= 2, f"慢 LLM 期间 updated_at 须按期刷新（touch ≥2 次），实际 {spy.count}"
    assert not vg_svc._liveness_covered(tid), "阶段结束覆盖登记必须摘除"


@pytest.mark.asyncio
async def test_tts_batch_pings(vg_svc, repo, tmp_path, monkeypatch):
    """W2：TTS 整批挂滴答--mock 慢 TTS 批，期间 updated_at 前进、登记 tts_batch 阶段。"""
    from types import SimpleNamespace

    from app.gateway.video_gen import service as vg_mod
    from app.gateway.video_gen.orchestration import tts as tts_mod
    from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame

    tid = await _mk_task(repo)
    spy = _TouchSpy(repo).attach(repo)
    task_dir = tmp_path / "u1" / "tasks" / tid
    task_dir.mkdir(parents=True, exist_ok=True)

    audio_dir = task_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)

    observed: dict = {}

    async def _slow_synthesize(text, *, voice_id, speed, output_path, fallback_voice=None, **kwargs):
        observed["covered"] = vg_svc._liveness_covered(tid)
        observed["stages"] = [s[0] for s in vg_svc._stage_entries(tid)]
        await asyncio.sleep(0.12)  # 慢 TTS（60 帧整批的快进模拟）
        from pathlib import Path as _P

        out = _P(output_path)
        out.write_bytes(b"fake-mp3")
        return SimpleNamespace(audio_path=str(out), srt="0\n00:00:00,000 --> 00:00:01,000\ntest\n", duration=1.0, actual_voice_id=None)

    monkeypatch.setattr(tts_mod, "synthesize", _slow_synthesize)

    async def _noop_ffmpeg(fn, *args, **kw):
        return None

    monkeypatch.setattr(vg_mod, "run_ffmpeg", _noop_ffmpeg)

    async def _noop_precompute(*a, **kw):
        return None

    monkeypatch.setattr(vg_mod, "_precompute_llm_chunks", _noop_precompute)

    sb = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_image", width=1152, height=2048, voice_id="preset:146"),
        frames=[StoryboardFrame(index=i, narration=f"第{i}帧旁白内容") for i in range(3)],
    )
    await vg_svc._synthesize_audio_locked(sb, task_dir, "preset:146", task_id=tid)

    assert observed["covered"] is True, "TTS 批期间任务必须被活性覆盖"
    assert "tts_batch" in observed["stages"], f"应登记 tts_batch 阶段：{observed['stages']}"
    assert spy.count >= 2, f"TTS 批期间 updated_at 须前进（touch ≥2 次），实际 {spy.count}"
    assert not vg_svc._liveness_covered(tid)


@pytest.mark.asyncio
async def test_frame_render_stage_pings(vg_svc, repo, tmp_path, monkeypatch):
    """W2：帧渲染阶段挂滴答（渲染 gather 外层包一层）--mock 单帧慢渲染，期间
    updated_at 按期刷新、登记 frame_render 阶段。"""
    from app.gateway.video_gen import service as vg_mod

    tid = await _mk_task(repo)
    spy = _TouchSpy(repo).attach(repo)
    task_dir = tmp_path / "u1" / "tasks" / tid
    task_dir.mkdir(parents=True, exist_ok=True)
    progress = ProgressUpdater(repo, tid)

    observed: dict = {}
    ticks_during: list[int] = []

    class _FakeVisual:
        async def render(self, storyboard, *, only_frames=None, on_frame_done=None, task_dir=None, reference_image_paths=None):
            observed["covered"] = vg_svc._liveness_covered(tid)
            observed["stages"] = [s[0] for s in vg_svc._stage_entries(tid)]
            ticks_during.append(len(vg_svc._liveness_ticks.get(tid, ())))
            await asyncio.sleep(0.15)  # mock 单帧 5min 渲染（快进）
            observed["covered_end"] = vg_svc._liveness_covered(tid)

    async def _fake_prep(task_type, payload, task_dir, progress, *, skip_tts=False, user_id="default", task_id=None):
        from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame

        sb = Storyboard(
            title="t",
            config=StoryboardConfig(video_type="ai_image", width=1152, height=2048, voice_id="preset:146"),
            frames=[StoryboardFrame(index=i, narration=f"n{i}") for i in range(5)],
        )
        return sb, str(task_dir / "audio.mp3"), str(task_dir / "subtitle.srt")

    monkeypatch.setattr(vg_svc, "_prepare_storyboard_and_audio", _fake_prep)
    monkeypatch.setattr(vg_svc, "_make_visual_source", lambda *a, **kw: _FakeVisual())
    monkeypatch.setattr(vg_svc, "_make_on_frame_done", lambda *a, **kw: lambda idx: asyncio.sleep(0))
    monkeypatch.setattr(vg_mod, "_reconcile_durations_with_audio", lambda *a, **kw: False)

    async def _noop_probe():
        return {}

    from app.gateway.video_gen import worker_probe

    monkeypatch.setattr(worker_probe, "probe_worker_capabilities", _noop_probe)
    monkeypatch.setattr(worker_probe, "decide_pipeline_version", lambda *a, **kw: "v1")

    payload = {"script": "x", "mode": "fixed"}
    await vg_svc._prepare_storyboard_video("ai_image", payload, task_dir, progress, user_id="u1", task_id=tid)

    assert observed["covered"] is True, "渲染期间任务必须被活性覆盖"
    assert "frame_render" in observed["stages"], f"应登记 frame_render 阶段：{observed['stages']}"
    assert spy.count >= 2, f"渲染期间 updated_at 须按期刷新，实际 {spy.count}"
    assert not vg_svc._liveness_covered(tid), "渲染结束覆盖摘除"


@pytest.mark.asyncio
async def test_stage_wrap_single_task(vg_svc, repo, tmp_path, monkeypatch):
    """W2：阶段级挂法--整个阶段只产生 1 个 tick task（不逐帧/逐调用 churn）。"""
    from app.gateway.video_gen import service as vg_mod

    tid = await _mk_task(repo)
    task_dir = tmp_path / "u1" / "tasks" / tid
    task_dir.mkdir(parents=True, exist_ok=True)
    progress = ProgressUpdater(repo, tid)

    ticks_observed: list[int] = []

    class _CountingVisual:
        async def render(self, storyboard, *, only_frames=None, on_frame_done=None, task_dir=None, reference_image_paths=None):
            for _ in range(5):  # 模拟多帧循环内部多次采样
                ticks_observed.append(len(vg_svc._liveness_ticks.get(tid, ())))
                await asyncio.sleep(0.02)

    async def _fake_prep(task_type, payload, task_dir, progress, *, skip_tts=False, user_id="default", task_id=None):
        from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame

        sb = Storyboard(
            title="t",
            config=StoryboardConfig(video_type="ai_image", width=1152, height=2048, voice_id="preset:146"),
            frames=[StoryboardFrame(index=i, narration=f"n{i}") for i in range(5)],
        )
        return sb, str(task_dir / "audio.mp3"), str(task_dir / "subtitle.srt")

    monkeypatch.setattr(vg_svc, "_prepare_storyboard_and_audio", _fake_prep)
    monkeypatch.setattr(vg_svc, "_make_visual_source", lambda *a, **kw: _CountingVisual())
    monkeypatch.setattr(vg_svc, "_make_on_frame_done", lambda *a, **kw: lambda idx: asyncio.sleep(0))
    monkeypatch.setattr(vg_mod, "_reconcile_durations_with_audio", lambda *a, **kw: False)

    from app.gateway.video_gen import worker_probe

    async def _noop_probe():
        return {}

    monkeypatch.setattr(worker_probe, "probe_worker_capabilities", _noop_probe)
    monkeypatch.setattr(worker_probe, "decide_pipeline_version", lambda *a, **kw: "v1")

    payload = {"script": "x", "mode": "fixed"}
    await vg_svc._prepare_storyboard_video("ai_image", payload, task_dir, progress, user_id="u1", task_id=tid)

    assert ticks_observed and all(n == 1 for n in ticks_observed), f"阶段级挂法应恰好 1 个 tick：{ticks_observed}"


@pytest.mark.asyncio
async def test_stage_liveness_noop_without_task_id(vg_svc, repo, tmp_path, monkeypatch):
    """W2：存量调用未传 task_id（独立复用/测试）-> 埋点为 no-op，不炸不覆盖。"""
    from app.gateway.video_gen import service as vg_mod

    tid = await _mk_task(repo)
    spy = _TouchSpy(repo).attach(repo)
    task_dir = tmp_path / "u1" / "tasks" / tid
    task_dir.mkdir(parents=True, exist_ok=True)
    progress = ProgressUpdater(repo, tid)

    class _FakeVisual:
        async def render(self, storyboard, *, only_frames=None, on_frame_done=None, task_dir=None, reference_image_paths=None):
            assert not vg_svc._liveness_covered(tid), "无 task_id 时不得登记覆盖"
            await asyncio.sleep(0.03)

    async def _fake_prep(task_type, payload, task_dir, progress, *, skip_tts=False, user_id="default", task_id=None):
        from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame

        sb = Storyboard(
            title="t",
            config=StoryboardConfig(video_type="ai_image", width=1152, height=2048, voice_id="preset:146"),
            frames=[StoryboardFrame(index=0, narration="n")],
        )
        return sb, str(task_dir / "audio.mp3"), str(task_dir / "subtitle.srt")

    monkeypatch.setattr(vg_svc, "_prepare_storyboard_and_audio", _fake_prep)
    monkeypatch.setattr(vg_svc, "_make_visual_source", lambda *a, **kw: _FakeVisual())
    monkeypatch.setattr(vg_svc, "_make_on_frame_done", lambda *a, **kw: lambda idx: asyncio.sleep(0))
    monkeypatch.setattr(vg_mod, "_reconcile_durations_with_audio", lambda *a, **kw: False)

    from app.gateway.video_gen import worker_probe

    async def _noop_probe():
        return {}

    monkeypatch.setattr(worker_probe, "probe_worker_capabilities", _noop_probe)
    monkeypatch.setattr(worker_probe, "decide_pipeline_version", lambda *a, **kw: "v1")

    await vg_svc._prepare_storyboard_video("ai_image", {"script": "x", "mode": "fixed"}, task_dir, progress, user_id="u1")
    assert spy.count == 0


# ---------------------------------------------------------------------------
# W6：video_gen 目录保全（现状锁死）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_video_gen_dir_never_deleted(vg_svc, repo, tmp_path):
    """W6/N2④：video_gen 收尸/重排/终态各路径 task_dir 均在（_cleanup_task no-op 现状锁死，
    仅用户显式 delete_task 才删）。"""
    tid = await _mk_task(repo)
    task_dir = tmp_path / "u1" / "tasks" / tid
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "storyboard.json").write_text("{}", encoding="utf-8")
    (task_dir / "scene_0").mkdir()
    (task_dir / "scene_0" / "video.mp4").write_bytes(b"x")

    # 终态路径（预算烧尽 -> _cleanup_task 被调）也不删
    await repo.update(tid, retry_count=2)
    await _age(repo, tid, timedelta(hours=1))
    reaped = await vg_svc.reap_stalled_tasks()
    assert reaped == 1
    row = await repo.get(tid)
    assert row.status == "failed"
    assert (task_dir / "scene_0" / "video.mp4").exists(), "video_gen 收尸终态不得删目录"
    assert (task_dir / "storyboard.json").exists()

    # _cleanup_task 本体是 no-op（显式调用也不删）
    await vg_svc._cleanup_task(row)
    assert (task_dir / "scene_0" / "video.mp4").exists()


@pytest.mark.asyncio
async def test_covered_long_render_not_reaped_by_progress_freeze(vg_svc, repo, tmp_path):
    """端到端误杀场景（WD1 缩影）：覆盖期整数 progress 冻结 40min（> P5 30min）+
    心跳正常 -> 不收尸（旧 C 谓词 20min 必杀的时序）。"""

    tid = await _mk_task(repo)
    # 模拟活跃帧渲染滴答（真实 tick）
    tick = asyncio.create_task(vg_svc._liveness_tick(tid, 0.05), name=f"liveness-tick-video_gen-{tid}")
    vg_svc._liveness_ticks.setdefault(tid, set()).add(tick)
    try:
        await asyncio.sleep(0.12)
        # progress 冻结自 40min 前（B/C 条件远超窗口）
        vg_svc._progress_baseline[tid] = (30, datetime.now(UTC) - timedelta(minutes=40))
        reaped = await vg_svc.reap_stalled_tasks()
        assert reaped == 0, "覆盖 + 心跳正常 -> B/C/主谓词均不得收尸"
        assert (await repo.get(tid)).status == "running"
    finally:
        vg_svc._liveness_ticks.pop(tid, None)
        tick.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(asyncio.shield(tick), timeout=2)


# ---------------------------------------------------------------------------
# 源码级接线断言（W2 挂点全量清单防漂移）
# ---------------------------------------------------------------------------


def test_all_long_stages_instrumented():
    """W2：以「两进度写库点之间可能 > P1」为准则的挂点全量清单（防未来漂移）。"""
    from pathlib import Path

    svc_py = Path(__file__).resolve().parents[1] / "app" / "gateway" / "video_gen" / "service.py"
    src = svc_py.read_text(encoding="utf-8")
    # 阶段名 -> 出现次数（stage= 关键字参数）
    expected = {
        "storyboard_llm": 2,  # _generate_draft + _prepare_storyboard_and_audio
        "tts_batch": 1,  # _synthesize_audio_locked（所有 TTS 调用方共用）
        "frame_render": 2,  # _prepare_storyboard_video + _render_from_draft
        "compose": 3,  # _render_remotion_via_agent + _render_remotion_storyboard + _run_remotion(legacy)
    }
    for stage, n in expected.items():
        actual = src.count(f'stage="{stage}"')
        assert actual == n, f"阶段 {stage} 挂点应为 {n} 处，实际 {actual} 处（挂点漂移）"
    # 排队期滴答在框架层（W2b），vimax engine.render 故意不挂（范围排除，P5 兜底保护）
    assert "with_liveness(task_id)" not in src.split("class VideoGenService")[1], "video_gen 侧不应有未带 stage 的裸挂法（排队滴答归框架层）"
