"""E1: storyboard GET route — DRAFT 生成期返 200 {status:generating}（非 404），渲染期 409 不变。"""

import json
from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.gateway._shared.task_framework.schemas import DRAFT
from app.gateway.routers.video_gen import router

_TEST_USER = "test-user-autouse"


def _make_app(tmp_path, *, task_status, task_result="{}", has_storyboard_file=False, storyboard_data=None, task_progress=0):
    app = FastAPI()
    app.include_router(router)
    storage_root = (tmp_path / "storage").resolve()
    storage_root.mkdir()
    task_dir = storage_root / _TEST_USER / "tasks" / "vg_test123"
    task_dir.mkdir(parents=True)
    if has_storyboard_file:
        data = storyboard_data or {"title": "t", "config": {}, "frames": []}
        (task_dir / "storyboard.json").write_text(json.dumps(data), encoding="utf-8")
    mock_svc = MagicMock()
    mock_task = MagicMock()
    mock_task.id = "vg_test123"
    mock_task.status = task_status
    mock_task.result = task_result
    mock_task.payload = "{}"
    mock_task.progress = task_progress
    mock_svc.get_task = AsyncMock(return_value=mock_task)
    mock_svc._storage_dir = str(storage_root)
    app.state.video_gen_service = mock_svc
    return app


def test_storyboard_route_200_generating_when_draft_no_file(tmp_path):
    """E1: 草稿生成期 storyboard.json 未落盘 → 200 {status:generating}，不 404 刷屏。"""
    app = _make_app(tmp_path, task_status=DRAFT, has_storyboard_file=False)
    client = TestClient(app)
    resp = client.get("/api/video/tasks/vg_test123/storyboard")
    assert resp.status_code == 200, f"须 200 generating，got {resp.status_code}: {resp.text}"
    assert resp.json().get("status") == "generating"


def test_storyboard_route_409_when_rendering(tmp_path):
    """E1: 渲染期（running）→ 409 不变（by design，storyboard 中变更不可读）。"""
    app = _make_app(tmp_path, task_status="running")
    client = TestClient(app)
    resp = client.get("/api/video/tasks/vg_test123/storyboard")
    assert resp.status_code == 409


def test_storyboard_route_404_when_ready_no_file(tmp_path):
    """ready 但无 storyboard 文件 → 真 404（非生成期，文件确缺失）。"""
    app = _make_app(tmp_path, task_status="ready", has_storyboard_file=False)
    client = TestClient(app)
    resp = client.get("/api/video/tasks/vg_test123/storyboard")
    assert resp.status_code == 404


def test_storyboard_route_200_data_when_draft_file_exists(tmp_path):
    """DRAFT 且 storyboard.json 已落盘 → 200 返回 storyboard 数据（合并 audio_ready）。"""
    app = _make_app(tmp_path, task_status=DRAFT, has_storyboard_file=True)
    client = TestClient(app)
    resp = client.get("/api/video/tasks/vg_test123/storyboard")
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("status") != "generating"  # 真数据，非 generating
    assert "frames" in data


def test_storyboard_route_200_disk_during_composing(tmp_path):
    """M3-3a: composing 期磁盘 storyboard.json 存在 → 200 返回它（非 409）。

    storyboard.json 由 _update_storyboard 原子写（tmp+rename），Worker composing 期不写它，
    故读安全。放宽 409 guard 为"磁盘优先"消除每帧预览 invalidate→refetch→409 风暴。
    """
    frames = [{"index": 0, "narration": "n0", "visual_url": "https://x/0.png"}]
    app = _make_app(
        tmp_path,
        task_status="composing",
        task_progress=95,
        has_storyboard_file=True,
        storyboard_data={"title": "t", "config": {}, "frames": frames},
    )
    client = TestClient(app)
    resp = client.get("/api/video/tasks/vg_test123/storyboard")
    assert resp.status_code == 200, f"composing+disk 须 200, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body.get("status") == "composing"
    assert body.get("progress") == 95
    assert len(body.get("frames", [])) == 1
    assert body["frames"][0]["visual_url"] == "https://x/0.png"


def test_storyboard_route_409_when_composing_no_file(tmp_path):
    """M3-3a: composing 期磁盘不存在 → 仍 409（无可读 storyboard，保留原 guard）。"""
    app = _make_app(tmp_path, task_status="composing", has_storyboard_file=False)
    client = TestClient(app)
    resp = client.get("/api/video/tasks/vg_test123/storyboard")
    assert resp.status_code == 409
