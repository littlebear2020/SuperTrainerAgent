"""T-E4（R18，2026-09-09 feedback-round1）：still in-flight 键升 cache_key 级。

修前键 = (task_id, frame_index)：同帧换版式期间（渲染中改 layout）新 cache_key
请求恒 already_running 且不自愈——旧渲染产出旧 key 文件，新 key 永不出现 → 前端
30s 超时降级。修后键含 cache_key：换版式即独立开新渲染，旧键条目自然过期。
"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.gateway.routers.video_gen as vg_router
from app.gateway.routers.video_gen import preview_frame_still


def _make_request(svc):
    class _Req:
        class app:
            class state:
                video_gen_service = svc

    return _Req()


@pytest.fixture()
def svc(tmp_path):
    from app.gateway.video_gen.config import VideoGenConfig
    from app.gateway.video_gen.providers.registry import ProviderRegistry
    from app.gateway.video_gen.service import VideoGenService

    cfg = VideoGenConfig(qihu_api_key="t")
    s = VideoGenService(
        repo=MagicMock(),
        config=cfg,
        providers=ProviderRegistry(cfg),
        storage_dir=str(tmp_path),
        sandbox_provider=AsyncMock(),
    )
    s.repo.update = AsyncMock(return_value=None)
    s.repo.create = AsyncMock(return_value=None)
    return s


def _seed(svc, tmp_path, user_id, task_id):
    task_dir = tmp_path / user_id / "tasks" / task_id
    task_dir.mkdir(parents=True)
    (task_dir / "storyboard.json").write_text(
        json.dumps(
            {
                "title": "t",
                "config": {"video_type": "remotion_course", "width": 1920, "height": 1080},
                "frames": [
                    {"index": 0, "narration": "封面", "layout": "CoverPage"},
                    {"index": 1, "narration": "正文帧较长内容走图文版式" * 2, "layout": "ImageTextLeft"},
                ],
                "scene_groups": [],
            }
        ),
        encoding="utf-8",
    )
    task = MagicMock()
    task.status = "draft"
    task.task_type = "remotion_course"
    task.payload = "{}"
    return task


class TestInflightCacheKey:
    @pytest.mark.asyncio
    async def test_same_key_second_request_already_running(self, svc, tmp_path, monkeypatch):
        vg_router._still_inflight.clear()
        user_id = "test-user-autouse"
        task = _seed(svc, tmp_path, user_id, "vg_r18")
        svc.get_task = AsyncMock(return_value=task)

        # repo.get：已登记 still 任务仍 composing → 第二次同 key 请求 already_running
        async def _repo_get(task_id, user_id=None):
            row = MagicMock()
            row.status = "composing"
            return row

        svc.repo.get = AsyncMock(side_effect=_repo_get)

        r1 = await preview_frame_still(_make_request(svc), "vg_r18", 1)
        assert r1["status"] == "started"
        r2 = await preview_frame_still(_make_request(svc), "vg_r18", 1)
        assert r2["status"] == "already_running", "同 cache_key 重复请求去重（D9 缓存语义不变）"
        vg_router._still_inflight.clear()

    @pytest.mark.asyncio
    async def test_layout_switch_starts_new_render_not_stuck(self, svc, tmp_path):
        """R18 核心用例：渲染中换版式（新 cache_key）→ 必须开新渲染，
        不得被旧键的 already_running 卡死。"""
        vg_router._still_inflight.clear()
        user_id = "test-user-autouse"
        task = _seed(svc, tmp_path, user_id, "vg_r18b")
        svc.get_task = AsyncMock(return_value=task)

        async def _repo_get(task_id, user_id=None):
            row = MagicMock()
            row.status = "composing"
            return row

        svc.repo.get = AsyncMock(side_effect=_repo_get)

        r1 = await preview_frame_still(_make_request(svc), "vg_r18b", 1)
        assert r1["status"] == "started"

        # 渲染进行中用户切版式 → storyboard 落盘 → 下次请求是新 cache_key
        sb = json.loads((tmp_path / user_id / "tasks" / "vg_r18b" / "storyboard.json").read_text(encoding="utf-8"))
        sb["frames"][1]["layout"] = "ImageTextRight"
        (tmp_path / user_id / "tasks" / "vg_r18b" / "storyboard.json").write_text(json.dumps(sb, ensure_ascii=False), encoding="utf-8")

        r2 = await preview_frame_still(_make_request(svc), "vg_r18b", 1)
        assert r2["status"] == "started", "换版式（新 cache_key）必须开新渲染（修前恒 already_running）"
        assert r1["cache_key"] != r2["cache_key"], "换版式 cache_key 必然不同（D9）"
        # 两条 in-flight 并存（旧渲染继续，新渲染独立）
        assert len([k for k in vg_router._still_inflight if k[0] == "vg_r18b"]) == 2
        vg_router._still_inflight.clear()


class TestBulkPriority:
    """T-E9（v4.2，2026-09-09 feedback-round1）：批量缩略图 still 优先级分层。

    bulk=true（帧条批量缩略图）→ remotion_still 任务 priority=10，不堵交互
    预览（20）/成片渲染（35）；缺省不传 → 保持 20。
    """

    @staticmethod
    def _last_created_priority(svc) -> int:
        assert svc.repo.create.await_count >= 1, "须至少创建一条 still 任务"
        return svc.repo.create.await_args.kwargs["priority"]

    @pytest.mark.asyncio
    async def test_bulk_true_maps_priority_10(self, svc, tmp_path):
        vg_router._still_inflight.clear()
        user_id = "test-user-autouse"
        task = _seed(svc, tmp_path, user_id, "vg_bulk1")
        svc.get_task = AsyncMock(return_value=task)
        svc.repo.get = AsyncMock(return_value=None)

        r = await preview_frame_still(_make_request(svc), "vg_bulk1", 1, bulk=True)
        assert r["status"] == "started"
        assert self._last_created_priority(svc) == 10, "bulk=true → priority=10（低于交互预览 20）"
        vg_router._still_inflight.clear()

    @pytest.mark.asyncio
    async def test_default_keeps_priority_20(self, svc, tmp_path):
        vg_router._still_inflight.clear()
        user_id = "test-user-autouse"
        task = _seed(svc, tmp_path, user_id, "vg_bulk2")
        svc.get_task = AsyncMock(return_value=task)
        svc.repo.get = AsyncMock(return_value=None)

        # 不传 bulk（交互预览路径——still-preview.tsx 不带参数）
        r = await preview_frame_still(_make_request(svc), "vg_bulk2", 1)
        assert r["status"] == "started"
        assert self._last_created_priority(svc) == 20, "缺省 → 保持 20（交互预览优先级不变）"
        vg_router._still_inflight.clear()


class TestInflightCleanupOnCacheHit:
    """review #3（2026-09-09 v4.2 增量 review）：缓存命中早退须清 in-flight 条目。

    修前 POST cached / GET FileResponse 两处早退都走不到 pop，前端拿到图即停止
    轮询 → 成功渲染的 (task_id, frame_index, cache_key) 条目滞留至进程重启
    （失败路径会清理、同键再请求自愈，故无功能正确性问题，纯进程级 dict 卫生）。
    本批 bulk 批量缩略图（10 帧任务开详情页即 10 条）放大了触发量。
    """

    @staticmethod
    def _write_cache(svc, tmp_path, user_id, task_id, cache_key):
        from app.gateway.routers.video_gen import _still_cache_path

        cache_file = _still_cache_path(svc, user_id, task_id, cache_key)
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_bytes(b"\x89PNG-fake")
        return cache_file

    @pytest.mark.asyncio
    async def test_post_cache_hit_pops_inflight(self, svc, tmp_path):
        vg_router._still_inflight.clear()
        user_id = "test-user-autouse"
        task = _seed(svc, tmp_path, user_id, "vg_r18c")
        svc.get_task = AsyncMock(return_value=task)
        svc.repo.get = AsyncMock(return_value=None)

        r1 = await preview_frame_still(_make_request(svc), "vg_r18c", 1)
        assert r1["status"] == "started"
        assert len(vg_router._still_inflight) == 1

        # 模拟 worker 渲染完成：缓存文件落盘 → 下次 POST 命中 cached 早退
        self._write_cache(svc, tmp_path, user_id, "vg_r18c", r1["cache_key"])

        r2 = await preview_frame_still(_make_request(svc), "vg_r18c", 1)
        assert r2["status"] == "cached"
        assert len(vg_router._still_inflight) == 0, "缓存命中早退须顺手清 in-flight 条目"
        vg_router._still_inflight.clear()

    @pytest.mark.asyncio
    async def test_get_cache_hit_pops_inflight(self, svc, tmp_path):
        from fastapi.responses import FileResponse

        from app.gateway.routers.video_gen import get_frame_still

        vg_router._still_inflight.clear()
        user_id = "test-user-autouse"
        task = _seed(svc, tmp_path, user_id, "vg_r18d")
        svc.get_task = AsyncMock(return_value=task)
        svc.repo.get = AsyncMock(return_value=None)

        r1 = await preview_frame_still(_make_request(svc), "vg_r18d", 1)
        assert r1["status"] == "started"
        assert len(vg_router._still_inflight) == 1

        # 渲染完成 + 前端轮询 GET 拿图（FileResponse 早退）→ 条目须清
        self._write_cache(svc, tmp_path, user_id, "vg_r18d", r1["cache_key"])
        r2 = await get_frame_still(_make_request(svc), "vg_r18d", 1, r1["cache_key"])
        assert isinstance(r2, FileResponse), "缓存命中 GET 须返回 PNG 文件"
        assert len(vg_router._still_inflight) == 0, "GET 缓存命中早退须顺手清 in-flight 条目"
        vg_router._still_inflight.clear()
