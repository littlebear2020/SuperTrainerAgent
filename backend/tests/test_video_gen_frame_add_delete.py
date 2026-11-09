"""M-UX: manual storyboard frame add/delete + reindex (POST/DELETE endpoints)."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.gateway._shared.task_framework.schemas import DRAFT
from app.gateway.routers.video_gen import router


def _make_app(tmp_path, *, task_status, has_storyboard_file=True):
    app = FastAPI()
    app.include_router(router)
    storage_root = (tmp_path / "storage").resolve()
    storage_root.mkdir()
    task_dir = storage_root / "u1" / "tasks" / "vg_ad"
    task_dir.mkdir(parents=True)
    if has_storyboard_file:
        (task_dir / "storyboard.json").write_text(
            json.dumps(
                {
                    "title": "t",
                    "config": {},
                    "frames": [{"index": 0, "narration": "n0"}],
                }
            )
        )
    mock_svc = MagicMock()
    mock_task = MagicMock()
    mock_task.id = "vg_ad"
    mock_task.status = task_status
    mock_task.result = "{}"
    mock_task.payload = "{}"
    mock_svc.get_task = AsyncMock(return_value=mock_task)
    mock_svc._storage_dir = str(storage_root)
    mock_svc._add_frame = AsyncMock()
    mock_svc._delete_frame = AsyncMock()
    # T4(ee042bc03) 起 add/delete 端点末尾 await svc.touch_task(...)——绑定 async mock
    mock_svc.touch_task = AsyncMock(return_value=None)
    app.state.video_gen_service = mock_svc
    return app


def test_add_frame_route_draft_200(tmp_path):
    """draft → POST /storyboard/frames → 200，调 _add_frame。"""
    app = _make_app(tmp_path, task_status=DRAFT)
    client = TestClient(app)
    resp = client.post("/api/video/tasks/vg_ad/storyboard/frames", json={"narration": "new"})
    assert resp.status_code == 200, resp.text
    app.state.video_gen_service._add_frame.assert_awaited_once()


def test_delete_frame_route_draft_200(tmp_path):
    """draft → DELETE /storyboard/frames/{i} → 200，调 _delete_frame。"""
    app = _make_app(tmp_path, task_status=DRAFT)
    client = TestClient(app)
    resp = client.delete("/api/video/tasks/vg_ad/storyboard/frames/0")
    assert resp.status_code == 200, resp.text
    app.state.video_gen_service._delete_frame.assert_awaited_once()


def test_add_delete_409_on_composing(tmp_path):
    """composing 期增删拒 409（与 storyboard GET 一致，状态门控）。"""
    app = _make_app(tmp_path, task_status="composing")
    client = TestClient(app)
    r1 = client.post("/api/video/tasks/vg_ad/storyboard/frames", json={"narration": "x"})
    r2 = client.delete("/api/video/tasks/vg_ad/storyboard/frames/0")
    assert r1.status_code == 409
    assert r2.status_code == 409


def test_add_delete_409_on_ready(tmp_path):
    """ready 态增删拒 409：recompose 不重 TTS（复用盘面 audio.mp3），ready 态增删后
    重组会音画错位（删帧后音频仍含被删帧配音 / 增帧无配音）。仅 draft 允许增删
    （draft→render 会重 TTS 重建 audio.mp3，无错位）。"""
    app = _make_app(tmp_path, task_status="ready")
    client = TestClient(app)
    r1 = client.post("/api/video/tasks/vg_ad/storyboard/frames", json={"narration": "x"})
    r2 = client.delete("/api/video/tasks/vg_ad/storyboard/frames/0")
    assert r1.status_code == 409
    assert r2.status_code == 409


@pytest.mark.asyncio
async def test_add_delete_frame_service_reindexes(tmp_path):
    """_add_frame/_delete_frame: 删帧后重排 index 0..N 连续；加帧追加 + 重排。

    BUG 3：_add_frame/_delete_frame 现在会调 _invalidate_audio_artifacts 失效盘面音频，
    需 mock repo.get 返回带 payload 的 task。
    """
    from app.gateway.video_gen.config import VideoGenConfig
    from app.gateway.video_gen.providers.registry import ProviderRegistry
    from app.gateway.video_gen.service import VideoGenService

    cfg = VideoGenConfig(qihu_api_key="t", engine_arch="multi_engine")
    providers = ProviderRegistry(cfg)
    # mock repo: get 返回带 audio_ready payload 的 task；update 接收写回
    mock_repo = MagicMock()
    mock_task = MagicMock()
    mock_task.payload = json.dumps({"audio_ready": True, "total_duration": 30.0})
    mock_repo.get = AsyncMock(return_value=mock_task)
    mock_repo.update = AsyncMock(return_value=None)
    svc = VideoGenService(
        repo=mock_repo,
        config=cfg,
        providers=providers,
        storage_dir=str(tmp_path),
        sandbox_provider=AsyncMock(),
    )
    user_id = "u1"
    task_id = "vg_ad"
    task_dir = Path(svc._storage_dir) / user_id / "tasks" / task_id
    task_dir.mkdir(parents=True)
    (task_dir / "storyboard.json").write_text(
        json.dumps(
            {
                "title": "t",
                "config": {},
                "frames": [
                    {"index": 0, "narration": "n0"},
                    {"index": 1, "narration": "n1"},
                    {"index": 2, "narration": "n2"},
                ],
            }
        )
    )

    # delete middle frame -> reindex
    await svc._delete_frame(task_id, user_id, 1)
    data = json.loads((task_dir / "storyboard.json").read_text())
    assert [f["narration"] for f in data["frames"]] == ["n0", "n2"]
    assert [f["index"] for f in data["frames"]] == [0, 1]

    # add frame -> append + reindex
    await svc._add_frame(task_id, user_id, {"narration": "new"})
    data = json.loads((task_dir / "storyboard.json").read_text())
    assert [f["narration"] for f in data["frames"]] == ["n0", "n2", "new"]
    assert [f["index"] for f in data["frames"]] == [0, 1, 2]

    # BUG 3: _invalidate_audio_artifacts 应清掉 audio_ready（update 被调用过）
    assert mock_repo.update.await_count >= 2, "增删各调一次 _invalidate_audio_artifacts -> repo.update"


@pytest.mark.asyncio
async def test_delete_frame_invalidates_audio_artifacts(tmp_path):
    """BUG 3：删帧后 audio.mp3/subtitle.srt 被删、payload.audio_ready 被清。"""
    from app.gateway.video_gen.config import VideoGenConfig
    from app.gateway.video_gen.providers.registry import ProviderRegistry
    from app.gateway.video_gen.service import VideoGenService

    cfg = VideoGenConfig(qihu_api_key="t", engine_arch="multi_engine")
    providers = ProviderRegistry(cfg)
    mock_repo = MagicMock()
    mock_task = MagicMock()
    mock_task.payload = json.dumps({"audio_ready": True, "total_duration": 30.0})
    mock_repo.get = AsyncMock(return_value=mock_task)
    mock_repo.update = AsyncMock(return_value=None)
    svc = VideoGenService(
        repo=mock_repo,
        config=cfg,
        providers=providers,
        storage_dir=str(tmp_path),
        sandbox_provider=AsyncMock(),
    )
    user_id = "u1"
    task_id = "vg_ad"
    task_dir = Path(svc._storage_dir) / user_id / "tasks" / task_id
    task_dir.mkdir(parents=True)
    (task_dir / "storyboard.json").write_text(
        json.dumps(
            {
                "title": "t",
                "config": {},
                "frames": [
                    {"index": 0, "narration": "n0"},
                    {"index": 1, "narration": "n1"},
                ],
            }
        )
    )
    # 创建盘面音频/字幕产物
    (task_dir / "audio.mp3").write_bytes(b"fake-audio")
    (task_dir / "subtitle.srt").write_text("fake-srt")

    await svc._delete_frame(task_id, user_id, 1)

    # audio.mp3 / subtitle.srt 已删
    assert not (task_dir / "audio.mp3").exists(), "audio.mp3 应被删除"
    assert not (task_dir / "subtitle.srt").exists(), "subtitle.srt 应被删除"
    # payload.audio_ready 被清（total_duration 保留）
    mock_repo.update.assert_awaited()
    updated_payload = json.loads(mock_repo.update.call_args.kwargs["payload"])
    assert "audio_ready" not in updated_payload, "audio_ready 应被清除"
    assert updated_payload.get("total_duration") == 30.0, "total_duration 应保留旧值"


@pytest.mark.asyncio
async def test_add_frame_invalidates_audio_artifacts(tmp_path):
    """BUG 3：增帧后 audio.mp3/subtitle.srt 被删、payload.audio_ready 被清。"""
    from app.gateway.video_gen.config import VideoGenConfig
    from app.gateway.video_gen.providers.registry import ProviderRegistry
    from app.gateway.video_gen.service import VideoGenService

    cfg = VideoGenConfig(qihu_api_key="t", engine_arch="multi_engine")
    providers = ProviderRegistry(cfg)
    mock_repo = MagicMock()
    mock_task = MagicMock()
    mock_task.payload = json.dumps({"audio_ready": True, "audio_error": None, "total_duration": 15.0})
    mock_repo.get = AsyncMock(return_value=mock_task)
    mock_repo.update = AsyncMock(return_value=None)
    svc = VideoGenService(
        repo=mock_repo,
        config=cfg,
        providers=providers,
        storage_dir=str(tmp_path),
        sandbox_provider=AsyncMock(),
    )
    user_id = "u1"
    task_id = "vg_ad"
    task_dir = Path(svc._storage_dir) / user_id / "tasks" / task_id
    task_dir.mkdir(parents=True)
    (task_dir / "storyboard.json").write_text(json.dumps({"title": "t", "config": {}, "frames": [{"index": 0, "narration": "n0"}]}))
    (task_dir / "audio.mp3").write_bytes(b"fake-audio")
    (task_dir / "subtitle.srt").write_text("fake-srt")

    await svc._add_frame(task_id, user_id, {"narration": "new"})

    assert not (task_dir / "audio.mp3").exists(), "audio.mp3 应被删除"
    assert not (task_dir / "subtitle.srt").exists(), "subtitle.srt 应被删除"
    updated_payload = json.loads(mock_repo.update.call_args.kwargs["payload"])
    assert "audio_ready" not in updated_payload, "audio_ready 应被清除"
    assert "audio_error" not in updated_payload, "audio_error 应被清除"
    assert updated_payload.get("total_duration") == 15.0, "total_duration 应保留旧值"


@pytest.mark.asyncio
async def test_add_frame_mid_insert_reindexes_and_regroups(tmp_path):
    """中插（index=1）：帧序插入 + 重排 0..N；新帧并入前一镜（N=0）所在组。"""
    from app.gateway.video_gen.config import VideoGenConfig
    from app.gateway.video_gen.providers.registry import ProviderRegistry
    from app.gateway.video_gen.service import VideoGenService

    cfg = VideoGenConfig(qihu_api_key="t", engine_arch="multi_engine")
    providers = ProviderRegistry(cfg)
    mock_repo = MagicMock()
    mock_task = MagicMock()
    mock_task.payload = json.dumps({"audio_ready": True, "total_duration": 30.0})
    mock_repo.get = AsyncMock(return_value=mock_task)
    mock_repo.update = AsyncMock(return_value=None)
    svc = VideoGenService(
        repo=mock_repo,
        config=cfg,
        providers=providers,
        storage_dir=str(tmp_path),
        sandbox_provider=AsyncMock(),
    )
    user_id = "u1"
    task_id = "vg_ad"
    task_dir = Path(svc._storage_dir) / user_id / "tasks" / task_id
    task_dir.mkdir(parents=True)
    (task_dir / "storyboard.json").write_text(
        json.dumps(
            {
                "title": "t",
                "config": {},
                "frames": [
                    {"index": 0, "narration": "n0"},
                    {"index": 1, "narration": "n1"},
                    {"index": 2, "narration": "n2"},
                ],
                "scene_groups": [
                    {"index": 0, "title": "A", "frame_indices": [0]},
                    {"index": 1, "title": "B", "frame_indices": [1, 2]},
                ],
            }
        )
    )

    await svc._add_frame(task_id, user_id, {"narration": "mid"}, index=1)

    data = json.loads((task_dir / "storyboard.json").read_text())
    assert [f["narration"] for f in data["frames"]] == ["n0", "mid", "n1", "n2"]
    assert [f["index"] for f in data["frames"]] == [0, 1, 2, 3]
    # 新帧 index1 并入前一镜（N=0）的组A，而不是组B
    assert data["scene_groups"][0]["frame_indices"] == [0, 1]
    assert data["scene_groups"][1]["frame_indices"] == [2, 3]


def test_add_frame_route_passes_index(tmp_path):
    """POST body 带 index → 透传给 _add_frame 第 4 位置参数（中插接线）。"""
    app = _make_app(tmp_path, task_status=DRAFT)
    client = TestClient(app)
    resp = client.post(
        "/api/video/tasks/vg_ad/storyboard/frames",
        json={"narration": "new", "index": 2},
    )
    assert resp.status_code == 200, resp.text
    args = app.state.video_gen_service._add_frame.call_args
    # 兼容位置/关键字两种传参形态（路由现走位置传参；日后改 kwargs 不该假红）
    idx = args.args[3] if len(args.args) > 3 else args.kwargs.get("index")
    assert idx == 2, "index 应原样透传给 _add_frame"


# ---------------------------------------------------------------------------
# T2（2026-09-01 拆帧计划）：_delete_frame 越界不再静默 no-op——修前越界返回
# 200 假成功（deleted_index=None 分支），用户操作与帧数状态有出入时无感知
# （vg_22ee06b39d19 同次编辑中 PUT 帧 85 返回过 400，坐实该场景真实存在）。
# ---------------------------------------------------------------------------


def _make_real_service(tmp_path):
    """service 级测试共用：真 VideoGenService + mock repo + 指定帧数落盘。"""
    from app.gateway.video_gen.config import VideoGenConfig
    from app.gateway.video_gen.providers.registry import ProviderRegistry
    from app.gateway.video_gen.service import VideoGenService

    cfg = VideoGenConfig(qihu_api_key="t", engine_arch="multi_engine")
    mock_repo = MagicMock()
    mock_task = MagicMock()
    mock_task.payload = json.dumps({"audio_ready": True, "total_duration": 30.0})
    mock_repo.get = AsyncMock(return_value=mock_task)
    mock_repo.update = AsyncMock(return_value=None)
    svc = VideoGenService(
        repo=mock_repo,
        config=cfg,
        providers=ProviderRegistry(cfg),
        storage_dir=str(tmp_path / "storage"),
        sandbox_provider=AsyncMock(),
    )
    task_dir = tmp_path / "storage" / "u1" / "tasks" / "vg_ad"
    task_dir.mkdir(parents=True)
    (task_dir / "storyboard.json").write_text(
        json.dumps(
            {
                "title": "t",
                "config": {},
                "frames": [{"index": i, "narration": f"n{i}"} for i in range(3)],
            }
        )
    )
    return svc, task_dir


@pytest.mark.asyncio
async def test_delete_frame_out_of_range_raises_with_frame_count(tmp_path):
    """T2：越界抛 ValueError 含当前帧数；updater 写盘前抛出 → storyboard.json 原样。"""
    svc, task_dir = _make_real_service(tmp_path)
    before = (task_dir / "storyboard.json").read_text()

    with pytest.raises(ValueError, match=r"分镜 4 不存在（当前共 3 镜）"):
        await svc._delete_frame("vg_ad", "u1", 3)

    assert (task_dir / "storyboard.json").read_text() == before, "越界不落任何盘面改动（原子写不受影响）"


@pytest.mark.asyncio
async def test_delete_frame_negative_index_raises(tmp_path):
    """T2：负 index 同样越界（path param 可传负数）——不再静默 200。"""
    svc, task_dir = _make_real_service(tmp_path)

    with pytest.raises(ValueError, match=r"当前共 3 镜"):
        await svc._delete_frame("vg_ad", "u1", -1)

    data = json.loads((task_dir / "storyboard.json").read_text())
    assert len(data["frames"]) == 3, "负 index 不删任何帧"


@pytest.mark.asyncio
async def test_delete_frame_in_range_still_deletes(tmp_path):
    """T2 回归：界内删除行为不变（删中间帧 + 重排 + 失效音频产物）。"""
    svc, task_dir = _make_real_service(tmp_path)

    await svc._delete_frame("vg_ad", "u1", 1)

    data = json.loads((task_dir / "storyboard.json").read_text())
    assert [f["narration"] for f in data["frames"]] == ["n0", "n2"]
    assert [f["index"] for f in data["frames"]] == [0, 1]


def test_delete_frame_route_out_of_range_400(tmp_path):
    """T2：端点把 ValueError 转 400 携 detail（修前静默 200 假成功）；
    前端 deleteFrame 对 !res.ok throw → 既有 onError toast 通道，无需前端改动。"""
    app = _make_app(tmp_path, task_status=DRAFT)
    app.state.video_gen_service._delete_frame = AsyncMock(side_effect=ValueError("分镜 4 不存在（当前共 3 镜）"))
    client = TestClient(app)
    resp = client.delete("/api/video/tasks/vg_ad/storyboard/frames/3")
    assert resp.status_code == 400, resp.text
    # SEC-M12（批次 2 项 2.10）：detail 改通用文案（帧数细节进服务端日志）。
    assert resp.json()["detail"] == "Invalid frame index"
    # 越界时不应再刷 updated_at（没删东西）
    app.state.video_gen_service.touch_task.assert_not_awaited()
