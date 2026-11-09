"""GET /frames/{i}/visual 本地文件缺失回退（2026-07-27）：曾 local 有值文件缺失即 404。"""

import json

import pytest
from fastapi import HTTPException
from fastapi.responses import FileResponse, RedirectResponse


def _make_request(svc):
    class _Req:
        class app:
            class state:
                video_gen_service = svc

    return _Req()


class _Svc:
    def __init__(self, tmp_path):
        self._storage_dir = str(tmp_path)

    async def get_task(self, task_id, user_id):
        from types import SimpleNamespace

        return SimpleNamespace(status="ready")


def _write_storyboard(tmp_path, task_id, frame):
    task_dir = tmp_path / "test-user-autouse" / "tasks" / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    sb = {"title": "t", "config": {"video_type": "ai_image"}, "frames": [frame]}
    (task_dir / "storyboard.json").write_text(json.dumps(sb), encoding="utf-8")
    return task_dir


@pytest.mark.asyncio
async def test_missing_local_file_falls_back_to_visual_url(tmp_path):
    from app.gateway.routers.video_gen import get_frame_visual

    _write_storyboard(
        tmp_path,
        "vg_miss",
        {"index": 0, "local_visual_path": "visuals/img_0000.png", "visual_url": "https://tos.example.com/a.png"},
    )
    resp = await get_frame_visual(_make_request(_Svc(tmp_path)), "vg_miss", 0)
    assert isinstance(resp, RedirectResponse), "local 文件缺失且 visual_url 存在时必须 302 回退（曾 404）"
    assert resp.headers["location"] == "https://tos.example.com/a.png"


@pytest.mark.asyncio
async def test_existing_local_file_still_served(tmp_path):
    from app.gateway.routers.video_gen import get_frame_visual

    task_dir = _write_storyboard(
        tmp_path,
        "vg_ok",
        {"index": 0, "local_visual_path": "visuals/img_0000.png", "visual_url": "https://tos.example.com/a.png"},
    )
    (task_dir / "visuals").mkdir(exist_ok=True)
    (task_dir / "visuals" / "img_0000.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    resp = await get_frame_visual(_make_request(_Svc(tmp_path)), "vg_ok", 0)
    assert isinstance(resp, FileResponse), "本地文件存在时必须照旧直出"


@pytest.mark.asyncio
async def test_missing_local_and_no_url_still_404(tmp_path):
    from app.gateway.routers.video_gen import get_frame_visual

    _write_storyboard(
        tmp_path,
        "vg_dead",
        {"index": 0, "local_visual_path": "visuals/img_0000.png", "visual_url": None},
    )
    with pytest.raises(HTTPException) as exc:
        await get_frame_visual(_make_request(_Svc(tmp_path)), "vg_dead", 0)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_path_traversal_still_404_no_fallback(tmp_path):
    """安全回归锁（评审 No.5）：越界路径必须硬 404——绝不回退 visual_url。

    语义非对称是有意设计：越界=数据被污染/攻击，显式失败；良性缺失才回退。
    若被误改成 return None 回退，端点会变成 visual_url 探测 oracle。"""
    from app.gateway.routers.video_gen import get_frame_visual

    _write_storyboard(
        tmp_path,
        "vg_evil",
        {"index": 0, "local_visual_path": "../../etc/passwd", "visual_url": "https://tos.example.com/a.png"},
    )
    with pytest.raises(HTTPException) as exc:
        await get_frame_visual(_make_request(_Svc(tmp_path)), "vg_evil", 0)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_sidecar_local_sources_served(tmp_path):
    """回退链第二环（评审 No.5）：local_visual_path 为空但 sidecar 有值 → 直出。"""
    from app.gateway.routers.video_gen import get_frame_visual

    task_dir = _write_storyboard(
        tmp_path,
        "vg_sidecar",
        {"index": 0, "local_visual_path": None, "visual_url": "https://tos.example.com/a.png"},
    )
    (task_dir / "visuals").mkdir(exist_ok=True)
    (task_dir / "visuals" / "img_0000.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    (task_dir / "local_sources.json").write_text(json.dumps({"0": "visuals/img_0000.png"}), encoding="utf-8")
    resp = await get_frame_visual(_make_request(_Svc(tmp_path)), "vg_sidecar", 0)
    assert isinstance(resp, FileResponse)


@pytest.mark.asyncio
async def test_sidecar_missing_file_falls_back_to_visual_url(tmp_path):
    """sidecar 指向的文件缺失 → 与主路径同语义，回退 visual_url 302。"""
    from app.gateway.routers.video_gen import get_frame_visual

    task_dir = _write_storyboard(
        tmp_path,
        "vg_sidecar_miss",
        {"index": 0, "local_visual_path": None, "visual_url": "https://tos.example.com/a.png"},
    )
    (task_dir / "local_sources.json").write_text(json.dumps({"0": "visuals/gone.png"}), encoding="utf-8")
    resp = await get_frame_visual(_make_request(_Svc(tmp_path)), "vg_sidecar_miss", 0)
    assert isinstance(resp, RedirectResponse)
    assert resp.headers["location"] == "https://tos.example.com/a.png"
