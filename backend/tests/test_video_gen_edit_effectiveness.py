"""video-gen「修改台词/配音/音色/字幕不生效」修复批回归（docs/plans/2026-09-24-video-gen-edits-not-effective-fix-plan.md v5）。

B1 recompose 无漂移刷 ASS（T1a/T1b）+ 竞态收口（T1c）
B2 写时重建 .ass（T2/T3）+ _invalidate 删 subtitle.ass（T4）
F2 audio 端点 Cache-Control（T8）
后续批次（B3/B5/B4/F*）的用例随实现批次追加。
"""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.gateway.video_gen.config import VideoGenConfig
from app.gateway.video_gen.providers.registry import ProviderRegistry
from app.gateway.video_gen.service import VideoGenService
from app.gateway.video_gen.subtitle_engine import srt_to_ass

USER = "test-user-autouse"
NL = chr(10)  # 测试内构造 SRT 文本用


# ---------------------------------------------------------------------------
# harness
# ---------------------------------------------------------------------------


def _make_service(tmp_path) -> VideoGenService:
    cfg = VideoGenConfig(qihu_api_key="t", script_model="test-script-model")
    providers = ProviderRegistry(cfg)
    repo = MagicMock()
    repo.update = AsyncMock()  # B3 双写 await repo.update（MagicMock 不可 await）
    return VideoGenService(
        repo=repo,
        config=cfg,
        providers=providers,
        storage_dir=str(tmp_path),
        sandbox_provider=AsyncMock(),
    )


def _fake_request(svc):
    class _Req:
        headers: dict = {}  # stream_frame_audio 读 Range 头

        class app:
            class state:
                video_gen_service = svc

    return _Req()


def _task_dir(tmp_path, task_id="vg_edit") -> Path:
    d = Path(tmp_path) / USER / "tasks" / task_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_storyboard(task_dir: Path, config_extra: dict | None = None, frames: list | None = None, subtitle_locked: bool | None = None) -> dict:
    cfg = {"video_type": "ai_image", "width": 1080, "height": 1920, "voice_id": "preset:146", "tts_speed": 1.2, "bgm_volume": 0.15}
    cfg.update(config_extra or {})
    sb = {
        "title": "t",
        "config": cfg,
        "frames": frames
        if frames is not None
        else [
            {"index": 0, "narration": "第一句旁白内容", "duration": 2.5},
            {"index": 1, "narration": "第二句旁白内容", "duration": 3.0},
        ],
    }
    if subtitle_locked is not None:
        sb["subtitle_locked"] = subtitle_locked
    (task_dir / "storyboard.json").write_text(json.dumps(sb, ensure_ascii=False), encoding="utf-8")
    return sb


_LOCKED_SRT = "1\n00:00:00,000 --> 00:00:02,000\n用户手改的字幕\n\n2\n00:00:02,000 --> 00:00:05,000\n手改的第二条\n"


def _expected_ass(tmp_path, srt_text: str, style: str, w=1080, h=1920) -> str:
    srt = tmp_path / "_expect.srt"
    srt.write_text(srt_text, encoding="utf-8")
    out = tmp_path / "_expect.ass"
    srt_to_ass(str(srt), out_path=str(out), video_w=w, video_h=h, style=style)
    return out.read_text(encoding="utf-8")


async def _run_recompose(svc, task_dir, monkeypatch, drift: bool = False):
    """驱动 _run_pipeline 的 recompose_only 分支（B0），reconcile 按 drift 参数打桩。"""
    monkeypatch.setattr(
        "app.gateway.video_gen.service._reconcile_durations_with_audio",
        lambda sb, td: drift,
    )
    progress = AsyncMock()
    return await svc._run_pipeline(
        task_id="vg_edit",
        user_id=USER,
        task_type="ai_image",
        payload={"recompose_only": True},
        task_dir=task_dir,
        progress=progress,
    )


# ---------------------------------------------------------------------------
# T1a/T1b：B1 recompose 无漂移只刷 ASS、不重切 SRT
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t1a_locked_no_drift_recompose_refreshes_ass_only(tmp_path, monkeypatch):
    """locked + 无漂移 recompose → .ass 从 locked SRT 刷新（样式生效），SRT 逐字节不变。"""
    svc = _make_service(tmp_path)
    task_dir = _task_dir(tmp_path)
    _write_storyboard(task_dir, config_extra={"subtitle_style": "bold"}, subtitle_locked=True)
    (task_dir / "subtitle.srt").write_text(_LOCKED_SRT, encoding="utf-8")
    (task_dir / "subtitle.ass").write_text("STALE ASS", encoding="utf-8")
    srt_before = (task_dir / "subtitle.srt").read_bytes()

    await _run_recompose(svc, task_dir, monkeypatch, drift=False)

    assert (task_dir / "subtitle.srt").read_bytes() == srt_before, "locked SRT 必须逐字节不变"
    ass = (task_dir / "subtitle.ass").read_text(encoding="utf-8")
    assert ass != "STALE ASS", "recompose 后 .ass 必须刷新（修前恒旧）"
    assert ass == _expected_ass(tmp_path, _LOCKED_SRT, "bold"), "ASS 须按当前样式从 locked SRT 派生"


@pytest.mark.asyncio
async def test_t1b_unlocked_no_drift_recompose_keeps_srt_bytes(tmp_path, monkeypatch):
    """未锁定未编辑 + 无漂移 recompose → SRT 逐字节不变（断句不退化为规则链）、.ass 随样式刷新。"""
    svc = _make_service(tmp_path)
    task_dir = _task_dir(tmp_path)
    _write_storyboard(task_dir, config_extra={"subtitle_style": "default"}, subtitle_locked=False)
    # 模拟 LLM 断句产物（与规则链按均分切分不同的边界）
    llm_srt = "1\n00:00:00,000 --> 00:00:01,100\n第一句旁白\n\n2\n00:00:01,100 --> 00:00:05,000\n内容第二句旁白\n"
    (task_dir / "subtitle.srt").write_text(llm_srt, encoding="utf-8")
    (task_dir / "subtitle.ass").write_text("STALE ASS", encoding="utf-8")
    srt_before = (task_dir / "subtitle.srt").read_bytes()

    await _run_recompose(svc, task_dir, monkeypatch, drift=False)

    assert (task_dir / "subtitle.srt").read_bytes() == srt_before, "未编辑任务的 SRT 断句不得被重切"
    ass = (task_dir / "subtitle.ass").read_text(encoding="utf-8")
    assert ass == _expected_ass(tmp_path, llm_srt, "default")

    # 换样式再跑一次 → .ass 随新样式变化（样式改动经 recompose 生效）
    sb = json.loads((task_dir / "storyboard.json").read_text(encoding="utf-8"))
    sb["config"]["subtitle_style"] = "outline"
    (task_dir / "storyboard.json").write_text(json.dumps(sb, ensure_ascii=False), encoding="utf-8")
    await _run_recompose(svc, task_dir, monkeypatch, drift=False)
    ass2 = (task_dir / "subtitle.ass").read_text(encoding="utf-8")
    assert ass2 == _expected_ass(tmp_path, llm_srt, "outline")
    assert (task_dir / "subtitle.srt").read_bytes() == srt_before


@pytest.mark.asyncio
async def test_t1b_none_style_skips_ass_refresh(tmp_path, monkeypatch):
    """subtitle_style=none → 不派生 ASS（none 闸由 build_worker_payload 兜底）。"""
    svc = _make_service(tmp_path)
    task_dir = _task_dir(tmp_path)
    _write_storyboard(task_dir, config_extra={"subtitle_style": "none"}, subtitle_locked=False)
    (task_dir / "subtitle.srt").write_text(_LOCKED_SRT, encoding="utf-8")
    stale = task_dir / "subtitle.ass"
    stale.write_text("STALE ASS", encoding="utf-8")

    await _run_recompose(svc, task_dir, monkeypatch, drift=False)

    # none 时既不重建也不删除（下发闸兜底；删除语义归 PUT 写路径 B2）
    assert stale.read_text(encoding="utf-8") == "STALE ASS"


@pytest.mark.asyncio
async def test_t1b_missing_srt_falls_back_to_full_rebuild(tmp_path, monkeypatch):
    """理论边界：.srt 缺失 → 回退 _write_storyboard_srt 全量重建（本就无字幕可保）。"""
    svc = _make_service(tmp_path)
    task_dir = _task_dir(tmp_path)
    _write_storyboard(task_dir, config_extra={"subtitle_style": "default"}, subtitle_locked=False)

    await _run_recompose(svc, task_dir, monkeypatch, drift=False)

    srt = task_dir / "subtitle.srt"
    assert srt.exists(), "srt 缺失时 recompose 应回退全量重建"
    content = srt.read_text(encoding="utf-8")
    assert "第一句旁白内容" in content and "第二句旁白内容" in content
    assert (task_dir / "subtitle.ass").exists(), "回退重建须同时派生 ASS"


# ---------------------------------------------------------------------------
# T1c：B1 竞态收口——PUT /subtitle 的写 srt + 派生 ass + 置锁单临界区
# ---------------------------------------------------------------------------


class _SubtitleSvc:
    """带可观测 _update_storyboard 的假服务（委托真实实现 + 进/出临界区采样）。"""

    def __init__(self, tmp_path, status="ready"):
        self._storage_dir = str(tmp_path)
        self._status = status
        self._sb_write_lock = asyncio.Lock()
        self.samples: list[tuple[dict, dict]] = []

    async def get_task(self, task_id, user_id):
        return SimpleNamespace(status=self._status, task_type="ai_image", payload="{}")

    async def _update_storyboard(self, task_dir, updater):
        srt_path = task_dir / "subtitle.srt"
        ass_path = task_dir / "subtitle.ass"
        sb_path = task_dir / "storyboard.json"

        def _snap() -> dict:
            sb = json.loads(sb_path.read_text(encoding="utf-8"))
            return {
                "srt": srt_path.read_text(encoding="utf-8") if srt_path.exists() else None,
                "ass": ass_path.read_text(encoding="utf-8") if ass_path.exists() else None,
                "locked": bool(sb.get("subtitle_locked")),
            }

        async with self._sb_write_lock:
            entry = _snap()  # 取得写锁前的盘面（「新 srt + 旧锁态」竞态窗口探测点）
            data = json.loads(sb_path.read_text(encoding="utf-8"))
            updater(data)
            tmp = sb_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(sb_path)
            self.samples.append((entry, _snap()))


@pytest.mark.asyncio
async def test_t1c_put_subtitle_single_critical_section(tmp_path):
    """写 .srt + 派生 .ass + 置锁须收进同一次 _update_storyboard 临界区。

    修前：先写 .srt 文件、再另起临界区置锁——并发 recompose 在窗口内读到
    「新 srt + 旧锁态」。探测口径：取得 sb 写锁那一刻（entry 采样），新 srt
    内容不得已可见而锁仍未置。
    """
    from app.gateway.routers import video_gen as vg

    task_dir = _task_dir(tmp_path)
    _write_storyboard(task_dir, config_extra={"subtitle_style": "bold"}, subtitle_locked=False)
    old_srt = "1\n00:00:00,000 --> 00:00:02,000\n旧字幕内容\n"
    (task_dir / "subtitle.srt").write_text(old_srt, encoding="utf-8")

    svc = _SubtitleSvc(tmp_path)
    body = vg.UpdateSubtitleRequest(cues=[vg.SubtitleCue(start="00:00:00,000", end="00:00:02,000", text="新字幕内容")])
    await vg.update_subtitle(_fake_request(svc), "vg_edit", body)

    lock_flips = [(e, i) for e, i in svc.samples if i["locked"] and not e["locked"]]
    assert len(lock_flips) == 1, f"置锁须恰为一次 _update_storyboard（实得 {len(lock_flips)}）"
    entry, inside = lock_flips[0]
    assert entry["srt"] == old_srt, "取得写锁时新 srt 不得已落盘（修前两步非原子的窗口）"
    assert "新字幕内容" in inside["srt"], "updater 运行后新 srt 已在临界区内落盘"
    assert "新字幕内容" in (inside["ass"] or ""), ".ass 派生同临界区完成"


# ---------------------------------------------------------------------------
# T2/T3：B2 写时重建 .ass
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t2_put_subtitle_rebuilds_ass(tmp_path):
    """PUT /subtitle → .srt 落盘且 .ass 同步重建（含样式参数断言 + none→default 修复）。"""
    from app.gateway.routers import video_gen as vg

    svc = _make_service(tmp_path)
    svc.get_task = AsyncMock(return_value=SimpleNamespace(status="ready", task_type="ai_image", payload="{}"))
    task_dir = _task_dir(tmp_path)
    _write_storyboard(task_dir, config_extra={"subtitle_style": "bold"}, subtitle_locked=False)
    (task_dir / "subtitle.ass").write_text("STALE ASS", encoding="utf-8")

    cues = [
        vg.SubtitleCue(start="00:00:00,000", end="00:00:02,000", text="新的第一条字幕"),
        vg.SubtitleCue(start="00:00:02,000", end="00:00:04,500", text="新的第二条字幕"),
    ]
    resp = await vg.update_subtitle(_fake_request(svc), "vg_edit", vg.UpdateSubtitleRequest(cues=cues))

    srt = (task_dir / "subtitle.srt").read_text(encoding="utf-8")
    assert "新的第一条字幕" in srt and "新的第二条字幕" in srt
    sb = json.loads((task_dir / "storyboard.json").read_text(encoding="utf-8"))
    assert sb["subtitle_locked"] is True
    ass = (task_dir / "subtitle.ass").read_text(encoding="utf-8")
    assert ass == _expected_ass(tmp_path, srt, "bold"), ".ass 须按 config 样式从新 srt 派生"
    assert resp["locked"] is True


@pytest.mark.asyncio
async def test_t2_put_subtitle_none_style_restored_then_derived(tmp_path):
    """config subtitle_style=none → 手改字幕恢复 default 后再派生（派生在 restore 之后）。"""
    from app.gateway.routers import video_gen as vg

    svc = _make_service(tmp_path)
    svc.get_task = AsyncMock(return_value=SimpleNamespace(status="ready", task_type="ai_image", payload="{}"))
    task_dir = _task_dir(tmp_path)
    _write_storyboard(task_dir, config_extra={"subtitle_style": "none"}, subtitle_locked=False)

    cues = [vg.SubtitleCue(start="00:00:00,000", end="00:00:02,000", text="无字幕任务改字幕")]
    resp = await vg.update_subtitle(_fake_request(svc), "vg_edit", vg.UpdateSubtitleRequest(cues=cues))

    assert resp["subtitle_style_restored"] == "default"
    srt = (task_dir / "subtitle.srt").read_text(encoding="utf-8")
    ass = (task_dir / "subtitle.ass").read_text(encoding="utf-8")
    assert ass == _expected_ass(tmp_path, srt, "default"), "样式须为恢复后的 default（非 none）"


@pytest.mark.asyncio
async def test_t2_put_subtitle_ass_warning_on_derive_failure(tmp_path, monkeypatch):
    """ASS 派生失败不 500：srt 已落盘，响应带 ass_warning。"""
    from app.gateway.routers import video_gen as vg

    def _boom(*a, **k):
        raise RuntimeError("derive failed")

    monkeypatch.setattr("app.gateway.video_gen.subtitle_engine.srt_to_ass", _boom)

    svc = _make_service(tmp_path)
    svc.get_task = AsyncMock(return_value=SimpleNamespace(status="ready", task_type="ai_image", payload="{}"))
    task_dir = _task_dir(tmp_path)
    _write_storyboard(task_dir, config_extra={"subtitle_style": "bold"}, subtitle_locked=False)

    cues = [vg.SubtitleCue(start="00:00:00,000", end="00:00:02,000", text="派生会失败的字幕")]
    resp = await vg.update_subtitle(_fake_request(svc), "vg_edit", vg.UpdateSubtitleRequest(cues=cues))

    assert "ass_warning" in resp, "派生失败须用户可见（不静默）"
    assert "派生会失败的字幕" in (task_dir / "subtitle.srt").read_text(encoding="utf-8"), "字幕文本必须已落盘"


@pytest.mark.asyncio
async def test_t3_put_config_style_change_rebuilds_ass(tmp_path):
    """PUT config 改 subtitle_style → .ass 用新样式重建；改 none 删残留 .ass。"""
    from app.gateway.routers import video_gen as vg

    svc = _make_service(tmp_path)
    svc.get_task = AsyncMock(return_value=SimpleNamespace(status="ready", task_type="ai_image", payload="{}"))
    task_dir = _task_dir(tmp_path)
    _write_storyboard(task_dir, config_extra={"subtitle_style": "default"}, subtitle_locked=False)
    (task_dir / "subtitle.srt").write_text(_LOCKED_SRT, encoding="utf-8")
    (task_dir / "subtitle.ass").write_text("STALE ASS", encoding="utf-8")

    await vg.update_storyboard_config(_fake_request(svc), "vg_edit", vg.UpdateStoryboardConfigRequest(subtitle_style="outline"))
    ass = (task_dir / "subtitle.ass").read_text(encoding="utf-8")
    assert ass == _expected_ass(tmp_path, _LOCKED_SRT, "outline"), "样式变化须触发 .ass 按新样式重建"

    await vg.update_storyboard_config(_fake_request(svc), "vg_edit", vg.UpdateStoryboardConfigRequest(subtitle_style="none"))
    assert not (task_dir / "subtitle.ass").exists(), "改为 none 须删除盘上残留 .ass"
    sb = json.loads((task_dir / "storyboard.json").read_text(encoding="utf-8"))
    assert sb["config"]["subtitle_style"] == "none"


@pytest.mark.asyncio
async def test_t3_put_config_same_style_no_ass_touch(tmp_path):
    """样式未实际变化（同值提交）→ 不重建 .ass（diff 口径，防无谓重写）。"""
    from app.gateway.routers import video_gen as vg

    svc = _make_service(tmp_path)
    svc.get_task = AsyncMock(return_value=SimpleNamespace(status="ready", task_type="ai_image", payload="{}"))
    task_dir = _task_dir(tmp_path)
    _write_storyboard(task_dir, config_extra={"subtitle_style": "outline"}, subtitle_locked=False)
    (task_dir / "subtitle.srt").write_text(_LOCKED_SRT, encoding="utf-8")
    (task_dir / "subtitle.ass").write_text("KEEP ME", encoding="utf-8")

    await vg.update_storyboard_config(_fake_request(svc), "vg_edit", vg.UpdateStoryboardConfigRequest(subtitle_style="outline"))
    assert (task_dir / "subtitle.ass").read_text(encoding="utf-8") == "KEEP ME"


# ---------------------------------------------------------------------------
# T4：_invalidate_audio_artifacts 删除清单补 subtitle.ass
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t4_invalidate_audio_artifacts_removes_ass(tmp_path):
    svc = _make_service(tmp_path)
    task_dir = _task_dir(tmp_path)
    for name in ("audio.mp3", "subtitle.srt", "subtitle.ass"):
        (task_dir / name).write_text("x", encoding="utf-8")
    svc.repo.get = AsyncMock(return_value=SimpleNamespace(payload=json.dumps({"audio_ready": True})))
    svc.repo.update = AsyncMock()

    await svc._invalidate_audio_artifacts("vg_edit", task_dir, USER)

    for name in ("audio.mp3", "subtitle.srt", "subtitle.ass"):
        assert not (task_dir / name).exists(), f"{name} 应被失效清理"
    pl = json.loads(svc.repo.update.await_args.kwargs["payload"])
    assert "audio_ready" not in pl, "payload audio_ready 须清除"


# ---------------------------------------------------------------------------
# T8：F2 后端 audio 端点 Cache-Control
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t8_frame_audio_no_cache_header(tmp_path):
    from app.gateway.routers import video_gen as vg

    svc = _make_service(tmp_path)
    svc.get_task = AsyncMock(return_value=SimpleNamespace(status="ready", task_type="ai_image", payload="{}"))
    task_dir = _task_dir(tmp_path)
    audio = task_dir / "a.mp3"
    audio.write_bytes(b"\xff\xfb\x00\x00")
    _write_storyboard(task_dir, frames=[{"index": 0, "narration": "n", "audio_path": str(audio), "duration": 1.0}])

    resp = await vg.stream_frame_audio(_fake_request(svc), "vg_edit", 0)
    assert resp.headers.get("cache-control") == "no-cache", "试听端点须禁缓存（修前恒旧音）"


# ---------------------------------------------------------------------------
# T5a–T5d：B3 PUT config 双写 DB payload（diff 门控）+ F4 显式 null 口径
# ---------------------------------------------------------------------------


def _seed_project(tmp_path, pid="cp_abcdef000001", **fields) -> str:
    d = Path(tmp_path) / "course_projects" / USER
    d.mkdir(parents=True, exist_ok=True)
    data = {"id": pid, "user_id": USER, "title": "跟随项目", "visual_style_preset": "watercolor", **fields}
    (d / f"{pid}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return pid


def _capture_re_render(svc, monkeypatch):
    """仿 test_style_arbitration._capture_pipeline：截获 _wrap_pipeline 收到的 payload。"""
    seen: list[dict] = []

    async def fake_wrap(task_id, user_id, task_type, payload):
        seen.append(payload)

    created: list = []
    monkeypatch.setattr(svc, "_wrap_pipeline", fake_wrap)
    monkeypatch.setattr(svc, "_register_active_task", lambda tid, factory: (created.append(factory()), True)[1])

    async def finish():
        import asyncio as _aio

        await _aio.gather(*[c for c in created if _aio.iscoroutine(c) or hasattr(c, "__await__")])

    return seen, finish


def _bare_re_render_service(tmp_path, payload: dict, storyboard: dict | None = None) -> VideoGenService:
    """裸实例 + FakeRepo（真实注入/解析链；storyboard 提供则写盘供 _settings_updater 回写）。"""
    svc = VideoGenService.__new__(VideoGenService)
    svc._storage_dir = str(tmp_path)
    svc._RE_RENDER_ALLOWED_TYPES = ("ai_image", "ai_video")
    svc._sb_write_lock = asyncio.Lock()

    class FakeRepo:
        async def get(self, task_id, user_id=None):
            return SimpleNamespace(id=task_id, status="ready", task_type="ai_image", payload=json.dumps(payload, ensure_ascii=False))

        async def update(self, task_id, **kw):
            pass

    svc.repo = FakeRepo()
    if storyboard is not None:
        task_dir = Path(tmp_path) / USER / "tasks" / "vg_edit"
        task_dir.mkdir(parents=True, exist_ok=True)
        (task_dir / "storyboard.json").write_text(json.dumps(storyboard, ensure_ascii=False), encoding="utf-8")
    return svc


@pytest.mark.asyncio
async def test_t5a_put_config_voice_dual_writes_payload(tmp_path):
    """T5a 前半：PUT config 改 voice_id → DB payload 同步写入（diff 命中）。"""
    from app.gateway.routers import video_gen as vg

    svc = _make_service(tmp_path)
    task_payload = {"bgm_volume": 0.15}
    svc.get_task = AsyncMock(return_value=SimpleNamespace(status="ready", task_type="ai_image", payload=json.dumps(task_payload)))
    svc.repo.update = AsyncMock()
    task_dir = _task_dir(tmp_path)
    _write_storyboard(task_dir)  # config.voice_id = preset:146

    await vg.update_storyboard_config(_fake_request(svc), "vg_edit", vg.UpdateStoryboardConfigRequest(voice_id="preset:200"))

    assert svc.repo.update.await_count == 1, "voice 实际变化须双写 DB payload"
    pl = json.loads(svc.repo.update.await_args.kwargs["payload"])
    assert pl["voice_id"] == "preset:200"
    assert pl["bgm_volume"] == 0.15, "双写不得覆盖 payload 其他键"
    sb = json.loads((task_dir / "storyboard.json").read_text(encoding="utf-8"))
    assert sb["config"]["voice_id"] == "preset:200"


@pytest.mark.asyncio
async def test_t5a_re_render_keeps_user_voice_after_dual_write(tmp_path, monkeypatch):
    """T5a 后半：双写后的 payload 喂 re-render → 管线拾取用户音色、盘面 config 不被旧快照改回。"""
    dual_written = {"voice_id": "preset:200", "bgm_volume": 0.15}
    storyboard = {"title": "t", "config": {"video_type": "ai_image", "width": 1080, "height": 1920, "voice_id": "preset:200"}, "frames": []}
    svc = _bare_re_render_service(tmp_path, dual_written, storyboard)
    seen, finish = _capture_re_render(svc, monkeypatch)

    await svc.re_render_task("vg_edit", USER)
    await finish()

    assert seen[0]["voice_id"] == "preset:200", "re-render 管线须拾取用户显式音色（payload=用户最新意图）"
    task_dir = Path(tmp_path) / USER / "tasks" / "vg_edit"
    cfg = json.loads((task_dir / "storyboard.json").read_text(encoding="utf-8"))["config"]
    assert cfg.get("voice_id") == "preset:200", "_settings_updater 回写不得把用户值改回旧快照"


@pytest.mark.asyncio
async def test_t5b_follow_task_bgm_only_re_render_re_resolves(tmp_path, monkeypatch):
    """T5b：跟随任务（payload 未显式音色）BGM-only 保存 → re-render 仍重取项目音色。"""
    from app.gateway.routers import video_gen as vg

    pid = _seed_project(tmp_path, voice_id="preset:332")
    payload = {"course_project_id": pid, "voice_id": None, "bgm_volume": 0.15}

    svc = _make_service(tmp_path)
    svc.get_task = AsyncMock(return_value=SimpleNamespace(status="ready", task_type="ai_image", payload=json.dumps(payload)))
    svc.repo.update = AsyncMock()
    task_dir = _task_dir(tmp_path)
    _write_storyboard(task_dir, config_extra={"voice_id": "preset:146"})

    await vg.update_storyboard_config(_fake_request(svc), "vg_edit", vg.UpdateStoryboardConfigRequest(bgm_volume=0.9))
    assert svc.repo.update.await_count == 0, "BGM-only 保存不得写 payload（tts_speed/bgm_volume 不双写）"

    svc2 = _bare_re_render_service(tmp_path, payload)
    seen, finish = _capture_re_render(svc2, monkeypatch)
    await svc2.re_render_task("vg_edit", USER)
    await finish()
    assert seen[0]["voice_id"] == "preset:332", "未显式 → 重取项目音色（P2-15 不被误杀）"


@pytest.mark.asyncio
async def test_t5c_explicit_null_voice_clears_config_and_payload(tmp_path):
    """T5c：显式 null voice_id → config 键与 payload 键同步移除；仅 null 的请求不 400。"""
    from app.gateway.routers import video_gen as vg

    svc = _make_service(tmp_path)
    payload = {"voice_id": "preset:146", "bgm_volume": 0.15}
    svc.get_task = AsyncMock(return_value=SimpleNamespace(status="ready", task_type="ai_image", payload=json.dumps(payload)))
    svc.repo.update = AsyncMock()
    task_dir = _task_dir(tmp_path)
    _write_storyboard(task_dir)  # config.voice_id = preset:146

    body = vg.UpdateStoryboardConfigRequest.model_validate({"voice_id": None})
    assert "voice_id" in body.model_fields_set, "前置：null 须被识别为显式设置"

    resp = await vg.update_storyboard_config(_fake_request(svc), "vg_edit", body)

    cfg = json.loads((task_dir / "storyboard.json").read_text(encoding="utf-8"))["config"]
    assert "voice_id" not in cfg, "显式 null 须从 config 删键（回落项目/系统默认解析链）"
    assert svc.repo.update.await_count == 1
    pl = json.loads(svc.repo.update.await_args.kwargs["payload"])
    assert "voice_id" not in pl, "payload 键须同步删除"
    assert pl["bgm_volume"] == 0.15
    assert "voice_id" not in resp["config"]


@pytest.mark.asyncio
async def test_t5d_bgm_only_save_with_same_voice_keeps_follow_semantics(tmp_path, monkeypatch):
    """T5d（v5 diff 门控）：跟随任务 BGM-only 保存但前端全量提交 voice_id=当前同值
    → payload.voice_id 不被写入（保持 None）→ re-render 仍重取项目音色。"""
    from app.gateway.routers import video_gen as vg

    pid = _seed_project(tmp_path, voice_id="preset:332")
    payload = {"course_project_id": pid, "voice_id": None, "bgm_volume": 0.15}

    svc = _make_service(tmp_path)
    svc.get_task = AsyncMock(return_value=SimpleNamespace(status="ready", task_type="ai_image", payload=json.dumps(payload)))
    svc.repo.update = AsyncMock()
    task_dir = _task_dir(tmp_path)
    _write_storyboard(task_dir, config_extra={"voice_id": "preset:146"})  # 创建期解析值

    # 前端全量提交：voice_id=当前 config 同值 + bgm 变化
    await vg.update_storyboard_config(_fake_request(svc), "vg_edit", vg.UpdateStoryboardConfigRequest(voice_id="preset:146", bgm_volume=0.9))

    assert svc.repo.update.await_count == 0, "voice 同值提交不得写 payload（diff 门控）——否则 _had_explicit_voice 误 True"
    sb = json.loads((task_dir / "storyboard.json").read_text(encoding="utf-8"))
    assert sb["config"]["bgm_volume"] == 0.9

    svc2 = _bare_re_render_service(tmp_path, payload)
    seen, finish = _capture_re_render(svc2, monkeypatch)
    await svc2.re_render_task("vg_edit", USER)
    await finish()
    assert seen[0]["voice_id"] == "preset:332", "跟随语义须保留：项目改音色 → re-render 重取"


# ---------------------------------------------------------------------------
# T7：B5 draft 态改旁白即时失效
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t7_draft_narration_edit_invalidates_audio(tmp_path):
    """draft 改旁白 → 帧 audio_path/srt/speech_duration 清除（duration 保留）+
    拼接产物删除 + payload audio_ready 清除（generate-audio 短路自然放行）。"""
    from app.gateway._shared.task_framework.schemas import DRAFT
    from app.gateway.routers import video_gen as vg

    svc = _make_service(tmp_path)
    svc.get_task = AsyncMock(return_value=SimpleNamespace(status=DRAFT, task_type="ai_image", payload="{}"))
    svc.repo.get = AsyncMock(return_value=SimpleNamespace(payload=json.dumps({"audio_ready": True, "total_duration": 12.0})))
    svc.repo.update = AsyncMock()
    task_dir = _task_dir(tmp_path)
    audio_file = task_dir / "audio" / "abc123.mp3"
    audio_file.parent.mkdir(exist_ok=True)
    audio_file.write_bytes(b"mp3")
    _write_storyboard(
        task_dir,
        frames=[
            {
                "index": 0,
                "narration": "旧旁白",
                "audio_path": str(audio_file),
                "srt": "1\n00:00:00,000 --> 00:00:02,000\n旧旁白",
                "speech_duration": 2.0,
                "duration": 2.5,
            }
        ],
    )
    for name in ("audio.mp3", "subtitle.srt", "subtitle.ass"):
        (task_dir / name).write_text("stale", encoding="utf-8")

    await vg.update_storyboard_frame(_fake_request(svc), "vg_edit", 0, vg.UpdateFrameRequest(narration="全新旁白内容"))

    sb = json.loads((task_dir / "storyboard.json").read_text(encoding="utf-8"))
    fr = sb["frames"][0]
    assert fr["narration"] == "全新旁白内容"
    assert "audio_path" not in fr, "旧音频引用须清除（试听消失）"
    assert "srt" not in fr, "帧级旧时间戳须清除"
    assert "speech_duration" not in fr, "旧语音时长须清除"
    assert fr["duration"] == 2.5, "duration 保留（v3 收窄——清掉会时长塌陷）"
    assert sb.get("subtitle_locked") is False
    for name in ("audio.mp3", "subtitle.srt", "subtitle.ass"):
        assert not (task_dir / name).exists(), f"{name} 须失效删除"
    assert audio_file.exists(), "逐帧 CAS 音频保留（未改帧零浪费）"
    payload_calls = [c for c in svc.repo.update.await_args_list if "payload" in c.kwargs]
    assert len(payload_calls) == 1, "payload 写入恰一次（另一次是 touch_task 刷 updated_at）"
    pl = json.loads(payload_calls[0].kwargs["payload"])
    assert "audio_ready" not in pl, "payload audio_ready 须清除 → generate-audio 短路放行"
    assert pl.get("total_duration") == 12.0, "total_duration 保留（UI 不塌零）"
    assert "pending_regen_frames" not in sb or 0 not in (sb.get("pending_regen_frames") or []), "draft 不标 pending（首渲重 TTS）"


# ---------------------------------------------------------------------------
# T6a–T6e：B4 批量重生配音端点 POST /tasks/{id}/regenerate-audio
# ---------------------------------------------------------------------------


def _tts_fake(fail_narrations: frozenset = frozenset()):
    """mock TTS 层：写真实文件 + 返回 TTSResult 形态；指定旁白抛错（不连坐用例）。"""

    async def _synth(narration, *, voice_id=None, speed=1.0, output_path=None, fallback_voice=None, **kw):
        if narration in fail_narrations:
            raise RuntimeError("TTS down")
        Path(output_path).write_bytes(b"mp3data")
        return SimpleNamespace(audio_path=str(output_path), srt="1" + NL + "00:00:00,000 --> 00:00:01,200" + NL + narration, duration=1.2, actual_voice_id=None)

    return _synth


async def _ffmpeg_fake(fn, args, out, **kw):
    Path(out).write_bytes(b"concat")


def _seed_regen_task(tmp_path, voice="preset:146", pending=None, locked=False, task_type="ai_image"):
    task_dir = _task_dir(tmp_path)
    from app.gateway.video_gen.service import _audio_cache_key

    frames = []
    for i, narr in enumerate(["甲句旁白", "乙句旁白"]):
        old_hash = _audio_cache_key(narr, voice, 1.2)
        old_file = task_dir / "audio" / f"{old_hash}.mp3"
        old_file.parent.mkdir(exist_ok=True)
        old_file.write_bytes(b"old")
        frames.append({"index": i, "narration": narr, "duration": 2.0, "audio_path": str(old_file), "speech_duration": 2.0, "srt": "old"})
    sb = {"title": "t", "config": {"video_type": task_type, "width": 1080, "height": 1920, "voice_id": voice, "tts_speed": 1.2, "subtitle_style": "default"}, "frames": frames}
    if locked:
        sb["subtitle_locked"] = True
    if pending is not None:
        sb["pending_regen_frames"] = list(pending)
    (task_dir / "storyboard.json").write_text(json.dumps(sb, ensure_ascii=False), encoding="utf-8")
    return task_dir, sb


def _regen_service(tmp_path, payload='{"audio_ready": true}', task_type="ai_image"):
    svc = _make_service(tmp_path)
    svc.repo.get = AsyncMock(return_value=SimpleNamespace(id="vg_edit", status="ready", task_type=task_type, payload=payload))
    svc.repo.update = AsyncMock()
    svc.repo.clear_dirty_frames = AsyncMock()
    svc._active_tasks = {}
    return svc


@pytest.mark.asyncio
async def test_t6a_regenerate_all_audio_full_chain(tmp_path, monkeypatch):
    """全帧重 TTS（CAS miss → 新 hash 文件）+ 重 concat + SRT/ASS 重写 + pending 清 + audio_ready 置位。"""
    from app.gateway.video_gen.service import _audio_cache_key

    monkeypatch.setattr("app.gateway.video_gen.orchestration.tts.synthesize", _tts_fake())
    monkeypatch.setattr("app.gateway.video_gen.service.run_ffmpeg", _ffmpeg_fake)

    task_dir, sb = _seed_regen_task(tmp_path, pending=[0])
    # 用户已改音色（PUT config 已保存 config.voice_id）
    sb["config"]["voice_id"] = "preset:200"
    (task_dir / "storyboard.json").write_text(json.dumps(sb, ensure_ascii=False), encoding="utf-8")
    old_paths = [f["audio_path"] for f in sb["frames"]]

    svc = _regen_service(tmp_path)
    result = await svc.regenerate_all_audio("vg_edit", USER)

    assert result == {"failed_frames": [], "regenerated": 2}
    disk = json.loads((task_dir / "storyboard.json").read_text(encoding="utf-8"))
    for i, fr in enumerate(disk["frames"]):
        assert fr["audio_path"] != old_paths[i], "CAS miss 须指向新文件"
        expected = task_dir / "audio" / f"{_audio_cache_key(fr['narration'], 'preset:200', 1.2)}.mp3"
        assert fr["audio_path"] == str(expected), "新文件名 = 新音色 CAS 键"
        assert expected.exists()
    assert (task_dir / "audio.mp3").exists(), "拼接产物须重建"
    srt = (task_dir / "subtitle.srt").read_text(encoding="utf-8")
    assert "甲句旁白" in srt and "乙句旁白" in srt, "SRT 须按新配音重切"
    assert (task_dir / "subtitle.ass").exists(), "ASS 须随之派生"
    assert disk.get("pending_regen_frames") == [], "成功帧 pending 须清除"
    assert all(f.get("regen_state") == "done" for f in disk["frames"]), "逐帧置/清 regen_state"
    payload_calls = [c for c in svc.repo.update.await_args_list if "payload" in c.kwargs]
    final_pl = json.loads(payload_calls[-1].kwargs["payload"])
    assert final_pl.get("audio_ready") is True


@pytest.mark.asyncio
async def test_t6b_single_frame_failure_not_connected(tmp_path, monkeypatch):
    """单帧 TTS 失败不连坐：其余帧完成、failed_frames 汇总、失败帧保留旧音且 pending 保留、audio_ready 不置。"""
    monkeypatch.setattr("app.gateway.video_gen.orchestration.tts.synthesize", _tts_fake(fail_narrations=frozenset(["乙句旁白"])))
    monkeypatch.setattr("app.gateway.video_gen.service.run_ffmpeg", _ffmpeg_fake)

    task_dir, sb = _seed_regen_task(tmp_path, pending=[0, 1])
    # 改语速强制全帧 CAS miss（否则旧文件命中短路、走不到 TTS 失败路径）
    sb["config"]["tts_speed"] = 1.5
    (task_dir / "storyboard.json").write_text(json.dumps(sb, ensure_ascii=False), encoding="utf-8")
    old_frame1_audio = sb["frames"][1]["audio_path"]

    svc = _regen_service(tmp_path)
    result = await svc.regenerate_all_audio("vg_edit", USER)

    assert result["failed_frames"] == [1]
    assert result["regenerated"] == 1
    disk = json.loads((task_dir / "storyboard.json").read_text(encoding="utf-8"))
    assert disk["frames"][1]["audio_path"] == old_frame1_audio, "失败帧保留旧音兜底"
    assert disk["frames"][0]["audio_path"] != sb["frames"][0]["audio_path"], "成功帧用新音"
    assert disk.get("pending_regen_frames") == [1], "失败帧 pending 保留（recompose 继续 409）"
    assert disk["frames"][1].get("regen_state") == "error"
    assert disk["frames"][0].get("regen_state") == "done"
    payload_calls = [c for c in svc.repo.update.await_args_list if "payload" in c.kwargs]
    final_pl = json.loads(payload_calls[-1].kwargs["payload"])
    assert "audio_ready" not in final_pl, "有失败帧不置 audio_ready"
    assert final_pl.get("audio_regen_failed_frames") == [1]


@pytest.mark.asyncio
async def test_t6c_locked_task_clears_lock_and_recuts(tmp_path, monkeypatch):
    """锁字幕任务批量重生 → subtitle_locked 清除 + 字幕按新配音重切（决策 1）。"""
    monkeypatch.setattr("app.gateway.video_gen.orchestration.tts.synthesize", _tts_fake())
    monkeypatch.setattr("app.gateway.video_gen.service.run_ffmpeg", _ffmpeg_fake)

    task_dir, sb = _seed_regen_task(tmp_path, locked=True)
    (task_dir / "subtitle.srt").write_text("1" + NL + "00:00:00,000 --> 00:00:02,000" + NL + "用户手改的旧字幕", encoding="utf-8")

    svc = _regen_service(tmp_path)
    await svc.regenerate_all_audio("vg_edit", USER)

    disk = json.loads((task_dir / "storyboard.json").read_text(encoding="utf-8"))
    assert disk.get("subtitle_locked") is False, "决策 1：清锁按新配音重切"
    srt = (task_dir / "subtitle.srt").read_text(encoding="utf-8")
    assert "用户手改的旧字幕" not in srt, "手改文本被覆盖（弹层已明示）"
    assert "甲句旁白" in srt


@pytest.mark.asyncio
async def test_t6d_endpoint_mutual_exclusion_and_registration(tmp_path, monkeypatch):
    """互斥四向：任务级在跑 409 / 单帧在跑 409 / 重复发起 409；反向——批量运行中
    全帧键已登记（入口一次性，v5）；结束后键摘除。"""
    from app.gateway.routers import video_gen as vg

    monkeypatch.setattr("app.gateway.video_gen.orchestration.tts.synthesize", _tts_fake())
    monkeypatch.setattr("app.gateway.video_gen.service.run_ffmpeg", _ffmpeg_fake)
    task_dir, sb = _seed_regen_task(tmp_path)

    svc = _regen_service(tmp_path)
    svc.get_task = AsyncMock(return_value=SimpleNamespace(status="ready", task_type="ai_image", payload="{}"))
    req = _fake_request(svc)

    # ① 任务级 pipeline 在跑（未完成 Future）
    import asyncio as _aio

    svc._active_tasks["vg_edit"] = _aio.get_running_loop().create_future()
    with pytest.raises(HTTPException) as ei:
        await vg.regenerate_all_audio(req, "vg_edit")
    assert ei.value.status_code == 409
    svc._active_tasks.clear()

    # ② 单帧重生在跑（帧级键前缀）
    vg._regen_inflight[("vg_edit", 0)] = _aio.get_running_loop().create_future()
    with pytest.raises(HTTPException) as ei:
        await vg.regenerate_all_audio(req, "vg_edit")
    assert ei.value.status_code == 409
    vg._regen_inflight.clear()

    # ③ 重复发起（payload audio_regen_running）
    svc.get_task = AsyncMock(return_value=SimpleNamespace(status="ready", task_type="ai_image", payload=json.dumps({"audio_regen_running": True})))
    with pytest.raises(HTTPException) as ei:
        await vg.regenerate_all_audio(req, "vg_edit")
    assert ei.value.status_code == 409

    # ④ 正常发起：返回前全帧键已登记（v5 入口一次性）；结束后全部摘除
    svc.get_task = AsyncMock(return_value=SimpleNamespace(status="ready", task_type="ai_image", payload="{}"))
    resp = await vg.regenerate_all_audio(req, "vg_edit")
    assert resp["status"] == "started"
    assert set(resp["frames"]) == {0, 1}
    assert ("vg_edit", 0) in vg._regen_inflight and ("vg_edit", 1) in vg._regen_inflight, "入口一次性登记全部帧键"
    bg = vg._regen_inflight[("vg_edit", 0)]
    await bg
    assert ("vg_edit", 0) not in vg._regen_inflight and ("vg_edit", 1) not in vg._regen_inflight, "结束后摘除"

    # ⑤ 反向互斥：批量登记期间 recompose/re-render/单帧重生 409（前缀/精确键皆命中）
    vg._regen_inflight[("vg_edit", 0)] = _aio.get_running_loop().create_future()
    svc2 = _regen_service(tmp_path)
    svc2.get_task = AsyncMock(return_value=SimpleNamespace(status="ready", task_type="ai_image", payload="{}"))
    with pytest.raises(HTTPException) as ei:
        await vg.recompose_task(_fake_request(svc2), "vg_edit")
    assert ei.value.status_code == 409
    vg._regen_inflight.clear()


@pytest.mark.asyncio
async def test_t6e_full_recompose_teardown_and_ttl_safety(tmp_path, monkeypatch):
    """完成后 recompose 走全量：segments 缓存删 + dirty 清空 + regen_state 无长 running。"""
    monkeypatch.setattr("app.gateway.video_gen.orchestration.tts.synthesize", _tts_fake())
    monkeypatch.setattr("app.gateway.video_gen.service.run_ffmpeg", _ffmpeg_fake)

    task_dir, sb = _seed_regen_task(tmp_path)
    seg = task_dir / "segments" / "seg_0.mp4"
    seg.parent.mkdir(exist_ok=True)
    seg.write_bytes(b"seg")
    (task_dir / "manifest.json").write_text("{}", encoding="utf-8")

    svc = _regen_service(tmp_path)
    await svc.regenerate_all_audio("vg_edit", USER)

    assert not (task_dir / "segments").exists(), "segments 缓存须删除（强制下次全量）"
    assert not (task_dir / "manifest.json").exists()
    svc.repo.clear_dirty_frames.assert_awaited_once_with("vg_edit")
    disk = json.loads((task_dir / "storyboard.json").read_text(encoding="utf-8"))
    assert all(f.get("regen_state") != "running" for f in disk["frames"]), "逐帧置/清——无整任务长 running（10min TTL 误杀面）"


@pytest.mark.asyncio
async def test_t6e_remotion_course_skips_ass_and_segments(tmp_path, monkeypatch):
    """remotion_course：跳过整片 SRT/ASS 重写与 segments 删除（帧级 srt→captions 链自洽）。"""
    monkeypatch.setattr("app.gateway.video_gen.orchestration.tts.synthesize", _tts_fake())
    monkeypatch.setattr("app.gateway.video_gen.service.run_ffmpeg", _ffmpeg_fake)

    task_dir, sb = _seed_regen_task(tmp_path, task_type="remotion_course")
    seg = task_dir / "segments" / "seg_0.mp4"
    seg.parent.mkdir(exist_ok=True)
    seg.write_bytes(b"seg")
    stale_srt = task_dir / "subtitle.srt"
    stale_srt.write_text("STALE SRT", encoding="utf-8")

    svc = _regen_service(tmp_path, task_type="remotion_course")
    result = await svc.regenerate_all_audio("vg_edit", USER)

    assert result["regenerated"] == 2
    assert stale_srt.read_text(encoding="utf-8") == "STALE SRT", "remotion 不消费整片 srt/ass——保持不动"
    assert seg.exists(), "remotion 无 segments 链——不删"
    svc.repo.clear_dirty_frames.assert_not_awaited()


# ---------------------------------------------------------------------------
# review 收尾（2026-09-28）：B4 启动清理守护
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recover_audio_regen_flags_clears_stale_running(tmp_path):
    """启动清理：按 video_gen 域扫描，仅清 audio_regen_running=true 的残留，
    payload 其余键保留、附引导文案（review 收尾 1/2——域默认值与 LIKE 收紧的守护）。"""
    svc = _make_service(tmp_path)
    svc.repo.list_audio_regen_running = AsyncMock(
        return_value=[
            SimpleNamespace(id="a", payload='{"audio_regen_running": true, "bgm_volume": 0.15}'),
            SimpleNamespace(id="b", payload='{"audio_ready": true}'),  # json 复核应排除
        ]
    )
    svc.repo.update = AsyncMock()

    n = await svc.recover_audio_regen_flags()

    assert n == 1
    svc.repo.list_audio_regen_running.assert_awaited_once_with("video_gen")
    svc.repo.update.assert_awaited_once()
    pl = json.loads(svc.repo.update.await_args.kwargs["payload"])
    assert "audio_regen_running" not in pl
    assert "请重新发起批量重生配音" in pl["audio_error"]
    assert pl["bgm_volume"] == 0.15
