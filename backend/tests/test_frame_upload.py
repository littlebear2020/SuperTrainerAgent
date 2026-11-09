"""Tests for local visual upload endpoint."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.gateway._shared.task_framework.schemas import DRAFT
from app.gateway.routers.video_gen import router

# Matches the autouse conftest fixture's user_id
_TEST_USER = "test-user-autouse"


def _make_app_with_mock(tmp_path):
    """Create a FastAPI app with a mock video_gen_service."""
    app = FastAPI()
    app.include_router(router)

    storage_root = (tmp_path / "storage").resolve()
    storage_root.mkdir()
    task_dir = storage_root / _TEST_USER / "tasks" / "vg_test123"
    task_dir.mkdir(parents=True)

    storyboard = {
        "title": "test",
        "config": {"video_type": "ai_image", "width": 1080, "height": 1920},
        "frames": [
            {"index": 0, "narration": "frame 0", "visual_url": "https://example.com/0.mp4"},
            {"index": 1, "narration": "frame 1", "visual_url": "https://example.com/1.mp4"},
        ],
    }
    (task_dir / "storyboard.json").write_text(json.dumps(storyboard), encoding="utf-8")

    mock_svc = MagicMock()
    mock_task = MagicMock()
    mock_task.id = "vg_test123"
    mock_task.status = DRAFT
    mock_svc.get_task = AsyncMock(return_value=mock_task)
    # _update_storyboard is an async method (read-merge-write on storyboard.json,
    # unified writer). MagicMock auto-creates a SYNC attribute → awaiting it raises
    # "object MagicMock can't be used in 'await' expression". Bind the REAL async
    # impl so the upload route's `await svc._update_storyboard(...)` actually runs
    # the read-merge-write against the on-disk storyboard set up above (regression
    # for glm's b9ed11f2 making _update_storyboard async without updating this mock).
    from app.gateway.video_gen.service import VideoGenService

    class _NullAsyncLock:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    mock_svc._sb_write_lock = _NullAsyncLock()
    mock_svc._update_storyboard = VideoGenService._update_storyboard.__get__(mock_svc, VideoGenService)
    mock_svc._storage_dir = str(storage_root)
    # add_dirty_frame is awaited by the upload route (added in 5082ccd8) — bind
    # an async mock so `await svc.repo.add_dirty_frame(...)` resolves (same class
    # of bug as _update_storyboard above).
    mock_svc.repo.add_dirty_frame = AsyncMock(return_value=None)
    # R2: reorder 路由清 dirty_frames——2026-08-08 P1-3 起走锁内 clear_dirty_frames
    mock_svc.repo.update = AsyncMock(return_value=None)
    mock_svc.repo.clear_dirty_frames = AsyncMock(return_value=None)
    # T4(ee042bc03) 起 7 个磁盘型端点末尾 await svc.touch_task(...)——同类第三类，
    # 绑定 async mock（同 _update_storyboard/add_dirty_frame 的教训）。
    mock_svc.touch_task = AsyncMock(return_value=None)

    app.state.video_gen_service = mock_svc
    return app, task_dir


def test_upload_frame_visual_success(tmp_path):
    """Upload a JPEG to replace frame visual."""
    app, task_dir = _make_app_with_mock(tmp_path)
    client = TestClient(app)

    resp = client.post(
        "/api/video/tasks/vg_test123/frames/0/upload-visual",
        files={"file": ("test.jpg", b"\xff\xd8\xff\xe0fake_jpeg_data", "image/jpeg")},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "frame_000.jpg" in data["path"]

    # Verify file was saved
    assert (task_dir / "visuals" / "frame_000.jpg").exists()

    # Verify storyboard was updated
    sb = json.loads((task_dir / "storyboard.json").read_text())
    assert sb["frames"][0]["local_visual_path"] == "visuals/frame_000.jpg"
    assert sb["frames"][0]["visual_type"] == "image"
    assert sb["frames"][0]["visual_url"] is None


def test_upload_rejects_invalid_type(tmp_path):
    """Non-image/video file is rejected."""
    app, _ = _make_app_with_mock(tmp_path)
    client = TestClient(app)

    resp = client.post(
        "/api/video/tasks/vg_test123/frames/0/upload-visual",
        files={"file": ("test.txt", b"hello", "text/plain")},
    )
    assert resp.status_code == 400
    assert "Unsupported" in resp.json()["detail"]


def test_upload_video_file(tmp_path):
    """Upload a video file works correctly."""
    app, task_dir = _make_app_with_mock(tmp_path)
    client = TestClient(app)

    resp = client.post(
        "/api/video/tasks/vg_test123/frames/1/upload-visual",
        files={"file": ("clip.mp4", b"\x00\x00\x00\x20ftypmp42", "video/mp4")},
    )
    assert resp.status_code == 200
    assert (task_dir / "visuals" / "frame_001.mp4").exists()
    sb = json.loads((task_dir / "storyboard.json").read_text())
    assert sb["frames"][1]["visual_type"] == "video"


def test_upload_task_not_found(tmp_path):
    """Return 404 if task doesn't exist."""
    app, _ = _make_app_with_mock(tmp_path)
    client = TestClient(app)

    app.state.video_gen_service.get_task = AsyncMock(return_value=None)

    resp = client.post(
        "/api/video/tasks/vg_notfound/frames/0/upload-visual",
        files={"file": ("test.jpg", b"data", "image/jpeg")},
    )
    assert resp.status_code == 404


def test_upload_rejects_wrong_status(tmp_path):
    """Return 409 if task is not draft or ready."""
    app, _ = _make_app_with_mock(tmp_path)
    client = TestClient(app)

    mock_task = MagicMock()
    mock_task.id = "vg_test123"
    mock_task.status = "running"
    app.state.video_gen_service.get_task = AsyncMock(return_value=mock_task)

    resp = client.post(
        "/api/video/tasks/vg_test123/frames/0/upload-visual",
        files={"file": ("test.jpg", b"data", "image/jpeg")},
    )
    assert resp.status_code == 409


def test_get_frame_visual_serves_no_cache_header(tmp_path):
    """本地帧图响应必须带 Cache-Control: no-cache——否则同一帧重复上传后
    <img> URL 不变，浏览器启发式缓存显示旧图。"""
    app, task_dir = _make_app_with_mock(tmp_path)
    (task_dir / "visuals").mkdir(exist_ok=True)
    (task_dir / "visuals" / "frame_000.jpg").write_bytes(b"\xff\xd8\xff" + b"0" * 600)
    # storyboard.json 里 frame 0 指向本地文件
    sb = json.loads((task_dir / "storyboard.json").read_text(encoding="utf-8"))
    sb["frames"][0]["local_visual_path"] = "visuals/frame_000.jpg"
    (task_dir / "storyboard.json").write_text(json.dumps(sb), encoding="utf-8")

    client = TestClient(app)
    resp = client.get("/api/video/tasks/vg_test123/frames/0/visual")
    assert resp.status_code == 200, resp.text
    assert resp.headers.get("cache-control") == "no-cache", dict(resp.headers)  # 修复前无此头


# ── Task 4 (F6): PUT 帧编辑放开 ready 态 ────────────────────────────────────


def _make_app_with_status(tmp_path, status):
    """同 _make_app_with_mock，但 task.status 参数化。"""
    app, task_dir = _make_app_with_mock(tmp_path)
    app.state.video_gen_service.get_task = AsyncMock(return_value=MagicMock(id="vg_test123", status=status))
    return app, task_dir


def test_update_frame_allows_ready(tmp_path):
    """ready 态允许改 prompt/旁白（F6）——此前仅 draft/failed，倒挂：
    failed 能改 ready 不能。"""
    app, task_dir = _make_app_with_status(tmp_path, "ready")
    client = TestClient(app)
    resp = client.put(
        "/api/video/tasks/vg_test123/storyboard/frames/1",
        json={"image_prompt": "新的画面提示词"},
    )
    assert resp.status_code == 200, resp.text  # 修复前 409
    data = resp.json()
    assert data["updated"]["image_prompt"] == "新的画面提示词"
    assert "重新生成" in data.get("warning", "")
    sb = json.loads((task_dir / "storyboard.json").read_text(encoding="utf-8"))
    assert sb["frames"][1]["image_prompt"] == "新的画面提示词"


def test_update_frame_ready_narration_warns_recompose(tmp_path):
    app, task_dir = _make_app_with_status(tmp_path, "ready")
    client = TestClient(app)
    resp = client.put(
        "/api/video/tasks/vg_test123/storyboard/frames/1",
        json={"narration": "改过的旁白"},
    )
    assert resp.status_code == 200, resp.text
    warning = resp.json().get("warning", "")
    assert "重新合成" in warning and "重新生成" in warning


def test_update_frame_still_rejects_composing(tmp_path):
    app, task_dir = _make_app_with_status(tmp_path, "composing")
    client = TestClient(app)
    resp = client.put(
        "/api/video/tasks/vg_test123/storyboard/frames/1",
        json={"image_prompt": "x"},
    )
    assert resp.status_code == 409


def test_reorder_clears_segment_cache(tmp_path):
    """重排后 segments/ 缓存必须删除（P1-3 启用增量后的鬼片防护）。"""
    app, task_dir = _make_app_with_status(tmp_path, DRAFT)
    (task_dir / "segments").mkdir()
    (task_dir / "segments" / "seg_000.mp4").write_bytes(b"old")
    client = TestClient(app)
    resp = client.put("/api/video/tasks/vg_test123/storyboard/reorder", json={"from_index": 0, "to_index": 2})
    assert resp.status_code == 200, resp.text
    assert not (task_dir / "segments").exists()  # 修复前：目录仍在


def test_reorder_clears_dirty_frames(tmp_path):
    """R2：reorder 后帧序变化，dirty_frames 索引语义失效，必须清空——
    否则 dirty=[1] 指向的已是另一帧（当前被 segments 清空→全量回退掩盖，属耦合地雷）。"""
    app, task_dir = _make_app_with_status(tmp_path, DRAFT)
    client = TestClient(app)
    resp = client.put("/api/video/tasks/vg_test123/storyboard/reorder", json={"from_index": 0, "to_index": 2})
    assert resp.status_code == 200, resp.text
    # 2026-08-08 P1-3：清空必须走锁内方法（裸 repo.update 与 add_dirty_frame 竞态丢帧）
    app.state.video_gen_service.repo.clear_dirty_frames.assert_any_await("vg_test123")


def test_upload_out_of_range_frame_index_404(tmp_path):
    """M6: 越界 frame_index → 404，文件不写盘、dirty 不标（修复前假成功 {"ok": true}）。"""
    app, task_dir = _make_app_with_mock(tmp_path)
    client = TestClient(app)

    resp = client.post(
        "/api/video/tasks/vg_test123/frames/9/upload-visual",
        files={"file": ("test.jpg", b"\xff\xd8\xff\xe0fake_jpeg_data", "image/jpeg")},
    )

    assert resp.status_code == 404  # 修复前：200 {"ok": true, ...}
    visuals = task_dir / "visuals"
    assert not visuals.exists() or not list(visuals.glob("frame_009*"))
    app.state.video_gen_service.repo.add_dirty_frame.assert_not_awaited()


def test_upload_missing_storyboard_404(tmp_path):
    """M6: storyboard 缺失 → 404（修复前静默跳过 storyboard 更新仍写文件报 ok）。"""
    app, task_dir = _make_app_with_mock(tmp_path)
    (task_dir / "storyboard.json").unlink()
    client = TestClient(app)

    resp = client.post(
        "/api/video/tasks/vg_test123/frames/0/upload-visual",
        files={"file": ("test.jpg", b"\xff\xd8\xff\xe0fake_jpeg_data", "image/jpeg")},
    )

    assert resp.status_code == 404
    app.state.video_gen_service.repo.add_dirty_frame.assert_not_awaited()


# ── P0: upload-visual must clear regen_state -> D1 假阳性回归 ───────────────


def test_upload_clears_regen_state_so_recompose_passes(tmp_path):
    """帧重生失败（regen_state=error）-> 用户上传本地图修复 -> recompose 放行。

    回归链（D1 引入）：_visual_updater 不清 regen_state ->
    frames_in_regen_error 命中 -> 409 假阳性（且文案「沿用旧画面」错误，
    实际会用新上传的图）。
    """
    app, task_dir = _make_app_with_mock(tmp_path)
    client = TestClient(app)

    # 模拟帧0重生失败
    sb = json.loads((task_dir / "storyboard.json").read_text(encoding="utf-8"))
    sb["frames"][0]["regen_state"] = "error"
    sb["frames"][0]["regen_error"] = "provider timeout"
    (task_dir / "storyboard.json").write_text(json.dumps(sb), encoding="utf-8")

    # 上传本地图修复帧0
    resp = client.post(
        "/api/video/tasks/vg_test123/frames/0/upload-visual",
        files={"file": ("fix.jpg", b"\xff\xd8\xff\xe0fake_jpeg_data", "image/jpeg")},
    )
    assert resp.status_code == 200, resp.text

    # 验证 storyboard 清了 regen_state/regen_error/preview_error
    sb = json.loads((task_dir / "storyboard.json").read_text(encoding="utf-8"))
    f0 = sb["frames"][0]
    assert f0.get("local_visual_path") == "visuals/frame_000.jpg"
    assert f0.get("visual_url") is None
    assert f0.get("regen_state") is None, "upload 必须清 regen_state，否则 D1 预检假阳性"
    assert f0.get("regen_error") is None, "upload 必须清 regen_error"
    assert f0.get("preview_error") is None, "upload 必须清 preview_error（frameBadge 不再显示失败徽标）"


def test_upload_preserves_regen_state_on_other_frames(tmp_path):
    """上传帧0不影响帧1的 regen_state。"""
    app, task_dir = _make_app_with_mock(tmp_path)
    client = TestClient(app)

    sb = json.loads((task_dir / "storyboard.json").read_text(encoding="utf-8"))
    sb["frames"][1]["regen_state"] = "error"
    sb["frames"][1]["regen_error"] = "boom"
    (task_dir / "storyboard.json").write_text(json.dumps(sb), encoding="utf-8")

    resp = client.post(
        "/api/video/tasks/vg_test123/frames/0/upload-visual",
        files={"file": ("fix.jpg", b"\xff\xd8\xff\xe0fake_jpeg_data", "image/jpeg")},
    )
    assert resp.status_code == 200

    sb = json.loads((task_dir / "storyboard.json").read_text(encoding="utf-8"))
    assert sb["frames"][1].get("regen_state") == "error", "不应误清其他帧的状态"
