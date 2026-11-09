"""T2 (Lane 2, 2026-10-15): vimax redo 逐镜重做补全（用户拍板补全，不摘除）。

修前三断点（plan 证据链 #2）：
1. 生产方从不写 ``vimax_shots`` manifest → redo 端点读空恒 404（前端入口死链）；
2. 端点 ``_gen`` 丢 ``reference_images``——redo_vimax_shot 组装的参考图白算；
3. 结果不回写不重拼——烧钱返回 200 但视频不变。

行为级断言①（plan T9）：``vimax_shots`` 非空 → POST redo → 200 → result 更新 → 成片重拼。
参考图降级链（决策①）：360 通道拦 AI 参考图 → 降级首帧参考 → 纯 prompt，result 留痕。
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.gateway.video_gen.engines.vimax.engine as ve
from app.gateway.video_gen.engines.base import EngineContext
from app.gateway.video_gen.engines.vimax.engine import VimaxEngine, build_vimax_shots_manifest

# ── 生产方：manifest 构建 ────────────────────────────────────────────────────


def _seed_shot(working_dir: Path, scene: Path, idx: int, *, with_frame: bool = True) -> None:
    shot_dir = scene / "shots" / str(idx)
    shot_dir.mkdir(parents=True, exist_ok=True)
    (shot_dir / "video.mp4").write_bytes(f"OLD{idx}".encode())
    if with_frame:
        (shot_dir / "first_frame.png").write_bytes(f"PNG{idx}".encode())


def _seed_storyboard(scene: Path, shots: list[dict]) -> None:
    (scene / "storyboard.json").write_text(json.dumps(shots), encoding="utf-8")


@pytest.fixture(autouse=True)
def _fake_probe(monkeypatch):
    """manifest/redo 的时长探测不打真 ffprobe——run_ffmpeg 包裹的同步桩。"""

    def _probe(video_path) -> float:
        return 5.5

    monkeypatch.setattr(ve, "_get_shot_duration", _probe)


@pytest.mark.asyncio
async def test_build_shots_manifest_script2video(tmp_path):
    """script2video：根 storyboard.json → 连续全局键，字段齐、路径相对 task_dir。"""
    task_dir = tmp_path
    working_dir = task_dir / "vimax_working"
    for i in range(3):
        _seed_shot(working_dir, working_dir, i)
    _seed_storyboard(working_dir, [{"idx": 0, "visual_desc": "v0"}, {"idx": 1, "visual_desc": "v1"}, {"idx": 2, "visual_desc": "v2"}])

    manifest = await build_vimax_shots_manifest(working_dir, task_dir)

    assert set(manifest.keys()) == {"0", "1", "2"}, f"三镜应得连续全局键，实际 {sorted(manifest)}"
    m1 = manifest["1"]
    assert m1["prompt"] == "v1"
    assert m1["video_path"] == "vimax_working/shots/1/video.mp4", "路径须相对 task_dir（result 持久化可移植）"
    assert m1["visual_path"] == "vimax_working/shots/1/first_frame.png"
    assert m1["duration"] == 5.5
    assert m1["reference_images"] == ["vimax_working/shots/1/first_frame.png"]
    assert m1["scene"] is None
    assert m1["scene_shot_idx"] == 1


@pytest.mark.asyncio
async def test_build_shots_manifest_multi_scene_global_keys(tmp_path):
    """idea2video 多场景：scene_N 各自 storyboard，镜内 idx 重复 → 全局连续键去重。"""
    task_dir = tmp_path
    working_dir = task_dir / "vimax_working"
    for scene_idx in (0, 1):
        scene = working_dir / f"scene_{scene_idx}"
        for i in (0, 1):
            _seed_shot(working_dir, scene, i)
        _seed_storyboard(scene, [{"idx": 0, "visual_desc": f"s{scene_idx}v0"}, {"idx": 1, "visual_desc": f"s{scene_idx}v1"}])

    manifest = await build_vimax_shots_manifest(working_dir, task_dir)

    assert sorted(manifest.keys()) == ["0", "1", "2", "3"], f"两场景各两镜 → 全局 0-3，实际 {sorted(manifest)}"
    assert manifest["2"]["scene"] == "scene_1"
    assert manifest["2"]["scene_shot_idx"] == 0, "镜内原始 idx 保留在 scene_shot_idx"
    assert manifest["2"]["video_path"] == "vimax_working/scene_1/shots/0/video.mp4"


@pytest.mark.asyncio
async def test_render_outcome_carries_vimax_shots(tmp_path):
    """render 完成 → EngineOutcome.engine_metadata 携带 vimax_shots（service 落 result 的载体）。"""
    working_dir = tmp_path / "vimax_working"
    for i in range(2):
        _seed_shot(working_dir, working_dir, i)
    _seed_storyboard(working_dir, [{"idx": 0, "visual_desc": "v0"}, {"idx": 1, "visual_desc": "v1"}])

    async def pipeline(*args, **kwargs):
        return None

    engine = VimaxEngine(config_factory=lambda wd, req: {"working_dir": str(wd)}, pipeline_factory=lambda _: pipeline)
    outcome = await engine.render(EngineContext(user_id="u", task_id="t", task_dir=tmp_path, request={"script": "s"}))

    assert outcome.engine_metadata.get("vimax_shots"), "engine_metadata 须携带 manifest，否则 redo 端点恒 404（修前形态）"
    assert set(outcome.engine_metadata["vimax_shots"].keys()) == {"0", "1"}


# ── service 落 result 的接缝 ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_finish_engine_outcome_merges_metadata(tmp_path):
    """finished 分支：engine_metadata 并入 result（set_result 合并语义），output_path/file_size 同写。"""
    from app.gateway.video_gen.engines.base import EngineOutcome
    from app.gateway.video_gen.service import _finish_engine_outcome

    out_file = tmp_path / "vimax_final.mp4"
    out_file.write_bytes(b"x" * 42)

    recorded: dict = {}

    class _Progress:
        async def __call__(self, **kw):
            recorded.setdefault("progress", []).append(kw)

        async def set_result(self, **kw):
            recorded.setdefault("result", {}).update(kw)

    outcome = EngineOutcome(mode="finished", finished_video_path=str(out_file), engine_metadata={"vimax_shots": {"0": {"prompt": "p"}}})
    await _finish_engine_outcome(_Progress(), outcome)

    merged = recorded["result"]
    assert merged["vimax_shots"] == {"0": {"prompt": "p"}}, "engine_metadata 须并入 result——redo 端点的数据源"
    assert merged["output_path"] == str(out_file)
    assert merged["file_size"] == 42


# ── 端点级 e2e（行为级断言①）────────────────────────────────────────────────


class _FakeVideoRef:
    def __init__(self, url: str):
        self.url = url


def _make_service(tmp_path, task_result: dict, payload: dict | None = None):
    from app.gateway.video_gen.config import VideoGenConfig
    from app.gateway.video_gen.providers.registry import ProviderRegistry
    from app.gateway.video_gen.service import VideoGenService

    cfg = VideoGenConfig(qihu_api_key="t")
    repo = MagicMock()
    task = MagicMock()
    task.task_type = "vimax"
    task.status = "ready"
    task.payload = json.dumps(payload or {"aspect": "9:16"}, ensure_ascii=False)
    task.result = json.dumps(task_result, ensure_ascii=False)
    repo.get = AsyncMock(return_value=task)
    repo.update = AsyncMock(return_value=None)
    svc = VideoGenService(repo=repo, config=cfg, providers=ProviderRegistry(cfg), storage_dir=str(tmp_path), sandbox_provider=AsyncMock())
    return svc, repo, task


def _make_request(svc):
    class _Req:
        class app:
            class state:
                video_gen_service = svc

    return _Req()


def _seed_task_disk(tmp_path, shots: int = 3, task_id: str = "vg_redo", user: str = "test-user-autouse") -> tuple[Path, Path]:
    """task_dir 下 vimax_working 三镜 + 根 storyboard + narration/subtitle。

    user 目录对齐 tests/conftest.py 的 autouse 用户注入（test-user-autouse）。"""
    task_dir = tmp_path / user / "tasks" / task_id
    working_dir = task_dir / "vimax_working"
    for i in range(shots):
        _seed_shot(working_dir, working_dir, i)
    _seed_storyboard(working_dir, [{"idx": i, "visual_desc": f"v{i}"} for i in range(shots)])
    (task_dir / "vimax_narration.mp3").write_bytes(b"AUDIO")
    (task_dir / "subtitle.srt").write_bytes(b"1\n00:00:00,000 --> 00:00:01,000\nhi\n")
    return task_dir, working_dir


async def _fake_manifest(working_dir: Path, task_dir: Path) -> dict:
    manifest = await build_vimax_shots_manifest(working_dir, task_dir)
    return manifest


@pytest.mark.asyncio
async def test_redo_endpoint_end_to_end(tmp_path, monkeypatch):
    """vimax_shots 非空 → POST redo → 200 → 单镜替换 + 重拼 + result 更新（行为级断言①）。"""
    task_dir, working_dir = _seed_task_disk(tmp_path)
    manifest = await _fake_manifest(working_dir, task_dir)

    provider_calls: list[dict] = []

    async def fake_generate_video(prompt, *, duration=30.0, **kwargs):
        provider_calls.append({"prompt": prompt, "duration": duration, **kwargs})
        return _FakeVideoRef("http://cdn/new_shot_1.mp4")

    # 重拼/交付链打桩（真实 ffmpeg/moviepy 由部署侧生效验证覆盖）
    concat_calls: list[list[str]] = []

    def fake_concat(paths, output, **kwargs):
        concat_calls.append(list(paths))
        Path(output).write_bytes(b"RECONCAT")

    async def fake_overlay(video_path, task_dir, *, audio_override=None, burn_subtitles=False, subtitle_style="default"):
        out = Path(task_dir) / "vimax_final_loudnorm.mp4"
        out.write_bytes(b"RECONCAT-AUDIO")
        return out

    async def fake_download(url, **kwargs):
        return b"NEWVIDEO"

    svc, repo, _task = _make_service(tmp_path, task_result={"vimax_shots": manifest})
    monkeypatch.setattr(svc._providers, "ai_video", MagicMock(generate_video=fake_generate_video))
    monkeypatch.setattr(ve, "concatenate_video_files", fake_concat)
    monkeypatch.setattr(ve, "overlay_audio_track", fake_overlay)
    monkeypatch.setattr(ve, "safe_download", fake_download)

    from app.gateway.routers.video_gen import redo_vimax_shot_endpoint

    resp = await redo_vimax_shot_endpoint(_make_request(svc), "vg_redo", 1)

    assert resp["regenerated_shot_id"] == 1
    # ①参考图透传：邻镜 0/2 首帧（本地文件 → data-URI）经 image_urls 下发 provider
    assert len(provider_calls) == 1, f"无降级场景应恰一次 provider 调用，实际 {len(provider_calls)}"
    urls = provider_calls[0].get("image_urls") or []
    assert len(urls) == 2, f"邻镜两参考图须透传，实际 {urls}"
    assert all(u.startswith("data:image/png;base64,") for u in urls), "本地参考图须转 data-URI（_to_image_url 形态）"
    assert provider_calls[0]["prompt"] == "v1"
    assert provider_calls[0]["duration"] == manifest["1"]["duration"], "时长用 manifest 实测值，非硬编码 5.0"
    assert provider_calls[0].get("ratio") == "9:16", "任务 aspect 须作 ratio 透传保画幅"
    # ②单镜替换
    assert (working_dir / "shots" / "1" / "video.mp4").read_bytes() == b"NEWVIDEO"
    assert (working_dir / "shots" / "0" / "video.mp4").read_bytes() == b"OLD0", "邻镜不动"
    # ③重拼：全镜按序 concat
    assert concat_calls == [[str(working_dir / "shots" / str(i) / "video.mp4") for i in range(3)]]
    # ④交付重跑：重拼后的静默成片落盘（overlay 吃它 + 原 narration）
    assert (working_dir / "final_video.mp4").read_bytes() == b"RECONCAT"
    # ⑤result 更新：output/时长/留痕
    update_kwargs = repo.update.call_args.kwargs
    merged = json.loads(update_kwargs["result"])
    assert merged["output_path"].endswith("vimax_final_loudnorm.mp4")
    assert merged["file_size"] > 0
    assert merged["vimax_shots"]["1"]["redo"]["reference_mode"] == "full", "无降级留痕 full"
    assert merged["vimax_shots"]["1"]["duration"] == 5.5, "替换后时长须按新视频回写"


@pytest.mark.asyncio
async def test_redo_downgrade_first_frame(tmp_path, monkeypatch):
    """决策①降级档一：全量参考被审 → 降级首帧参考成功，result 留痕。"""
    task_dir, working_dir = _seed_task_disk(tmp_path)
    manifest = await _fake_manifest(working_dir, task_dir)

    calls: list[dict] = []

    async def fake_generate_video(prompt, *, duration=30.0, **kwargs):
        calls.append({"prompt": prompt, **kwargs})
        urls = kwargs.get("image_urls") or []
        if len(urls) >= 2:
            raise RuntimeError("green net check failed")
        return _FakeVideoRef("http://cdn/downgraded.mp4")

    async def fake_download(url, **kwargs):
        return b"DOWNGRADED"

    def fake_concat(paths, output, **kwargs):
        Path(output).write_bytes(b"RECONCAT")

    async def fake_overlay(video_path, task_dir, *, audio_override=None, burn_subtitles=False, subtitle_style="default"):
        out = Path(task_dir) / "vimax_final_loudnorm.mp4"
        out.write_bytes(b"X")
        return out

    svc, repo, _task = _make_service(tmp_path, task_result={"vimax_shots": manifest})
    monkeypatch.setattr(svc._providers, "ai_video", MagicMock(generate_video=fake_generate_video))
    monkeypatch.setattr(ve, "concatenate_video_files", fake_concat)
    monkeypatch.setattr(ve, "overlay_audio_track", fake_overlay)
    monkeypatch.setattr(ve, "safe_download", fake_download)

    from app.gateway.routers.video_gen import redo_vimax_shot_endpoint

    resp = await redo_vimax_shot_endpoint(_make_request(svc), "vg_redo", 1)

    assert resp["regenerated_shot_id"] == 1
    assert resp["reference_mode"] == "first_frame_only"
    assert len(calls) == 2 and len(calls[1].get("image_urls") or []) == 1, "第二次调用只带首帧参考"
    merged = json.loads(repo.update.call_args.kwargs["result"])
    assert merged["vimax_shots"]["1"]["redo"]["reference_mode"] == "first_frame_only"
    assert "green net" in merged["vimax_shots"]["1"]["redo"]["note"]


@pytest.mark.asyncio
async def test_redo_downgrade_prompt_only(tmp_path, monkeypatch):
    """决策①降级档二：首帧参考也被审 → 纯 prompt 重生，参考图不摘除语义留痕。"""
    task_dir, working_dir = _seed_task_disk(tmp_path)
    manifest = await _fake_manifest(working_dir, task_dir)

    calls: list[dict] = []

    async def fake_generate_video(prompt, *, duration=30.0, **kwargs):
        calls.append({"prompt": prompt, **kwargs})
        if kwargs.get("image_urls"):
            raise RuntimeError("green net check failed")
        return _FakeVideoRef("http://cdn/t2v.mp4")

    async def fake_download(url, **kwargs):
        return b"T2V"

    def fake_concat(paths, output, **kwargs):
        Path(output).write_bytes(b"RECONCAT")

    async def fake_overlay(video_path, task_dir, *, audio_override=None, burn_subtitles=False, subtitle_style="default"):
        out = Path(task_dir) / "vimax_final_loudnorm.mp4"
        out.write_bytes(b"X")
        return out

    svc, repo, _task = _make_service(tmp_path, task_result={"vimax_shots": manifest})
    monkeypatch.setattr(svc._providers, "ai_video", MagicMock(generate_video=fake_generate_video))
    monkeypatch.setattr(ve, "concatenate_video_files", fake_concat)
    monkeypatch.setattr(ve, "overlay_audio_track", fake_overlay)
    monkeypatch.setattr(ve, "safe_download", fake_download)

    from app.gateway.routers.video_gen import redo_vimax_shot_endpoint

    resp = await redo_vimax_shot_endpoint(_make_request(svc), "vg_redo", 1)

    assert resp["reference_mode"] == "prompt_only"
    assert len(calls) == 3 and not calls[2].get("image_urls"), "第三次调用纯 prompt"
    merged = json.loads(repo.update.call_args.kwargs["result"])
    assert merged["vimax_shots"]["1"]["redo"]["reference_mode"] == "prompt_only"


@pytest.mark.asyncio
async def test_redo_endpoint_404_when_manifest_missing(tmp_path, monkeypatch):
    """无 manifest（修前恒态）→ 404 Shot not found（端点守卫保持）。"""
    svc, repo, _task = _make_service(tmp_path, task_result={})
    from fastapi import HTTPException

    from app.gateway.routers.video_gen import redo_vimax_shot_endpoint

    with pytest.raises(HTTPException) as ei:
        await redo_vimax_shot_endpoint(_make_request(svc), "vg_redo", 0)
    assert ei.value.status_code == 404
